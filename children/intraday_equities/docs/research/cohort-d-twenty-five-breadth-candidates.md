# Cohort D: twenty-five breadth candidates

## Finding

Twenty-five additions are frozen here, before any of them is pulled or scored: **ORCL, GLD, MRK, CRM, NEM, XBI, FCX, FTNT, DIS, DAL, PYPL, NRG, GM, MDT, BA, SBUX, TGT, ADM, MET, TMUS** are ranks 1-20 and the cohort proper; **PLD, PM, NKE, LVS, DHI** are ranks 21-25, documented spares for any name that fails at pull time. None of the 25 duplicates the 25 names already in the study, and META stays excluded.

The point of this cohort is breadth, and the headline result is that **breadth and the liquidity rule of thumb pull against each other**. Applying the project rule — the typical three-minute move over the half-spread must be roughly 25 to 45 to clear the spread at 0.3% skill — only **eleven of the twenty-five clear the band** (ORCL 95, GLD 92, MRK 75, CRM 69, NEM 56, XBI 37, FCX 35, FTNT 35, DIS 31, DAL 29, PYPL 29); NRG at 24 sits on its edge; the other thirteen land between 11 and 23. That is not a defect of the selection. It is the rule being **sector-selective by construction**: the one-cent minimum quoting increment is a *fixed* cost in basis points, worth `50/P` bp of half-spread, so the ratio is driven by share price and volatility rather than by anything about the business. Defensive, low-volatility, low-priced sectors — staples, tobacco, telecom, rails, REITs, machinery — cannot clear it whatever their microstructure. The roster already contains the same contradiction: LLY sits at 8 and BAC near 13 to 15 by this arithmetic, and both were admitted.

That tension is itself the cohort's question. If modelability is a property of the *sector or mechanism* rather than of the name, then a set spanning 22 industries at ratios from 11 to 95 is the instrument that can separate the two, and the low-ratio names are informative precisely because they are expensive to trade.

Two facts make the window unusually clean. **Exactly one stock split falls inside 2016-01-01 to 2026-02-28 across all twenty-five names** — Fortinet's 5-for-1 on 2022-06-23 — and it is removed by `adjustment=split`. And the SEC's half-penny amendment to Rule 612, which would let tick-constrained stocks quote in half-cents, had its compliance date moved from November 2025 to **November 2026**, after the hard cut. The one-cent increment therefore holds for the entire study window, and the `50/P` bp half-spread floor is hard rather than approximate.

## Frozen selection rule

Selection used **only non-backtest facts**: US listing history, continuous ticker, Alpaca SIP coverage, share price, average volume, realised volatility, quoted-spread structure, corporate actions, and market mechanism. No validation return, gate score, walk-forward result, IC, or trading outcome was consulted, inferred, or reasoned from. The candidates are frozen before the modelability gates see them; that is the whole basis on which this cohort can be read as evidence.

**Rank order.** Names are ranked by the estimated three-minute-move over half-spread ratio, descending — the project's own eligibility rule, and the only quantity here that varies enough to order twenty-five names. **Tie-break, in order:** (1) sector novelty, a sector the roster has zero coverage of outranking one it covers thinly; (2) fewer corporate actions inside the window, a clean name beating one carrying a split or a spin-off; (3) longer continuous ticker history; (4) higher average dollar volume. Breadth is enforced as a *constraint on the set*, not on the order: **no more than two names from any one industry**, which is what produced 22 distinct industries across 25 names, and is why two higher-ratio candidates — GDX at 53, which would have been a third Metals and Mining name, and NUE — were displaced by the cap rather than out-ranked.

**How the ratio was estimated.** Three-minute move (bp) = annualised volatility (%) × 0.5527, from a 390-minute session. Half-spread (bp) = the one-cent tick floor, `50/P`, for names liquid enough to quote a single cent, widened by hand to two, three or more ticks for thin or high-priced names. The model was calibrated against the study's own five measured names: it reproduces AAPL near 0.3 bp, JPM and XOM in the 0.2 to 0.5 bp range, and LLY in the 1.5 to 3 bp range recorded in `p4-price-definition-bounce-diagnostic-and-the-close-vwap-mid-comparison.md`. **Every spread figure is an estimate.** Volatilities are a 2026-09-04 snapshot and several are plainly elevated by regime (ORCL, CRM, MRK, NEM); re-running the arithmetic at long-run volatilities lowers the top ratios but does not move any of the eleven below the band.

