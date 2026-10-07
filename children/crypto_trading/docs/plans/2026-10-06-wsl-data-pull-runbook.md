# WSL runbook: crypto_trading stage A (data pulls) and stage B (features, kill test)

**Problem.** Pull the inputs for a calibrated BTC/ETH distribution at each Kalshi
crypto contract's settlement (CF Benchmarks RTI, 60-second average): Kalshi
markets, fees, 15-minute candles and books, plus Binance Vision spot klines and
BVOL implied vol; sections 7 and 8 add the realised settlement value, hourly-ladder
candles and trades (`kalshi_history`), and Coinbase and Deribit (`restwindow`).
Everything enters through onboarding. Nothing has been pulled yet.

**Done when.** Every pull below was acquired and its suite gated `pass`
(`warn` is expected for the Binance BVOL missing days and for unset-strike
15-minute markets, see section 5), and `verify` is clean. The one exception is `trades-15m` (7e): days
and about 50 GB, so it is acquired only once its count gate (7c) is accepted.

**Two data sets.** Sections 2 to 4 are the 15-minute set: each strike's yes/no result, no
settlement value. Section 7 adds the realised value, history before the live cutoff, the hourly
ladders' candles and trades; section 8 adds Coinbase and Deribit.

**Order (WSL).** 1 set up; 2 register the nine sources; 3 the cheap history, cheapest first; 4 books
(optional, any time); 5 verify and read it correctly; 7a to 7c register `kalshi_history`, pull
its markets, COUNT the archive; 8 Coinbase and Deribit (independent of 7); 7d the hourly candles and trades,
only after the count; 7e the 15-minute trades, last and only if days of pulling are accepted;
stage B: B1 to B4 (15-minute), B6 (hourly).

Binance Vision is CC BY-NC-SA: research use only, not for live trading features.
Needs about 10 GB free for everything except `trades-15m` (an estimate; a pull stages in `/tmp` before the
store copy), and `trades-15m` alone about 50 GB (measured, 7e); keep the store on the WSL disk, not `/mnt/c`.

## 0. Environment: paste at the top of EVERY new shell

Every later step relies on these lines (they hold the one machine path, the
variable the configs name, and the `pull` helper). Nothing else carries state.

```bash
cd <worktree on branch claude/crypto-trading-child>/children/crypto_trading
export CRYPTO_TRADING_ROOT=$PWD                  # configs name this variable, never a path
export OB=/home/russell/data/crypto_trading/ob   # PLACEHOLDER: the owner picks the store root
pull() {   # pull <source> <stream> <suite> [mode]: acquire, print, validate its snapshot (none to validate on a no-op)
  out=$(python -m dskit.onboarding acquire --root "$OB" --source "$1" --stream "$2" --mode "${4:-backfill}") || return 1
  echo "$out"
  snap=$(echo "$out" | python -c 'import json,sys; print(json.load(sys.stdin)["snapshot"] or "")')
  [ -n "$snap" ] || { echo "no new snapshot (declaration unchanged): nothing to validate"; return 0; }
  python -m dskit.onboarding validate --root "$OB" --suite "configs/$3" --snapshot "$snap"
}
```

Run everything from that directory: acquires are journaled into `docs/decisioning/`.

## 1. Set up, once

```bash
pip install -e ../.. -e ".[parquet]"                    # dskit, then the child with pyarrow
python -m pytest tests -q                               # offline, must pass
! python -m pytest tests/test_binance_vision.py -q -rs | grep -i skip   # gate: transform tests RAN
python -m dskit.onboarding init --root "$OB"
```

The gate fails (exit 1) if pyarrow is missing and `importorskip` skipped the
transform tests: a green run with those skipped proves nothing about the pull.

## 2. Register the nine sources

```bash
for s in kalshi-crypto kalshi-crypto-candles-btc kalshi-crypto-candles-eth \
         kalshi-crypto-books-15m kalshi-crypto-books-hourly; do
  python -m dskit.onboarding register-source $s --root "$OB" --catalog-source $s \
    --connector kalshi --config @configs/source-$s.json --activate; done
for s in binance-btcusdt-1m binance-ethusdt-1m binance-btcbvol binance-ethbvol; do
  python -m dskit.onboarding register-source $s --root "$OB" --catalog-source $s \
    --connector httpblobs --config @configs/source-$s.json --activate; done
```

## 3. Pull and validate history, cheapest first

Times are estimates from the probe (about 0.4 s a Kalshi request), not measured pulls.
By hand, paste acquire's `"snapshot"` id as `--snapshot <snapshot-vid>`.

