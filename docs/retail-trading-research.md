# Retail trading edges: evidence and a research agenda

Research date: 2026-09-22 UTC. Audience: an ML researcher new to quantitative
finance, already developing `pmquant` and `intraday_equities`.

## The answer and the top options

There are plausible research opportunities for a retail account, but this review
does not establish a currently executable, reliably profitable strategy for this
owner. The strongest historical findings, the clearest small-account privileges,
and the best applications of ML are different things.

My research-priority ranking is:

1. **Slow, diversified trend following.** Best first baseline and relatively cheap
   experiment. Study monthly decisions across liquid asset classes. The historical
   futures evidence is substantial, but its forecasting interpretation is disputed;
   a long/cash ETF adaptation needs its own test.
2. **Low-turnover quality plus momentum in liquid equities.** Best bridge from
   existing equity/ML infrastructure to slower investing. Test a transparent,
   long-only ranking before ML. Published long/short factor evidence is not proof
   that this particular long-only portfolio produces net excess returns.
3. **A bounded SEC filing and corporate-event study.** Best fit for ML/NLP skills,
   with weaker evidence of tradable return prediction. Start with a precise event
   and timestamp; distinguish extracting useful information from predicting a
   price move that has not happened yet.
4. **An odd-lot tender-offer scanner.** Clearest example of a genuine small-account
   privilege, but likely an occasional supplemental activity. Evaluate annual net
   dollars per hour, not annualized percentages from a handful of successful deals.
5. **Fixed-maturity crypto cash-and-carry, conditional on access.** A documented
   economic premium with substantial operational and margin risks. Its first
   experiment is executable spread and cash-flow accounting, not price prediction.

**Reserve candidate:** closed-end funds with an explicit discount-closing catalyst.
This can replace crypto in the investigation queue if ordinary brokerage access
and corporate-event research are a better fit. A discount without a credible
convergence mechanism is insufficient.

This is a ranking of **research usefulness for this owner**, not predicted returns
or investment allocations. Evidence quality and cheap falsification put the first
two ahead; ML fit puts the third ahead of stronger but sparse contractual mechanics.
The fourth moves to the top only if the question is specifically where small size
confers an explicit privilege. The fifth depends materially on jurisdiction and
broker access. Sources and competing explanations follow below.

## Scope, evidence standard, and existing work

The review covers slower public-market signals, small-capacity corporate events,
derivatives and carry. It is a targeted literature and implementation review, not
an exhaustive systematic meta-analysis. It uses original papers, institutional
research, issuer filings and official data documentation. No new return backtest,
live-fill experiment, broker eligibility check or prospective trading record was
produced for this report. Evidence labels distinguish historical results,
contractual mechanics and untested hypotheses.

The owner's country of residence, account permissions, capital, tax treatment and
available time remain unknown. Dollar scenarios below are illustrations, not
eligibility or suitability determinations. Broker charges, product availability,
margin and tax rules need account-specific verification before any implementation.

The inspected repository base is `2242abd5537396d0d2e3602de61e901d1434613b`:

- [pmquant](../children/pmquant/README.md) already addresses Kalshi/Polymarket ladder
  mispricing, recorded books, fee-aware allocation and statistical gates.
- [intraday_equities](../children/intraday_equities/README.md) addresses short-horizon
  US equities with separate historical and live feeds, model comparisons and paper
  execution. Existing configurations and machinery do not establish profitability.
- The [pipeline inventory](../dskit/pipeline/README.md) and
  [onboarding inventory](../dskit/onboarding/README.md) provide useful reusable
  machinery. This study does not alter either child's research decisions or data.

An economically meaningful result must improve on an accessible benchmark after
implementation costs, with a convincing account of risk. Forecast accuracy,
statistical significance, profitable simulated trades and personal economic value
are separate milestones. A strategy can earn positive returns by taking equity,
crash, liquidity or financing risk without possessing forecasting skill.

## 1. Slow diversified trend

### Evidence and mechanism