Ranked rationale — picks 1-20:

1. **ORCL** — the widest ratio in the set; a database and cloud capex leader whose flow is now an AI-narrative channel independent of the semis block.
2. **GLD** — a non-equity state variable the study has none of; bullion is priced 23 hours a day in futures and in London, so the ETF reopens against an already-moved reference.
3. **MRK** — large-cap pharma driven by patent-cliff and regulatory event risk, a different clock from LLY's.
4. **CRM** — enterprise SaaS; the software follower to ORCL and MSFT, and the cleanest in-sector lead-lag pair the cohort adds.
5. **NEM** — gold producer; operating leverage on bullion makes an explicit metal-to-miner lead-lag testable inside one cohort.
6. **XBI** — an equal-weighted biotech basket whose moves are idiosyncratic binary clinical and FDA events, unlike every cap-weighted ETF on the roster.
7. **FCX** — copper; COMEX and LME pass-through plus China demand headlines, a commodity channel distinct from gold.
8. **FTNT** — cybersecurity; an enterprise-budget cycle, and the one name in the cohort carrying an in-window split.
9. **DIS** — parks, sports rights and linear television; a media mechanism unlike NFLX's pure-subscription one.
10. **DAL** — jet fuel is roughly a quarter of operating cost, giving a signed crude pass-through and a negative lead-lag against XLE and XOM.
11. **PYPL** — payments and consumer spend, high retail ownership, no split ever and no corporate action inside the window.
12. **NRG** — independent power producer; wholesale power prices, weather, and data-centre load growth, a mechanism absent from every roster name.
13. **GM** — traditional autos; tariff and steel input pass-through plus consumer-credit sensitivity.
14. **MDT** — medical devices; procedure volume and hospital capex, with low macro beta.
15. **BA** — aerospace and defence; a duopoly order book where single headlines dominate, and the least index-like large cap in the set.
16. **SBUX** — restaurants; store traffic and China demand, a consumer-discretionary clock separate from WMT's staples one.
17. **TGT** — broadline discretionary retail; monthly retail-sales prints and an inventory cycle that WMT's staples mix mutes.
18. **ADM** — agribusiness; crush margins and grain prices, the only agricultural commodity pass-through available at this liquidity.
19. **MET** — life insurance; long-duration liabilities make it the purest rate-*level* equity, distinct from the credit and net-interest-margin channel in JPM and BAC.
20. **TMUS** — telecom; subscriber and spectrum economics with the lowest beta in the cohort at 0.33, a near-orthogonal factor.

Documented spares 21-25, in order of substitution:

21. **PLD** — logistics REIT; rate duration plus e-commerce warehouse demand. The best real-estate ratio available, against AMT's 7.
22. **PM** — tobacco; the highest-ratio staples-like name, carried to represent a sector the arithmetic says cannot clear the spread.
23. **NKE** — apparel and footwear; a global brand with FX and China exposure, now priced low enough that the tick eats the ratio.
24. **LVS** — casinos; Macau gross-gaming-revenue prints and Chinese policy headlines, an overnight-Asia channel nothing else carries.
25. **DHI** — homebuilder; the ten-year yield transmits to it through mortgage rates, and the 08:30 ET housing releases are scored instants.

## Verification table

Prices, share volumes and volatilities are as of 2026-09-04. Move and half-spread are estimates; see the method note above.

