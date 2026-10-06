"""Binance Vision daily files, reshaped for the store: the ``httpblobs`` transform (ADR-0233).

``data.binance.vision`` publishes one zip per symbol per UTC day, each holding
one CSV. The ``httpblobs`` connector fetches the zip; the classes here are its
``transform`` hook and own the one vendor decision, so the parquet a reader gets
is the same shape on every day:

- **One epoch unit.** Spot files switched from millisecond to MICROSECOND
  epochs on 2025-01-01 (probed 2026-10-06: ``1790812800000000`` for
  2026-10-01 00:00 UTC). Every ``ts`` column is written in epoch
  MILLISECONDS: a value at or above ``10**14`` is microseconds (ms epochs reach
  that only in year 5138) and is floored to ms, anything else is already ms.
- **Header or none.** Spot klines carry no header row, the BVOL index does;
  a first row whose first cell is not a number is a header and is skipped.
- **Typed columns, vendor values.** Prices, volumes and the index value are
  float64 as published; counts int64. The klines ``ignore`` column (always 0)
  is dropped. Nothing else is derived, filled or resampled.

A file that cannot be reshaped (not a zip, a bad CRC, no rows, a wrong column
count, an unparseable value) raises ``ValueError``, which ``httpblobs`` records
as a refused entity and moves on. Row order is NOT enforced: a file out of order
is reshaped as given and :meth:`ZipCsvParquet.note` says so in the inventory.

The vendor's data is CC BY-NC-SA, research use only: nothing stored through
this module may feed a live trading decision (see each source config's notes).

Import cost: stdlib; ``pyarrow`` only inside :meth:`ZipCsvParquet.transform`.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import zipfile
import zlib
from abc import ABC, abstractmethod

__all__ = ["BVOL_COLUMNS", "KLINE_COLUMNS", "BinanceBvol", "BinanceKlines", "ZipCsvParquet"]

#: Epoch values at or above this are microseconds (see the module docstring).
_MICROS_FLOOR = 10**14

#: ``(vendor name, output name or None to drop, kind)`` per CSV column, in file order.
_KLINES = (
    ("open_time", "open_time_ms", "ts"),
    ("open", "open", "float"),
    ("high", "high", "float"),
    ("low", "low", "float"),
    ("close", "close", "float"),
    ("volume", "volume", "float"),
    ("close_time", "close_time_ms", "ts"),
    ("quote_volume", "quote_volume", "float"),
    ("count", "trades", "int"),
    ("taker_buy_volume", "taker_buy_volume", "float"),
    ("taker_buy_quote_volume", "taker_buy_quote_volume", "float"),
    ("ignore", None, "int"),
)
_BVOL = (
    ("calc_time", "calc_time_ms", "ts"),
    ("symbol", "symbol", "text"),
    ("base_asset", "base_asset", "text"),
    ("quote_asset", "quote_asset", "text"),
    ("index_value", "index_value", "float"),
)

KLINE_COLUMNS = tuple(out for _, out, _ in _KLINES if out)
BVOL_COLUMNS = tuple(out for _, out, _ in _BVOL if out)


def _epoch_ms(text):
    """Parse an epoch in ms or us and return ms."""
    value = int(text)
    return value // 1000 if value >= _MICROS_FLOOR else value


def _finite(text):
    """Parse a float, refusing nan and infinity."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {text!r}")
    return value


#: kind -> (parser, pyarrow type name): the closed vocabulary a layout draws on.
_KINDS = {
    "ts": (_epoch_ms, "int64"),
    "float": (_finite, "float64"),
    "int": (int, "int64"),
    "text": (str, "string"),
}


def _is_number(text):
    """Report whether a CSV cell parses as a number."""
    try:
        float(text)
    except ValueError:
        return False
    return True


