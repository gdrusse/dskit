# P5 result: the 30 horizon-sweep walks under ADR-0063

Date: 2026-09-03. Rule: `docs/architecture/decision-log.md`, ADR-0063. Plan: `docs/plans/2026-09-horizon-search.md`, P5.

## What could be re-scored, and what could not

The 30 walks on disk saved **no per-row predictions** — every fold's `carry.json` keeps only `mspe_model`, `mspe_mean`, `n` and the Clark-West `t` per (name, lead). No walk was re-run to regenerate them.

So of ADR-0063's two required halves:

- **Across-fold t** — computable exactly. `R2oos_f = 1 - mspe_model/mspe_mean` is fixed by the saved pair, and the t is over the 20 folds on 19 df.
- **Pooled Diebold-Mariano t** — NOT computable. It needs `d_t`, and an MSPE pair cannot be unpacked back into rows.
- **Out-of-sample R2 vs the mean** — computable exactly: `1 - sum(n*mspe_model) / sum(n*mspe_mean)` over all 20 folds.
- **Group** — the per-timestamp cross-sectional average needs timestamps, so the exact panel series is unavailable. The GROUP rows below equal-weight the three names' per-fold R2 instead, which is the same quantity when the names carry equal row counts (they do: 3108 / 3092 / 3107 in fold 1). Its pooled t is unavailable for the same reason.

The rule PASSES only when BOTH halves clear their bar, so **a cell that fails the across-fold half is a definite fail**. A cell that clears it is `unresolved` — not a pass. Nothing here is guessed.

Cells are `verdict R2oos%` — the R2 against the constant training mean, in percent, which is the quantity the hand-computed "gain" column in `docs/RE-ENTRY.md` was measuring.

## JPM

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | fail +0.0511% | fail +0.0762% | fail -6.8617% | fail -10.4477% | fail -4.9665% |
| 2 | fail +0.0014% | fail +0.0368% | fail -7.0169% | fail -11.0712% | fail -4.9297% |
| 3 | fail -0.0082% | fail +0.0007% | fail -8.1681% | fail -11.4476% | fail -5.2167% |
| 20 | fail -0.0125% | fail -0.7097% | fail -10.2720% | fail -10.1599% | fail -10.5031% |
| 30 | fail +0.0086% | fail -1.1063% | fail -37.4534% | fail -40.8580% | fail -32.1945% |
| 60 | fail -0.0458% | fail -0.9440% | fail -56.3998% | fail -58.3638% | fail -43.5943% |

## LLY

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | unresolved +0.3541% (t_fold 6.79) | unresolved +0.6948% (t_fold 7.06) | fail -8.0912% | fail -11.6636% | fail -4.8750% |
| 2 | unresolved +0.1236% (t_fold 3.86) | unresolved +0.1948% (t_fold 2.46) | fail -8.9071% | fail -11.2542% | fail -5.6556% |
| 3 | fail +0.0335% | fail -0.0045% | fail -9.0214% | fail -11.7845% | fail -5.5972% |
| 20 | fail -0.0395% | fail -0.7417% | fail -8.0417% | fail -7.0392% | fail -10.7210% |
| 30 | fail -0.1307% | fail -1.3128% | fail -34.5079% | fail -26.8888% | fail -19.2411% |
| 60 | fail -0.0860% | fail -1.2464% | fail -48.6931% | fail -32.8427% | fail -26.0263% |

## XOM

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | fail -0.1222% | fail -0.0468% | fail -7.2669% | fail -9.7904% | fail -4.8654% |
| 2 | fail -0.0835% | fail +0.0174% | fail -7.3403% | fail -11.1785% | fail -4.6275% |
| 3 | fail -0.0787% | fail -0.0107% | fail -8.1198% | fail -11.8769% | fail -4.9844% |
| 20 | fail -0.0002% | fail -0.6733% | fail -15.6526% | fail -19.7513% | fail -25.9516% |
| 30 | fail -0.1434% | fail -1.0056% | fail -47.1531% | fail -46.3933% | fail -34.6847% |
| 60 | fail -0.2076% | fail -0.9281% | fail -56.8104% | fail -56.5587% | fail -49.5179% |

## group (JPM+LLY+XOM)

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | unresolved +0.0891% (t_fold 2.64) | unresolved +0.2325% (t_fold 4.23) | fail -7.3936% | fail -10.6135% | fail -4.9027% |
| 2 | fail +0.0098% | unresolved +0.0794% (t_fold 2.38) | fail -7.7210% | fail -11.1660% | fail -5.0506% |
| 3 | fail -0.0202% | fail -0.0049% | fail -8.4124% | fail -11.7017% | fail -5.2515% |
| 20 | fail -0.0185% | fail -0.7096% | fail -11.2264% | fail -12.1823% | fail -15.7296% |
| 30 | fail -0.0946% | fail -1.1520% | fail -39.5233% | fail -37.3497% | fail -28.0712% |
| 60 | fail -0.1142% | fail -1.0546% | fail -53.5835% | fail -48.0014% | fail -38.8071% |

## Clark-West, as a side column only

Mean Clark-West t over the 20 folds, and the fraction of folds it rejected at 5%, for the group. It is reported because ADR-0063 says to report it — it is never the verdict.

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | t +1.59, rej 40% | t +1.81, rej 48% | t +0.66, rej 20% | t +0.50, rej 15% | t +0.39, rej 15% |
| 2 | t +0.71, rej 23% | t +1.16, rej 32% | t +0.37, rej 8% | t +0.19, rej 13% | t +0.33, rej 10% |
| 3 | t +0.35, rej 12% | t +0.74, rej 20% | t +0.29, rej 7% | t +0.12, rej 7% | t +0.29, rej 15% |
| 20 | t +0.22, rej 8% | t -0.05, rej 3% | t -0.14, rej 5% | t +0.03, rej 3% | t +0.11, rej 3% |
| 30 | t +0.22, rej 12% | t +0.13, rej 2% | t -0.04, rej 2% | t +0.22, rej 12% | t -0.14, rej 5% |
| 60 | t +0.24, rej 13% | t +0.20, rej 10% | t -0.09, rej 7% | t +0.30, rej 7% | t +0.10, rej 10% |

## Answer

- Cells scored: 120 (30 walks x 4 series rows).
- Cells that clear the across-fold half: 7. Every other cell is a definite FAIL under the rule.
  - H=1 lgbm GROUP: R2oos +0.2325%, t_fold 4.23
  - H=1 lgbm LLY: R2oos +0.6948%, t_fold 7.06
  - H=1 ridge GROUP: R2oos +0.0891%, t_fold 2.64
  - H=1 ridge LLY: R2oos +0.3541%, t_fold 6.79
  - H=2 lgbm GROUP: R2oos +0.0794%, t_fold 2.38
  - H=2 lgbm LLY: R2oos +0.1948%, t_fold 2.46
  - H=2 ridge LLY: R2oos +0.1236%, t_fold 3.86
- No cell can be declared a PASS: the pooled half is unrecoverable from what was saved. To settle these, re-run the affected configurations with the ADR-0063 artifact in place.

## How to reproduce

```
python -m dskit.pipeline skill <walk-forward summary dir>
```
