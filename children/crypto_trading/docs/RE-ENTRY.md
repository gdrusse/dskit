# RE-ENTRY — crypto_trading

Updated: 2026-10-19 Eastern by GPT-6 Astra.
Previous note archived at docs/archive/re-entry/crypto_trading.md.

## Now

- **Recommendation: one pooled Kalshi BTC/ETH model, aligned-24 (method 24), and exactly two cells for the next prospective test.**
- KXETH15M: five minutes before close, native YES midpoint 00-10%, buy NO only under the frozen model-versus-executable-NO-ask-plus-fee signal.
- KXBTC15M: eight minutes before close, native YES midpoint 00-10%, buy NO under that same side-specific rule.
- On later retained W4 and W5, method 24 beat both raw and calibrated market log loss in both cells. ETH: W4 0.151148 vs raw 0.153524 (902 contracts), W5 0.158124 vs 0.160514 (478). BTC: W4 0.216612 vs 0.217844 (287), W5 0.205555 vs 0.208529 (167). The staged JSON retains exact calibrated scores.
- Historical 10-share candle-cost means for ETH NO were +1.25c/506 W4 and +0.97c/269 W5; BTC NO +1.37c/175 W4 and +1.66c/117 W5. BTC W4/W5 selected counts miss the frozen 200-contract floor.
- **These are retrospective later-set results, not an untouched holdout, verified delayed fills, or production approval.** The two original protected tests use different frozen rules and cannot be repurposed to validate this recommendation.
- Polymarket model 10 / BTC 11m / UP 80-90% is a separate research reserve: +3.37c on 324 primary displayed-book selections, but no distinct later settled outcome set and no corrected pass. No Polymarket cell is in this immediate recommendation.
- Complete historical audit: 77/77 Kalshi and 20/20 native BTC fixed runs; all 600 registered side/cells retained; 219 positive modeled scenarios among 300 assessable entries; zero corrected after-cost passes. Branch remains unmerged.
- Existing staged JSON now records these two cells and later retained score comparisons. Latest pre-wrap branch candidate was 27a47d61; no final independent reviews have approved the study.

## Next

- Keep work on the two method-24 Kalshi cells. Freeze a distinct future evaluation interval and exact source, executable-ask, fee, depth, delay, size, overlap and failure rules before its outcomes.
- Preserve the original overnight Oct 12-Nov 11/read Nov 11 and predictive Oct 17-Jan 15/read Jan 15 protocols; their overlap still needs owner reconciliation. Neither is an interim selector.
- Obtain two independent final correctness/integration reviews and resolve material findings before merging. Focused crypto checks had 125 passed/3 known failures; documentation budget checks passed 4/4. Do not rerun the full suite without relevant code changes or owner instruction.

## Blocked / owner decisions

- No certified untouched period is assigned to the two-cell test. W4/W5 and Oct 8-12 were inspected.
- Kalshi historical candles lack decision/entry timestamps and standing depth needed to verify fills; model forecast allowances are small.
- Polymarket ETH/SOL/XRP lack settled native labels and earlier training history; Polymarket BTC lead remains a reserve.
- No subagents or extra paid model/API work without owner approval. No shadow service or live trading.

## Read first

- docs/plans/2026-10-19-staged-multicurrency-selection.json
- docs/memos/2026-10-19-retained-history-study.md
- docs/memos/2026-10-18-executable-cells.md
- docs/memos/2026-10-19-retained-history-protocol.json

## Do not

- Claim these two cells are independently validated or profitable, drop the 600-entry audit, override protected reads, refit on protected outcomes, launch services/orders, or merge before required reviews.
