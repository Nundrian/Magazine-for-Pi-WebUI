# Install or update Magazine 0.1.0-pi.8-ui.3

Plugin ID: `magazine-pi`. This is the native Pi plugin, separate from Open WebUI
Magazine and the older Magazine Simple for Pi plugin. The distributed ZIP contains
code and documentation, not private config, saved runs, models or credentials.

## Requirements

Use a Linux backend, including Docker Desktop Linux containers on Windows.
Current UI acceptance is Pi WebUI **0.120.0 / SDK 1.5.2**. Inherited allowed pairs
are 0.96.1/1.0.2 and 0.115.0/1.1.0. Check the actual SDK resolved by the host after
any rebuild; do not override the exact compatibility guard merely to activate.

Magazine requires Python 3.11, `httpx==0.28.1` and `PyYAML==6.0.3`. The standard
upstream Docker runtime has no Python; add a Python virtual environment in an
image layer and set Magazine's `python` config path accordingly. The separately
supplied install kit includes a Dockerfile, example Compose file and complete
PowerShell instructions following Pi's official Docker-from-source method.

Magazine's internal state must support atomic no-overwrite directory publication.
On Docker Desktop, use a Docker-managed Linux volume for `/app/.pi-web/magazine`;
Windows `9p` bind mounts can reject the required operation. See
[Linux storage and verified migration](LINUX-STORAGE.md) before changing mounts
on an existing installation. Preserve and verify the old state first.

## Preserve data first

Update only at an idle Magazine checkpoint: no active worker, queued work or
uncertain STOP. Back up the installed `plugins/magazine-pi` folder/config, the
complete Pi data directory, and any external Magazine workspace. Preserve model
providers, permissions, other plugins and original project files. Never use
workflow import to install this software ZIP.

## Fresh installation using an existing compatible Docker host

Extract the plugin ZIP on Windows. Its root folder is `magazine-pi`, containing
manifest.json, index.mjs, client/ and the Python service files. For a Compose
service called `pi-web-ui`, from the folder containing its Compose file:

```powershell
Expand-Archive -LiteralPath .\Magazine-Pi-0.1.0-pi.8-ui.3-plugin.zip -DestinationPath .\package
$packagePath = (Resolve-Path .\package).Path
docker compose stop pi-web-ui
docker compose run --rm --no-deps --volume "${packagePath}:/opt/magazine-install:ro" pi-web-ui install /opt/magazine-install/magazine-pi --data-dir /app/.pi-web
```

The stop targets only the named instance. If using another service name/data
path, adapt those values. Verify the ZIP using the supplied SHA256SUMS.txt before
installing. On an update, `--force` permits replacing the existing plugin; preserve
its existing config. Never overwrite an existing private configuration with an
example.

For the install kit's fresh `./data:/app/.pi-web` and `./workspace:/workspace`
bind mounts, create `data/plugins/magazine-pi/config.json` with:

```json
{
  "workspace": "/workspace",
  "agentDir": "/app/.pi-web/agent",
  "python": "/opt/magazine-venv/bin/python",
  "allowedTools": ["read", "write", "edit", "bash", "grep", "find", "ls"]
}
```

These are example container paths. Select only needed tools per workflow. Keep
this file outside shareable archives. For a named data volume, write the same
configuration inside that volume rather than assuming a Windows `data` folder.

Start with `docker compose up -d`, use Pi's native token sign-in, then open
**Plugins → Magazine**. Confirm version 0.1.0-pi.8-ui.3. Native token, networking
and model-provider setup remain Pi's responsibility; no extra login service is
added by Magazine. An Ollama model must also appear in Pi's configured catalogue.

## Update only Magazine without restarting Pi

The deployed alignment update used Pi's native uninstall/install lifecycle and
ordinary browser connections to unload and reactivate only `magazine-pi`:

1. At idle, back up the plugin/config and runtime data. Stage the verified new
   plugin folder in an accessible local directory; preserve its private config.
2. Run `pi-web-ui uninstall magazine-pi --data-dir <actual-data-dir>` in the host.
3. Refresh/connect the browser and confirm Magazine has disappeared. Other
   plugins stay active. Do not use global **Plugins → Reload**.
4. Run `pi-web-ui install <staged-magazine-pi-folder> --data-dir <actual-data-dir>`.
   Ensure the existing private configuration is in place before activation.
5. Refresh the browser, confirm the new version and check the panels/catalogue.

Keep the staged source in persistent storage if it is recorded as the local
plugin source. Installation is not permission to change provider/reasoning/context
settings or start a workflow. Client-only asset polish can be published atomically
without reloading the running service; refresh the browser to load the new asset.

## Verify the interface and journey

Choose a workflow and explicitly **Load your workflow**. Review models/tools,
choose **Prepare workflow**, inspect its READY run, then choose **Start workflow**
only when execution is intended. Use **Previous work → View previous work** to
open a saved run without starting it. Confirm the individual-cartridge ledger,
full Chats list, Results and Manage. New appearance settings affect only Magazine.
See UI8-ALIGNMENT.md for exact evidence and testing limits.

## Rollback

At idle, reinstall the exact backed-up previous plugin/config through the same
Magazine-only procedure, or stop only the intended instance and reinstall via
Compose. Keep runtime data and workspaces: this release requires no migration.
Restore needed trash items with this version before reverting to a UI without
Restore. Restoring a full runtime backup discards subsequent work and must be a
separate deliberate action. Do not start committed-result runs under a plugin
that does not implement their protocol. Do not delete volumes/project folders.