[Moskowitz, Ooi and Pedersen](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)
documented time-series momentum across 58 futures/forward instruments, with
continuation at intermediate horizons. Slow information adjustment and hedging
pressure are candidate explanations. However,
[Huang, Li, Wang and Zhou](https://ink.library.smu.edu.sg/lkcsb_research/6521/)
found weak asset-level forecasting evidence and similar portfolio performance from
a historical-mean strategy that does not require momentum predictability.

**Verdict:** meaningful historical strategy evidence, disputed interpretation as
forecasting alpha. Neither paper establishes the return of a retail ETF adaptation.
Small accounts can have little market impact, but they have no exclusive signal;
contract sizing and a limited instrument set can disadvantage them.

### Feasibility and ML role

Begin with daily, distribution-aware histories for liquid equity, bond, gold and
broad commodity ETFs; choose the universe before evaluating returns. Holding
periods of weeks to months and monthly decisions make a pilot computationally
modest. An ETF long/cash study is an adaptation, not a replication of a diversified
long/short futures portfolio. Commodity ETF construction and distributions matter.

Data requirements: dated instruments and inception dates, tradable prices,
distributions/splits, cash yield and observed spreads. Later futures work needs
individual contracts, expiry, roll trades, multiplier, settlement and margin;
back-adjusted price changes alone are not a valid cash-flow ledger.

ML is secondary: test smooth sizing, shrinkage or abstention only after a simple
signal works. A neural model's greater flexibility increases the number of ways
the historical sample can be selected accidentally.

### First experiment and stopping rule

Predeclare a monthly 12-month excess-return sign rule, with a long/cash portfolio.
Use next-session execution. Compare with passive holdings, a historical-mean rule,
and matched-volatility and matched-cash-exposure controls. Report returns, drawdown,
turnover and contribution by asset class; include cash interest and fund expenses
without double-counting expenses already embedded in returns.

Use expanding evaluation windows, reserve a terminal period before tuning, and
stress costs and execution delay. Stop expanding this project if apparent benefit
depends on one asset class or period, disappears under plausible costs, or cannot
be distinguished from simpler exposure controls. A failure of the forecast claim
can still leave a useful risk-management rule, which needs a different claim and
benchmark. The pilot is feasible on the M4/32 GB machine; actual resources have not
been benchmarked.

## 2. Liquid-equity quality plus momentum

### Evidence and disagreement

[Asness, Frazzini and Pedersen's Quality Minus Junk](https://doi.org/10.1007/s11142-018-9470-2)
finds international evidence for quality portfolios, but its headline construction
buys quality and shorts junk. Removing the short side and adding momentum creates
a different strategy. Investor underreaction or mispricing is a possible mechanism;
factor exposure and benchmark specification remain alternative explanations.

Costs materially change the opportunity set.
[Novy-Marx and Velikov](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)
find that low turnover and different entry/holding thresholds help retain modeled
net returns. Conversely,
[Chen and Velikov](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
combine costs, recent/post-publication data and selection adjustment and report
negligible expected net returns among the selected anomalies they examine.

Successful reproduction of a paper is a lower bar than investment success.
[Chen and Zimmermann](https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm)
provide open code/data and broad reproduction of published predictors. That does
not negate cost or selection concerns: reproducing a historical specification and
finding a current, liquid, net-of-cost portfolio answer different questions.

**Verdict:** credible reason to study low-turnover factor exposure; unproven
incremental retail alpha. Small size reduces market impact but does not create
exclusive information or protect against factor underperformance.

### Feasibility and ML role

Use a long-only liquid universe, conservative position caps, quarterly rebalancing
and rank buffers. Measure quality with simple profitability/balance-sheet features
and momentum with a predeclared past-return window. Exact features and weights are
research choices, not findings of this review.

The hard part is historical data: security identifiers with validity dates,
delisted firms, corporate actions, point-in-time fundamentals and an investable
universe. Today's index members and retrospectively restated accounts are not
adequate. SEC data is publicly available, but that does not make a complete
survivor-free research panel free or easy to assemble.

Ridge or gradient-boosted trees are reasonable first ML comparators. Models must
beat the same simple composite after their induced turnover, not merely increase
prediction correlation. Accounting data and monthly panels offer a manageable
route to reusing the existing equity research infrastructure.

### First experiment and stopping rule

Freeze a liquid-universe rule and compare a simple quality/momentum composite
against cap-weighted and equal-weighted universe portfolios and an accessible
factor benchmark. Use past-only fitting, delayed accounting availability,
predeclared position limits and explicit costs. Report a recent post-publication
period separately and test nearby rebalance/holding choices without selecting the
best after viewing results.

Reject a claim that survives only in microcaps, unavailable short positions,
survivor-only histories, missing delisting losses or implausible spreads. Stop the
ML expansion if it cannot improve the composite on frozen forward windows. A
public factor-return series is useful for orientation but is not a security-level
implementation backtest.

## 3. SEC disclosures and corporate-event information

### Evidence and mechanism

The strongest initial ML use may be converting difficult disclosures into reliable
structured facts. An investor-attention or slow-processing mechanism could make
some facts predictive after release, but that is a hypothesis requiring a
post-release test.

[Frankel, Jennings and Lee](https://pubsonline.informs.org/doi/abs/10.1287/mnsc.2021.4156)
find ML disclosure-sentiment measures explain returns at filing/conference-call
dates better than dictionaries. This supports information extraction; it does not
show that an investor reading the disclosure can subsequently capture those
returns. [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
offer public submissions and XBRL data suitable for building an auditable corpus.

**Verdict:** high skill fit and practical data access, weak evidence for current
tradable directional alpha. This ranks third as a bounded learning experiment, not
because its profit evidence is stronger than a tender's contractual mechanics.

### Choose a narrow question

One example: do changes in a specified section of 10-Q/10-K filings improve
20-day residual-return or risk forecasts beyond numeric fundamentals, prior
returns and a simple text baseline? Another is extracting tender/catalyst terms
for the event screens below. Do not start with every form, every horizon and every
embedding model.

Retain accession number, CIK, form, acceptance timestamp, amendment lineage,
original bytes/hash, document period, security mapping and the first time each
field was available. Earnings may already have appeared in a press release before
EDGAR acceptance. Return windows and simulated orders must respect that sequence.

An additional ML-specific trap is retrospective knowledge in pretrained models:
an embedding or language model trained after the historical test period may know
the issuer, event or outcome. Strict historical claims require a defensible model
training cutoff or past-only training. Otherwise label the exercise exploratory
and rely on genuinely future evaluation for the final claim.

### First experiment and stopping rule

Build a small hand-labeled event corpus, audit extraction error and compare a
past-trained bag-of-words model with numeric-only, dictionary and no-text
baselines. A modern frozen embedding is a separate exploratory comparator unless
its historical information boundary can be established.

Use next-regular-session execution and price labels starting at that entry,
separate release-day explanation from later prediction, and cluster correlated
filings/events. Stop the directional project if text adds no net value beyond
numeric and momentum controls, or if the gain is entirely an already-completed
overnight move. Accurate event extraction or volatility forecasting can remain
useful without a return-alpha claim. Batch text locally and cache features; avoid
full-corpus transformer training as the first step.

## 4. Odd-lot tenders: a small-account privilege

### What the evidence actually establishes

Some issuer tenders give priority to eligible owners of fewer than 100 shares.
A [2024 Coca-Cola Consolidated offer](https://www.sec.gov/Archives/edgar/data/317540/000119312524142266/d842206dex99a1a.htm)
requires aggregate ownership below 100, tendering all shares and satisfying the
offer's conditions. Splitting a larger beneficial holding across accounts does
not create eligibility. A Dutch auction also leaves the final purchase price
uncertain; its advertised upper endpoint is not a guaranteed payout.

This is strong evidence for that offer's mechanics, not for positive expected
returns across future tenders. The review did not find a complete historical,
survivor-free retail return series establishing this strategy's profitability.
The position cap explains why a large fund cannot scale the same privilege.

### Economics and execution

Evaluate each event from its actual documents and broker processing requirements.
Build a distribution of accepted quantity, payment, failed-offer resale proceeds,
capital-days and fees. Include amendments, delays, settlement, broker cutoff,
withdrawals and unfavorable events. Thin historical documentation or missing
failures should lower confidence rather than silently remove cases.

Even a high percentage return on a small position may produce little annual
income. As an illustration only, a hypothetical 99-share trade with a $1 net
profit per share earns $99. Ten such completed trades earn $990 before any costs
not included in the hypothetical $1. More capital cannot enlarge the per-owner
eligibility cap. Event frequency and time spent checking terms matter as much as
the apparent spread.

### First experiment and stopping rule

Enumerate issuer Schedule TO filings and amendments for a fixed historical
window, including cancelled and expired offers. Hand-check a sample before
automating extraction of eligibility, price mechanism, deadlines and conditions.
Use executable entry prices after public announcement and actual final outcomes.

Track net dollars, dollar loss, capital-days, annual opportunity count and operator
hours. Stop the build if complete terms/outcomes cannot be reconstructed, apparent
profits rely on the maximum auction price, or annual net dollars do not justify
the work. NLP can save reading time; a rules baseline should decide eligibility.
This is a supplemental event tool, not a reason to begin a large forecasting zoo.

## 5. Crypto cash-and-carry: conditional and operational

### Evidence and mechanism

[Schmeling, Schrimpf and Todorov's BIS research](https://www.bis.org/publications/working-paper-1087-crypto-carry)
documents large, variable crypto futures basis and links it to leveraged demand
and constrained arbitrage capital. It explicitly identifies margin spikes and
liquidations as risks. Its historical gross carry figures are not current
executable yields or returns on the owner's total committed capital.

In principle, buying spot while selling an appropriately matched fixed-maturity
future can monetize a positive basis. Cash settlement requires matching the spot
exit to the relevant settlement exposure; it does not automatically exchange the
spot holding for the future's quoted sale price. Perpetual funding is a separate,
variable cash flow without fixed-maturity convergence.

**Verdict:** documented premium, uncertain net benefit for this account, high
operational burden. Small size may ease entry but does not remove collateral,
custody, settlement or access constraints. The primary research problem is cash
and risk accounting; ML is optional.

### First experiment and stopping rule

After confirming lawful account access, record synchronized spot/futures bid and
ask, available size, contract terms, settlement basis, margin and fees. Simulate
both legs, cash funding, variation margin, spot exit and liquidation. Compare net
dollars with the cash benchmark on **all committed cash and liquidity reserves**.
Treat an ETF-based spot proxy as a distinct construction with tracking, hours and
expense differences.

Stress adverse legging, wider basis, higher margin and delayed transfers. A market
neutral terminal payoff does not prevent an interim cash shortfall. Stop if the
executable spread does not clear all costs and a predeclared risk hurdle, if
liquidation occurs under plausible stress, or if venue access is unavailable.
Only later ask whether simple models of stress or spread persistence add value.
Start with fixed maturity; do not import a perpetual funding backtest as evidence
for this strategy.

## Reserve and lower-priority opportunities

### Closed-end funds with explicit catalysts

A listed closed-end fund can trade below its underlying asset value without
providing a shareholder redemption route. Study a scheduled termination, tender,
open-ending proposal or other specific mechanism that could close the discount.

[Bradley, Brav, Goldstein and Jiang](https://finance.wharton.upenn.edu/~itayg/Files/cefactivism-published.pdf)
find discount changes around activist attempts in historical US equity CEFs.
Their sample is old and does not represent every CEF category or a current
post-announcement retail trading rule. A
[BlackRock final tender-results filing](https://www.sec.gov/Archives/edgar/data/1320375/000119312524262813/d815856dex99a5iii.htm)
illustrates another difficulty: oversubscribed tenders can accept only a fraction
of submitted shares. Model that fraction and the market value of the remainder.

This is a reasonable bounded extension of the SEC-event project. Required data
includes dated NAV, distributions, leverage, dead/merged funds, event terms and
final acceptance. Compare catalyst-aware selection with discount-only and
category-matched portfolios. Reject a result that assumes full acceptance or
uses the eventual activist outcome to select the historical sample.

### Options, broad carry and other special situations

- **Options/volatility selling:** distinguish insurance compensation from alpha.
  A [2025 RFS study of option anomalies](https://academic.oup.com/rfs/article/38/6/1783/8010873)
  finds common risk and transaction costs sharply reduce the apparent opportunity.
  This review does not establish a retail edge in generic option anomaly mining.
  Expensive historical quotes, multi-leg fills and tail accounting make it a weak
  first project. High win rate is not an adequate objective.
- **Broad futures carry:** plausible economic premia merit study, but a small
  account may have poor diversification after contract sizing and margin. This
  review does not establish the net performance of an accessible retail subset.
- **Spinoffs and forced selling:** retain as hypotheses for a bounded event audit.
  Selection, takeover outcomes, dead securities and long-horizon benchmarks can
  dominate apparent returns; do not assume every unwanted distribution rebounds.
- **Index changes, generic earnings drift and insider copying:** deprioritize
  until a recent, timestamp-correct replication survives costs. A predictable
  public event is not by itself a profitable public trading opportunity.
- **High-frequency market making, generic 0DTE selling and cross-venue crypto
  arbitrage:** the present review supplies no evidence of an owner-specific
  advantage. Quote competition, adverse selection, tail losses or operational
  frictions belong in the economic test before model development.

These are research-budget decisions, not claims that every implementation fails.

## Shared validation design

### Freeze what is being tested

Write the mechanism, eligible universe, entry time, features, model family,
benchmark, cost assumptions and primary outcome before measuring returns.
Record every tried variant, including abandoned ideas. Prefer the smallest test
that could contradict the thesis. An economic hurdle and risk tolerance should
be chosen by the owner before selecting winners.

Fit preprocessing and model selection on past data only. Use genuinely nested
tuning where required: an outer validation window reused by a search is no longer
an unbiased evaluation. Preserve the final lockbox and reject silent changes to
universe, timestamp or cost convention. Existing child holdouts are not a free
test set for unrelated new strategies.

### Measure investable economics

Keep transaction prices and cash flows, not just normalized returns. Account for
commissions, spread, slippage, delay, partial fills, borrow/financing when used,
rolls, collateral opportunity cost and rejected orders. Separate pre-tax from
account-specific after-tax analysis. Do not use a generic tax haircut as a
substitute for identifying turnover, realized gains and applicable rules.

Report excess return, drawdown, tail loss, turnover, capital usage and uncertainty.
Use common units when comparing strategies. Tender dollars per event and futures
returns on posted margin cannot be ranked directly against a fully funded
portfolio's annual return. Mark open positions and unsuccessful events as well
as winners.

### Dependence, selection and prospective evidence

Resample at the level of genuinely independent information: time blocks for
portfolio returns and appropriate event/issuer/date clusters for corporate
events. Correlated securities and overlapping return horizons do not create
independent samples. Account for the family of tried strategies and avoid pooling
unrelated mechanisms merely to increase nominal sample size.

Use a prospective frozen paper period to expose ingestion, timing and execution
mistakes. **Ninety days is a feasibility/falsification phase, not proof of a durable
monthly or quarterly edge.** Sparse event strategies and slow portfolios may need
years of suitable historical evidence and much longer prospective observation.
Measure expected interval width and minimum detectable effect; an inconclusive
result is not a deployment pass. Any live experiment is a separate owner decision.

## A staged 90-day research agenda

### Days 1–7: define the experiment and inspect data

Choose one primary research lane; my default is slow trend. Define the investable
universe, accessible benchmark, maximum research budget, cost cases and rejection
criteria. Inventory existing data licenses rather than assuming intraday OHLCV
contains accounting histories, historical event terms or futures cash flows.

Create a small, immutable data sample and hand-check identifiers, dates,
distributions and missing periods. Establish what the owner can access through
their actual broker. In parallel, a small manual census of odd-lot offers can
estimate whether there are enough events to justify a scanner.

### Days 8–30: establish simple baselines

Run the predeclared trend pilot with passive/exposure controls. If the equity
historical universe is defensible, build a simple quality/momentum composite as
the second lane; otherwise stop at the data-availability finding. Produce a
cash-flow and costs report before any HPO. Choose one small SEC form/event corpus
only if an information-extraction question remains compelling.

Deliverable: data audit, baseline specification, tried-variant registry, net-cost
sensitivity and a go/no-go **for further research**. None of these is a trading
approval.

### Days 31–60: challenge and replicate

Vary execution delay and plausible costs; remove the strongest period or asset
class; compare with simpler factor/risk controls. Audit failed events and
delisted names. Have an independent reader reconstruct the claim from raw input
timestamps and cash flows.

Only a surviving baseline earns an ML comparison. Use the same dates, universe,
capital budget and cost model. Record whether improvement comes from prediction,
lower turnover or changed risk. If a CEF/event lane is preferred, explicitly
model proration and remaining holdings. Crypto remains a recorder/accounting
feasibility exercise until access and stress liquidity are established.

### Days 61–90: freeze and observe

Freeze the surviving specification and begin prospective paper observation.
Compare intended versus executable decisions and attribute discrepancies.
Separate an operational pass from an economic claim; extend the observation
period when statistical power is insufficient.

Deliverable: a short decision memo for each lane—stop, acquire a specific missing
dataset, continue frozen observation, or propose a bounded next experiment. Do
not launch several large model searches because the first month is inconclusive.

## Capital, time and hardware constraints

For an illustrative **$10,000** research account, consider fully funded ETF or
liquid-equity baselines first. An odd-lot opportunity may require more capital
than this when the share price is high. Derivatives granularity and reserve cash
can dominate the trade even when a broker permits the minimum order.

At **$50,000**, diversification and simultaneous events become easier, but a small
percentage edge may still mean little income. Hypothetically, 2% incremental
annual return is $1,000 before any uncounted costs; five hours a week is about 260
hours a year. These are arithmetic illustrations, not an expected return estimate.

At **$250,000**, data and operational fixed costs can be spread over more capital.
Risk and access constraints remain; odd-lot eligibility still caps each event's
position. A bigger account does not turn a weak statistical claim into a strong
one.

Daily ETF panels, monthly equity baselines, document extraction and modest tree
models are plausible M4/32 GB projects. Estimate peak memory on a sample first;
use columnar partitions, streamed text and cached features. The desktop can run
larger historical assembly or repeated fits in WSL2. Build a native Apple Silicon
environment for Mac execution; copying a Linux virtual environment is not a
portable installation. Resource feasibility here is an engineering estimate, not
a benchmark result.

The opportunity-cost benchmark should also include ordinary portfolio costs,
cash management and account-appropriate tax planning. Those can improve personal
outcomes without discovering a trading signal. This review has not inspected
the owner's finances and makes no claim about which such changes apply.

## What dskit can reuse

The inspected documentation establishes the following seams; their suitability
for a new domain still needs focused tests:

- [Onboarding](../dskit/onboarding/README.md): immutable acquisition, validation,
  publication and effective/acquired timestamps. Suitable foundations for quotes,
  filings and terms available at a decision time.
- [Assets](../dskit/assets/README.md): content-addressed identities, lineage and
  configurable storage. Pin datasets and outputs used by every claim.
- [Pipeline](../dskit/pipeline/README.md): declarative runs, fitted-transform
  boundaries, time/event splits, walk-forward execution, retained predictions,
  clustered comparisons and multiple-attempt machinery. Check each class's
  parameter contract before reusing it; the registry is not the full inventory.
- [Production](../dskit/production/README.md): documented paper/shadow and replay
  infrastructure. Its existence is not certification of a new strategy's fills,
  venue access or safety. Domain accounting must match actual contracts.

Potential new work is mainly dated security/event data, precise cash-flow
accounting and domain adapters. A missing generic capability would belong in
dskit under its architecture rules, but this report proposes no package or ADR.
There is no justification here for building another general ML framework.

## Evidence ledger and interpretation limits

The entries below preserve the claim each source supports. Revisions and
institutional hosts of one paper are not counted as independent replications.

1. **Trend portfolio evidence:** [Moskowitz, Ooi and Pedersen, 2012](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf).
   Original multi-asset futures study; historical strategy evidence, not retail
   ETF net returns. Two authors were affiliated with AQR.
2. **Trend counterevidence:** [Huang et al., 2020](https://ink.library.smu.edu.sg/lkcsb_research/6521/).
   Asset-level tests and historical-mean comparator challenge the predictability
   interpretation. University record exposes the paper's abstract and citation.
3. **Quality:** [Asness, Frazzini and Pedersen, published 2018/2019](https://doi.org/10.1007/s11142-018-9470-2).
   International quality evidence; long/short construction and affiliated asset
   management research. Not direct evidence for the proposed long-only blend.
4. **Trading-cost mitigation:** [Novy-Marx and Velikov, author manuscript, August 2015](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf).
   Modeled anomaly costs and entry/hold buffers; published in RFS in 2016.
   Historical cost models are not the owner's measured execution costs.
5. **Anomaly selection and decay:** [Chen and Velikov, 2020](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf).
   Combined cost, post-publication and selection adjustments challenge expected
   net returns. Does not prove every alternative implementation fails.
6. **Reproducibility:** [Chen and Zimmermann, 2021](https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm).
   Open source asset-pricing reproduction. Published predictor portfolios are
   benchmarks, not a complete executable security database.
7. **Disclosure measurement:** [Frankel, Jennings and Lee, online 2021; volume 2022](https://pubsonline.informs.org/doi/abs/10.1287/mnsc.2021.4156).
   ML explains contemporaneous disclosure-date returns; no automatic implication
   of tradable post-disclosure returns.
8. **Public data access:** [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
   Official submissions/XBRL interface. Data access is established; complete
   event universes, timing alignment and licensed price data still require work.
9. **Odd-lot mechanics:** [Coca-Cola Consolidated Schedule TO exhibit, 2024](https://www.sec.gov/Archives/edgar/data/317540/000119312524142266/d842206dex99a1a.htm).
   An actual offer's conditional priority and auction mechanism; not a current
   opportunity recommendation or a historical return sample.
10. **Crypto carry:** [Schmeling, Schrimpf and Todorov, BIS Working Paper 1087, 2023](https://www.bis.org/publications/working-paper-1087-crypto-carry).
    Historical basis and constraints on arbitrage, including margin/liquidation.
    A revision of this paper is the same research program, not another replicate.
11. **CEF activism:** [Bradley, Brav, Goldstein and Jiang, 2010](https://finance.wharton.upenn.edu/~itayg/Files/cefactivism-published.pdf).
    Historical US equity CEFs traded in 1988–2002, with activist events through
    2003. Not representative evidence for bond/municipal CEFs or current fills.
12. **CEF acceptance risk:** [BlackRock final tender results, 2024](https://www.sec.gov/Archives/edgar/data/1320375/000119312524262813/d815856dex99a5iii.htm).
    Actual oversubscription/proration outcomes; arithmetic discounts overstate
    capture if the remaining position is ignored.
13. **Option-anomaly counterevidence:** [Goyal and Saretto, Can Equity Option Returns Be Explained by a Factor Model? IPCA Says Yes](https://doi.org/10.1093/rfs/hhae087).
    Online December 2024; RFS 38(6), June 2025, pages 1783–1821.
    Common-risk and transaction-cost analysis challenges apparent option alpha.
    Findings depend on the tested strategies and risk model, not every option use.

Some publisher pages restrict full text or intermittently fail retrieval. This
report uses accessible manuscripts, official abstracts and source documents for
the corresponding claims; it does not claim independent code replication.
Exact current fee schedules, tax rules, margin figures and account eligibility
are deliberately unresolved implementation inputs, not implied by these links.

## Research provenance and delivery scope

The owner requested a separate deep-research task, parallel research agents,
and then this report in the repository with a push. The initial task was
`01a0c6fb-7dfa-7283-8a12-b3fbe7fd3c6c`. Its three specialist research tasks were:

- Slower signals: `01a0c6fb-d30b-7e02-9b58-59405b163799`.
- Structural/small-capacity opportunities: `01a0c6fb-f285-7602-9f56-1d638ca73250`.
- Derivatives/alternative markets: `01a0c6fc-0e27-7912-a321-f5b999e4f724`.

The first two supplied additional skeptical ranking/interpretation passes.
Their completed findings were retrieved and synthesized in the originating task;
the original coordinator had not produced a final report. A fresh evidence
review challenged the final synthesis. Material distinctions retained include
ETF versus futures evidence, long-only versus long/short factors, contemporaneous
text explanation versus prediction, odd-lot ownership/auction conditions, and
carry measured against all committed cash.

This is a cross-project report under root `docs/`, like the existing quantitative
modeling guide. It is not a research action of either active child; no child
journal, owner Path, holdout, run or production approval is changed. The bounded
delivery is this document plus a re-entry pointer, checked for accuracy, local
links and whitespace. No code tests or trading experiments are claimed.

### Review and verification record

Independent reviewer `/root/retail_evidence_check` in originating task
`01a0c6f2-8272-7850-948d-f646c4ade478` reviewed candidate `12a0369` in full.
The final verdict was a bounded accuracy pass, with no consequential misleading
claims, incorrect source scopes or demonstrably broken citation destinations
found. The requested Goyal/Saretto citation-title correction was applied; the
remaining post-review changes are this record and the re-entry evidence note.

The review did not replicate backtests, establish account eligibility or audit
the repository machinery. QMJ's DOI intermittently failed retrieval; the reviewer
corroborated its destination and claim through publisher results and the authors'
AQR page. The coordinator checked every report-local file link and the new
re-entry link, inspected the named repository documentation at the recorded base,
and ran `git diff --check`. This records a documentation accuracy check, not an
economic validation or production approval.
