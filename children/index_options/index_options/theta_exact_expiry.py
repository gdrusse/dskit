"""Theta end-of-day chains -> the inputs of the exact-expiry CDF machinery (ADR-0270).

The exact-expiry stack (``ExactExpiryCDFPanel``, ``RawChainFeatureBuilder``,
``DecisionRegionContextBuilder``) was built on four artifacts derived from the options-dataset-hist
archive: the per-(symbol, quote_date, expiry) ``exact_expiry_surface`` table, the
``date_expiry_lifecycle`` table, the annual chain archive at ``archive_relpath(symbol, year)`` and a
per-symbol volatility-index close series (``own_iv``, served by ``IndexCloseRows``). This node
rebuilds those four from the frozen ThetaData export (a ``ParquetChainRows`` source), so the same
panel, chain tensors and strike-region scores run on Theta chains with no change to any owner. It
adds no model, no fit and no market read beyond the reader's declared bounds.

**Outputs** (one directory, created whole by one rename; an existing directory is refused)::

    surface.parquet                       exact_expiry_surface columns, one row per kept expiry-day
    lifecycle.parquet                     [symbol, quote_date, expiry, first_seen_date], same keys
    archive/<symbol>/options_<year>.parquet   the chain archive, build_archive's 16 columns
    own_iv/index_daily/<SYMBOL>.parquet   the daily 30-day chain ATM-IV close series
    prices/index_daily/<SYMBOL>.parquet   the split-adjusted daily bars (the panel's price_source)
    manifest.json                         counts, refusals, definitions, provenance, sha256 of each file

The prices shard holds EVERY bar the bars reader supplies (from its own start, a warm-up for the
backward-only volatility and lag features), while surface, archive and own_iv start at ``start``.
``open``/``high``/``low`` are written only if every bar of the symbol has all three, finite and positive
(``IndexCloseRows`` refuses a row with some of them); otherwise all three are dropped for that symbol and
the manifest says ``prices_ohlc: dropped_incomplete``.

``own_iv/`` and ``prices/`` are ``localtables`` directory layouts (``layout`` "directory",
``stamp_stem_as`` "symbol", ``effective_field`` "date"); acquiring each as a source gives
``IndexCloseRows(source=..., symbol=...)`` its ``index_daily`` stream (``symbol``, ``date``,
``close``; the bar shards add ``open``/``high``/``low``/``volume``/``dividend_amount`` when the bars
carry them and NEVER a ``split_coefficient``, which that reader refuses unless it is 1).

**Price basis.** The panel labels returns with split-adjusted closes and refuses a symbol whose
close disagrees with ``chain_underlying_price`` by more than its ``spot_tolerance``, but Theta's
strikes and underlying are RAW. The node therefore takes the bars as a second input and converts
every Theta price to the bars' basis (the split-adjusted ``close``; ``adjusted_close`` is also
dividend-adjusted, which would make the labels total returns and the ratio drift, so it is not the
default). Per symbol, the day's ratio ``C/U`` (bar close over the median vendor underlying) is
constant inside each segment between the bars' split dates; the segment's factor ``f`` is the
median ratio, snapped to a simple fraction (a denominator of at most 50 on the ratio or on its
reciprocal) ONLY when it equals the measured median to 1e-8 (a 4:1 split gives exactly 0.25, a 1:12 under a
1:30 gives 360; any other ratio keeps its measured median, ``snapped`` false). A day whose
ratio is more than ``basis_tolerance`` from its segment's ``f``, or with no bar or no underlying,
is refused (listed in the manifest) so a stale vendor underlying never rescales a day. SCALED by
``f``: ``strike``, ``underlying_price``, ``bid``, ``ask`` (hence ``mark``), ``vega`` (times ``f``,
it is a price per vol) and ``gamma`` (times ``1/f``, a price over a price squared), so the
consumer's ``log1p(sum|gamma or vega| x open_interest)`` has no level shift at a split. NOT scaled:
sizes, open interest, volume, IV and delta (dimensionless); theta and rho are not read. The archive keeps ``strike_raw``, the adjusted
``underlying_price`` and ``basis_factor`` as columns after the sixteen the consumers read, and the
manifest records ``f`` for every accepted symbol-day.

**Definitions.** The legacy surface was built outside this repository and never written down; the
definitions below were recovered by re-deriving it from the archive it came from (IWM, March 2017:
``chain_contracts``, ``chain_mean_iv``, ``chain_rel_spread``, the open-interest, depth and
25-delta columns reproduce exactly; ``chain_atm_iv`` on 129 of 147 expiry-days, the rest differing
by about 0.001 because the original ATM band used a spot not stored in the table). They are
the rules the consumers' features were trained on, so they are kept, not improved. All of them are
computed over the rows of one (symbol, quote_date, expiry), the unit the reader's units already are:

- ``chain_contracts``: the count of listed contract rows, quoted or not.
- ``chain_mean_iv``: mean vendor ``implied_vol`` over rows whose IV is finite and positive.
- ``chain_atm_iv``: the same mean over rows with ``|strike / underlying - 1| <= atm_band`` (call
  and put alike; ``atm_band`` 0.025). NaN when no row is in the band.
- ``chain_call25_iv`` / ``chain_put25_iv``: the same mean over the calls (puts) whose vendor
  ``|delta|`` lies in ``[wing_delta - wing_delta_width, wing_delta + wing_delta_width]`` (0.20 to
  0.30, inclusive); NaN when none. ``chain_risk_reversal25 = call25 - put25``.
- ``chain_rel_spread``: mean ``(ask - bid) / mid`` over rows with a finite quote, ``ask >= bid``
  and ``mid > 0`` (a zero-bid row counts: the legacy mean did).
- ``chain_put_call_oi`` / ``chain_put_call_volume``: put total over call total; NaN unless BOTH
  totals are positive (the consumer logs the ratio). The legacy raw archive
  DID carry volume (IWM 2017-03-01 has nonzero volumes), but the legacy SURFACE it was derived into
  held ``chain_volume`` zero and ``chain_put_call_volume`` missing throughout: a build artifact of the
  legacy derivation, not a vendor gap. Theta fills both from the real column, so models trained on
  the legacy surface never saw these two columns populated.
- ``chain_open_interest`` / ``chain_volume``: sums over all rows (missing counts as 0).
- ``chain_quote_depth``: the sum of ``bid_size + ask_size`` over all rows (missing counts as 0).
- ``chain_underlying_price``: the median vendor ``underlying_price`` of the rows.
- ``first_seen_date``: the first date, among the processed ones, on which ANY row lists the
  expiry (whatever its DTE or quality, so a refused day still counts), scanned from ``lifecycle_start``
  (rows between it and ``start`` feed only this column). An expiry whose first_seen equals the
  symbol's first SCANNED date may have been listed earlier: it is left-censored, ``first_seen_censored``
  (a boolean column AFTER the legacy four) is true, and the manifest counts them. The date is kept,
  not nulled, because ``ExactExpiryCDFPanel`` turns a null into negative session ages and life
  fractions above 1 without refusing (checked); a model should treat a censored age as a lower bound.

**Archive rows.** Every row of a kept expiry-day is written, in the consumers' vocabulary
(``right`` -> ``type``, ``implied_vol`` -> ``implied_volatility``), so provenance is whole. ``mark``
is ``(bid + ask) / 2`` for a valid quote (``bid > 0`` and ``ask >= bid``; the rule of
:func:`~dskit.pipeline.libs.chain_census.quote_state`'s ``quoted`` and ``locked`` classes, pinned by
a test) and null otherwise, so the builder's ``_usable_quotes`` masks crossed, zero-bid and
missing quotes by itself.

**Refusals** (counted in the manifest, never emitted): an expiry with fewer than
``min_two_sided_strikes`` strikes carrying a valid (two-sided: bid > 0, ask >= bid) quote on either
right, and every expiry of a quote day on which no kept-horizon row carries a valid quote. A chain
symbol with no bar rows is refused whole (``refused_symbols`` in the manifest and the summary). Rows with ``dte < 1`` or ``dte > max_dte`` (calendar
days) are outside the horizon and counted, not refused.

**own_iv.** One row per symbol and quote date: ``close`` is the ``chain_atm_iv`` of the kept expiry
whose DTE is nearest ``own_iv_target_dte`` (ties go to the shorter), provided it lies within
``own_iv_max_gap_days`` and is finite. The unit is VIX-style POINTS, the surface's decimal ATM IV x 100
(0.18 is written 18.0), because every existing own_iv consumer (strata at 20 and 30, entry_iv) is in points. ``source_expiry`` and ``source_dte`` record which expiry served.

**Bounds.** ``start`` and ``end_before`` are declared by the reader AND here; the node refuses a
source whose bounds differ and any chunk with a date outside them. The reader's ``sealed_from``
refuses a whole file reaching the sealed date. Memory is bounded by: the bars of the chain
symbols (seven values per symbol-day, held for the whole run; the other bar symbols are dropped on
intake), plus one symbol's chunk of about ``flush_rows`` rows cut only at complete dates, plus that
symbol's surface rows (about one per expiry-day). The manifest holds one factor per symbol-day.
``archive_rows_unusable`` counts archived rows the builder's ``_usable_quotes`` drops for a row-local
reason (invalid quote, non-finite size, open interest or IV, IV <= 0); duplicates are not counted.

Heavy imports (pyarrow, pandas, numpy) are inside the methods that need them.
"""

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from dskit.pipeline.node import Node, atomic_write, check_int_param, reject_unknown_params

