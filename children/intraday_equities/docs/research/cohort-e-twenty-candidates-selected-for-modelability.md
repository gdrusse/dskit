# Cohort E: twenty candidates selected for modelability

## Finding

Twenty additions are frozen here, before any of them is pulled or scored: **MSTR, MRVL, NOW, LULU, SHOP, PANW, GDX, BABA, INTC, MU, CIEN, WDC, LRCX, TER, BIDU, LITE, C, ADBE, ANET, EOG**. None of the twenty duplicates the forty-five names already in the study, and META stays excluded — it was removed before cohort C's registration and is not re-litigated here.

Cohort D was selected for breadth and, by design, only eleven of its twenty-five cleared the liquidity rule. This cohort inverts the objective, and the inversion works: **all twenty clear the 25-45 band, and twelve of the twenty clear its upper edge** — MSTR 155, MRVL 138, NOW 96, LULU 84, SHOP 68, PANW 62, GDX 58, BABA 54, INTC 49, MU 49, CIEN 47, WDC 46. The remaining eight sit between 31 and 42. Cohort D's median candidate scored 23; this cohort's scores 48.

The price of that inversion is concentration, and it is not a preference — it is what the rule does in this regime. At the one-cent floor the ratio is `0.011 x vol(%) x price($)`, so a name clears only by being volatile, liquid enough to quote a penny, and not so expensive that the penny stops binding. On 2026-09-04 the names satisfying all three are overwhelmingly the AI hardware and software complex: **fourteen of the twenty are technology**, and the three-per-industry cap binds in three separate industries at once (semiconductors, application software, communication equipment). Everything defensive is excluded by arithmetic, exactly as in cohort D — but where cohort D spanned twenty-two industries at ratios from 11 to 95, cohort E is one macro theme at ratios from 31 to 155. If cohort D asks whether modelability is a property of the *sector*, cohort E asks the sharper question: whether it is a property of a *regime*. A cohort drawn from a single volatility complex is the instrument that can answer that, and it is also the cohort most exposed to that complex mean-reverting.

**Two traps this selection had to catch, and both are new.** First, a split *after* the hard cut makes today's price the wrong scale for the window. KLA (KLAC) scores 68 on its 2026-09-04 price of $185.60, which would have ranked it fifth — but its ten-for-one split had ex-date **2026-06-12**, three and a half months *after* the 2026-02-28 cut. The instrument that actually traded through the entire study window was an approximately $1,856 stock, whose one-cent tick is 0.0027 bp and whose realistic half-spread is nearer 2.4 bp. Rescored on the price it traded at inside the window, KLAC falls to about 11 and is rejected. Second, a continuous *ticker* is not a continuous *listing*. Super Micro (SMCI) scores 35 and has carried the ticker SMCI since 2007, but Nasdaq suspended it on 2018-08-23 and delisted it on 2019-03-22; it traded OTC — outside the SIP, and so outside Alpaca — until relisting on 2020-01-14. That is a seventeen-month hole inside the window and it is disqualifying.

The one-cent increment holds for the whole window. The SEC's half-penny amendment to Rule 612 was granted temporary exemptive relief on 2025-10-31 that moved compliance to the first business day of **November 2026**, eight months past the cut, so the `50/P` floor is hard rather than approximate — as it was for cohort D.

Where cohort D carried exactly one in-window split, this cohort carries **eight, all forward, all removed by `adjustment=split`**: MSTR 10-for-1 (2024-08-08), NOW 5-for-1 (2025-12-18), SHOP 10-for-1 (2022-06-29), PANW 3-for-1 (2022-09-14) and 2-for-1 (2024-12-16), LRCX 10-for-1 (2024-10-03), and ANET 4-for-1 (2021-11-18) and 4-for-1 (2024-12-04). **No reverse split falls inside the window for any of the twenty.** Two names carry pre-window reverse splits that this gate is built to notice, and both are comfortably clear of it: Citigroup's one-for-ten effective 2011-05-06, and Ciena's one-for-seven effective 2006-09-22.

## Frozen selection rule

Selection used **only non-backtest facts**: US listing history, continuous ticker, Alpaca SIP coverage, share price, average share and dollar volume, realised volatility, quoted-spread structure, corporate actions, and market mechanism. No validation return, gate score, walk-forward result, IC, or trading outcome was consulted, inferred, or reasoned from. The candidates are frozen before the modelability gates see them; that is the whole basis on which this cohort can be read as evidence.

**Rank order.** Names are ranked by the estimated three-minute-move over half-spread ratio, descending. **Tie-break, in order:** (1) higher ratio; (2) mechanism novelty, a channel the roster has no coverage of outranking one it already covers; (3) fewer corporate actions inside the window; (4) longer continuous ticker history; (5) higher average dollar volume. Two pairs are inside rounding and were separated on rule (5): SHOP 67.6 against a displaced KLAC, and INTC 49.1 against MU 48.9.

