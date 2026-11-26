"""Magazine Simple 0.2.0-reliability.3 workflow semantics for the Pi plugin.

Adapted from Nundrian/Magazine integrations/open_webui/function/
magazine_simple_reliability3.py at commit 09a8706e5f01fe6d8db0b8941ccf0d772b374005
(recovery_policy, loop_policy, load_workflow declarations, reply/JSON parsing,
select_pointer). Open WebUI chat, HTTP and storage code is not used here.

A workflow manifest accepted here is a superset of the strict v1 package
contract. The unchanged core still receives only {id, job_type, prompt}; the
extra declarations become a per-run protocol record (protocol.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from magazine import document as core_document
from magazine import package as core_package
from magazine import paths as core_paths
from magazine.schema import MagazineSchemaError

PROTOCOL_VERSION = 1
# pi.10: runs that declare host dependencies or project checkpoints are recorded as version 2, so an older
# Magazine refuses to resume them instead of silently running without the checkpoint gate.
PROTOCOL_VERSION_EXTENDED = 2
PROTOCOL_VERSION_TRANSACTIONS = 3
KNOWN_PROTOCOL_VERSIONS = (PROTOCOL_VERSION, PROTOCOL_VERSION_EXTENDED, PROTOCOL_VERSION_TRANSACTIONS)


def protocol_version(protocol):
    return PROTOCOL_VERSION_TRANSACTIONS if (protocol.get("requires") or protocol.get("project_checkpoint")) else PROTOCOL_VERSION


def validate_saved_extensions(value, cartridge_ids):
    """Validate extended declarations also when reopening persisted runs."""
    import checkpoint
    import host_tools

    declaration = value.get("project_checkpoint")
    requires = value.get("requires")
    if declaration is not None:
        if value.get("handover_mode") != "result_file_v1" or value.get("version") == PROTOCOL_VERSION:
            raise ProtocolError("project_checkpoint needs the extended result_file_v1 protocol")
        value["project_checkpoint"] = checkpoint.parse_declaration(declaration, cartridge_ids)
    if requires:
        if value.get("version") == PROTOCOL_VERSION:
            raise ProtocolError("Host dependencies require the extended protocol")
        value["requires"] = host_tools.parse_saved_requires(requires, value.get("version") == PROTOCOL_VERSION_EXTENDED)
    return value
HANDOVER_MODES = ("result_file_v1", "legacy_text")
MODE_ALIASES = {"legacy_chat": "legacy_text"}
DEFAULT_HANDOVER_MODE = "result_file_v1"
LEGACY_PI5 = "pi5_legacy"
HARD_ATTEMPT_CEILING = 5
DONE_LINE = '{"status":"DONE"}'
DONE_INSTRUCTION = ("When this step is finished:\n"
                    "- End your final message with this line, on its own: " + DONE_LINE + "\n"
                    '- If you cannot finish it, end with {"status":"HALT","reason":"why"} instead.\n'
                    "A reply that ends without one of these lines is treated as unfinished.")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
TOP_FIELDS = {"schema_version", "name", "cartridges", "handover_mode", "result_profile",
              "recovery", "loop_police", "required_paths", "requires", "project_checkpoint"}
CARTRIDGE_FIELDS = {"id", "model", "job_type", "prompt_file", "prompt", "handover",
                    "artifacts", "retry_safe", "constraints"}
IGNORED_FIELDS = {"constraints"}  # accepted by Magazine Simple and ignored there too


class ProtocolError(MagazineSchemaError):
    pass


DEFAULT_LOOP_POLICE = {"enabled": True, "warn_repeats": 3, "stop_repeats": 5,
                       "error_repeats": 3, "cycle_repeats": 3, "history_size": 64}


def recovery_policy(value=None):
    """Verbatim reliability.3 semantics: five total attempts, 1,800 s each."""
    value = {} if value is None else value
    allowed = {"max_attempts", "attempt_seconds", "max_retries", "seconds", "io_retries"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ProtocolError("recovery accepts max_attempts, attempt_seconds, max_retries, seconds and io_retries")
    if "max_retries" in value and (type(value['max_retries']) is not int or not 0 <= value['max_retries'] <= 5):
        raise ProtocolError("recovery.max_retries must be an integer between 0 and 5")
    attempts = value.get("max_attempts", min(5, value.get("max_retries", 4) + 1))
    seconds = value.get("attempt_seconds", value.get("seconds", 1800))
    io_retries = value.get("io_retries", 2)
    for name, item, low, high in (("max_attempts", attempts, 1, 5),
                                  ("attempt_seconds", seconds, 1, 7200),
                                  ("io_retries", io_retries, 0, 5)):
        if type(item) is not int or not low <= item <= high:
            raise ProtocolError(f"recovery.{name} must be an integer between {low} and {high}")
    if "seconds" in value and (type(value['seconds']) is not int or not 1 <= value['seconds'] <= 7200):
        raise ProtocolError("recovery.seconds must be an integer between 1 and 7200")
    if "max_attempts" in value and "max_retries" in value and attempts != min(5, value["max_retries"] + 1):
        raise ProtocolError("Conflicting max_attempts and max_retries")
    if "attempt_seconds" in value and "seconds" in value and seconds != value["seconds"]:
        raise ProtocolError("Conflicting attempt_seconds and seconds")
    return {"max_attempts": attempts, "attempt_seconds": seconds,
            "max_retries": attempts - 1, "seconds": seconds, "io_retries": io_retries}


def loop_policy(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_LOOP_POLICE):
        raise ProtocolError("Unknown loop_police configuration")
    policy = dict(DEFAULT_LOOP_POLICE, **value)
    if type(policy["enabled"]) is not bool:
        raise ProtocolError("loop_police.enabled must be boolean")
    for key in set(policy) - {"enabled"}:
        if type(policy[key]) is not int or not 2 <= policy[key] <= 256:
            raise ProtocolError(f"loop_police.{key} must be an integer between 2 and 256")
    if policy["warn_repeats"] >= policy["stop_repeats"]:
        raise ProtocolError("Antiloop warning threshold must precede stop threshold")
    if policy["history_size"] < max(policy["stop_repeats"], policy["error_repeats"], 4 * policy["cycle_repeats"]):
        raise ProtocolError("Antiloop history is too small for its thresholds")
    return policy


def cartridge_dir_name(cid):
    """A safe single path component for a cartridge identifier."""
    if SAFE_ID.fullmatch(cid) and cid not in (".", ".."):
        return cid
    return "cid-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:24]


def safe_relpath(value, what):
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{what} must be a non-empty relative path")
    if "\\" in value or "\x00" in value:
        raise ProtocolError(f"{what} must use forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ProtocolError(f"{what} must stay inside the workflow")
    return str(path)


def _text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{what} must be a non-empty string")
    return value.strip()


def _read_prompt(root, rel, where):
    path = core_paths.resolve_contained_path(str(root), rel)
    probe = Path(root)
    for part in PurePosixPath(rel).parts:
        probe = probe / part
        if probe.is_symlink():
            raise ProtocolError(f"{where}: prompt file must not be a link")
    if not path.is_file() or not stat.S_ISREG(os.stat(path).st_mode):
        raise FileNotFoundError(f"{where}: prompt file {rel!r} not found")
    return path.read_text(encoding="utf-8")


def parse_manifest(root):
    """Load magazine.yaml from an unpacked workflow directory.

    Returns ``(core_document, protocol, name)``. ``protocol`` is ``None`` for a
    plain v1 manifest (so its default protocol is chosen at preparation), or a
    dict of the extended declarations.
    """
    root = Path(root)
    manifest_path = root / core_package.PACKAGE_MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise FileNotFoundError("missing package manifest")
    data = core_package._parse_yaml(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProtocolError("magazine.yaml must be a YAML mapping")
    unknown = set(data) - TOP_FIELDS
    if unknown:
        raise ProtocolError(f"magazine.yaml: unknown fields: {sorted(unknown)}")
    version = data.get("schema_version", "1.0")
    if version != "1.0":
        raise ProtocolError("magazine.yaml: schema_version must be '1.0'")
    items = data.get("cartridges")
    if not isinstance(items, list) or not items:
        raise ProtocolError("magazine.yaml needs a non-empty 'cartridges' list")
    name = data.get("name")
    name = _text(name, "name") if name is not None else root.name
    cartridges, declarations, seen, outputs_by, ignored = [], {}, [], {}, []
    for index, item in enumerate(items, 1):
        where = f"cartridge {index}"
        if not isinstance(item, dict):
            raise ProtocolError(f"{where} must be a mapping")
        unknown = set(item) - CARTRIDGE_FIELDS
        if unknown:
            raise ProtocolError(f"{where}: unknown fields: {sorted(unknown)}")
        cid = item.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise ProtocolError(f"{where}: id must be a non-empty string")
        if cid in seen:
            raise ProtocolError(f"duplicate cartridge id {cid!r}")
        where = f"cartridge {cid!r}"
        if "model" in item and "job_type" in item:
            raise ProtocolError(f"{where}: use model or job_type, not both")
        model = _text(item.get("model", item.get("job_type")), f"{where} model")
        if ("prompt_file" in item) == ("prompt" in item):
            raise ProtocolError(f"{where}: give exactly one of prompt_file or prompt")
        if "prompt_file" in item:
            if not isinstance(item["prompt_file"], str):
                raise ProtocolError(f"{where}: prompt_file must be a string")
            prompt = _read_prompt(root, item["prompt_file"], where)
        else:
            prompt = item["prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProtocolError(f"{where} has an empty prompt")
        handover = []
        for h in item.get("handover") or []:
            if not isinstance(h, dict) or set(h) - {"from", "select", "as"}:
                raise ProtocolError(f"{where}: handover entries must be mappings of from/select/as")
            source = _text(h.get("from"), f"{where} handover 'from'")
            if source not in seen:
                raise ProtocolError(f"{where}: handover from {source!r} must be an earlier cartridge")
            pointer = _text(h.get("select"), f"{where} handover 'select'")
            if not pointer.startswith("/"):
                raise ProtocolError(f"{where}: handover select must start with '/'")
            handover.append({"from": source, "select": pointer,
                             "as": _text(h.get("as") or pointer.strip("/"), f"{where} handover 'as'")})
        artifacts = item.get("artifacts") or {}
        if not isinstance(artifacts, dict) or set(artifacts) - {"inputs", "outputs", "review"}:
            raise ProtocolError(f"{where}: artifacts accepts inputs and outputs")
        if "review" in artifacts:
            ignored.append(f"{cid}.artifacts.review")
        inputs = []
        for f in artifacts.get("inputs") or []:
            if not isinstance(f, dict) or set(f) - {"from", "path"}:
                raise ProtocolError(f"{where}: artifact inputs must be mappings of from/path")
            source = _text(f.get("from"), f"{where} input 'from'")
            path = safe_relpath(f.get("path"), f"{where} input path")
            if source not in seen:
                raise ProtocolError(f"{where}: input from {source!r} must be an earlier cartridge")
            if path not in outputs_by.get(source, ()):
                raise ProtocolError(f"{where}: {source!r} does not declare output {path!r}")
            inputs.append({"from": source, "path": path})
        outputs = [safe_relpath(p, f"{where} output") for p in artifacts.get("outputs") or []]
        if len(set(outputs)) != len(outputs):
            raise ProtocolError(f"{where}: duplicate declared output")
        if "retry_safe" in item and type(item["retry_safe"]) is not bool:
            raise ProtocolError(f"{where}: retry_safe must be boolean")
        if "constraints" in item:
            ignored.append(f"{cid}.constraints")
        seen.append(cid)
        outputs_by[cid] = set(outputs)
        cartridges.append({"id": cid, "job_type": model, "prompt": prompt})
        declarations[cid] = {"handover": handover, "inputs": inputs, "outputs": outputs,
                             "retry_safe": bool(item.get("retry_safe", bool(outputs))),
                             "dir": cartridge_dir_name(cid)}
    dirs = [d["dir"] for d in declarations.values()]
    if len(set(dirs)) != len(dirs):
        raise ProtocolError("cartridge identifiers collide as directory names")
    extended = bool(set(data) - {"schema_version", "name", "cartridges"}) or any(
        set(i) - {"id", "model", "prompt_file"} for i in items)
    document = {"schema_version": "1.0", "cartridges": cartridges}
    from magazine import schema as core_schema
    core_schema.validate_magazine(document)
    mode = data.get("handover_mode", DEFAULT_HANDOVER_MODE)
    mode = MODE_ALIASES.get(mode, mode)
    if mode not in HANDOVER_MODES:
        raise ProtocolError("Unknown handover_mode")
    profile = data.get("result_profile", "generic")
    if profile not in ("generic", "magazine.task.v1"):
        raise ProtocolError("Unknown result_profile")
    protocol = {"handover_mode": mode, "result_profile": profile,
                "recovery": recovery_policy(data.get("recovery")),
                "loop_police": loop_policy(data.get("loop_police")),
                "cartridges": declarations, "ignored_fields": ignored,
                "extended_manifest": extended,
                "required_paths": required_paths(data.get("required_paths"))}
    # pi.10: optional host-application dependencies and deterministic project checkpoints. Absent in
    # ordinary workflows, whose protocol records are unchanged.
    if data.get("requires") is not None:
        import host_tools
        try:
            protocol["requires"] = host_tools.parse_requires(data["requires"])
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
    if data.get("project_checkpoint") is not None:
        import checkpoint
        try:
            protocol["project_checkpoint"] = checkpoint.parse_declaration(
                data["project_checkpoint"], [c["id"] for c in cartridges])
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        if mode != "result_file_v1":
            raise ProtocolError("project_checkpoint needs handover_mode result_file_v1")
    if mode == "legacy_text" and any(d["handover"] or d["inputs"] or d["outputs"] for d in declarations.values()):
        raise ProtocolError("legacy_text workflows cannot declare committed handovers or artifacts; use result_file_v1")
    return document, protocol, name


WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s`'\"<>|*?]+")


def required_paths(value):
    """Optional workflow preflight: absolute container paths that must exist."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ProtocolError("required_paths must be a list of at most 32 absolute container paths")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.startswith("/") or len(item) > 1024 or "\0" in item \
                or any(part == ".." for part in item.split("/")):
            raise ProtocolError("required_paths entries must be absolute container paths such as /projects/MyProject")
        if item not in out:
            out.append(item)
    return out


