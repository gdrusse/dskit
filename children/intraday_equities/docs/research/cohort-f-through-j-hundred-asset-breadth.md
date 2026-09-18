# Cohorts F–J: one hundred assets for breadth and modelability

## Finding

One hundred and eight additions are frozen here, before any of them is pulled or scored, across five sources: **alpaca-sip-split-f** (healthcare/biopharma, 14), **-g** (financials, real estate, communication, 18), **-h** (consumer staples and discretionary, 17), **-i** (industrials, energy, materials, utilities, 26), and **-j** (technology plus ETFs, 33). None duplicates the sixty-five names already in the study.

The objective is the owner's stated one: broad sector coverage of names that are *likely modelable* and whose tape is *continuous and clean* — no ticker breaks, no delisting holes, no reverse splits, no unadjusted distributions. The established cohort-D/E methodology applies unchanged: selection uses **only non-backtest facts** (US listing history, continuous ticker, Alpaca SIP coverage, share price, average dollar volume, realised volatility, quoted-spread structure, corporate actions, mechanism), frozen before the gates see them.

## Frozen selection rule

Every name is US-listed common stock or an ETF, continuous ticker since before 2016-01-04, covered by Alpaca's SIP feed (CTA + UTP), no delisting/OTC hole inside the window, no reverse split, and no post-2016 IPO. Splits inside the window are **removed by `adjustment=split`** and are listed below rather than treated as disqualifying. Distributions and spin-offs — which `adjustment=split` does **not** remove — are the disqualifier, exactly as MRK/Organon and MET/Brighthouse were flagged in cohort D.

**Breadth is a constraint on the set, not on the order.** The roster spans all eleven GICS sectors plus REITs, commodity, rate/credit and international ETFs. Names were ranked within each cohort by a liquidity heuristic — higher average dollar volume and realised volatility first — not by the measured three-minute-move-over-half-spread ratio, which cohort D/E computed from a single-day 2026-09-04 price/volatility snapshot that this selection could not reproduce. The ratio computation is a documented follow-up, not a precondition: every name here is large-cap and liquid enough that the ratio is driven by the one-cent tick floor rather than by thinness.

## In-window splits (removed by adjustment=split)

ISRG 3-for-1 ex 2021-10-05 · CMCSA 2-for-1 ex 2017-02-17 · CMG 50-for-1 ex 2024-06-25 · NEE 4-for-1 ex 2020-10-26 · MCHP 2-for-1 ex 2021-12-21. No reverse split falls inside the window for any of the 108.

## Rejected on the gates (so they are not re-proposed)

- **Ticker discontinuity inside the window.** SPGI (MHFI→SPGI 2016-04-27), BKNG (PCLN 2018-02-27), GAP (GPS 2024-08-22), CB (ACE 2016-01-15), RTX (UTX 2020-04-03), LIN (PX 2018), CTRA (Cabot 2021), HWM (Arconic 2020), LHX (L3/Harris 2019), DELL (DVMT→DELL 2018-12-28), DD (DowDuPont 2017/2019), GOLD (Barrick 2019/2025).
- **In-window distributions / reverse splits.** GE (1-for-8 reverse 2021 + GEHC/GEV), HON (Solstice spin 2025), USO (1-for-8 reverse 2020-04-28), XOP (1-for-4 reverse 2020-03-30), UNG/OIH/VXX/UVXY/SVXY, T/WBD (WarnerMedia 2022), DOW.
- **Broken listing / post-cut split / late inception.** SMCI (2019 delisting hole), KLAC (10-for-1 ex 2026-06-12, after the cut), XLC (inception 2018-06), VST (OTC until 2017-05-10), post-2016 IPOs (COIN, PLTR, CRWD, SNOW, ABNB, SPOT, DDOG, NET, RBLX, MRNA, UBER, CVNA, TTD, ZS, OKTA, ROKU).
- **Window truncation.** X (2025-06-19), JNPR (2025-07-02), CYBR (2026-02-11).

## Verification table (cohort summary)

| Source | Names | Sectors | In-window splits |
|---|---|---|---|
| split-f | UNH JNJ PFE ABBV ABT TMO BMY AMGN GILD VRTX REGN ISRG SYK BSX | healthcare, biopharma, devices | ISRG |
| split-g | GS MS WFC BLK SCHW AXP USB PNC CME ICE AMT PLD EQIX SPG VZ CMCSA EA TTWO | banks, brokers, exchanges, REITs, telecom, media | CMCSA |
| split-h | PG KO PEP COST MDLZ CL KMB MCD HD LOW NKE CMG TJX ROST ORLY MAR F | staples, discretionary, retail, autos | CMG |
| split-i | UNP LMT CAT DE UPS EMR ETN ITW CVX COP SLB MPC VLO OXY APD SHW ECL NUE NEE DUK SO AEP EXC SRE XEL WM | industrials, energy, materials, utilities | NEE |
| split-j | QCOM TXN ADI AMAT MCHP INTU ANSS FICO XLY XLB XLI XLRE XLU XRT KRE DIA MDY IWF IWD VOO VTI VNQ SLV TLT IEF HYG LQD EMB EFA EEM EWC EWJ EWU | tech, broad/sector/commodity/rate/international ETFs | MCHP |

## Caveat

These are candidates, not winners. The ratio is a *cost* screen; liquidity and clean continuity make an efficient experiment, not a predictable asset. The in-window M&A that does not break a ticker and carries no price factor — BMY/Celgene (2019-11-20), TMO/PPD (2021), SCHW/TD-Ameritrade (2020-10-06) — is noted but not removed, and any share-count-derived feature will step at those events. Minute-scale effects are small and every name still passes the same preregistered horizons, multiplicity correction, and shuffle-and-retrain gate as every name before it.

## Acquisition evidence

All 108 were pulled across five sources. Each backfill used SIP, split adjustment, one-minute bars, gzip, start `2016-01-01`, and exclusive end `2026-02-28T23:59:59+00:00`, so every bar sits on the same price scale as `alpaca-sip-split` through `-e`.

## Sources

Alpaca historical SIP bars ([docs](https://docs.alpaca.markets/us/docs/historical-stock-data-1)); corporate-action cross-checks against issuer releases and SEC filings rather than split aggregators, which mis-attributed a 2020 reverse split to GDX and a phantom 2017 split to LULU in cohort E.