**The cap is a constraint on the set, not on the order:** no more than **three names from any one industry**. It binds three times here and displaces two names that out-rank members of the final twenty — **MCHP at 47** and **QCOM at 28**, the fourth and fifth semiconductors behind MRVL, INTC and MU. Breadth was not otherwise pursued.

**How the ratio was estimated.** Three-minute move (bp) = annualised volatility (%) x 0.5527, from a 390-minute session. Half-spread (bp) = `50 x s / P`, where `s` is the estimated quoted spread in **cents** and `P` the share price — so `s = 1` reproduces the one-cent tick floor `50/P` exactly. `s` is assigned by hand from price and average share volume: a base of 1 cent below $150, 2 cents to $250, 4 cents to $400, 9 cents to $600, 16 cents to $900 and 24 cents above, multiplied by 0.5 for names trading over 25M shares a day, 0.7 for 10-25M, 1.0 for 5-10M, 1.5 for 2-5M and 2.5 for 1-2M, and floored at one cent.

The model is calibrated against the study's own measured names. It returns AAPL 0.33 bp, XOM 0.45 bp and JPM 0.52 bp against the recorded "JPM/XOM approximately 1 tick, so s approximately 0.2-0.5 bp" of the p4 bounce diagnostic, and 2.0 bp for Lilly at $600-750 on 3M shares against that note's "LLY wider, so s approximately 1-3 bp". It also returns 0.31 bp for ORCL at its actual 2026-09-04 close of $158.78 and 0.50 bp for MRK at $150.33, against cohort D's 0.32 and 0.33.

**Every spread figure is an estimate.** Cohort D's flat `50/P` was safe because all twenty-five of its names traded below $335, where the tick binds. **Eight of these twenty trade above $250 and three above $450**, where the tick no longer binds and the book sets the spread; that is where these estimates are softest, and it is why the hand-widening above matters more here than it did there. Volatilities are a 2026-09-04 snapshot.

Ranked rationale:

1. **MSTR** — the widest ratio recorded in the study so far; a listed bitcoin-treasury proxy, so it reopens each morning against a reference that has been trading continuously for seventeen and a half hours. It is the only 24-hour state variable the roster can carry without a crypto connector, which the second-cohort note ruled out on fees and history.
2. **MRVL** — custom AI silicon; the second-highest volatility in the cohort among names that still quote inside two cents, and the natural in-industry lead-lag counterpart to AVGO already on the roster.
3. **NOW** — enterprise workflow software; the December 2025 five-for-one split moved it into the tick-constrained regime, which makes it the cohort's cleanest natural experiment on whether the tick, not the business, sets modelability.
4. **LULU** — apparel retail; the only consumer name anywhere near semiconductor volatility, and the cohort's single non-technology idiosyncratic-flow channel.
5. **SHOP** — merchant e-commerce; a Canadian issuer on Nasdaq whose GMV cycle keys on the same retail-sales prints as TGT and WMT, giving a cross-asset lead-lag the roster can already price.
6. **PANW** — cybersecurity; enterprise budget cycle, and as the acquirer that closed the $25B CyberArk deal on 2026-02-11, seventeen days before the cut, it is the one name here with live M&A as an intraday channel inside the window.
7. **GDX** — gold-miner basket; bullion prices for 23 hours a day, so the ETF reopens against an already-moved reference, and it supplies a basket-level metal-to-miner lead-lag against GLD and NEM already on the roster.
8. **BABA** — China internet retail ADR; the Hong Kong session closes at 04:00 ET, so the US open is a scored instant against a book that has already moved overnight.
9. **INTC** — semiconductors; the highest share volume in the cohort at 97.7M a day, and a policy channel — federal equity stake, foundry awards — that no other name carries.
10. **MU** — memory; the purest cyclical in the AI capex chain, and the largest dollar volume in the study at $35.8B a day, which is what lets a $1,017 stock quote inside a basis point at all.
11. **CIEN** — optical transport; datacentre interconnect orders, and the middle node of an ANET-CIEN-LITE supply-chain chain this cohort adds whole.
12. **WDC** — hard-disk storage; nearline drive pricing for AI datacentres, and the one name here carrying an in-window distribution.
13. **LRCX** — deposition and etch; NAND capex, and a same-industry follower to AMAT on a cleaner tape than AMAT's own.
14. **TER** — automated test equipment; the back end of the semiconductor supply chain plus industrial robotics, a different capex clock from the front-end tools.
15. **BIDU** — China search and autonomous driving ADR; a second, independent China channel whose information flow is AI rather than consumption, and a within-cohort pair against BABA.
16. **LITE** — optical components; the highest realised volatility in the cohort at 93.7%, and also its second-highest share price, which makes it simultaneously the most attractive and the least certain estimate in the set.
17. **C** — diversified banking; it clears on the cheapness of its tick rather than on movement, and supplies the rate and credit channel against JPM and BAC already on the roster.
18. **ADBE** — creative and document software; the incumbent-software-repriced-by-AI channel, distinct from NOW's workflow cycle and SHOP's merchant cycle.
19. **ANET** — datacentre switching; the AI network buildout, and the leading node of the ANET-CIEN-LITE chain.
20. **EOG** — oil and gas exploration and production; the only commodity pass-through in the cohort and its only **scheduled** flow, the EIA Weekly Petroleum Status Report at 10:30 ET on Wednesdays.