def check_required_paths(paths):
    """Missing or non-directory required paths, as operator-readable problems."""
    import os
    problems = []
    for item in paths or []:
        if not os.path.isdir(item):
            problems.append(f"{item} is not available inside the Pi container" if not os.path.exists(item)
                            else f"{item} exists but is not a folder")
    return problems


def windows_path_hints(document):
    """Windows drive paths named in prompts (informational; never blocks)."""
    found = []
    for c in document.get("cartridges", []):
        for match in WINDOWS_PATH.finditer(c.get("prompt", "")):
            path = match.group(0).rstrip(".,;:)")
            if path not in found:
                found.append(path)
    return found[:10]


def default_protocol(document, mode=DEFAULT_HANDOVER_MODE):
    """Protocol for a plain v1 document or pasted JSON workflow."""
    return {"handover_mode": mode, "result_profile": "generic", "recovery": recovery_policy(),
            "loop_police": loop_policy(), "ignored_fields": [], "extended_manifest": False, "required_paths": [],
            "cartridges": {c["id"]: {"handover": [], "inputs": [], "outputs": [], "retry_safe": False,
                                     "dir": cartridge_dir_name(c["id"])} for c in document["cartridges"]}}


class _Plan:
    def __init__(self, name, document):
        self.name = name
        self.cartridges = ()
        self.document = document


