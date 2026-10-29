# 2019 MIO rerun with available-size fills (2026-10-29)

## TL;DR

Both fresh MIO cohorts and all 56 stress cases completed. The eight controls exactly reproduce the new saved runs; every case has a 252-day trade and expiry record. The nominal method earns $259.05 in limited36 and $67,911.40 in broad212, but the combined lower-expiry stress turns these into losses of $23,860.30 and $29,525.40.

Start with the [report and all daybooks](../reports/mio-execution-stress-2019-available-size-20261029-v4/report.md). These remain 2019 development results under simulated execution and fixed future selections in the stress replay.

## Execution contract

The only economic changes are the approved sizing fixes. A single leg fills up to the available displayed size. A multi-leg book fills the largest whole-number quantity that preserves every leg's selected ratio, or refuses if one complete ratio unit is unavailable. A book is a group of call/put orders for one symbol and expiry; contracts are the quantities on those orders. Quote screening, next-session execution, fees, account caps and holding until expiry are unchanged. An opening short leg is a sale; it does not introduce an early exit.

Each fresh MIO run makes 53 weekly decisions and carries the actual executed holdings and cash through 252 sessions. The four methods are nominal_lp, w1_003_lp, w1_003_shock10_lp and w1_003_component25_lp. Starting cash is $10,000; the existing deposits bring contributions to $35,000 per independent account. Fresh MIO decisions respond to its account. The subsequent stress replay fixes those fresh selections while repricing execution and expiry, allowing later cash-dependent refusals to change.

The seven cases are control, adverse price, half size, expiry down 15%, expiry up 15%, and the two joint cases. Adverse buys pay ask plus 25% of the spread; adverse sells receive bid minus 25%. Half size floors displayed depth to half before applying the same sizing rules. Expiry shocks multiply each observed expiry underlying by 0.85 or 1.15 before computing the option payoff and realized profit. They are pointwise settlement counterfactuals; they do not describe a single changed price path.

## Implementation and verification

Runtime candidate 922b53d00dc0a49797f350fa0f300343a3ddf717 remains unchanged. Two fresh Sonnet and two fresh Luna final reviews reported zero Critical/Major findings, and 334 focused tests passed. Previously recorded Minor coverage preferences remain deferred. No new runtime or test code was added for this execution and publication.

Both cohorts have an attested complete close receipt, four completed methods with exact recorded replay, the exact source plan and config, 53 decisions and 252 account days. Direct saved-field CSVs retain selected versus executed quantities. Existing StudyPacketResults/MIOPacketCompare publication and StudyProtocolControl review completed serially; registry revision 59 is inactive. Neither cohort was relaunched.

Stress source commit c0c44033d7ddde76f507603f8cdad31a7ab921af binds the new result and quote hashes. All eight controls passed before any of the 48 stressed cases. Each control exactly matches its saved result and four CSV extracts, including fills, refusals, prices, fees, daily accounts and profits. All 121 output files matched their recorded hashes and byte counts. All 56 cases completed, producing 14,112 daily rows; none was skipped or unsupported.

Independent checks recomputed executed sizing, adverse prices, fees and expiry intrinsic payoffs; reconciled daily cash flows, held IDs, reserve and final profits; and verified every saved selected book has a disposition. These independent arithmetic recombinations differ by at most $0.0000000014 because of summation order. That tolerance was not used by the exact control gate.

## Results and how to read them

The control accounts together contain 1,011 selected books, 371 filled books and 640 refusals. Of the fills, 116 were downsized. These counts sum eight independent accounts; they are not a combined portfolio.

Limited36 nominal filled 45 of 127 selected books (35.43%), including 10 downsized fills. Its $259.05 profit includes $729.95 fees, with an 85.6% maximum drawdown in the marked account. Refusals were 54 quote-screen failures, 22 size failures and six repriced account-cap failures.

Broad212 nominal filled 163 of 433 selected books (37.64%), including 63 downsized fills. Its $67,911.40 profit includes $2,251.60 fees, with an 81.9% maximum drawdown. The joint lower-expiry case fills 104 of the same 433 selections, loses $29,525.40 and reaches 96.4% drawdown. Its 329 refusals comprise 183 quote-screen failures, 96 size failures and 50 repriced cap failures.

A stress need not lower every result: changed quantities, put exposure and later cash-dependent admissions can change the mix of executed trades. The higher-expiry cases produce large mechanical gains on call exposure. Read profit alongside fill counts, drawdown, fees, gross gains/losses, time-weighted return, win rate, profit factor, worst trade and expiry day, minimum cash and peak reserve in summary.csv. The existing ordinary-run stress_pnl field is a separate legacy spread deduction; the seven new scenarios are the named rows in this report.

Every daybook shows possible books/contracts, chosen IDs, actual buy/sell legs with filled/selected quantities and prices, refusal reasons, expiries, realized losses, marked changes, cash and equity. Candidate-preparation refusals appear separately. Zero possibilities on non-decision days do not mean the entire market had no opportunities. A refused book is not an executed trade. dispositions.csv records each selected book's quotes, fees, reserve, payoff and cash impact.

## Limits, reproducibility and handoff

No runtime failures or unsupported cases occurred. No full test suite, model refit, provider pull, new year, early exit, quote-rule change or optional hindsight was run. American assignment and broker liquidity are unmodeled. Path A0003/A0008, TDD, original evidence, original viewer and earlier reports are preserved. The separate user-managed Claude report-standardization branch/worktree is outside this wrap.

Run identities are in [the new results pins](../../configs/mio-2019-available-size-results-pins-20261029.json); the [v4 scenario JSON](../../configs/mio-2019-execution-stress-available-size-20261029.json) specifies the exact source files and scenarios. The MIO data root is /home/russell/data/index_options/mio-2019-available-size-20261029. The full stress output, including all per-case JSONs, is /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4.

Execution used /home/russell/dskit/.venv/bin/python -u -B -m dskit.pipeline run with the pinned cohort JSON and --asof 2020-03-08, then -m index_options.mio_execution_stress with the pinned v4 JSON. Both used the accepted checkout and child on PYTHONPATH and the new data root as working directory. The GitHub mirror preserves report.md, all 56 daybooks, four CSVs and pins.json byte for byte. The next action is owner interpretation; this task authorizes no broader execution.