```bash
pull kalshi-crypto          fee_schedules suite-kalshi-crypto-fees.json     # ~1 min
pull kalshi-crypto          markets       suite-kalshi-crypto-markets.json  # ~20 min, ~1.2M rows
pull binance-btcusdt-1m     files         suite-binance-files.json          # ~10 min
pull binance-ethusdt-1m     files         suite-binance-files.json          # ~10 min
pull binance-btcbvol        files         suite-binance-files.json          # ~30 min, ~1.6 GB
pull binance-ethbvol        files         suite-binance-files.json          # ~30 min, ~1.6 GB
pull kalshi-crypto-candles-btc candles    suite-kalshi-crypto-candles.json  # ~1.25 h, ~6.4k requests
pull kalshi-crypto-candles-eth candles    suite-kalshi-crypto-candles.json  # ~1.25 h
```

- A failed pull commits nothing and keeps the old cursor: rerun it. An unchanged
  Binance declaration answers `"snapshot": null`, and `pull` then skips validate (the earlier
  snapshot already passed).
- Candles run last, one series per pull, so a failure costs about an hour; stop WSL sleeping.
- `block` on a Kalshi suite names the tripped rule. Do not edit the suite to get green;
  amend it deliberately (a new hash) if the venue changed its vocabulary.

## 4. Live books (optional, any time)

Two sources, because the cost differs by orders of magnitude. A pass is one request per open market:
the 15-minute pair is a few seconds, the four hourly series are about 1,400 books
(about 10 minutes, review count). `watch --every-seconds N` sleeps N AFTER a pass,
so the cadence is pass time plus N.

```bash
pull kalshi-crypto-books-15m    orderbooks suite-kalshi-crypto-books.json live   # prove each once
pull kalshi-crypto-books-hourly orderbooks suite-kalshi-crypto-books.json live
```

`watch` exits on the FIRST error (no retry loop hides a gap), so run each recorder
under a restart loop inside `tmux` (or a systemd user unit) and read its log:

```bash
LOGS="$(dirname "$OB")/logs"; mkdir -p "$LOGS"
recorder() {   # recorder <source> <every-seconds>
  while true; do
    python -m dskit.onboarding watch --root "$OB" --source "$1" --stream orderbooks \
      --mode live --every-seconds "$2"
    echo "$(date -u +%FT%TZ) watch exited $?; restarting in 30 s" >&2; sleep 30
  done >> "$LOGS/$1.log" 2>&1
}
recorder kalshi-crypto-books-15m 60 &       # about one pass a minute
recorder kalshi-crypto-books-hourly 900 &   # about one pass per 25 minutes
```

A restart leaves a gap in the series (nothing records while it is down): note it from the log.
Re-run `pull ... live` after an outage to confirm the source still validates.

**`captured_at` is a lower bound.** It is the pass start floored to the minute; the
snapshot's commit time (`acquired_at`) is the upper bound. A hourly book read at
minute 9 of a pass is stamped nine minutes early. The 15-minute source is accurate to
about a minute; per-row `observed_at` is what the `kalshi_history` `orderbooks` stream adds (these recorders keep `captured_at`).

**Disk growth.** Each pass commits a snapshot of about 5 small files (about 7 KB,
about 20 KB on disk, measured). At 60 s that is about 1,440 passes, 7k files and
30 MB a day; 25-minute hourly passes add about 58 snapshots a day plus their rows
(estimate: ~1,400 books of 0.35 to 2 KB each, gzip about 10x). The store is
write-once: pick a cadence you can keep, or stop the 15-minute recorder when done.

## 5. Check, record, and read it correctly

```bash
python -m dskit.onboarding verify --root "$OB"           # re-hash every snapshot; must be clean
git add docs/decisioning && git commit                   # the acquires journaled themselves
```

Digest spot check (child-side; ADR-0238 is withdrawn, so this manual check is the permanent one): the two lines must print the same hash.

```bash
python -c "import hashlib; from dskit.onboarding import payload_files as p; \
print(hashlib.sha256(p('$OB','binance-btcusdt-1m','files')['files']['_raw/2026-10-01.zip'].read_bytes()).hexdigest())"
curl -s https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-01.zip.CHECKSUM
```

Expected warnings, not failures:

- BVOL: 26 days the vendor does not publish (2023-09-25, 2023-10-24, 2024-06-11 to 06-12,
  2024-06-30, 2025-12-15 to 2026-01-04), the same for BTC and ETH, found by a HEAD sweep
  on 2026-10-06. Klines had none missing. Read `reason` in the inventory
  (`scan_stream(root, source, "files", key_fields=["entity"])`) before modelling across a gap.
