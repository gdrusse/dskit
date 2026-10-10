# RE-ENTRY archive — index_options

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

# Active handoff — 2026-10-09 (final): replay v2 complete under the accepted same-day fill

Owner accepted the same-day average fill as the assumption (the owned archive has no
next-session bar). Replay v2 a1c35e4b (config run-equity-condor-robust-backtest-v2.json,
identity 0135e9ff, candidate 383a6af3) ran clean: rho supported 10/10, passing 2025-10/11
at 0.01; nominal 1,259 trades net -$8,395 (131 original trades reproduce +$5,354.85 exactly;
the 1,128 previously refused solves -$13,750); all seven robust arms net negative in their
two trading months; 115 parity_failed refusals above the frozen 1e-5 stand; account study
refuses on 42 non-positive decision reservations. Read
children/index_options/docs/memos/2026-10-09-condor-executable-clock-result.md.
Backup condor-replay-v2-complete-v1 (22 files, sha256sums). Diagnostics: no code bug, MILP
exact (primal/dual harness), oracle control +$1.87M; research note
docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md: the $396k
claimed edge is 59% forecast drift above the market forward, dispersion and favourable
prints; the short put is leveraged long stock. Open small defect: DiscreteCDFGrid.
worst_expected_loss HiGHS tolerance (~6.7e-7/share at rho>0). Next: owner decides the
selector/forecast direction (shrink toward market, OTM + wing cap) before any replay. No 2026 access,
fits, acquisition, spending or deployment.

---

