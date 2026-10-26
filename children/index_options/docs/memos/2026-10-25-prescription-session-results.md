## TL;DR

The best completed funded weekly experiment remains **M05 at 28–32 DTE: $6,800.05 profit on $35,000 contributed**, but only **$403.80** after extra-spread stress and with a **65.88% maximum marked drawdown**. The full-DTE M05 experiment has now completed and passed independent replay: **−$7,804.40 net**, **−$10,406.65 stressed**, with **34 of 53 weeks skipped at the solver timeout**. See the [high-level status memo](2026-10-26-mio-high-level-status.md) for the leader's tickers, DTEs and results.

Session: October 22–26, 2026; final v11 audit and publication completed October 26 UTC.
Owner: index_options, ADR-0278 (historically labeled ADR-0276). Code checkpoint: 82ac1ee1.
The [interactive research report](/home/russell/.cursor/projects/home-russell-dskit/canvases/index-options-2019-condor-oracle.canvas.tsx) retains raw results, detailed trades, oracle comparisons, source snapshots and failed attempts.

## Execution contract

- Offline trading development uses 2019. A separately approved January 2020 fitted-M51 mechanics diagnostic ran early in the session and is disclosed below. No 2024–25 or 2026 records were opened.
- Locked Path A0002/A0003/A0005/A0007/A0008 continues to govern forecast scoring, windows and promotion. The owner wants any eventual holdout examination month by month, starting with January 2024 after rules are frozen; no opening is authorized now.
- Every 2019 trading forecast here is the **base wide-MLP64 fallback**, M51 fitted=false. These runs do not measure the trading performance of fitted M51.
- Decision-day CDFs and quotes choose positions. Orders use next-session EOD bid/ask proxies, all-or-none per book, subject to quote, depth, basis and account limits. Committed orders are not reselected using next-day information.
- Costs include quote sides and $0.65 per contract. Stress adds 25% of each leg's quoted spread as a cost sensitivity. It is not a verified realized-slippage model.
- Settlement uses terminal intrinsic value. American early assignment, broker margin, intraday execution and actual fill probability are not modeled. Apparent guaranteed-profit boxes and near-zero-cost combinations need quote review.

## Predictive starting point

The [round-five memo](2026-10-21-edge-methods-round5-results.md) owns the forecasting evidence. M51 and M53 blend the model, recalibrated market, pooled and per-name distributions. M51 improves threshold-weighted CRPS by 0.68435% over the recalibrated option-implied market; M53 by 0.69034%. Their gates pass 233/831 and 200/831 cells, and both improve aggregate scores in all five regimes. M51 has 91/110 positive tickers and 21 positive in all four years.

These are **distribution forecast error comparisons**, not returns versus a stock index. Small forecast improvements can be economically useful, but selected payoffs still need to survive costs and risk limits.

The upstream completion addendum reports M57 bag blend +0.58167%, M64 66-input blend +0.54946%, and M65 106-input blend +0.57846%; none beats M51/M53. Those are prior predictive-lane results. Proposed packet six remains separate.

## Fixed condors: results and diagnosis

1. **AAPL January 2019 mechanics:** two decisions, one missing-next-session-leg refusal, one fill January 4 expiring February 1. Net +$323.40; declared maximum loss $2,876.60; extra stress $6.
2. **January 2020 fitted-M51 diagnostic:** six decisions, five cash decisions, one fill January 13 expiring February 7. Net +$703.40; maximum loss $2,546.60; extra stress $13.50. One additional unfitted context refused. These are one-trade anecdotes.
3. **Monthly 88-name sweep:** 1,056 ticker-month slots, including 88 December slots with no qualifying 2019-settling expiry. The other 968 contain 435 cash decisions, 220 insufficient-leg refusals, 161 unfilled selections and 152 fills. Net **−$10,859.19**, stress **−$15,066.44**; 99/152 winners. Forty-eight names traded: 27 positive, 21 negative; 40 of 88 stayed flat. May lost $30,528.20; AMZN May alone lost $14,065.60.
4. **17 nominally significant names:** AAPL, ACN, AG, AKAM, AMGN, APO, AXP, BX, CHTR, CLF, COF, CPB, CVS, EQT, FSLR, JPM, KKR. There were 31 fills in ten names: six positive names, four negative, seven flat. Net **−$3,675.60**, stress **−$4,380.85**. Summed per-trade maximum loss was $39,476.60; net/summed risk = −9.31%, versus −2.96% in the 88-name sweep. Neither ratio is a portfolio return. The screen used the same 2019 outcomes and no multiple-search correction.

