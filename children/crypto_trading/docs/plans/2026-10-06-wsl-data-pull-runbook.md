# WSL runbook: crypto_trading stage A (data pulls) and stage B (features, kill test)

Pull the Kalshi, Binance, Coinbase and Deribit data through onboarding, then build the feature tables and the kill test.
Nothing has been pulled yet. **Done when** every pull was acquired and gated `pass` (`warn` is expected, section 5), `verify` is
clean and the runs exit 0. **Order:** 0 to 5; 7a to 7c (register, markets, COUNT the archive); 8; 7d (after the count); 7e (last, only if days of pulling are
accepted); B1 to B6.

Binance Vision is CC BY-NC-SA: research only. Needs about 10 GB free for everything except `trades-15m` (estimate; a pull stages
in `/tmp` first) and `trades-15m` alone about 50 GB (7e); keep the store on the WSL disk, not `/mnt/c`.

## 0. Environment: paste at the top of EVERY new shell

Acquires are journaled into `docs/decisioning/`, so run from this directory.

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

## 1. Set up, once

```bash
pip install -e "../..[dev]" -e ".[parquet,features]"     # dskit with pytest; the child with pyarrow and numpy
python -m pytest tests -q                               # offline, must pass
out=$(python -m pytest tests/test_binance_vision.py -q -rs) && ! grep -qi skip <<<"$out"   # gate: they RAN (a skip means no pyarrow)
python -m dskit.onboarding init --root "$OB"
```

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

Times are estimates (about 0.4 s a Kalshi request). A failed pull commits nothing: rerun it (stop WSL sleeping). `block` names
the tripped rule: amend the suite deliberately (a new hash), never to get green.

## 4. Live books (optional, any time)

One request per open market (seconds for the 15-minute pair, about 10 minutes for the hourly four). `watch` exits on the FIRST
error: run each recorder under a restart loop in `tmux` (a restart leaves a gap).

```bash
pull kalshi-crypto-books-15m    orderbooks suite-kalshi-crypto-books.json live   # prove each once
pull kalshi-crypto-books-hourly orderbooks suite-kalshi-crypto-books.json live
recorder() {   # recorder <source> <every-seconds>
  while true; do
    python -m dskit.onboarding watch --root "$OB" --source "$1" --stream orderbooks --mode live --every-seconds "$2"
    echo "$(date -u +%FT%TZ) watch exited $?" >&2; sleep 30
  done >> "$(dirname "$OB")/$1.log" 2>&1
}
recorder kalshi-crypto-books-15m 60 &       # about one pass a minute
recorder kalshi-crypto-books-hourly 900 &   # about one pass per 25 minutes
```

`captured_at` is a lower bound (`acquired_at` is the upper). The store is write-once, about 30 MB a day at 60 s.

## 5. Check, record, read it correctly

```bash
python -m dskit.onboarding verify --root "$OB"           # re-hash every snapshot; must be clean
git add docs/decisioning && git commit                   # the acquires journaled themselves
```

Digest spot check (ADR-0238 is withdrawn: this is the permanent check), both lines print the same hash:

```bash
python -c "import hashlib; from dskit.onboarding import payload_files as p; \
print(hashlib.sha256(p('$OB','binance-btcusdt-1m','files')['files']['_raw/2026-10-01.zip'].read_bytes()).hexdigest())"
curl -s https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-01.zip.CHECKSUM
```

Expected warnings (drop rows by name, never guess a value): 26 BVOL days the vendor does not publish (read `reason` in
`scan_stream(root, source, "files", key_fields=["entity"])`) and strike-less markets (`Target price: TBD`; over the full
KXBTC15M history 358 `markets-strike-type-set` and 2,816 `markets-expiration-value-present`).

**Never use a settled market's `yes_bid`, `yes_ask` or `last_price` as a feature** (after close they are about 0 or 1). Kalshi
candle `ts` is epoch SECONDS at the END of its minute; a Binance kline (epoch MS, USDT-quoted) is labelled by its START: use only
bars closed at decision time.

## 6. Not covered

Kraken has no source (`restwindow` can read it), no node reads Coinbase, and a one-second book recorder needs a WebSocket client
dskit lacks.

## 7. History: the settlement value, hourly candles, trades (`kalshi_history`)

Five sources over dskit's `kalshi_history` pack (ADR-0236): the realised `expiration_value` (a LABEL), markets from BEFORE the
live cutoff, hourly event candles and every trade.

**7a. Register, then 7b. Markets first: it is also the measurement** (a full re-pull of six series from both archives; its row
count prices 7d and 7e).