## Verification table

Prices, share volumes and volatilities are the 2026-09-04 close. Volatility is 30-day close-to-close realised. Move and half-spread are estimates; see the method note above.

| Ticker | Industry | Price used | Ann. vol used | 3-min move (bp) | Half-spread (bp) | Ratio | Splits / actions in window | Mechanism | Same ticker since before 2016-01-04? |
|---|---|---|---|---|---|---|---|---|---|
| MSTR | Digital-asset treasury | $142.80 | 97.97% | 54.1 | 0.35 | 155 | 10-for-1 ex 2024-08-08; renamed Strategy Inc 2025-08-11, ticker unchanged | Bitcoin proxy repricing a 24-hour reference | Yes; Nasdaq since 1998-06-11 |
| MRVL | Semiconductors | $223.55 | 78.34% | 43.3 | 0.31 | 138 | none; Bermuda-to-Delaware reorg 2021-04-21 changed CIK and CUSIP, not the ticker | Custom AI silicon; AVGO lead-lag | Yes; Nasdaq since 2000-06-27 |
| NOW | Software (application) | $141.26 | 61.30% | 33.9 | 0.35 | 96 | **5-for-1 ex 2025-12-18** | Enterprise workflow; mid-window tick regime change | Yes; NYSE since 2012-06-29 |
| LULU | Apparel retail | $100.61 | 75.66% | 41.8 | 0.50 | 84 | none (last 2-for-1, post-split trading 2011-07-12) | Consumer idiosyncratic flow at semis volatility | Yes; Nasdaq since 2007-07-26 |
| SHOP | Software (application) | $145.09 | 42.16% | 23.3 | 0.34 | 68 | **10-for-1 ex 2022-06-29**; NYSE to Nasdaq 2025-03-31 | Merchant GMV; retail-sales print lead-lag | Yes, but only from 2015-05-21 |
| PANW | Cybersecurity | $333.26 | 67.72% | 37.4 | 0.60 | 62 | **3-for-1 ex 2022-09-14; 2-for-1 ex 2024-12-16**; NYSE to Nasdaq 2021-10-25; CyberArk acquisition closed 2026-02-11 (share issuance, not a split) | Enterprise security budget; live M&A channel | Yes; NYSE since 2012-07-20 |
| GDX | Precious-metals ETF | $99.26 | 53.25% | 29.4 | 0.50 | 58 | none, ever; fund renamed 2016-05-01, ticker unchanged | Overnight bullion reprice; metal-to-miner lead-lag | Yes; NYSE Arca from 2006-05-22, fund inception 2006-05-16 |
| BABA | China internet retail (ADR) | $113.24 | 43.19% | 23.9 | 0.44 | 54 | none at ADS level (2019-07-30 8-for-1 ordinary split offset by an ADS-ratio change) | Hong Kong closes 04:00 ET; US open is a scored instant | Yes; NYSE since 2014-09-19 |
| INTC | Semiconductors | $95.80 | 46.38% | 25.6 | 0.52 | 49 | none (last split 2000-07-30); Mobileye 2022 was an IPO carve-out, not a distribution | Highest share volume in the cohort; policy channel | Yes; Nasdaq since 1971 |
| MU | Semiconductors | $1,016.59 | 52.23% | 28.9 | 0.59 | 49 | none (last split 2000-05-02) | Memory cycle; $35.8B a day of notional | Yes; Nasdaq since 2009-12-30, NYSE before |
| CIEN | Communication equipment | $321.00 | 79.40% | 43.9 | 0.93 | 47 | none in window (1-for-7 **reverse** effective 2006-09-22, pre-window) | Optical transport; middle node of the ANET-CIEN-LITE chain | Yes; Nasdaq IPO 1997-02-07, moved to NYSE 2013-12-23 — both pre-window |
| WDC | Computer hardware (storage) | $467.46 | 79.72% | 44.1 | 0.96 | 46 | no split; **SanDisk spin-off, separate trading 2025-02-24** | Nearline drive pricing for AI datacentres | Yes; Nasdaq since 2012-06-01, NYSE before |
| LRCX | Semiconductor equipment | $307.65 | 49.01% | 27.1 | 0.65 | 42 | **10-for-1 ex 2024-10-03** | NAND capex; in-industry follower to AMAT | Yes; Nasdaq since 1984-05-11 |
| TER | Semiconductor equipment | $357.03 | 62.20% | 34.4 | 0.84 | 41 | none; NYSE to Nasdaq 2018-11-27 | Back-end test capex plus industrial robotics | Yes; US-listed since the 1970s |
| BIDU | China internet content (ADR) | $99.47 | 54.00% | 29.8 | 0.75 | 40 | none at ADS level (2021-03-01 ratio change offset a 1-for-80 subdivision) | Second, independent China channel; AI rather than consumption | Yes; Nasdaq since 2005-08-05 |
| LITE | Communication equipment | $881.26 | 93.71% | 51.8 | 1.36 | 38 | none, ever; the JDSU-to-VIAV rename at the 2015 separation did not touch LITE | Optical components; highest volatility in the cohort | Yes, but only from 2015-08-04 |
| C | Banks (diversified) | $137.72 | 23.47% | 13.0 | 0.36 | 36 | none in window (1-for-10 **reverse** effective 2011-05-06, pre-window) | Rate and credit channel against JPM and BAC | Yes; NYSE since 1998 |
| ADBE | Software (application) | $266.51 | 47.69% | 26.4 | 0.75 | 35 | none (last split 2005-05-24); renamed Adobe Inc. 2018-10-08, ticker unchanged | Incumbent software repriced by AI news | Yes; Nasdaq since 1986-08-20 |
| ANET | Communication equipment | $193.78 | 47.55% | 26.3 | 0.77 | 34 | **4-for-1 ex 2021-11-18; 4-for-1 ex 2024-12-04** | Datacentre switching; leading node of the chain | Yes; NYSE since 2014-06-06 |
| EOG | Oil and gas E&P | $145.19 | 28.53% | 15.8 | 0.52 | 31 | no split; **seven special dividends 2021-2023** | Crude pass-through; EIA print at 10:30 ET Wednesdays | Yes; NYSE since 1989 |

