# Simple two-cartridge workflow

ZIP this folder's contents with `magazine.yaml` at the archive root. Import it
in Magazine's native Pi tab, preview both cartridges, choose the models and
prepare a READY run. Choose Start workflow explicitly to execute it.

The first cartridge requests a fixed token. The second asks an independent
arithmetic question. They execute in manifest list order and do not share
conversation messages or feed one result into the next prompt.

`concise` and `reasoning` are arbitrary routing keys, not installed model names
or reasoning settings. The native UI maps the cartridges to available Pi
provider/model IDs; shared and independent assignments are both supported.

```python
model_map = {
    "concise": "provider/YOUR_FIRST_MODEL_ID",
    "reasoning": "provider/YOUR_SECOND_MODEL_ID",
}
```

Choose different IDs to exercise per-cartridge model routing. Both keys can
also resolve to the same model. Magazine saves the exact returned result before
advancing; it does not check whether the fixed token or arithmetic answer is
correct. Use a fresh state directory for a new run.