```bash
for s in kalshi-history-crypto kalshi-history-candles-hourly-btc kalshi-history-candles-hourly-eth \
         kalshi-history-trades-15m kalshi-history-trades-hourly; do
  python -m dskit.onboarding register-source $s --root "$OB" --catalog-source $s \
    --connector dskit.onboarding.libs.kalshi_history:KalshiHistoryConnector \
    --config @configs/source-$s.json --activate; done
pull kalshi-history-crypto markets suite-kalshi-history-markets.json
```

**7c. Count before you choose.** Candle and trade pulls cost a request chain per MARKET (no date bound) and a chain pages. Count
the archive per series, then how long each market was open (the 'hourly' series also list 25 h daily and 7 d weekly ladders,
which every pull pools):

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

(The archive lists about a day past the cutoff: that day counts as live.) Requests, at about 0.65 s each (measured 2026-10-07):

- Hourly candles: one per ARCHIVED hourly market, plus about 3 per live 1-hour event and 55 per live 25 h ladder.
- Hourly trades: `ceil(trades / 1000)` pages a market, never 1; about 0.9 s a market.
- 15-minute trades: about 15 requests, 14,955 trades and 9.5 to 12.3 s a market, the heaviest family (7e).

If the figure is days, do not start: a bounded pull needs a dskit knob (an ADR).

**7d. One pull at a time** (a failed pull commits nothing; rerun it), each only after its 7c figure is accepted:

```bash
pull kalshi-history-candles-hourly-btc candles suite-kalshi-history-candles.json
pull kalshi-history-candles-hourly-eth candles suite-kalshi-history-candles.json
pull kalshi-history-trades-hourly trades suite-kalshi-history-trades.json
python -m dskit.onboarding verify --root "$OB"
```

The label exists only from `settlement_ts`, minutes after the close: gate a join on it, never on the row's date. `volume` is a
whole-life total, never a feature; a trade is usable at decision time I only if `created_time` is before I.

**7e. The 15-minute trades: days, all or nothing, last.** 28,037 settled KXBTC15M markets and about as many KXETH15M, at 14,955
trades and 9.5 to 12.3 s a market (7c): about 6 to 8 days in ONE acquire, 0.84 billion rows, 52 GB plus staging in `/tmp`; a sleep,
network drop or full disk commits nothing. Start it only after the 7c count is accepted (no stage B document reads trades).

```bash
pull kalshi-history-trades-15m trades suite-kalshi-history-trades.json    # about 6 to 8 days, about 52 GB, all or nothing
python -m dskit.onboarding verify --root "$OB"
```

## 8. Coinbase and Deribit (`restwindow`)