All 31 filled condors had positive projected value at decision quotes. Their total projection was +$1,365.31; substituting fill prices while holding forecasts fixed left +$1,315.31. The difference from realized −$3,675.60 was **$4,990.91 of expiry-payoff surprise**, while quote movement cost $50.

Seven settled between both short strikes (+$1,928.80); six breached the put (−$1,764.60); eighteen breached the call (−$3,839.80). CHTR January lost $3,212.60 with short call 290 and settlement 340.95; AAPL May lost $2,250.60 with short put 210 and settlement 175.07.

Call-wing liability was $10,460 versus $3,490.87 forecast, above expectation in eight of eleven decision months. CHTR January explained 58.4% of the excess. This supports investigating selected right tails and concentration; 31 dependent observations do not establish systematic bias.

### Exposure and structure experiments

Each rule reoptimized the same visible 2019 development schedule, with caps checked again at fill. Their different choices can change later availability and fill counts.

| Condor rule | Fills | Net P&L | Stress P&L |
|---|---:|---:|---:|
| Uncapped | 31 | −$3,675.60 | −$4,380.85 |
| Maximum loss $500 | 16 | $272.40 | $146.40 |
| Maximum loss $750 | 22 | $171.80 | −$71.20 |
| Maximum loss $1,000 | 27 | $613.80 | $316.55 |
| Maximum loss $1,500 | 28 | $83.20 | −$235.55 |
| Maximum loss $2,000 | 29 | $286.60 | −$99.15 |
| Call wing no wider than put wing | 30 | −$1,650.00 | −$2,311.25 |
| Same wing rule plus $1,000 cap | 26 | $1,678.40 | $1,403.65 |
| Same wing rule plus $1,250 cap | 28 | −$598.80 | −$994.30 |

| Allowed one-lot structures | Fills | Net P&L | Stress P&L |
|---|---:|---:|---:|
| Put credit, uncapped | 56 | $8,839.20 | $8,211.45 |
| Put/call credit, uncapped | 66 | $6,465.20 | $5,787.20 |
| Four vertical types, uncapped | 69 | $7,932.30 | $7,274.55 |
| Put credit, $1,000 cap | 52 | $4,914.40 | $4,459.65 |
| Put/call credit, $1,000 cap | 63 | $2,751.10 | $2,235.85 |
| Four vertical types, $1,000 cap | 69 | $8,604.30 | $8,021.05 |

The four vertical types are put/call credit and put/call debit spreads. June and October supplied $8,166 of the capped four-vertical net. Moving the wing-rule condor cap from $1,000 to $1,250 reversed its profit sign. Stability and concentration remain unresolved.

## Free integer positions and robustness

The uncertainty set is a Wasserstein-1 ball around the projected CDF: probability moves along the finite price grid, with distance divided by spot and total transport bounded by rho. The optimizer maximizes expected net profit under the least favorable distribution inside that set. This is not a confidence interval or protection against every possible forecast error outside the support.

At rho .001, AAPL January 2/3 projected values fell from $263.366110/$271.565389 to $247.574110/$257.346389. The same condor strikes and +$323.40 fill remained. A synthetic rho 1 case selected cash.

The first free-leg binary pilot selected 50 one-lot shorts, revealing the remaining one-contract restriction and unlimited short-call loss. A later ten-contract integer pilot sold ten AAPL February 1 $157.50 puts: nominal expected net $2,237.39, robust $2,079.47, declared maximum loss $150,756.50. This had no funded-account cap.

Post-solve labels recognize standard structures and call unmatched books custom. They do not constrain optimization. The funded study ultimately permits arbitrary integer quantities within displayed quote depth, with shared capital and terminal-loss constraints.

### Own-CDF replay and hindsight

On the same 31 saved ticker/date/expiry opportunities, the own-CDF integer replay filled 24 and rejected seven for fill size. It filled 240 contracts, ten per book, with $156 fees. Net **$47,941**, stress **$41,237.75**, 16 wins/eight losses; best $20,984.50, worst −$34,086.50. No filled book was a condor. Two short-call books had unlimited modeled tail loss; the largest finite maximum loss was $273,306.50.

