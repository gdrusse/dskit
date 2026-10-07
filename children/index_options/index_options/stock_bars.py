"""Reshape one Yahoo-style chart response into a per-symbol daily-bar parquet.

The ``httpblobs`` connector (ADR-0233) fetches a chart JSON per symbol; this
class is its ``transform`` hook and owns the one project decision: the
archive-style ``underlying_prices`` schema the option archive already uses
(``id, symbol, date, open, high, low, close, adjusted_close, volume,
dividend_amount, split_coefficient, created_at``), so the same price-source
relpath reads either.

Basis (owner ruling 2026-10-03, corrected after review): everything Yahoo
gives is on ONE split-adjusted share basis and is stored AS GIVEN.
``open/high/low/close`` show no jump on a split day (the series the
price-calendar panel assumes); ``volume`` is Yahoo's split-adjusted volume
(continuous across a split: AAPL 2020-08-28 187,630,000 and 2020-08-31
225,702,700), so ``close * volume`` is a split-adjusted dollar volume, not the
as-traded one; ``adjusted_close`` is split and dividend adjusted;
``dividend_amount`` and ``split_coefficient`` are informational. No as-traded
column is carried: the response does not hold one and the file keeps the
reference archive's columns. A split whose ratio is neither ``n`` nor ``1/n``
is usually a spin-off Yahoo labels a split; it is stored the same way and
:meth:`StockDailyBars.note` flags it.

Import cost: stdlib; ``pyarrow`` only inside :meth:`StockDailyBars.transform`.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import zoneinfo

__all__ = ["StockDailyBars", "COLUMNS"]

#: The output schema, in order (the archive's ``underlying_prices`` layout).
COLUMNS = ("id", "symbol", "date", "open", "high", "low", "close", "adjusted_close",
           "volume", "dividend_amount", "split_coefficient", "created_at")

#: Defaults for ``params`` — named once, used by validation and by transform.
DEFAULT_PRICE_DECIMALS = 4
DEFAULT_DIVIDEND_DECIMALS = 6
_PARAMS = ("price_decimals", "dividend_decimals", "strict_split_inventory")


class StockDailyBars:
    """Chart JSON -> archive-style daily-bar parquet bytes.

    Parameters
    ----------
    params : dict
        ``price_decimals`` (int >= 0, default 4) and ``dividend_decimals``
        (int >= 0, default 6): rounding of the rebuilt raw values.
        strict_split_inventory (bool, default False) requires a valid present
        split inventory and one emitted bar per split date (ADR-0249).
    as_of : str
        The pull's declared ISO instant; every row's ``created_at`` (declared,
        never the wall clock, so equal input bytes give equal output bytes).

    Examples
    --------
    The ``httpblobs`` config names it as the transform::

        {"transform": "index_options.stock_bars:StockDailyBars",
         "transform_params": {"price_decimals": 4}}

    Direct use::

        bars = StockDailyBars({}, "2026-10-03T00:00:00+00:00")
        parquet_bytes = bars.transform("ABC", chart_json_bytes)
    """

    def __init__(self, params, as_of):
        params = {} if params is None else params
        unknown = sorted(set(params) - set(_PARAMS))
        if unknown:
            raise ValueError(f"unknown params {unknown}; allowed {list(_PARAMS)}")
        strict = params.get("strict_split_inventory", False)
        if type(strict) is not bool:
            raise ValueError("strict_split_inventory must be boolean")
        self.strict_split_inventory = strict
        self.price_decimals = self._count(params, "price_decimals", DEFAULT_PRICE_DECIMALS)
        self.dividend_decimals = self._count(params, "dividend_decimals", DEFAULT_DIVIDEND_DECIMALS)
        self.created_at = dt.datetime.fromisoformat(as_of).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _count(params, name, default):
        """Return an int >= 0 param, or its default when absent."""
        value = params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be an int >= 0, got {value!r}")
        return value

    @staticmethod
    def _result(body):
        """Return the one chart result dict from raw response bytes."""
        import json

        doc = json.loads(body.decode("utf-8"))["chart"]
        if not doc.get("result"):
            raise ValueError(f"chart response holds no result: {doc.get('error')!r}")
        return doc["result"][0]

    @staticmethod
    def _day_of(stamp, zone):
        """Return the exchange-local ISO date of a unix timestamp."""
        return dt.datetime.fromtimestamp(stamp, zone).date().isoformat()

    def _events(self, result, zone, kind):
        """Return ``{iso date: event}`` for ``splits`` or ``dividends``."""
        found = (result.get("events") or {}).get(kind) or {}
        return {self._day_of(int(v["date"]), zone): v for v in found.values()}

    def rows(self, entity, body):
        """Build the column dict for one response.

        Parameters
        ----------
        entity : str
            The requested symbol (used when the response omits one).
        body : bytes
            Raw chart JSON.

        Returns
        -------
        dict
            ``{column: list}`` in :data:`COLUMNS` order; rows with a null
            open or close are dropped.

        Raises
        ------
        ValueError
            If the response holds no result or the arrays are unaligned.
        """
        result = self._result(body)
        meta = result["meta"]
        zone = zoneinfo.ZoneInfo(meta["exchangeTimezoneName"])
        stamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
        if any(len(v) != len(stamps) for v in [*quote.values(), adjusted]):
            raise ValueError("chart arrays are not aligned")
        dates = [self._day_of(s, zone) for s in stamps]
        inventory = self._split_inventory(result, zone) if self.strict_split_inventory else None
        splits = self._events(result, zone, "splits") if inventory is None else {
            day: {"numerator": ratio, "denominator": 1.0}
            for day, ratio in inventory.ratios.items()}
        dividends = self._events(result, zone, "dividends")
        symbol = meta.get("symbol") or entity
        out = {c: [] for c in COLUMNS}
        for i, day in enumerate(dates):
            if quote["close"][i] is None or quote["open"][i] is None:
                continue
            out["id"].append(len(out["id"]) + 1)
            out["symbol"].append(symbol)
            out["date"].append(day)
            for name in ("open", "high", "low", "close"):
                out[name].append(round(quote[name][i], self.price_decimals))
            out["adjusted_close"].append(adjusted[i])
            out["volume"].append(int(quote["volume"][i] or 0))
            amount = dividends[day]["amount"] if day in dividends else 0.0
            out["dividend_amount"].append(round(amount, self.dividend_decimals))
            ratio = splits[day]["numerator"] / splits[day]["denominator"] if day in splits else 1.0
            out["split_coefficient"].append(ratio)
            out["created_at"].append(self.created_at)
        if inventory is not None:
            from dskit.onboarding.base import AssetError
            try:
                inventory.require_bar_dates(out["date"])
            except AssetError as error:
                raise ValueError(str(error)) from error
        return out

    @staticmethod
    def _split_inventory(result, zone):
        from dskit.onboarding.base import AssetError
        from dskit.onboarding.libs.yahoo import YahooSplitInventory
        try:
            return YahooSplitInventory(result, zone)
        except AssetError as error:
            raise ValueError(str(error)) from error

    def note(self, entity, body):
        """Describe the splits in a response, flagging irregular ratios.

        Parameters
        ----------
        entity : str
            The requested symbol.
        body : bytes
            Raw chart JSON.

        Returns
        -------
        str or None
            ``"splits YYYY-MM-DD:ratio ..."`` with ``"IRREGULAR"`` appended to a
            ratio that is neither ``n`` nor ``1/n`` (a probable spin-off), or
            ``None`` when the chart has no split.
        """
        result = self._result(body)
        zone = zoneinfo.ZoneInfo(result["meta"]["exchangeTimezoneName"])
        if self.strict_split_inventory:
            self.rows(entity, body)  # the same no-lost-action admission as transform
            found = {day: {"numerator": ratio, "denominator": 1.0}
                     for day, ratio in self._split_inventory(result, zone).ratios.items()}
        else:
            found = self._events(result, zone, "splits")
        parts = []
        for day in sorted(found):
            ratio = found[day]["numerator"] / found[day]["denominator"]
            regular = any(math.isfinite(r) and abs(r - round(r)) < 1e-9
                          for r in (ratio, 1 / ratio))
            parts.append(f"{day}:{ratio:g}" + ("" if regular else " IRREGULAR"))
        return "splits " + "; ".join(parts) if parts else None

    def transform(self, entity, body):
        """Return the parquet file bytes for one response.

        Parameters
        ----------
        entity : str
            The requested symbol.
        body : bytes
            Raw chart JSON.

        Returns
        -------
        bytes
            A parquet file in the :data:`COLUMNS` schema.

        Raises
        ------
        ValueError
            If the response cannot be reshaped.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        types = {"id": pa.int64(), "symbol": pa.string(), "date": pa.string(),
                 "volume": pa.int64(), "created_at": pa.string()}
        schema = pa.schema([(c, types.get(c, pa.float64())) for c in COLUMNS])
        buffer = io.BytesIO()
        pq.write_table(pa.table(self.rows(entity, body), schema=schema), buffer)
        return buffer.getvalue()
