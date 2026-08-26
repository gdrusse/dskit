# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `chore/gitignore-env-glob` (merged to `main`) · **Tests:**
children gates 5 passed, `intraday_poc` 69 passed / 8 skipped; the full
suite was not re-run (no `dskit/` code changed) · **ruff:** untouched.

**Landed this round: `intraday_poc` ran against real data for the first
time** — and it exposed a scaling defect the stub tests cannot see.

- **The onboarding seam is proven.** 2,013,682 Alpaca SIP 1-minute bars
  (AAPL+MSFT, 2021→now) acquired with 0 skipped, validated 5/5 rules with
  0 tripped, certified, published, and `onboarding verify` returned
  `problems: []`. The WORM chain holds end to end on real vendor data.
- **The production fit is proven.** `run-train.json` ran 6/6 nodes green;
  both LSTMs fit (891k / 745k examples) and the artifacts land as
  `qhat_aapl` / `qhat_msft` — the `live.py:DEFAULT_ARTIFACTS` contract,
  satisfied for real.
- **The walk-forward backtest is BLOCKED**, and this is the finding that
  matters. `IntradayBars._scan()` holds the whole stream about four times
  over — a 2M-entry `best` dict, a second 2M-dict `records` list cached
  permanently as `_snap`, and a third full copy in `run()` via
  `[dict(row) for row in self._scan()]` — while `fingerprint()`
  `json.dumps`es all 2M records into one string just to hash them.
  Measured peak is **14.3 GB for a single run** against an 18 GB WSL cap;
  three folds OOM (observed kill at 17.4 GB anon-rss). One fold did
  complete before the kill: picked 13,518 minutes, predicted 0.164506,
  realized 0.107305.
- **Config cannot fix it** — the `bars` node takes only
  `root`/`source`/`stream`, so there is no history knob. The fix is code:
  drop the redundant copies and hash incrementally.
- `.gitignore` now ignores `.env*` with `!.env.example` negated. The old
  `.env` rule let a Notepad-saved `.env.txt` carrying a live key pair sit
  untracked in the repo; the first attempt at the fix silently swallowed
  `.env.example` for every future child, which is why the negation is
  there. Both directions are verified.

**Next session:** fix the `bars` node's memory behavior (with a test that
pins it), then re-run `walkforward run-backtest.json` — everything
upstream of it is already proven. The live loop is untouched and needs
only a market-hours session. The defect generalizes: pmquant's ladder
data is far larger than 2M rows, so the same shape would bite harder
there.
