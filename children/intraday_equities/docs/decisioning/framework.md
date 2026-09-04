# Training framework (2026-08-31)

## Current modelability locks (2026-09-04)

- **Gate 1:** action A2822 is the locked stock-modelability decision.
- **HFDR in MIO:** action A2850 locks joint holding and capital selection under
  `sum_i(x_i * pi_i) <= q * sum_i(x_i)`.
- **Gate 2:** **No longer used.** Its P10/P11 artifacts remain historical
  evidence only.

Owner-locked. Seams: ADR-0050…0054, ADR-0055. H/L identification: [hstar-go.md](hstar-go.md) (ADR-0058). Old |IC| row: [decision-hl-scan.md](decision-hl-scan.md) — not the estimand.

**Unchanged:** five tradable + SPY; 1-minute raw bars; Alpaca SIP + Schwab live; separate immutable vendors; RTH for research; paper only.

| Knob | Rule |
|---|---|
| H | Sliding 40-fold WF through 2025-11-30. One LightGBM (symbol category) at lead=5; inner-train HPO; per-name no-information `h*`. Book collapse deferred. Not \|IC\|. |
| L | Same 40 folds at locked H*. 1-SE shortest of mean fold MSPE. `universe.lookback` stays 30. |
| T | Bakeoff `{1y, 2y, 3y, 5y, all-prior}` on the **HPO** window (Dec 2025–Feb 2026). H/L slide is `train_days=730`. |
| V | This H/L pass **is** V: 40 folds, `embargo_days=5` (`lead_stop`). Later V after H* may use H-length embargo. |
| `w_k` | `weight_halflife_folds` in JSON, not searched (ADR-0053). |
| Features | 46 session + 20 momentum/vol for H. Train-only importance through 2025-11-30 after L lock. |
| Models | TFT in `torch_ts`. No TimesFM/Chronos/Moirai this round. |
| Holdouts | CV through 2025-11-30. HPO Dec 2025–Feb 2026. Untouched 2026-03-01 → (confirm Mar–May; backtest Jun–Aug). |
| Ensemble | ~50 TPE on the HPO window; top 10% reseeded (ADR-0052). |
| Action docs | 1/5/15/30/60 twins remain. They are not the training lock. |

Next: ~~ŷ collapsed on the 40-fold pass (IC=0; mean go_frac=0.07).~~ **That pass is VOID — `min_split_gain: 0.02` made every tree a stump, so ŷ was constant and IC was 0 by construction.** Do not lock H until a run that passes the constant-ŷ guard says so. Nothing after Feb 2026 until confirm.
