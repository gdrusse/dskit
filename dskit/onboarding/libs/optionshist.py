"""End-of-day option chains from the ``options-dataset-hist`` Parquet archive (ADR-0182 amendment).

Philipp D. Dubach's MIT-licensed archive holds one row per option contract
per trading day for SPY, QQQ and IWM, 2008-2025, as yearly Parquet files,
with each ticker's daily closes beside them::

    <path>/<symbol lower>/options_<YYYY>.parquet
    <path>/<symbol lower>/underlying_prices.parquet

This pack maps it onto the ``option_chain`` stream the Cboe pack defines
(:data:`~dskit.onboarding.libs.cboe.CHAIN_FIELDS`, keyed
``(option, quote_time)``), so recorded Cboe chains and this history are
read by one reader. The mapping:

- ``option`` = ``contract_id`` (already OCC); ``root``, ``expiry``,
  ``right`` and ``strike`` come from :func:`~dskit.onboarding.libs.cboe.parse_occ`,
  never from the archive's own columns. Those are CROSS-CHECKED instead:
  ``expiration`` and ``type`` must agree exactly, ``strike`` within
  :data:`STRIKE_TOLERANCE` (the column rounds an adjusted strike such as
  ``259.779`` to cents), and ``symbol`` must be the directory's ticker.
  A disagreement refuses the pull, naming the file and row.
- ``underlying`` = ``symbol``; ``iv`` = ``implied_volatility`` (a decimal
  fraction, as Cboe's); ``last_trade_price`` = ``last``; ``bid``, ``ask``,
  their sizes, ``volume``, ``open_interest`` and the greeks by name, as
  the archive sends them. ``last_trade_time`` is None — the archive has
  no trade clock. ``mark``, ``rho`` and ``in_the_money`` are not read; the
  latter is known to be wrong in the archive.
- ``underlying_price`` = that ticker's ``close`` on the row's ``date``
  from ``underlying_prices.parquet``; None when the file has no such date
  (SPY 2024-01-15, a holiday carrying two stray rows, is the one case in
  the published archive), and a LOG message names every such date.
- ``quote_time`` = the row's ``date`` at 16:00 America/New_York — the
  archive's declared snapshot, the market close — converted to UTC
  (``YYYY-MM-DDTHH:MM:SS+00:00``, 21:00 under EST and 20:00 under EDT),
  and the RECORD's ``effective_date``. Early-close sessions (13:00) are
  still stamped 16:00: the archive declares one snapshot time.
  Numeric values pass through the Cboe pack's own coercion, so a
  missing, NaN or infinite value is None.

**Clocks.** ``effective_date`` is the market instant the row describes.
The archive was published long after it, so the platform's
``acquired_at`` (the commit instant of the pull) is the row's
knowledge time; nothing here claims the quote was available to a
trader in real time from this source.

**Provenance, fail closed.** ``source_url`` and ``source_commit`` (a
40-hex git commit) name the published archive, and ``files`` pins every
Parquet file under each declared ticker's directory by sha256. A file on
disk that is not pinned, a pinned file that is missing, and a pin
outside the declared tickers each refuse at ``check`` and ``read``; a
file is hashed before any row of it is read, and a mismatch refuses.
Every pull logs the archive URL and commit. A missing required column
(:data:`OPTION_COLUMNS`, :data:`UNDERLYING_COLUMNS`) refuses before any
row is emitted; so does a repeated ``(contract_id, date)``, a repeated
close date, a row dated outside its file's year, or a non-finite close.

**Cursor.** State is ``{"option_chain": {"cursor": <max quote_time
emitted>}}``; a pull emits only days whose close is strictly after it,
in ``(quote_time, underlying, option)`` order, and whole days only, so a
cursor never splits a day. ``max_days`` bounds a pull to that many
trading days (the union across tickers): repeated backfill pulls walk the
archive forward in bounded chunks. A year is skipped unopened once the
cursor reaches its December 31 close; a year whose last session is earlier
is re-opened (hashed, its dates read) but emits nothing. The logic is
identical in both modes (the platform keys the cursors apart, ADR-0014). One cursor spans
every ticker, so a ticker added to an already-walked source would be
skipped before the cursor — register a new source to add one.

Knobs (default-deny, per ``spec()``): ``path``, ``symbols``, ``files``,
``source_url``, ``source_commit`` (all required) and ``max_days``.

Import cost: stdlib. pyarrow is imported inside the verbs (the
``parquet`` extra).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ..base import MODES, AssetError, _raise_if, parse_utc
from ..connector import PROTOCOL, Connector
from .cboe import CHAIN_FIELDS, CHAIN_KEY_FIELDS, CHAIN_STREAM, QUOTE_TZ, parse_occ
from .kalshi import _finite
from .localtables import _pyarrow

__all__ = [
    "OPTION_COLUMNS",
    "SNAPSHOT_TIME",
    "STRIKE_TOLERANCE",
    "UNDERLYING_COLUMNS",
    "OptionsHistConnector",
]

#: The archive columns the mapping reads — a file lacking any refuses.
OPTION_COLUMNS = (
    "contract_id", "symbol", "expiration", "strike", "type", "last", "bid",
    "bid_size", "ask", "ask_size", "volume", "open_interest", "date",
    "implied_volatility", "delta", "gamma", "theta", "vega",
)
#: The ``underlying_prices.parquet`` columns the mapping reads.
UNDERLYING_COLUMNS = ("symbol", "date", "close")

#: The archive's declared snapshot: the market close, New York wall clock.
SNAPSHOT_TIME = time(16, 0)

#: Largest |OCC strike - ``strike`` column| accepted: the column rounds to cents.
STRIKE_TOLERANCE = 0.005

_UNDERLYING_FILE = "underlying_prices.parquet"
_OPTIONS_FILE = re.compile(r"^options_(\d{4})\.parquet\Z")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]*\Z")
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"^[0-9a-f]{40}\Z")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_RIGHT_OF_TYPE = {"call": "call", "put": "put"}

#: Pairs of (chain field, archive column) copied through ``_finite``.
_NUMERIC = (
    ("bid", "bid"), ("bid_size", "bid_size"), ("ask", "ask"),
    ("ask_size", "ask_size"), ("iv", "implied_volatility"),
    ("open_interest", "open_interest"), ("volume", "volume"),
    ("delta", "delta"), ("gamma", "gamma"), ("vega", "vega"),
    ("theta", "theta"), ("last_trade_price", "last"),
)


def _close_instant(day):
    """Return the aware UTC instant of ``day``'s 16:00 New York close."""
    local = datetime.combine(day, SNAPSHOT_TIME, tzinfo=ZoneInfo(QUOTE_TZ))
    return local.astimezone(timezone.utc)