from .datafiles import archive_relpath

__all__ = [
    "ARCHIVE_COLUMNS", "ARCHIVE_EXTRA_COLUMNS", "BAR_FIELDS", "DEFAULT_ATM_BAND",
    "DEFAULT_BAR_PRICE_FIELD", "DEFAULT_BASIS_TOLERANCE", "DEFAULT_FLUSH_ROWS", "DEFAULT_MIN_TWO_SIDED_STRIKES",
    "DEFAULT_OWN_IV_MAX_GAP_DAYS", "DEFAULT_OWN_IV_TARGET_DTE", "DEFAULT_WING_DELTA",
    "DEFAULT_WING_WIDTH", "DEFINITIONS", "LIFECYCLE_COLUMNS", "LIFECYCLE_EXTRA_COLUMNS", "SOURCE_FIELDS", "SURFACE_COLUMNS",
    "ThetaExactExpiryTables", "quotes_valid"]

#: No node kind is registered: a document names ``index_options.theta_exact_expiry:ThetaExactExpiryTables``.
NODE_KINDS = ()

#: The half-width of the ATM strike band as a fraction of the underlying (legacy surface rule).
DEFAULT_ATM_BAND = 0.025
#: The |delta| the wing IVs are taken around, and the half-width of the band (legacy: 0.20 to 0.30).
DEFAULT_WING_DELTA = 0.25
DEFAULT_WING_WIDTH = 0.05
#: Strikes with a valid quote an expiry-day needs to be emitted.
DEFAULT_MIN_TWO_SIDED_STRIKES = 2
#: The tenor, in calendar days, the own_iv series follows, and the farthest it may stray from it.
DEFAULT_OWN_IV_TARGET_DTE = 30
DEFAULT_OWN_IV_MAX_GAP_DAYS = 15
#: own_iv is written in VIX-style points (decimal ATM IV x 100), the unit every own_iv consumer
#: (strata at 20 and 30, entry_iv) was built on.
OWN_IV_POINTS = 100.0
#: Rows buffered before a chunk is processed (cut at the last complete date).
DEFAULT_FLUSH_ROWS = 1_000_000
#: The bar field the adjusted basis follows (split-adjusted, NOT dividend-adjusted), and how far a day's
#: vendor-underlying-to-close ratio may stray from its split segment's factor before the day is refused.
DEFAULT_BAR_PRICE_FIELD = "close"
DEFAULT_BASIS_TOLERANCE = 0.01
#: The largest denominator a snapped factor may need, on the factor or on its reciprocal (4:1 as 1/4, 3:2, 50:49,
#: a 1:12 under a 1:30 as 360/1; 50 so odd cumulative factors still reduce).
_SNAP_DENOMINATOR = 50
#: How exact a measured segment ratio must be to snap to a simple fraction: a true split gives the
#: fraction to machine precision (the bars ARE the raw prices divided by it), so anything looser is a
#: non-split factor and keeps its measured median.
SNAP_TOLERANCE = 1e-8

#: The exact_expiry_surface columns, in the consumer's order.
SURFACE_COLUMNS = (
    "symbol", "quote_date", "expiry", "chain_contracts", "chain_mean_iv", "chain_atm_iv",
    "chain_call25_iv", "chain_put25_iv", "chain_risk_reversal25", "chain_rel_spread",
    "chain_put_call_oi", "chain_put_call_volume", "chain_open_interest", "chain_volume",
    "chain_quote_depth", "chain_underlying_price")
#: The date_expiry_lifecycle columns.
LIFECYCLE_COLUMNS = ("symbol", "quote_date", "expiry", "first_seen_date")
#: Extra lifecycle column after the legacy four: true when first_seen_date is a scan-start date, not a listing.
LIFECYCLE_EXTRA_COLUMNS = ("first_seen_censored",)
#: The columns ``RawChainFeatureBuilder.build_archive`` reads from the chain archive.
ARCHIVE_COLUMNS = (
    "symbol", "date", "expiration", "strike", "type", "mark", "bid", "ask", "bid_size", "ask_size",
    "open_interest", "volume", "implied_volatility", "delta", "gamma", "vega")
#: Extra archive columns after the consumers' sixteen: the RAW strike, the ADJUSTED underlying, the factor.
ARCHIVE_EXTRA_COLUMNS = ("strike_raw", "underlying_price", "basis_factor")
#: The fields the node reads from each bar row; ``close`` is replaced by ``bar_price_field``.
BAR_FIELDS = ("symbol", "date", "close", "split_coefficient")
#: The fields the node reads from the reader (the ``fields`` of the ``ParquetChainRows`` node).
SOURCE_FIELDS = (
    "symbol", "date", "expiration", "strike", "right", "bid", "ask", "bid_size", "ask_size",
    "open_interest", "volume", "implied_vol", "delta", "gamma", "vega", "underlying_price")