- Markets: three settled KXBTC15M rows from 2026-08 have no strike (`Target price: TBD`) in the live `kalshi` pack. The
  `kalshi_history` pull (section 7) reads the archive too: over a full KXBTC15M history pull (28,037 markets)
  `markets-strike-type-set` warns on 358 rows (262 from 2025-12, no `floor_strike`) and `markets-expiration-value-present`
  on 2,816 (all `finalized`, no value). Drop them by name; never guess a strike.

Reading the data:

- **Never use a settled market's `yes_bid`, `yes_ask` or `last_price` as a feature.**
  After close they are post-settlement values (about 0 or 1), not what the market quoted.
  Prices for a feature come from candles or books at a lead time.
- **Time conventions differ.** Kalshi candle `ts` is epoch SECONDS at the END of its minute.
  Binance columns are epoch MILLISECONDS, and a kline is labelled by its bar START: it is
  known only after `close_time_ms`. Use only bars already closed at decision time.
- Klines are USDT-quoted: a proxy for, not a copy of, the USD BRTI Kalshi settles on.

## 6. Not covered

- Sections 2 to 4 store only each strike's yes/no result, which brackets the realised value;
  the value and history before the cutoff are section 7.
- Kraken: the `restwindow` pack can read it, but no source is configured here.
- A spot node that reads Coinbase: the shipped documents read Binance day-file parquet, and
  section 8 only acquires and validates the Coinbase candles.
- A one-second book recorder needs a WebSocket client; dskit has none.

## 7. History: the settlement value, hourly candles, trades (`kalshi_history`)

Five sources over dskit's `kalshi_history` pack (ADR-0236, named by import path, no credential). They add
what the `kalshi` pack lacks: `expiration_value` (the realised settlement value, a LABEL: the BRTI 60-second
average, the same on every strike of an event), `settlement_ts`, `volume`, markets from BEFORE the live cutoff
(served by the venue: 2026-08-07 when probed on 2026-10-07), event-level candles for the hourly ladders and every
trade. Paste the section 0 block first.

**7a. Register.**

```bash
for s in kalshi-history-crypto kalshi-history-candles-hourly-btc kalshi-history-candles-hourly-eth \
         kalshi-history-trades-15m kalshi-history-trades-hourly; do
  python -m dskit.onboarding register-source $s --root "$OB" --catalog-source $s \
    --connector dskit.onboarding.libs.kalshi_history:KalshiHistoryConnector \
    --config @configs/source-$s.json --activate; done
```

**7b. Markets first: it is also the measurement.** A full re-pull of the six series from both archives, 1000
rows a page; the row count tells you what 7d and 7e would cost.

```bash
pull kalshi-history-crypto markets suite-kalshi-history-markets.json
```

**7c. Count before you choose.** The candle and trade pulls cost one request chain per MARKET (the archive is
requested market by market and the pack has no date bound), and a chain pages: a liquid market's trades are many
requests, not one. Count the archive per series first:

```bash
CUT=$(curl -s https://external-api.kalshi.com/trade-api/v2/historical/cutoff | python -c 'import json,sys; print(json.load(sys.stdin)["market_settled_ts"])')
python - "$OB" "$CUT" <<'PY'    # census: archived (settled before the cutoff) and live markets per series
import collections, sys
from dskit.onboarding import scan_stream
root, cut = sys.argv[1], sys.argv[2]
rows = scan_stream(root, "kalshi-history-crypto", "markets", key_fields=["ticker"])
count = collections.Counter((r["series_ticker"], "archived" if r["settlement_ts"] and r["settlement_ts"] < cut else "live") for r in rows)
for key in sorted(count):
    print(*key, count[key])
PY
```

(The archive lists about a day past the cutoff, probed 2026-10-07; those markets count as live here, which mis-sizes the
census by that day only.) Then how long each market was open, because the four 'hourly' series also list daily ladders (open 25 h, closing at 17:00 New York time)
and weekly ones (open 7 d), and every pull and the hourly feature table pool all of them (measured 2026-10-07: 1 of 33 sampled
archived events was 25 h):

```bash
python - "$OB" <<'PY'    # census: how long each market was open, per series
import collections, datetime, sys
from dskit.onboarding import scan_stream
def minutes(row):
    try:
        a, b = (datetime.datetime.fromisoformat(row[k].replace("Z", "+00:00")) for k in ("open_time", "close_time"))
    except ValueError:
        return None
    return (b - a).total_seconds() / 60
def label(m):
    return "no_open_time" if m is None else "<=20min" if m <= 20 else "<=70min" if m <= 70 else "<=2days" if m <= 2880 else ">2days"
rows = scan_stream(sys.argv[1], "kalshi-history-crypto", "markets", key_fields=["ticker"])
count = collections.Counter((r["series_ticker"], label(minutes(r))) for r in rows)
for key in sorted(count):
    print(*key, count[key])
PY
```

