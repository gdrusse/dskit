# 2019 option portfolio execution and the stress-testing gap

## TL;DR

The eight 2019 historical simulations selected 915 option books: 224 filled and settled, while 691 were rejected. Their saved independent replays and a fresh read-only ledger check agree on fills, refusals, fill dates and final profit. The 813 recent stress jobs only calculated hypothetical values for positions already held; they did **not** run trading under stressed conditions. The next bounded task is an execution-and-expiry replay with the original historical run as an exact control.

## Purpose, scope and words used here

This memo answers what executed, what was checked, and what remains to test. It can be read without the project's other documents. "MIO" is the project's weekly option portfolio study. A **book** is one selected trade that may contain several option legs. A **fill** means the simulation accepted the whole book at its next-session quote. A **rejection** means no part of that book entered the account. A **mark** is an estimated value for a position already held. A **replay** reconstructs a saved run to check that the same inputs produce the same result.

The study uses 2019 historical data as **development evidence**, under locked Path A0003. The saved model-zoo forecasts follow locked Path A0008; they were not retrained here. The broad cohort has 212 supported names. The limited cohort has 36 ticker-and-expiry-band cells across 21 names, chosen retrospectively using both 2019 halves; it is not independent validation. Neither cohort is a live-trading result or a 2020-and-later test.

Each cohort ran four previously fixed methods: nominal selection; a robust selection with radius 0.03; that robust selection with a signal-time terminal shock screen; and that robust selection with a 25% component reserve cap. This yields eight method runs. Each method made 53 weekly decisions over 252 account sessions. The account began with $10,000 and added $1,000 every 14 days, for $35,000 total contributions. Option contract multiplier was 100 and commission was $0.65 per contract. These settings and the original 2019 data were not changed by the later stress work.

The original simulated execution sequence was:

1. At a weekly close, form and select candidate option books using information available by that decision.
2. At the next market session's end-of-day bid/ask quote, test every selected leg's quote, displayed size, shared capacity and the account's funding/reserve constraints. Fill the whole book or reject it; rejected books are not replaced.
3. Carry filled books in one continuous cash account until their expiries, settle option payoffs, charge commissions once, and record daily equity and final profit.

This is a historical, rule-based simulation. Bid/ask quotes are observations, not proof that an actual broker would have executed the order. American early assignment, forced liquidation, borrowing and broker margin were not simulated.

## What the normal historical runs actually produced

Across all eight runs, 915 books were selected, 224 filled, and 691 were rejected before entering the account. The filled/rejected counts and profit by method are:

- Limited, nominal: 40 filled / 149 selected; final profit **$42,278.21**.
- Limited, robust: 10 / 20; **$607.90**.
- Limited, robust plus signal shock: 10 / 19; **$196.55**.
- Limited, robust plus component cap: 9 / 16; **−$6,156.25**.
- Broad, nominal: 61 / 310; **$20,446.21**.
- Broad, robust: 19 / 101; **$36,141.65**.
- Broad, robust plus signal shock: 50 / 202; **$89,497.05**.
- Broad, robust plus component cap: 25 / 98; **$17,224.10**.

"Profit" here is final account equity minus the $35,000 contributed, after the recorded commissions. For example, broad robust-plus-shock finished with $124,497.05: $124,497.05 − $35,000.00 = $89,497.05. These outcomes are concentrated and retrospective; they do not identify a promoted strategy.

The 691 refusals are visible, not silent trades: 338 lacked a next-session fill quote, 285 lacked enough displayed size, 65 violated a shared cap after repricing, and three had a fill basis/session problem. A rejected book may still carry a `fill_price` field for a quote that was observed before a later cap rejection. That field is **not** evidence of an executed trade; the book status remains `unfilled` and its ID does not appear among filled trades. This matters when reading the exported CSVs.

The existing "extra spread" sensitivity subtracts an additional 25% of the recorded bid/ask spread from each **already filled** trade's profit. All eight saved results satisfy: extra-spread profit = historical profit − sum of the filled trades' extra-spread costs. For example, broad robust-plus-shock changes $89,497.05 to $79,935.80, and limited robust-plus-shock changes $196.55 to −$289.70. It does not revisit which orders could fill, which books survive, or how later cash constraints change.

## What was verified about ordinary execution

The saved result for each cohort says `complete=true`; all four methods in each say `status=completed` and `replay_parity=true`. Those independent replays were completed and pinned before the recent stress work.