#: Every column written, with the rule that makes it; copied into the manifest.
DEFINITIONS = {
    "chain_contracts": "count of listed contract rows of the expiry-day, quoted or not",
    "chain_mean_iv": "mean implied_vol over rows with finite positive IV",
    "chain_atm_iv": "mean implied_vol over rows with |strike/underlying - 1| <= atm_band",
    "chain_call25_iv": "mean implied_vol over calls with |delta| in [wing_delta -/+ wing_delta_width]",
    "chain_put25_iv": "mean implied_vol over puts with |delta| in [wing_delta -/+ wing_delta_width]",
    "chain_risk_reversal25": "chain_call25_iv - chain_put25_iv",
    "chain_rel_spread": "mean (ask-bid)/mid over rows with finite quote, ask>=bid, mid>0",
    "chain_put_call_oi": "sum put open_interest / sum call open_interest; NaN unless both > 0",
    "chain_put_call_volume": "sum put volume / sum call volume; NaN unless both > 0",
    "chain_open_interest": "sum of open_interest over all rows (missing = 0)",
    "chain_volume": "sum of volume over all rows (missing = 0)",
    "chain_quote_depth": "sum of bid_size + ask_size over all rows (missing = 0)",
    "chain_underlying_price": "median vendor underlying_price of the rows",
    "first_seen_date": "first scanned date (from lifecycle_start) on which any row lists the expiry",
    "first_seen_censored": "true when first_seen_date is the symbol's first scanned date (listed earlier, unknown)",
    "chain_volume_note": "legacy surface held zeros/missing for the volume columns (build artifact); filled here",
    "mark": "(bid+ask)/2 for a valid quote (bid>0, ask>=bid), else null",
    "basis_factor": "split-segment factor f taking raw Theta prices to the bars' split-adjusted basis; "
                    "strike, underlying_price, bid, ask, mark and vega are multiplied by f, gamma by 1/f, strike_raw is not",
    "own_iv.close": "chain_atm_iv x 100 (VIX-style POINTS, 0.18 -> 18.0) of the kept expiry nearest "
                    "own_iv_target_dte calendar days, within own_iv_max_gap_days",
}

_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RIGHTS = ("call", "put")
_PIECES = ".pieces"


def _iso(value):
    """Return ``value`` when it is an ISO ``YYYY-MM-DD`` text, else ``None``."""
    try:
        return value if isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def quotes_valid(bid, ask):
    """Say which bid/ask pairs are valid quotes: ``bid > 0`` and ``ask >= bid``, both finite.

    The vectorized twin of :func:`dskit.pipeline.libs.chain_census.quote_state` being ``quoted`` or
    ``locked`` (a test pins the agreement).

    Parameters
    ----------
    bid, ask : numpy.ndarray of float
        The quote sides; NaN is missing.

    Returns
    -------
    numpy.ndarray of bool
        True where the pair is a valid quote.
    """
    import numpy as np

    return np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask >= bid)


def _sha256(path):
    """Return the sha256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_table(key, table):
    """Return ``table`` restricted to :data:`SOURCE_FIELDS` with ISO-text dates, refusing a missing field."""
    import pyarrow as pa
    import pyarrow.compute as pc

    missing = [name for name in SOURCE_FIELDS if name not in table.schema.names]
    if missing:
        raise ValueError(f"{key}: the source lacks field(s) {missing}; declare them in the reader's fields")
    table = table.select(list(SOURCE_FIELDS))
    for name in ("date", "expiration"):
        if pa.types.is_date(table.schema.field(name).type):
            table = table.set_column(table.schema.get_field_index(name), name,
                                     pc.cast(table.column(name), pa.string()))
    return table


class _PriceBasis:
    """The factor taking one symbol's RAW Theta prices to the basis of its split-adjusted bars.

    Pass 1 reads the symbol's rows once to find each day's median vendor underlying ``U(d)``. With
    the bar close ``C(d)`` the day's ratio is ``C/U``. Splits seen in the bars' ``split_coefficient``
    cut the dates into segments inside which the ratio must be one constant (the cumulative split
    factor, 1 when none). Each segment's factor is its median ratio, snapped to a simple fraction
    (``Fraction.limit_denominator`` on the reciprocal, then on the ratio itself) ONLY when that fraction equals the measured median to
    :data:`SNAP_TOLERANCE` (1e-8: true splits do, and 0.50505 must not become 50/99), so the adjusted strike grid is exact (140 x 1/4 = 35) and a
    non-split ratio such as 0.9267 is never forced onto the nearby simple fraction. A day whose own ratio differs from its
    segment's factor by more than ``tolerance`` is refused: a stale or mis-timed vendor underlying
    never silently rescales a day. A day with no bar or no usable underlying is refused too.
    """

    def __init__(self, bars, tolerance, start):
        self.bars, self.tolerance, self.start = bars, tolerance, start
        self.dates, self.under = set(), {}
        self.factors, self.segments = {}, []
        self.refused = {"no_underlying": [], "no_bar": [], "basis_deviation": []}

    def add(self, table):
        """Pass 1: note the dates of a table and the finite positive underlying prices of each."""
        import numpy as np

        frame = table.select(["date", "underlying_price"]).to_pandas()
        frame = frame[frame["date"] >= self.start]            # earlier dates only feed the listings
        self.dates.update(str(d) for d in frame["date"].unique())
        values = frame["underlying_price"].to_numpy(dtype=float)
        good = frame[np.isfinite(values) & (values > 0)]
        for date, group in good.groupby("date"):
            self.under.setdefault(str(date), []).append(group["underlying_price"].to_numpy(dtype=float))

    def resolve(self):
        """Pass 2 set-up: decide each day's factor, the segments and the refused days."""
        import bisect
        import numpy as np

        splits = sorted(d for d, (price, k) in self.bars.items()
                        if k is not None and np.isfinite(k) and k > 0 and k != 1)
        ratios = {}
        for date in sorted(self.dates):
            if date not in self.under:
                self.refused["no_underlying"].append(date)
            elif date not in self.bars or not np.isfinite(self.bars[date][0]) or self.bars[date][0] <= 0:
                self.refused["no_bar"].append(date)
            else:
                ratios[date] = self.bars[date][0] / float(np.median(np.concatenate(self.under[date])))
        grouped = {}
        for date, ratio in ratios.items():
            grouped.setdefault(bisect.bisect_right(splits, date), []).append((date, ratio))
        for number in sorted(grouped):
            days = grouped[number]
            median = float(np.median([r for _, r in days]))
            factor, snapped, residual = self._snap(median)
            kept = [(d, r) for d, r in days if abs(r / factor - 1) <= self.tolerance]
            self.refused["basis_deviation"].extend(d for d, r in days if abs(r / factor - 1) > self.tolerance)
            self.factors.update({d: factor for d, _ in kept})
            self.segments.append({"from": days[0][0], "to": days[-1][0], "median_ratio": median,
                                  "factor": factor, "snapped": snapped, "residual": residual, "days": len(days),
                                  "days_refused": len(days) - len(kept)})
        self.refused["basis_deviation"].sort()

    @staticmethod
    def _snap(median):
        """Return ``(factor, snapped, residual)``: the nearest simple fraction only if exact, else the median.

        The fraction is sought on the reciprocal first (a 4:1 split gives 0.25, found as 1/4) and on
        the median itself second (a 1:30 reverse split on top of a 1:12 gives 360, a denominator-1
        fraction the reciprocal search cannot reach because 1/360 rounds to zero at the limit).

        ``residual`` is the relative distance between the factor used and the measured median (0 for
        an unsnapped median, at most :data:`SNAP_TOLERANCE` for a snapped one).
        """
        from fractions import Fraction

        inverse = Fraction(1 / median).limit_denominator(_SNAP_DENOMINATOR)
        direct = Fraction(median).limit_denominator(_SNAP_DENOMINATOR)
        for simple in (float(1 / inverse) if inverse else None, float(direct) if direct else None):
            if simple is None:
                continue                       # a reciprocal (or a median) that rounds to zero is no fraction
            residual = abs(simple / median - 1)
            if residual <= SNAP_TOLERANCE:
                return simple, True, residual
        return median, False, 0.0


