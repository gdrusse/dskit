## Question

Do we hold all the Theta data and other sources the relative-IV prediction studies need (three pairs, the ADR-0274 fifteen-name cohort, and the next registrations), and what exactly is missing? Read-only audit by a Sonnet subagent on 2026-10-21; nothing was acquired.

## Finding

### Outcome

- Nothing blocks the current three-pair studies or the fifteen-name cohort: Theta Parquet, adjusted stock bars with dividends and splits, QQQ, rates and VIX-family series are all on disk.
- Stock bars for the twelve new cohort names already exist (8 in `stock-daily-bars`, 4 in `stock-daily-bars-300`); no acquisition is needed for them.
- The real gaps: (1) the full-universe study, because the Theta backfill holds 230 of 414 tested roots and is stopped on a hard quality alarm; (2) signed order flow (no connector stream); (3) earnings dates (partial); (4) QQQ after 2025-12-15 (minor); (5) any 2026 option data (sealed by design).

### 1. Theta store inventory

| Item | Fact | Source |
|---|---|---|
| Parquet tree | `/home/russell/data/thetadata/parquet-v2/` holds ONE tier: `options-tested/theta` (+ `first_quote.json` 230 roots, `verify.jsonl` 459 rows). No parquet for core, core2, index, rest1000, orphan | `$S/parquet_inventory.json` |
| options-tested Parquet | 459 files, 26.90 GB, 383,983,583 rows, 230 symbols (median 1.45M rows/root), dates 2016-01-04..2025-12-31, 0 files reaching 2026, one schema digest (version 2, 48 fields), 0 row groups without statistics | `$S/parquet_inventory.json`, `$S/parquet_symbols.json` |
| Roots landed | 230, alphabetical A..MRVL (MRVL partial, oldest 2023-01-31). 149 start 2016-01-04. Cursor tracks 237 roots; 7 have no chain (BRUN, CBRS, FPS, FRVO, GLND, INFQ, KEEL). 177 registered roots NOT started: MS, MSFT, MSTR, MU ... ZS (includes MSFT, NVDA, TSLA, NFLX, XOM, CVX, COP) | `$S/tested_coverage.txt`; `options-tested/state/theta/option_eod-backfill.json` |
| Schema (confirmed) | symbol, expiration, strike, right, date, created, last_trade, timestamp, open, high, low, close, volume, count, bid/ask size, exchange, condition, bid, ask, open_interest; vendor Greeks delta, theta, vega, rho, epsilon, lambda, gamma, vanna, charm, vomma, veta, vera, speed, zomma, color, ultima, d1, d2, dual_delta, dual_gamma; implied_vol, iv_error; underlying_timestamp, underlying_price; acquired_at. Timestamps naive US/Eastern (EOD report ~17:15 ET) | `$S/parquet_symbols.json` (`fields`) |
| 2026 presence | none; every config seals at 2026-01-01 and the pull cut is `end` 2026-01-01 | `$S/parquet_symbols.json` (`files_reaching_2026` empty) |
| Fifteen cohort roots | 69,598,780 rows (AAPL+AMZN+GOOGL 23,469,278), each 2016-01-04..2025-12-31, 2,514 dates, 3-4 files each | `$S/parquet_symbols.json`, `$S/cohort_census.txt` |
| Raw gzip, options-tested | 459 snapshots, all with manifest, 40.78 GB raw; 346 observation gz files, 30.06 GB (113 snapshots are "raw_only": observations not landed) | `$S/raw_inventory.json`; `pull.log` |
| Raw gzip, options-core | 8 snapshots, 0.67 GB, 4 roots only (AA, AAL, AAOI; AAP back to 2017-08-30); not exported to Parquet; options-tested's list excludes them | `$S/raw_inventory.json` |
| core2 (303 roots), index (12 roots: SPX, SPXW, NDX, NDXP, RUT, RUTW, VIX, VIXW ...), rest1000 (1000 roots, predecessors FB, SQ, UTX, RTN, BRKB, TWTR, ATVI, CELG first), options-index-orphan-20261012 | registered configs only; 0 raw snapshots, 0 rows. Leftover half-landed `.stage-norm-*` dirs in options-tested (4), options-core (1), orphan (1), dated 2026-10-12 | `$S/raw_inventory.json` |
| `driver/` | `chain.sh` (tiers in order tested, index, core2, rest1000), `pull_theta.sh` (acquire, parquet export, verify loop; gates: 120 GB free on data, 30 GB free on /mnt/c), `start_all.sh`, `watchdog.sh`, `pull.log`, `HANDOFF-2026-10-20-wsl-restart.md`, `ALERT.txt` (old disk-guard stop 2026-10-17) | `/home/russell/data/thetadata/driver/` |
| `terminal/` | Theta Terminal v3 jar (build 20261014:a03167c per log), `config.toml` (REST 127.0.0.1:25503, websocket 25520), `terminal.log`. Log: Subscriptions Stock FREE, Options STANDARD, Index FREE; max concurrent requests 4 | `terminal/terminal.log` |
| Process state | Java terminal alive (pids 2507/2543, up 8 h), ports 25503 and 25520 listening. No chain.sh, pull_theta.sh or watchdog running: the pull STOPPED at 2026-10-20T22:22:12Z, exit 3, hard alarm `crossed_quotes` (crossed share 2.12e-4 vs limit 1e-4, snapshot `20261020T222113Z-backfill-a74f30f3`, MRVL) | `ps`, `ss`, `pull.log` |
| Pull rate | 412 pulls 2026-10-14..20; 14.9M rows in the 2.9 h after the last restart (about 5M rows/h); 115M rows on 2026-10-16 | `pull.log` |
| Disk | data disk 377 GB free; `/mnt/c` 41 GB free (guard 30 GB) | `df` |

