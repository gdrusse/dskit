# P4: price definition — bounce diagnostic and the close / vwap / mid comparison

Date: 2026-09-03. Research only; nothing run. Plan item P4 of `docs/plans/2026-09-horizon-search.md`.

## Question

`px` is the last trade of the minute (`price_field: close`). A print sits at bid or ask, so `px` carries a ±half-spread coin flip that appears in the label with a minus sign and in `ret_lag_0` with a plus. Is the H≤2 gain (ridge +0.09%, LightGBM +0.23% at H=1) the next print or the next value? Which price definition settles it, and what does it cost?

## Finding

**(a) Accepted fixes.** Roll (1984): under a random-walk efficient price with half-spread s, trade-price changes have cov(Δp_t, Δp_{t-1}) = −s² (= −spread²/4) and zero beyond lag 1; ρ(1) = −s²/(σ²+2s²). The microstructure literature therefore builds short-horizon (1–60 min) return targets from the **prevailing NBBO midquote at the decision instant** — Chordia–Roll–Subrahmanyam (5-min midquote returns), realized-measure work (Hansen–Lunde 2006; Bollerslev et al.), and the LOB deep-learning line (DeepLOB, Sirignano–Cont) all label on mid. The **microprice** (Stoikov 2018) beats mid only at tick/sub-second scale; at 1-min it is ≈ mid, and its imbalance term is a feature, not a target. **VWAP** and **time-weighted mid** shrink bounce (~s²/n for n prints) but are interval averages: Working (1960) shows first differences of averaged random-walk prices acquire *positive* lag-1 autocorrelation up to +0.25 — a fake momentum that does not exist in point prices. **Last-mid-of-minute** is just "mid" sampled at bar end and is what Databento's `bbo-1m` and AlgoSeek TAQ-minute bars carry. Verdict: mid (last valid NBBO before bar end) is the reference target; VWAP is a one-sided diagnostic.

**(b) Diagnostic.** Free, on bars already held, per name: (1) ρ̂(1) of 1-min close returns; Roll-implied half-spread ŝ = √(−cov) against the known tick spread (JPM/XOM ≈ 1 tick ⇒ s ≈ 0.2–0.5 bp; LLY wider ⇒ s ≈ 1–3 bp). (2) Bounce-only skill bound: R² ≈ ρ̂(1)² (about 1e-5 for JPM, up to 4e-3 for LLY). The CW gain of +0.09% ≈ R² 0.0009 sits inside that band — consistent with bounce, not proof. (3) ρ̂(1) of vwap returns: should flip toward ≥0 (Working). (4) Ablation on existing close data: drop `ret_lag_0` (and `_1`); bounce lives only there. Then the price-definition runs below: bounce ⇒ the gain vanishes under mid.

**(c) Pitfalls.** *VWAP target:* anchored at the minute's volume-centre, not at t — `close[t]−vwap[t]` mechanically predicts `vwap[t+1]−vwap[t]` (look-back inside the bar; Working/Blume–Stambaugh bias), so a vwap label can *add* spurious gain; must use vwap for label AND lags together, and read only a collapse as evidence. *Mid target:* stale/locked/crossed NBBO, zero-size sides, halts, and the first ~5 min after open where spreads are many ticks; take the last quote with ask>bid and size>0 before bar end, drop bars with no quote in the last 60 s, keep 09:35–15:59. Mid is not tradable; the model is prediction-only, so that is acceptable.

**(d) Cost.** Alpaca `/v2/stocks/quotes` (SIP NBBO, back to 2016): 10,000 records/page, ~13,000 rec/s ceiling (~100 ms per call); AAPL 2023-08-08 = 1.48M quotes = 148 calls ≈ 114 s. JPM/LLY/XOM plausibly 0.3–1.5M NBBO updates/day; 3 names × ~1,300 days (2021→2026-02) ≈ 1–4 B records ≈ 30–90 h transfer and ~10²-GB JSON in flight, reduced to 1.5M minute rows (~50 MB) on disk. Rate limit (200/min Basic) is not binding; throughput is. Cheaper cuts: pull only the fold-validation windows (≈60 days × 3 ≈ 3–5 h), or Databento `bbo-1m` (one last-BBO row per minute — exactly the target; consolidated US-equities history is shorter than 2021 and price needs a quote). Deriving a mid from trades needs quotes anyway (Lee–Ready); Roll gives the spread, not the sign. Polygon/Massive aggregates carry no quotes; its quotes endpoint (50k/page) is no cheaper. Alpaca bars carry no quotes. No free minute bid/ask exists.

## Recommended design

Three universes differing in one knob, everything else byte-identical: **A `close`** (existing h01/h02/h03 ridge+lgbm, the control), **B `vwap`** (`price_field: vwap`, in bars, zero cost), **C `mid`** (new field from Alpaca NBBO, last valid quote before bar end; starts on a fold-validation slice). Hold fixed: rows/timestamps (bars with no valid quote dropped from all three), feature list, `label_scale`/`label_residual`, H ∈ {1,2,3}, 20 folds, seeds, CW test, IC and calibration slope. Label and `ret_lag_*` use the same price within a universe. Add the four free diagnostics of (b) as a scan-node metric before any run.

**Bounce:** ρ̂(1)_close ≈ −ŝ²/σ² with ŝ near the tick, H=1 gain ≤ ρ̂(1)², gain and IC at H=1 fall to the H=3 level under mid, collapse under vwap, and the ablation without `ret_lag_0` loses it. **Real:** mid keeps the gain within fold CI, ρ̂(1)_mid ≈ 0 yet the gain survives, the ablation keeps it, and the two-bar wall stays at H=2 under mid. Build on **mid**; vwap only confirms a collapse.

## Sources

- Roll (1984), J. Finance — https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1984.tb03897.x
- Portnaya (2026), "The Bounce Has No Direction" — https://arxiv.org/html/2606.29591 (ρ(1) = −s²/(σ²+2s²); bounce is magnitude-only)
- Nikolopoulos (2026), "Spurious Predictability in Financial ML" — https://arxiv.org/pdf/2604.15531
- Stoikov (2018), "The micro-price" — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694
- Chordia, Roll, Subrahmanyam (2005/2008), 5-min midquote returns — https://www.sciencedirect.com/science/article/abs/pii/S0304405X07001833
- Hansen & Lunde (2006), microstructure noise — https://public.econ.duke.edu/~get/browse/courses/201/spr10/DOWNLOADS/MicroStructureNoise/Hansen-Lunde-JBES-2006+COMMENTS/s1.pdf
- Working (1960), Econometrica 28(4) 916–918, autocorrelation of differences of averages
- Alpaca historical quotes — https://docs.alpaca.markets/reference/stockquotes-1 ; throughput thread — https://forum.alpaca.markets/t/download-speed-for-historical-quote-data/12777
- Databento bbo-1m/ohlcv-1m schemas — https://databento.com/blog/upcoming-changes-to-pricing-plans-in-january-2025
- Massive (Polygon) quotes — https://massive.com/docs/rest/stocks/trades-quotes/quotes