class _SurfaceAggregator:
    """The legacy surface aggregates over the rows of each (date, expiration), vectorized."""

    def __init__(self, atm_band, wing_low, wing_high):
        self.atm_band, self.wing_low, self.wing_high = atm_band, wing_low, wing_high

    def aggregate(self, rows):
        """Return one row of surface values per (date, expiration) of ``rows``.

        Parameters
        ----------
        rows : pandas.DataFrame
            Columns ``date``, ``expiration``, ``strike``, ``right``, ``bid``, ``ask``,
            ``bid_size``, ``ask_size``, ``open_interest``, ``volume``, ``implied_vol``,
            ``delta``, ``underlying_price``.

        Returns
        -------
        pandas.DataFrame
            Indexed by (date, expiration), sorted, with the numeric ``chain_*`` columns.
        """
        import numpy as np
        import pandas as pd

        def column(name):
            return rows[name].to_numpy(dtype=float)

        iv, und = column("implied_vol"), column("underlying_price")
        iv = np.where(np.isfinite(iv) & (iv > 0), iv, np.nan)
        und = np.where(np.isfinite(und) & (und > 0), und, np.nan)
        bid, ask, delta = column("bid"), column("ask"), column("delta")
        call = (rows["right"] == "call").to_numpy()
        mid = (ask + bid) / 2
        usable = np.isfinite(bid) & np.isfinite(ask) & (ask >= bid) & (mid > 0)
        frame = pd.DataFrame({
            "date": rows["date"].to_numpy(), "expiration": rows["expiration"].to_numpy(),
            "iv": iv, "und": und, "strike": column("strike")})
        keys = ["date", "expiration"]
        und_group = frame.groupby(keys, sort=True)["und"].transform("median").to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            in_band = np.abs(frame["strike"].to_numpy() / und_group - 1) <= self.atm_band
            spread = np.where(usable, (ask - bid) / mid, np.nan)
        wing = np.isfinite(delta) & (np.abs(delta) >= self.wing_low) & (np.abs(delta) <= self.wing_high)
        oi = np.nan_to_num(column("open_interest"), nan=0.0)
        volume = np.nan_to_num(column("volume"), nan=0.0)
        depth = np.nan_to_num(column("bid_size"), nan=0.0) + np.nan_to_num(column("ask_size"), nan=0.0)
        frame = frame.assign(
            atm=np.where(in_band, iv, np.nan), call25=np.where(wing & call, iv, np.nan),
            put25=np.where(wing & ~call, iv, np.nan), spread=spread,
            oi_call=np.where(call, oi, 0.0), oi_put=np.where(call, 0.0, oi),
            vol_call=np.where(call, volume, 0.0), vol_put=np.where(call, 0.0, volume), depth=depth)
        grouped = frame.groupby(keys, sort=True)
        out = pd.DataFrame({
            "chain_contracts": grouped.size().astype("int64"),
            "chain_mean_iv": grouped["iv"].mean(), "chain_atm_iv": grouped["atm"].mean(),
            "chain_call25_iv": grouped["call25"].mean(), "chain_put25_iv": grouped["put25"].mean(),
            "chain_rel_spread": grouped["spread"].mean(), "chain_quote_depth": grouped["depth"].sum(),
            "chain_underlying_price": grouped["und"].median()})
        sums = grouped[["oi_call", "oi_put", "vol_call", "vol_put"]].sum()
        out["chain_risk_reversal25"] = out["chain_call25_iv"] - out["chain_put25_iv"]
        out["chain_put_call_oi"] = self._ratio(sums["oi_put"], sums["oi_call"])
        out["chain_put_call_volume"] = self._ratio(sums["vol_put"], sums["vol_call"])
        out["chain_open_interest"] = sums["oi_call"] + sums["oi_put"]
        out["chain_volume"] = sums["vol_call"] + sums["vol_put"]
        return out

    @staticmethod
    def _ratio(put, call):
        """Put over call, missing unless both totals are positive."""
        import numpy as np

        with np.errstate(invalid="ignore", divide="ignore"):
            return (put / call).where((put > 0) & (call > 0))