def _parse_day(value, where):
    """Return the ``date`` of an archive ``YYYY-MM-DD`` string; refuse anything else."""
    if not isinstance(value, str) or not _DAY.match(value):
        raise AssetError([f"{where}: date must be 'YYYY-MM-DD', got {value!r}"])
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AssetError([f"{where}: date {value!r} names no day: {exc}"]) from exc


def _sha256(path):
    """Return the hex sha256 of the file at ``path``, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class OptionsHistConnector(Connector):
    """The ``options-dataset-hist`` archive as the ``option_chain`` stream.

    Stateless like every connector; see the module docstring for the
    mapping, the clock, provenance pinning and the cursor.

    Examples
    --------
    Walk a local copy of the archive forward one quarter per pull::

        conn = OptionsHistConnector()
        config = {"path": "~/data/options_archives/philippdubach_full",
                  "symbols": ["SPY"], "files": {...},  # relpath -> sha256
                  "source_url": "https://github.com/...", "source_commit": "37f6...",
                  "max_days": 63}
        msgs = list(conn.read(config, ["option_chain"], {}, "backfill"))
        (msgs[0]["type"], msgs[-1]["type"])
        # -> ('SCHEMA', 'STATE')
    """

    def spec(self):
        """Declare the default-deny archive configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "path": {
                "required": True,
                "notes": "Archive root: <symbol lower>/options_<YYYY>.parquet and "
                         "<symbol lower>/underlying_prices.parquet beneath it.",
            },
            "symbols": {
                "required": True,
                "notes": "Non-empty list of distinct upper-case tickers (SPY, QQQ, IWM); "
                         "each names the lower-case directory it is read from.",
            },
            "files": {
                "required": True,
                "notes": "Provenance pin: {'<symbol lower>/<file>.parquet': sha256 hex} for "
                         "EVERY parquet file under each declared ticker; an unpinned, "
                         "missing or changed file refuses.",
            },
            "source_url": {
                "required": True,
                "notes": "The http(s) URL the archive was obtained from (logged each pull).",
            },
            "source_commit": {
                "required": True,
                "notes": "The 40-hex git commit of that repository the files are from.",
            },
            "max_days": {
                "notes": "Optional int >= 1: at most this many trading days (across "
                         "tickers) per pull; absent emits every day after the cursor.",
            },
        }}

    # -- knobs and files ----------------------------------------------------

    def resolve_knobs(self, config):
        """Validate config values and normalize them.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are removed.

        Returns
        -------
        dict
            ``path`` absolute, ``symbols`` a list, ``files`` a dict,
            ``max_days`` None when absent.

        Raises
        ------
        AssetError
            Listing every malformed value.
        """
        if not isinstance(config, dict):
            raise AssetError([f"config must be a dict, got {type(config).__name__}"])
        problems = []
        path = config.get("path")
        if not isinstance(path, str) or not path:
            problems.append(f"config.path must be a non-empty string, got {path!r}")
        symbols = config.get("symbols")
        if (not isinstance(symbols, list) or not symbols
                or not all(isinstance(s, str) and _SYMBOL.match(s) for s in symbols)
                or len(set(symbols)) != len(symbols)):
            problems.append(
                f"config.symbols must be a non-empty list of distinct upper-case "
                f"tickers, got {symbols!r}"
            )
            symbols = None
        files = config.get("files")
        if not isinstance(files, dict) or not files:
            problems.append(
                f"config.files must be a non-empty {{relpath: sha256}} dict, got {files!r}"
            )
        else:
            for relpath, digest in sorted(files.items()):
                parts = relpath.split("/") if isinstance(relpath, str) else []
                if (len(parts) != 2 or not parts[0] or parts[0] in (".", "..")
                        or not (parts[1] == _UNDERLYING_FILE
                                or _OPTIONS_FILE.match(parts[1]))):
                    problems.append(
                        f"config.files key {relpath!r} must be '<symbol>/options_<YYYY>"
                        f".parquet' or '<symbol>/{_UNDERLYING_FILE}'"
                    )
                elif not isinstance(digest, str) or not _SHA256.match(digest):
                    problems.append(
                        f"config.files[{relpath!r}] must be a lower-case sha256 hex "
                        f"digest, got {digest!r}"
                    )
                elif symbols is not None and parts[0] not in {s.lower() for s in symbols}:
                    problems.append(
                        f"config.files pins {relpath!r} outside the declared symbols "
                        f"{symbols!r}"
                    )
        url = config.get("source_url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")) \
                or len(url) <= len("https://"):
            problems.append(f"config.source_url must be an http(s) URL, got {url!r}")
        commit = config.get("source_commit")
        if not isinstance(commit, str) or not _COMMIT.match(commit):
            problems.append(
                f"config.source_commit must be a 40-hex git commit, got {commit!r}"
            )
        max_days = config.get("max_days")
        if max_days is not None and (
            isinstance(max_days, bool) or not isinstance(max_days, int) or max_days < 1
        ):
            problems.append(f"config.max_days must be an int >= 1, got {max_days!r}")
        _raise_if(problems)
        return {
            "path": os.path.abspath(os.path.expanduser(path)),
            "symbols": list(symbols),
            "files": dict(files),
            "source_url": url,
            "source_commit": commit,
            "max_days": max_days,
        }

    def _layout(self, knobs):
        """Return ``{symbol: ({year: relpath}, underlying relpath)}``; refuse any pin gap."""
        base = knobs["path"]
        if not os.path.isdir(base):
            raise AssetError([f"config.path is not a directory: {base!r}"])
        problems, out = [], {}
        on_disk = set()
        for symbol in knobs["symbols"]:
            sub = symbol.lower()
            directory = os.path.join(base, sub)
            if not os.path.isdir(directory):
                problems.append(f"{directory!r}: no directory for symbol {symbol}")
                continue
            for name in sorted(os.listdir(directory)):
                if name.endswith(".parquet") and os.path.isfile(os.path.join(directory, name)):
                    on_disk.add(f"{sub}/{name}")
        pinned = set(knobs["files"])
        for relpath in sorted(on_disk - pinned):
            problems.append(f"{relpath}: on disk but not pinned in config.files")
        for relpath in sorted(pinned - on_disk):
            problems.append(f"{relpath}: pinned in config.files but missing under {base!r}")
        for relpath in sorted(on_disk & pinned):
            name = relpath.split("/")[1]
            if name != _UNDERLYING_FILE and not _OPTIONS_FILE.match(name):
                problems.append(
                    f"{relpath}: not an archive file (options_<YYYY>.parquet or "
                    f"{_UNDERLYING_FILE})"
                )
        _raise_if(problems)
        for symbol in knobs["symbols"]:
            sub = symbol.lower()
            years = {}
            for relpath in sorted(r for r in pinned if r.split("/")[0] == sub):
                match = _OPTIONS_FILE.match(relpath.split("/")[1])
                if match:
                    years[int(match.group(1))] = relpath
            underlying = f"{sub}/{_UNDERLYING_FILE}"
            if underlying not in pinned:
                problems.append(f"{underlying}: required and not pinned in config.files")
            if not years:
                problems.append(f"{sub}/: no options_<YYYY>.parquet file")
            out[symbol] = (years, underlying)
        _raise_if(problems)
        return out

    def _verified(self, knobs, relpath):
        """Return the absolute path of ``relpath`` after its sha256 matches the pin."""
        full = os.path.join(knobs["path"], *relpath.split("/"))
        try:
            actual = _sha256(full)
        except OSError as exc:
            raise AssetError([f"{relpath}: cannot read: {exc}"]) from exc
        if actual != knobs["files"][relpath]:
            raise AssetError(
                [f"{relpath}: sha256 {actual} does not match the pinned "
                 f"{knobs['files'][relpath]} — the archive changed"]
            )
        return full

    def _require_columns(self, pa, full, relpath, required):
        """Refuse a parquet file whose footer lacks a required column; return its column names."""
        try:
            names = set(pa.parquet.read_schema(full).names)
        except (OSError, pa.ArrowException) as exc:
            raise AssetError([f"{relpath}: cannot read parquet: {exc}"]) from exc
        missing = [column for column in required if column not in names]
        if missing:
            raise AssetError([f"{relpath}: missing required column(s) {missing}"])

    def _read_table(self, pa, full, relpath, columns, filters=None):
        """Read ``columns`` of one parquet file as an Arrow table, errors typed."""
        try:
            return pa.parquet.read_table(full, columns=list(columns), filters=filters)
        except (OSError, pa.ArrowException) as exc:
            raise AssetError([f"{relpath}: cannot read parquet: {exc}"]) from exc

    def _closes(self, pa, knobs, symbol, relpath):
        """Return ``{date ISO: close}`` from one verified ``underlying_prices`` file."""
        full = self._verified(knobs, relpath)
        self._require_columns(pa, full, relpath, UNDERLYING_COLUMNS)
        table = self._read_table(pa, full, relpath, UNDERLYING_COLUMNS)
        out = {}
        for n, row in enumerate(table.to_pylist(), start=1):
            where = f"{relpath} row {n}"
            if row["symbol"] != symbol:
                raise AssetError(
                    [f"{where}: symbol {row['symbol']!r} is not the directory's {symbol!r}"]
                )
            day = _parse_day(row["date"], where).isoformat()
            if day in out:
                raise AssetError([f"{where}: date {day} repeats"])
            close = row["close"]
            if isinstance(close, bool) or not isinstance(close, (int, float)) \
                    or not math.isfinite(close):
                raise AssetError([f"{where}: close must be a finite number, got {close!r}"])
            out[day] = float(close)
        return out

    # -- rows ----------------------------------------------------------------

    def _chain_row(self, symbol, raw, quote_time, underlying_price, where):
        """Map one archive row onto the 21 ``option_chain`` fields, cross-checking it."""
        if raw["symbol"] != symbol:
            raise AssetError(
                [f"{where}: symbol {raw['symbol']!r} is not the directory's {symbol!r}"]
            )
        option = raw["contract_id"]
        try:
            root, expiry, right, strike = parse_occ(option)
        except AssetError as exc:
            raise AssetError([f"{where}: {problem}" for problem in exc.errors]) from exc
        if raw["expiration"] != expiry:
            raise AssetError(
                [f"{where}: expiration {raw['expiration']!r} disagrees with the OCC "
                 f"symbol {option!r} ({expiry})"]
            )
        if _RIGHT_OF_TYPE.get(raw["type"]) != right:
            raise AssetError(
                [f"{where}: type {raw['type']!r} disagrees with the OCC symbol "
                 f"{option!r} ({right})"]
            )
        column_strike = _finite(raw["strike"])
        if column_strike is None or abs(column_strike - strike) > STRIKE_TOLERANCE + 1e-9:
            raise AssetError(
                [f"{where}: strike {raw['strike']!r} disagrees with the OCC symbol "
                 f"{option!r} ({strike}) by more than {STRIKE_TOLERANCE}"]
            )
        row = {
            "underlying": symbol,
            "option": option,
            "root": root,
            "expiry": expiry,
            "right": right,
            "strike": strike,
        }
        for field, column in _NUMERIC:
            row[field] = _finite(raw[column])
        row["last_trade_time"] = None
        row["underlying_price"] = underlying_price
        row["quote_time"] = quote_time
        return {field: row[field] for field in CHAIN_FIELDS}

    def _year_days(self, pa, knobs, relpath, year):
        """Verify one yearly file and return its distinct trading days, refusing a stray date."""
        full = self._verified(knobs, relpath)
        self._require_columns(pa, full, relpath, OPTION_COLUMNS)
        column = self._read_table(pa, full, relpath, ("date",)).column("date")
        days = set()
        for value in pa.compute.unique(column).to_pylist():
            day = _parse_day(value, relpath)
            if day.year != year:
                raise AssetError(
                    [f"{relpath}: date {value} lies outside the file's year {year}"]
                )
            days.add(day)
        return full, days

    def _year_table(self, pa, full, relpath, days):
        """Read one yearly file's rows for ``days`` once; return ``(table, {day: (offset, length)})``."""
        wanted = [d.isoformat() for d in days]
        table = self._read_table(pa, full, relpath, OPTION_COLUMNS,
                                 filters=[("date", "in", wanted)])
        table = table.sort_by([("date", "ascending"), ("contract_id", "ascending")])
        spans, start, previous = {}, 0, None
        for i, value in enumerate(table.column("date").to_pylist()):
            if value != previous:
                if previous is not None:
                    spans[previous] = (start, i - start)
                start, previous = i, value
        if previous is not None:
            spans[previous] = (start, table.num_rows - start)
        return table, spans

    def _day_rows(self, symbol, relpath, table, spans, day, closes, missing):
        """Return one day's mapped rows of one ticker, sorted by option; refuse a repeat."""
        iso = day.isoformat()
        span = spans.get(iso)
        if span is None:
            return []
        quote_time = _close_instant(day).isoformat()
        price = closes.get(iso)
        if price is None:
            missing.add(iso)
        rows, previous = [], None
        for i, raw in enumerate(table.slice(*span).to_pylist(), start=1):
            where = f"{relpath} {iso} row {i}"
            if raw["contract_id"] == previous:
                raise AssetError([f"{where}: contract {previous} on {iso} repeats"])
            previous = raw["contract_id"]
            rows.append(self._chain_row(symbol, raw, quote_time, price, where))
        return rows

    def _pull(self, pa, knobs, layout, cursor_dt, logs):
        """Yield ``(quote_time, row)`` for every day after the cursor, bounded by ``max_days``.

        Days are emitted in order, and within a day tickers in name order
        and options in symbol order. One year is held at a time, and only
        its chosen days.
        """
        budget = knobs["max_days"]
        years = sorted({y for yearly, _u in layout.values() for y in yearly})
        closes = {}
        missing = {symbol: set() for symbol in layout}
        for year in years:
            if budget is not None and budget <= 0:
                break
            if cursor_dt is not None and _close_instant(date(year, 12, 31)) <= cursor_dt:
                continue  # every close of this year is already durable
            per_symbol, union = {}, set()
            for symbol in sorted(layout):
                relpath = layout[symbol][0].get(year)
                if relpath is None:
                    continue
                full, days = self._year_days(pa, knobs, relpath, year)
                days = {d for d in days
                        if cursor_dt is None or _close_instant(d) > cursor_dt}
                per_symbol[symbol] = (relpath, full, days)
                union |= days
            chosen = sorted(union)
            if budget is not None:
                chosen = chosen[:budget]
                budget -= len(chosen)
            if not chosen:
                continue
            tables = {}
            for symbol, (relpath, full, days) in sorted(per_symbol.items()):
                mine = [d for d in chosen if d in days]
                if not mine:
                    continue
                if symbol not in closes:
                    closes[symbol] = self._closes(pa, knobs, symbol, layout[symbol][1])
                tables[symbol] = (relpath,) + self._year_table(pa, full, relpath, mine)
            for day in chosen:
                for symbol, (relpath, table, spans) in sorted(tables.items()):
                    for row in self._day_rows(symbol, relpath, table, spans, day,
                                              closes[symbol], missing[symbol]):
                        yield row["quote_time"], row
            del tables
        for symbol in sorted(missing):
            if missing[symbol]:
                logs.append(
                    f"{symbol}: no underlying close for {sorted(missing[symbol])}; "
                    "those rows carry underlying_price None"
                )

    # -- the four verbs ------------------------------------------------------

    def check(self, config):
        """Validate config, the pin set against the files on disk, and every file's columns.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Raises
        ------
        AssetError
            If config is invalid, a file is unpinned or missing, pyarrow
            is absent, or a file lacks a required column. Hashes are
            verified by ``read`` for the files it opens.
        """
        knobs = self.resolve_knobs(config)
        layout = self._layout(knobs)
        pa = _pyarrow()
        for symbol, (yearly, underlying) in sorted(layout.items()):
            for relpath in [underlying] + [yearly[y] for y in sorted(yearly)]:
                required = UNDERLYING_COLUMNS if relpath == underlying else OPTION_COLUMNS
                full = os.path.join(knobs["path"], *relpath.split("/"))
                self._require_columns(pa, full, relpath, required)

    def discover(self, config):
        """Declare the ``option_chain`` stream without reading a file.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One ``{stream, schema, primary_key}`` declaration.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [{"stream": CHAIN_STREAM, "schema": {"fields": list(CHAIN_FIELDS)},
                 "primary_key": list(CHAIN_KEY_FIELDS)}]

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, the provenance LOG, cursor-filtered RECORDs, then one STATE.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            ``["option_chain"]``.
        state : dict
            Prior mode-keyed checkpoint: ``{"option_chain": {"cursor": ISO}}``.
        mode : str
            ``backfill`` or ``live`` — identical; the platform keys the
            cursors apart.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, the pins or the archive's rows are invalid.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        bad = [k for k, v in state.items() if not isinstance(v, dict)]
        if bad:
            raise AssetError([f"state.{k} must be a dict, got {state[k]!r}" for k in bad])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        unknown = [s for s in streams if s != CHAIN_STREAM]
        if unknown:
            raise AssetError(
                [f"unknown stream(s) {unknown}; discovered: {[CHAIN_STREAM]}"]
            )
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        knobs = self.resolve_knobs(config)
        layout = self._layout(knobs)
        pa = _pyarrow()
        import pyarrow.compute  # noqa: F401 — binds pa.compute for _year_days

        new_state = {key: dict(value) for key, value in state.items()}
        cursor = state.get(CHAIN_STREAM, {}).get("cursor", "")
        cursor_dt = parse_utc(cursor) if cursor else None
        yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": CHAIN_STREAM,
               "schema": {"fields": list(CHAIN_FIELDS)}}
        yield {"protocol": PROTOCOL, "type": "LOG", "level": "info",
               "message": f"optionshist archive {knobs['source_url']} at commit "
                          f"{knobs['source_commit']}; every file opened is "
                          "sha256-verified against config.files"}
        logs = []
        emitted = cursor
        for quote_time, row in self._pull(pa, knobs, layout, cursor_dt, logs):
            yield {"protocol": PROTOCOL, "type": "RECORD", "stream": CHAIN_STREAM,
                   "effective_date": quote_time, "kind": "observation", "data": row}
            emitted = quote_time
        for message in logs:
            yield {"protocol": PROTOCOL, "type": "LOG", "level": "warning",
                   "message": message}
        new_state.setdefault(CHAIN_STREAM, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
