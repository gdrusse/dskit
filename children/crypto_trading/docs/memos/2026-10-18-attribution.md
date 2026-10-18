# Prediction attribution and information quality

## Best candidate options

**Continue narrowly with method24; stop architecture and parameter sweeps.** Its advantage over the market-only and reduced-input controls is negative in every validation month, in both existing halves, and after removing any three of fourteen validation week blocks. That rejects the simple explanation that one or a few observed weeks produced the average gain. It does not establish a prediction edge: corrected weekly tests against market-only45 and no-GRU46 remain inconclusive.

**Do not replace24 with48.** Its small validation advantage reverses in July and can disappear when one week is removed. Keep48 only as a recorded sensitivity.

**Preserve ETH / 11-minute lead / market probability10–20% as an unconfirmed observation; stop local optimization.** Setting03 improves raw and all four declared controls on average in every validation month, but none of its five corrected comparisons passes. The gain is in narrow-spread, positive-volume records, not a wide-spread or inactive-quote effect. Exact quote-update timestamps remain unavailable. Do not convert this diagnostic into a newly selected narrow-spread pocket.

## Execution contract

User authorized implementing the Astra review and further tests. Base cabba42c2470ac89b2158ef90a712c1e824e47b3. Protocol checkpoint a9a78467 froze nine contrasts and all input identities before this calculation. This is an attribution diagnostic on already explored history, not a new model-training study, untouched validation, or profitability assessment.

Warm-up: February19–April19,2026,52,999 forecasts/11,213 contracts; existing March21 split. Validation: May2–July31,84,728 forecasts/17,158 contracts; existing June16 split. All original correction/benchmark predictions and training schedules are retained. The GRU input uses the inherited chronological model update on July1; monthly diagnostics do not create new holdouts.

Four global primary contrasts:24 versus45/46/47 and48 versus24. Five local contrasts:setting03 versus raw market/logistic/02/45/24, all in the already fixed ETH11m10–20 cell. Models38/44 and pocket06/09/12 remain correlation sensitivities, not alternative winners.

Global observations are contract averages over available leads, equally weighted by contract. Local observations have one forecast per contract at the fixed lead. Negative paired log-loss or Brier difference favors the candidate. The nine full-stage tests form one Bonferroni family at .05, separately for each stage and day/week clustering. Confidence intervals in artifacts are unadjusted two-sided95%; adjusted p-values are one-sided. A diagnostic robust pass additionally requires nonpositive Brier difference and negative log loss in both existing halves. These checks do not account for or erase all previous historical selection.

All monthly, spread, activity and full-map comparisons are descriptive; their displayed nine-adjusted p-values are not an omnibus correction for those extra views and are not used for promotion. Quality groups use the warm-up median spread0.010000000000000009 (approximately one probability point), frozen by the protocol rule, and candle volume zero/positive. A contract can appear in multiple quality groups because different leads can have different spreads; group contract counts overlap and are not additive.

## Answers from the nine primary comparisons

- **global: 24 full sparse correction versus 45 market-only correction.** n=17,158; log-loss difference -0.000545552; Brier -0.000143929; day/week adjusted p=0.02659/0.16027; diagnostic robust pass=False.
- **global: 24 full sparse correction versus 46 without GRU.** n=17,158; log-loss difference -0.000436653; Brier -0.000139822; day/week adjusted p=0.04715/0.26391; diagnostic robust pass=False.
- **global: 24 full sparse correction versus 47 GRU-disagreement only.** n=17,158; log-loss difference -0.000472712; Brier -0.000093980; day/week adjusted p=0.00485/0.01175; diagnostic robust pass=True.
- **global: 48 volatility experts versus 24 full sparse correction.** n=17,158; log-loss difference -0.000015439; Brier -0.000005682; day/week adjusted p=1.00000/1.00000; diagnostic robust pass=False.
- **pocket: 03 stronger method24 pocket correction versus raw market.** n=689; log-loss difference -0.003898573; Brier -0.001017688; day/week adjusted p=0.21859/0.15717; diagnostic robust pass=False.
- **pocket: 03 stronger method24 pocket correction versus logistic recalibration.** n=689; log-loss difference -0.003231856; Brier -0.000885116; day/week adjusted p=0.35587/0.65274; diagnostic robust pass=False.
- **pocket: 03 stronger method24 pocket correction versus 02 isotonic recalibration.** n=689; log-loss difference -0.004255167; Brier -0.001116880; day/week adjusted p=0.12398/0.13688; diagnostic robust pass=False.
- **pocket: 03 stronger method24 pocket correction versus 45 market-only correction.** n=689; log-loss difference -0.003417271; Brier -0.000914850; day/week adjusted p=0.27460/0.40542; diagnostic robust pass=False.
- **pocket: 03 stronger method24 pocket correction versus 24 full sparse correction.** n=689; log-loss difference -0.000972292; Brier -0.000243812; day/week adjusted p=0.62185/0.42813; diagnostic robust pass=False.