All twenty are US-listed common stock, ordinary shares, ADSs or an ETF, and all are covered by Alpaca's SIP feed, which consolidates CTA and UTP for 100% of US stock and ETF volume, so none needs a different connector. **Two are ADRs** — BABA on the NYSE and BIDU on Nasdaq — both large and liquid, and both admitted under the ADR clause. GDX is the cohort's only ETF and pre-dates the window by nearly a decade.

**Two names carry short pre-window histories**, the same exposure cohort D accepted for PYPL and which the pull settled: **LITE** from 2015-08-04, five months, and **SHOP** from 2015-05-21, seven and a half months. Both were trading on 2016-01-04.

**Corporate actions that are not splits but will still move an adjusted series.** WDC's SanDisk distribution (record 2025-02-12, separate trading 2025-02-24) is the MRK/Organon case again and must be reconciled against whatever Alpaca returns — with one extra hazard: several vendors encode it as a synthetic "1323-for-1000 split", which it is not, and ingesting it from a split table would corrupt the series. EOG paid seven special dividends between December 2021 and December 2023, which will open a wide price-return against total-return wedge. BABA's 2019 ordinary-share subdivision and BIDU's 2021-03-01 ADS-ratio change are both exactly net-neutral at the ADS level but may surface as flags in a naive corporate-action feed. MRVL (2021-04-21) and, among the reserves, STX (2021-05-19) changed CIK and CUSIP without changing ticker, so anything joining on CUSIP will show a break. **PANW** issued 2.2005 shares plus $45 cash per CyberArk share on 2026-02-11, which enlarged its share count sharply seventeen days before the cut; that is dilution rather than a price adjustment, so the tape needs no factor, but any share-count-derived feature will step. PANW has also said it intends a Tel Aviv secondary listing under the ticker **CYBR**, which is a live symbol-collision hazard for any vendor keyed on symbol without an exchange qualifier.

**Three aggregator traps were hit while verifying this cohort, and all three would have produced a wrong answer.** Vendors synthesise a "1323-for-1000 split" for WDC's SanDisk distribution, which is not a split. Several sources attribute a 2020-04-15 one-for-ten reverse split to **GDX**; that split belongs to five other VanEck funds (OIH, KOL, EINC, REMX, FRAK) and GDX has never split — the price history alone refutes it. And low-quality sites circulate a phantom 2017 two-for-one split for **LULU**, which its 110.71M shares outstanding refutes arithmetically. In the other direction, both major split aggregators *omit* Ciena's 2006 one-for-seven reverse split entirely, so a reverse-split screen run against them would return a false clean. Corporate actions in this cohort were confirmed against SEC filings and issuer releases, not aggregators.

