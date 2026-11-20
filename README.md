# Magazine for Pi WebUI

**Experimental open-source Magazine plugin for Pi WebUI**. This is a separate implementation from [Magazine for Open WebUI](https://github.com/Nundrian/Magazine); their host APIs are not interchangeable.

## Current source release

The prepared source release is **`0.1.0-pi.8-ui.2`**. It adds Magazine-specific dark/light/follow-Pi themes, a clear **Load your workflow → Set up → Prepare workflow → Start workflow** sequence, previous-run viewing, cartridge summaries, and bounded read-only discovery retries.

The Pi runner, handover, scheduler, recovery, token accounting and persistence modules are unchanged from `pi.8-ui.1`, apart from the service version marker. The default policy remains five attempts total and 1,800 seconds per fresh attempt.

**Compatibility:** documented host acceptance on Pi WebUI 0.120.0 / SDK 1.5.2. Independent offline checks passed, but live LLM inference and a full security audit have not been performed for this update.

## Publication status

**Repository initialised; complete source import pending.** The full vetted source, deployment files, tests, original `ui.1`/`ui.2` release archives and two-version Git bundle have been prepared for import. This placeholder intentionally does not claim the plugin files are already present. Please do not clone this repository expecting an installable plugin until that import is complete.

Never commit `.env`, private `config.json`, provider credentials, sessions, run exports, workspaces or backups. Before updating a running Pi installation, stop workers, empty the queue, and back up its persistent data and workspace.

MIT-licensed; repository publication and installed-host verification are separate activities.
