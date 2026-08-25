# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1715 pass, 73 skip (env without
torch/transformers; that pack now importorskips) · **ruff:** clean

**Landed this session (orchestrated pass):** (1) TODO residual closed —
`append_event` broken-symlink guard mirrors the read side,
mutation-verified. (2) Capability-gap reports for pmquant + rl_stocks
(`docs/architecture/child-gap-*.md`): dskit/pipeline is a
rename-extraction of pmquant's engine and was a strict subset;
rl_stocks adds no new generic gaps; pmquant no longer imports rl_stocks
(their D-147/D-148 — pmquant's CLAUDE.md is stale on this). (3)
Engine-parity ports ADR-0022/0023: `concat`/`join`/`derive` +
`table-file`/`table-write`, registry 8 → 13, byte-faithful, 10+ mutants
killed, atomicity test strengthened. (4) ADR-0021 `children/`
convention: guide + pinned RUNNABLE skeleton exercising all three
seams, subprocess runner (timeout), isolation + pin tests, rename
runbook proven live. (5) Docs overhaul: pipeline README+CLAUDE (were
missing), root README rebalanced to the three pillars + child pattern,
~200 claims fact-checked, stale architecture/docstring claims fixed.
Three skeptic loops run; every finding fixed.

**Decisions awaiting user:** ADR-0024 (split policies + event bounds),
ADR-0025 (declared-model seam + trainlog), ADR-0026 (report renderer
parity) — PROPOSED in the decision log. Below-the-line gap candidates
(calibration/stats pack, scoring/distributions, backfill ergonomics,
job orchestrator) listed in `child-gap-pmquant.md`.

**Next session:** rule on 0024–0026 (0024+0025 together let pmquant's
adapter run on dskit's engine with only an import rename); optionally
incubate `children/pmquant` / `children/rl_stocks` per the sketches.
