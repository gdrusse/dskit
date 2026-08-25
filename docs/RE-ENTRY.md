# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 2161 pass, 95 skip · **ruff:** clean

**Landed this round (owner directive: knock out every open item; all
TDD + skeptic-looped, every surviving mutant killed by a pinned test):**
(1) **ADR-0026 ported** — the full report-renderer surface
(`kinds_report.py` 409 → 1,498: CSV export, bounded tables with
truncation notes, ledger/hit-rate/decision renderers; boundary ruled
full-module via the `records.py` precedent). Review hardening beyond
the parent: both CSV headers and `_iso`'s full stamp are now pinned.
(2) **ADR-0025 residual closed** — TrainingCurve lines stream bare to
stderr during a run (installed only when the caller has no live stream
handler; removed on every exit path; stdout stays clean for the piped
report). (3) **ADR-0031** — walk-forward folds carry the document's
declared split `policy`: the merge-era refusal became pass-through,
per-fold ADR-0024 bounds binding, loud propagation, policy-less
parents proven hash-neutral.

**Engine parity with the parent fork: COMPLETE** (ADR-0022…0026 all
ported, 0031 extends beyond it). **ADR-0032 (owner-ratified): the
child is the adapter unit** — `pipeline_<venue>` sibling packages are
retired everywhere (ten in-code exemplars swept; docs state the ban).
pmquant's adapter content can run on this engine with an import rename
— per 0032 it migrates INTO `children/pmquant` as modules; the
`pipeline_kalshi` name does not survive.

**Decisions awaiting user: none.** Deferred by standing rulings only:
engine multi-writer coordination (needs a consumer + its own ADR) and
the move-plant listing residual (declared, not fixed — the fix defeats
the sqlite index). Minor recorded corners: mid-loop ConfigError after
completed folds discards the summary (docstring carve-out);
TrailingSplitSpec-parent policy pass-through is runtime-verified but
untested; sb3/matplotlib packs' tests skip here (libs absent).

**Next session:** incubate a child (pmquant or rl_stocks) per the
sketches — the pmquant one now doubles as the adapter-migration proof
(its old `pipeline_kalshi` content, as child modules, ADR-0032).
