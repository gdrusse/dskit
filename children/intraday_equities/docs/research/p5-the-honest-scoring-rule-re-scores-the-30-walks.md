# P5: the honest scoring rule, and what it does to the 30 walks

Date: 2026-09-03

## Question

Does the model beat a simple average guess, judged the same way everywhere?

## Finding

The old score counted wins from a test that can call a worse-than-average
forecast a win. The new rule (ADR-0067) measures how much error the model
actually removes, and asks twice whether that is real: once over all rows
in time order, once across the 20 test periods. Both must clear the bar.

The 30 finished runs saved only per-period summaries, not row-by-row
predictions, so only the second half can be applied. That half is enough
to fail a run outright. It fails 113 of the 120 cells. Seven remain open,
all at 1 or 2 minutes ahead, all from the two simplest models, and almost
all of the gain is one stock, LLY. Three of the four previously positive
group cells survive this far; the fourth (ridge at 2 minutes) does not.
Nothing can be called a win yet — the first half needs a re-run that
saves the rows.

## Sources

- RESULT-P5.md (this branch), ADR-0067
- docs/research/p5-skill-rule-pooled-diebold-mariano-against-the-training-mean.md