On 2026-10-28 I made a separate, read-only WSL2 reconciliation from each pinned `result.json`, `trades.csv`, `rejected-orders.csv` and `daily-account.csv`. For all eight methods:

- Filled and rejected book IDs and counts match the saved result, and no book ID is in both groups.
- Every recorded fill date is the next recorded market session after its decision date. Every filled leg's recorded capacity covers its quantity.
- Every rejected book has `unfilled` status; its quoted price, if present, is not entered as a trade.
- The sum of filled-book profits equals the method's reported profit within $0.000001. Reported profit also equals final equity minus contributions within $0.000001.
- Each method has 252 daily account rows, and the last row's equity and contributions match the saved result. No book remains open in the last row.

This checks important ledger and export identities. It is **not** a new independent reconstruction of every raw quote, forecast, optimizer choice or expiry settlement. The saved independent replay and packaging checks are separate evidence. No ordinary MIO run was repeated for this memo.

The one-off reconciliation had no saved script or receipt. This equivalent, read-only WSL2 command reproduces its assertions from the pinned files; its eight lines end in totals of 224 filled and 691 rejected. It does not rerun the MIO engine:

```bash
python3 - <<'PY'
import csv, json, math
from collections import defaultdict, Counter
from pathlib import Path
root = Path("/home/russell/data/index_options/mio-broad-2019-20261027")
totals = Counter()
for cohort in ("limited36", "broad212"):
    saved = json.loads((root/cohort/"result.json").read_text())
    rows = {name: list(csv.DictReader((root/cohort/f"{name}.csv").open()))
            for name in ("trades", "rejected-orders", "daily-account")}
    by = {name: defaultdict(list) for name in rows}
    for name, data in rows.items():
        for row in data:
            by[name][row["method"]].append(row)
    for packet in saved["results"]:
        result = packet["result"]
        method = result["method"]["id"]
        trades, rejected, daily = (by[name][method] for name in rows)
        dates = sorted(row["date"] for row in daily)
        next_date = dict(zip(dates, dates[1:]))
        filled_ids = {row["id"] for row in trades}
        refused_ids = {row["id"] for row in rejected}
        assert packet["status"] == "completed" and packet["replay_parity"]
        assert filled_ids == {row["id"] for row in result["trades"]}
        assert refused_ids == {row["id"] for row in result["rejected"]}
        assert not filled_ids & refused_ids
        assert all(next_date.get(row["decision_date"]) == row["fill_date"] for row in trades)
        assert all(all(int(leg["capacity"]) >= abs(int(leg["quantity"]))
                       for leg in row["legs"]) for row in result["trades"])
        assert all(row["status"] == "unfilled" for row in result["rejected"])
        assert math.isclose(math.fsum(float(row["pnl"]) for row in trades),
                            result["pnl"], abs_tol=1e-6)
        assert math.isclose(result["final_equity"] - result["contributions"],
                            result["pnl"], abs_tol=1e-6)
        assert len(daily) == 252 and daily[-1]["open_ids"] == "[]"
        assert math.isclose(float(daily[-1]["equity"]), result["final_equity"], abs_tol=1e-6)
        assert math.isclose(float(daily[-1]["contributions"]),
                            result["contributions"], abs_tol=1e-6)
        totals.update(filled=len(trades), rejected=len(rejected))
        print(cohort, method, len(trades), len(rejected), round(result["pnl"], 2))
print("totals", dict(totals))
PY
```

## What the stress work actually executed

The original stress packet ran 5,645 **book/date/scenario calculations** on actual filled books at entry and while held. Its five cases are: no change; a one-day underlying −5% and option-implied-volatility +5 percentage points; a one-day −12%/+15 points; a one-day +10%/−5 points; and an expiry payoff with underlying −15%. "Implied volatility" (IV) is an input derived from an option quote for estimating its value before expiry. At expiry, the payoff instead follows the final underlying price, strike, option right and signed quantity; no IV is needed for that payoff.

Of those original 5,645 calculations, 4,496 priced and 1,149 explicitly refused. The refusals were 814 outside the original pricing method's declared numerical range and 335 with source IV recorded as zero. A later owner-approved, opt-in numerical method executed **813 new, distinct stress JSON pipelines** on the previously refused numerical cases: 232 limited and 581 broad. It retained the original attempts and did not invent IV. The combined view has 5,309 priced cases and 336 still refused: all 335 zero-IV cases and one RIOT option whose positive far-tail price is too small for the supported computer number range.