| Ticker | Industry | 3-min move (bp) | Half-spread (bp) | Ratio | Splits in window | Mechanism | Same ticker since before 2016-01-04? |
|---|---|---|---|---|---|---|---|
| ORCL | Software (infrastructure) | 30.4 | 0.32 | 95 | none (last 2000-10-13) | AI and cloud capex information leader | Yes; Nasdaq to NYSE 2013-07-15, ticker unchanged |
| GLD | Commodity ETF (gold bullion) | 13.8 | 0.15 | 92 | none (never split) | Overnight futures and London gold reprice | Yes; ETF inception 2004-11-18 |
| MRK | Pharmaceuticals | 24.9 | 0.33 | 75 | none | Patent-cliff and regulatory events | Yes; Organon spin ex-2021-06-03 (not a split) |
| CRM | Software (application) | 33.2 | 0.48 | 69 | none (last 2013-04-18) | Enterprise SaaS follower to ORCL and MSFT | Yes; NYSE since 2004 |
| NEM | Metals and mining (gold) | 25.4 | 0.45 | 56 | none (last 1994) | Miner operating leverage on bullion | Yes; renames only, no ticker change |
| XBI | Biotechnology ETF | 16.6 | 0.45 | 37 | none | Idiosyncratic binary clinical and FDA events | Yes; inception 2006-01-31; 3:1 ex-2015-09-11 pre-window |
| FCX | Metals and mining (copper) | 24.3 | 0.69 | 35 | none (last 2011-02-02) | COMEX and LME copper, China demand | Yes |
| FTNT | Cybersecurity | 27.6 | 0.80 | 35 | **5-for-1, ex 2022-06-23** | Enterprise security budget cycle | Yes; Nasdaq IPO 2009-11-17 |
| DIS | Media and entertainment | 14.9 | 0.48 | 31 | none (last 2007) | Parks and sports-rights consumer cycle | Yes; 2019 holdco reorg changed the CIK, not the ticker |
| DAL | Airlines and transportation | 17.7 | 0.62 | 29 | none | Signed jet-fuel and crude pass-through | Yes; relisted 2007-05-03 after Chapter 11 |
| PYPL | Payments and fintech | 26.5 | 0.91 | 29 | none (never split) | Consumer spend, high retail ownership | Yes, but only from 2015-07-20 |
| NRG | Utilities and independent power | 25.4 | 1.05 | 24 | none (last 2007-06-01) | Wholesale power, weather, data-centre load | Yes; relisted 2004-03-25 after Chapter 11 |
| GM | Automobiles | 13.3 | 0.57 | 23 | none | Tariff and steel input pass-through | Yes from 2010-11-18; do not splice pre-2010 GM |
| MDT | Medical devices | 11.6 | 0.53 | 22 | none (last 1999-09-27) | Procedure volume and hospital capex | Yes; Covidien inversion closed 2015-01-26 |
| BA | Aerospace and defence | 13.8 | 0.71 | 19 | none (last 1997-06-09) | Duopoly order book, headline-dominated | Yes |
| SBUX | Restaurants | 11.6 | 0.60 | 19 | none (2:1 ex-2015-04-09, pre-window) | Store traffic and China demand | Yes; Nasdaq since 1992 |
| TGT | Broadline retail | 17.1 | 0.90 | 19 | none (last 2000-07-20) | Retail-sales prints, inventory cycle | Yes; the DH to TGT rename was January 2000 |
| ADM | Agribusiness | 14.4 | 0.75 | 19 | none | Crush margin and grain price pass-through | Yes |
| MET | Insurance (life) | 12.7 | 0.75 | 17 | none | Long-duration liability, rate *level* | Yes; Brighthouse spin ex-2017-08-07 (not a split) |
| TMUS | Telecom | 17.1 | 1.10 | 16 | none | Subscriber and spectrum, beta 0.33 | Yes from 2013-05-01; NYSE to Nasdaq 2015-10-27 |
| PLD | REIT (logistics) | 13.8 | 0.90 | 16 | none (never split) | Rate duration plus warehouse demand | Yes from 2011-06-03 (AMB renamed Prologis) |
| PM | Tobacco | 12.2 | 0.85 | 14 | none (never split) | Defensive, FX-heavy, pricing power | Yes; the 2008 Altria spin is pre-window |
| NKE | Apparel and footwear | 16.6 | 1.30 | 13 | none (2:1 ex-2015-12-24, 7 sessions pre-window) | Global brand, FX and China demand | Yes; NKE is the Class B listing throughout |
| LVS | Casinos and gaming | 14.9 | 1.13 | 13 | none (never split) | Macau GGR prints, China policy headlines | Yes |
| DHI | Homebuilders | 19.3 | 1.75 | 11 | none (last 2005-03-17) | Mortgage-rate duration, 08:30 housing data | Yes |

