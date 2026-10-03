"""The tier-2 bar-feature pack: per-(entity, date) features from daily bars and option-trade bars.

Two transforms, both generic and both configured entirely by params (every
window, band, column and output field name is declared, nothing is a default
of the code):

* :class:`DailyBarFeatures` reads a daily bar stream (and optionally a
  benchmark close series) and writes volume/liquidity features and
  market-relative features, one row per input bar.
* :class:`AsTradedClose` rebuilds the point-in-time as-traded close from a
  split-adjusted close and the split ratios, so an option strike (always
  as traded) can be compared with a close on the same basis.
* :class:`TradeBarFeatures` reduces option-trade rows to one row per
  (entity, date): volume mix, trade and strike counts, volume-weighted
  log-moneyness against an underlying close, and a near-ATM straddle ratio.

**Leak rule.** A feature at date ``t`` is a function of rows at or before
``t`` only (trailing windows, same-day trades): the nodes never look ahead.
Whether ``t``'s own bar is KNOWN at the decision clock is the consumer's
call, made downstream with a strict-prior join (``observation-tables``).

**No imputation.** An undefined value (window not yet full, a missing
benchmark date, a zero where a log is taken, a leg that never traded) is
``None``; nothing is filled.

**Streaming.** Trade input may be any iterable of rows, a generator
included. With ``presorted`` true (rows grouped by entity and date) each
group is reduced and emitted the moment its key changes, so memory is one
group's cells, never the stream; without it every group is held until the
end. Neither path builds a list of the input rows.

Why tier 2: pandas and numpy are named only inside ``run()`` and the helpers
it calls, as the other packs do; the module imports with nothing installed.
Registration is by :data:`NODE_KINDS` / :func:`register`.
"""

import math
from datetime import date, datetime

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.observation_tables import iso_day
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "AsTradedClose", "DailyBarFeatures", "TradeBarFeatures", "register"]

#: Volume/liquidity feature keys a ``volume_liquidity.fields`` map may name.
VOLUME_KEYS = ("log_volume", "log_volume_ratio", "log_dollar_volume", "amihud")

#: Trade feature keys a ``fields`` map may name, with the params each needs.
TRADE_KEYS = {
    "log_call_put_ratio": (), "put_share": (), "log_total_volume": (),
    "log_trade_count": (), "n_strikes": (),
    "vw_log_moneyness": ("underlying",), "atm_straddle_ratio": ("underlying", "price", "band", "expiry"),
}

#: The one spelling of the window placeholder in ``relative_field``.
WINDOW_TOKEN = "{window}"

#: ``presorted`` when a document does not say.
DEFAULT_PRESORTED = False

#: ``strict_prior`` when a document does not say: a trade day serves only LATER entry dates.
DEFAULT_STRICT_PRIOR = True

_UNDERLYING = ("underlying_entity_field", "underlying_date_field", "underlying_close_field")


def _name(value):
    return isinstance(value, str) and bool(value)


def _names(value):
    return isinstance(value, list) and bool(value) and all(_name(v) for v in value)


def _iso(value, where):
    """ISO date text of a cell, refusing anything that is not a real ISO date or datetime."""
    if not isinstance(value, (str, date)):
        raise ValueError(f"{where}: {value!r} is not an ISO date")
    text = str(value)
    day = iso_day(value, where)
    if len(text) > 10:
        try:
            datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where}: {value!r} is not an ISO date") from None
    return day.isoformat()


def _real(value, where, missing_ok=False):
    """Float of a real-number cell; ``None``/NaN is ``nan`` only when ``missing_ok``, else refused."""
    if missing_ok and (value is None or (isinstance(value, float) and math.isnan(value))):
        return math.nan
    if not number_ok(value):
        raise ValueError(f"{where}: {value!r} is not a finite number")
    return float(value)


def _entity(value, where):
    """Return the entity key if it is a non-bool int or non-empty str, else refuse."""
    if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, str) and value)):
        raise ValueError(f"{where}: entity key {value!r} must be a non-empty str or an int")
    return value


def _same_entity_type(seen, value, where):
    """Add ``value``'s type to ``seen``, refusing a second type (they would not sort)."""
    seen.add(type(value))
    if len(seen) > 1:
        raise ValueError(f"{where}: entity keys of mixed types {sorted(t.__name__ for t in seen)}")


def _finite_or_none(value):
    return float(value) if value is not None and math.isfinite(value) else None


