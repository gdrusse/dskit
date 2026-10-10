# RE-ENTRY — pmquant

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/pmquant.md` (dskit repo).

## Now

- The child landed 2026-09-04 (PR #7): `configs/run-e2e.json` is the proof
  document (22 nodes, run by `tests/test_e2e.py` on the synthetic world);
  `run-kalshi-ladders.json` is its real-data twin. dskit gained ADR-0075..0080
  (the `kalshi`, `polymarket`, `predexon` packs, `localtables`, the
  `observations` kind, the clause DSL).
- No activity recorded since.

## Next

Run the real-data twin on a machine holding `~/pmquant_data` once the owner
items below are settled.

## Blocked / owner decisions

- `PREDEXON_API_KEY` in the environment before `configs/source-predexon.json`
  can pull.
- The rulings listed in the dskit `TODO.md` under "Found by the pmquant child
  build".

## Read first

- `README.md`
- `configs/run-e2e.json`

## Do not

- Nothing trades; the twin is a backtest document.
