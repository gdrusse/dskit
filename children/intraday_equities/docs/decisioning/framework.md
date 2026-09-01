# Training framework (2026-08-31)

Owner-locked. Seams: ADR-0050…0054, ADR-0055. H/L/keep: [decision-hl-scan.md](decision-hl-scan.md) (H=470, L=120, 28 keep, no lags).

**Unchanged:** five tradable + SPY; 1-minute raw bars; Alpaca SIP + Schwab live; separate immutable vendors; RTH for research; paper only.

| Knob | Rule |
|---|---|
| H | LightGBM IC on the full session set. Do not size from the 1165 top-k row. |
| L | Floor 30, step 5, through `min(2H, lookback_stop)`. 1-SE shortest. Do not overwrite `universe.lookback` until a decisioning row. |
| T | Bakeoff `{1y, 2y, 3y, 5y, all-prior}` after H/L (ADR-0050 `train_days`). |
| V | H-length embargo, 36–48 folds. Drop labels that resolve after `val_end`. |
| `w_k` | `weight_halflife_folds` in JSON, not searched (ADR-0053). |
| Features | Train-only importance; keep to 95% cumulative or ≥ τ of max; always keep calendar/static. |
| Models | TFT in `torch_ts`. No TimesFM/Chronos/Moirai this round. |
| Holdouts | Val Dec 2025–Feb 2026. Test A through 2026-07-31. Test B = August 2026, unassigned. |
| Ensemble | ~50 TPE; sample top 10% with new seeds; train to completion (ADR-0052). |
| Action docs | 1/5/15/30/60 twins remain. They are not the training lock. |

Pipeline #1 and #2 done. Next: T bakeoff `{1y,2y,3y,5y,all-prior}`; then V walk-forward. Ensemble members are sampled, not yet trained to completion.