Only24 versus disagreement-only47 passes the full validation diagnostic rule.24 versus no-GRU46 and45 has consistent mean gains, but weekly uncertainty remains too large. The result supports investigating the joint information set; it does not prove the GRU has incremental predictive information, since removing inputs also changes fitted coefficients and shrinkage.

The original logistic calibrator was weaker than raw market globally, so beating it alone was insufficient.45 is a stronger matched market-only control. The local setting also beats02 isotonic and45 in mean score, but without corrected statistical support. Its average predicted probability need not be closest to the cell frequency to win log loss: individual-forecast ranking and calibration within the cell matter.

## Stability, composition and concentration

The strongest reference comparisons (24 versus45/46/47) retain negative validation means in May, June and July.48 versus24 is negative in May/June but positive in July (+0.00006889); its second-half difference is also slightly positive. This is incompatible with promoting48 as a stable improvement.

After the primary results retained their sign under every single-week omission, a separately recorded descriptive follow-up enumerated every omission of two and three week blocks. It did not change the primary contrasts, thresholds or pass rules. There are14 validation week blocks, so364 three-week combinations per contrast. The week blocks are fixed Unix-epoch seven-day blocks; edge blocks can be partial. No independence or confidence guarantee follows from this sensitivity.

- global m24 vs m45: worst remaining mean -0.000247421; all364 omissions remain negative=True.
- global m24 vs m46: worst remaining mean -0.000207286; all364 omissions remain negative=True.
- global m24 vs m47: worst remaining mean -0.000327507; all364 omissions remain negative=True.
- global m48 vs m24: worst remaining mean +0.000047854; all364 omissions remain negative=False.
- pocket s03 vs raw: worst remaining mean -0.001681003; all364 omissions remain negative=True.
- pocket s03 vs logistic: worst remaining mean -0.000089261; all364 omissions remain negative=True.
- pocket s03 vs m02: worst remaining mean -0.001702907; all364 omissions remain negative=True.
- pocket s03 vs m45: worst remaining mean -0.000712902; all364 omissions remain negative=True.
- pocket s03 vs m24: worst remaining mean -0.000244308; all364 omissions remain negative=True.

For the fixed pocket, May/June/July raw-market loss differences are −0.005676/−0.002061/−0.004485 across206/268/215 contracts. The complete cell has689 contracts and98 YES outcomes. Its market mean is15.41%, realized YES14.22%, and setting03 mean14.68%.

At the warm-up-defined narrow/wide spread boundary, setting03 versus raw is −0.005712 on488 narrow-spread contracts and +0.000505 on201 wide-spread contracts. Versus parent24 it is −0.001549 narrow and +0.000427 wide. All689 local observations have positive candle volume. Globally,24 versus45 remains negative in both spread groups and is stronger in the narrow group. These are composition diagnostics, not a new selected rule or proof of contemporaneous quotes.

The four leading parent corrections correlate0.965–0.996, and their paired-loss differences0.959–0.994, on identical validation forecasts. The four local variants use the same689 contracts and have paired-loss correlations0.936–0.996. They are overlapping versions of one signal; ensembling or counting them as four confirmations is unsupported.

## Quote and GRU provenance

Every one of137,727 admitted forecasts joins uniquely to the original acquired decision files; all model input fields match the hashed panel. Raw-file hashes were checked only for the three authorized historical bands w1-fold14/w2-block1/w2-block2. No protected or post-July outcome file was read. The subset excludes markets without valid GRU/common features, so results describe this matched cohort rather than all observable markets.

All admitted quote endpoints have candle age0; all spot ages are positive and all strikes were known before the decision. Bid/ask arithmetic exactly reproduces midpoint and spread. The original reader uses the last closed one-minute candle, whose closing bid/ask need not expose their individual update times. No actual bid/ask quote-update timestamp is present in the retained decision rows. Consequently this audit found no endpoint mismatch but cannot settle intraminute quote freshness. Positive volume does not establish quote freshness either.

The three GRU state hashes were recomputed from model weights plus their full sidecars and match the source configurations. Latest recorded training instants are February17 23:59:59.999 UTC, April30 23:59:59.999 UTC and June29 23:59:59.999 UTC for the three source bands. After allowing the15-minute maximum label horizon, every scored input remains after the training-label reach by at least84,660,000ms (23h31m). Saved row_offset_ms is−1. All requested leads exactly match the saved forecast horizon.

The source code feeds early stopping from the prior calibration band, not the scored validation interval. The metadata and guard checks support causal timing at this seam; this packet did not regenerate the minute panel, reconstruct original source observations, refit the GRU or independently reproduce every inherited feature. The architecture had already been selected on broader W1 experience, so model-selection reuse remains.