Broad groups and average dollars per filled book: two long-call books $258.50; two long-put books −$4,211.50; two short-call books −$5,566.50; sixteen short-put books $4,171.06; two mixed short-put/long-call books $121.50. Broad exposure groups differ from the exact whole-book classifier.

The hindsight condor oracle made $8,777.40 on the original 31 opportunities. The ten-contract unrestricted hindsight oracle made $345,913.50, stress $339,915.75, with 31 winners and four unbounded books. Hindsight chooses positions using realized outcomes; it is not predictive performance. The own-CDF replay also lacks the $10,000 funded-account constraint, so neither result is comparable to the weekly account study.

## Funded weekly 28–32 DTE study

Initial cash was $10,000 plus $1,000 every 14 days: 25 deposits, **$35,000 total contributed**. New weekly reserve was capped at 25% of funded capital, total reserve at 100%, with further limits in specified variants. The universe was the original same-2019 screened 17 names. Reserves cover modeled terminal loss/debit, not broker margin.

Ten definitions were set: M01 nominal; M02–M05 radii .001/.003/.01/.03; M06 name 25%; M07 name 10%; M08 total reserve 50%; M09 correlation component 35%; M10 radius .003 plus total 50%/name 25%/component 35%. **Four of ten completed under the corrected older contract-count tie policy.** M03, M04 and M07–M10 have no accepted completion. Later single-profit/full-DTE attempts belong to separate policies.

| Method | Filled books | Contracts | Net P&L | Stress P&L | Final equity | Time-weighted return | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| M01 nominal | 25 | 926 | $-8,679.90 | $-13,280.65 | $26,320.10 | -4.78% | -67.34% |
| M02 W1 .001 | 25 | 634 | $-1,310.10 | $-5,389.10 | $33,689.90 | -47.23% | -82.57% |
| M05 W1 .03 | 17 | 583 | $6,800.05 | $403.80 | $41,800.05 | 13.44% | -65.88% |
| M06 ticker 25% | 28 | 749 | $-546.85 | $-4,943.35 | $34,453.15 | -47.08% | -81.33% |

For M05, $6,800.05/$35,000 = **19.43% of contributions**, which ignores deposit timing. The daily flow-adjusted, time-weighted return is **13.44%**. Stress subtracts $6,396.25 of extra spread costs: $6,800.05−$6,396.25=$403.80. It does not change the optimized objective.

M05 had 17 fills, 47 unfilled selected books, ten wins/seven losses, and 583 contracts. CPB May plus AXP November made $10,556.75; all other fills combined lost $3,756.70. Two apparent short boxes contributed $34.80 at zero modeled reserve. Near-zero debit combinations and high contract counts require source-quote review.

### Exact M05 filled history

DTE is **decision-date / fill-date** calendar days to expiry. B/S means buy/sell; number is contracts; C/P is call/put; strike is raw dollars; @ is fill premium per share. Each contract represents 100 shares. P&L includes entry fees. This is the completed older 28–32 DTE experiment.