Those 813 pipelines each called the option repricer for an existing filled book. They did **not** call the weekly selector, order-fill process, cash ledger or expiry settlement process. They created zero new trades, changed zero original fills and rejected orders, and did not change any original historical profit. They improved the number of active weekly account snapshots for which every held book has a one-day model value: 588 to 915 out of 1,124. The remaining 209 snapshots are incomplete (208 include unavailable source IV; one contains the RIOT refusal). A snapshot is a book of held positions on a date under one scenario, **not** a unique trade or a failed fill.

The expiry-down-15 output includes a terminal payoff number for each of its 1,129 original book/date rows, including rows whose pre-expiry model mark refused. A book can appear on several weekly dates, so these are not 1,129 separate trades or an alternate full-account history. They do not reprocess order filling, account cash, subsequent selections or settlement under a coherent changed market path.

The broad robust-plus-shock account illustrates the distinction. On 2019-06-17 it held three filled call books. Under the **one-day** −12% underlying/+15-point IV hypothetical, their combined estimated value fell by $116,535.08. That is a model-mark change, not a realized 2019 trade loss or an expiry result. The original signal-time shock screen considered newly selected books, not the accumulated account, and was not recomputed after individual fill refusals. Earlier selected hedges in WYNN and CIEN failed for insufficient fill size, while the exposed TTD/GME books filled. A separate opt-in `filled_account` terminal gate has since been built and reviewed, but has **not** been run on these eight 2019 accounts. It also addresses a terminal screen, not this one-day model value.

## What remains unverified and the next bounded task

The ordinary historical execution has replay and accounting evidence. **Execution under changed fill conditions and changed expiry outcomes has not been run end to end.** The price-only stress jobs and fixed-trade extra-spread calculation cannot establish whether different books would fill, whether the account would have enough cash for later selected orders, or what final profit would be after those changes. The 2019 results should not be presented as surviving an execution stress test.

The next task should be a **fixed-selection execution-and-expiry replay** on the pinned 2019 inputs, with an unperturbed control. Freeze the original selected order batches and forecasts; do not rerun model training or the optimizer. Replay the fill and settlement rules in chronological order, carrying a separate cash/held-book state for each scenario. This isolates execution from strategy selection. Because future orders remain frozen even when a stressed account would have made different choices, results will be a conditional fixed-order diagnostic, not a full adaptively reoptimized strategy backtest.

The proposed first scenario contract, to be approved in an ADR before any code or run, is:

1. **Control:** original selected orders, quotes, displayed sizes and actual expiry settlements. Require exact filled/rejected book IDs, reasons, fill prices, fees, daily cash and final profit to reproduce all eight original runs. A control mismatch stops stress interpretation.
2. **Worse fill price:** use the actual next-session bid/ask quote and move each executable side adversely by an additional 25% of its observed spread, the same increment as the existing fixed-trade diagnostic. Recheck premium, fees, funding, reserve and shared caps at the altered prices; keep all-or-none books. The ADR must pin buy/sell price arithmetic, rounding, and refusal of invalid or nonpositive prices before implementation.
3. **Less fill size:** as a proposed sensitivity, reduce each observed next-session displayed capacity to the integer floor of half its value. Recheck every leg and shared cap; refuse rather than partially fill or replace a book. The half-size choice is **proposed, not approved**.
4. **Expiry price shocks:** re-settle each surviving book using its actual underlying expiry settlement multiplied by 0.85 and, separately, 1.15. This tests losses on both option directions without IV. These are pointwise terminal counterfactuals at different expiries, not a probability forecast or a single coherent historical price path.
5. **Joint cases:** combine adverse fill price and lower size with each expiry direction, carrying their effects on cash, holdings, later fill eligibility and final profit.

Every scenario should retain, per selected book, the original and stressed quote/size, decision, refusal reason or fill, fees, expiry payoff and account impact. Pin distinct JSON identities and CSV/JSON outputs beside, not over, the original evidence. Review the implementation with two independent lenses and use focused synthetic controls plus the pinned 2019 control; stop if the control is not exact. No full suite, 2020-and-later observations, provider pull, retraining, optional broad hindsight, Path/TDD change or duplicate original MIO run belongs in this packet.

