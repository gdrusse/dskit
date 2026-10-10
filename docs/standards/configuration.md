# Configuration standards

- **JSON is the interface.** A config declares the whole process; the code reads it.
- **The identity hash is what a config IS.** sha256 over the config's canonical
  JSON with `notes` stripped and `env`/`outputs`/`schedule`/`tracking` excluded
  — so documentation and placement never change identity, but any change to what
  the run COMPUTES does. It names the run directory, keys the `$prev` run series,
  and is what a store pins at init. **Moving a hash orphans every prior run and
  every stored artifact keyed to it**, which is why adding or removing a graded
  field is a breaking change even when no behavior changed. Optional fields are
  emitted ONLY WHEN PRESENT for exactly this reason.
- **Comment rigorously and explanatorily.** Standard JSON has no `//` syntax and
  the loader uses `json.load`, so comments use the first-class **`notes`** field,
  supported on every config object (documents, nodes, splits, asset-model kinds,
  suites, …) and **excluded from the identity hash** (so documentation never
  changes a config's identity).
- A `notes` string should say *why*, not restate the key. Explain intent,
  trade-offs, and how to change the behavior.
- **Default-deny params.** A node lists the knobs it allows and refuses the rest,
  so a typo is an error, not a silent default.

```jsonc
{
  "name": "my-run",
  "notes": "What this config is for and how to adapt it — the 'why'.",
  "pipeline": {
    "<key>": {
      "uses": "<kind or pkg.module:Class>",
      "inputs": { "port": "$other_node.output" },
      "params": { },
      "notes": "Why this node is wired this way; what to change and when."
    }
  }
}
```