Cost, in requests, then multiplied by about 0.65 s each (0.4 s probed plus `pace_s` 0.25):

- Hourly candles: one request per ARCHIVED hourly market, plus per live event a number of calls that grows with how long
  it was open, not with its strikes: about 3 for a 1-hour event, about 55 for a live 25 h ladder of 80 strikes (a call
  covers about 26 minutes of the window; measured 2026-10-07).
- Hourly trades: one chain per market, measured about 0.9 s a market. A chain is `ceil(trades / 1000)` pages, one request
  each, so a market's trades divided by the limit is the figure to use, never 1.
- 15-minute trades: a chain is about 15 requests. Measured 2026-10-07 over 40 markets (20 per asset, 24 archived and 16
  live): 598,198 trades, 492 s, so 14,955 trades and 12.3 s a market (a 12-market re-measure: 9.5 s). That is
  the heaviest family, not the lightest; 7e prices the whole pull.

If the figure is days, do not start: the pack has no date bound, so a bounded pull needs a new dskit knob (an ADR),
not child code. A failed or interrupted pull commits nothing.

**7d. Then, one pull at a time (a failed pull commits nothing; rerun it).** The hourly pulls, each only after its 7c
figure is accepted:

```bash
pull kalshi-history-candles-hourly-btc candles suite-kalshi-history-candles.json
pull kalshi-history-candles-hourly-eth candles suite-kalshi-history-candles.json
pull kalshi-history-trades-hourly trades suite-kalshi-history-trades.json
python -m dskit.onboarding verify --root "$OB"
```

Reading it correctly:

- **The label is known only at settlement.** A market row is dated at its close, but `result` and
  `expiration_value` exist only from `settlement_ts`, minutes later (probe: closed 23:00:00Z, settled
  23:02:52Z). Gate a join on `settlement_ts`, never on the row's date. The reader carries them as
  `settle_value` and `settlement_ms` (labels, never features) and excludes a market with no settlement
  instant by name (`no_settlement_ts`).
- `volume` is the whole-life total, known after the end: never a feature at a decision. A trade is usable at
  decision time I only if `created_time` is before I. A settled market's `yes_bid`, `yes_ask` and
  `last_price` are post-settlement values (B5).
- `warn` on the markets suite for a null `expiration_value` is read, not ignored: the yes/no result still labels
  the row, but the value is missing. An empty `settlement_ts` cannot be caught by a suite rule (see its notes).

**7e. The 15-minute trades: days, all or nothing, last.** `kalshi-history-trades-15m` is the heaviest pull here, not the
cheapest. The venue holds 28,037 settled KXBTC15M markets (measured by a full markets pull, live and archive, deduplicated)
and about as many KXETH15M. At the measured 14,955 trades and 9.5 to 12.3 s a market (7c), about 56,000 markets take about
6 to 8 days in ONE acquire, return about 0.84 billion rows and store about 52 GB (the 40-market slice stored 18.5 MB of
payload and 18.5 MB of observations per 598,198 rows), plus the staging copy in `/tmp`. Any sleep, network drop or full
disk commits nothing, so the days are lost. Start it only after 7c's count and these figures are accepted and the disk is
there; otherwise leave it out (no stage B document reads trades) until a dskit date bound exists (an ADR).

```bash
pull kalshi-history-trades-15m trades suite-kalshi-history-trades.json    # about 6 to 8 days, about 52 GB, all or nothing
python -m dskit.onboarding verify --root "$OB"
```

## 8. Spot and implied vol from other venues (`restwindow`): Coinbase, Deribit

Four sources over dskit's `restwindow` pack (ADR-0237, by import path, public endpoints, no credential):
Coinbase Exchange BTC-USD and ETH-USD 1-minute candles, and Deribit's BTC and ETH DVOL implied-vol index at 1
minute. **Coinbase is the live-safe alternative to Binance** for a spot input: a US venue, a constituent of the
BRTI Kalshi settles on, in USD; Binance's klines are USDT-quoted and CC BY-NC-SA (research only). Confirm
Coinbase's current market-data terms at go-live; this repo stores neither a licence text nor a credential. The
shipped documents still read Binance: the spot node reads day-file parquet and no Coinbase reader is built, so
these sources are acquired and validated, not yet read. Independent of section 7; paste the section 0 block first.