All twenty-five are US-listed common stock or ETFs and are covered by Alpaca's SIP feed, which consolidates CTA and UTP for 100% of US stock and ETF volume, so none needs a different connector. Both ETFs pre-date the window comfortably: GLD from 2004-11-18 and XBI from 2006-01-31.

**Corporate actions that are not splits but will still move an adjusted price series.** MRK's Organon distribution (ex-2021-06-03) and MET's Brighthouse distribution (ex-2017-08-07) are spin-offs, not splits: some vendors encode them as 1.048× and 1.122× adjustment factors and some do not, so both series must be reconciled against whatever Alpaca returns. DIS's 2019 holding-company reorganisation changed the SEC CIK from 1001039 to 1744489 while ticker and CUSIP carried through, which matters only if something joins on CIK. GM's pre-2010 history belongs to a legally different, bankrupt issuer and must not be spliced.

## Rejected on the gates

Recorded here so the exclusions are on file and are not re-proposed.

**Ticker discontinuity inside the window.** CB — ACE Limited took the Chubb name and the CB symbol on 2016-01-15, so bars before that date under CB belong to a different company. DD — DuPont became DWDP on 2017-09-01 and returned as DD on 2019-06-03. BKNG — PCLN was renamed on 2018-02-27. GAP — GPS became GAP on 2024-08-22. XYZ — SQ was renamed on 2025-01-21. AA — the current Alcoa Corporation first traded on 2016-11-01, the prior holder of the ticker having become ARNC.

**Window truncation.** X — U.S. Steel ceased trading on the NYSE on 2025-06-19 when Nippon Steel closed, ending the series nine months early.

**Reverse splits.** USO — a one-for-eight reverse share split effective 2020-04-28, precisely the distress signal this gate is meant to catch; it removes the only liquid continuous crude instrument. GE — a one-for-eight reverse split on 2021-07-30 that is *not* a liquidity signal, but which comes with the GE HealthCare (2023) and GE Vernova (2024) distributions that `adjustment=split` does not remove. UNG, OIH and the volatility complex fail the same way; VXX's note was replaced in 2019, and UVXY and SVXY had their leverage changed on 2018-02-28.

**Failed the liquidity rule and were not worth a slot.** TLT at 9 — the rate factor is the largest single gap in the study and there is no way to fill it: TLT's 30-day realised volatility is 10.1%, so a three-minute move of 5.6 bp faces a 0.61 bp half-spread, and the leveraged alternatives (TMF, TBT) all carry reverse splits. Also AMT 7, RCL 7, UNP 8, ICE 9, CAT 9, ETN 10, NUE 10, V 13, MCD 14. Machinery and electrical equipment (CAT, ETN) and rails (UNP) fail for one structural reason: high price, thin share volume, modest volatility.

**No candidate exists.** Shipping — the most liquid continuous US name, MATX, trades roughly 300k shares a day, an order of magnitude below the bar. Chemicals — the best available, ALB, scores about 21 and LYB about 17.

**First reserves beyond the twenty-five.** GDX (53) if a Metals and Mining name fails; BSX (19) if MDT fails; ALB (21) and NUE (10) for materials.

## Caveat

These are candidates, not winners, and nothing here predicts a gate outcome. The ratio is a *cost* screen, not a signal screen: a name that clears it is merely affordable to trade, and liquidity plus plausible structure make an efficient experiment, not a predictable asset. Every spread number is a judgement estimate anchored on five measured names, and the volatilities are a single-day snapshot taken in a regime where several of these names — ORCL, CRM, MRK, NEM — are running well above their long-run levels; the ranking would reshuffle within tiers under different volatility inputs, though the eleven above the band stay above it. PYPL carries only five and a half months of pre-2016 history, the tightest continuity in the set. Minute-scale effects are small, bar data omits most order-flow information, and thirteen of these names cannot clear their own spread at 0.3% skill — which is the finding, not an oversight. Apply the same preregistered horizons, the same multiplicity correction, and the same shuffle and retrain gate to every one of them.

