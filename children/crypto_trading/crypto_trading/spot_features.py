"""Point-in-time spot, volatility and implied-vol features, strictly before the decision instant.

For each decision row this node reads the Binance 1-minute klines and BVOL index of the
row's asset and writes what was KNOWN just before ``decision_ms``:

- **The leak rule.** A kline is labelled by its bar START but is complete only at its
  ``close_time_ms``, so a bar is usable only when ``close_time_ms < decision_ms``, strictly
  (at 01:00:30 the bar that opened at 01:00:00 has not closed). A BVOL reading is usable only when
  ``calc_time_ms < decision_ms``: the row AT the decision instant is not yet known. Both use
  :func:`dskit.pipeline.libs.parquet_series.prior_index`, the one home of that rule. Every estimator
  is causal (the value at a bar reads that bar and earlier ones), so the bars the loaded days
  hold after the decision are never reached; the tests plant spikes there and in BVOL and assert
  nothing moves, with a control that proves the spike would have mattered.
- **No imputation.** A reading older than ``max_spot_age_ms`` / ``max_bvol_age_ms`` is missing
  (None, with a ``*_missing`` flag), not carried forward; a window that holds a missing minute
  yields None for that estimator only.
- **Units.** Times are epoch ms (Binance's, the pipeline's); every volatility column is the
  standard deviation of the log return per sqrt(second): the square root of dskit's per-bar
  VARIANCE (the estimators emit variance) divided by the bar's seconds, named ``<vol_column_prefix>
  <label>`` (``rv_rms_60``), or the annualised BVOL (``index_value * bvol_scale``) over
  ``sqrt(seconds_per_year)``.
- **Basis.** Strikes are in settlement-index dollars (CF Benchmarks' BRTI, USD) and Binance is a
  different instrument (USDT-quoted; on live data the index averaged about 22 USD BELOW Binance, which
  alone biased every fair value up by about 11 percentage points). The ``anchors`` input
  (:class:`~crypto_trading.anchors.StrikeAnchors`) carries observations of the index: each anchor is the
  index's 60-second average ending at ``anchor_ms``, known from ``known_ms``. Its Binance counterpart
  is the mean of the open and close of the ONE 1-minute bar that starts ``bar_ms`` before ``anchor_ms``
  (it closes at ``anchor_ms - 1 ms``, so it is strictly before the anchor is known) and ``basis = anchor /
  Binance``. A decision uses the latest anchor whose ``known_ms`` is STRICTLY before it and no older than
  ``max_basis_age_ms``; the row carries ``basis``, ``basis_age_ms``, ``basis_missing`` and
  ``spot_brti = spot * basis`` (the Binance spot in index units). An anchor with no bar to compare with is
  skipped; none usable means missing (None), never a stale or unit-less price. Anchors are rows of an
  asset, not of a market, so an hourly row can use the 15-minute series' anchors of the same asset.
- **Moneyness.** ``ln_floor_over_spot`` and ``ln_cap_over_spot`` are ``ln(K / spot_brti)`` for the
  strikes the row's payoff reads (None for the other); ``log_moneyness`` is the single strike's value for
  ``above`` / ``below`` and None for ``between``.

Klines are USDT-quoted, a proxy for the USD index Kalshi settles on, and CC BY-NC-SA: research
use only, so the node is forbidden in a served graph.

The streams are read through :class:`dskit.pipeline.libs.parquet_series.ParquetSeries` (ADR-0240)
and the estimators come from :mod:`dskit.pipeline.libs.vol_estimators` (ADR-0241), which emit a
per-bar VARIANCE: the square root and the per-second unit are this node's thin step. The ``manifests``
input, from a :class:`dskit.pipeline.libs.parquet_series.StreamManifests` node, puts the store into
the run identity; a store that moved since is refused.

Import cost: stdlib + dskit; numpy and pyarrow only when ``run`` computes.
"""

import math

from dskit.pipeline.binary_pricing import LOWER, UPPER, payoff
from dskit.pipeline.libs.parquet_series import ParquetSeries, prior_index, stream_problems
from dskit.pipeline.libs.vol_estimators import Bars, build_estimators
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f
from .ports import ListPortsNode

__all__ = ["SpotFeatures", "tape_name"]