| Decision | Fill | Ticker | Expiry | DTE decision/fill | Contracts | Filled legs | Net P&L |
|---|---|---|---|---:|---:|---|---:|
| 2019-01-14 | 2019-01-15 | AMGN | 2019-02-15 | 32 / 31 | 4 | B1 180P @ 1.26; S1 210P @ 12.6; B1 210C @ 1.77; S1 180C @ 20.55 | $9.40 |
| 2019-01-14 | 2019-01-15 | CLF | 2019-02-15 | 32 / 31 | 9 | S3 9P @ 0.91; B3 11C @ 0.07; S3 9C @ 0.37 | $-242.85 |
| 2019-02-11 | 2019-02-12 | AMGN | 2019-03-15 | 32 / 31 | 8 | B2 155P @ 0.14; S1 185P @ 3.5; S1 195P @ 9.15; B1 185C @ 6.1; B1 195C @ 1.7; S2 145C @ 41.4 | $-268.20 |
| 2019-02-19 | 2019-02-20 | APO | 2019-03-22 | 31 / 30 | 3 | S1 31P @ 1.7; B1 31C @ 0.35; S1 20C @ 9.2 | $-46.95 |
| 2019-03-18 | 2019-03-19 | ACN | 2019-04-18 | 31 / 30 | 260 | B23 150P @ 0.8; B21 152.5P @ 1.05; B21 155P @ 1.6; S14 170P @ 6.7; S9 172.5P @ 8.5; S21 175P @ 10.4; S21 177.5P @ 12.4; B14 170C @ 2.5; B9 172.5C @ 1.65; B21 175C @ 1; B21 177.5C @ 0.7; S13 150C @ 16.5; S41 152.5C @ 14.4; S11 155C @ 12.1 | $-169.00 |
| 2019-05-06 | 2019-05-07 | CPB | 2019-06-07 | 32 / 31 | 12 | B12 35.5C @ 3.5 | $4,888.20 |
| 2019-05-13 | 2019-05-14 | AG | 2019-06-14 | 32 / 31 | 7 | S7 7.5P @ 1.5 | $380.45 |
| 2019-07-01 | 2019-07-02 | CVS | 2019-08-02 | 32 / 31 | 57 | B1 48P @ 0.13; B1 48.5P @ 0.15; B12 53P @ 0.82; S5 56P @ 1.92; S10 59P @ 4.7; B5 56C @ 0.74; B10 59C @ 0.17; S1 48.5C @ 5.3; S12 53C @ 2.34 | $0.95 |
| 2019-07-15 | 2019-07-16 | AMGN | 2019-08-16 | 32 / 31 | 4 | B1 165P @ 1.74; S1 185P @ 10.2; B1 185C @ 2.03; S1 165C @ 13.85 | $25.40 |
| 2019-07-22 | 2019-07-23 | AAPL | 2019-08-23 | 32 / 31 | 28 | B1 180P @ 0.62; B1 182.5P @ 0.74; B5 185P @ 0.89; S2 215P @ 9.75; S3 217.5P @ 11.2; S2 225P @ 17.3; B2 215C @ 3.4; B3 217.5C @ 2.55; B2 225C @ 0.98; S3 182.5C @ 26.95; S4 185C @ 24.6 | $-45.20 |
| 2019-08-19 | 2019-08-20 | ACN | 2019-09-20 | 32 / 31 | 6 | B2 160P @ 0.4; S2 190P @ 2.55; B2 190C @ 7.6 | $-475.90 |
| 2019-09-23 | 2019-09-24 | CVS | 2019-10-25 | 32 / 31 | 116 | B29 58.5P @ 0.5; S16 66.5P @ 4; S13 67P @ 4.15; B3 66C @ 0.53; B10 66.5C @ 0.45; B16 67C @ 0.35; S29 58.5C @ 3.8 | $-3,729.40 |
| 2019-09-30 | 2019-10-01 | CLF | 2019-11-01 | 32 / 31 | 11 | S11 8P @ 1.06 | $685.85 |
| 2019-10-14 | 2019-10-15 | EQT | 2019-11-15 | 32 / 31 | 33 | B11 14P @ 4.5; S22 9P @ 0.45 | $33.55 |
| 2019-10-28 | 2019-10-29 | BX | 2019-11-29 | 32 / 31 | 10 | B1 45P @ 0.14; S3 56P @ 3.65; B3 56C @ 0.3; S3 41C @ 11.75 | $9.50 |
| 2019-11-18 | 2019-11-19 | CPB | 2019-12-20 | 32 / 31 | 2 | S1 45P @ 0.6; B1 45C @ 4 | $75.70 |
| 2019-11-25 | 2019-11-26 | AXP | 2019-12-27 | 32 / 31 | 13 | B3 114C @ 6.7; B9 115C @ 5.85; B1 116C @ 4.95 | $5,668.55 |

## Pre-2019 universe and all supported DTEs

New universe: **ABBV, ACN, AMGN, AMZN, BA, CAT, CME, COF, GILD, GLW, GM, GOOG, GOOGL, HON, HUM, ISRG, JPM**.

The screen uses sampled fold-1 training-fit scores from 2017-01-03 through 2018-08-30, with outcomes settled by 2018-08-31. Require at least 100 quote dates and nominal t≥2 versus both raw and recalibrated market distributions, rank by the smaller lower edge estimate, retain 17. There were 86,798 sampled fit rows, 38,810 paired market-eligible rows, 90 evaluable tickers and 42 passes. These are training-fit observations with unadjusted uncertainty, so this is an earlier-data development screen.