```bash
for s in coinbase-btcusd-1m coinbase-ethusd-1m deribit-btc-dvol deribit-eth-dvol; do
  python -m dskit.onboarding register-source $s --root "$OB" --catalog-source $s \
    --connector dskit.onboarding.libs.restwindow:RestWindowConnector \
    --config @configs/source-$s.json --activate; done
pull coinbase-btcusd-1m candles suite-coinbase-candles.json    # about 4,860 requests: under an hour (estimate)
pull coinbase-ethusd-1m candles suite-coinbase-candles.json
pull deribit-btc-dvol dvol suite-deribit-dvol.json             # about 2,020 requests: about 15 minutes (estimate)
pull deribit-eth-dvol dvol suite-deribit-dvol.json
python -m dskit.onboarding verify --root "$OB"
```

- `start` is 2024-01-01 in each config (a choice: edit it, with its notes, to widen); `max_windows` refuses before any
  request a span that outgrew it (about 235 days of headroom for Coinbase). A pull stops one minute before now (`lag`
  60), so the candle still forming is never stored, and a later pull continues from its checkpoint.
- **Time.** Coinbase `time` (epoch seconds) and Deribit `ts` (epoch ms) are the START of the minute: the bar is complete
  at start + 60 s, so use only bars that ended before a decision instant. `time_iso` and `ts_iso` are the ISO form.
- Deribit refuses, rather than stores, a window the vendor cut short (`result.continuation`). Coinbase answers HTTP 400
  above 300 granules a span (probed: 18000 s is 301 rows, 18060 s a 400); the window `step` is 299 granules, one under.
- The suites cannot check for gaps: count rows per day yourself (1440 expected; the first and last days are partial):

```bash
python - "$OB" coinbase-btcusd-1m candles time_iso <<'PY'
import collections, sys
from dskit.onboarding import scan_stream
root, source, stream, field = sys.argv[1:5]
days = collections.Counter(r[field][:10] for r in scan_stream(root, source, stream, key_fields=[field]))
short = {d: n for d, n in sorted(days.items()) if n != 1440}
print(len(days), "days;", len(short), "not 1440 rows:", dict(list(short.items())[:10]))
PY
```

---

# Stage B: feature table and kill test

**Problem.** From the stage A acquisitions build one row per settled 15-minute Kalshi market and
decision lead (2, 5 and 10 minutes before the close): the label, point-in-time spot and volatility,
the market's own quote, three fair values and Kalshi's taker fee, then ask the kill-test question:
does a simple fair value beat the market mid on held-out rows after fees? Only the 15-minute
series are read here; the hourly ladders have their own document, B6. Nothing here has run on real data yet.

**Done when.** The run exits 0, `binary_score.md` exists, the table is acquired as `features-15m` and
`verify` is clean. Paste the section 0 block into every new shell first (it sets `$OB`).

## B1. Set up, once

```bash
pip install -e ".[parquet,features]"                    # adds numpy beside pyarrow
python -m pytest tests -q                               # offline, must pass
mkdir -p ~/data/crypto_trading/features-15m             # where the run writes the table
```

The document's store root is `/home/russell/data/crypto_trading/ob`, the same placeholder as `$OB`
in section 0 (every reader names it; the readers do not expand `~`). If your `$OB` differs, rewrite all
of them with this one command and commit nothing else (a test runs exactly this line):

```bash
sed -i "s#/home/russell/data/crypto_trading/ob#$OB#g" configs/run-features-15m.json
```

## B2. Check before the run

```bash
python -m dskit.pipeline validate configs/run-features-15m.json
python -m dskit.pipeline plan     configs/run-features-15m.json | head -30
python - <<'PY'
import os, pyarrow.parquet as pq
from dskit.onboarding import payload_files
got = payload_files(os.path.expanduser(os.environ["OB"]), "binance-btcbvol", "files")
rel = sorted(r for r in got["files"] if r.endswith(".parquet"))[-1]
meta = pq.read_metadata(got["files"][rel])
print(rel, meta.num_rows, "rows;", pq.read_table(got["files"][rel]).slice(0, 3).to_pylist())
PY
```

The document assumes BVOL is published in percentage points (`index_value` near 50 for BTC, so
`bvol_scale` 0.01; confirmed on a real day in the 2026-10-06 review). UNVERIFIED until you read that
output: about one row a second (`max_bvol_age_ms` 60000 assumes it) and a 365-day year. If the cadence
or basis differs, edit `spot.params.max_bvol_age_ms` or `seconds_per_year` and say why in that node's
`notes` before running.

## B3. Run

```bash
python -m dskit.pipeline run configs/run-features-15m.json --asof "$(date -u +%F)"
```

Exit 0 ran, 1 error (the reason names the node), 3 halted. Time is an estimate, not measured: well
under an hour, since only the days around the decisions are read. The run directory prints at the
end. Open:

