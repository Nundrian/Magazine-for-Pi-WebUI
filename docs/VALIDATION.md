# Reproducible verification

## Current access-simplification candidate

For pi.5-rc.1 run the focused tests in `tests/test_workspace_access.py` alongside
the Pi SDK integration/model/export suites. Native packaged UI acceptance uses
`scripts/native_ui_smoke.py` with pi.4-rc.1 as the previous plugin ZIP; select one
Firefox desktop case and one authenticated Chromium mobile case via `--case`.
`scripts/native_default_smoke.py` verifies installation without a custom config.
`scripts/simplification_live.py` accepts an explicitly chosen Ollama model/endpoint
for two small live cartridges; it creates only disposable Pi/model configuration.
See SIMPLIFICATION.md for actual results and boundaries. Older driver commands
below describe historical candidates, not the current no-key interface.

The original release was extracted into `reference/owui-beta5/` and tested there.
The authoritative original directory is never a test target. That isolated copy
reproduced 611 core + 244 Function + 134 JavaScript UI/sidebar tests. Python used
3.11.9 and the original pinned requirements; the JavaScript rerun used installed
Windows Node 24.14.0 rather than historical Node 22.16.0.

The standalone source contains the unchanged core suite in `tests/core/`, its
fixtures under `tests/examples/`, and actual Pi SDK integration tests in
`tests/test_pi_integration.py`. On Linux with the evidenced Pi SDK installed:

```sh
python3 -m venv .test-venv
.test-venv/bin/pip install -r requirements-test.txt
PYTHONPATH=magazine-pi .test-venv/bin/python -m pytest tests -q -s
```

Put pytest temporary data on a Linux filesystem. A Windows bind mount does not
supply the atomic no-replace publication operation used by the library. This is
an inherited platform constraint, not a mocked-away test failure.

`scripts/test-pi.sh` runs from this source directory using the selected Python
environment (`MAGAZINE_TEST_PYTHON`, default python3). The historical Docker
test mounted only the development folder and Linux temporary storage, with
`--network none`.
The real Pi SDK called a deterministic local HTTP provider in that container.
Responses were injected, while sessions, routing, tool execution, persistence,
abort, parsing and ownership were real.

`scripts/native_smoke.py` runs an isolated actual Pi Web UI server and plugin,
checks native HTTP/WebSocket discovery, binary ZIP import, authentication and
execution, then preserves its snapshot under `evidence/`. `--keep` holds the
test server for browser review. The browser review used localhost:8797 and
the actual native plugin tab. Test ports/data directories must be independent
of any running service.

`scripts/live_llm.py --model MODEL --ollama-url URL` explicitly assigns an
operator-supplied available model via Ollama. It runs two independent text cartridges,
a read-tool cartridge and a streaming STOP probe against development fixtures.
It records actual model digest, outputs, state and saved sessions. It is an
optional live check, not part of the deterministic regression suite. It requires
the configured endpoint and does not install/download any model.

`scripts/prepare_reference.py` records/copies the frozen core and helper source
segments from the isolated baseline. `scripts/build_release.py` checks these
hashes and builds explicit allowlisted plugin/source archives with fixed ZIP
metadata, CRC and byte-for-byte source parity. Build twice and compare checksums
to establish reproducibility. Neither release contains runtime keys, chat
histories, cached dependencies, the original project, or private evidence.

Final packaging checks are retained separately under `evidence/release-finalisation/`.
`scripts/verify_release.py` compares the full intended inventory, every member's
source bytes, copied installation/security/acceptance documents, core/helper
hashes, release manifest and checksum list against a saved first-build manifest.
`scripts/verify_package.py` then extracts the rebuilt plugin ZIP and invokes the
real Pi installer in a clean isolated data directory. Run the focused native
smoke with `--plugin-source` pointing at that installed directory, rather than
the development plugin. These checks do not rerun the successful regression
suite or live-model scenarios.

Runtime testing was on Linux Python 3.11.2 and Node 24.21.0 in the installed
Pi image. Alternate versions, native Windows Python, hostile multi-tenant
deployments, arbitrary extensions/providers, and proxy-prefix setups remain
outside acceptance. The Docker example is optional; the installed host/SDK
contracts are pinned and checked independently of it.

## Portable browser authentication, clean installation and upgrade

Install `requirements-browser-test.txt` in an isolated test environment and run
`python -m playwright install --with-deps firefox chromium`. These browser tools
are test-only; they are not plugin runtime dependencies. On a compatible Linux
Pi host, with fresh isolated scratch/data and no production mounts:

```sh
python scripts/portable_auth_smoke.py \
  --plugin-zip /path/to/Magazine-Pi-0.1.0-pi.2-plugin.zip \
  --previous-plugin-zip /path/to/Magazine-Pi-0.1.0-pi.1-plugin.zip \
  --scratch /path/to/new-linux-scratch \
  --evidence /path/to/test-evidence
```

The driver uses a disposable token, real Pi CLI/host/SDK, actual restarted
Firefox/Chromium persistent profiles, and a deterministic HTTP provider. It
tests tokenless/authenticated fresh installs and upgrades, native view/API,
browser process restarts, host restarts, token rotation and saved data/config.
It never reads a production token or connects to a live LLM. Port 8787 must be
free in the isolated network namespace. It does not repeat the 622 regressions.
Persistent-context close terminates the browser, and relaunch uses the same
profile, as documented by [Playwright's Python API](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).

## Native UI styling candidate

`scripts/native_ui_smoke.py` uses the same isolated Linux host/SDK fixture and
browser test requirements, installing validated pi.2 before the candidate to
test preservation. Supply candidate/previous ZIP paths, a NEW Linux scratch
path and a test-only evidence directory:

```sh
python scripts/native_ui_smoke.py \
  --plugin-zip /path/to/Magazine-Pi-0.1.0-pi.3-rc.1-plugin.zip \
  --previous-plugin-zip /path/to/Magazine-Pi-0.1.0-pi.2-plugin.zip \
  --scratch /path/to/new-linux-scratch --evidence /path/to/evidence
```

It drives every existing view control in Firefox/Chromium, native dark/Ayu Light
themes and 1440px/390px layouts, including active streaming STOP, recovery,
history and actual downloads. The provider is deterministic, not a live LLM.
An optional retained completed-case report avoids repeating already passing
matrix cases after a test-driver correction. This is separate from the preserved
622-test baseline; no backend changes justify rerunning that suite.

Release parity verification with `--previous-plugin` and `--style-only-client`
requires every non-client runtime file to match pi.2 and requires exact client
markup/JavaScript identity after removing just its one embedded style block.
`scan_public_release.py` scans the exact staged GitHub source and both ZIPs,
optionally matching privately supplied credentials without logging their values.
