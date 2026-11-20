"""Cartridge executor: the injected core dispatch for one admitted run.

The unchanged core calls ``dispatch(prompt, model_id)`` once per cartridge and
saves whatever string it returns as the cartridge's canonical result. This
executor turns that call into a bounded chain of fresh, independently saved
Pi sessions (reliability.3 retry admission), commits result_file_v1
handovers, applies Antiloop to Pi events and records every attempt in the
ledger. It raises to let the core fence-persist HALTED, or raises the core's
own StopRequested at a safe boundary between attempts.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import antiloop
import handover as ho
import ledger as lg
import protocol as proto
import tokens
from pi_dispatch import PiDispatch, WorkerFailure
from scheduler import QueueStopped

HARD_WORKER_ERRORS = (
    "Pi attempted model substitution", "Worker changed assigned model", "Pi tool allowlist differs",
    "Worker context was not fresh", "Saved Pi session", "Saved Pi worker session missing",
    "Requested tool unavailable", "Assigned model unavailable", "Invalid tool allowlist entry",
    "Magazine has not verified Pi SDK", "Pi SDK at", "Pi SDK not found", "Pi model configuration",
    "Configured worker extension failed", "Pi dispatch response identity mismatch",
)


class CartridgeFailed(RuntimeError):
    """Raised to the core: the run halts with this cartridge's last decision."""

    def __init__(self, cid, attempt):
        self.code = attempt.get("code")
        decision = (attempt.get("retry_decision") or {}).get("reason_code")
        super().__init__(f"{cid} attempt {attempt.get('attempt_number')}: {self.code or attempt.get('status')}"
                         + (f"; {decision}" if decision else ""))


class Stopped(Exception):
    pass


def classify_worker_error(message):
    if any(message.startswith(prefix) for prefix in HARD_WORKER_ERRORS):
        return ho.ResultFailure("WORKER_INTEGRITY", message, "hard")
    return ho.ResultFailure("WORKER_FAILED", message, "repair")


def effective_timeout(protocol, config):
    seconds = protocol["recovery"]["attempt_seconds"]
    explicit = config.get("timeoutSeconds")
    if type(explicit) is int and 0 < explicit < seconds:
        return explicit  # an explicitly configured smaller limit is respected
    return seconds


def attempt_prompt(prompt, attempt, protocol, cid, layout, values, inputs):
    """Exact cartridge prompt, then the declared inputs and the file protocol."""
    decl = protocol["cartridges"][cid]
    extra = []
    if values:
        lines = ["Inputs from earlier steps:"]
        for name, source, value in values:
            lines.append(f"\n{name} (from {source}):\n```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```")
        extra.append("\n".join(lines))
    if decl["inputs"] or decl["outputs"]:
        out_dir = layout.work(cid) / "outputs"
        lines = ["Files for this step:", f"- Save your output files in: {out_dir}"]
        if decl["outputs"]:
            lines.append("- Required output files: " + ", ".join(decl["outputs"]))
        if inputs:
            lines.append("- Input files (verified copies):")
            lines += [f"  - {f['local']} (from {f['from']}: {f['path']})" for f in inputs]
        extra.append("\n".join(lines))
    pointers = ho.outgoing_pointers(protocol, cid)
    lines = ["MAGAZINE FILE HANDOVER (the final response protocol for this attempt):",
             f"Attempt: {attempt['attempt_id']}. Publish with the magazine_publish_result tool.",
             "For the usual /result handover, call it with verified, note, and lists of files and checks you actually performed. For a text deliverable with no files, put the full deliverable text in note.",
             "For other handover keys, pass the complete handover object as payload_json (JSON text).",
             "The tool supplies the trusted run, cartridge, attempt and session identities and validates the serialized result.",
             "Publish after performing the task and checking the actual files. The verified flag records your claim; it does not run checks.",
             f"Required outgoing selectors: {', '.join(pointers) or '(none; still publish an attempt result)'}. The published file is the handover.",
             "Finish with the DONE line after publication. The handover payload need not be repeated in your reply."]
    context = attempt.get("failure_context")
    if context:
        lines += ["AUTOMATIC RECOVERY: " + json.dumps(context, ensure_ascii=False),
                  "The earlier session is archived. Treat earlier files as unverified candidates; verify useful completed work and preserve it."]
        if attempt["kind"] == "publish":
            lines.append("This attempt is verification and publication only. Inspect existing task files, repeat the task's checks, "
                         "and publish the observed result. If the work needs edits or cannot be verified, end with HALT and describe the missing work.")
        else:
            lines.append("Repair only this task in its designated output folder, using verified inputs. Recheck existing candidates before changing them.")
    extra.append("\n".join(lines))
    extra.append(proto.DONE_INSTRUCTION)
    separator = "" if prompt.endswith("\n") else "\n"
    text = prompt + separator + "\n---\n" + "\n\n".join(extra) + "\n"
    assert text.startswith(prompt)
    return text


