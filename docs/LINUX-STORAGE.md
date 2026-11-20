# Atomic workflow import on Docker Desktop

Magazine publishes a fully validated, synchronized directory with Linux
`renameat2(RENAME_NOREPLACE)`. This prevents replacement even when another
process creates an empty destination directory. The importer deliberately has
no ordinary-rename fallback. Nested workflow trees must become visible together.

Some Docker Desktop Windows bind mounts expose `9p`/DrvFS and reject this call
with `EINVAL` (22), despite allowing writes and directory synchronization.
Keep Magazine's internal state on a Docker-managed Linux volume. Project
workspaces can remain separate Windows bind mounts. A read-only project mount
is appropriate for an inspection workflow.

Export's older hard-link fallback is retained: its flat, fixed file set uses a
last-published manifest. That protocol does not apply to nested imports, whose
readers use an existing `magazine.yaml` file and directory tree.

## Existing installations: preserve state before switching

1. Identify the exact Compose project, service, data mount and plugin config.
   Confirm no active Pi session, Magazine worker, uncertain STOP or queue. Back
   up the complete Pi data directory, plugin/config, Compose files and external
   workspaces. Record checksums. Stop only the selected idle Pi service.
2. Create a **new empty** named Linux volume. Mount the old `magazine` state
   read-only at `/old` and the new volume at `/new` in a disposable helper using
   the same image. Mount this release's `scripts` read-only. As root, run:

   ```sh
   python3 /scripts/migrate_storage.py /old /new
   python3 /scripts/migrate_storage.py /old /new --verify-only
   ```

   Save both JSON inventories privately. They compare every file's SHA-256,
   size, directory/file type, permissions and numeric owner/group, including
   empty directories. The helper flushes copied files and directories, rejects
   links/special files, refuses active state and nonempty copy destinations,
   and never removes the source. A failed copy is unselected; investigate it
   and use a new empty volume for any retry. Never merge over existing state.
3. Add an **external** volume definition to the existing Compose configuration,
   using the verified volume's exact name. Mount it at the same container
   Magazine state path under the existing Pi data mount:

   ```yaml
   services:
     pi-web-ui:
       volumes:
         - ./data:/app/.pi-web
         - magazine-state:/app/.pi-web/magazine
         - ./workspace:/workspace
   volumes:
     magazine-state:
       external: true
       name: your-verified-existing-volume
   ```

   `external: true` makes a missing volume an error rather than silently creating
   an empty replacement. Explicitly configured input/finished-work directories
   outside the state root need separate preservation and filesystem checks.
4. Recreate **only** the affected service (`docker compose up -d --no-deps
   pi-web-ui`). Check effective mounts, UID/GID, inventory and versions before
   any API operation changes state. A mount switch cannot use plugin-only reload.
5. Import a bounded workflow, preview all prompts, prepare READY without starting
   inference, refresh, and verify discovery/persistence after an idle restart.
   Reject a duplicate and compare the original. Retain the old data and backup.

For a new installation, initialize a Linux state volume with the container
service user's UID/GID and private permissions before mounting it. Do not use
an uninitialized volume to repair an existing installation.

## Diagnostics and rollback

pi.8-ui.3 distinguishes unsupported operations, permissions, conflicts and
unexpected I/O errors. Server logs include a `Magazine filesystem:` JSON record
with operation, exact errno/symbol and storage directory. Browser messages omit
configured paths. A failure after publication but during directory fsync reports
an uncertain durability outcome; refresh and inspect before retrying.

To undo a plugin-only update at idle, use Pi's native uninstall/install lifecycle
with the backed-up plugin/config. To undo a mount migration **before new work**,
restore the backed-up Compose configuration and recreate only that service.
After new imports/runs, the Linux volume is authoritative: back it up and migrate
the current state to another verified empty compatible Linux destination. Simply
removing its mount would expose the stale pre-migration Windows copy and hide
new records. Never delete the volume or use `compose down -v` as rollback.