## Acquisition evidence

Ranks 1-20 were pulled; ranks 21-25 were not. The journaled `alpaca-sip-split-d`
backfill used SIP, split adjustment, one-minute bars, gzip, start `2016-01-01`,
and exclusive end `2026-02-28T23:59:59+00:00`. Source-config hash:
`1c5c9d9b22dabac130288f78ce0a02e6a5754eb3d8efdabef46c3280a54f5f36`; snapshot:
`d1e5c3b10b0144ad83fcebd79db18d38d62d8928af249265779bbad03433c4fd`.

The immutable snapshot contains **22,425,594** bars, zero skipped records, all
twenty expected symbols, no unexpected symbols, and zero timestamps at or beyond
the cut. Source-scoped hash verification checked one snapshot with zero problems.
The pull took 25m28s of wall clock at a peak resident set of 822 MB.

Counts were ORCL 1,131,810; GLD 1,365,299; MRK 1,068,427; CRM 1,116,136;
NEM 1,118,108; XBI 1,103,603; FCX 1,197,099; FTNT 1,011,425; DIS 1,207,028;
DAL 1,201,795; PYPL 1,235,715; NRG 999,911; GM 1,154,615; MDT 1,016,418;
BA 1,274,266; SBUX 1,109,417; TGT 1,073,124; ADM 1,006,635; MET 1,008,887;
TMUS 1,025,876.

Every symbol has usable 2016 observations, between 97,537 (FTNT) and 129,410
(FCX) bars in that year. That includes **PYPL**, the tightest continuity case in
the set at 102,923 bars in 2016, which settles the one eligibility question the
selection could not answer from listing records alone. The three names whose
history does not reach 2010 - GM, TMUS and PYPL - all open on the first session
of the window, so no name in the cohort starts late.

**Not yet done.** The MRK and MET spin-off price factors are still unreconciled:
whether Alpaca's `adjustment=split` tape encodes the Organon and Brighthouse
distributions as 1.048x and 1.122x factors, or leaves them as the price falls
they were, must be checked against the unadjusted `alpaca-sip` tree before either
name is modelled. The cohort is pulled, not gated: no candidate here has been
through Gate 1-3.

## Sources