Quality ledger of the Parquet verify run (459 rows): alarms date_coverage 22 snapshots (22 roots over the 8% missing-weekday limit: ASX, B, CORZ, DOW, FLY, LEN, LITE, LMT, LNG, LOW, LRCX, LULU, LUV, LVS, M, MA, MARA, MAT, MCD, MCHP, MDB, META; none is a cohort root), crossed_quotes 2 (META 4.56e-4 on 2026-10-18; MRVL 2.12e-4 now), rows_per_date_jump 1 (AI). Source: `/home/russell/data/thetadata/parquet-v2/options-tested/verify.jsonl`.

### 2. Theta streams: supported by the connector vs acquired

The connector (`dskit/onboarding/libs/thetadata.py`) supports exactly three streams.

| Stream | Endpoint | Acquired? |
|---|---|---|
| `option_eod` | `/v3/option/history/greeks/eod` (or `eod`) + `open_interest`, one `expiration=*` request per root-date | YES (options-tested 230 roots; options-core 4 roots) |
| `underlying_eod` (`underlying_kind` stock or index) | `/v3/stock/history/eod`, `/v3/index/history/eod` | NO (no snapshot of this stream in any Theta store). Docstring: stock bars need a Stock plan; terminal reports Stock FREE. Not tested |
| `option_quote_at` (`quote_times` HH:MM:SS) | `/v3/option/at_time/quote`: whole-chain last NBBO at a clock time | NO |

Not supported by the connector at all: option trades or trade-quote history (intraday), stock intraday bars, dividends, earnings, index-option tiers beyond the registered-but-unstarted `options-index`. Whether Theta serves earnings or what Standard entitles beyond EOD was not determinable offline.

### 3. Stock and market data in `/home/russell/data/index_options/ob`

Origin of the bars: Yahoo chart API v8 through `httpblobs` + child transform `index_options.stock_bars:StockDailyBars` (config in `children/index_options/configs/source-stock-daily.json`; store record `ob/store/records/source_config/2402c7e3...e289e17.json`). Columns: id, symbol, date, open, high, low, close, adjusted_close, volume, dividend_amount, split_coefficient, created_at. Raw Yahoo response kept at `payload/files/_raw/<T>.json`.