def _iso(ms):
    """Return epoch ms as ``YYYY-MM-DDTHH:MM:SSZ`` (whole seconds)."""
    return dt.datetime.fromtimestamp(ms // 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ZipCsvParquet(ABC):
    """One vendor zip holding one CSV -> parquet bytes, the layout supplied by a subclass.

    The abstract :meth:`layout` is the whole contract a subclass fills in, so a
    half-built subclass refuses at construction, not on the first file.

    Parameters
    ----------
    params : dict or None
        ``transform_params`` from the source config. No knob exists yet, so any
        key is refused (default-deny).
    as_of : str
        The pull's declared ISO instant, handed over by ``httpblobs``; the files
        written here carry no stamp, so equal input bytes give equal output bytes.

    Examples
    --------
    A subclass names its columns and is then used as an ``httpblobs`` transform::

        class Ticks(ZipCsvParquet):
            @classmethod
            def layout(cls):
                return (("t", "t_ms", "ts"), ("px", "price", "float"))

        parquet_bytes = Ticks({}, "2026-10-06T00:00:00+00:00").transform("2026-10-01", zip_bytes)
    """

    _PARAMS = ()

    def __init__(self, params, as_of):
        params = {} if params is None else params
        unknown = sorted(set(params) - set(self._PARAMS))
        if unknown:
            raise ValueError(f"unknown params {unknown}; allowed {list(self._PARAMS)}")
        self.as_of = as_of
        self._memo = (None, None)

    @classmethod
    @abstractmethod
    def layout(cls):
        """Declare the CSV columns in file order.

        Returns
        -------
        tuple
            ``(vendor name, output name or None to drop, kind)`` per column, with
            ``kind`` one of ``ts``, ``float``, ``int``, ``text``. The first
            ``ts`` column is the row's instant, used for the order check and the
            note.
        """

    @staticmethod
    def _member(body):
        """Return the one CSV member's text, refusing a bad zip, a bad CRC or a wrong member count."""
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                if not names:
                    raise ValueError("no .csv member in the archive")
                if len(names) != 1:
                    raise ValueError(f"exactly one .csv member expected, got {len(names)}")
                return archive.read(names[0]).decode("utf-8")
        except (zipfile.BadZipFile, zlib.error, EOFError, UnicodeDecodeError) as exc:
            raise ValueError(f"not a zip archive of UTF-8 CSV, or corrupt: {exc}") from exc

    def _parse(self, body):
        """Return ``{output name: [values]}`` for one zip, memoised on the body object."""
        if self._memo[0] is body:
            return self._memo[1]
        layout = self.layout()
        reader = csv.reader(io.StringIO(self._member(body), newline=""))
        columns = {out: [] for _, out, _ in layout if out}
        first = True
        for row in reader:
            if not row:
                continue
            if first:
                first = False
                if not _is_number(row[0]):
                    continue  # a header row
            if len(row) != len(layout):
                raise ValueError(f"line {reader.line_num}: expected {len(layout)} columns, got {len(row)}")
            for (name, out, kind), cell in zip(layout, row):
                if out is None:
                    continue
                try:
                    columns[out].append(_KINDS[kind][0](cell))
                except ValueError as exc:
                    raise ValueError(f"line {reader.line_num}: column {name!r}: {exc}") from exc
        if not next(iter(columns.values())):
            raise ValueError("no rows in the CSV")
        self._memo = (body, columns)
        return columns

    def _instants(self, columns):
        """Return the row-instant column: the layout's first ``ts`` column."""
        return columns[next(out for _, out, kind in self.layout() if kind == "ts")]

    def note(self, entity, body):
        """Describe one file for the inventory row.

        Parameters
        ----------
        entity : str
            The requested date.
        body : bytes
            The raw zip.

        Returns
        -------
        str
            ``"rows N, <first instant> .. <last instant>"``, with ``" OUT OF ORDER"``
            appended when the instants are not non-decreasing in file order.

        Raises
        ------
        ValueError
            If the file cannot be reshaped.
        """
        instants = self._instants(self._parse(body))
        ordered = all(a <= b for a, b in zip(instants, instants[1:]))
        text = f"rows {len(instants)}, {_iso(instants[0])} .. {_iso(instants[-1])}"
        return text if ordered else text + " OUT OF ORDER"

    def transform(self, entity, body):
        """Return the parquet file bytes for one day's zip.

        Parameters
        ----------
        entity : str
            The requested date (``YYYY-MM-DD``).
        body : bytes
            The raw zip, as the vendor served it.

        Returns
        -------
        bytes
            A parquet file whose columns are the layout's output names.

        Raises
        ------
        ValueError
            If the file is not a zip of one CSV, is corrupt, is empty, or a row
            has the wrong width or an unparseable value.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        columns = self._parse(body)
        schema = pa.schema([(out, getattr(pa, _KINDS[kind][1])())
                            for _, out, kind in self.layout() if out])
        buffer = io.BytesIO()
        pq.write_table(pa.table(columns, schema=schema), buffer)
        return buffer.getvalue()


class BinanceKlines(ZipCsvParquet):
    """Spot ``klines`` daily file (``…/spot/daily/klines/<SYMBOL>/<interval>/``) -> parquet.

    Columns: :data:`KLINE_COLUMNS` — open time and close time in epoch ms (the
    vendor's microsecond files are floored), OHLC, base volume, quote volume,
    trade count and the taker-buy volumes. Spot klines are quoted in USDT, not
    USD: a proxy for, not a copy of, the CF Benchmarks BRTI that Kalshi settles on.

    Parameters
    ----------
    params : dict or None
        Must be empty (no knobs).
    as_of : str
        The pull's declared ISO instant.

    Examples
    --------
    The ``httpblobs`` source config names it as the transform::

        {"transform": "crypto_trading.binance_vision:BinanceKlines",
         "transform_params": {}}

    Direct use::

        klines = BinanceKlines({}, "2026-10-06T00:00:00+00:00")
        parquet_bytes = klines.transform("2026-10-01", zip_bytes)
    """

    @classmethod
    def layout(cls):
        """Return the 12-column klines layout.

        Returns
        -------
        tuple
            The klines columns in file order.
        """
        return _KLINES


class BinanceBvol(ZipCsvParquet):
    """``BVOLIndex`` daily file (``…/option/daily/BVOLIndex/<SYMBOL>/``) -> parquet.

    Columns: :data:`BVOL_COLUMNS` — the index instant in epoch ms, the index
    symbol, its base and quote asset, and ``index_value`` (Binance's implied
    volatility index, about one value a second).

    Parameters
    ----------
    params : dict or None
        Must be empty (no knobs).
    as_of : str
        The pull's declared ISO instant.

    Examples
    --------
    The ``httpblobs`` source config names it as the transform::

        {"transform": "crypto_trading.binance_vision:BinanceBvol",
         "transform_params": {}}

    Direct use::

        bvol = BinanceBvol({}, "2026-10-06T00:00:00+00:00")
        parquet_bytes = bvol.transform("2026-10-01", zip_bytes)
    """

    @classmethod
    def layout(cls):
        """Return the 5-column BVOL layout.

        Returns
        -------
        tuple
            The BVOL columns in file order.
        """
        return _BVOL
