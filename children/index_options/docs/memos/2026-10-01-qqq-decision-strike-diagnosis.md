# QQQ decision-strike degradation diagnosis

## Decision

The frozen raw wide decision-weighted MLP does not contain usable QQQ
decision-region signal. Its 2019 actual-listed-strike Brier skill versus the
horizon empirical reference is -4.376%. The paired 95% intervals cross zero
widely: -12.588% to +2.759% with 30-date blocks and -13.758% to +4.032% with
60-date blocks. No model is promoted and optimizer tuning remains blocked.

The degradation is not confined to a thin, irrelevant slice. Point estimates
are -3.262% on calls and -7.373% on puts. In the economically dense distance
bands below three reference-scale units, every aggregate band is negative.
The apparent gains beyond five scale units come from rare, near-deterministic
cutoffs with almost-zero reference Brier scores; they are not evidence for a
tradable tail advantage.

## Frozen training and validation

This study did not retrain, tune, calibrate, or select a model. It restored the
exact raw 2019 QQQ curves saved by ADR-0208. The wide network is a three-Gaussian
mixture MLP with hidden widths 64 and 32, 10% dropout, eight epochs, batch size
1,024, and deterministic seed 11. Its proper loss combined mixture negative
log likelihood, balanced Brier loss at every entry-known listed put/call
cutoff, and a smaller fixed-grid Brier term.

For the 2019 fold, both models fit 9,419 QQQ forecast rows on 1,703 quote dates
from 2011-03-23 through 2017-12-28; every fit label was settled by 2017-12-29.
The untouched calibration band contained 1,550 rows on 249 dates in 2018. The
descriptive validation band contained 1,677 forecast identities on 251 dates
in 2019, with labels through 2020-02-07. One identity had no eligible condor,
leaving 1,676 identities and 90,795 unique listed thresholds (181,590 paired
model-threshold rows). Exact symbol/date/expiry identities and source hashes
were verified before scoring.

## Where the error comes from

Both models overpredicted the weighted threshold event rate, but the MLP made
that bias worse. Overall observed frequency was 37.745%; empirical predicted
43.502% and the MLP 43.966%. On puts, observed downside-breach frequency was
6.786%; empirical predicted 9.110% and the MLP 10.010%. On calls, observed
CDF frequency was 68.704%; empirical predicted 77.893% and the MLP 77.922%.

The clearest persistent failure is downside probability. Put skill was
-6.510%, -10.301%, -10.271%, -7.328%, and -5.351% across exact DTE bands
0--7, 8--14, 15--21, 22--30, and 31--45 days. Calls at 0--7 days were the only
positive side-by-DTE point estimate (+2.574%); all other call DTE bands were
negative. The model therefore learned excess QQQ downside mass rather than a
more accurate decision boundary.

## Method, limits, and next step

The standardized JSON study scores every saved listed threshold, stratifies by
put/call side, absolute standardized strike distance, exact calendar DTE, and
year, and emits weighted observed/predicted event rates. Uncertainty uses 2,000
circular quote-date block resamples at 30 and 60 dates. Same-date observations
remain paired; zero-reference-score strata are explicitly undefined instead
of divided by zero. The run completed in 12.6 seconds at 0.77 GiB maximum RSS
inside hard WSL2 30-minute/6-GiB/no-swap limits. The focused child suite passed
49 tests, and Terra skeptic review closed with zero Critical or Major findings.

Evidence is under `pipeline_runs/decision_strike_qqq_diagnosis_20261001`.
This is a causal diagnosis of frozen outputs, not a new architecture search.
The next justified model step is narrow: test an index-specific probability
correction or hierarchical index head that can remove the QQQ side/DTE bias,
while preserving the frozen SPY guard. Pre-register the same local and global
guards; do not expand the zoo or run the robust optimizer before local signal
passes.
