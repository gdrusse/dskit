"""StampSeriesStats: a stamped value stream reduced to one series per group, then tested — by import path.

Several studies end the same way: rows of ``(stamp, value)`` (a trade's return at its open date, an
event's P&L on its date, one date's premium) become a TIME SERIES whose mean, Newey-West t, Sharpe ratio
and bootstrap interval are the finding. :class:`StampSeriesStats` is that last step, so a document does not
need a node per statistic and a result cannot be computed on a differently-bucketed series than the one
reported.

**The series.** Rows are optionally split into groups (``by``: the group is the tuple of those fields'
values; the whole stream is always the group ``all``). Within a group, rows that fall in one ``bucket`` (a
day, an ISO week or a calendar month of the stamp) are first REDUCED to one value (``bucket_agg``: ``mean``,
equal weight, or ``sum``): this is the clustering step, so ten events in one week count as one observation of
that week and a daily P&L becomes a monthly one. The bucket's stamp is its earliest date. A row whose value
is not a finite number is dropped and counted; a missing or non-ISO stamp refuses by row index.

**The statistics** of a group's stamp-ordered series: the mean and the one-sided Newey-West statistics of
:func:`~dskit.pipeline.kinds_stats.series_statistics` (``lags`` or the Diebold-Mariano rule over
``h_steps``, with its band-spans-the-series guard), fewer than ``min_stamps`` stamps withholding the HAC
numbers, the per-period Sharpe ratio (never annualised on its own; with ``periods_per_year`` the annualised
one is ``sharpe * sqrt(periods_per_year)``) beside the analytic Mertens standard error of
:func:`~dskit.pipeline.stats.sharpe_ratio`, and with ``bootstrap`` the calendar-block percentile interval of
the mean and of the annualised Sharpe ratio. ``hurdle`` states a Sharpe the interval's lower bound must clear:
``passes`` is true only when the bootstrap is supported and ``lo > hurdle``.

**A contrast** (``contrast``: the field, the ``plus`` and ``minus`` values, ``paired``) compares two groups of
that field. ``paired`` takes the difference of the two bucket series on the stamps both have (unmatched stamps
are counted) and tests it like any series; unpaired it is the difference of the two groups' means with the
square root of the sum of the two Newey-West variances, a difference of independent series. A side with no usable row
leaves the contrast empty (``mean`` null, ``hac_withheld`` naming the side), never a refusal that would lose the run.

Import cost: stdlib and the pipeline core only. Wired by import path
(``dskit.pipeline.kinds_series:StampSeriesStats``); :data:`NODE_KINDS` is empty and nothing registers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_stats import block_bootstrap_interval, iso_date, series_statistics
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import sharpe_ratio

__all__ = ["BUCKETS", "BUCKET_AGGS", "NODE_KINDS", "StampSeriesStats"]

NODE_KINDS = ()
#: The calendar bucket a stamp falls in.
BUCKETS = ("day", "week", "month")
#: How the rows of one bucket reduce to its value.
BUCKET_AGGS = ("mean", "sum")
_ALL = "all"
_BOOTSTRAP_KEYS = ("block_days", "replicates", "seed", "level")
_CONTRAST_KEYS = ("field", "plus", "minus", "paired")


def _bucket_key(stamp_date, bucket):
    """The key rows share when they fall in one bucket."""
    if bucket == "day":
        return stamp_date
    if bucket == "week":
        return stamp_date.isocalendar()[:2]
    return (stamp_date.year, stamp_date.month)


def _annual_sharpe(factor):
    """The bootstrap statistic: the annualised Sharpe ratio of a resample, ``None`` without variance."""
    def take(sample):
        n = len(sample)
        if n < 2:
            return None
        mean = math.fsum(sample) / n
        var = math.fsum((x - mean) ** 2 for x in sample) / (n - 1)
        return None if var <= 0.0 else mean / math.sqrt(var) * factor
    return take


class StampSeriesStats(Node):
    """Reduce stamped values to per-group series and test them — by import path (role ``transform``).

    See the module docstring for the series, the statistics and the contrast.

    Outputs
    -------
    ``summary``
        ``stage``, ``rows_in``, ``dropped`` (by reason), ``groups`` (per group name: ``n_rows``,
        ``n_stamps``, ``mean``, ``sd``, ``se``, ``t``, ``p_value``, ``lags``, ``frac_pos``,
        ``hac_withheld``, ``sharpe``, ``sharpe_annualized``, ``sharpe_analytic`` and, when declared,
        ``ci`` / ``sharpe_ci`` / ``hurdle``) and ``contrast`` (null when undeclared).
    ``records``
        One row per group and bucket in stamp order: ``group``, ``stamp``, ``value``, ``n_rows``.

    Parameters
    ----------
    params : dict
        REQUIRED ``stamp_field`` (ISO dates) and ``value_field``. Optional: ``by`` (non-empty list of
        distinct row fields), ``bucket`` (``day`` default, ``week``, ``month``), ``bucket_agg`` (``mean``
        default, ``sum``), ``periods_per_year`` (number > 0), ``lags`` (int >= 0 or null), ``h_steps`` (int
        >= 1, default 1), ``min_stamps`` (int >= 2, default 2), ``bootstrap`` (``block_days``,
        ``replicates``, ``seed``, ``level``, all required), ``hurdle`` (a Sharpe; needs ``bootstrap`` and
        ``periods_per_year``) and ``contrast`` (``field``, ``plus``, ``minus`` and optional ``paired``,
        default true).

    Examples
    --------
    A monthly strategy return series with an annualised Sharpe and its block-bootstrap interval::

        node = StampSeriesStats("sharpe", {
            "stamp_field": "open_date", "value_field": "ret", "by": ["structure"],
            "bucket": "month", "bucket_agg": "sum", "periods_per_year": 12, "h_steps": 1,
            "bootstrap": {"block_days": 90, "replicates": 2000, "seed": 7, "level": 0.9},
            "hurdle": 0.5})
        out = node.run(ctx, {"records": rows})
        out["summary"]["groups"]["structure=put_write"]["hurdle"]["passes"]
    """

    role = "transform"
    outputs = ("summary", "records")
    _PARAMS = ("stamp_field", "value_field", "by", "bucket", "bucket_agg", "periods_per_year", "lags",
               "h_steps", "min_stamps", "bootstrap", "hurdle", "contrast")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep the study node out of served graphs."""
        return "forbidden"

    @classmethod
    def _names_problems(cls, params):
        """Problems with the two required field names and ``by``."""
        problems = []
        for name in ("stamp_field", "value_field"):
            value = params.get(name)
            if not is_node_ref(value) and (not isinstance(value, str) or not value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        by = params.get("by")
        if "by" in params and not is_node_ref(by):
            if not isinstance(by, list) or not by or not all(isinstance(v, str) and v for v in by):
                problems.append(f"by must be a non-empty list of field names, got {by!r}")
            elif len(set(by)) < len(by):
                problems.append("by names must be distinct")
        return problems

    @classmethod
    def _choice_problems(cls, params):
        """Problems with the bucket, its reduction and the numeric knobs."""
        problems = []
        for name, allowed in (("bucket", BUCKETS), ("bucket_agg", BUCKET_AGGS)):
            value = params.get(name, allowed[0])
            if not is_node_ref(value) and value not in allowed:
                problems.append(f"{name} must be one of {list(allowed)}, got {value!r}")
        ppy = params.get("periods_per_year")
        if "periods_per_year" in params and not is_node_ref(ppy) and not (
                number_ok(ppy) and not isinstance(ppy, bool) and ppy > 0):
            problems.append(f"periods_per_year must be a number > 0, got {ppy!r}")
        for name, floor in (("lags", 0), ("h_steps", 1), ("min_stamps", 2)):
            value = params.get(name)
            if name in params and value is not None and not is_node_ref(value):
                check_int_param(problems, name, value, ge=floor)
        return problems

    @classmethod
    def _bootstrap_problems(cls, params):
        """Problems with ``bootstrap`` and with a ``hurdle`` that needs it."""
        problems = []
        spec = params.get("bootstrap")
        if "bootstrap" in params and not is_node_ref(spec):
            if not isinstance(spec, dict):
                problems.append(f"bootstrap must be a dict, got {spec!r}")
            else:
                problems += [f"bootstrap: unknown key {k!r}" for k in sorted(set(spec) - set(_BOOTSTRAP_KEYS))]
                for key in ("block_days", "replicates"):
                    if key not in spec:
                        problems.append(f"bootstrap.{key} is required")
                    elif not is_node_ref(spec[key]):
                        check_int_param(problems, f"bootstrap.{key}", spec[key], ge=1)
                if "seed" not in spec:
                    problems.append("bootstrap.seed is required")
                elif not is_node_ref(spec["seed"]):
                    check_int_param(problems, "bootstrap.seed", spec["seed"], ge=0)
                level = spec.get("level")
                if "level" not in spec:
                    problems.append("bootstrap.level is required")
                elif not is_node_ref(level) and not (number_ok(level) and 0 < level < 1):
                    problems.append(f"bootstrap.level must be a number in (0, 1), got {level!r}")
        hurdle = params.get("hurdle")
        if "hurdle" in params and not is_node_ref(hurdle):
            if isinstance(hurdle, bool) or not number_ok(hurdle):
                problems.append(f"hurdle must be a number, got {hurdle!r}")
            if "bootstrap" not in params or "periods_per_year" not in params:
                problems.append("hurdle needs bootstrap and periods_per_year: it is a bound on the annualised Sharpe")
        return problems

    @classmethod
    def _contrast_problems(cls, params):
        """Problems with the ``contrast`` block."""
        spec = params.get("contrast")
        if "contrast" not in params or is_node_ref(spec):
            return []
        if not isinstance(spec, dict):
            return [f"contrast must be a dict, got {spec!r}"]
        problems = [f"contrast: unknown key {k!r}" for k in sorted(set(spec) - set(_CONTRAST_KEYS))]
        for key in ("field", "plus", "minus"):
            if key not in spec:
                problems.append(f"contrast.{key} is required")
        if not isinstance(spec.get("field", ""), str) or not spec.get("field", "x"):
            problems.append("contrast.field must be a non-empty field name")
        if "plus" in spec and "minus" in spec and spec["plus"] == spec["minus"]:
            problems.append("contrast.plus and contrast.minus must differ")
        if "paired" in spec and not isinstance(spec["paired"], bool):
            problems.append(f"contrast.paired must be a boolean, got {spec['paired']!r}")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for check in (cls._names_problems, cls._choice_problems, cls._bootstrap_problems,
                      cls._contrast_problems):
            problems += check(params)
        return problems

    # -- intake --------------------------------------------------------------------------------

    def _collect(self, rows):
        """Group the usable rows into ``{group: {bucket key: [stamp date, values]}}`` and count the drops."""
        p = self.params
        by, bucket = p.get("by") or [], p.get("bucket", "day")
        contrast_field = (p.get("contrast") or {}).get("field")
        groups, dropped = {}, {}
        for index, row in enumerate(rows):
            where = f"{self.key}: row {index}"
            if not isinstance(row, Mapping):
                raise ValueError(f"{where} must be a mapping, got {type(row).__name__}")
            if row.get(p["stamp_field"]) is None:
                raise ValueError(f"{where} lacks the stamp field {p['stamp_field']!r}")
            stamp = iso_date(row[p["stamp_field"]])
            value = row.get(p["value_field"])
            if isinstance(value, bool) or not number_ok(value):
                dropped["value_not_a_number"] = dropped.get("value_not_a_number", 0) + 1
                continue
            names = [_ALL]
            if by:
                names.append("|".join(f"{field}={row.get(field)}" for field in by))
            if contrast_field:
                names.append(f"{contrast_field}={row.get(contrast_field)}")
            key = _bucket_key(stamp, bucket)
            for name in dict.fromkeys(names):
                cell = groups.setdefault(name, {}).setdefault(key, [stamp, []])
                cell[0] = min(cell[0], stamp)
                cell[1].append(float(value))
        return groups, dropped

    def _series(self, cells):
        """The stamp-ordered ``(stamps, values, row counts)`` of one group's buckets."""
        agg = self.params.get("bucket_agg", "mean")
        ordered = sorted(cells.values(), key=lambda cell: cell[0])
        reduce = (lambda v: math.fsum(v)) if agg == "sum" else (lambda v: math.fsum(v) / len(v))
        return ([stamp for stamp, _ in ordered], [reduce(vals) for _, vals in ordered],
                [len(vals) for _, vals in ordered])

    # -- one block -----------------------------------------------------------------------------

    def _block(self, stamps, values, n_rows):
        """The statistics block of one stamp-ordered series."""
        p = self.params
        stats = series_statistics(values, p.get("lags"), p.get("h_steps", 1))
        if 0 < len(values) < p.get("min_stamps", 2) or (len(values) and len(values) < 2):
            stats.update(se=None, t=None, p_value=None, lags=None,
                         hac_withheld=f"fewer than min_stamps ({p.get('min_stamps', 2)}) stamps: no HAC statistic")
        n = len(values)
        sd = None
        if n >= 2:
            mean = math.fsum(values) / n
            sd = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))
        block = {"n_rows": n_rows, "n_stamps": n, "sd": sd, **stats,
                 "sharpe": None, "sharpe_annualized": None, "sharpe_analytic": None}
        if n >= 2:
            analytic = sharpe_ratio(values)
            block["sharpe_analytic"] = analytic
            block["sharpe"] = analytic["sharpe"]
            factor = math.sqrt(p["periods_per_year"]) if "periods_per_year" in p else None
            if analytic["sharpe"] is not None and factor is not None:
                block["sharpe_annualized"] = analytic["sharpe"] * factor
        if "bootstrap" in p and n >= 2:
            block["ci"] = block_bootstrap_interval(stamps, values, p["bootstrap"])
            if "periods_per_year" in p:
                block["sharpe_ci"] = block_bootstrap_interval(
                    stamps, values, p["bootstrap"], _annual_sharpe(math.sqrt(p["periods_per_year"])))
        if "hurdle" in p:
            low = (block.get("sharpe_ci") or {}).get("lo")
            block["hurdle"] = {"sharpe": p["hurdle"], "lower_bound": low,
                               "passes": low is not None and low > p["hurdle"]}
        return block

    def _contrast(self, groups):
        """The contrast block, or ``None`` when none is declared."""
        spec = self.params.get("contrast")
        if spec is None:
            return None
        field = spec["field"]
        series, paired = {}, spec.get("paired", True)
        empty = [side for side in ("plus", "minus") if f"{field}={spec[side]}" not in groups]
        if empty:
            return {"paired": paired, "plus": spec["plus"], "minus": spec["minus"], "n_stamps": [
                len(self._series(groups[f"{field}={spec[s]}"])[1]) if s not in empty else 0 for s in ("plus", "minus")],
                "mean": None, "se": None, "t": None,
                "hac_withheld": "no usable row on the side(s) " + ", ".join(repr(spec[s]) for s in empty)}
        for side in ("plus", "minus"):
            series[side] = self._series(groups[f"{field}={spec[side]}"])
        if paired:
            plus = dict(zip(series["plus"][0], series["plus"][1]))
            minus = dict(zip(series["minus"][0], series["minus"][1]))
            common = sorted(set(plus) & set(minus))
            values = [plus[s] - minus[s] for s in common]
            block = self._block(common, values, len(common))
            block["stamps_unmatched"] = len(set(plus) ^ set(minus))
        else:
            hac = [series_statistics(series[s][1], self.params.get("lags"), self.params.get("h_steps", 1))
                   for s in ("plus", "minus")]
            mean = (hac[0]["mean"] or 0.0) - (hac[1]["mean"] or 0.0)
            ses = [h["se"] for h in hac]
            se = None if None in ses else math.sqrt(ses[0] ** 2 + ses[1] ** 2)
            block = {"n_stamps": [len(series["plus"][1]), len(series["minus"][1])], "mean": mean, "se": se,
                     "t": None if not se else mean / se, "hac_withheld": ""
                     if se else "a side's HAC statistic is withheld"}
        block.update(paired=paired, plus=spec["plus"], minus=spec["minus"])
        return block

    def run(self, ctx, inputs):
        """Build every group's series and test it.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``records`` (REQUIRED): mapping rows carrying the stamp and value fields.

        Returns
        -------
        dict
            ``summary`` and ``records`` as the class docstring states.

        Raises
        ------
        ValueError
            A row that is not a mapping, lacks the stamp or has a non-ISO one; a contrast side with no
            usable row.
        """
        p = self.params
        groups, dropped = self._collect(inputs["records"])
        blocks, records = {}, []
        for name, cells in sorted(groups.items(), key=lambda kv: (kv[0] != _ALL, kv[0])):
            stamps, values, counts = self._series(cells)
            blocks[name] = self._block(stamps, values, sum(counts))
            records += [{"group": name, "stamp": s.isoformat(), "value": v, "n_rows": c}
                        for s, v, c in zip(stamps, values, counts)]
        summary = {
            "stage": f"{p.get('bucket', 'day')} series of {p['value_field']} by {p['stamp_field']}"
                     + (f" per {p['by']}" if p.get("by") else ""),
            "rows_in": len(inputs["records"]), "dropped": dropped, "groups": blocks,
            "contrast": self._contrast(groups)}
        self.log.info("%s: %d row(s), %d group(s)", self.key, summary["rows_in"], len(blocks))
        return {"summary": summary, "records": records}