Coinbase BTC-USD, ETH-USD and Deribit BTC, ETH DVOL at 1 minute (dskit's `restwindow`, ADR-0237). **Coinbase is the live-safe
alternative to Binance** (confirm its terms at go-live); no node reads it yet.

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

`start` is 2024-01-01 in each config. Coinbase `time` and Deribit `ts` are the START of the minute (use only bars that ended
before a decision). The suites cannot check for gaps: count rows per day (1440; the end days are partial):

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

One row per settled 15-minute market and lead (2, 5, 10 minutes before the close); does a simple fair value beat the market mid
on held-out rows after fees? **Done when** the run exits 0, `binary_score.md` exists and the table is acquired. Paste section 0 first.

## B1. Set up, once

Readers do not expand `~`: one command points the document at `$OB` (a test runs exactly this line):

```bash
mkdir -p ~/data/crypto_trading/features-15m             # where the run writes the table
sed -i "s#/home/russell/data/crypto_trading/ob#$OB#g" configs/run-features-15m.json
```

## B2. Check before the run

```bash
python -m dskit.pipeline validate configs/run-features-15m.json
python -m dskit.pipeline plan     configs/run-features-15m.json | head -30
python - <<'PY'    # peek at BVOL: its cadence and units are assumed, not verified
import os, pyarrow.parquet as pq
from dskit.onboarding import payload_files
got = payload_files(os.path.expanduser(os.environ["OB"]), "binance-btcbvol", "files")
rel = sorted(r for r in got["files"] if r.endswith(".parquet"))[-1]
print(rel, pq.read_metadata(got["files"][rel]).num_rows, "rows;", pq.read_table(got["files"][rel]).slice(0, 3).to_pylist())
PY
```

UNVERIFIED until you read that output: about one BVOL row a second (`max_bvol_age_ms`), a 365-day year (`seconds_per_year`).

## B3. Run

```bash
python -m dskit.pipeline run configs/run-features-15m.json --asof "$(date -u +%F)"
```

Exit 0 ran, 1 error (names the node), 3 halted; well under an hour (estimate). Read `artifacts/kill_test/binary_score.md`
(`development` only; calibration first) and what was dropped and why (`artifacts/{markets,decisions,spot}/`). "the store moved
since the manifest was fingerprinted": an acquire ran meanwhile, run again. "no longer matches the manifest": `verify` first.

## B4. Publish the table back through onboarding

A table a later run reads is a source, not a path; each run writes its OWN file, hence its own stream:

```bash
publish() {   # publish <source>: acquire every run's table (a stream already taken is a no-op), then verify
  for f in ~/data/crypto_trading/$1/decision_features-*.jsonl; do
    python -m dskit.onboarding acquire --root "$OB" --source "$1" --stream "$(basename "$f" .jsonl)" --mode backfill
  done
  python -m dskit.onboarding verify --root "$OB"
}
python -m dskit.onboarding register-source features-15m --root "$OB" --catalog-source features-15m \
    --connector localtables --config @configs/source-features-15m.json --activate   # once
publish features-15m
git add docs/decisioning && git commit                  # the run and the acquire journaled themselves
```

Never republish a changed table under an old stream name: the cursor makes the acquire a no-op and a read serves the OLD rows.

## B5. Read the held-out set once

- **The cut is decided before you look.** `development` ends 2026-09-15. Tune on `development` only; then add `"heldout"` to
  `kill_test.params.report_segments`, commit that edit (it changes the hash, so the read is recorded), run once, read it. Never
  move the cut or re-tune afterwards.
- Only a negative held-out Brier difference with a positive after-fee profit, each by a few block-clustered standard errors,
  survives; anything else means stop. Fills use the information-instant quote, so a surviving profit is an upper bound.

## B6. The hourly document

The same nodes over the hourly ladders, leads 5, 15, 30; knobs and cut as B5. Needs 7a, 7b, the two hourly candle pulls (7d) and
the section 3 `fee_schedules` and Binance pulls.

```bash
mkdir -p ~/data/crypto_trading/features-hourly         # where the run writes the table
sed -i "s#/home/russell/data/crypto_trading/ob#$OB#g" configs/run-features-hourly.json
python -m dskit.pipeline validate configs/run-features-hourly.json
python -m dskit.pipeline plan     configs/run-features-hourly.json | head -30
```

**Size it first: the engine keeps every node's rows in memory,** about 9.7 KB a row (measured 2026-10-07): the 61 live days alone
are about 6.4 M rows and 60 GB. Count, then compare with `free -g`:

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

If it fits, run it and publish the table with B4's `publish`:

```bash
python -m dskit.pipeline run configs/run-features-hourly.json --asof "$(date -u +%F)"
python -m dskit.onboarding register-source features-hourly --root "$OB" --catalog-source features-hourly \
    --connector localtables --config @configs/source-features-hourly.json --activate   # once
publish features-hourly
```

If not, run a SIZING copy (one series, one lead) and scale by what you dropped; a date bound needs a dskit knob (an ADR). It
rebuilds its own directory, so after any failure paste it again; never publish its table.

```bash
python - <<'PY'    # B6 sizing copy: one series, one lead, everything under its own directory
import json, os, shutil
size = os.path.expanduser("~/data/crypto_trading/features-hourly-sizing")
doc = json.load(open("configs/run-features-hourly.json"))
doc["name"] += "-sizing"
doc["pipeline"]["markets"]["params"]["series"] = ["KXBTCD"]
doc["pipeline"]["decisions"]["params"]["leads_minutes"] = [15]
doc["pipeline"]["write"]["params"]["path"] = size + "/decision_features-{run}.jsonl"
doc.setdefault("outputs", {})["run_root"] = size + "/runs"
shutil.rmtree(size, ignore_errors=True)
os.makedirs(size)
json.dump(doc, open(size + "/run.json", "w"), indent=1)
PY
python -m dskit.pipeline run ~/data/crypto_trading/features-hourly-sizing/run.json --asof "$(date -u +%F)"
```

B3 to B5 apply. Rows also carry the labels `settle_value` and `settlement_ms` (the cut is on `settlement_ms`); daily and weekly
ladders are pooled (7c counts them); most strikes have no two-sided quote: read the census before the pooled numbers.

## Known issues

- Size B6 before the full pull; the candle and trade pulls cost a chain per ARCHIVED market, with no date bound (7c, 7e).
- Not modelled: latency slippage, the Jensen gap of the 60-second average. The basis is noisy (a 1-minute bar's mean). Unverified:
  BVOL cadence, the 365-day year. The fee schedule is today's.
- `ObservationRows` and `scan_stream` do not expand `~` in `root`.
- Migrated to dskit's modules (ADR-0239 to 0244; `tests/test_migration_golden.py` pins every row and score): the 15-minute
  document hash and the four Binance declaration digests moved by design.