| Source | Content | Source of numbers |
|---|---|---|
| `stock-daily-bars` | 3 snapshots (7bb8fded, 149ab5a5, be101b08), 116 symbols, 2016-01-04..2026-09-30, 2,701 rows each | `$S/stock_inventory.json`, `$S/stock_bars_cohort.json` |
| `stock-daily-bars-300` | 1 snapshot, 300 symbols, same window and columns | same |
| `benchmark-daily-bars` | SPY only, same window, 43 dividends | same |
| `philippdubach-options` | MIT options-dataset-hist mirror, EOD (16:00 ET) option chains SPY 2008-2025, QQQ 2011-2025, IWM 2008-2025 (about 53.4M contract-day rows) plus `underlying_prices.parquet`: QQQ 1999-11-01..2025-12-15 (adjusted_close present, 85 dividends), SPY ..2025-12-12, IWM ..2025-12-15 with adjusted_close all null | `$S/stock_inventory.json`; source_config notes |
| `fred-market-features` | 31 FRED series by `localfiles`: DGS3MO 1999-01-04..2026-09-24 (7,235 rows), DGS1MO/6MO/1/2/5/10/30, DFF, SOFR, NFCI, HY/IG spreads, WALCL, others; the pinned rate is DGS3MO | `$S/market_inventory.json`, `features/fred_graph/*.csv` |
| `cboe-index-wide` | daily OHLC to 2026-10-19: VIX (1990-01-02), VIX9D (2011), VIX3M (2009), VIX6M, VIX1Y, VIX1D (2022-05-13), VVIX (2006), SKEW (1990), VXN, RVX, VXD, SPX, XSP, plus single-stock vol indices VXAPL, VXAZN, VXGOG, VXGS, VXIBM (each 2011-01-07..2026-10-19, 3,964 dates, still changing daily) | `$S/vol_inventory.json` (`inv_vol.py`) |
| `cboe-index`, `production-vix-20261007` | SPX and VIX 1990/1975..2026-10-19; VIX copy 1990-01-02..2025-12-31 | same |
| `cboe-chain`, `cboe-chain-wide`, `optionshist-chain` | live Cboe chain snapshots from 2026-09-24 (SPX, XSP, NDX, RUT, SPY, QQQ, ...) and the philippdubach chain | source_config notes |

Basis difference across snapshots of `stock-daily-bars` (checked, `$S/stock_bars_cohort.json`): the FIRST snapshot (7bb8fded) stores unadjusted `close` (AAPL 2020-08-31 return -74.15%, AMZN 2022-06-06 -94.90%, ISRG 2017-10-06 -66.8%) with raw per-share dividends; the two later snapshots, including the pinned be101b08 (latest, so what `payload_files` returns), store split-adjusted OHLC (AAPL +3.39% that day) and split-adjusted dividends. ADR-0271's "data-basis correction" describes the first snapshot, as the v6 audit memo A3 already records.

### ADR-0274 cohort, per name (pinned snapshot be101b08 for eight names, 8df98554 in `-300` for four)