_MS_PER_S = 1000
_KLINE_COLUMNS = ("open_time", "close_time", "open", "high", "low", "close")
_BVOL_COLUMNS = ("time", "value")
_ASSET_KEYS = ("series", "klines", "bvol")
_MONEYNESS = {LOWER: "ln_floor_over_spot", UPPER: "ln_cap_over_spot"}
_SPOT_AGE, _SPOT_MISSING = "spot_age_ms", "spot_missing"
_BVOL_IV, _BVOL_VOL, _BVOL_AGE, _BVOL_MISSING = "bvol_iv", "bvol_per_sqrt_s", "bvol_age_ms", "bvol_missing"
_LOG_MONEYNESS = "log_moneyness"


def tape_name(asset, tape):
    """Name the manifest of an asset's ``tape`` (``klines`` or ``bvol``): the key the document's manifests node and this node share.

    Parameters
    ----------
    asset : str
        The asset key (``BTC``).
    tape : str
        The kind of series.

    Returns
    -------
    str
        ``"<asset>_<tape>"``.
    """
    return f"{asset}_{tape}"


def _cell(value):
    """Return a numpy scalar as a Python float, or None when it is NaN."""
    value = float(value)
    return None if math.isnan(value) else value


class SpotFeatures(ListPortsNode):
    """Add point-in-time spot, volatility and BVOL columns to decision rows (role ``transform``).

    Inputs: ``records`` (decision rows with ``series``, ``decision_ms``, ``payoff`` and the
    strikes), ``manifests`` (a :class:`dskit.pipeline.libs.parquet_series.StreamManifests` output) and
    ``anchors`` (:class:`~crypto_trading.anchors.StrikeAnchors` rows; may be empty, then every basis is
    missing). Outputs: ``records`` (every row plus the columns above, one ``rv_*`` column per estimator)
    and ``provenance`` (per asset: the manifests read and the counts of missing readings).

    Parameters
    ----------
    params : dict
        All REQUIRED. ``root`` (str) the onboarding root. ``assets`` (dict) asset ->
        ``{"series": [...], "klines": {"source", "stream"}, "bvol": {"source", "stream"}}``; a
        series belongs to one asset and a row of any other series is refused. ``columns`` (dict)
        the files' column names: ``{"klines": {open_time, close_time, open, high, low, close},
        "bvol": {time, value}}``. ``day_relpath_template`` (str with one ``{day}``).
        ``bar_ms`` (int >= 1) the kline interval. ``estimators`` (list) specs for
        :func:`dskit.pipeline.libs.vol_estimators.build_estimators`. ``vol_column_prefix`` (non-empty
        str) what each volatility column's name starts with (``rv_`` makes ``rv_rms_60``).
        ``max_spot_age_ms`` and
        ``max_bvol_age_ms`` (int >= 1) how old a reading may be; ``max_basis_age_ms`` (int >= 1) how old the
        anchor behind the basis may be. ``bvol_scale`` (number > 0)
        turns the index value into an annualised fraction (0.01 when published in percent).
        ``seconds_per_year`` (number > 0) the annualisation basis of that index.

    Examples
    --------
    Add features for rows of two assets::

        node = SpotFeatures("spot", {
            "root": "./ob",
            "assets": {"BTC": {"series": ["KXBTC15M"],
                               "klines": {"source": "binance-btcusdt-1m", "stream": "files"},
                               "bvol": {"source": "binance-btcbvol", "stream": "files"}}},
            "columns": {"klines": {"open_time": "open_time_ms", "close_time": "close_time_ms",
                                   "open": "open", "high": "high", "low": "low", "close": "close"},
                        "bvol": {"time": "calc_time_ms", "value": "index_value"}},
            "day_relpath_template": "{day}.parquet", "bar_ms": 60000,
            "estimators": [{"kind": "rms", "window": 60}], "vol_column_prefix": "rv_",
            "max_spot_age_ms": 120000, "max_bvol_age_ms": 60000, "max_basis_age_ms": 1200000,
            "bvol_scale": 0.01, "seconds_per_year": 31536000})
        out = node.run(ctx, {"records": rows, "manifests": manifests, "anchors": anchors})
        # -> out["records"][0]["spot"] is the close of the last bar closed before decision_ms
    """

    role = "transform"
    outputs = ("records", "provenance")
    LIST_PORTS = ("records", "anchors")
    _PARAMS = ("root", "assets", "columns", "day_relpath_template", "bar_ms", "estimators", "vol_column_prefix",
               "max_spot_age_ms", "max_bvol_age_ms", "max_basis_age_ms", "bvol_scale", "seconds_per_year")

    @classmethod
    def _assets_problems(cls, assets):
        """Problems with the ``assets`` map, including a series claimed twice."""
        if not isinstance(assets, dict) or not assets:
            return [f"assets is required: a non-empty map asset -> spec, got {assets!r}"]
        problems, owner = [], {}
        for asset, spec in assets.items():
            if not isinstance(spec, dict) or set(spec) != set(_ASSET_KEYS):
                problems.append(f"assets[{asset!r}] must have exactly the keys {list(_ASSET_KEYS)}, got {spec!r}")
                continue
            series = spec["series"]
            if not isinstance(series, list) or not series or any(not isinstance(s, str) or not s for s in series):
                problems.append(f"assets[{asset!r}].series must be a non-empty list of strings, got {series!r}")
                continue
            for tape in ("klines", "bvol"):
                problems += stream_problems(f"assets[{asset!r}].{tape}", spec[tape])
            for name in series:
                if owner.setdefault(name, asset) != asset:
                    problems.append(f"series {name!r} is claimed by both asset {owner[name]!r} and {asset!r}")
        return problems

    @classmethod
    def _columns_problems(cls, columns):
        """Problems with the ``columns`` map."""
        expected = {"klines": _KLINE_COLUMNS, "bvol": _BVOL_COLUMNS}
        if not isinstance(columns, dict) or set(columns) != set(expected):
            return [f"columns is required: exactly the keys {sorted(expected)}, got {columns!r}"]
        problems = []
        for tape, names in expected.items():
            block = columns[tape]
            if (not isinstance(block, dict) or set(block) != set(names)
                    or any(not isinstance(v, str) or not v for v in block.values())):
                problems.append(f"columns.{tape} must map exactly {list(names)} to column names, got {block!r}")
        return problems

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
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if not isinstance(params.get("root"), str) or not params.get("root"):
            problems.append(f"root is required: the onboarding root, got {params.get('root')!r}")
        problems += cls._assets_problems(params.get("assets"))
        problems += cls._columns_problems(params.get("columns"))
        template = params.get("day_relpath_template")
        if not isinstance(template, str) or template.count("{day}") != 1:
            problems.append(f"day_relpath_template is required: a file name with one {{day}}, got {template!r}")
        for name in ("bar_ms", "max_spot_age_ms", "max_bvol_age_ms", "max_basis_age_ms"):
            if name not in params:
                problems.append(f"{name} is required")
            else:
                check_int_param(problems, name, params[name], ge=1)
        for name in ("bvol_scale", "seconds_per_year"):
            if not (number_ok(params.get(name)) and params[name] > 0):
                problems.append(f"{name} is required: a number > 0, got {params.get(name)!r}")
        prefix = params.get("vol_column_prefix")
        if not isinstance(prefix, str) or not prefix:
            problems.append(f"vol_column_prefix is required: a non-empty string, got {prefix!r}")
        if not isinstance(params.get("estimators"), list):
            problems.append(f"estimators is required: a list of estimator specs, got {params.get('estimators')!r}")
        else:
            try:
                build_estimators(params["estimators"])
            except ValueError as error:
                problems.append(f"estimators: {error}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``records`` or ``anchors`` port that is not a list or a ``manifests`` port that is not a dict.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per bad port.
        """
        problems = super().validate_inputs(inputs)
        if not isinstance(inputs.get("manifests"), dict):
            problems.append(f"manifests must be a StreamManifests output, got {type(inputs.get('manifests')).__name__}")
        return problems

    def _asset_of(self):
        """Map each series to its asset."""
        return {s: asset for asset, spec in self.params["assets"].items() for s in spec["series"]}

    def _tape(self, asset, tape, wired, columns, time_column):
        """Open one asset's tape and refuse it when the store moved since the manifest was fingerprinted."""
        spec = self.params["assets"][asset][tape]
        series = ParquetSeries(self.params["root"], spec["source"], spec["stream"],
                               self.params["day_relpath_template"], time_column, columns)
        name = tape_name(asset, tape)
        held = wired.get(name)
        if held is None:
            raise ValueError(f"{self.key}: the manifests input has no {name!r}; wire a StreamManifests that names it")
        series.require_manifest(held, label=f"{self.key}: {name}")
        return series, series.manifest()

    def _bars(self, series, instants, estimators, anchors):
        """Load the klines that can matter for ``instants`` and their anchors; return ``(bars, columns)``."""
        cols = self.params["columns"]["klines"]
        bar_ms = self.params["bar_ms"]
        reach = max([e.lookback_bars() for e in estimators] + [1]) + 1
        first = int(instants.min()) - reach * bar_ms - self.params["max_spot_age_ms"]
        recent = [a[f.ANCHOR_MS] for a in anchors
                  if int(instants.min()) - self.params["max_basis_age_ms"] <= a[f.ANCHOR_KNOWN_MS] < int(instants.max())]
        got = series.load_span(min([first] + [m - bar_ms for m in recent]), int(instants.max()))
        bars = Bars(got[cols["open_time"]], bar_ms, high=got[cols["high"]], low=got[cols["low"]],
                    close=got[cols["close"]])
        return bars, got

    def _kline_arrays(self, asset, instants, wired, estimators, anchors):
        """Return per-instant spot, age, basis and one volatility array per estimator, NaN where unknown."""
        import numpy as np

        cols = self.params["columns"]["klines"]
        series, manifest = self._tape(asset, "klines", wired, [cols[k] for k in _KLINE_COLUMNS],
                                      cols["close_time"])
        names = [f.SPOT, _SPOT_AGE, f.BASIS, f.BASIS_AGE_MS] + [self._vol_column(e) for e in estimators]
        out = {name: np.full(instants.size, np.nan) for name in names}
        bars, got = self._bars(series, instants, estimators, anchors)
        closes = got[cols["close_time"]]
        if closes.size == 0:
            return out, manifest
        index = prior_index(closes, instants)
        safe = np.where(index >= 0, index, 0)
        age = instants - closes[safe]
        usable = (index >= 0) & (age <= self.params["max_spot_age_ms"])
        out[f.SPOT] = np.where(usable, bars.close[safe], np.nan)
        out[_SPOT_AGE] = np.where(usable, age, np.nan)
        seconds = self.params["bar_ms"] / _MS_PER_S
        for estimator in estimators:
            with np.errstate(invalid="ignore"):
                out[self._vol_column(estimator)] = np.where(
                    usable, np.sqrt(estimator.variance(bars)[safe] / seconds), np.nan)
        out[f.BASIS], out[f.BASIS_AGE_MS] = self._basis_arrays(bars, got[cols["open"]], anchors, instants)
        return out, manifest

    def _vol_column(self, estimator):
        """Name an estimator's output column: the document's prefix and the estimator's own label."""
        return self.params["vol_column_prefix"] + estimator.label()

    def _basis_arrays(self, bars, opens, anchors, instants):
        """Return per-instant basis and its age from the latest usable anchor known strictly before it."""
        import numpy as np

        bar_ms = self.params["bar_ms"]
        known, basis = [], []
        for anchor in anchors:
            at = int(np.searchsorted(bars.open_time, anchor[f.ANCHOR_MS] - bar_ms))
            if at >= bars.open_time.size or bars.open_time[at] != anchor[f.ANCHOR_MS] - bar_ms:
                continue  # no bar to compare with: not an anchor
            reference = (opens[at] + bars.close[at]) / 2.0
            if np.isfinite(reference) and reference > 0 and number_ok(anchor[f.ANCHOR_VALUE]) and anchor[f.ANCHOR_VALUE] > 0:
                known.append(anchor[f.ANCHOR_KNOWN_MS])
                basis.append(anchor[f.ANCHOR_VALUE] / reference)
        empty = np.full(instants.size, np.nan)
        if not known:
            return empty, empty.copy()
        order = np.argsort(known, kind="stable")
        known, basis = np.asarray(known, dtype=np.int64)[order], np.asarray(basis)[order]
        index = prior_index(known, instants)
        safe = np.where(index >= 0, index, 0)
        age = instants - known[safe]
        usable = (index >= 0) & (age <= self.params["max_basis_age_ms"])
        return np.where(usable, basis[safe], np.nan), np.where(usable, age, np.nan)

    def _bvol_arrays(self, asset, instants, wired):
        """Return per-instant BVOL value and age (NaN where unknown)."""
        cols = self.params["columns"]["bvol"]
        series, manifest = self._tape(asset, "bvol", wired, [cols["time"], cols["value"]], cols["time"])
        got = series.prior(instants, self.params["max_bvol_age_ms"])
        return {"value": got[cols["value"]], "age": got["age_ms"]}, manifest

    def _moneyness(self, row, spot):
        """Return the ln(K / S) columns for the strikes the row's payoff reads, S in index units."""
        shape = payoff(row[f.PAYOFF])
        out = {name: None for name in _MONEYNESS.values()}
        if spot is not None:
            for bound in shape.bounds:
                out[_MONEYNESS[bound]] = math.log(row[f.BOUND_FIELDS[bound]] / spot)
        single = len(shape.bounds) == 1
        out[_LOG_MONEYNESS] = out[_MONEYNESS[shape.bounds[0]]] if single else None
        return out

    def _row(self, record, k, kline, bvol, estimators):
        """Build the output row for input ``record`` at position ``k`` of its asset's arrays."""
        spot, age = _cell(kline[f.SPOT][k]), _cell(kline[_SPOT_AGE][k])
        basis, basis_age = _cell(kline[f.BASIS][k]), _cell(kline[f.BASIS_AGE_MS][k])
        spot_brti = None if spot is None or basis is None else spot * basis
        value, bvol_age = _cell(bvol["value"][k]), _cell(bvol["age"][k])
        iv = None if value is None else value * self.params["bvol_scale"]
        row = dict(record)
        row.update({f.SPOT: spot, _SPOT_AGE: None if age is None else int(age), _SPOT_MISSING: spot is None})
        row.update({self._vol_column(e): _cell(kline[self._vol_column(e)][k]) for e in estimators})
        row.update({f.BASIS: basis, f.BASIS_AGE_MS: None if basis_age is None else int(basis_age),
                    f.BASIS_MISSING: basis is None, f.SPOT_BRTI: spot_brti})
        row.update(self._moneyness(record, spot_brti))
        row.update({_BVOL_IV: iv, _BVOL_VOL: None if iv is None else iv / math.sqrt(self.params["seconds_per_year"]),
                    _BVOL_AGE: None if bvol_age is None else int(bvol_age), _BVOL_MISSING: iv is None})
        return row

    def _anchors_by_asset(self, anchors, asset_of):
        """Group the anchor rows by the asset of their series; refuse a series no asset claims."""
        by_asset = {}
        for anchor in anchors:
            asset = asset_of.get(anchor[f.SERIES])
            if asset is None:
                raise ValueError(f"{self.key}: anchor {anchor.get(f.TICKER)!r} has series {anchor[f.SERIES]!r}, "
                                 f"which no asset claims; assets: {self.params['assets']}")
            by_asset.setdefault(asset, []).append(anchor)
        return by_asset

    def run(self, ctx, inputs):
        """Add the point-in-time columns to every row.

        Parameters
        ----------
        ctx : NodeContext or None
            The run frame; its run directory receives ``provenance.json`` (the run record holds only shapes).
        inputs : dict
            ``records``, ``manifests`` and ``anchors``.

        Returns
        -------
        dict
            ``{"records": [...], "provenance": {...}}``; rows keep their input order.

        Raises
        ------
        ValueError
            On a row or an anchor of a series no asset claims, a store that moved since its
            manifest, a drifted or malformed file.
        """
        import numpy as np

        asset_of, rows = self._asset_of(), inputs["records"]
        positions = {}
        for position, record in enumerate(rows):
            asset = asset_of.get(record[f.SERIES])
            if asset is None:
                raise ValueError(f"{self.key}: row {record.get(f.TICKER)!r} has series {record[f.SERIES]!r}, "
                                 f"which no asset claims; assets: {self.params['assets']}")
            positions.setdefault(asset, []).append(position)
        estimators = build_estimators(self.params["estimators"])
        by_asset = self._anchors_by_asset(inputs["anchors"], asset_of)
        out, provenance = [None] * len(rows), {}
        for asset, where in positions.items():
            instants = np.array([rows[p][f.DECISION_MS] for p in where], dtype=np.int64)
            kline, kline_manifest = self._kline_arrays(asset, instants, inputs["manifests"], estimators,
                                                       by_asset.get(asset, []))
            bvol, bvol_manifest = self._bvol_arrays(asset, instants, inputs["manifests"])
            for k, position in enumerate(where):
                out[position] = self._row(rows[position], k, kline, bvol, estimators)
            provenance[asset] = {"rows": len(where), "klines": kline_manifest, "bvol": bvol_manifest,
                                 "spot_missing": int(np.isnan(kline[f.SPOT]).sum()),
                                 "basis_missing": int(np.isnan(kline[f.BASIS]).sum()),
                                 "bvol_missing": int(np.isnan(bvol["value"]).sum())}
        self.log.info("features for %d row(s) over %d asset(s)", len(rows), len(positions))
        if ctx is not None:
            self.write_artifact(ctx, "provenance.json", provenance)
        return {"records": out, "provenance": provenance}