## Rejected on the gates

Recorded here so the exclusions are on file and are not re-proposed.

**Rescored on the window's own price scale, and failed.** **KLAC** — a ten-for-one split with ex-date 2026-06-12, after the hard cut. Scored on its post-split $185.60 it ranks 68; scored on the roughly $1,856 instrument that actually traded inside the window it is about 11. This failure mode is new to cohort E and any future cohort must screen for post-cut splits before using a current-price snapshot.

**Broken listing inside the window.** **SMCI** — Nasdaq suspended trading on 2018-08-23 and delisted on 2019-03-22; the stock traded OTC, outside the SIP, until relisting on 2020-01-14. The ticker never changed, which is precisely why a ticker-continuity check alone would have passed it. Its 2024-25 delayed-filing episode did *not* produce a delisting and is not the problem.

**Displaced by the three-per-industry cap.** **MCHP at 47** and **QCOM at 28**, the fourth and fifth semiconductors behind MRVL, INTC and MU. MCHP out-ranks eight members of the final twenty and is the cohort's first reserve.

**Eligible, out-ranked, and available as reserves in this order:** MCHP 47, ANET's alternates **TTWO 31** (electronic gaming, a release-calendar mechanism the cohort otherwise lacks), **STX 28** (storage, a same-industry lead-lag partner for WDC), **ETSY 28**, **AEM 26**, **AMAT 25**, **ARKK 25** and **SLV 25**. ETSY moved Nasdaq to NYSE on 2025-10-13 and TER moved NYSE to Nasdaq in 2018 — in-window exchange transfers with unchanged tickers, which are not disqualifying but will break venue-partitioned data.

**Failed the liquidity rule and were not worth a slot.** With ratios: SCHW 24, PAAS 23, UNH 22, DECK 22, NXPI 21, ISRG 21, CVS 21, XLY 21, GILD 20, VLO 20, TSM 19, OXY 19, FSLR 18, CCJ 18, UAL 18, MS 18, ADI 17, WYNN 16, EXPE 14, MPC 13, FANG 13. Two are worth naming. **TSM** is enormous and tight — 12.3M shares a day at $428.91 — but its 30-day realised volatility is 24.98%, so a 13.8 bp three-minute move cannot clear a 0.57 bp half-spread by enough; size does not substitute for movement. And the **entire defensive and mid-cap complex fails on price and volume together**, exactly as in cohort D: BIIB at 465k shares a day and HUM at 783k are too thin for any spread estimate to rescue.

**Whole sectors with no qualifying candidate.** Healthcare produced none — UNH 22, ISRG 21, GILD 20, with BIIB and HUM disqualified on volume. Energy produced exactly one, EOG at 31, because 2026 crude volatility sits near 28% and the refiners are thin: VLO 20, MPC 13, FANG 13. Precious metals produced one ETF, GDX at 58, with the miners and silver on the edge at AEM 26 and SLV 25.

**Window truncation.** **CYBR** — CyberArk was acquired by Palo Alto Networks; the merger closed pre-open on 2026-02-11, the last CYBR trading day was 2026-02-10 and Nasdaq suspended the symbol on 2026-02-12, ending the series eighteen days before the cut. It is the mirror of cohort D's U.S. Steel case, and it is worth recording that the *acquirer* in the same transaction, PANW, is rank 6 here. **JNPR** — Juniper ceased NYSE trading on 2025-07-02 when HPE closed, ending the series eight months early.

**Ticker discontinuity inside the window.** Each of these was screened for this cohort and rejected: **COHR** — II-VI became Coherent and IIVI became COHR on 2022-09-08. **AXON** — twice discontinuous, TASR to AAXN on 2017-04-05 and AAXN to AXON on 2021-01-26. **GOLD** — Barrick took the GOLD symbol on 2019-01-02, and vacated it again in 2025 on the rename to Barrick Mining under **B**, so the symbol is doubly broken. **TCOM** — Ctrip's ADSs became TCOM on 2019-11-05. **DELL** — the DVMT tracking stock was replaced by DELL Class C on 2018-12-28. **RTX** — UTX became RTX on 2020-04-03. **VST** — Vistra's shares began trading on 2016-10-04, and then only **OTCQX under VSTE**; the NYSE listing under VST did not begin until 2017-05-10, so it fails both the date gate and the no-OTC gate.