| Name | Bar source | Rows | First..last | Adjusted close | Dividends (ex-date rows) | Splits in window | Theta chain rows 2016-2025 |
|---|---|---|---|---|---|---|---|
| AAPL | stock-daily-bars | 2701 | 2016-01-04..2026-09-30 | yes, 0 nulls | 43 | 2020-08-31 4:1 | 4,735,792 |
| AMZN | stock-daily-bars | 2701 | same | yes | 0 | 2022-06-06 20:1 | 10,470,321 |
| GOOGL | stock-daily-bars | 2701 | same | yes | 10 (from 2024-06-10) | 2022-07-18 20:1 | 8,263,165 |
| ISRG | stock-daily-bars | 2701 | same | yes | 0 | 2017-10-06 3:1, 2021-10-05 3:1 | 4,860,964 |
| CMG | stock-daily-bars-300 | 2701 | same | yes | 0 | 2024-06-26 50:1 | 6,884,191 |
| BA | stock-daily-bars | 2701 | same | yes | 17 (last 2020) | none | 3,936,108 |
| GS | stock-daily-bars | 2701 | same | yes | 43 | none | 4,228,168 |
| CHTR | stock-daily-bars-300 | 2701 | same | yes | 0 | none | 3,971,236 |
| CAT | stock-daily-bars | 2701 | same | yes | 43 | none | 3,009,106 |
| AVGO | stock-daily-bars | 2701 | same | yes | 43 | 2024-07-15 10:1 | 4,841,643 |
| BIDU | stock-daily-bars-300 | 2701 | same | yes | 0 (ADR, none declared) | none | 2,817,818 |
| ABBV | stock-daily-bars | 2701 | same | yes | 43 | none | 2,524,151 |
| GILD | stock-daily-bars | 2701 | same | yes | 43 | none | 2,358,357 |
| CRM | stock-daily-bars | 2701 | same | yes | 11 (from 2024-03-13) | none | 3,182,691 |
| HUM | stock-daily-bars-300 | 2701 | same | yes | 42 | none | 3,515,069 |

Every cohort name has all 2,514 Theta session dates inside its 2,701 bar dates (2,701 = 2,514 + 187 sessions of 2026), no duplicate dates. Split dates also sit in the raw Yahoo `events.splits` (ISRG 3:1 x2, AVGO 10:1, CMG 50:1, AAPL 4:1) and dividends in `events.dividends` (AVGO 43, CRM 11, HUM 42, ABBV 43), so ex-dates are recoverable from `_raw/`. All twelve new cohort names plus the three phase-one names: HAVE.

### 4. Other on-disk sources

| Source | Fact |
|---|---|
| ADR-0274 universe surface | `/home/russell/data/statistical_arbitrage/structural-universe-20261021/surface_universe_rows.jsonl`: 30,000 rows, 15 names x 2,000 dates, 2018-01-02..2025-12-15, 30-DTE only (ATM IV, 25-delta put/call IV, risk reversal, spot, forward, half-spread ratio); status ok on all but 2 (AMZN, GOOGL no_forward); stale_spot true on 1 row (`$S/universe_surface_coverage.json`) |
| Chain panel (2016-2020) | `/home/russell/data/index_options/theta-training-20261016/chain-panel`: 1,259 daily parquet files 2016-01-04..2020-12-31, 93 roots, 405 stale-spot symbol-days over 16 roots (cohort: only AAPL 2019-09-11). Built in 622 s node time. Also `theta-ml-prep-20261016` panels (2019-12-16 onward) and model-ready augmented panels (59k, 62k, 197k rows) |
| Earnings dates | `children/index_options/configs/theta-earnings-labels-2016-2020.json`: hand-compiled announcement dates for AAPL, NVDA, AMZN, GOOGL, MSFT, 2016-01-01..2020-12-31 only; AAPL's 20 verified against Apple releases, the other four unverified. No calendar for 2021-2025 or for the twelve names. Derived alternative: `ChainEventDetector` (ADR-0265 Amendment 4) from the chain's front-month IV collapse |
| Market-implied factor | VIX family above (HAVE). QQQ chain from philippdubach 2011-2025 also local; Theta SPY/QQQ/index roots are registered but unstarted |
| Intraday equity bars | none on disk under `/home/russell/data` (the `intraday_equities` child holds code only; `/home/russell/data/intraday_runs` is logs, 3 MB) |
| Alpaca option daily bars | `/home/russell/data/stock_options/amzn`, `msft` (alpaca_free_history, 2024-02-01..2026-09-30, DTE 30-45), 3.7 MB and 1.0 MB; one-day ORATS sample zip; DoltHub sample |
| Older bars | `production-older-bars-20261007-v2` AMZN 1997-05-15..2025-12-31; `universe-older-bars-20261008` MSFT 2003-03-03..2025-12-31 |