class LibraryDocuments:
    """Drop-in for ``magazine.document`` used by the frozen library helpers.

    Unpacked library workflows are read with the extended manifest parser;
    every other source is delegated to the unchanged core loader.
    """
    package = core_package

    @staticmethod
    def load_document(path):
        source = Path(path)
        if source.is_dir():
            document, protocol, name = parse_manifest(source)
            return core_document.LoadedMagazine(document=document, plan=_Plan(name, document))
        return core_document.load_document(path)


# ------------------------------------------------------------ reply parsing
_FENCE = re.compile(r"(?ms)^[ \t]*(```+|~~~+)[ \t]*(?:json)?[ \t]*\r?\n(.*?)^[ \t]*\1[ \t]*$")


def json_objects(text):
    found = []

    def add(candidate):
        try:
            value = json.loads(candidate)
        except Exception:
            return
        if isinstance(value, dict):
            found.append(value)

    stripped = (text or "").strip()
    add(stripped)
    for match in _FENCE.finditer(text or ""):
        add(match.group(2).strip())
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[ \t]*\{", text or ""):
        try:
            value, _ = decoder.raw_decode(text, match.end() - 1)
        except Exception:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def _halt_text(obj):
    return str(obj.get("reason") or obj.get("message") or "The model replied HALT.")


def halt_reason(text):
    for obj in json_objects(text):
        if obj.get("status") == "HALT":
            return _halt_text(obj)
    return None


def _status_object(value):
    status = str(value.get("status") or "").strip().upper() if isinstance(value, dict) else ""
    return dict(value, status=status) if status in ("DONE", "HALT") else None


def closing_object(text):
    plain = (text or "").rstrip()
    text = plain.rstrip("`~*").rstrip()
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"(?m)^[ \t`*]*\{", text))):
        try:
            value, end = decoder.raw_decode(text, match.end() - 1)
        except Exception:
            continue
        if end == len(text):
            return _status_object(value)
    start = max(0, len(plain) - 4000)
    for match in reversed(list(re.finditer(r"(?<=[.!?])[ \t]+\{", plain[start:]))):
        try:
            value, end = decoder.raw_decode(plain, start + match.end() - 1)
        except Exception:
            continue
        if end == len(plain):
            return _status_object(value)
    return None