**A standing exclusion that turns out to be wrong, corrected here so it is not repeated.** **ON** — ON Semiconductor's change from ONNN to ON was effective at the open on **2015-04-06**, nine months *before* the window, not in 2016 as is widely reported. ON is therefore **not** disqualified on ticker continuity. It is out of this cohort only because it would be a fourth semiconductor behind MRVL, INTC and MU, and the cap binds.

**Reverse splits and post-2016 IPOs.** The standing exclusions from cohorts C and D carry over unchanged — CB, DD, BKNG, GAP, XYZ, AA, X, USO, GE, UNG, OIH, VXX, UVXY, SVXY. Post-2016 IPOs are excluded by construction and were never scored: COIN, PLTR, CRWD, SNOW, ABNB, SPOT, DDOG, NET, RBLX, MRNA, UBER, CVNA, TWLO, TTD, ZS, OKTA and ROKU. Leveraged and inverse volatility and commodity ETFs remain excluded on reverse splits, and **XOP** joins that list on a one-for-four reverse share split effective 2020-03-30 — the only split in its history, and the reason the energy sleeve here has no basket instrument and yields exactly one qualifying single name.

## Caveat

These are candidates, not winners, and nothing here predicts a gate outcome. The ratio is a **cost** screen, not a signal screen: a name that clears it is affordable to trade, which makes an efficient experiment, not a predictable asset.

**The estimates are softer than cohort D's, for a specific reason.** All twenty-five of cohort D's names traded below $335, where the one-cent tick binds and `50/P` is close to exact. Eight of these twenty trade above $250 and three above $450, where the tick no longer binds and the book sets the spread, so the half-spread is an interpolation between the study's own five measured anchors rather than a floor. **LITE at $881 and MU at $1,017 are the two least certain numbers in the table**, and LITE's 93.7% volatility and MU's $35.8B of daily notional are each doing a great deal of work; if either name's real spread is twice the estimate, it falls to the bottom of the band or through it.

**The volatilities are a single-day snapshot in a violently tech-led regime, and several are plainly transient.** LULU's 75.7% follows a 17.4% single-session decline on 2026-09-04 and will decay; at a long-run 40-45% it scores near 48 and still clears, but it is the most regime-dependent number here, and its reported 37.6M average daily volume is inflated by the same session. LITE, CIEN, WDC, TER and PANW are all running well above their own long-run levels. Re-running the arithmetic at long-run volatilities would move most of this cohort toward the band rather than through it, and would push the two thinnest clears below it: **EOG at 31 on a 28.5% volatility and C at 36 on a 23.5% volatility — the lowest in the set — are the two names least likely to survive a revolatilisation**, and C clears on the cheapness of its tick rather than on movement at all.

**NOW is two instruments.** For the first ten years of the window it traded above $200 and through much of 2024-25 near $800, where its half-spread in basis points was several times the 0.35 bp quoted here; only the final ten weeks after the 2025-12-18 split are the tick-constrained instrument the ratio describes. The same caution applies in weaker form to every name carrying an in-window split, and to any name whose 2026-09-04 price is far from its 2026-02-28 price. Post-cut splits were screened for across all twenty and only KLAC's was found.

**Three names are thin in dollar terms** relative to the cohort — BIDU at $0.26B a day, EOG at $0.38B and C and TER at about $0.80B — against a cohort median near $1.9B.

**The concentration is the cohort's own largest risk.** Fourteen of twenty are technology and most sit on one AI-capex factor, so a single drawdown in that complex is a common shock across the majority of the set — the mirror image of cohort D's failure mode, and a reason to expect the per-name results here to be far less independent than the count of names suggests. Note also that **GDX was cohort D's documented first reserve at a ratio of 53 and was never pulled**; cohort E claims it.

Minute-scale effects are small, bar data omits most order-flow information, and every candidate here still has to pass the same preregistered horizons, the same multiplicity correction, and the same shuffle and retrain gate as every name before it.

## Acquisition evidence

All twenty were pulled. The journaled `alpaca-sip-split-e` backfill used SIP,
split adjustment, one-minute bars, gzip, start `2016-01-01`, and exclusive end
`2026-02-28T23:59:59+00:00`. Source-config hash:
`85bda25ce4e0239b7be53c8b8450860a397e9861b2151dd6a1616193699e136f`; snapshot:
`088bb14e03f122be972b2230112f52d24c892f67f0dd0827a5f6fc94d4ceaa64`.

The immutable snapshot contains **22,469,719** bars, zero skipped records, all
twenty expected symbols, no unexpected symbols, and zero timestamps at or beyond
the cut. Source-scoped hash verification checked one snapshot with zero problems.
The pull took 25m21s of wall clock at a peak resident set of 885 MB.

