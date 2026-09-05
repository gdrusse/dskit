# Research

Every finding lives in a **topic folder**. No markdown in this root
(legacy flat files stay). New writes:

```
docs/research/<topic>/<YYYY-MM-DD>-<name>.md
docs/research/<topic>/<YYYY-MM-DD>-synthesis.md
```

Use `record-research` (and `deep-research` for multi-agent work). The
journal CLI is the only writer:

```bash
python -m dskit.journal research "TITLE" --topic <topic> --name synthesis --body-file <draft>
python -m dskit.journal research "TITLE" --topic <topic> --name <subagent> --body-file <draft>
```

Re-running a topic adds new dated files in the same folder. Never write
these files by hand. Never edit the generated decisioning README.
