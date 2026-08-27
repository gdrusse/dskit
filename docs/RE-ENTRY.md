# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Two rounds landed on main this day (2026-08-26), from two sessions.**

## Round A — §14 rulings + four graduated TODO clusters (ADR-0033…0036)

**Branch:** `feat/todo-graduation-adr-0033-0036` (merged to `main`) ·
**Tests:** full suite green (2389 passed, 108 optional-lib skips) ·
**ruff:** clean.

- **§14 answered.** All eight owner questions ruled conceptually and
  recorded in the proposal (`docs/children_design_proposals/pmquant.md`
  §14): hold-at-PROPOSED, `q_hold` unset, high-precision cross-venue
  dedup, coverage bar at graduation, no MIO HPO, recorder on a VPS
  (host TBD), E6a OFF, **Tier B ratified with sunset at TODO-8**. The
  proposal stays PROPOSED; P0 still needs the owner's go.
- **ADR-0033** — stats seam: `stat_test` evidence self-description, the
  studentized recentered cluster bootstrap-t as a `method` (closed
  tuple), `register_correction` + `weighted-bh` with a `weights` input
  port. **TODO-2, the deploy→size blocker, is closed.**
- **ADR-0034** — the `cal` split band: fourth split name in the val
  window's tail, trailing `cal_days` (+1 boundary discipline),
  straddle-ledger + planner + walkforward guards.
- **ADR-0035** — torch `monitor` + best-state restore; trainlog's
  silent fallback removed; divergence-safe metric monitors.
- **ADR-0036** — onboarding codec: extension-declared gzip, reserved
  `storage` config block, deterministic members, pre-commit decode
  guard. The ratified Tier-B sunset path exists.
- **Validated:** a 26-agent adversarial review (5 lenses,
  refute-by-default verification); 16 confirmed findings fixed same-day,
  with review-amendment records inside each ADR. The identity/hash
  freeze held — zero movement for existing artifacts. Two confirmed
  findings live in `children/intraday_poc/` (hands-off from this
  session) — see the action items under Round B.

## Round B — intraday_poc's first real-data run (other session)

- The onboarding seam and the production fit are **proven on real
  data** (2M Alpaca SIP bars acquired/validated/certified/published,
  `verify` clean; both LSTMs fit and land their artifacts).
- The **walk-forward backtest is BLOCKED** by an `IntradayBars._scan()`
  memory defect (~4x stream copies; 14.3 GB peak, folds OOM) — logged in
  `TODO.md`; the fix is child-side code plus a peak-pinning test.
- `.gitignore` now ignores `.env*` with `!.env.example` negated.

**Action items FOR the POC session** — two Round-A review findings,
confirmed by execution, that live in `children/intraday_poc/` (Round A
was hands-off there; neither bites until a config opts in):

1. **Score kinds refuse the new `"cal"` split.**
   `intraday_poc/nodes.py:289` (`ForecastRows`) and `:380` (`SelectOne`)
   validate `params.split in ("train", "val", "test")`; dskit now has a
   fourth name (ADR-0034) and the planner accepts `"cal"` for score
   roles. Widen both tuples (and their messages) to
   `("train", "val", "cal", "test")` when the child wants calibration
   blocks — or leave as-is deliberately if it never will.
2. **The replay reader would miss gzipped observations.**
   `intraday_poc/nodes.py:118-121` globs
   `observations/<source>/*/<stream>.jsonl` by literal extension. If any
   source it consumes ever sets `storage.observations_codec: "gzip"`
   (ADR-0036), those rows silently vanish. Adopt
   `dskit.onboarding.codec.resolve_stream_file` / `iter_text_lines`
   (extension-sniffing, loud on corruption) before flipping any codec.

**Next session:** Round-A thread — pmquant ratification/P0 on the
owner's word (rulings 1–3, 5–7 bind the build); §13 gaps
5/6/7/9/10/11/12 remain in `TODO.md`, ADR-less until graduated.
Round-B thread — fix the `bars` node memory, re-run the backtest.
