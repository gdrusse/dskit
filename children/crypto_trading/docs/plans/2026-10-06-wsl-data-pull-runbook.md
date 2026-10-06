# WSL runbook: crypto_trading stage A (data pulls) and stage B (features, kill test)

**Problem.** Pull the inputs for a calibrated BTC/ETH distribution at each Kalshi
crypto contract's settlement (CF Benchmarks RTI, 60-second average): Kalshi
markets, fees, 15-minute candles and books, plus Binance Vision spot klines and
BVOL implied vol. Everything enters through onboarding. Nothing has been pulled yet.

**Done when.** Every pull below was acquired and its suite gated `pass`
(`warn` is expected for the Binance BVOL missing days and for unset-strike
15-minute markets, see section 5), and `verify` is clean.

**Not a finished data set.** Stage A stores no realised settlement value (the label)
and no hourly-ladder candles: both wait on PROPOSED ADR-0236 (section 6).

Binance Vision is CC BY-NC-SA: research use only, not for live trading features.
Needs about 10 GB free (a pull stages in `/tmp` before the store copy); keep the
store on the WSL disk, not `/mnt/c`.

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
about a minute; per-row `observed_at` is proposed in ADR-0236.

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

Interim digest spot check (until ADR-0238): the two lines must print the same hash.

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
- Markets: three settled KXBTC15M rows from 2026-08 have no strike (`Target price: TBD`).
  Drop them by name; never guess a strike.

Reading the data:

- **Never use a settled market's `yes_bid`, `yes_ask` or `last_price` as a feature.**
  After close they are post-settlement values (about 0 or 1), not what the market quoted.
  Prices for a feature come from candles or books at a lead time.
- **Time conventions differ.** Kalshi candle `ts` is epoch SECONDS at the END of its minute.
  Binance columns are epoch MILLISECONDS, and a kline is labelled by its bar START: it is
  known only after `close_time_ms`. Use only bars already closed at decision time.
- Klines are USDT-quoted: a proxy for, not a copy of, the USD BRTI Kalshi settles on.

## 6. Not covered yet

- **The label.** The realised settlement value (`expiration_value`) and `settlement_ts` are
  not stored; only each strike's yes/no result is, which brackets the value. ADR-0236 adds
  them and gates the model.
- Hourly-ladder candles (the event-level endpoint, about 25k requests) and Kalshi history
  before the 2026-08-07 cutoff: ADR-0236.
- Coinbase, Deribit and Kraken history: ADR-0237.
- A one-second book recorder needs a WebSocket client; dskit has none.

---

# Stage B: feature table and kill test

**Problem.** From the stage A acquisitions build one row per settled 15-minute Kalshi market and
decision lead (2, 5 and 10 minutes before the close): the label, point-in-time spot and volatility,
the market's own quote, three fair values and Kalshi's taker fee, then ask the kill-test question:
does a simple fair value beat the market mid on held-out rows after fees? Only the 15-minute
series have candles; hourly ladders wait on PROPOSED ADR-0236 and plug in by config (see the notes
of `configs/run-features-15m.json`). Nothing here has run on real data yet.

**Done when.** The run exits 0, `kill_test.md` exists, the table is acquired as `features-15m` and
`verify` is clean. Paste the section 0 block into every new shell first (it sets `$OB`).

## B1. Set up, once

```bash
pip install -e ".[parquet,features]"                    # adds numpy beside pyarrow
python -m pytest tests -q                               # offline, must pass
mkdir -p ~/data/crypto_trading/features-15m             # where the run writes the table
```

The document's store root is `~/data/crypto_trading/ob` (every reader names it, `~` expands). If
your `$OB` differs, change all of them at once and commit nothing else:

```bash
grep -c '"root"' configs/run-features-15m.json          # how many nodes carry a root
sed -i "s#~/data/crypto_trading/ob#$OB#g" configs/run-features-15m.json
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

- `artifacts/kill_test/kill_test.md` (the numbers) and `kill_test.json` (every cell). The shipped
  document reports the `development` segment ONLY (`kill_test.params.report_segments`): this run does
  not print the held-out numbers. Read the calibration table first: mean fair value minus the base rate
  per lead must be near zero before any edge means anything;
- `result.json` for each node's state. The `spot` node's `provenance` counts missing spot and BVOL
  readings and missing basis (no usable strike anchor) per asset; the `markets` node's `excluded` and
  `census` name every market dropped (`no_strike` is the TBD target-price rows, then `not_settled`,
  `no_result`, and so on); the `decisions` node's `excluded` lists leads at or above the market's
  duration minus the strike lag (`strike_not_known`).

A refusal "the store moved since the manifest was fingerprinted" means an acquire ran between plan
and run: run again. "no longer matches the manifest sha256" means a stored file changed on disk:
`verify` the source before anything else.

## B4. Publish the table back through onboarding

A table a later run reads is a source, not a path (CLAUDE.md). Register once, acquire after every run:

```bash
python -m dskit.onboarding register-source features-15m --root "$OB" --catalog-source features-15m \
    --connector localtables --config @configs/source-features-15m.json --activate
python -m dskit.onboarding acquire --root "$OB" --source features-15m \
    --stream decision_features --mode backfill
python -m dskit.onboarding verify --root "$OB"
git add docs/decisioning && git commit                  # the run and the acquire journaled themselves
```

An unchanged table makes no new snapshot. A later run reads it with `ObservationRows` (source
`features-15m`, stream `decision_features`, `key_fields` `[ticker, lead_minutes]`, `ts_field`
`decision_ms`, `ts_unit` `ms`).

## B5. Read the held-out set once, then read it correctly

- **The cut is decided before you look.** `development` ends 2026-09-15 and `heldout` starts there
  (`kill_test.params.segments`, cut on each market's CLOSE so a market's rows never straddle it).
  Choose the vol window, leads and margin on `development` only. When you are done, add `"heldout"` to
  `kill_test.params.report_segments`, commit that edit (it changes the document hash, so the read is
  recorded), run once and read it. Never move the cut or re-tune afterwards. Three fair values are scored
  at once, so one winner of three is weaker evidence than it looks.
- **Leak rules** (enforced and tested): a Binance bar counts only after its `close_time_ms`; BVOL and a
  Kalshi candle only strictly before the decision (a candle's `end_period_ts` is the inclusive last second
  of its minute); a strike anchor only once known (open plus the lag). An old or absent reading is `None`
  with a `*_missing` flag, never a carried value.
- **Units.** Strikes are settlement-index (BRTI) dollars; Binance is on average a few bp higher. The
  `anchors` and `spot` nodes measure the basis at each market's open (the strike IS the index's average at
  that instant) and price against `spot_brti`. A large `basis_missing` count in the `spot` provenance means
  rows were dropped for lack of an anchor, not priced against the wrong unit.
- **Other proxies and caveats.** The basis comes from a 1-minute bar's mean, a proxy for a 60-second
  average, so it is noisy; BVOL is a 30-day implied vol used at a 15-minute horizon; the fee schedule is
  today's applied to history (`fee_schedule_retrieved` says which); the label is each strike's yes/no
  result, not the realised value. A negative held-out Brier difference with a positive after-fee profit,
  each by a few cluster-robust standard errors, is the only reading that survives; anything else means stop.

Not covered yet: the realised settlement value and every hourly series (ADR-0236), history before
2026-08-07 (ADR-0236), Coinbase, Deribit and Kraken (ADR-0237).