Counts were MSTR 798,568; MRVL 1,148,737; NOW 986,662; LULU 1,017,574;
SHOP 1,126,215; PANW 1,026,671; GDX 1,375,414; BABA 1,600,650; INTC 1,501,384;
MU 1,454,284; CIEN 989,065; WDC 1,073,584; LRCX 1,021,775; TER 993,175;
BIDU 1,168,915; LITE 950,251; C 1,194,450; ADBE 1,059,313; ANET 963,906;
EOG 1,019,126.

Every symbol has usable 2016 observations and every symbol opens on the first
session of the window, including the two thin-pre-window names, SHOP (85,142
bars in 2016) and LITE (82,690).

**MSTR is the exception the counts expose, and it was not visible at selection.**
It carries **798,568** bars against a cohort median near 1.05 million, and only
**41,173** in 2016 against a median near 99,000 — roughly 40% of the coverage
every other name has that year. Minute bars exist only where trades print, so
this is a direct measure of how thinly MicroStrategy traded before it became a
bitcoin treasury vehicle in August 2020. The ratio of 155 that ranked it first
describes the post-2020 instrument; the 2016-2020 segment is a different, far
less liquid company under the same ticker. MSTR should be treated the way the
selection note already treats NOW, whose 5-for-1 split on 2025-12-18 means only
the last ten weeks are the tick-constrained name its ratio describes: **one
ticker, two instruments**, and any pooled fit that spans the regime boundary is
fitting two populations. No other name in the cohort shows a comparable
discontinuity in coverage.

**Not yet done.** Three distributions in this cohort are not splits and are not
removed by `adjustment=split`: the WDC SanDisk spin-off separately trading
2025-02-24, seven EOG special dividends across 2021-2023, and the PANW share
issuance for CyberArk closing 2026-02-11. Each must be reconciled against the
unadjusted `alpaca-sip` tree before the name is modelled, the same debt cohort D
still owes on MRK/Organon and MET/Brighthouse. The cohort is pulled, not gated:
no candidate here has been through Gate 1-3.

## Sources