class _BarNode(Node):
    """Shared base: pure serving effect and the field-name checks."""

    role = "transform"
    outputs = ("records",)

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"``, a function of the wired inputs and params.

        Parameters
        ----------
        params : dict
            The declared params; unused.
        verified_run_evidence : dict
            The release's evidence; unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    @staticmethod
    def _required_names(problems, params, names):
        for name in names:
            value = params.get(name)
            if name not in params:
                problems.append(f"{name} is required")
            elif not is_node_ref(value) and not _name(value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")

    @staticmethod
    def _output_names_distinct(problems, params, declared):
        """Refuse two outputs sharing a name, or one equal to the entity/date field."""
        seen = {params.get("entity_field"), params.get("date_field")}
        for name in declared:
            if name in seen:
                problems.append(f"output field {name!r} is declared twice or collides with entity/date field")
            seen.add(name)


class DailyBarFeatures(_BarNode):
    """Volume/liquidity and market-relative features from a daily bar stream.

    Role ``transform``; inputs ``bars`` (rows with the entity, date, close
    and, for volume features, volume fields) and optional ``benchmark``
    (date and close rows, required when ``market_relative`` is declared);
    output ``records``: one row per bar, sorted by entity then date, holding
    the entity and date fields plus every declared feature, ``None`` where
    undefined. Windows count the entity's own consecutive bars; a return is
    a log return; the benchmark is aligned by date and a bar whose date (or
    window start date) has no benchmark close gets ``None``. This node is
    SAME-DAY by design: the value at ``t`` uses ``t``'s own bar, so the
    consumer decides the clock (strict-prior join) downstream. A ``None`` or
    NaN close or volume, or one that is zero or negative where a log is
    taken, makes the dependent features ``None`` (and the window features
    that span it); nothing is filled. Dates must be ISO, entity keys a
    non-empty str or int of one type, and empty bars give no rows.

    Parameters
    ----------
    params : dict
        ``entity_field``, ``date_field``, ``close_field`` (str, required).
        ``volume_field`` (str, required with ``volume_liquidity``).
        ``benchmark_date_field``, ``benchmark_close_field`` (str, required
        with ``market_relative``). ``volume_liquidity`` (dict: ``window``
        int >= 2; ``fields`` map of any of ``log_volume``,
        ``log_volume_ratio`` (log of volume over its trailing ``window``-bar
        mean, today included), ``log_dollar_volume``, ``amihud`` (trailing
        ``window``-bar mean of |log return| over dollar volume) to the output
        field name). ``market_relative`` (dict: ``windows`` list of int >= 1
        with ``relative_field`` containing ``{window}``: bar log return minus
        benchmark log return over the same dates; ``beta_window`` int >= 3
        with ``beta_field`` and/or ``corr_field``: trailing beta and
        correlation of 1-bar returns). At least one block is required.

    Examples
    --------
    Liquidity features over a 22-bar window and returns relative to a market series::

        node = DailyBarFeatures("bars", {
            "entity_field": "symbol", "date_field": "date", "close_field": "close",
            "volume_field": "volume",
            "benchmark_date_field": "date", "benchmark_close_field": "close",
            "volume_liquidity": {"window": 22, "fields": {"log_volume": "log_vol"}},
            "market_relative": {"windows": [1, 5], "relative_field": "rel_{window}d"}})
        out = node.run(ctx, {"bars": bars, "benchmark": market})
        # -> out["records"][0] == {"symbol": ..., "date": ..., "log_vol": ..., "rel_1d": ..., "rel_5d": ...}
    """

    _PARAMS = ("entity_field", "date_field", "close_field", "volume_field", "benchmark_date_field",
               "benchmark_close_field", "volume_liquidity", "market_relative")
    _VOLUME_PARAMS = ("window", "fields")
    _MARKET_PARAMS = ("windows", "relative_field", "beta_window", "beta_field", "corr_field")

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._required_names(problems, params, ("entity_field", "date_field", "close_field"))
        outputs = []
        if "volume_liquidity" not in params and "market_relative" not in params:
            problems.append("declare at least one of volume_liquidity, market_relative")
        if "volume_liquidity" in params:
            cls._required_names(problems, params, ("volume_field",))
            outputs += cls._volume_problems(problems, params["volume_liquidity"])
        if "market_relative" in params:
            cls._required_names(problems, params, ("benchmark_date_field", "benchmark_close_field"))
            outputs += cls._market_problems(problems, params["market_relative"])
        cls._output_names_distinct(problems, params, outputs)
        return problems

    @classmethod
    def _volume_problems(cls, problems, block):
        """Check ``volume_liquidity``; return its output field names."""
        if not isinstance(block, dict):
            problems.append(f"volume_liquidity must be an object, got {block!r}")
            return []
        reject_unknown_params(problems, {f"volume_liquidity.{k}": v for k, v in block.items()},
                              [f"volume_liquidity.{k}" for k in cls._VOLUME_PARAMS])
        check_int_param(problems, "volume_liquidity.window", block.get("window"), ge=2)
        return cls._fields_problems(problems, "volume_liquidity.fields", block.get("fields"), VOLUME_KEYS)

    @staticmethod
    def _fields_problems(problems, where, fields, allowed):
        if not isinstance(fields, dict) or not fields:
            problems.append(f"{where} must be a non-empty map of feature -> output field")
            return []
        bad = sorted(set(fields) - set(allowed))
        if bad:
            problems.append(f"{where}: unknown feature(s) {bad} - allowed: {sorted(allowed)}")
        names = list(fields.values())
        if not all(_name(v) for v in names):
            problems.append(f"{where} values must be non-empty strings")
            return []
        return names

    @classmethod
    def _market_problems(cls, problems, block):
        """Check ``market_relative``; return its output field names."""
        if not isinstance(block, dict):
            problems.append(f"market_relative must be an object, got {block!r}")
            return []
        reject_unknown_params(problems, {f"market_relative.{k}": v for k, v in block.items()},
                              [f"market_relative.{k}" for k in cls._MARKET_PARAMS])
        outputs = []
        has_rel = "windows" in block or "relative_field" in block
        has_beta = any(k in block for k in ("beta_window", "beta_field", "corr_field"))
        if not (has_rel or has_beta):
            problems.append("market_relative declares neither windows nor a beta window")
        if has_rel:
            outputs += cls._relative_problems(problems, block)
        if has_beta:
            check_int_param(problems, "market_relative.beta_window", block.get("beta_window"), ge=3)
            fields = [block[k] for k in ("beta_field", "corr_field") if k in block]
            if not fields:
                problems.append("market_relative.beta_window needs beta_field and/or corr_field")
            if not all(_name(f) for f in fields):
                problems.append("market_relative beta_field/corr_field must be non-empty strings")
            else:
                outputs += fields
        return outputs

    @staticmethod
    def _relative_problems(problems, block):
        windows, template = block.get("windows"), block.get("relative_field")
        if not isinstance(windows, list) or not windows or len(set(windows)) != len(windows):
            problems.append("market_relative.windows must be a non-empty list of distinct ints")
            return []
        for w in windows:
            check_int_param(problems, "market_relative.windows[]", w, ge=1)
        if not _name(template) or WINDOW_TOKEN not in template:
            problems.append(f"market_relative.relative_field must be a string containing {WINDOW_TOKEN}")
            return []
        return [template.replace(WINDOW_TOKEN, str(int(w))) for w in windows if isinstance(w, (int, float))]

    # -- running ------------------------------------------------------------

    def _bar_rows(self, rows):
        """Return the bars as validated (entity, ISO date, close, volume) tuples."""
        p, where = self.params, self.key
        volume = p["volume_field"] if "volume_liquidity" in p else None
        seen, out = set(), []
        for row in rows:
            for col in (p["entity_field"], p["date_field"], p["close_field"], volume):
                if col is not None and col not in row:
                    raise ValueError(f"{where}: bars lack field {col!r}")
            entity = _entity(row[p["entity_field"]], f"{where} bars")
            _same_entity_type(seen, entity, f"{where} bars")
            out.append((entity, _iso(row[p["date_field"]], f"{where} bars date"),
                        _real(row[p["close_field"]], f"{where} close", missing_ok=True),
                        _real(row[volume], f"{where} volume", missing_ok=True) if volume else math.nan))
        return out

    def _frame(self, rows):
        """Sorted frame of the bars with log close, refusing duplicate keys."""
        import numpy as np
        import pandas as pd

        out = pd.DataFrame(self._bar_rows(rows), columns=["entity", "day", "close", "volume"])
        if out.duplicated(["entity", "day"]).any():
            raise ValueError(f"{self.key}: duplicate (entity, date) bar rows")
        out = out.sort_values(["entity", "day"], kind="stable").reset_index(drop=True)
        out["lnc"] = np.log(out["close"].where(out["close"] > 0))
        return out

    def _benchmark(self, frame, rows):
        """Log benchmark close aligned onto the bars' dates (NaN where absent)."""
        import numpy as np
        import pandas as pd

        p, where = self.params, self.key
        if rows is None:
            raise ValueError(f"{where}: market_relative needs the benchmark input")
        days, closes = [], []
        for row in rows:
            for col in (p["benchmark_date_field"], p["benchmark_close_field"]):
                if col not in row:
                    raise ValueError(f"{where}: benchmark lacks field {col!r}")
            days.append(_iso(row[p["benchmark_date_field"]], f"{where} benchmark date"))
            closes.append(_real(row[p["benchmark_close_field"]], f"{where} benchmark close", missing_ok=True))
        if len(set(days)) != len(days):
            raise ValueError(f"{where}: duplicate benchmark dates")
        close = pd.Series(closes, dtype=float)
        series = pd.Series(np.log(close.where(close > 0)).to_numpy(), index=days, dtype=float)
        return frame["day"].map(series).astype(float)

    def _volume_columns(self, frame):
        """Add the declared volume/liquidity columns to ``frame``."""
        import numpy as np

        block = self.params["volume_liquidity"]
        window, fields = int(block["window"]), block["fields"]
        grouped = frame.groupby("entity", sort=False)
        volume, dollar = frame["volume"], frame["close"] * frame["volume"]
        wanted = {
            "log_volume": lambda: np.log(volume.where(volume > 0)),
            "log_dollar_volume": lambda: np.log(dollar.where(dollar > 0)),
            "log_volume_ratio": lambda: np.log(
                (volume / grouped["volume"].transform(lambda s: s.rolling(window, min_periods=window).mean()))
                .where(volume > 0)),
            "amihud": lambda: (frame["lnc"].groupby(frame["entity"], sort=False).diff().abs()
                               / dollar.where(dollar > 0)).groupby(frame["entity"], sort=False).transform(
                lambda s: s.rolling(window, min_periods=window).mean()),
        }
        for key, field in fields.items():
            frame[field] = wanted[key]()

    def _market_columns(self, frame, bench):
        """Add the declared market-relative columns to ``frame``."""
        block = self.params["market_relative"]
        entity = frame["entity"]
        frame["_m"] = bench
        by = frame.groupby("entity", sort=False)
        for w in map(int, block.get("windows", [])):
            own = frame["lnc"] - by["lnc"].shift(w)
            mkt = frame["_m"] - by["_m"].shift(w)
            frame[block["relative_field"].replace(WINDOW_TOKEN, str(int(w)))] = own - mkt
        if "beta_window" in block:
            self._beta_columns(frame, block, by["lnc"].diff(), by["_m"].diff(), entity)

    @staticmethod
    def _beta_columns(frame, block, own, mkt, entity):
        """Add trailing beta and correlation of 1-bar returns."""
        import pandas as pd

        w = int(block["beta_window"])
        pairs = pd.DataFrame({"y": own, "x": mkt, "e": entity})
        beta, corr = [], []
        for _, grp in pairs.groupby("e", sort=False):
            roll = grp["y"].rolling(w, min_periods=w)
            beta.append(roll.cov(grp["x"]) / grp["x"].rolling(w, min_periods=w).var())
            corr.append(roll.corr(grp["x"]))
        if "beta_field" in block:
            frame[block["beta_field"]] = pd.concat(beta)
        if "corr_field" in block:
            frame[block["corr_field"]] = pd.concat(corr)

    def _declared_fields(self):
        """Output field names in declaration order."""
        p = self.params
        out = list(p.get("volume_liquidity", {}).get("fields", {}).values())
        block = p.get("market_relative", {})
        out += [block["relative_field"].replace(WINDOW_TOKEN, str(int(w))) for w in block.get("windows", [])]
        out += [block[k] for k in ("beta_field", "corr_field") if k in block]
        return out

    def run(self, ctx, inputs):
        """Compute the declared features for every bar.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is a function of its inputs and params.
        inputs : dict
            ``bars``: iterable of mapping rows. ``benchmark``: iterable of
            date/close rows, required when ``market_relative`` is declared.

        Returns
        -------
        dict
            ``{"records": rows}`` sorted by entity then date, undefined as ``None``.

        Raises
        ------
        ValueError
            A required field is missing, a (entity, date) bar repeats, or
            the benchmark is absent or repeats a date.
        """
        frame = self._frame(inputs["bars"])
        if frame.empty:
            return {"records": []}
        if "volume_liquidity" in self.params:
            self._volume_columns(frame)
        if "market_relative" in self.params:
            self._market_columns(frame, self._benchmark(frame, inputs.get("benchmark")))
        p, fields = self.params, self._declared_fields()
        cells = {f: frame[f].to_numpy(dtype=float) for f in fields}
        records = [
            {p["entity_field"]: e, p["date_field"]: d, **{f: _finite_or_none(cells[f][i]) for f in fields}}
            for i, (e, d) in enumerate(zip(frame["entity"], frame["day"]))]
        self.log.info("computed %d daily bar feature row(s)", len(records))
        return {"records": records}


class AsTradedClose(_BarNode):
    """Derive the point-in-time as-traded close from a split-adjusted close and split ratios.

    Role ``transform``; input ``bars`` (rows with entity, ISO date, close
    and split-ratio fields); output ``records``: every input row, in input
    order, plus ``output_field`` = close times the product of the entity's
    split ratios dated STRICTLY AFTER the row (the split day's own close is
    already post-split). A row at ``t`` is thus a function of rows at or
    after ``t``'s splits only through the ratios known by the data's end:
    the value is the price a trader saw at ``t`` on the strike basis. A
    ``None`` or NaN close gives ``None``; a ratio that is missing or not
    positive is refused (nothing is imputed). Optional ``irregular_field``
    is 1 when any later ratio is not a whole ratio ``n`` or ``1/n`` within
    ``irregular_tolerance`` (a spin-off adjuster the data vendor books as a
    split): the as-traded close is then APPROXIMATE for the strike basis,
    since a spin-off adjusts the price but not the strikes' ratio.

    Parameters
    ----------
    params : dict
        ``entity_field``, ``date_field``, ``close_field``, ``split_field``,
        ``output_field`` (str, required). ``irregular_field`` (str) with
        ``irregular_tolerance`` (float > 0), both or neither.

    Examples
    --------
    Rebuild raw closes across a 10:1 split::

        node = AsTradedClose("raw", {
            "entity_field": "symbol", "date_field": "date", "close_field": "close",
            "split_field": "split", "output_field": "raw_close"})
        out = node.run(ctx, {"bars": bars})
        # -> out["records"][0]["raw_close"] == 100.0 for a 10.0 close before the split
    """

    _PARAMS = ("entity_field", "date_field", "close_field", "split_field", "output_field",
               "irregular_field", "irregular_tolerance")

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._required_names(problems, params, (
            "entity_field", "date_field", "close_field", "split_field", "output_field"))
        cls._output_names_distinct(problems, params, [params.get("output_field"), params.get("irregular_field")])
        tol = params.get("irregular_tolerance")
        if ("irregular_field" in params) != ("irregular_tolerance" in params):
            problems.append("declare irregular_field and irregular_tolerance together")
        elif "irregular_field" in params and not is_node_ref(tol) and not (
                isinstance(tol, (int, float)) and not isinstance(tol, bool) and tol > 0):
            problems.append(f"irregular_tolerance must be a number > 0, got {tol!r}")
        return problems

    @staticmethod
    def _whole(ratio, tol):
        """Say whether ``ratio`` is a whole split ratio ``n`` or ``1/n``."""
        for value in (ratio, 1.0 / ratio):
            if abs(value - round(value)) <= tol:
                return True
        return False

    def run(self, ctx, inputs):
        """Add the as-traded close to every bar.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``bars``: iterable of mapping rows.

        Returns
        -------
        dict
            ``{"records": rows}`` in input order, each a copy with the new field(s).

        Raises
        ------
        ValueError
            A field is missing, a date is not ISO, an (entity, date) repeats,
            or a split ratio is missing or not positive.
        """
        p, where = self.params, self.key
        rows, seen, groups = [], set(), {}
        for index, row in enumerate(inputs["bars"]):
            for col in (p["entity_field"], p["date_field"], p["close_field"], p["split_field"]):
                if col not in row:
                    raise ValueError(f"{where}: bars lack field {col!r}")
            entity = _entity(row[p["entity_field"]], f"{where} bars")
            _same_entity_type(seen, entity, f"{where} bars")
            day = _iso(row[p["date_field"]], f"{where} bars date")
            ratio = _real(row[p["split_field"]], f"{where} split ratio")
            if ratio <= 0:
                raise ValueError(f"{where}: split ratio {ratio!r} must be > 0")
            rows.append(dict(row))
            groups.setdefault(entity, []).append((day, index, ratio))
        tol = p.get("irregular_tolerance")
        for entity, items in groups.items():
            items.sort()
            if len({d for d, _, _ in items}) != len(items):
                raise ValueError(f"{where}: duplicate (entity, date) bar rows for {entity!r}")
            factor, approx = 1.0, 0
            for day, index, ratio in reversed(items):
                close = rows[index][p["close_field"]]
                rows[index][p["output_field"]] = (
                    None if close is None or not math.isfinite(close) else float(close) * factor)
                if "irregular_field" in p:
                    rows[index][p["irregular_field"]] = approx
                factor *= ratio
                approx = 1 if approx or (tol is not None and not self._whole(ratio, tol)) else 0
        return {"records": rows}


class _Cells:
    """One (entity, date) group's trade cells: (expiry, strike, right) -> [size, price*size, count]."""

    __slots__ = ("cells",)

    def __init__(self):
        self.cells = {}

    def add(self, cell, size, price):
        got = self.cells.get(cell)
        if got is None:
            got = self.cells[cell] = [0.0, 0.0, 0]
        got[0] += size
        got[1] += size * price
        got[2] += 1


class TradeBarFeatures(_BarNode):
    """Per (entity, date) features reduced from option-trade rows.

    Role ``transform``; outputs ``records`` and ``dropped_dates`` (the
    ``[entity, date]`` pairs strict mode dropped; empty when same-day);
    inputs ``trades`` (any iterable of rows, a
    generator included) and ``underlying`` (entity/date/close rows; the
    calendar when ``strict_prior``, and the closes the moneyness fields
    use); output ``records``: one row per (entity, date) holding the entity
    and date fields plus the declared features, ``None`` where undefined
    (a zero or absent side makes a ratio ``None``; nothing is filled). Order
    is by key, or the input's own order when ``presorted``. :meth:`stream`
    is the lazy form of the same reduction. Moneyness is
    ``ln(strike / underlying close)`` with the close of the trade's own
    date. Dates must be ISO, entity keys a non-empty str or int of one
    type, strikes, sizes and prices real numbers; empty trades give no rows.

    **Clock.** ``strict_prior`` (default true): the row dated ``t`` holds
    the features of the entity's trades on the latest calendar date strictly
    before ``t`` (the calendar is the entity's ``underlying`` dates), so an
    entry at ``t`` never sees ``t``'s trades; a last calendar date has no
    later entry and its row is dropped. A trade date absent from the entity's
    calendar (a weekend, say) has no entry date either and is likewise
    dropped, never carried forward; every dropped (entity, date) is listed in
    the ``dropped_dates`` output so the loss is visible. ``strict_prior: false`` labels
    features with their own trade date (same-day) and is refused without a
    non-empty ``clock_note`` recording why the bar is known by the decision
    time.

    **Memory.** Constant (one group's cells) only with ``presorted: true``.
    Otherwise every group is held until the input ends: O(distinct
    (entity, date, expiry, strike, side) cells), up to O(rows).

    Parameters
    ----------
    params : dict
        ``entity_field``, ``date_field``, ``right_field``, ``strike_field``,
        ``size_field`` (str, required). ``call_values`` / ``put_values``
        (non-empty, disjoint lists of the right field's values, required).
        ``expiry_field`` (str) or ``single_expiry`` (bool; true asserts every
        group holds one expiry): one of the two is required by
        ``atm_straddle_ratio``. ``price_field`` (str; needed by
        ``atm_straddle_ratio``). ``underlying_entity_field``,
        ``underlying_date_field``, ``underlying_close_field`` (str; required
        when ``strict_prior`` or a moneyness feature is declared).
        ``moneyness_band`` (float > 0; needed by ``atm_straddle_ratio``: only
        strikes with |ln(K/S)| within it count). ``strict_prior`` (bool,
        default ``DEFAULT_STRICT_PRIOR``) and ``clock_note`` (str; required
        and non-empty exactly when ``strict_prior`` is false). ``presorted``
        (bool, default ``DEFAULT_PRESORTED``: rows arrive grouped by entity
        and non-decreasing in date within an entity; a violation is refused).
        ``fields`` (map of feature -> output field name, non-empty):
        ``log_call_put_ratio`` (None unless both sides traded),
        ``put_share`` (put volume over total), ``log_total_volume``,
        ``log_trade_count``, ``n_strikes``, ``vw_log_moneyness``
        (size-weighted), ``atm_straddle_ratio`` (call vwap plus put vwap,
        over the close, at the in-band strike nearest the money that has BOTH
        sides traded in one expiry; ties go to the earlier expiry then the
        lower strike).

    Examples
    --------
    Put share and a near-the-money straddle ratio, same-day with a recorded clock::

        node = TradeBarFeatures("trades", {
            "entity_field": "symbol", "date_field": "date", "right_field": "right",
            "strike_field": "strike", "size_field": "size", "price_field": "price",
            "expiry_field": "expiry", "call_values": ["C"], "put_values": ["P"],
            "underlying_entity_field": "symbol", "underlying_date_field": "date",
            "underlying_close_field": "close", "moneyness_band": 0.05,
            "strict_prior": False, "clock_note": "bars close before the decision time",
            "fields": {"put_share": "put_share", "atm_straddle_ratio": "atm"}})
        out = node.run(ctx, {"trades": rows, "underlying": closes})
        # -> out["records"][0] == {"symbol": ..., "date": ..., "put_share": 0.5, "atm": 0.09}
    """

    outputs = ("records", "dropped_dates")

    _PARAMS = ("entity_field", "date_field", "right_field", "strike_field", "size_field",
               "call_values", "put_values", "expiry_field", "single_expiry", "price_field",
               *_UNDERLYING, "moneyness_band", "strict_prior", "clock_note", "presorted", "fields")

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._required_names(problems, params, (
            "entity_field", "date_field", "right_field", "strike_field", "size_field"))
        cls._sides_problems(problems, params)
        for name in ("expiry_field", "price_field", *_UNDERLYING):
            if name in params and not is_node_ref(params[name]) and not _name(params[name]):
                problems.append(f"{name} must be a non-empty string, got {params[name]!r}")
        for name, default in (("presorted", DEFAULT_PRESORTED), ("single_expiry", False)):
            value = params.get(name, default)
            if not is_node_ref(value) and not isinstance(value, bool):
                problems.append(f"{name} must be a boolean, got {value!r}")
        cls._clock_problems(problems, params)
        fields = params.get("fields")
        if not isinstance(fields, dict) or not fields:
            problems.append("fields is required: a non-empty map of feature -> output field")
            return problems
        bad = sorted(set(fields) - set(TRADE_KEYS))
        if bad:
            problems.append(f"fields: unknown feature(s) {bad} - allowed: {sorted(TRADE_KEYS)}")
        if not all(_name(v) for v in fields.values()):
            problems.append("fields values must be non-empty strings")
            return problems
        cls._output_names_distinct(problems, params, list(fields.values()))
        cls._needs_problems(problems, params, [k for k in fields if k in TRADE_KEYS])
        return problems

    @staticmethod
    def _clock_problems(problems, params):
        """Check ``strict_prior`` and its paired ``clock_note``."""
        strict = params.get("strict_prior", DEFAULT_STRICT_PRIOR)
        note = params.get("clock_note")
        if is_node_ref(strict) or is_node_ref(note):
            return
        if not isinstance(strict, bool):
            problems.append(f"strict_prior must be a boolean, got {strict!r}")
        elif not strict and not (isinstance(note, str) and note.strip()):
            problems.append("strict_prior false (same-day) requires a non-empty clock_note recording the bar clock")
        elif strict and "clock_note" in params:
            problems.append("clock_note is only meaningful with strict_prior false")

    @staticmethod
    def _sides_problems(problems, params):
        sides = {}
        for name in ("call_values", "put_values"):
            value = params.get(name)
            if name not in params or not (is_node_ref(value) or _names(value)):
                problems.append(f"{name} is required: a non-empty list of strings")
            elif _names(value):
                sides[name] = set(value)
        if len(sides) == 2 and sides["call_values"] & sides["put_values"]:
            problems.append("call_values and put_values must be disjoint")

    @staticmethod
    def _needs_problems(problems, params, keys):
        """Refuse a declared feature whose supporting params are absent."""
        have = {
            "underlying": all(n in params for n in _UNDERLYING),
            "price": "price_field" in params,
            "band": "moneyness_band" in params,
            "expiry": "expiry_field" in params or params.get("single_expiry") is True,
        }
        given = [n for n in _UNDERLYING if n in params]
        if given and len(given) < len(_UNDERLYING):
            problems.append(f"declare all of {list(_UNDERLYING)} or none; got {given}")
        if params.get("strict_prior", DEFAULT_STRICT_PRIOR) is True and not have["underlying"]:
            problems.append("strict_prior needs the underlying_*_field params (the entry calendar)")
        if "expiry_field" in params and params.get("single_expiry") is True:
            problems.append("declare expiry_field or single_expiry, not both")
        for key in keys:
            for need in TRADE_KEYS[key]:
                if not have[need]:
                    problems.append(f"fields.{key} needs the {need} param(s)")
        band = params.get("moneyness_band")
        if "moneyness_band" in params and not is_node_ref(band) and not (
                isinstance(band, (int, float)) and not isinstance(band, bool) and band > 0):
            problems.append(f"moneyness_band must be a number > 0, got {band!r}")

    # -- reducing -------------------------------------------------------------

    def _needs_underlying(self):
        p = self.params
        return p.get("strict_prior", DEFAULT_STRICT_PRIOR) or any(
            "underlying" in TRADE_KEYS[k] for k in p["fields"])

    def _closes(self, rows):
        """Map (entity, ISO date) -> underlying close; refuses an unwired input and duplicate rows."""
        p, where = self.params, self.key
        if rows is None:
            if self._needs_underlying():
                raise ValueError(f"{where}: the underlying input is required (calendar or moneyness fields)")
            return {}
        out, seen = {}, set()
        for row in rows:
            entity = _entity(row[p["underlying_entity_field"]], f"{where} underlying")
            _same_entity_type(seen, entity, f"{where} underlying")
            key = (entity, _iso(row[p["underlying_date_field"]], f"{where} underlying date"))
            if key in out:
                raise ValueError(f"{where}: duplicate underlying row for {key!r}")
            out[key] = _real(row[p["underlying_close_field"]], f"{where} underlying close")
        return out

    def _cell_of(self, row, seen):
        """Return ((entity, date), (expiry, strike, side), size, price) of one trade row, validated."""
        p = self.params
        where = self.key
        entity = _entity(row[p["entity_field"]], f"{where} trades")
        _same_entity_type(seen, entity, f"{where} trades")
        key = (entity, _iso(row[p["date_field"]], f"{where} trades date"))
        right = str(row[p["right_field"]])
        side = "C" if right in p["call_values"] else "P" if right in p["put_values"] else None
        if side is None:
            raise ValueError(f"{where}: right {right!r} is in neither call_values nor put_values")
        strike = _real(row[p["strike_field"]], f"{where} strike")
        size = _real(row[p["size_field"]], f"{where} size")
        if strike <= 0:
            raise ValueError(f"{where}: strike {strike!r} must be > 0")
        if size < 0:
            raise ValueError(f"{where}: size {size!r} must be >= 0")
        price = _real(row[p["price_field"]], f"{where} price") if "atm_straddle_ratio" in p["fields"] else 0.0
        expiry = str(row[p["expiry_field"]]) if "expiry_field" in p else ""
        return key, (expiry, strike, side), size, price

    def _groups(self, rows):
        """Yield (key, cells) per (entity, date): lazily when presorted, else after the whole input."""
        if self.params.get("presorted", DEFAULT_PRESORTED):
            yield from self._sorted_groups(rows)
            return
        held, seen = {}, set()
        for row in rows:
            key, cell, size, price = self._cell_of(row, seen)
            held.setdefault(key, _Cells()).add(cell, size, price)
        for key in sorted(held):
            yield key, held[key]

    def _sorted_groups(self, rows):
        """Group an input ordered by entity then non-decreasing date, refusing any violation."""
        closed, current, group, seen = set(), None, None, set()
        for row in rows:
            key, cell, size, price = self._cell_of(row, seen)
            if key != current:
                self._check_order(closed, current, key)
                if group is not None:
                    yield current, group
                    if key[0] != current[0]:
                        closed.add(current[0])
                current, group = key, _Cells()
            group.add(cell, size, price)
        if group is not None:
            yield current, group

    def _check_order(self, closed_entities, current, key):
        """Refuse an entity that returns, or a date that goes backwards within an entity."""
        if key[0] in closed_entities or (current is not None and key[0] == current[0] and key[1] < current[1]):
            raise ValueError(f"{self.key}: presorted input out of order at group {key!r}")

    @staticmethod
    def _log_or_none(value):
        return math.log(value) if value > 0 else None

    def _features(self, cells, close):
        """Return every feature this group can state; ``close`` may be None."""
        total = {"C": 0.0, "P": 0.0}
        count, strikes = 0, set()
        for (_, strike, side), (size, _, n) in cells.items():
            total[side] += size
            count += n
            strikes.add(strike)
        volume = total["C"] + total["P"]
        both = total["C"] > 0 and total["P"] > 0
        return {
            "log_call_put_ratio": math.log(total["C"] / total["P"]) if both else None,
            "put_share": total["P"] / volume if volume > 0 else None,
            "log_total_volume": self._log_or_none(volume),
            "log_trade_count": self._log_or_none(count),
            "n_strikes": len(strikes),
            "vw_log_moneyness": self._weighted_moneyness(cells, close, volume),
            "atm_straddle_ratio": self._straddle(cells, close),
        }

    @staticmethod
    def _weighted_moneyness(cells, close, volume):
        if close is None or close <= 0 or volume <= 0:
            return None
        return sum(size * math.log(strike / close) for (_, strike, _), (size, _, _) in cells.items()) / volume

    def _straddle(self, cells, close):
        """Call vwap plus put vwap over the close at the nearest in-band two-sided strike."""
        if "atm_straddle_ratio" not in self.params["fields"] or close is None or close <= 0:
            return None
        band = self.params["moneyness_band"]
        best = None
        for (expiry, strike, side), (size, notional, _) in cells.items():
            other = cells.get((expiry, strike, "P" if side == "C" else "C"))
            distance = abs(math.log(strike / close))
            if side != "C" or other is None or size <= 0 or other[0] <= 0 or distance > band:
                continue
            rank = (distance, expiry, strike)
            if best is None or rank < best[0]:
                best = (rank, notional / size + other[1] / other[0])
        return None if best is None else best[1] / close

    @staticmethod
    def _next_dates(closes):
        """Map (entity, date) -> the entity's next calendar date, from the underlying dates."""
        by_entity = {}
        for entity, day in closes:
            by_entity.setdefault(entity, []).append(day)
        nxt = {}
        for entity, days in by_entity.items():
            days.sort()
            nxt.update({(entity, a): b for a, b in zip(days, days[1:])})
        return nxt

    def stream(self, rows, closes):
        """Lazily reduce ``rows`` to one feature row per (entity, date).

        Parameters
        ----------
        rows : iterable of dict
            Trade rows; consumed once. With ``presorted`` a group is yielded
            as soon as the next key is read.
        closes : dict
            ``(entity, ISO date)`` -> underlying close; the calendar when
            ``strict_prior``. May be empty (then strict output is empty).

        Returns
        -------
        generator of dict
            Feature rows keyed by the entity and date fields.

        Raises
        ------
        ValueError
            A row is malformed or ``presorted`` input is out of order.
        """
        p = self.params
        fields = p["fields"]
        strict = p.get("strict_prior", DEFAULT_STRICT_PRIOR)
        entry = self._next_dates(closes) if strict else None
        self._dropped = []
        for (entity, day), group in self._groups(rows):
            label = entry.get((entity, day)) if strict else day
            if label is None:
                self._dropped.append([entity, day])
                continue
            feats = self._features(group.cells, closes.get((entity, day)))
            yield {p["entity_field"]: entity, p["date_field"]: label,
                   **{out: feats[key] for key, out in fields.items()}}

    def run(self, ctx, inputs):
        """Reduce the trade rows to per-(entity, date) features.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is a function of its inputs and params.
        inputs : dict
            ``trades``: iterable of mapping rows, read once. ``underlying``:
            iterable of entity/date/close rows; required when ``strict_prior``
            or a moneyness feature is declared.

        Returns
        -------
        dict
            ``{"records": rows}``.

        Raises
        ------
        ValueError
            The underlying input is missing or repeats a key, a date is not
            ISO, an entity key is bad, a right is neither a call nor a put
            value, a number is not real, a strike is not positive, a size is
            negative, or presorted input is out of order.
        """
        records = list(self.stream(inputs["trades"], self._closes(inputs.get("underlying"))))
        self.log.info("reduced trades to %d (entity, date) row(s)", len(records))
        return {"records": records, "dropped_dates": list(self._dropped)}


#: The pack's kinds.
NODE_KINDS = (("as-traded-close", AsTradedClose), ("daily-bar-features", DailyBarFeatures),
              ("trade-bar-features", TradeBarFeatures))


def register(registry=None):
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means the toolkit default. Idempotent.

    Returns
    -------
    None
        Registration is the effect.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