Frozen 2019 intake has 53 weekly decisions, 252 sessions, **5,867 ticker-expiry opportunities** and **544,346 option-contract opportunities**. Only 816 ticker-expiry opportunities are 28–32 DTE. Actual eligible DTE spans 2–60; longer listed expirations lack supported CDF forecasts. Every included expiry settles in 2019.

M05 uses rho .03 uniformly. There is no additional learned ticker/DTE accuracy weight in the objective. The CDF and screen influence choices; confidence-dependent robustness would require prior-only calibration.

### Attempts and current policy

- v1/v3/v4 formulations and marking/intake profiles remain archived. Sparse transport and dominated-opposite-pair simplification reduced overhead; expiry-basis intake and recorded-account validation were corrected. The table uses sealed corrected v5/v6 results.
- v7 removed the second contract-minimization solve; preparation only, no completed market result.
- v8 full-DTE/$1 gap stopped after 600 seconds with gap $48.8656773943; no accepted method.
- v9 restored historical acceptance max($0.05, 0.01% of absolute expected profit). It started, but no terminal record was saved and processes disappeared. Cause unknown; no OOM evidence found.
- v10/$50 gap stopped after 600 seconds at gap $60.4079223517. Peak service memory 6.1 GiB, zero swap; the recorded failure was optimization certification. An exact retry was stopped to implement the owner's new rule.
- **v11:** $50 absolute acceptance/native gap, zero relative gap, one profit solve, one worker/thread, unchanged universe/funding, **900-second native limit**. A qualifying incumbent can trade; unresolved native timeouts record timeout_skipped and place no new orders. Existing positions settle and deposits continue. Solver errors and invalid evidence remain failures. Model preparation and independent audit/accounting lie outside the solver cap.

Completion can include skipped weeks. Report that count alongside selected/cash/ineligible decisions; completion does not imply 53 certified solves. A timeout with no bound or incumbent is explicitly uncertified, not an invented cash optimum.

## Completed full-DTE result — 2026-10-26

V11 completed under the approved 17-name pre-2019 screen, rho .03, all supported positive DTEs through 60, $10,000 initial funding plus 25 biweekly $1,000 deposits, $50 absolute gap, zero relative gap and 900-second native solve limit. Every one of 53 decision dates and 252 daily account observations is present. No positions remain open and no execution error is recorded.

- **Final equity $27,195.60; net P&L −$7,804.40; stress P&L −$10,406.65.** Profit/contributions = −$7,804.40 / $35,000 = −22.30%; time-weighted return, which adjusts for deposit timing, **−16.29%**. Maximum marked, flow-adjusted drawdown **62.18%**.
- **17 selected weeks, 34 timeout skips, one cash week, one ineligible week.** These are weekly decisions, not fill counts. The 17 selected weeks produced 127 ticker/expiry books: 13 filled (10.24%) and 114 unfilled (89.76%).
- Unfilled reasons: 39 missing next-session quotes, 60 insufficient next-session size and 15 repriced shared-cap refusals. Skipped timeout weeks contributed no orders.
- **13 filled books, eight tickers, 256 gross contracts, $166.40 fees, six winners and seven losers.** No unbounded book. Maximum simultaneous open books: six; peak modeled reserve $22,183.05. Five short boxes, one single long call and seven custom books; no condors.
- Actual fills spanned **17–53 decision DTE and 16–52 fill DTE**. Eligibility remained positive DTE≤60 with settlement inside 2019.
- AMZN's two long-call books lost **$14,322.60**. The other 11 books together earned **$6,518.20**, principally ACN's **$6,201.75**. This is an observed contribution breakdown, not evidence to remove AMZN after seeing its outcome.
- The historical 28–32 DTE M05 remains the strongest completed funded experiment by both net profit and time-weighted return. The new experiment also changes ticker screen, tie-break policy and timeout policy, so its deterioration cannot be attributed to DTE alone.

### Full-DTE ticker results