- `artifacts/kill_test/binary_score.md` (the numbers) and `binary_score.json` (every cell); the node key is
  still `kill_test`, the report files are dskit's `BucketedBinaryScore`'s. The shipped
  document reports the `development` segment ONLY (`kill_test.params.report_segments`): this run does
  not print the held-out numbers. Read the calibration table first: mean fair value minus the base rate
  per lead must be near zero before any edge means anything;
- what was dropped and why, kept as run-directory artifacts (`result.json` holds only shapes):
  `artifacts/markets/excluded.json` and `census.json` (every market dropped by ticker and reason:
  `no_strike` is the TBD target-price rows, then `not_settled`, `no_result`, and so on),
  `artifacts/decisions/excluded.json` (a lead before the open, or not strictly after the strike is
  known, or filled after the close) and `artifacts/spot/provenance.json` (per asset: the manifests
  read and the counts of missing spot, BVOL and basis, i.e. no usable strike anchor).

A refusal "the store moved since the manifest was fingerprinted" means an acquire ran between plan
and run: run again. "no longer matches the manifest sha256" means a stored file changed on disk:
`verify` the source before anything else.

## B4. Publish the table back through onboarding

A table a later run reads is a source, not a path (CLAUDE.md). Every run writes its OWN file,
`decision_features-<run>.jsonl` (`<run>` is the run directory's name: document, as-of, identity hash),
and each file is its own stream. Register once, acquire after every run:

```bash
python -m dskit.onboarding register-source features-15m --root "$OB" --catalog-source features-15m \
    --connector localtables --config @configs/source-features-15m.json --activate   # once
for f in ~/data/crypto_trading/features-15m/decision_features-*.jsonl; do
    s=$(basename "$f" .jsonl)
    python -m dskit.onboarding acquire --root "$OB" --source features-15m --stream "$s" --mode backfill
done                                                    # a stream already taken is a no-op
python -m dskit.onboarding verify --root "$OB"
git add docs/decisioning && git commit                  # the run and the acquire journaled themselves
```

Never publish a changed table under an old stream name: `localtables` advances on a `decision_ms` cursor,
so the acquire exits 0 with `"snapshot": null` and a read serves the OLD rows. A new configuration is a new
run, hence a new file and stream, and the old table stays readable. A later run reads one with
`ObservationRows` (source `features-15m`, stream `decision_features-<run>`, `key_fields`
`[ticker, lead_minutes]`, `ts_field` `decision_ms`, `ts_unit` `ms`); the rows also carry `run_id`.

## B5. Read the held-out set once, then read it correctly

- **The cut is decided before you look.** `development` ends 2026-09-15 and `heldout` starts there
  (`kill_test.params.segments`, integer epoch ms: 1789430400000 is 2026-09-15T00:00:00Z; cut on each market's
  CLOSE so a market's rows never straddle it; the hourly document cuts on `settlement_ms`, B6).
  Choose the vol window, leads and margin on `development` only. When you are done, add `"heldout"` to
  `kill_test.params.report_segments`, commit that edit (it changes the document hash, so the read is
  recorded), run once and read it. Never move the cut or re-tune afterwards. Three fair values are scored
  at once, so one winner of three is weaker evidence than it looks.
- **Timing** (enforced and tested). `decision_ms` is the information instant I: the spot is the Binance bar
  that closed before I, the quote is the candle that ended by I (the same minute), BVOL is strictly before
  I, and a strike anchor counts once known (open plus the lag). An order fills only at I plus `exec_lag_s`
  and the fair-value horizon runs from there. A quote a minute older than the spot made a zero-edge world
  look like a 6-standard-error win (`tests/test_zero_edge.py` pins that it no longer does). An old or absent
  reading is `None` with a `*_missing` flag, never a carried value. The fill is priced at the I quote, so
  latency slippage is NOT modelled: a profit that survives here is an upper bound.
- **Units.** Strikes are settlement-index (BRTI) dollars; Binance is on average a few bp higher. The
  `anchors` and `spot` nodes measure the basis at each market's open (the strike IS the index's average at
  that instant) and price against `spot_brti`. A large `basis_missing` count in the `spot` provenance means
  rows were dropped for lack of an anchor, not priced against the wrong unit.
- **Other proxies and caveats.** The basis comes from a 1-minute bar's mean, a proxy for a 60-second
  average, so it is noisy; BVOL is a 30-day implied vol used at a 15-minute horizon; the fee schedule is
  today's applied to history (`fee_schedule_retrieved` says which); this document's label is each
  strike's yes/no result (its source has no value; B6 carries the realised value). The standard errors cluster on 1-day blocks of close (per-event errors,
  also reported, are about half as large because volatility regimes persist). A negative held-out Brier
  difference with a positive after-fee profit, each by a few block-clustered standard errors, is the only
  reading that survives; anything else means stop.

