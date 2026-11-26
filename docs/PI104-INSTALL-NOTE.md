# Installation notes — Magazine 0.1.0-pi.10.4

**Corrective candidate.** Use Pi WebUI 0.120.0 / SDK 1.5.2. Historical compatibility entries are not proof that the current confined worker works with those pairs. Pi 0.122.0 / SDK 1.6.1 failed the worker integrity check in the owner's testing. Keep the guard enabled.

The reviewed source and plugin contain older version labels in INSTALL.md. Those historical labels have been retained to keep all 85 files of the reviewed plugin byte-identical. Use the procedure in [INSTALL.md](INSTALL.md) with **Magazine-Pi-0.1.0-pi.10.4-plugin.zip**, and verify the actual installed manifest version is **0.1.0-pi.10.4**.

For an existing Docker deployment, check the exact host and SDK versions before installing:

```powershell
$container = 'pi-web-ui' # Replace with your actual container name.
docker exec $container pi-web-ui --version
docker exec $container node -p "require('/usr/local/lib/node_modules/pi-web-ui/node_modules/@earendil-works/pi-coding-agent/package.json').version"
```

Expected: 0.120.0 and 1.5.2. Python 3.11 and pinned Python dependencies are required. On Docker Desktop use a dedicated Linux-managed volume at `/app/.pi-web/magazine` as described in [LINUX-STORAGE.md](LINUX-STORAGE.md). Back up the existing plugin, settings and persistent state; update only an idle instance. Preserve other containers and applications.

Build the distributable from the repository root with `python scripts/build_release.py`. The original reviewed distribution ZIP SHA-256 is:

```text
aefb5cf8a16c73393255b05f763adca807ba7670c38640822994c6a1d316a7b0
```

The original delivered source ZIP SHA-256 was `7bb9feeba80a785345b3a57c35eea34d92224dfc4e17441242af5f290133cffd`. The published source tree adds this note and revises the root README; rebuilding its source ZIP will therefore produce a different source checksum. All 85 plugin files remain byte-identical to the reviewed distribution. The ZIP container checksum can differ when built on another OS because Python records the ZIP creator platform. Verify file contents against the reviewed package rather than assuming cross-platform ZIP byte identity.

The machine-specific installer from the owner's handoff targets a particular existing container and is not included in the general-purpose repository. The correction does not require any private configuration or saved run data to be published.

See [CHANGELOG.md](../CHANGELOG.md) for the hotfix and [PI103-CORRECTIVE.md](PI103-CORRECTIVE.md) for recovery limitations. Live model acceptance is not established by publishing the source.