- [Alpaca historical stock data: SIP consolidates CTA and UTP for all US stocks and ETFs](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
- [Alpaca market data FAQ: symbol-rename and asof mapping](https://docs.alpaca.markets/us/docs/market-data-faq)
- [SEC exemptive order of 2025-10-31 moving Rule 612 half-penny compliance to the first business day of November 2026](https://www.sec.gov/newsroom/press-releases/2025-130-sec-issues-exemptive-order-regarding-compliance-certain-rules-under-regulation-nms)
- [SEC adopts the Rule 612 minimum-pricing-increment amendments](https://www.sidley.com/en/insights/newsupdates/2024/10/sec-adopts-rules-modifying-minimum-pricing-increments-access-fee-caps-and-order-transparency)
- [ServiceNow shareholders approve the five-for-one split; adjusted trading from 2025-12-18](https://investor.servicenow.com/news/news-details/2025/ServiceNow-Shareholders-Approve-5-for-1-Stock-Split/default.aspx)
- [KLA announces its ten-for-one stock split, adjusted trading from 2026-06-12 — after the study cut](https://ir.kla.com/news-events/press-releases/detail/515/kla-corporation-announces-ten-to-one-stock-split-and)
- [Super Micro: Nasdaq trading suspension, 2018-08-23](https://www.sec.gov/Archives/edgar/data/0001375365/000162828018011320/exhibit991_20180822.htm)
- [Super Micro: relisting on Nasdaq, 2020-01-14](https://www.sec.gov/Archives/edgar/data/1375365/000137536520000004/exhibit99120200109.htm)
- [Western Digital completes the SanDisk separation; separate trading from 2025-02-24](https://www.sec.gov/Archives/edgar/data/106040/000119312525019294/d922460dex991.htm)
- [Palo Alto Networks completes the CyberArk acquisition, 2026-02-11; CYBR delisted from Nasdaq](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-cyberark-to-secure-the-ai-era)
- [State Street announces the XOP one-for-four reverse split, effective 2020-03-30](https://www.nasdaq.com/press-release/state-street-global-advisors-announces-reverse-splits-of-two-etfs-2020-03-23)
- [HPE closes the Juniper acquisition; JNPR ceases trading 2025-07-02](https://www.sec.gov/Archives/edgar/data/1645590/000164559025000096/ex-991x932025x8k.htm)
- [CyberArk merger completion and Nasdaq symbol suspension (Nasdaq ECA2026-84)](https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2026-84)
- [ON Semiconductor changes its ticker from ONNN to ON, effective 2015-04-06 — before the window](https://www.sec.gov/Archives/edgar/data/0001097864/000119312515103280/d896291dex991.htm)
- [II-VI becomes Coherent; IIVI becomes COHR, 2022-09-08](https://www.coherent.com/news/press-releases/ii-vi-changes-name-to-coherent-and-launches-new-brand)
- [Axon changes its Nasdaq symbol from AAXN to AXON, 2021-01-26](https://www.prnewswire.com/news-releases/axon-enterprise-to-change-nasdaq-symbol-to-axon-301213676.html)
- [Barrick takes the GOLD ticker on the NYSE, 2019-01-02](https://www.sec.gov/Archives/edgar/data/756894/000119312519000759/d675256dex994.htm)
- [Ctrip ADSs begin trading as TCOM, 2019-11-05](https://www.sec.gov/Archives/edgar/data/1269238/000119312519274119/d814130dex991.htm)
- [Dell completes the Class V transaction; DELL Class C from 2018-12-28](https://investors.delltechnologies.com/news-releases/news-release-details/dell-technologies-completes-class-v-transaction)
- [United Technologies and Raytheon complete the merger; RTX from 2020-04-03](https://www.rtx.com/news/2020/04/03/united-technologies-and-raytheon-complete-merger-of-equals-transaction)
- [Vistra's NYSE listing under VST begins 2017-05-10, after OTCQX trading as VSTE](https://investor.vistracorp.com/2017-05-04-Vistra-Energy-Announces-Trading-on-New-York-Stock-Exchange-to-Commence-on-May-10-2017)
- [Palo Alto Networks transfers its listing to Nasdaq, 2021-10-25](https://www.paloaltonetworks.com/company/press/2021/palo-alto-networks-to-transfer-stock-exchange-listing-to-nasdaq)
- [Shopify transfers its US listing to Nasdaq, 2025-03-31](https://www.shopify.com/news/shopify-to-transfer-u-s-stock-exchange-listing-to-nasdaq)
- [Marvell holding-company reorganisation to Delaware, 2021-04-21](https://www.sec.gov/Archives/edgar/data/1835632/000119312521123305/d282209d8k.htm)
- [Alibaba: the 2019 eight-for-one ordinary-share subdivision and the offsetting ADS ratio](https://www.sec.gov/Archives/edgar/data/1577552/000104746919006436/a2240126z424b5.htm)
- [Baidu ADS ratio change effective 2021-03-01 (Nasdaq ECA 2021-34)](https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2021-34)
- [Teradyne transfers its listing to Nasdaq, 2018-11-27](https://investors.teradyne.com/news-releases/news-release-details/teradyne-transfer-stock-exchange-listing-nasdaq)
- [Etsy transfers its listing to the NYSE, 2025-10-13](https://www.sec.gov/Archives/edgar/data/1370637/000137063725000077/etsy-20250929.htm)
- [Seagate Technology Holdings scheme of arrangement; STX unchanged from 2021-05-19](https://www.sec.gov/Archives/edgar/data/1137789/000119312521166009/d338012d8k12b.htm)
- [Citigroup one-for-ten reverse split effective 2011-05-06, well before the window](https://www.sec.gov/Archives/edgar/data/0000831001/000119312511131957/dex991.htm)
- [Ciena one-for-seven reverse split effective 2006-09-22, well before the window](https://www.sec.gov/Archives/edgar/data/0000936395/000095013306004172/w25346e8vk.htm)
- [JDS Uniphase completes the separation; Lumentum trades as LITE from 2015-08-04](https://www.sec.gov/Archives/edgar/data/0000912093/000119312515253636/d97642d8k.htm)
- [Adobe Systems Incorporated becomes Adobe Inc., 2018-10-08, ticker unchanged](https://www.sec.gov/Archives/edgar/data/796343/000079634318000168/adbe8k-namechange10082018.htm)
- [VanEck's April 2020 reverse splits covered OIH, KOL, EINC, REMX and FRAK — not GDX](https://www.businesswire.com/news/home/20200401005108/en/VanEck-Announces-Reverse-Share-Split-of-Five-VanEck-Vectors-ETFS)
- [lululemon sets the record date for its two-for-one split, the last one, in 2011](https://corporate.lululemon.com/newsroom/press-releases/2011/06-23-2011-084941414)
- [EIA Weekly Petroleum Status Report, released 10:30 ET on Wednesdays](https://www.eia.gov/petroleum/supply/weekly/)
- [Chinco, Clark-Joseph and Ye: one-minute cross-stock lagged-return signals](https://www.nber.org/papers/w23933)
- [Ait-Sahalia et al.: high-frequency predictability and market microstructure](https://www.nber.org/papers/w30366)
- [Box, Davis, Evans and Lynch: intraday arbitrage between ETFs and their underlying portfolios](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21001537)
- Prices, share volumes and shares outstanding from [stockanalysis.com](https://stockanalysis.com/); 30-day close-to-close realised volatility from [alphaquery.com](https://www.alphaquery.com/); split cross-checks against [splithistory.com](https://www.splithistory.com/)