## B6. The hourly document

`configs/run-features-hourly.json` is the same nodes over the hourly ladders (KXBTC, KXBTCD, KXETH, KXETHD): markets and the
realised value from `kalshi-history-crypto` (both archives), event-level candles from the two
`kalshi-history-candles-hourly-*` sources, fees from `kalshi-crypto`, spot and BVOL from the Binance tapes, and the 15-minute
series' strikes as the anchors of the index units. Needs sections 7a, 7b and the two hourly candle pulls (7d), the section 3 `fee_schedules` and Binance pulls, plus B1.
One row per settled hourly market and lead (5, 15, 30 minutes before the close); every modelling knob and the held-out cut are
the 15-minute document's, pinned equal by a test, so one cut serves both.

The sed is B1's one command for this document (a test runs exactly this line).

```bash
mkdir -p ~/data/crypto_trading/features-hourly         # where the run writes the table
sed -i "s#/home/russell/data/crypto_trading/ob#$OB#g" configs/run-features-hourly.json
python -m dskit.pipeline validate configs/run-features-hourly.json
python -m dskit.pipeline plan     configs/run-features-hourly.json | head -30
python -m dskit.pipeline run      configs/run-features-hourly.json --asof "$(date -u +%F)"
python -m dskit.onboarding register-source features-hourly --root "$OB" --catalog-source features-hourly \
    --connector localtables --config @configs/source-features-hourly.json --activate   # once
for f in ~/data/crypto_trading/features-hourly/decision_features-*.jsonl; do
    s=$(basename "$f" .jsonl)
    python -m dskit.onboarding acquire --root "$OB" --source features-hourly --stream "$s" --mode backfill
done
python -m dskit.onboarding verify --root "$OB"
```

Read B3 to B5 first; they apply unchanged. What differs:

- **Size it before you run it: the engine keeps every node's rows in memory.** Measured 2026-10-07 on a 2-hour slice (3,168
  markets, 23 s), peak memory is linear in decision rows, about 9.7 KB a row (3,168 rows 190 MB; 28,512 rows 410 MB; 186,912 rows
  1.8 GB); the table it writes is 1.5 KB a row and its `acquire` peaks near 7 KB a row. Rows are the settled hourly-series markets
  of 7b times 3 leads, and 1,464 of them close every hour (282 KXBTC and 282 KXBTCD per event, 450 KXETH and 450 KXETHD), so the 61
  live days alone are about 2.1 M markets, 6.4 M rows and 60 GB, before any archived market: more than a typical WSL host has.
  Count first and compare with `free -g`:

```bash
python - "$OB" <<'PY'    # B6 size: decision rows = settled hourly-series markets x leads
import sys
from dskit.onboarding import scan_stream
HOURLY, LEADS, RUN_KB, ACQUIRE_KB = {"KXBTC", "KXBTCD", "KXETH", "KXETHD"}, 3, 9.7, 7.0
rows = scan_stream(sys.argv[1], "kalshi-history-crypto", "markets", key_fields=["ticker"])
markets = sum(1 for r in rows if r["series_ticker"] in HOURLY and r["status"] in ("finalized", "settled") and r["settlement_ts"])
print(f"{markets} settled hourly markets x {LEADS} leads = {markets * LEADS} rows: "
      f"about {markets * LEADS * RUN_KB / 1e6:.1f} GB peak for the run, {markets * LEADS * ACQUIRE_KB / 1e6:.1f} GB for the acquire")
PY
```

  If it does not fit, do not run the shipped document. Run a SIZING copy over one series and one lead (its own run hash, its
  table in its own directory, so the `acquire` loop above never publishes it; read it for size and census, never as the result),
  then scale the figure by the series and leads you dropped. A date bound for the pulls and an open-length filter need a dskit
  knob (an ADR), and a row-count refusal before the run is the owner's call; none exists today.

```bash
python - /tmp/run-features-hourly-sizing.json <<'PY'    # one series, one lead; the roots are already rewritten above
import json, sys
doc = json.load(open("configs/run-features-hourly.json"))
doc["name"] += "-sizing"
doc["pipeline"]["markets"]["params"]["series"] = ["KXBTCD"]
doc["pipeline"]["decisions"]["params"]["leads_minutes"] = [15]
doc["pipeline"]["write"]["params"]["path"] = "~/data/crypto_trading/features-hourly-sizing/decision_features-{run}.jsonl"
json.dump(doc, open(sys.argv[1], "w"), indent=1)
PY
python -m dskit.pipeline run /tmp/run-features-hourly-sizing.json --asof "$(date -u +%F)"
```