| Ticker | Filled books | Contracts | Decision DTE | Fill DTE | Net P&L | Stress P&L |
|---|---:|---:|---|---|---:|---:|
| ABBV | 1 | 32 | 53 | 52 | −$60.80 | −$298.80 |
| ACN | 1 | 5 | 52 | 51 | $6,201.75 | $6,164.25 |
| AMGN | 1 | 4 | 32 | 31 | $9.40 | −$47.35 |
| AMZN | 2 | 4 | 38, 46 | 37, 45 | −$14,322.60 | −$14,513.85 |
| BA | 3 | 52 | 17, 24, 32 | 16, 23, 31 | −$194.80 | −$1,055.05 |
| CAT | 1 | 52 | 45 | 44 | −$85.80 | −$440.05 |
| GILD | 2 | 100 | 24, 25 | 23, 24 | −$80.00 | −$451.75 |
| GOOG | 2 | 7 | 32, 39 | 31, 38 | $728.45 | $235.95 |

Nine screened names had no filled book: COF, CME, GLW, GM, GOOGL, HON, HUM, ISRG and JPM.

### Full-DTE exact filled history

All dates are in 2019. DTE is calendar expiry minus decision/fill date. B/S means buy/sell, P/C means put/call; premiums following @ are dollars per share. One option contract covers 100 shares. Net P&L includes $0.65 per contract; stress subtracts 25% of the fill spread per leg. Table order is decision date; the saved ledger closes in expiry order.

| Decision | Fill | Ticker | Expiry | DTE decision / fill | Contracts | Net P&L | Stress P&L | Exact legs at fill |
|---|---|---|---|---:|---:|---:|---:|---|
| 2019-01-14 | 2019-01-15 | AMGN | 2019-02-15 | 32 / 31 | 4 | $9.40 | −$47.35 | B1 180P @1.26; S1 210P @12.60; B1 210C @1.77; S1 180C @20.55 |
| 2019-01-14 | 2019-01-15 | BA | 2019-02-15 | 32 / 31 | 44 | −$232.60 | −$983.60 | B11 265P @0.44; S1 365P @19.70; S10 370P @23.05; B1 365C @6.25; B10 370C @4.65; S11 265C @86.85 |
| 2019-01-22 | 2019-01-23 | ACN | 2019-03-15 | 52 / 51 | 5 | $6,201.75 | $6,164.25 | B1 145C @7.30; B4 150C @4.40 |
| 2019-01-22 | 2019-01-23 | BA | 2019-02-08 | 17 / 16 | 4 | $10.40 | −$42.60 | B1 340P @5.00; S1 385P @29.05; B1 385C @1.97; S1 340C @23.05 |
| 2019-01-22 | 2019-01-23 | BA | 2019-02-15 | 24 / 23 | 4 | $27.40 | −$28.85 | B1 300P @0.96; S1 385P @29.35; B1 385C @2.49; S1 300C @59.40 |
| 2019-08-26 | 2019-08-27 | ABBV | 2019-10-18 | 53 / 52 | 32 | −$60.80 | −$298.80 | B8 60P @1.01; S8 72.5P @7.75; B8 72.5C @0.44; S8 60C @6.15 |
| 2019-08-26 | 2019-08-27 | GOOG | 2019-09-27 | 32 / 31 | 5 | $331.75 | $81.75 | B2 1200C @18.60; B1 1210C @15.00; B1 1215C @13.30; B1 1220C @11.60 |
| 2019-09-03 | 2019-09-04 | AMZN | 2019-10-11 | 38 / 37 | 1 | −$7,770.65 | −$7,818.15 | B1 1770C @77.70 |
| 2019-09-03 | 2019-09-04 | CAT | 2019-10-18 | 45 / 44 | 52 | −$85.80 | −$440.05 | B13 105P @0.99; S13 125P @8.15; B13 125C @1.95; S13 105C @14.75 |
| 2019-09-03 | 2019-09-04 | GILD | 2019-09-27 | 24 / 23 | 24 | −$0.60 | −$87.60 | B3 60.5P @0.67; B3 61P @0.82; S3 64.5P @2.45; S3 65.5P @3.15; B3 64.5C @0.74; B3 65.5C @0.47; S3 60.5C @2.99; S3 61C @2.66 |
| 2019-09-09 | 2019-09-10 | AMZN | 2019-10-25 | 46 / 45 | 3 | −$6,551.95 | −$6,695.70 | B1 1940C @24.30; B1 1950C @21.75; B1 1960C @19.45 |
| 2019-11-18 | 2019-11-19 | GOOG | 2019-12-27 | 39 / 38 | 2 | $396.70 | $154.20 | B1 1320C @28.80; B1 1322.5C @28.50 |
| 2019-12-02 | 2019-12-03 | GILD | 2019-12-27 | 25 / 24 | 76 | −$79.40 | −$364.15 | B10 61P @0.30; B9 64P @0.87; S12 67.5P @2.65; S7 68P @3.00; B12 67.5C @0.72; B7 68C @0.59; S10 61C @4.80; S9 64C @2.50 |

