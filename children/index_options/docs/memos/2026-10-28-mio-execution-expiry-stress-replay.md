# MIO 2019 execution and expiry stress replay (2026-10-28)

## TL;DR

The replay finished all 56 cases: eight exact historical controls and six stresses for each. Each 252-day daybook shows possible trades, selected orders, next-session buys and sells, refusals, expiries, realized losses, cash and equity. The stressed expiry values change realized profit and later cash-dependent fills; future trade selections remain fixed, so this is a conditional diagnostic on 2019 development data.

## Execution contract

ADR-0280 authorized this bounded replay. The inputs are the pinned 2019 limited36 and broad212 results, four saved methods in each, with 915 originally selected books, 224 original fills and 691 original refusals. A selection made after a close is attempted on the next session at observed bid or ask. The ordinary baseline therefore already includes the overnight quote change and bid/ask spread. The starting account is $10,000, with $1,000 deposits every 14 calendar days through the original cutoff ($35,000 total contributions per case), the original fee and reserve rules, and chronological settlement.

The seven cases per method are: exact control; adverse fill price; half displayed size; 15% lower expiry underlying; 15% higher expiry underlying; and each expiry direction joined with both execution stresses. Adverse buys pay observed ask plus 25% of the observed spread, and adverse sells receive observed bid minus 25% of the spread. Half size floors each displayed executable side to half its original number of contracts; a too-small side refuses the whole book. On an expiry shock, each selected expiry settlement is multiplied by 0.85 or 1.15 before the option payoff and realized profit are calculated. Deposits, fills, refusals, cash, holdings and reserves advance session by session. Future saved selections do not adapt to the stressed account.

A refused order is not a trade. Missing quotes, insufficient size, basis/session defects and account/shared-cap failures remain explicit. A case stops as unsupported if a settlement or account invariant cannot be represented; none of the 56 cases stopped. These pointwise expiry shocks are counterfactuals, not one coherent market price path or a prediction.

## Implementation and verification evidence

The frozen implementation is commit 1035e1c812c203851f71e1d675dc6ac3b186f366. The config is children/index_options/configs/mio-2019-execution-stress.json, SHA-256 334aa0c0ebe7169bdfdbe2785190cf891727c1866af9b4855a0c3ec4c079ca23. It pins the original results-pins document at 2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2, both original result files, and the source quote indexes.

Gate 0 passed: all eight controls reproduced the pinned selections, executed legs and prices, refusals, fees, daily account rows and final profits with exact numeric identity and row order. The original 915/224/691 census matched. Fourteen focused tests passed. Two independent final skeptic reviews of the frozen commit each reported zero Critical, Major, Minor and Nit findings. The full test suite was deliberately unrun under the user testing instruction.

The completed output contains 56 case results, 14,112 daily rows (56 x 252), and 6,405 selected-book attempt and disposition rows. All 56 cases have complete status. Read-only checks verified every case's filled/refused counts against dispositions; sum of daily expiry profit against final profit; last cash against final equity; and final equity against $35,000 contributions plus realized profit. All 121 files listed in pins.json matched their SHA-256 and byte counts. The original result root, viewer, Path A0003/A0008 and TDD were left intact.

## Results to read first

The main report links every daybook. On each decision day the daybook gives the number of possible books and option contracts, selected IDs, and separately listed candidate-preparation refusals. The next session shows each actual buy/sell leg and price, or the refusal reason. The expiry date shows each held book's realized profit or loss; daily cash and equity continue through year-end. The per-book dispositions CSV adds original/effective quotes and sizes, fees, reserves, settlement payoff and account impact.

The limited36 nominal method illustrates a downside outcome. Its exact control selected 149 books, filled 40, refused 109, and finished with $42,278.21 profit. The joint lower-expiry case kept the same 149 selections, filled 22 and refused 127, ending with a $15,122.10 loss and 61.3% maximum drawdown. Of the 127 refusals, 75 lacked half-size depth, 51 lacked a fill quote, and one failed the shared account cap. Its daybook records each loss on the actual expiry day.

The broad212 W1 shock method illustrates that a stress need not make every method unprofitable. Its control filled 50 of 202 selections for $89,497.05 profit. The joint lower-expiry case filled 34 of 202 for $74,810.00 profit; 168 were refused. Its maximum drawdown was 60.9%, gross losing trades totaled $34,026.10, and its worst expiry day lost $13,458.70. The refusal mix was 88 insufficient-size, 74 missing-quote, five shared-cap and one basis/session. Its filled fraction was 34 / 202 = 16.83%.

Across the eight methods, the joint lower-expiry case has both profits and losses. The up-expiry cases can produce very large mechanical gains for call exposure. Fewer fills and changed later account eligibility can also make an adverse-price or size case finish with more profit than control. Do not read the seven cases as a ranking of likely future outcomes. The report's time-weighted return, drawdown, fees, gross gains and losses, profit factor, win rate, worst trade, worst expiry day, minimum cash and peak reserve should be read together with fill count and refusal reasons.

## Failures and limits

No case failed or was skipped. The full suite, 2020+ validation, adaptive reselection, broker execution, assignment, margin and a coherent changed market path were deliberately unrun. End-of-day bid/ask quotes are execution proxies. The stresses change fills and realized expiry payoffs under frozen selected orders; they cannot establish live profitability or a strategy that would have selected different orders after stressed losses.

## Reproducibility and handoff

Run command from WSL2 child directory:

PYTHONPATH=/home/russell/dskit-index-options-stress-adr:/home/russell/dskit-index-options-stress-adr/children/index_options /home/russell/dskit/.venv/bin/python -m index_options.mio_execution_stress configs/mio-2019-execution-stress.json

Output root: /home/russell/data/index_options/mio-execution-stress-2019-20261028-v1. Start with report.md, then the case daybook, daily.csv and dispositions.csv; summary.csv supports all-method comparisons. pins.json SHA-256 is 391206b77fb7a1e3c75f26873c6da4d6a9069f4af3c4f2c667e8faed2d84cedc. The next authorized action is owner interpretation of this 2019 development evidence; no wider-year or live run is included.