The user has asked for execution stress testing, but the numbered sensitivity choices above have **not** been approved or run. Root `AGENTS.md` requires an owner-approved ADR before implementation. The next session should draft that ADR and obtain approval of the exact fill-price, size, expiry and control contract before writing or executing the replay.

## Reproducibility and handoff

Original evidence root: `/home/russell/data/index_options/mio-broad-2019-20261027`. The original repository pin file is `children/index_options/configs/mio-2019-results-pins.json`, SHA-256 `2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2`. It records the original runtime commit `64cb99b5aacecab8f2b579b3dc3dd09b17087b73`, run documents, close receipts, source and result evidence. The apparent 2020-03-08 run as-of is a settlement-tail/control timestamp, not permission to use 2020 market observations. The 2019 observations, original run configs and original artifacts remain unchanged.

The limited original `result.json` hashes to `d2e569dc83fc1234063d57db78ea63d61a6e33cd8ce0a5cfc63ed5557a417596`; its `trades.csv`, `rejected-orders.csv` and `daily-account.csv` hash to `5ea517c6f17d8edc4d6a6acf2d3205db52f04d4ef3629efe2ab67690120be058`, `f8a9d84d0abd3ba49e2ea8b920a9311db8e6eb90acd2ec8550259acd8af1778a` and `a3aa2a5e25a800eb8089cf5c5b1bbfe53f1394263a63eb51ab2cedd5e2f6da53`, respectively. The broad originals hash to `581ec30666a7d8b59107b80970f8cbe88e86d2a93fb932398dc0f92ad524904e`, `2e33345d8e78a237cf14db45a5662569335609114fd0ec7a57e48d5028f68c32`, `eb2c5a3d35e10209ecdbc550d9ab703448d87c6a30d9b0dbd56da05b18989d4e` and `244f188199dcfd9932441aebea04fe5572757fe6dfe69b3e16762a3aa2f60086` in the same order.

The opt-in pricing packet used reviewed code candidate `5df492c4c3386559d20a3e2579fcda000d75350e` and `python -m index_options.mio_stress_coverage` with `prepare`, `execute` and `aggregate` stages for each cohort, verified against the original results-pins hash. Its distinct outputs are under `stress-coverage-v2` inside the evidence root. The exact new pin files are `/home/russell/data/index_options/mio-broad-2019-20261027/stress-coverage-v2/limited36/coverage-pins.json` (SHA-256 `2f921b98610347d33bcd13a5ed7dbcdb6f46e4385e1fc383d595146f37bbfc3d`) and `/home/russell/data/index_options/mio-broad-2019-20261027/stress-coverage-v2/broad212/coverage-pins.json` (SHA-256 `b9df054182ff3c6de919ae586da41a96f35e86735b14f4c5bd637db0c3b18468`). Each hashes all new attempt records and account/book JSON/CSV. The detailed original and new stress JSON/CSV hashes are in the [stress diagnosis](2026-10-28-mio-2019-stress-diagnosis.md).

This memo is a read-only explanation and proposed next contract. It did not launch another MIO, stress, replay, training or provider job. The original WSL2 data, private viewer and prior checkouts were retained. The next authorized action is drafting the fixed-selection execution-stress ADR for owner review; code and scenario execution follow only after that approval.

## Next task prompt

Copy this into a new task:

> Continue DSKit `index_options` in WSL2 from current remote main. Read `AGENTS.md`, locked Path A0003/A0008, this memo, `children/index_options/configs/mio-2019-results-pins.json` (SHA-256 `2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2`) and the original evidence at `/home/russell/data/index_options/mio-broad-2019-20261027`. Draft an owner-approvable ADR for a **fixed-selection execution-and-expiry stress replay**, with no code or run yet. Specify an exact zero-shock control that reproduces all eight original runs’ selected, filled and rejected books, their reasons, fill prices, fees, daily cash and final profits. Define proposed adverse fill-price and capacity rules, including buy/sell arithmetic, rounding, invalid-price refusal and shared-cap treatment; define pointwise ±15% shocks to actual expiry settlements and joint cases. Require chronological all-or-none filling, account cash/reserve and settlement, distinct pinned outputs, focused tests and two independent Luna reviews. State that frozen future selections make this a conditional execution diagnostic, not a reoptimized strategy backtest. Preserve original runs, viewer, data, Path and TDD; do not open 2020+, retrain, pull providers, resume optional hindsight, run the full suite or rerun identical MIO. Stop after the ADR is concrete and ask the owner to approve its exact scenario choices before implementation.