- **The label has a value.** Each row carries `label` (the yes/no result), `settle_value` (the realised BRTI average, the
  same on every strike of an event) and `settlement_ms` (when it became known, after the close). They are labels: no feature
  reads them (a test changes every value and result and asserts no feature moves), and a later decision may use one only
  after its `settlement_ms`.
- **The cut is on the settlement instant.** `kill_test.settle_field` is `settlement_ms`, the instant the label became known
  (minutes after the close), so a market whose label was unknown at the cut is never in development. The cut is on the
  15-minute and hourly grids, so today no market straddles it (a test pins that); the 15-minute document has no
  settlement instant and cuts on the close.
- **An hourly strike is fixed at the open** (`strike_known_ms` is the open), and a 1-hour market is open for the hour before
  its close, so a lead of up to 59 minutes is a decision after it. The four series also hold daily (25 h) and weekly (7 d)
  ladders, pooled with the 1-hour events by the table and the kill test (7c's open-length census counts them; filtering on
  open duration needs an ADR). The spot basis comes from the latest 15-minute anchor known before the decision, at most
  15.5 minutes old.
- **Most strikes have no quote.** An event has about 190 strikes, most far from the money; a row without a two-sided quote
  is counted out by `two_sided` on the census, not dropped quietly. Read the census before the pooled numbers; the table is
  large (strikes x events x leads).
- Spot is still Binance (research only). Coinbase (section 8) is the live-safe alternative, not yet read by any node.

## Known issues (not fixed; B6 must be sized before the full pull)

- B6 holds every row in memory, about 9.7 KB a decision row (60 GB for the live days alone): size it, or run the sizing
  copy, before the document runs over the full pull.
- The candle and trade pulls (7d, 7e) cost one request chain per ARCHIVED market, a chain pages, and the pack has no date
  bound: 7c counts them first (the 15-minute trades are days, 7e), and a bounded pull needs a new dskit knob.

- Fills are priced at the quote of the information instant, so adverse selection during the execution lag
  is not modelled (see B5).
- The basis uses the mean of a 1-minute bar's open and close as a proxy for a 60-second average, so each
  basis is noisy; the strike anchors only exist for the 15-minute series.
- `ObservationRows` and `scan_stream` do not expand `~` in `root` (the reason the document carries an
  absolute path): a known dskit limitation the child works around; no dskit change is proposed.
- BVOL cadence (about one row a second) and the 365-day year are unverified; the fee schedule is today's.
- The held-out cut and margin are chosen, not derived; the fair value is not fitted and ignores the
  Jensen gap of the settlement average.

---

# Migration to dskit's modules (no behaviour change)

The child's interim copies were replaced by the dskit modules that now ship (ADR-0239 to 0244): the Binance zip
transform (`libs.zipcsv`, layouts in each source's `transform_params`), day-file series and `StreamManifests`
(`libs.parquet_series`), vol estimators (`libs.vol_estimators`), fair value, payoffs and scoring
(`binary_pricing`, `binary_scoring`), the per-run writer (`kinds_run_write`) and fee mechanics (`fee_mechanics`;
the Kalshi fee-type mapping stays in this child, the `fees` node's `fee_types`). Over the synthetic store every
pre-migration row, column and score is reproduced bit for bit (`tests/test_migration_golden.py`).

Two things moved by design, and only these:

| What | Before | After |
|---|---|---|
| `configs/run-features-15m.json` identity hash (placeholder root) | `ffd37f874df0` | `8f4488329122` |
| `source-binance-btcusdt-1m` declaration digest | `5355b1fcee22` | `23f78b4b6d23` |
| `source-binance-ethusdt-1m` declaration digest | `fc6190d734a5` | `b01ce9b6964b` |
| `source-binance-btcbvol` declaration digest | `bd6d11bb359a` | `7e9de1f14b82` |
| `source-binance-ethbvol` declaration digest | `8aa384b20d96` | `4d17b2842088` |

The run hash moved because the nodes, their params (`vol_column_prefix`, `fee_types`, the scorer's names and
epoch-ms segments) and the fair-value lag are now declared; it names the run directory and keys the `$prev` series, so
a run of the old document is a different series. The digests moved because each Binance source now names dskit's
transform and carries its layout (digest = url, dates, as_of, headers, transform and its params): an unchanged
declaration is a no-op and a changed one re-pulls. Nothing had been pulled when this landed, so the "re-pull" costs
nothing; anyone who pulled earlier re-acquires all four sources (the files are the same; the snapshot ids are new).
The report files are renamed `binary_score.{md,json}`, the three fair-value columns gain `<fair>_tau` (the horizon the
node priced, equal to `tau_s`), and the scorer's score keys say `model` and `market` where they said `fair` and `mid`.
