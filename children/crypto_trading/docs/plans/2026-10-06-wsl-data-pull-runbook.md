# WSL data-pull runbook: crypto_trading stage A

**Problem.** Pull the inputs for a calibrated BTC/ETH distribution at each Kalshi
crypto contract's settlement (CF Benchmarks RTI, 60-second average): Kalshi
markets, fees, 15-minute candles and books, plus Binance Vision spot klines and
BVOL implied vol. Everything enters through onboarding. Nothing has been pulled yet.

**Done when.** Every row of the table below was acquired and its suite gated
`pass` (Binance may `warn` for a day Binance lacks), and `verify` is clean.

Binance Vision is CC BY-NC-SA: research use only, not for live trading features.
Needs about 10 GB free (a pull stages in `/tmp` before the store copy); keep the
store on the WSL disk, not `/mnt/c`.

## 1. Set up, once

```bash
cd <worktree on branch claude/crypto-trading-child>
pip install -e . -e "children/crypto_trading[parquet]"   # dskit, the child, pyarrow
cd children/crypto_trading                  # run everything from here: acquires are journaled
python -m pytest tests -q                   # offline, must pass first
export CRYPTO_TRADING_ROOT=$PWD             # the configs name this variable, never a path
OB=/home/russell/data/crypto_trading/ob     # the store: the one machine path
python -m dskit.onboarding init --root $OB
```

## 2. Register the six sources

```bash
for s in kalshi-crypto kalshi-crypto-candles; do
  python -m dskit.onboarding register-source $s --root $OB --catalog-source $s \
    --connector kalshi --config @configs/source-$s.json --activate; done
for s in binance-btcusdt-1m binance-ethusdt-1m binance-btcbvol binance-ethbvol; do
  python -m dskit.onboarding register-source $s --root $OB --catalog-source $s \
    --connector httpblobs --config @configs/source-$s.json --activate; done
```

## 3. Pull and validate, cheapest first

Times are estimates from the probe (about 0.4 s a Kalshi request), not measured pulls.

`pull` acquires, prints the result, and feeds acquire's `"snapshot"` id to
`validate`. Done by hand, paste that id as `--snapshot <snapshot-vid>`.

```bash
pull() {   # pull <source> <stream> <suite> [mode]
  out=$(python -m dskit.onboarding acquire --root $OB --source "$1" --stream "$2" --mode "${4:-backfill}") || return 1
  echo "$out"
  python -m dskit.onboarding validate --root $OB --suite "configs/$3" \
    --snapshot "$(echo "$out" | python -c 'import json,sys; print(json.load(sys.stdin)["snapshot"])')"
}
pull kalshi-crypto          fee_schedules suite-kalshi-crypto-fees.json     # ~1 min
pull kalshi-crypto          markets       suite-kalshi-crypto-markets.json  # ~20 min, ~1.2M rows
pull binance-btcusdt-1m     files         suite-binance-files.json          # ~10 min
pull binance-ethusdt-1m     files         suite-binance-files.json          # ~10 min
pull binance-btcbvol        files         suite-binance-files.json          # ~30 min, ~1.6 GB
pull binance-ethbvol        files         suite-binance-files.json          # ~30 min, ~1.6 GB
pull kalshi-crypto-candles  candles       suite-kalshi-crypto-candles.json  # ~2.5 h, ~13k requests
```

- A failed pull commits nothing and keeps the old cursor: rerun it. An unchanged
  Binance declaration answers `"snapshot": null` (nothing new).
- Candles run last and long; stop WSL sleeping. 429s are retried with backoff.
- A Binance `warn` means a refused day: read `reason` in the inventory
  (`scan_stream(root, source, "files", key_fields=["entity"])`) before modelling across it.
- `block` on a Kalshi suite names the tripped rule; do not edit the suite to get green.
  Amend it deliberately (a new hash) if the venue changed its vocabulary.

## 4. Live books (optional, any time)

One pass is a request per open market (about 300 each for KXBTC and KXBTCD at the
probe, so several minutes for six series), so poll in minutes, not seconds:

```bash
pull kalshi-crypto orderbooks suite-kalshi-crypto-books.json live            # once
python -m dskit.onboarding watch --root $OB --source kalshi-crypto \
  --stream orderbooks --mode live --every-seconds 900                        # recorder
```

## 5. Check and record

```bash
python -m dskit.onboarding verify --root $OB             # re-hash every snapshot; must be clean
git add docs/decisioning && git commit                   # the acquires journaled themselves
```

Interim digest spot check (until ADR-0238): the store keeps each vendor zip and
its `raw_sha256`. For one day, the two lines below must print the same hash:

```bash
python -c "import hashlib; from dskit.onboarding import payload_files as p; \
print(hashlib.sha256(p('$OB','binance-btcusdt-1m','files')['files']['_raw/2026-10-01.zip'].read_bytes()).hexdigest())"
curl -s https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-01.zip.CHECKSUM
```

## Not covered yet

- Kalshi history before the 2026-08-07 cutoff, trades with taker side, and candles for
  the four hourly ladders (up to about 1.2M requests): wait on ADR-0236.
- Coinbase, Deribit and Kraken history: wait on ADR-0237.
- A one-second book recorder needs a WebSocket client; dskit has none.