class _SymbolPass:
    """One symbol's streaming pass: chunks in, archive files written, surface pieces kept.

    Rows arrive as Arrow tables of the reader's fields with dates ascending; ``push`` buffers
    them and ``flush`` processes the buffer up to its last COMPLETE date, so an expiry-day is never
    split across chunks. ``finish`` writes the symbol's surface, lifecycle and own_iv pieces.
    """

    ARCHIVE_TYPES = {
        "symbol": "string", "date": "string", "expiration": "string", "strike": "float64",
        "type": "string", "mark": "float64", "bid": "float64", "ask": "float64",
        "bid_size": "int64", "ask_size": "int64", "open_interest": "int64", "volume": "int64",
        "implied_volatility": "float64", "delta": "float64", "gamma": "float64", "vega": "float64",
        "strike_raw": "float64", "underlying_price": "float64", "basis_factor": "float64"}

    def __init__(self, key, params, symbol, work, number, basis):
        self.key, self.p, self.symbol, self.work, self.number = key, params, symbol, work, number
        self.basis = basis
        low = params.get("wing_delta", DEFAULT_WING_DELTA) - params.get("wing_delta_width", DEFAULT_WING_WIDTH)
        high = params.get("wing_delta", DEFAULT_WING_DELTA) + params.get("wing_delta_width", DEFAULT_WING_WIDTH)
        self.aggregator = _SurfaceAggregator(params.get("atm_band", DEFAULT_ATM_BAND), low, high)
        self.min_strikes = params.get("min_two_sided_strikes", DEFAULT_MIN_TWO_SIDED_STRIKES)
        self.flush_rows = params.get("flush_rows", DEFAULT_FLUSH_ROWS)
        self.floor = params.get("lifecycle_start", params["start"])
        self.buffer, self.buffered = [], 0
        self.first_date = self.last_date = None
        self.rows_before_start = self.archive_unusable = 0
        self.first_seen, self.surface, self.writers = {}, [], {}
        self.rows_read = self.rows_outside_dte = self.rows_refused = 0
        self.refused_days, self.expiries_no_valid_day, self.expiries_few = [], 0, 0
        self.archive_rows = {}

    # -- feeding ---------------------------------------------------------------------------------

    def push(self, table):
        """Buffer one reader table; process the complete dates once enough rows are held."""
        table = _source_table(self.key, table)
        if table.num_rows:
            self.buffer.append(table)
            self.buffered += table.num_rows
        if self.buffered >= self.flush_rows:
            self.flush(final=False)

    def flush(self, final):
        """Process the buffered rows (all of them when ``final``, else up to the last complete date)."""
        import pyarrow as pa
        import pyarrow.compute as pc

        if not self.buffer:
            return
        table = pa.concat_tables(self.buffer)
        if final:
            complete, rest = table, None
        else:
            last = pc.max(table.column("date")).as_py()
            before = pc.less(table.column("date"), last)
            complete, rest = table.filter(before), table.filter(pc.invert(before))
            if not complete.num_rows:
                return
        self.buffer = [rest] if rest is not None and rest.num_rows else []
        self.buffered = rest.num_rows if rest is not None else 0
        self._process(complete)

    # -- one chunk -------------------------------------------------------------------------------

    def _check_chunk(self, table, df):
        """Refuse a chunk outside the declared bounds, out of date order or with an unknown right."""
        low, high = df["date"].min(), df["date"].max()
        if low < self.floor or high >= self.p["end_before"]:
            raise ValueError(f"{self.key}: {self.symbol} has rows dated {low}..{high}, outside "
                             f"[{self.floor}, end_before {self.p['end_before']}); "
                             "the reader's bound was not applied")
        if self.last_date is not None and low <= self.last_date:
            raise ValueError(f"{self.key}: {self.symbol} dates are not ascending ({low} after {self.last_date})")
        unknown = sorted({str(v) for v in df["right"].unique() if v not in _RIGHTS})
        if unknown:
            raise ValueError(f"{self.key}: {self.symbol} has right value(s) {unknown}; "
                             f"only {list(_RIGHTS)} are known")
        self.first_date = low if self.first_date is None else self.first_date
        self.last_date = high

    def _note_listings(self, df):
        """Record the first date each expiry is listed on, from every row whatever its DTE."""
        for expiry, first in df.groupby("expiration", sort=False)["date"].min().items():
            if expiry not in self.first_seen or first < self.first_seen[expiry]:
                self.first_seen[expiry] = first

    @staticmethod
    def _ordinals(series):
        """Map a column of ISO dates to day ordinals, one parse per distinct value."""
        table = {value: dt.date.fromisoformat(value).toordinal() for value in series.unique()}
        return series.map(table).to_numpy()

    def _process(self, table):
        """Filter one chunk of complete dates, write its archive rows and keep its surface rows."""
        import numpy as np

        df = table.to_pandas()
        self._check_chunk(table, df)
        self.rows_read += len(df)
        self._note_listings(df)
        dte = self._ordinals(df["expiration"]) - self._ordinals(df["date"])
        window = (df["date"] >= self.p["start"]).to_numpy()
        in_range = (dte >= 1) & (dte <= self.p["max_dte"])
        factor = df["date"].map(self.basis.factors).to_numpy(dtype=float)
        usable = window & in_range & np.isfinite(factor)
        self.rows_before_start += int((~window).sum())
        self.rows_outside_dte += int((window & ~in_range).sum())
        self.rows_refused += int((window & in_range & ~usable).sum())
        positions = np.flatnonzero(usable)
        if not len(positions):
            return
        rows = self._adjusted(df.iloc[positions], factor[positions])
        valid = quotes_valid(rows["bid"].to_numpy(dtype=float), rows["ask"].to_numpy(dtype=float))
        kept = self._kept_rows(rows, valid)
        self.rows_refused += int((~kept).sum())
        if not kept.any():
            return
        chosen = rows[kept]
        self.archive_unusable += self._unusable(chosen, valid[kept])
        piece = self.aggregator.aggregate(chosen).reset_index().rename(
            columns={"date": "quote_date", "expiration": "expiry"})
        piece["dte"] = self._ordinals(piece["expiry"]) - self._ordinals(piece["quote_date"])
        piece.insert(0, "symbol", self.symbol)
        self.surface.append(piece)
        self._write_archive(chosen, valid[kept])

    @staticmethod
    def _adjusted(rows, factor):
        """Return ``rows`` in the adjusted basis.

        Scaled by the factor ``f``: strike, underlying, bid and ask (price units), vega (``dP/d sigma``
        is a price: times ``f``) and gamma (``d2P/dS2`` is a price over a price squared: times
        ``1/f``), so ``log1p(|greek| x open_interest)`` has no level shift at a split. Unscaled:
        sizes, open interest, volume, IV and delta (dimensionless). Theta and rho are not read. The RAW
        strike is kept as ``strike_raw`` and the factor as ``basis_factor``.
        """
        rows = rows.copy()
        rows["strike_raw"] = rows["strike"].to_numpy(dtype=float)
        rows["basis_factor"] = factor
        for name in ("strike", "underlying_price", "bid", "ask", "vega"):
            rows[name] = rows[name].to_numpy(dtype=float) * factor
        rows["gamma"] = rows["gamma"].to_numpy(dtype=float) / factor
        return rows

    @staticmethod
    def _unusable(chosen, valid):
        """Count archived rows ``RawChainFeatureBuilder._usable_quotes`` would drop for a row-local reason.

        An invalid quote (null mark), a non-finite strike, size, open interest or IV, or an IV not above
        zero. Duplicate contracts are decided across rows and are not counted, so this is a lower bound.
        """
        import numpy as np

        finite = np.ones(len(chosen), dtype=bool)
        for name in ("strike", "bid_size", "ask_size", "open_interest", "implied_vol"):
            finite &= np.isfinite(chosen[name].to_numpy(dtype=float))
        iv = chosen["implied_vol"].to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            usable = valid & finite & (iv > 0) & (chosen["strike"].to_numpy(dtype=float) > 0)
        return int((~usable).sum())

    def _kept_rows(self, rows, valid):
        """Return the mask of rows whose expiry-day survives the refusal rules (counting the refused)."""
        import pandas as pd

        frame = pd.DataFrame({"date": rows["date"].to_numpy(), "expiration": rows["expiration"].to_numpy(),
                              "strike": rows["strike"].to_numpy(dtype=float), "valid": valid})
        day_valid = frame.groupby("date")["valid"].any()
        dead_days = day_valid.index[~day_valid.to_numpy()]
        self.refused_days.extend(str(d) for d in dead_days)
        keys = pd.MultiIndex.from_frame(frame[["date", "expiration"]])
        every = keys.unique()
        quoted = (frame[frame["valid"]].drop_duplicates(["date", "expiration", "strike"])
                  .groupby(["date", "expiration"]).size())
        strikes = quoted.reindex(every, fill_value=0).to_numpy()
        on_dead_day = every.get_level_values(0).isin(dead_days)
        enough = strikes >= self.min_strikes
        self.expiries_no_valid_day += int(on_dead_day.sum())
        self.expiries_few += int((~on_dead_day & ~enough).sum())
        return keys.isin(every[~on_dead_day & enough])

    def _archive_schema(self):
        """Return the Arrow schema of the chain archive."""
        import pyarrow as pa

        return pa.schema([(name, getattr(pa, self.ARCHIVE_TYPES[name])())
                          for name in (*ARCHIVE_COLUMNS, *ARCHIVE_EXTRA_COLUMNS)])

    def _write_archive(self, chosen, valid):
        """Append the kept rows, in the consumers' vocabulary, to their (symbol, year) files."""
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc

        bid, ask = chosen["bid"].to_numpy(dtype=float), chosen["ask"].to_numpy(dtype=float)
        mark = np.where(valid, (bid + ask) / 2, np.nan)
        source = {"type": "right", "implied_volatility": "implied_vol"}
        schema = self._archive_schema()
        arrays = []
        for field in schema:
            kind = field.type
            if field.name == "mark":
                values = mark
            else:
                values = chosen[source.get(field.name, field.name)]
            if pa.types.is_string(kind):
                arrays.append(pa.array(values.to_numpy(), type=kind))
            else:
                numbers = np.asarray(values, dtype=float)
                arrays.append(pa.array(numbers, type=pa.float64(), mask=np.isnan(numbers)).cast(kind))
        out = pa.Table.from_arrays(arrays, schema=schema).sort_by(
            [("date", "ascending"), ("expiration", "ascending"), ("strike", "ascending"),
             ("type", "ascending")])
        years = pc.utf8_slice_codeunits(out.column("date"), 0, 4)
        for year in sorted(pc.unique(years).to_pylist()):
            part = out.filter(pc.equal(years, year))
            self._writer(year).write_table(part)
            self.archive_rows[year] = self.archive_rows.get(year, 0) + part.num_rows

    def _writer(self, year):
        """Return the open writer of this symbol's archive file for ``year``."""
        import pyarrow.parquet as pq

        if year not in self.writers:
            path = self.work / "archive" / archive_relpath(self.symbol, year)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.writers[year] = pq.ParquetWriter(path, self._archive_schema())
        return self.writers[year]

    # -- the symbol's tables ---------------------------------------------------------------------

    def close(self):
        """Close every open archive writer (idempotent)."""
        for writer in self.writers.values():
            writer.close()
        self.writers = {}

    def finish(self):
        """Process the rest, close the archive, write the symbol's pieces and return its counts.

        Returns
        -------
        dict
            The symbol's manifest entry (without the reader's own report).
        """
        import pandas as pd

        self.flush(final=True)
        self.close()
        surface = (pd.concat(self.surface, ignore_index=True) if self.surface
                   else self._empty_surface())
        surface = surface.sort_values(["quote_date", "expiry"], kind="mergesort").reset_index(drop=True)
        lifecycle = surface[["symbol", "quote_date", "expiry"]].assign(
            first_seen_date=surface["expiry"].map(self.first_seen))
        lifecycle["first_seen_censored"] = (lifecycle["first_seen_date"] == self.first_date).astype(bool)
        own_iv, own_counts = self._own_iv(surface)
        pieces = self.work / _PIECES
        pieces.mkdir(exist_ok=True)
        self._write_table(pieces / f"surface-{self.number:06d}.parquet", surface[list(SURFACE_COLUMNS)],
                          _surface_schema())
        self._write_table(pieces / f"lifecycle-{self.number:06d}.parquet", lifecycle, _lifecycle_schema())
        target = self.work / "own_iv" / "index_daily" / f"{self.symbol}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write_table(target, own_iv, _own_iv_schema())
        years = surface["quote_date"].str[:4]
        by_year = {year: {"surface_rows": int((years == year).sum()),
                          "archive_rows": int(self.archive_rows.get(year, 0))}
                   for year in sorted(set(years) | set(self.archive_rows))}
        archived = sum(self.archive_rows.values())
        return {
            "rows_read": self.rows_read, "rows_before_start": self.rows_before_start,
            "rows_outside_dte": self.rows_outside_dte, "archive_rows_unusable": self.archive_unusable,
            "rows_refused": self.rows_refused, "rows_archived": int(archived),
            "surface_rows": int(len(surface)), "by_year": by_year,
            "first_scanned_date": self.first_date, "last_date": self.last_date,
            "left_censored_expiries": int(lifecycle.drop_duplicates("expiry")["first_seen_censored"].sum()),
            "basis": {"price_field": self.p.get("bar_price_field", DEFAULT_BAR_PRICE_FIELD),
                      "tolerance": self.basis.tolerance, "segments": self.basis.segments,
                      "factors": dict(sorted(self.basis.factors.items()))},
            "refused": {"days_no_bar": self.basis.refused["no_bar"],
                        "days_no_underlying": self.basis.refused["no_underlying"],
                        "days_basis_deviation": self.basis.refused["basis_deviation"],
                        "days_no_valid_quotes": sorted(set(self.refused_days)),
                        "expiries_no_valid_day": self.expiries_no_valid_day,
                        "expiries_few_two_sided_strikes": self.expiries_few},
            "own_iv": own_counts}

    @staticmethod
    def _empty_surface():
        """Return a surface frame with no rows."""
        import pandas as pd

        kinds = {"symbol": "object", "quote_date": "object", "expiry": "object",
                 "chain_contracts": "int64", "dte": "int64"}
        return pd.DataFrame({name: pd.Series([], dtype=kinds.get(name, "float64"))
                             for name in (*SURFACE_COLUMNS, "dte")})

    @staticmethod
    def _write_table(path, frame, schema):
        """Write ``frame`` as one Parquet file with ``schema``."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), path)

    def _own_iv(self, surface):
        """Return the daily nearest-to-target ATM-IV series and its counts."""
        import pandas as pd

        target, limit = (self.p.get("own_iv_target_dte", DEFAULT_OWN_IV_TARGET_DTE),
                         self.p.get("own_iv_max_gap_days", DEFAULT_OWN_IV_MAX_GAP_DAYS))
        usable = surface[surface["chain_atm_iv"].notna()].assign(gap=lambda f: (f["dte"] - target).abs())
        best = (usable.sort_values(["quote_date", "gap", "dte"], kind="mergesort")
                .drop_duplicates("quote_date", keep="first"))
        within = best[best["gap"] <= limit]
        series = pd.DataFrame({
            "symbol": self.symbol, "date": within["quote_date"].to_numpy(),
            "close": within["chain_atm_iv"].to_numpy(dtype=float) * OWN_IV_POINTS,
            "source_expiry": within["expiry"].to_numpy(), "source_dte": within["dte"].to_numpy("int64")})
        counts = {"rows": int(len(series)), "days_beyond_gap": int(len(best) - len(within)),
                  "days_without_atm": int(surface["quote_date"].nunique() - len(best))}
        return series, counts


def _surface_schema():
    """Return the Arrow schema of ``surface.parquet``."""
    import pyarrow as pa

    return pa.schema([
        (name, pa.string() if name in ("symbol", "quote_date", "expiry")
         else pa.int64() if name == "chain_contracts" else pa.float64()) for name in SURFACE_COLUMNS])


def _lifecycle_schema():
    """Return the Arrow schema of ``lifecycle.parquet``."""
    import pyarrow as pa

    return pa.schema([*[(name, pa.string()) for name in LIFECYCLE_COLUMNS],
                      *[(name, pa.bool_()) for name in LIFECYCLE_EXTRA_COLUMNS]])


def _own_iv_schema():
    """Return the Arrow schema of an ``own_iv`` shard."""
    import pyarrow as pa

    return pa.schema([("symbol", pa.string()), ("date", pa.string()), ("close", pa.float64()),
                      ("source_expiry", pa.string()), ("source_dte", pa.int64())])


class ThetaExactExpiryTables(Node):
    """Write the exact-expiry surface, lifecycle, chain archive and own_iv series from a chain source.

    Role ``transform``. Inputs: ``records``, a
    :class:`~dskit.pipeline.libs.chain_parquet.ChainRowSource` whose fields are
    :data:`SOURCE_FIELDS` (it must answer ``symbols()``, ``select()``, ``identity()``,
    ``contributing()`` and ``report()``); and ``bars``, an iterable of daily bar rows (dicts with
    ``symbol``, ``date``, the ``bar_price_field`` and ``split_coefficient``; ``open``, ``high``,
    ``low``, ``volume`` and ``dividend_amount`` are carried when present), for example a
    ``ParquetStreamRows`` over the split-adjusted stock bars. Outputs ``summary`` (small counts)
    and ``manifest`` (what ``manifest.json`` holds). See the module docstring for every definition.

    Parameters
    ----------
    params : dict
        ``output_root`` (str, REQUIRED): the directory to create; an existing one is refused.
        ``max_dte`` (int >= 1, REQUIRED): the farthest expiry, in calendar days, that is emitted.
        ``start`` and ``end_before`` (ISO dates, REQUIRED): the emitted window; ``end_before`` and
        ``lifecycle_start`` (``start`` when absent) are the reader's bounds, declared again and
        checked equal to the source's. ``atm_band`` (float in (0, 1), default 0.025),
        ``wing_delta`` (float in (0, 1), default 0.25), ``wing_delta_width`` (float > 0, default
        0.05; the band must stay inside (0, 1]), ``min_two_sided_strikes`` (int >= 1, default 2),
        ``own_iv_target_dte`` (int >= 1 and <= ``max_dte``, default 30), ``own_iv_max_gap_days``
        (int >= 0, default 15), ``flush_rows`` (int >= 1, default 1,000,000), ``bar_price_field``
        (non-empty str, default ``"close"``: the split-adjusted close, not ``adjusted_close``),
        ``basis_tolerance`` (float in (0, 1), default 0.01), ``lifecycle_start`` (ISO date <= ``start``,
        default ``start``: the first date scanned for expiry listings; rows from it up to ``start``
        feed ``first_seen_date`` only, and the reader's ``start`` must equal it).

    Examples
    --------
    Rebuild the 2017 inputs of two symbols from a reader source::

        node = ThetaExactExpiryTables("tables", {
            "output_root": "/data/exact-expiry-2017", "max_dte": 60,
            "start": "2017-01-03", "end_before": "2018-01-01"})
        out = node.run(ctx, {"records": chain_rows, "bars": split_adjusted_bars})
        out["summary"]["surface_rows"]
    """

    role = "transform"
    outputs = ("summary", "manifest")
    _PARAMS = ("output_root", "max_dte", "start", "end_before", "atm_band", "wing_delta",
               "wing_delta_width", "min_two_sided_strikes", "own_iv_target_dte",
               "own_iv_max_gap_days", "flush_rows", "bar_price_field", "basis_tolerance",
               "lifecycle_start")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        root = params.get("output_root")
        if not isinstance(root, str) or not root:
            problems.append(f"output_root must be a non-empty string, got {root!r}")
        check_int_param(problems, "max_dte", params.get("max_dte"), ge=1)
        dates = {name: _iso(params.get(name)) for name in ("start", "end_before")}
        for name, value in dates.items():
            if value is None:
                problems.append(f"{name} must be an ISO date text YYYY-MM-DD, got {params.get(name)!r}")
        if all(dates.values()) and dates["start"] >= dates["end_before"]:
            problems.append("start must be before end_before")
        cls._float_problems(problems, params)
        if "lifecycle_start" in params:
            first = _iso(params["lifecycle_start"])
            if first is None:
                problems.append(f"lifecycle_start must be an ISO date text YYYY-MM-DD, got "
                                f"{params['lifecycle_start']!r}")
            elif dates["start"] and first > dates["start"]:
                problems.append(f"lifecycle_start {first} is after start {dates['start']}: listings are "
                                "scanned from before the window, never after it")
        field = params.get("bar_price_field", DEFAULT_BAR_PRICE_FIELD)
        if not isinstance(field, str) or not field:
            problems.append(f"bar_price_field must be a non-empty string, got {field!r}")
        for name, floor in (("min_two_sided_strikes", 1), ("own_iv_target_dte", 1),
                            ("own_iv_max_gap_days", 0), ("flush_rows", 1)):
            if name in params:
                check_int_param(problems, name, params[name], ge=floor)
        target, horizon = params.get("own_iv_target_dte"), params.get("max_dte")
        if (isinstance(target, int) and isinstance(horizon, int) and not isinstance(target, bool)
                and not isinstance(horizon, bool) and target > horizon):
            problems.append(f"own_iv_target_dte {target} exceeds max_dte {horizon}: no expiry could serve it")
        return problems

    @staticmethod
    def _float_problems(problems, params):
        """Check the three fractional knobs and that the wing band stays inside (0, 1]."""
        def number(name):
            value = params.get(name)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        for name in ("atm_band", "wing_delta", "wing_delta_width", "basis_tolerance"):
            if name in params and (number(name) is None or not 0 < number(name) < 1):
                problems.append(f"{name} must be a number in (0, 1), got {params[name]!r}")
        center = number("wing_delta") if "wing_delta" in params else DEFAULT_WING_DELTA
        width = number("wing_delta_width") if "wing_delta_width" in params else DEFAULT_WING_WIDTH
        if (center is not None and width is not None and 0 < center < 1 and 0 < width < 1
                and (center - width <= 0 or center + width > 1)):
            problems.append(f"wing_delta_width {width} puts the wing band [{center - width:.4g}, "
                            f"{center + width:.4g}] outside (0, 1]")

    # -- the run ---------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Build the four tables and the manifest from the source, then publish them by one rename.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the chain row source; ``bars``: the daily bar rows.

        Returns
        -------
        dict
            ``{"summary": counts, "manifest": the manifest dict}``.

        Raises
        ------
        ValueError
            The output or its partial directory exists, the source's bounds differ from the
            params, a field is missing, a row lies outside the bounds or has an unknown right, or a
            symbol cannot be a path segment. Nothing is left behind on a failure.
        """
        source = inputs["records"]
        self._check_bounds(source)
        symbols = self._symbols(source)
        bars, ignored = self._bars(inputs["bars"], set(symbols))
        output = Path(self.params["output_root"])
        work = output.parent / f".{output.name}.partial"
        if output.exists():
            raise ValueError(f"{self.key}: {output} exists; refusing to overwrite an output (choose a new "
                             "output_root)")
        if work.exists():
            raise ValueError(f"{self.key}: {work} is a partial directory from an interrupted run; inspect "
                             "and remove it before running again")
        output.parent.mkdir(parents=True, exist_ok=True)
        work.mkdir()
        try:
            manifest = self._build(source, symbols, bars, ignored, work)
            atomic_write(str(work / "run.json"), (json.dumps(
                {"output_root": str(output), "seconds": round(self.seconds, 3)}, indent=2) + "\n").encode())
            os.rename(work, output)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        totals = manifest["totals"]
        return {"summary": {"output_root": str(output), "seconds": round(self.seconds, 3), **totals,
                            "refused_symbols": sorted(manifest["refused_symbols"])}, "manifest": manifest}

    def _check_bounds(self, source):
        """Refuse a source whose declared bounds differ from this node's (one bound, said twice)."""
        identity = source.identity()
        declared = {"end_before": self.params["end_before"],
                    "start": self.params.get("lifecycle_start", self.params["start"])}
        for name, value in declared.items():
            if identity.get(name) != value:
                label = "lifecycle_start (start when absent)" if name == "start" else name
                raise ValueError(f"{self.key}: {label} {value!r} differs from the reader's {name} "
                                 f"{identity.get(name)!r}; the two declarations must agree")

    #: The optional bar fields carried to the price shard, in column order.
    _BAR_EXTRAS = ("open", "high", "low", "volume", "dividend_amount")

    def _bars(self, rows, wanted):
        """Index the bar rows of the ``wanted`` symbols by symbol and date, refusing bad rows at intake.

        Every row is checked for its fields and its date (ISO and before ``end_before``); only the
        wanted symbols' rows are kept, as small tuples, so memory is the chain symbols' bars (one
        tuple of seven values per symbol-day), not the whole bar universe. A kept row with a null,
        non-finite or non-positive price, or a duplicate (symbol, date), is refused.

        Returns
        -------
        tuple
            ``({symbol: {date: (price, split_coefficient, extras)}}, ignored)`` where ``extras`` is the
            :attr:`_BAR_EXTRAS` values and ``ignored`` counts the symbols of the bars with no chain.
        """
        import math

        field = self.params.get("bar_price_field", DEFAULT_BAR_PRICE_FIELD)
        needed = [field if name == "close" else name for name in BAR_FIELDS]
        bars, unused = {}, set()
        for row in rows:
            missing = [name for name in needed if name not in row]
            if missing:
                raise ValueError(f"{self.key}: a bar row lacks field(s) {missing}")
            symbol, date = row["symbol"], row["date"]
            if _iso(date) is None or date >= self.params["end_before"]:
                raise ValueError(f"{self.key}: bar {symbol} {date!r} is not an ISO date before end_before "
                                 f"{self.params['end_before']}; bound the bars' reader")
            if symbol not in wanted:
                unused.add(symbol)
                continue
            price, split = row[field], row["split_coefficient"]
            if (isinstance(price, bool) or not isinstance(price, (int, float))
                    or not math.isfinite(price) or price <= 0):
                raise ValueError(f"{self.key}: bar {symbol} {date} has {field} {price!r}; a bar price must be "
                                 "a finite positive number")
            if date in bars.setdefault(symbol, {}):
                raise ValueError(f"{self.key}: duplicate bar for {symbol} on {date}")
            bars[symbol][date] = (float(price), float(split) if split is not None else None,
                                  tuple(row.get(name) for name in self._BAR_EXTRAS))
        return bars, len(unused)

    def _symbols(self, source):
        """Return the source's symbols, each usable as one path segment."""
        symbols = list(source.symbols())
        if not symbols:
            raise ValueError(f"{self.key}: the source holds no symbol")
        folded = {}
        for name in symbols:
            folded.setdefault(name.lower(), []).append(name)
        clash = sorted(v for v in folded.values() if len(v) > 1)
        if clash:
            raise ValueError(f"{self.key}: symbols {clash[:5]} are equal ignoring case and would share one "
                             "archive file (archive_relpath lowercases the symbol)")
        bad = [s for s in symbols if not _SYMBOL.match(s)]
        if bad:
            raise ValueError(f"{self.key}: symbol(s) {bad[:5]} cannot name a file (letters, digits, '.', '_' "
                             "and '-' only, not starting with a separator)")
        return symbols

    def _basis(self, one, symbol, bars):
        """Run pass 1 over one symbol and return its resolved :class:`_PriceBasis`."""
        basis = _PriceBasis({d: (price, split) for d, (price, split, _) in bars.get(symbol, {}).items()},
                            self.params.get("basis_tolerance", DEFAULT_BASIS_TOLERANCE), self.params["start"])
        for table in one.batches():
            basis.add(_source_table(self.key, table))
        basis.resolve()
        return basis

    def _ohlc(self, rows, dates, field):
        """Return ``{name: values}`` of open/high/low, or None unless EVERY bar has all three, positive.

        ``IndexCloseRows`` refuses a row carrying some of the three or a non-positive one, so a symbol
        with one incomplete bar drops all three columns (the close series is unaffected). A basis other
        than the split-adjusted ``close`` never carries OHLC.
        """
        import math

        if field != "close":
            return None
        out = {}
        for number, name in enumerate(self._BAR_EXTRAS[:3]):
            values = [rows[d][2][number] for d in dates]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                   for v in values):
                return None
            out[name] = [float(v) for v in values]
        return out

    def _write_prices(self, work, symbol, bars):
        """Write the symbol's bars as an ``index_daily`` shard (adjusted close, no split column).

        Returns
        -------
        int
            Rows written.
        """
        import pandas as pd

        rows = bars.get(symbol, {})
        if not rows:
            return 0
        field = self.params.get("bar_price_field", DEFAULT_BAR_PRICE_FIELD)
        dates = sorted(rows)
        frame = pd.DataFrame({"symbol": symbol, "date": dates, "close": [rows[d][0] for d in dates]})
        ohlc = self._ohlc(rows, dates, field)
        for number, name in enumerate(self._BAR_EXTRAS):
            if name in ("open", "high", "low"):
                if ohlc is not None:
                    frame[name] = ohlc[name]
                continue
            values = [rows[d][2][number] for d in dates]
            if any(v is not None for v in values):
                frame[name] = pd.to_numeric(pd.Series(values), errors="coerce")
        self.ohlc_status[symbol] = "kept" if ohlc is not None else (
            "absent" if field != "close" else "dropped_incomplete")
        target = work / "prices" / "index_daily" / f"{symbol}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        return len(frame)

    def _build(self, source, symbols, bars, ignored, work):
        """Run every symbol with bars, merge the pieces, write the manifest and return it."""
        started = time.monotonic()
        self.ohlc_status = {}
        refused = {symbol: "no_bar_rows" for symbol in symbols if symbol not in bars}
        info = {}
        for number, symbol in enumerate(s for s in symbols if s in bars):
            one = source.select([symbol])
            basis = self._basis(one, symbol, bars)
            run = _SymbolPass(self.key, self.params, symbol, work, number, basis)
            try:
                for table in one.batches():
                    run.push(table)
                info[symbol] = run.finish()
            finally:
                run.close()
            info[symbol]["prices_rows"] = self._write_prices(work, symbol, bars)
            info[symbol]["reader"] = json.loads(json.dumps(one.report(), default=str))
            info[symbol]["contributing_files"] = [list(item) for item in one.contributing(symbol)]
        self._merge(work, len(info))
        for symbol, status in self.ohlc_status.items():
            info[symbol]["prices_ohlc"] = status
        manifest = self._manifest(source, bars, ignored, refused, work, info, time.monotonic() - started)
        atomic_write(str(work / "manifest.json"),
                     (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        self.seconds = time.monotonic() - started
        return manifest

    @staticmethod
    def _merge(work, count):
        """Concatenate the per-symbol pieces into ``surface.parquet`` and ``lifecycle.parquet``."""
        import pyarrow.parquet as pq

        pieces = work / _PIECES
        for name, schema in (("surface", _surface_schema()), ("lifecycle", _lifecycle_schema())):
            with pq.ParquetWriter(work / f"{name}.parquet", schema) as writer:
                for number in range(count):
                    writer.write_table(pq.read_table(pieces / f"{name}-{number:06d}.parquet", schema=schema))
        shutil.rmtree(pieces)

    def _manifest(self, source, bars, ignored, refused, work, info, seconds):
        """Return the manifest: params, definitions, source identity, files with digests, counts."""
        import pyarrow.parquet as pq

        files = {}
        for path in sorted(p for p in work.rglob("*") if p.is_file()):
            files[path.relative_to(work).as_posix()] = {
                "sha256": _sha256(path), "bytes": path.stat().st_size,
                "rows": int(pq.read_metadata(path).num_rows)}
        keys = ("rows_read", "rows_before_start", "rows_outside_dte", "rows_refused", "rows_archived",
                "surface_rows", "archive_rows_unusable")
        totals = {key: sum(entry[key] for entry in info.values()) for key in keys}
        totals["symbols"] = len(info)
        return {
            "kind": "ThetaExactExpiryTables", "version": 1,
            "params": {k: v for k, v in self.params.items() if k != "output_root"},
            "own_iv_unit": "vix_points",
            "definitions": DEFINITIONS,
            "source": {"identity": json.loads(json.dumps(source.identity(), default=str)),
                       "symbols": list(info), "chain_symbols": list(source.symbols())},
            "refused_symbols": refused,
            "bars": {"symbols": len(bars), "symbols_ignored": ignored,
                     "price_field": self.params.get("bar_price_field", DEFAULT_BAR_PRICE_FIELD)},
            "files": files, "symbols": info, "totals": totals}