## Decision and next bounded step

Keep24 as the research reference. Stop tuning its strength/pruning/regime boundaries and stop optimizing the ETH cell on this history. The attribution results do not justify stopping all research: consistent gains survive the stronger controls and removal of multiple weeks in mean, and the broad-spread/inactivity explanation is not supported. However, uncertainty and measurement limitations prevent an established prediction-edge claim.

Next, inventory admissible historical quote data with exact bid/ask timestamps and documented prior-access history. If an unexamined interval exists, freeze24 and the existing03 pocket correction, the same raw/logistic/isotonic/45 controls and training/embargo rules before opening outcomes. Use a common synchronized cohort with the same complete lead/bin/calibration reports. Do not add the narrow-spread filter because of this result. If such data cannot be obtained, close this historical optimization line as inconclusive; more models on these rows will not answer the unresolved question. No new data acquisition or unexamined-outcome read was launched here. The separate2027 protocol is unchanged and is not a prerequisite for the completed diagnostic.

## Implementation, failures and verification

Implemented reproducible local analysis and audit scripts with frozen JSON protocol; reused24 completed source configurations (12 models/settings ×2 stages) plus raw/logistic baseline columns. No new model was fitted. Exported joined forecasts and contract scores,2,800 asset/lead/bin rows,140 lead rows,280 asset/lead rows,1,400 calibration rows, all nine comparisons, monthly/quality views, correlations and week-removal sensitivity. Every output has a retained hash.

The initial focused test failed on the missing paired-mean implementation. After implementation,12 focused tests pass, including clustered-error arithmetic, invalid values, identity alignment, contract weighting, date boundaries and week sensitivity. The independent author audit recomputes all18 full-stage contrasts, all primary day/week p-values, all single-week omissions, verifies complete output dimensions, source features and model-state hashes. The full suite was not run.

One first analysis attempt stopped before writing results because the archive payload subdirectory was omitted. The failure log and original script are retained; the corrected retry completed. No experiment was refitted and no completed output was overwritten. The concentration follow-up is explicitly post-primary descriptive work.

Two fresh sequential GPT-6 Astra reviews completed on candidate1bcb02c2 with zero Critical/Major findings. The correctness reviewer independently reproduced all18 primary comparisons,204 breakdowns,5,382 week-removal means, all4,620 summary rows, every source join and the three state hashes. The integration reviewer reproduced all4,620 rows, checked source/fit/protocol identities, tested refusal-before-I/O and caught seven meaningful code mutants. Both reviewers passed the12 focused tests. Full actual reports and hashes are retained in the evidence packet.

Deferred Minor INT-1: the permanent tests do not exercise identical duplicate identities on both sides of alignment. The existing runtime guard correctly rejects that case, independently verified by the reviewer; all actual cohorts are unique. Add that regression in a future test change. No current output is affected. Final unresolved counts: Critical0, Major0, Minor1 deferred, Nit0.

All scripts, tests, protocol, scientific outputs and dependencies remain identical to the reviewed candidate. The results-view TypeScript check passes; the UI was not rendered as part of this audit. Four documentation-budget tests pass. This closure appends review receipts and handoff only.

## Reproducibility and handoff

Artifacts: /home/russell/data/crypto_trading/research/attribution-20261018. The tracked evidence packet embeds the final scripts, tests, protocol, analysis, audit, concentration results and hashes. Existing source forecasts, raw decision records and saved model files are required to reproduce. The analysis refuses an existing completed analysis.json; copy the retained scripts/protocol to a fresh directory under the same research root for a new run. It does not retrain or change historical inputs.

Commands (from the isolated repository, WSL2):

    PYTHONPATH=. /home/russell/dskit/.venv/bin/python <artifact-dir>/analyze.py
    PYTHONPATH=. /home/russell/dskit/.venv/bin/python <artifact-dir>/audit.py
    /home/russell/dskit/.venv/bin/python <artifact-dir>/concentration.py
    PYTHONPATH=. /home/russell/dskit/.venv/bin/python -m pytest <artifact-dir>/test_attribution.py -q

- [Evidence packet](2026-10-18-attribution-evidence.json)
- [Previous aligned study](2026-10-18-warmup20-aligned-methods.md)
- [Previous tuning study](2026-10-18-pocket-tuning.md)
- [All comparisons](/home/russell/data/crypto_trading/research/attribution-20261018/primary-and-breakdowns.csv)
- [All cells](/home/russell/data/crypto_trading/research/attribution-20261018/all-cells.csv)
- [Calibration](/home/russell/data/crypto_trading/research/attribution-20261018/all-calibration.csv)
- [Source and state audit](/home/russell/data/crypto_trading/research/attribution-20261018/audit.json)
