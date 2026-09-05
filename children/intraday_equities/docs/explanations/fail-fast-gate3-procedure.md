# Fail-fast Gate 3: a worked procedure

## TL;DR

Gate 3 asks whether an asset's observed out-of-sample score is better than scores obtained after deliberately breaking the input-to-label relationship. It runs the 19 fixed scrambled refits in order and fails the asset immediately when one scrambled score matches or beats the real score; an asset that beats all 19 still needs the existing calibration check. The procedure is implemented and tested, but the active P12 study is not yet a final result.

## The question

A strong Gate 1 score can still be luck, overfitting, or leakage. Gate 3 asks: if the same modeling process is retrained on deliberately mismatched future returns, can it look at least as good?

## Inputs and terms

- **Asset** is one fitted symbol, such as ORCL. Gate 3 treats each asset separately.
- **Observed score** is that asset's Gate 1 out-of-sample R2oos score at its selected horizon. Higher is better; zero means no improvement over the baseline and a negative score is worse.
- **Null draw** is one complete retrain-and-score walk after whole trading sessions donate their label column to different sessions. Inputs remain in their own sessions.
- **Seed** is the fixed number that chooses one reproducible session shuffle. The procedure uses seeds 0 through 18, so there can be at most 19 null draws.
- **Match or exceed** means the null score is greater than or equal to the observed score. A tie is a failure, because the real score must be strictly better than every null score.

## Procedure

1. Gate 1 selects an asset and its horizon only after scoring its real-label walks.
2. Save the selected cell's observed R2oos. This is the number every null draw must beat strictly.
3. Run the scrambled refit for seed 0, score it, then compare its R2oos with the observed score.
4. Continue through seeds 1 to 18 in order only while every completed null score is lower than the observed score.
5. At the first null score that is equal to or larger than the observed score, stop that asset. Record the stopping seed and how many null draws ran. The asset fails Gate 3.
6. If all 19 null scores are lower, do not stop early. Run the established Gate 3 centre-and-spread calibration on the complete 19-draw family. An asset can beat all nulls and still fail that calibration.

Stopping early changes the cost, not the rank decision. The full-family rule is:

```text
pass rank test only when observed R2oos > maximum(null R2oos)
```

Once one null is at least the observed score, the maximum is already at least the observed score. Later draws cannot restore a pass.

## Worked teaching example

These numbers are invented.

Suppose the observed Gate 1 score is:

```text
observed R2oos = 0.018
```

Seed 0 gives a scrambled score of 0.011:

```text
0.011 < 0.018
```

The null loses, so continue.

Seed 1 gives a scrambled score of 0.018:

```text
0.018 = 0.018
0.018 >= 0.018
```

That tie means the real result is not strictly better. Stop immediately:

```text
stopped  = true
stop_seed = 1
n_draws = 2
gate3_status = fail
```

The result records a conservative stopping bound:

```text
p_bound = 2 / (n_draws + 1)
        = 2 / (2 + 1)
        = 2 / 3
        = 0.667
```

This is a bound produced by the early-stop rule, not a precise point probability and not a calibration result. The row therefore says calibration was not computed, and its null mean and null standard deviation are left empty. Computing a spread from only two draws would be misleading and unnecessary: the asset already failed the rank test.

## What a completed family looks like

Suppose instead that every one of the 19 scrambled scores is below the observed score. The asset has passed the rank part of Gate 3, but it is not automatically approved. The procedure then uses all 19 scores to check whether their centre and spread look sufficiently well calibrated. This preserves the former protection against a null family whose variability is implausible.

## What is preserved by the scramble

Whole-session label donation keeps each donor day's within-day order, overlapping labels, time-of-day pattern, and market-wide co-movement. It breaks the specific relationship under test: whether this session's inputs predict this session's later returns. Each null draw retrains the model; simply rescoring a model already trained on real labels would not test the fitting process.

## Current project status

ADR-0092's fail-fast procedure is accepted, implemented, and covered by tests. The ORCL P12 smoke completed the staged wiring, but ORCL failed Gate 1 at its first horizon, so no in-stage Gate 3 null family was needed; one separate real scrambled walk provided seam evidence. The active 63-asset P12 run must finish before it can supply final Gate 3 decisions.

## Limits

Gate 3 is a safeguard against a result that survives only because of chance, flexibility, or leakage. It does not prove an asset will remain predictive in live trading, and it does not replace the calibration check for an asset that survives all 19 null draws.
