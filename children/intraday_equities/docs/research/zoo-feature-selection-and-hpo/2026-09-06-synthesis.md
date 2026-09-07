# Tune the pooled incumbent before pruning it; the inner holdout can now rank

## Question

After the P13 pooled zoo, what should the next iteration change: the feature
set, the search space, or both? Two agents worked the halves — feature
selection and hyperparameter search — over the same 20-fold protocol, the same
25 Gate-3 pairs, and the same 61-column frozen matrix.

## Finding

**Do the search work first, and treat selection as a family-mask question, not
a selector question.** The two notes reach that ordering independently and for
one shared reason: the pooled inner holdout resolves far more than the
single-name evidence everyone has been reasoning from.

1. **The binding constraint was misread, and both notes correct it.** The
   standing lesson is the JPM note's — 32 draws on a ~1,800-row inner holdout
   ranked nothing. That verdict is about 1,800 single-name rows. The pooled
   holdout is roughly 45,000 rows and is NOT restricted to the 30-minute
   scoring lattice (`nodes.py:5504` passes no `score_period_ms` where the
   outer val call does). The HPO note puts the paired resolvable gap at about
   0.006-0.009 IC, five to eight times sharper; the selection note reaches the
   same order independently. Four draws was therefore a coverage failure, not
   prudent variance control: about 3.6 of P13's 18 knob-level cells were never
   visited at all.

2. **The search space omits the knob that matters and spends two dimensions on
   ones that do not.** `learning_rate` is absent from the P13 LightGBM space
   while the tunability literature ranks it first among non-structural knobs;
   `max_depth` and `colsample_bytree` rank near the bottom and occupy two of
   the six dimensions. Rebuild on log ladders, raise `hpo_trials` to 24, and
   make the inner objective the outer path score rather than a different one.

3. **Selection is not the lever for LightGBM.** Trees tolerate irrelevant
   columns, `colsample_bytree` already subsamples them, and a univariate
   pre-filter can delete one half of an interaction the tree would have found.
   The selection-shaped experiment worth its compute is a fixed family-mask
   ablation, which needs no inner holdout and cannot leak.

4. **The MLP is where pruning may pay, and the target is variance.** Its fold
   standard deviation is 2.3 times the incumbent's on a mean that ties. Rank by
   LightGBM gain on inner-train rows, never `f_regression` on a heavy-tailed
   target scored by rank correlation.

5. **The two notes' budgets now agree.** The selection note held joint tuning
   of `k` with model knobs back because four draws made `k` a coin flip; at 24
   draws that objection lapses, which is why its E5 experiment is affordable
   only after the HPO change lands. Sequence: HPO first, masks second, MLP
   pruning third.

6. **One leakage catch, from the selection half.** The existing
   `universe.keep_features` list was chosen with validation on the finalist HPO
   window. Treat it as void for development-era work; do not reuse it as a
   starting mask.

7. **Both halves name the same class of gap.** The child's scan node restates
   search machinery dskit already owns, and log-uniform ranges exist only in
   the optuna pack. Seven generic gaps are named across the two notes, each
   with a tier; none was solved child-side.

## Sources

- `docs/research/zoo-feature-selection-and-hpo/2026-09-06-feature-selection.md`
- `docs/research/zoo-feature-selection-and-hpo/2026-09-06-hpo-search-spaces.md`

Both carry their own primary citations and the repo files they relied on.