- [Alpaca historical stock data: SIP consolidates CTA and UTP for all US stocks and ETFs](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
- [Alpaca market data FAQ: symbol-rename and asof mapping](https://docs.alpaca.markets/us/docs/market-data-faq)
- [SEC adopts the Rule 612 minimum-pricing-increment amendments; half-penny compliance deferred past the study cut](https://www.sidley.com/en/insights/newsupdates/2024/10/sec-adopts-rules-modifying-minimum-pricing-increments-access-fee-caps-and-order-transparency)
- [Fortinet announces its five-for-one stock split, effective 2022-06-23](https://www.fortinet.com/corporate/about-us/newsroom/press-releases/2022/fortinet-announces-five-for-one-stock-split)
- [PayPal completes its separation from eBay; regular-way PYPL trading from 2015-07-20](https://newsroom.paypal-corp.com/2015-07-20-PayPal-Celebrates-Listing-on-Nasdaq-and-Completes-Separation-from-eBay-Inc)
- [NIKE two-for-one split, ex-date 2015-12-24, just before the window](https://investors.nike.com/investors/news-events-and-reports/investor-news/investor-news-details/2015/NIKE-Inc-Announces-New-12-Billion-Share-Repurchase-Program-14-Percent-Increase-in-Quarterly-Dividend-and-Two-for-One-Stock-Split/default.aspx)
- [Starbucks two-for-one split, ex-date 2015-04-09](https://investor.starbucks.com/news/financial-releases/news-details/2015/Starbucks-Announces-2-for-1-Stock-Split-its-Sixth-Split-Since-Initial-Public-Offering/default.aspx)
- [Merck declares the record date for the Organon spin-off](https://www.merck.com/news/merck-declares-record-date-and-dividend-for-the-organon-co-spinoff/)
- [MetLife 8-K on the Brighthouse Financial distribution](https://www.sec.gov/Archives/edgar/data/0001099219/000119312517249328/d420457dex991.htm)
- [T-Mobile US FY2013 10-K: the MetroPCS reverse split and the TMUS symbol from 2013-05-01](https://www.sec.gov/Archives/edgar/data/0001283699/000128369914000012/tmus12312013form10k.htm)
- [Disney Form 8-K12B for the 2019 holding-company reorganisation](https://www.sec.gov/Archives/edgar/data/1744489/000095015719000301/form8k-12b.htm)
- [Delta exits Chapter 11; DAL relisted in 2007](https://ir.delta.com/news/news-details/2007/Delta-Air-Lines-Exits-Chapter-11-Stronger-and-Better-Positioned-for-New-Era-of-Competition/default.aspx)
- [Prologis and AMB Property merger of equals; PLD on the NYSE from 2011-06-03](https://ir.prologis.com/press-releases/detail/530/prologis-and-amb-property-corporation-announce-merger-of)
- [USCF announces the one-for-eight reverse share split for USO](https://www.prnewswire.com/news-releases/uscf-announces-one-for-eight-reverse-share-split-for-the-united-states-oil-fund-nyse-arca-uso-301045001.html)
- [GE completes its one-for-eight reverse stock split, 2021-07-30](https://www.ge.com/news/press-releases/ge-completes-one-for-eight-reverse-stock-split)
- [MIAX corporate action: ACE Limited becomes Chubb Limited under CB, 2016-01-15](https://www.miaxglobal.com/alert/2016/01/14/miax-corporate-action-alert-ace-limited-ace-name-and-symbol-change-chubb)
- [Priceline Group becomes Booking Holdings; BKNG from 2018-02-27](https://ir.bookingholdings.com/news/news-details/2018/The-Priceline-Group-Inc.-NASDAQ-PCLN-Announces-Name-Change-to-Booking-Holdings-Inc.-02-21-2018/default.aspx)
- [Gap Inc. changes its ticker from GPS to GAP, 2024-08-22](https://www.prnewswire.com/news-releases/gap-inc-to-change-ticker-symbol-to-gap-on-august-22-to-report-second-quarter-fiscal-2024-results-on-august-29-302218082.html)
- [Block announces the SQ to XYZ ticker change, effective 2025-01-21](https://investors.block.xyz/investor-news/news-details/2025/Block-Announces-Ticker-Symbol-Change-to-XYZ-To-Report-Fourth-Quarter-Results/default.aspx)
- [Alcoa 8-K on the Arconic separation and the AA and ARNC symbol swap, 2016-11-01](https://www.sec.gov/Archives/edgar/data/0000004281/000119312516731663/d249430d8k.htm)
- [U.S. Steel ceases NYSE trading as Nippon Steel closes, 2025-06-18](https://www.cnbc.com/2025/06/18/us-steel-ceases-trading-on-the-nyse-as-japans-nippon-finalizes-takeover.html)
- [DowDuPont separation: DD returns to the NYSE on 2019-06-03](https://www.sec.gov/Archives/edgar/data/1666700/000119312519163322/d715311dex991.htm)
- [EIA Weekly Petroleum Status Report, released 10:30 ET on Wednesdays](https://www.eia.gov/petroleum/supply/weekly/)
- [Chinco, Clark-Joseph and Ye: one-minute cross-stock lagged-return signals](https://www.nber.org/papers/w23933)
- [Ait-Sahalia et al.: high-frequency predictability and market microstructure](https://www.nber.org/papers/w30366)
- [Ernst: stock-specific price discovery from ETFs](https://www.mit.edu/~ternst/docs/jmp.pdf)
- [Box, Davis, Evans and Lynch: intraday arbitrage between ETFs and their underlying portfolios](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21001537)
- Prices, share volumes and split histories from [stockanalysis.com](https://stockanalysis.com/); realised and implied volatility from [alphaquery.com](https://www.alphaquery.com/); split cross-checks against [stocksplithistory.com](https://www.stocksplithistory.com/)
