# Automatic model discovery validation — 0.1.0-pi.8-ui.5

Validated on 24 November 2026 with Pi WebUI 0.120.0 and SDK 1.5.2.

| Check | Result |
|---|---|
| Complete Python regression suite | 932 passed, 1 legacy skip |
| Node discovery and native lifecycle tests | 10 passed |
| Real SDK with metadata-only fake Ollama | Download/removal/outage/recovery and saved assignments passed |
| Fresh worker resolving an unconfigured discovered model | Resolved successfully; stopped before inference |
| Chromium client with changing catalogue | Automatic refresh, disabled missing selections, other providers and tools preserved |
| Actual installed native dropdown | Matched independent Ollama metadata; embeddings and stale IDs excluded |
| Native package install, CRC, source parity and repeated builds | Passed |

The legacy pi.5 upgrade case needs its older package and SDK 1.0.2 and remains
skipped. Regression inference uses scripted fixtures only. No local LLM or
workflow was started during the installed dropdown checks. No Ollama model was
downloaded or deleted for testing.

The actual shared selector was expanded in a temporary browser for inspection;
no selection or application data was changed by that presentation adjustment.
Tests verify that existing `models.json` bytes and saved-run assignments remain
unchanged and unavailable assignments are refused rather than substituted.

See [automatic model behaviour and limits](AUTOMATIC-MODELS.md). This update
adds no changes to the bundled core execution engine or recovery fences.