def judge(final, missing, require_done, facts=None):
    """(status, note): verbatim reliability.3 decision, Pi stop reason facts."""
    closing = closing_object(final) or {}
    if closing.get("status") == "HALT":
        return "halted", _halt_text(closing)
    closing = closing.get("status")
    quoted = halt_reason(final) if closing != "DONE" else None
    if quoted and not require_done:
        return "halted", quoted
    unfinished = ""
    if require_done and closing != "DONE":
        facts = facts or {}
        seen = [f"Pi recorded stop reason {facts['stop_reason']!r}"] if facts.get("stop_reason") else []
        unfinished = ("Unfinished: the final message did not end with " + DONE_LINE + ", so the step is not "
                      "known to be complete" + (" (" + "; ".join(seen) + ")" if seen else "")
                      + (". A HALT object appears in the final message but is not its last line, so it was "
                         "not taken as a HALT" if quoted else "")
                      + ". Files and the saved session are kept.")
    if missing:
        return "failed", ("Required output file(s) not found: " + ", ".join(missing)
                          + (". " + unfinished if unfinished else ""))
    if unfinished:
        return "failed", unfinished
    return "done", None


def result_json(text):
    objects = [o for o in json_objects(text) if o != {"status": "DONE"}]
    return objects[0] if objects and (text or "").strip().startswith("{") else (objects[-1] if objects else None)


def select_pointer(value, pointer):
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ProtocolError(f"value {pointer!r} not found")
    return value
