# Cross-application contract: Magazine ↔ 3D Resource Kit ↔ Unreal Connector (25 November 2026)

This is the supported integration after this repair. Future Magazines should copy the **workflow declaration**
below and give each cartridge only the short task contract it needs, instead of asking a model to discover
endpoints, ports or keys.

For the pi.10.3 candidate, see [compatibility and recovery](PI103-CORRECTIVE.md).
The integrations are optional. Use a separate project ID for each service.

## Responsibilities (unchanged; no duplication)
| Product | Owns |
|---|---|
| Magazine (Pi, pi.10+) | Orchestration, model execution, dependency preflight, progress, recovery, handovers, deterministic project checkpoints, the trusted host broker |
| 3D Resource Kit (0.4.3+) | Catalogue, owned assets, local files, project registry, asset staging into `<project>\Imports`, sharing project roots with the Connector |
| Unreal Connector (1.0.5+) | Authorised headless engine work: preview → owner approval → checkpoint → apply → fresh-process verify → rollback |

## Connection path (who holds which secret)
```
model ──tool call──▶ Pi worker (Landlock sandbox, no keys) ──private stdin/stdout──▶ Magazine service
Magazine service ──Bearer key──▶ http://host.docker.internal:8765  (Unreal Connector API, operationId = tool name)
                 ──Bearer key──▶ http://host.docker.internal:8766  (Resource Kit /tools/*)
3D Resource Kit ──ulk_ link key, loopback only──▶ Unreal Connector /app-link/*   (never reachable from Docker)
```
- Keys: Magazine `host-services.json` (0600, Magazine data folder; workers cannot read it). The Resource Kit key
  written by the Kit's own Pi pairing (Pi `agent/mcp.json`) is reused automatically.
- Human pages stay loopback-only: Connector `/account` and Kit `/app/` refuse the Docker host name (by design).

## Workflow declaration (Magazine manifest)
```yaml
schema_version: '1.0'
name: <workflow>
handover_mode: result_file_v1
required_paths: ['/workspace/Unreal Projects/<Project>']
requires:
  services:
    unreal_connector:
      operations: [health, capabilities, discover_projects, project_status, list_assets, inspect_asset,
                   list_actors, inspect_blueprint, run_validation, submit_change, get_job, cancel_job,
                   list_evidence, get_evidence]
      projects: [<project_id from discover_projects>]   # broker refuses any other project_id
      min_api: '0.2.0'
    resource_kit:
      operations: [whoami, search_catalogue, resolve_asset_reference, list_projects, inspect_project,
                   stage_asset_for_project, get_operation_status]
      projects: [<project_id from list_projects>]
      scopes: [library, projects_write]
  exclusive_unreal: true          # preflight refuses Start while any Unreal process runs
  approval_note: 'Level, Blueprint and import changes need your approval in Unreal Connector.'
project_checkpoint:
  root: '/workspace/Unreal Projects/<Project>/.magazine-level-v2'
  baseline_step: <last verified step, for a continuation>   # omit for a new workflow
  baseline_sha256: <SHA-256 of the reviewed state file>       # omit for a new workflow
  required: true
```
Ordinary workflows omit `requires` and `project_checkpoint` and behave exactly as before.

## Task contract to paste into an Unreal cartridge (short)
```
Use the magazine_host tool. Never look for keys, URLs or ports.
Project: unreal_connector project_id=<id>. Read state with project_status / list_assets.
Writes: submit_change(mode='preview') → read plan_hash → submit_change(mode='apply', confirm=<plan_hash>).
If the reply is approval_required: report the approval_id and end with HALT; the owner approves on the PC.
Engine jobs return job_id: poll get_job(job_id, wait_seconds=20) until succeeded/failed.
Write .magazine-level-v2/<step>.json {"step_id": "<step>", "status": "DONE", ...evidence...},
then call magazine_checkpoint. Never edit state.json. Publish, then DONE.
```

## Asset path (Kit → Connector)
1. Kit `resolve_asset_reference` (exact ID or path; never substitutes).
2. Kit `stage_asset_for_project` → files in `<project>\Imports\…`, returns an `import_files` hand-off.
3. Connector `submit_change` `change_type=import_files` preview → owner approval (always) → apply.
   Requires `"import_files"` in the Connector's `allowed_change_types`.

## Failure semantics a cartridge can rely on
| Situation | What the model sees | What it must do |
|---|---|---|
| Service down / key missing | Preflight refuses Start (no model runs) | — |
| 401 from a service | `status: 401` + "do not retry" hint | HALT |
| Undeclared operation or project | tool error from the broker | HALT, name the need |
| `approval_required` | 403 body with `approval_id` | HALT (owner approves); recovery prompt lists the id |
| Interrupted attempt | recovery prompt lists earlier `job_id` / `approval_id` / `plan_hash` | query them, do not resubmit |
| Checkpoint refused | tool error with the reason | HALT |
| DONE without checkpoint | `CHECKPOINT_NOT_COMMITTED` (not accepted) | — |

## Versions verified together
Magazine 0.1.0-pi.10 (Pi Web UI 0.120.0, SDK 1.5.2) · Unreal Connector 1.0.5 RC (API 0.2.0) · 3D Resource Kit 0.4.3 RC.
Verified by automated tests with a loopback Connector fixture; not yet with the installed Windows applications.