class CartridgeExecutor:
    def __init__(self, *, core, config, run_base, run_id, owner, state_path, lock_path, prepared,
                 protocol, meta, workspace, scheduler=None):
        self.core = core
        self.scheduler = scheduler
        self._calls = 0
        self.config = config
        self.run_base = Path(run_base)
        self.run_id = run_id
        self.owner = owner
        self.state_path = state_path
        self.lock_path = lock_path
        self.prepared = prepared
        self.protocol = protocol
        self.meta = meta
        self.mode = protocol["handover_mode"]
        self.layout = ho.Layout(run_base, workspace, run_id, protocol)
        self.dispatch = PiDispatch(config, self.run_base / "sessions")
        order = [c["id"] for c in meta["document"]["cartridges"]]
        self.ledger = lg.load(run_base) or lg.new(run_id, order, self.mode)
        self.operator_stop = False
        self.loop_abort = None
        self._abort_task = None
        self._usage = []

    # ----------------------------------------------------------- core API
    async def __call__(self, prompt, model_id):
        state = self.core["ss"].load_run_state(self.state_path)
        cid = state.get("current")
        cartridge = next((c for c in self.meta["document"]["cartridges"] if c["id"] == cid), None)
        if cartridge is None or cartridge["prompt"] != prompt:
            raise RuntimeError("Dispatched prompt does not match the persisted current cartridge")
        if self.meta["cartridge_models"].get(cid) != model_id:
            raise RuntimeError("Dispatched model does not match the prepared assignment")
        if self.scheduler is not None:
            # The previous cartridge is durably committed by the core before this
            # call, so this is the only point where Round Robin may rotate.
            try:
                await self.scheduler.turn(self.run_id, self.owner, len(state.get("done") or []), first=self._calls == 0)
            except QueueStopped:
                self.stop_boundary()
                raise RuntimeError("Stopped while waiting in the queue") from None
        self._calls += 1
        return await self.run_cartridge(cid, prompt, model_id)

    async def cancel_active(self):
        """Operator STOP (via the frozen helper): ask the active worker to abort."""
        self.operator_stop = True
        if self.scheduler is not None and self.scheduler.cancel_wait(self.run_id):
            return SimpleNamespace(confirmed=False, active=False,
                                   detail="Run was waiting in the queue; no inference was active")
        return await self.dispatch.cancel_active()

    # ------------------------------------------------------------ helpers
    def save(self):
        lg.save(self.run_base, self.ledger)

    def stop_boundary(self):
        rc = self.core["rc"]
        if rc.load_stop_control(self.state_path) is None:
            return
        stopped = rc.persist_stop_if_requested(
            self.state_path, self.lock_path, self.owner, self.prepared.attempt, self.prepared.identity,
            method="boundary-between-attempts")
        raise rc.StopRequested(stopped)

    def automatic_allowance(self):
        if self.mode in (proto.LEGACY_PI5, "legacy_text"):
            return 1  # pi.5 compatible: one attempt per explicit Start
        return self.protocol["recovery"]["max_attempts"]

    # ------------------------------------------------------- the attempts
    async def run_cartridge(self, cid, prompt, model_id):
        record = self.ledger["cartridges"].setdefault(cid, {"status": "pending", "attempts": []})
        remaining = min(self.automatic_allowance(), lg.HARD_CEILING - len(record["attempts"]))
        if remaining <= 0:
            record["status"] = "failed"
            lg.note(self.ledger, f"{cid} has used all {lg.HARD_CEILING} attempts; no further attempt starts.", cid, "error")
            self.save()
            raise CartridgeFailed(cid, {"code": "MAX_ATTEMPTS_REACHED", "attempt_number": len(record["attempts"])})
        context, fingerprints = None, set()
        for index in range(remaining):
            self.stop_boundary()
            attempt = {"attempt_id": uuid.uuid4().hex, "attempt_number": len(record["attempts"]) + 1,
                       "execution_attempt": self.prepared.attempt, "model": model_id, "status": "running",
                       "phase": "working", "started": lg.now_iso(), "session_id": None,
                       "kind": (context or {}).get("kind", "work"), "protocol": self.mode,
                       "tokens": tokens.unavailable("not_started")}
            if context:
                attempt["failure_context"] = context
            record["attempts"].append(attempt)
            record["status"] = "running"
            lg.note(self.ledger, f"{cid} attempt {attempt['attempt_number']} started ({attempt['kind']}).", cid)
            self.save()
            text = None
            try:
                text = await self.one_attempt(cid, prompt, model_id, attempt)
            except asyncio.CancelledError:
                attempt.update(status="stopped" if self.operator_stop else "interrupted",
                               code="STOPPED" if self.operator_stop else "CANCELLED",
                               note="Stopped by the operator." if self.operator_stop else "Execution was cancelled.")
                self.finish(cid, record, attempt, 0)
                raise
            except Stopped:
                attempt.update(status="stopped", code="STOPPED", note="Stopped by the operator.")
            except ho.ResultFailure as exc:
                attempt.update(status="failed", code=exc.code, note=str(exc)[:1200], recovery=exc.recovery)
            except Exception as exc:  # never lose the record of an unexpected failure
                attempt.update(status="failed", note=f"{type(exc).__name__}: {exc}"[:1200], recovery="hard",
                               code="COMMIT_INCOMPLETE" if attempt.get("phase") in ("committing", "committed") else "UNEXPECTED_ERROR")
            if text is not None and attempt["status"] == "done":
                self.finish(cid, record, attempt, 0)
                return text
            if self.mode == "result_file_v1" and self.protocol["cartridges"][cid]["outputs"]:
                attempt["candidate_files"] = ho.snapshot_candidates(self.layout, cid, attempt)
            fingerprint = ho.sha256_bytes(json.dumps([attempt.get("code"), attempt.get("candidate_files"),
                                                      attempt.get("payload_sha256")], sort_keys=True).encode())
            stop_requested = self.operator_stop or self.core["rc"].load_stop_control(self.state_path) is not None
            decision = self.finish(cid, record, attempt, remaining - index - 1,
                                   repeated=fingerprint in fingerprints, stop_requested=stop_requested)
            decision["failure_fingerprint"] = fingerprint
            self.save()
            if decision["decision"] != "retry":
                if stop_requested:
                    self.stop_boundary()
                raise CartridgeFailed(cid, attempt)
            fingerprints.add(fingerprint)
            kind = "repair" if attempt.get("recovery") == "repair" and self.protocol["cartridges"][cid]["retry_safe"] else "publish"
            context = {"parent_attempt_id": attempt["attempt_id"], "code": attempt.get("code"),
                       "error": (attempt.get("note") or "")[:1200], "kind": kind,
                       "candidate_files": attempt.get("candidate_files", [])}
        raise CartridgeFailed(cid, record["attempts"][-1])  # pragma: no cover - loop always returns/raises

    def finish(self, cid, record, attempt, automatic_remaining, *, repeated=False, stop_requested=False):
        attempt.setdefault("finished", lg.now_iso())
        decision = lg.decide_retry(attempt, len(record["attempts"]), automatic_remaining,
                                   repeated=repeated, stop_requested=stop_requested)
        attempt["retry_decision"] = decision
        record["status"] = attempt["status"]
        level = "info" if attempt["status"] == "done" else "warn"
        lg.note(self.ledger, f"{cid} attempt {attempt['attempt_number']}: {attempt['status']}"
                + (f" ({attempt['code']})" if attempt.get("code") else "") + f" — {decision['reason']}", cid, level)
        self.save()
        return decision

    def on_event(self, attempt, police):
        def handle(event):
            kind = event.get("event")
            changed = False
            if kind == "session":
                attempt["session_id"] = event.get("id")
                if event.get("file"):
                    attempt["session_file"] = os.path.relpath(event["file"], self.run_base)
                attempt["sdk_version"] = event.get("sdkVersion")
                attempt["active_tools"] = event.get("activeTools")
                changed = True
            elif kind == "assistant":
                self._usage.append(event.get("usage"))
                attempt["tokens"] = tokens.live(self._usage, len(self._usage))
                changed = True
            elif kind == "published":
                attempt.setdefault("publish_receipts", []).append(
                    {k: event.get(k) for k in ("toolCallId", "bytes", "sha256")})
                changed = True
            if police is not None and kind in ("assistant", "tool_result") and self.loop_abort is None:
                before = police.observed, len(police.warnings)
                try:
                    police.observe(event)
                except antiloop.LoopDetected as exc:
                    self.loop_abort = exc.evidence
                    attempt["loop_evidence"] = exc.evidence
                    lg.note(self.ledger, f"Antiloop stopped attempt {attempt['attempt_number']}: {exc.evidence['reason']}", attempt.get("cid"), "warn")
                    self._abort_task = asyncio.ensure_future(self.abort_for_loop())
                    changed = True
                if (police.observed, len(police.warnings)) != before:
                    if len(police.warnings) > before[1]:
                        w = police.warnings[-1]
                        lg.note(self.ledger, f"Antiloop warning: {w['tool']} repeated {w['repetitions']} times.", attempt.get("cid"), "warn")
                    changed = True
                attempt["loop_police"] = police.snapshot()
            if changed:
                self.save()
        return handle

    async def abort_for_loop(self):
        result = await self.dispatch.cancel_active()
        self.loop_abort["abort_acknowledged"] = bool(result.confirmed)
        if not result.confirmed:
            self.loop_abort["process_terminated"] = await self.dispatch.terminate()

    async def one_attempt(self, cid, prompt, model_id, attempt):
        attempt["cid"] = cid
        self.loop_abort, self._abort_task, self._usage = None, None, []
        file_mode = self.mode == "result_file_v1"
        publish = None
        delivered = prompt
        if file_mode:
            attempt["inputs"] = ho.prepare_files(self.layout, self.ledger, cid)
            values = []
            for h in self.protocol["cartridges"][cid]["handover"]:
                payload = ho.committed_payload(self.layout, self.ledger, h["from"])
                try:
                    values.append((h["as"], h["from"], proto.select_pointer(payload, h["select"])))
                except proto.ProtocolError:
                    raise ho.ResultFailure("INPUT_INTEGRITY_FAILED", f"{h['from']} committed result lacks {h['select']!r}") from None
            attempt["handover_inputs"] = [{"as": n, "from": s} for n, s, _ in values]
            delivered = attempt_prompt(prompt, attempt, self.protocol, cid, self.layout, values, attempt["inputs"])
            base = ho.setup_attempt(self.layout, cid, attempt, delivered)
            publish = {"runId": self.run_id, "cartridgeId": cid, "attemptId": attempt["attempt_id"],
                       "resultPath": str(base / "publish" / "result.json")}
        attempt["prompt_sha256"] = ho.sha256_bytes(prompt.encode("utf-8"))
        attempt["delivered_prompt_sha256"] = ho.sha256_bytes(delivered.encode("utf-8"))
        enabled = self.protocol["loop_police"]["enabled"]
        police = antiloop.LoopPolice(self.protocol["loop_police"]) if enabled else None
        attempt["loop_police"] = police.snapshot() if police else {"enabled": False}
        seconds = effective_timeout(self.protocol, self.config)
        attempt["timeout_seconds"] = seconds
        self.save()
        try:
            result = await self.dispatch.run(delivered, model_id, publish=publish, timeout=seconds,
                                             on_event=self.on_event(attempt, police))
        except asyncio.TimeoutError:
            self.record_tokens(attempt, False)
            if not self.dispatch.terminated:
                raise ho.ResultFailure("STOP_UNCONFIRMED", "The timed-out worker could not be confirmed stopped.") from None
            raise ho.ResultFailure("ATTEMPT_TIMEOUT", f"Fresh session exceeded its {seconds}-second allowance; worker terminated.", "repair") from None
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                self.record_tokens(attempt, False)
                raise
            await self.settle_loop_abort(attempt)
            if self.operator_stop:
                raise Stopped() from None
            raise
        except WorkerFailure as exc:
            await self.settle_loop_abort(attempt)
            self.record_tokens(attempt, False)
            raise classify_worker_error(str(exc)) from None
        if self.loop_abort is not None:
            # Detection raced with normal completion; the evidence still stands.
            await self.settle_loop_abort(attempt)
        finish = result.get("finish")
        text = result.get("text") or ""
        attempt["finish"] = finish
        if result.get("sessionId") != attempt.get("session_id"):
            raise ho.ResultFailure("RESULT_IDENTITY_MISMATCH", "Worker session identity changed during the attempt.")
        attempt["session_file"] = os.path.relpath(result["sessionFile"], self.run_base)
        self.record_tokens(attempt, finish == "stop")
        if file_mode:
            attempt["session_archive"] = await self.retry_storage(
                attempt, lambda: ho.archive_session(self.layout, cid, attempt, result["sessionFile"]))
        if finish == "error":
            raise ho.ResultFailure("WORKER_FAILED", f"The model returned an error: {result.get('errorMessage') or 'unknown'}", "repair")
        if finish == "length":
            raise ho.ResultFailure("INCOMPLETE_RESPONSE", "The model response stopped at its output limit (stop reason 'length').", "repair")
        if finish != "stop" or not text.strip():
            raise ho.ResultFailure("NO_FINAL_ANSWER", f"The session ended without a saved final answer (stop reason {finish!r}).", "repair")
        if not file_mode:
            attempt.update(status="done", phase="completed", finished=lg.now_iso())
            return text
        attempt["outputs"], missing = ho.collect_outputs(self.layout, cid)
        status, note = proto.judge(text, missing, True, {"stop_reason": finish})
        if status == "halted":
            attempt.update(status="halted", code="HALT_RECEIVED", note=note)
            return None
        if status != "done":
            raise ho.ResultFailure("OUTPUT_MISSING" if missing else "DONE_MISSING", note, "repair")
        base = self.layout.attempt_dir(cid, attempt["attempt_id"])
        document, source = ho.read_result_file(base)
        attempt["source_result"] = source
        attempt["payload_sha256"] = (ho.sha256_bytes(json.dumps(document.get("payload"), sort_keys=True,
                                     allow_nan=False).encode()) if isinstance(document, dict) else None)
        receipts = attempt.get("publish_receipts") or []
        if not receipts or receipts[-1].get("sha256") != source["sha256"] or receipts[-1].get("bytes") != source["bytes"]:
            raise ho.ResultFailure("RESULT_RECEIPT_MISMATCH",
                                   "The result file was not written by this attempt's Magazine publisher tool.")
        pointers = ho.validate_envelope(document, ho.attempt_identity(self.run_id, cid, attempt), self.protocol, cid)
        attempt["phase"] = "committing"
        self.save()
        attempt["handover"] = await self.retry_storage(
            attempt, lambda: ho.commit_attempt_result(self.layout, cid, attempt, document, source, pointers))
        attempt.update(phase="committed", status="done", finished=lg.now_iso())
        self.ledger["cartridges"][cid]["status"] = "done"
        self.save()  # the consumer cannot be dispatched before this succeeds
        ho.committed_payload(self.layout, self.ledger, cid)
        return text

    async def settle_loop_abort(self, attempt):
        if self._abort_task is not None:
            await asyncio.gather(self._abort_task, return_exceptions=True)
        if self.loop_abort is None:
            return
        evidence = self.loop_abort
        attempt["loop_evidence"] = evidence
        if not evidence.get("abort_acknowledged") and not evidence.get("process_terminated"):
            raise ho.ResultFailure("STOP_UNCONFIRMED", "Antiloop could not confirm that the worker stopped.")
        raise ho.ResultFailure("LOOP_DETECTED", "Antiloop: " + json.dumps(
            {k: evidence[k] for k in ("reason", "repetitions", "signature")}, sort_keys=True), "repair")

    def record_tokens(self, attempt, finished_normally):
        rel = attempt.get("session_file")
        if rel:
            attempt["tokens"] = tokens.from_session(self.run_base / rel, finished_normally=finished_normally)
        else:
            attempt["tokens"] = tokens.unavailable("no_session")

    async def retry_storage(self, attempt, operation):
        import errno
        limit = int(self.protocol["recovery"].get("io_retries", 2))
        for n in range(limit + 1):
            try:
                return operation()
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EROFS) or n >= limit:
                    raise ho.ResultFailure("STORAGE_FAILED", str(exc)) from None
                attempt.setdefault("storage_retries", []).append({"at": lg.now_iso(), "error": str(exc)[:300]})
                await asyncio.sleep(0.2 * (n + 1))


def reconcile(run_base, has_local_task):
    """After a restart: a still-'running' attempt without its executor is interrupted.

    Never starts anything. Token usage is recomputed from the saved session.
    """
    if has_local_task:
        return None
    try:
        value = lg.load(run_base)
    except ValueError:
        return None
    if not value:
        return None
    changed = False
    for cid, record in value["cartridges"].items():
        for attempt in record["attempts"]:
            if attempt.get("status") == "running":
                attempt.update(status="interrupted", code="INTERRUPTED", finished=lg.now_iso(),
                               note="The Magazine service ended while this attempt was running; it was not resumed.")
                rel = attempt.get("session_file")
                attempt["tokens"] = (tokens.from_session(Path(run_base) / rel, finished_normally=False)
                                     if rel else tokens.unavailable("no_session"))
                attempt["retry_decision"] = lg.decide_retry(attempt, len(record["attempts"]), 0)
                record["status"] = "interrupted"
                lg.note(value, f"{cid} attempt {attempt.get('attempt_number')} marked interrupted after a restart.", cid, "warn")
                changed = True
    if changed:
        lg.save(run_base, value)
    return value