### Solver and audit evidence

All 34 timeout skips carry independently checked feasible incumbents with positive modeled profit and native maxTimeLimit records. All were skipped because the absolute gap exceeded $50. No missing-bound or no-incumbent timeout occurred in this actual run. The observed timeout gap was $51.89 minimum, $255.92 median and $2,830.35 maximum. All 18 accepted selected/cash decisions satisfy the $50 gate; the one ineligible week required no solve.

There are 52 native solve records. The largest recorded model has 35,983 variables and 38,195 constraints. Summed recorded solve durations are 35334.27 seconds; the saved method elapsed field is 36,362.30 seconds. These fields are retained as recorded, not asserted to be an exclusive decomposition of total wall time. Timer placement and setup/audit overhead belong in the speed investigation.

The service launched at 03:22:52 UTC; week 53 was logged at 13:11:53 UTC; the independent replay finished before RESULT at 13:36:47 UTC; COMPLETE True followed at 13:36:48 UTC and the service ended at 13:36:49 UTC. The final replay/publication interval was approximately 25 minutes. Systemd reported 10h13m30.771s CPU, 9.5G peak memory and 3.0G peak swap; this run did use swap. No OOM failure was recorded.

The coordinator accepts an outcome only after a separate process rebuilds bounded 2019 input identity and the candidate census, replays saved positions/certificates through all weekly decisions and the account, checks the reconstructed outcome, and rechecks source hashes. Publication completed through that gate. The parent additionally checked:

- complete calendar/decision-date coverage and empty terminal holdings;
- all six production and six embedded source hashes against the approved activation;
- sums of trade P&L, fees, contracts, stress costs and deposits;
- exact decision/fill DTE and expiry settlement for every filled book;
- cash plus contributions and the independent product of daily deposit-adjusted returns, including drawdown;
- each timeout's empty order list, native maxTimeLimit reason and gap above $50; every accepted selected/cash gap at or below $50.

No optimization or full test suite was rerun for these evidence checks. The existing approved trust in local native solver bounds remains; recorded replay is not authentication against forged coherent native bounds.

Completed evidence hashes: report SHA256 a38f8e5a8f26fec2814bb4b03d473891ebe35f6f00af3c9e94e6476caea4f1fa; raw value hash a4186a8c359d6cef1b206ce71d16bd05d346a105f4aa76bb6397e3a26773a25c; method value hash 6f0ae27e85deebfd46e59cd89ef7956f666dd91642bf324638c1e33141cfe882. The final report is retained without edits by this memo closeout.

The plain-language overview and historical leader's ticker/DTE ledger are in [MIO high-level status](2026-10-26-mio-high-level-status.md).

## Implementation verification

Generic owners: TransportPortfolioSolve, WassersteinDual, FundedOptionAccount and OptionStructureLedger. The child owns bounded option intake and weekly decision/fill/account chronology. Full-domain terminal-loss/debit reserves prohibit unlimited short-call exposure in the funded study. There is no forced condor or fixed leg count.

The timeout change passed **123 relevant synthetic tests** and **13 embedded policy/integration families**, including generated workers, independent recorded audits, later trades after timeouts, settlement of earlier books, exact resume, and 171 malformed-record refusals. UI syntax/default rendering passed. No full suite ran.

The owner approved trusting native local solver bounds for this study. Replay reconstructs positions, caps, values, chronology, cashflow and accounting; it cannot authenticate deliberately forged coherent native bounds. Deferred minor issues remain: tuple/list cleanup compatibility for generic callers and auxiliary LP thread-pool configuration. Earlier quote-spread boundary rounding refusal remains documented.

## Where to go next

