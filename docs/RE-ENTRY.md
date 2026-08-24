# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1640 pass, 82 skip

**Landed:** `restapi` connector pack (ADR-0017) —
`dskit/onboarding/libs/restapi.py`, kind `restapi`, stdlib urllib only.
Declarative streams (path / params / records_path), pagination closed
vocabulary `none|cursor|page|offset`, one env-var credential
(header/param via format template), `since_param` server-side filtering
with the client-side cursor filter still applied, retry/backoff above a
single scripted-in-tests `_fetch` seam, query strings stripped from
errors. 24 new conformance tests (no network). Also fixed: shipped
`source-localfiles.json` failed its own default-deny — `check_config`
now exempts document-level `notes`; regression test guards both
examples. Package README/CLAUDE.md trees updated.

**Next:** tier-2 store packs (ADR-0011) — start fresh session; design
first (ADR before code). Other open seams: semantic validation above
the engines, more connector packs. `ruff` still unavailable in the
anaconda env — `pip install -e ".[dev]"` to lint (user hasn't approved
the install).

**Decisions awaiting user:** none.
