# Question

What is the empirically sound way to choose the longest horizon H at which we can still be confident in a model — not “which label length can a retrained specialist fit”?

# Finding

**H should be read off a decay curve of one frozen score, not a specialist-per-h kitchen sink.** The object that matches “how far out can we still trust the model?” is:

1. Fit **one** predictor on train only, on a single pre-declared target (e.g. next 5-minute residual vs SPY). Freeze `ŷ_t`.
2. At each val timestamp `t`, for each grid `h`, let `y_{i,t}(h)` be name `i`’s RTH-tape log return from `t` to `t+h` (label must land inside val).
3. **Cross-sectional** rank IC at that stamp: `IC_t(h) = Spearman(ŷ_{·,t}, y_{·,t}(h))` across the five tradable names. Never pool stocks and times into one correlation.
4. Summarize the **time series** `{IC_t(h)}`: mean `μ(h)`, ICIR = `μ / sd(IC_t)`. Standard error is **Newey–West / HAC** with lag on the order of the overlap, `≈ h / 5` five-minute steps — not `1/√n_rows`.
5. **Signed only.** Pass iff `μ(h) > 2 · SE_HAC(h)` (pre-registered `se_mult`). Negative mean IC is a no-go at that `h`, not skill.
6. **H** = the largest `h` such that **every** grid point from the first passing lead through `h` still passes (first contiguous block after skill appears). That is “still confident as we go out.” Do not jump to a later isolated spike. Optional extra: also require `μ(h) ≥ μ_peak − SE` so H is not a long tail of barely-significant noise; that is a *near-peak* band, not a substitute for beating zero.
7. Pre-register the lead grid and the go shape (anchors and/or a minimum band length). 234 unadjusted tests will false-pass. A contiguous-band rule is the multiple-testing control; do not also hunt `|IC|`.
8. Choose H on val. Confirm later on Test A. Do not TPE-maximize the same val IC.

**Half-life is not H.** Fitting `μ(h) ≈ μ0 e^{−λh}` and taking `ln(2)/λ` (or “75% of peak”) is a **rebalance / cost** knob. Confidence H is the last `h` that still rejects a zero-IC null with overlap-correct SEs.

**Why the last scan was a different question.** Retraining LightGBM at every `h` answers “which target length is rankable,” not “how far does this model’s 10:00 score still work.” Pooled Spearman mixes stock-picking with “which bars in the quarter had large returns.” `|IC|` treats inversion as skill. `1/√(n−1)` treats overlapping 5-minute rows as independent, so 2 SE ≈ 0.016 on 15k rows is far too easy.

**N=5 is a real constraint.** CS Spearman on five names is coarse; power comes from many timestamps plus HAC, not from a giant pooled `n`. Neutralize vs SPY before the CS rank so the IC is not market beta. Report mean IC and ICIR together. If the CS series is too noisy, a pre-declared robustness check is the time-series of “model’s top name vs bottom name” at each `t` — not a fishing expedition.

**Later, not for this H lock:** ADR-0049 `n_ahead` (one net, many output steps) is a legitimate *model class* once H is a number. It does not replace the evaluation recipe above. Diebold–Mariano vs a naive baseline (zero or last-period return) with HAC lag `h−1` is a useful extra gate, not the primary H picker.

## Academic provenance (what is a paper vs a recipe)

No single paper is “how to lock maximum confident H for an intraday five-name book.” The **pieces** are academic; the **assembly** (one frozen score → CS `IC_t(h)` → HAC → signed contiguous block = H) is a synthesis for this child.

| Piece | Academic home | What it is not |
|---|---|---|
| IC as cross-sectional correlation, then average over time | Grinold (1989, *JPM*); Grinold & Kahn textbook; same two-step shape as Fama–MacBeth (1973, *JPE*) | Not a theorem that farthest `h` is the lock |
| Do not pool all asset–dates into one correlation | Implication of that CS-then-TS design; practitioner notes restated it | Not one famous “never pool” paper |
| Overlapping multi-step returns need overlap-aware SEs | Hansen–Hodrick (1980); Newey–West (1987); Hodrick (1992); Diebold–Mariano (1995, *JBES*) | DM compares two models; it does not pick H |
| Long overlapping horizons can **look** more predictable | Boudoukh, Richardson, Whitelaw (2006/8, “The Myth of Long-Horizon Predictability”) | Naive `1/√n` or unadjusted t-stats at large `h` are anti-conservative |
| NW/HH can still be too small when `h` is large vs T | Britten-Jones, Neuberger, Nolte (2011, overlapping-observation transform) | Our “lag ≈ h/5” is a default, not their estimator |
| Multi-horizon model comparison | Quaedvlieg (2021, *JBES*) | Compares models across a path of `h`, not one H lock |
| Exponential IC half-life / “75% of peak rebalance” | Common **practitioner** heuristic, not a journal result we should treat as a test | Not confidence H |
| Foucault & Frésard (2023) CS vs TS decay | Cited via a course summary, **not read in full here** | Do not hang the lock on that claim until the paper is read |
| Contiguous first passing block = H | Adaptation of this child’s pre-registered `band_leads` idea | Not a named test |

**Bottom line:** signed CS-IC + overlap-correct SE is the academically defensible *measurement*. The cutoff rule (2 SE, first contiguous block) is a pre-registered decision, not a theorem. Half-life blogs are not the source.

## Names that do not share the same H

The book is a **relative-rank long-short**. One H is the coherent object. Per-name H is a different product.

**If name A’s skill dies at 30 minutes and name B’s still shows at 400:** at long `h`, A’s returns look like noise against `ŷ`. That **pulls `μ(h)` down**. The CS curve already answers “can we still rank this set this far out?” You do **not** need a separate `H_A`, `H_B` to see that.

**Do not estimate five H’s on five names.** That is 5 × 234 tests. With this sample it will overfit. If a name-level diagnostic is wanted, shrink: `H_i` toward the book H, or only *drop* a name from the tradable set when its time-series IC is negative at short `h` on train (pre-declared), not when its long-`h` curve wiggles on val.

**Missing / halted names at `t+h`:** skip that `(i,t,h)` cell. Require a **minimum cross-section** at `t` (e.g. 4 of 5 finite labels) or skip `t`. Do not impute. Do not compute Spearman on 2 names.

**Production:** one `label_lead = H` for the optimizer. Mixed horizons in one Pyomo book means mixed settlement and an ill-posed relative rank (A’s 30-minute score vs B’s 400-minute score). Separate books only if we explicitly split the product.

**Coverage vs skill:** a name that often lacks a long-`h` label is a **data** problem, not a longer H. A name that has labels but zero CS contribution is a **cohort** problem (maybe it should not be tradable).

# Sources

- Grinold (1989, *JPM*); Grinold & Kahn textbook; Fama–MacBeth (1973, *JPE*).
- Hansen–Hodrick (1980); Newey–West (1987); Hodrick (1992); Diebold–Mariano (1995, *JBES*); Quaedvlieg (2021, *JBES*).
- Boudoukh, Richardson, Whitelaw, “The Myth of Long-Horizon Predictability.”
- Britten-Jones, Neuberger, Nolte (2011), overlapping-observation transform.
- Half-life / 75%-of-peak: practitioner notes only (QuanterLab; Micro Alphas).
- Foucault & Frésard (2023): not read in full; do not hang a lock on it.
- This child: design-proposal §7; ADR-0049; HL-scan `c60b2910`.
