# Automatic Ollama models — pi.8-ui.5

Magazine's workflow model selectors use the current library of the configured
`ollama` provider. Download a chat model into Ollama and it appears; remove it
and it stops being offered. No manual entry in `models.json` is required.
The visible Magazine view refreshes the catalogue every 15 seconds. Opening the
view or choosing Refresh list also refreshes it. Prepare, Start and each fresh
worker independently recheck availability. No inference runs during discovery.

Discovery reads `GET /api/tags` and `POST /api/show` at the existing provider's
base URL, accepting either an Ollama root or its `/v1` compatibility URL. It uses
the SDK's resolved authentication. Only models advertising `completion` are
offered, so embedding-only models are excluded even when they also advertise
tools or thinking. Metadata failures omit the affected model with a diagnostic.
An unreachable Ollama offers no Ollama models; it never falls back to stale
configured names. Other providers remain available, and refresh retries
automatically. No model is downloaded, deleted or loaded by this mechanism.

The adapter registers definitions in the worker's memory. It does **not** rewrite
`models.json`, remove custom definitions, change authentication or rewrite any
saved-run assignments. Installed models keep their exact existing configured
settings, including names, reasoning, input types, limits and compatibility
overrides. A removed model's configuration is retained, so reinstalling its exact
ID restores those settings. A saved run still shows its recorded model; Start
refuses an unavailable assignment rather than substituting another model.

For a newly discovered model, capabilities come from Ollama metadata. Its context
and output limits follow the first configured model's explicit local limits,
capped by the reported model context; absent configured limits use 32,768 context
and 8,192 output tokens. No local inference, image or tool-call reliability is
implied by an advertised capability.

If a selected model disappears while editing a new workflow, its selection is
retained as a disabled **unavailable** entry. This applies to the shared and
per-cartridge selectors and prevents silent fallback to a different model.
Choose another installed model explicitly. Existing runs and exports retain
their original assignments and historical evidence.

This is Magazine's worker catalogue; Pi's ordinary chat catalogue is managed by
the host separately. The implementation uses the supported SDK in-memory model
registration interface and adds no background service, configuration writer or
synchronisation database.

Official metadata references:
[list installed models](https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models),
[show model details](https://docs.ollama.com/api-reference/show-model-details).