### 5. Gap table

| Need | Status | Missing piece | Cheapest path | Size / time |
|---|---|---|---|---|
| (a) three-pair studies | HAVE | QQQ factor ends 2025-12-15 (replay `end_before` 2025-12-16); to use Theta data through 2025-12-31 add QQQ bars | Yahoo QQQ via `httpblobs` (block B) | one request, seconds |
| (b) fifteen-name cohort | HAVE | none. Only cohort-specific limits: 2016 unusable for CMG, BA, GS, CAT, ABBV, CRM, HUM (study starts 2018); 30-DTE surface only, so no 90-DTE term slope, OI or Greeks for the twelve names (compute from existing Parquet, no acquisition) | none | none |
| (c) full tested universe, 2018-2025 pooled | PARTIAL | (i) 177 of 414 roots not acquired (alphabetical N..Z side; includes MSFT, NVDA); (ii) pull stopped on a hard alarm; (iii) chain panel covers 2016-2020 only, and must be rebuilt over the 230 landed roots (compute, no acquisition); (iv) universe is today's names (rest1000 holds delisted predecessors, unstarted) | resume driver (block A), then rebuild panel | about 257-296M rows, about 21 GB Parquet plus about 31 GB raw gz (more if observation gz are kept, about 34 GB): about 51-59 h at about 5M rows/h; panel rebuild extrapolated to about 40 min for 230 roots 2018-2025 (not measured) |
| (d) strike-level gamma profile | HAVE (data) | code only: per-strike OI, vendor gamma, strike, expiration are in every Parquet row; no node aggregates a near-spot profile | extend `ChainDayPositioning` | none |
| (e) signed order flow | MISSING | EOD rows carry volume and trade `count`, no aggressor side. Needs intraday option trades and quotes; the connector has no such stream (only whole-chain `option_quote_at`) | new Theta stream (ADR, code), or quote-at plus tick rule | EOD `count` totals: AAPL+AMZN+GOOGL 570.6M trades 2016-2025 (535.5M 2018-2025; 82.7M in 2025), 15 names 733.9M 2018-2025 (`$S/trade_counts_cohort.json`); bytes not estimated |
| (f) market-vol factor | HAVE | none (VIX, VIX9D, VIX3M, VVIX, SKEW, VXN to 2026-10-19; single-stock VXAPL, VXAZN, VXGOG for the three names) | none | none |
| (g) earnings-day exclusion | PARTIAL | no dated calendar for 2021-2025 or for the twelve names; five-name 2016-2020 list is hand-compiled, AAPL only verified | chain-derived events (zero acquisition); SEC 8-K Item 2.02 via `httpblobs` (draft config, needs parse step); no dskit earnings connector exists | chain-derived: compute only; SEC: 14 requests plus an ADR |
| (h) distributional scoring | HAVE | nothing new | none | none |
| (i) corporate actions by ex-date, cohort | HAVE | none; caveat: pinned snapshot dividends are split-adjusted per share (raw per-share in snapshot 7bb8fded and derivable from the splits) | none | none |
| 2026 option data (not a listed need) | MISSING by design | sealed; ADR-0265 requires a separate owner decision | block C | about 6.1M rows for 15 names (about 1.4M for the three), about 1.2 h (17 min) |

### 6. Data-quality notes