1. Use the completed v11 result as the baseline for the requested Fable investigation: 34/53 timeout skips, 13/127 filled books and a loss concentrated in two AMZN call books. Preserve failures beside successes.
2. Attribute changes carefully: universe, DTE range, contract-count tie policy and timeout behavior differ from old M05. A profit difference cannot isolate DTE.
3. Audit economic drivers before tuning: CPB/AXP concentration, high-quantity near-zero debit books, apparent boxes, missing quotes, depth, carried marks, and expected versus realized payoff by ticker/DTE/strategy.
4. Freeze a small same-input comparison on 2019, including nominal versus robust forecasts and conservative costs. Count all tried methods; use earlier-data calibration and shrink sparse ticker/DTE estimates before adding accuracy weights.
5. Establish regime stability and freeze the recipe and abandonment criteria before any holdout opening. Keep predictive validation distinct from strategy tuning; January 2024 remains unopened.

## Reproducibility and handoff

Workspace: /home/russell/wt/index-options-harvest-20261022.
WSL interpreter: /home/russell/dskit/.venv/bin/python -B.
Environment: PYTHONDONTWRITEBYTECODE=1, OPENBLAS_NUM_THREADS=1,
OMP_NUM_THREADS=1, MKL_NUM_THREADS=1; PYTHONPATH is the workspace plus
children/index_options. One heavy study worker; C: must stay above 30 GiB.

The report embeds weeklyStudyConfig, weeklyPortfolio, weeklyPriorFormulationRuns,
pre2019FullDtePlan, initializer/coordinator/comparison sources and focused tests.
Failed attempts preserve original raw lines, source profiles and review evidence.
Do not edit that artifact or runtime sources while the service runs: publication
refuses a concurrent-generation change.

V11 activation: 565e6a28c22ce307559d1b30b8361f388b0fa0295b6fa06ce7b6da5eb0aca61b.
Prepared intake: 44d615b48bdcdf0e300081ecdedefd0d7b2d0194ab8f10d118d4e4fa29b94580.
Original metadata/census: c762fce8cd29b91572bcdcc330583a93b384420bcb9fae076b817f5d5df39b2c.
V10 archive: 437532540c6e5cb519e93ef5b1704df405479820879b5b5f9e259d98b37bd81c.

## Closure status

Both fresh independent final reviews passed with zero Critical/Major and no new Minor/Nit findings; the two previously documented minors remain deferred. Code checkpoint 82ac1ee1404a1b545ee803902ebf15d9d578f3e7 passed 123 focused tests and 13 embedded verification families. Upstream d64ff302 integration changed documentation/evidence only; reviewed runtime and test identities are unchanged.

V11 launched at **2026-10-26 03:22:52 UTC** (October 25 locally) in WSL as index-options-2019-full-dte-v11.service. The journal confirms FRESH_PRE2019_FULL_DTE_INITIALIZED with the intake identity above and START M05 W1 .03 verified inputs. Launch PID: 2713844; invocation: bc5e40558c1844f08a8097651e6d1ebd. The service was active/running at the launch check. The completed result and independent audit are recorded above.

V11 ended successfully at 13:36:49 UTC on October 26. The requested high-level memo and full results addendum were written before wrap. This closeout changes documentation only; runtime, tests and the completed report remain unchanged. The next authorized step after publication and wrap is a separate Claude/Fable research session for method selection, additional MIO proposals and solve-speed investigation. The launch was subsequently verified as recorded below; the completion heartbeat can now be paused.


### Fable session launch receipt

After both memos were published in commit 3aaff02508da09032d840746d5302ea51f6d9d2f and wrap completed, the requested research session started at **2026-10-26 14:07:03 UTC**. Actual assistant model verified from its transcript: **Claude Fable 5.1** (claude-fable-5-1); session **09563c01-7415-4275-a466-2db186f3c75d**. The trusted three-part prompt was received and the agent began read-only investigation.

- Own checkout: /home/russell/wt/index-options-mio-fable-20261026, based on the published memo commit.
- WSL service: index-options-mio-fable-20261026.service; invocation 885a028e0421423ebbe044dbd29f0888.
- Launch/prompt/model receipts: /home/russell/.local/state/index-options-mio-fable-20261026/.
- The dispatcher saves its eventual final answer as research-report.md in that receipt directory. At this launch check the investigation was running; no recommendation is claimed complete.
- Scope: rank methods, propose additional MIO work and investigate solve speed. No runtime edits, new market backtests, holdout access, model substitution or unattended mutation flags.