- Vendor IV placeholders: about 4% of contracts carry `iv_error` 100 with `implied_vol` 0.5, about 8% `implied_vol` 0 (deep in the money) (ADR-0273 amendment 1e). Cohort `iv_error` 100 share from first bid: AAPL 4.0%, AMZN 1.2%, GILD 1.2%, BA 1.1%, others under 1% (`$S/cohort_census.txt`).
- OI resets on split days: the OI put/call difference reached +1,220 on AAPL 2020-08-31 (ADR-0273); positioning inputs use log ratios.
- AAPL 2019-09-11: vendor spot stale (195.57, the 2019-06-25 value, true close 223.59), flagged in the panel. In the universe surface, `stale_spot` is true on 1 of 30,000 rows. 2021-2025 has no stale-spot scan beyond that.
- 90-DTE expiry is missing on about 10% of days (ADR-0273).
- New for the cohort: for CMG, BA, GS, CAT, ABBV, CRM, HUM, every 2016 row has null `underlying_price` and null `bid` (100% of 299,045, 148,349, 147,245, 168,993, 113,180, 154,254, 93,538 rows; first quoted date 2017-01-03). From 2017 on, null `underlying_price` is 0.6-2.7% of rows per root-year (contracts with open interest and no trade) (`$S/cohort_nulls.json`).
- Non-standard strike share (adjusted strikes after splits) from the census: ISRG 16.1%, CMG 10.5%, HUM 9.0%, CHTR 7.9%, GOOGL 7.3%, AMZN 6.9%, GS 7.0%, BA 6.2%, AVGO 6.0%, AAPL 4.6%, GILD 0%.
- Census reuse candidates (`census-reuse-candidates.jsonl`, 11 rows): the only cohort name is AVGO 2024-07-15 (price ratio 0.101: the 10:1 split). No cohort name is renamed or reused. GOOG is a second share class, excluded by rule. No cohort root is among the 22 date-coverage alarms.
- Yahoo bars: spin-offs are labelled as splits for some names (DHR, GE, HON, CMCSA, DELL); no cohort name has an irregular ratio. HES is absent (404). Tested roots without bars: HES only.
- ADR basis: BIDU is an ADR; the option deliverable is the ADS; dividends none declared by Yahoo.
- Pull alarms are batch-level: attribution to a root is unknown for older ones; the 2026-10-18 crossed_quotes batch is quality-flagged, not repaired.

### 7. Acquisition plan (prepared, not run)

Prepared files in `$S/plan/`: `commands.txt`, `source-benchmark-qqq-daily.json`, `suite-daily-bars-files.json`, `source-theta-options-2026ytd.json`, `export_params-2026ytd.json`, `source-sec-submissions-draft.json`. The QQQ and Theta-2026 configs were checked through `resolve_knobs` in memory (valid with `as_of` set in the past); the transform imports from `children/index_options`.

Prerequisites (this shell): the env grep for alpaca, theta, fred, hf_, polygon, av_, alphavantage returned nothing; none is set. Credentials that exist as files: `/home/russell/.config/dskit/thetadata-creds.txt` (36 bytes; the Terminal reads it itself; the connector holds no secret), `/home/russell/.config/dskit/adr-0046.env` (export lines for `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_CALLBACK_URL`, `SCHWAB_TOKEN_PATH`; load with `set -a; . file; set +a`). Connector secret names: Alpaca `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` (overridable by `key_env` / `secret_env`), Hugging Face `HF_TOKEN` (optional, anonymous otherwise), `restapi` any name via `secret`, Schwab as above. Yahoo (`httpblobs`) and Theta need no env var; Cboe and FRED CSVs are keyless. Theta Terminal: running, REST on 127.0.0.1:25503 (`DEFAULT_BASE_URL`), terminal dir `/home/russell/data/thetadata/terminal`, driver `/home/russell/data/thetadata/driver`.

| Block | What | Destination root | Prerequisites and decisions | Size / time |
|---|---|---|---|---|
| A | Resume Theta `option_eod` backfill for the 177 unstarted roots with the existing driver (`start_all.sh`, then `chain.sh` tiers tested, index, core2, rest1000) | `/home/russell/data/thetadata/options-tested`, Parquet `parquet-v2/options-tested` | Terminal alive (yes); gates 377 GB and 41 GB free (pass); the crossed_quotes alarm is hard, so a restart is an owner acknowledgement (the 2026-10-20 handoff's one-time authorization covered the 2026-10-18 batch; a new one is not established); do not start a second pull | about 51-59 h for tested; `index`, `core2`, `rest1000` follow in `chain.sh` and are not needed for the studies here |
| B | QQQ daily bars (Yahoo `httpblobs`, same transform and window as the existing bars; source `benchmark-daily-bars-qqq`) via `register-source`, `acquire`, `validate`, `certify`, `publish` | `/home/russell/data/index_options/ob` (beside `benchmark-daily-bars`) | no key; set `as_of` to the run instant; `PYTHONPATH` includes `children/index_options` (transform lives in the sibling child; per the repo rule a stat-arb-owned source would need that transform graduated into dskit); network access to Yahoo not tested; validate/certify/publish are optional by precedent (the existing bar sources have none) | 1 entity, 0.6 s throttle: under a minute, about 0.3 MB |
| C | Theta `option_eod` 2026-01-02..2026-10-19 for the fifteen names, then export with `sealed_from` 2026-10-20 | new `/home/russell/data/thetadata/options-2026ytd`, `parquet-v2/options-2026ytd` | owner decision to unseal 2026; Terminal alive; run only when no other Theta pull is active (4 concurrent requests in total) | about 6.1M rows (fifteen), about 1.4M (three); about 1.2 h (17 min) at 5M rows/h; about 0.4 GB Parquet |
| D | Earnings dates, SEC EDGAR submissions per CIK via `httpblobs` | would be `ob` or a stat-arb root | draft only: CIKs not filled (not verified), User-Agent contact is the owner's choice, BIDU files 6-K so is not covered, older filings may sit in extra files, and a parse step to Item 2.02 dates does not exist (sweep, ADR). Cheaper: use chain-derived events | 14 requests; seconds |
| E | Signed order flow | none | no connector stream; needs an ADR and a new Theta stream; entitlements not verified | see gap table |

Not needed (already held): stock bars with dividends and splits for the twelve new names, a market-vol series. If a fresh bars pull were still wanted, the exact prior recipe is `children/index_options/configs/source-stock-daily.json` (and `-300`): `register-source stock-daily-bars --catalog-source stock-daily-bars --connector httpblobs --config @configs/source-stock-daily.json --activate --root /home/russell/data/index_options/ob`, then `acquire --source stock-daily-bars --stream files --mode backfill`, with `INDEX_OPTIONS_ROOT` and `STOCK_DAILY_RAW_ROOT` exported (cached raw responses re-derive without the network).

### 8. Could not determine

- What Theta Standard entitles beyond EOD (trade/quote history, earnings, stock EOD on the FREE stock plan); the Terminal was deliberately not called.
- Whether Yahoo or SEC are reachable from this sandbox.
- Whether `/mnt/c` (41 GB free) can absorb the VHD growth of the remaining pull (estimated 52-86 GB written to the data disk).
- Whether a new owner acknowledgement of the crossed_quotes alarm is given.
- Whether the Yahoo `events` data carries special dividends distinct from regular ones.
- Why `ChainEventDetector` event dates for the cohort (the H2 runs) are not on disk as a table; not searched beyond file names.

## Sources

- Scripts and outputs retained under the session scratchpad `data-inventory/` (parquet_inventory.json, parquet_symbols.json, raw_inventory.json, stock_inventory.json, stock_bars_cohort.json, market_inventory.json, vol_inventory.json, cohort_census.txt, cohort_nulls.json, trade_counts_cohort.json, plan/).
- `/home/russell/data/thetadata/{parquet-v2,options-tested,driver,terminal}`, `/home/russell/data/index_options/ob`, `dskit/onboarding/libs/thetadata.py`, ADR-0265 and ADR-0270..0274.
