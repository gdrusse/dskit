"""The kill test: a fair value must beat the market's own mid, after fees, on held-out rows.

For each scored row (a two-sided quote, a fair value, a label) the node compares what the model
and the market each believed with what happened:

- **Brier and log-loss** of the fair value and of the mid against the 0/1 label, per row, using
  dskit's own per-observation rules (:func:`dskit.pipeline.metrics.brier` and ``logloss``,
  imported, not rewritten; log-loss clips at their ``CLIP``). Lower is better; the DIFFERENCE
  fair minus mid is what the test reads: negative means the fair value was closer to the truth.
- **A naive take rule**: buy YES at the ask when ``fair - mid > fee_buy_yes + margin``, or buy NO
  at ``1 - bid`` when ``mid - fair > fee_buy_no + margin``; one contract's profit is the payout
  minus the price minus the fee. It trades at the TOUCH, so the half-spread is paid; the fee is
  Kalshi's taker fee from the fee column. A row with a missing fee takes no trade and is counted.

Rows are cut into declared ``segments`` by decision instant (``development`` and ``heldout`` in
the shipped document, one cut, no overlap). Choices such as the volatility window, the leads and
the margin belong to the development rows; the held-out rows are read once. Within a segment each
row also lands in a mid ``bucket`` (declared edges; the last bucket is closed on the right), a
``group`` per ``by`` field (e.g. lead time) and pooled ``all``. Each cell reports the mean of
the per-row differences with a CLUSTER-ROBUST standard error (rows sharing ``cluster_field``,
e.g. one event, are correlated and are summed before squaring), so a pile of near-identical
rows does not look like evidence. Nothing is a pass/fail gate: the report states numbers.

Rows that cannot be scored are counted, never dropped quietly, on ``summary["census"]``.
Outputs land in the run directory: ``kill_test.json`` (every score) and ``kill_test.md``.

Import cost: stdlib + dskit.
"""

import math

from dskit.pipeline.metrics import brier, logloss
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

from . import fields as f
from .clock import instant_ms

__all__ = ["KillTestScore"]

_ALL = "all"


def _cluster_se(pairs):
    """Return the cluster-robust standard error of the mean of ``(cluster, value)`` pairs, or None below two."""
    count = len(pairs)
    if count < 2:
        return None
    mean = sum(value for _, value in pairs) / count
    sums = {}
    for cluster, value in pairs:
        sums[cluster] = sums.get(cluster, 0.0) + (value - mean)
    return math.sqrt(sum(total * total for total in sums.values())) / count


def _mean(values):
    """Return the mean of ``values``, or None when empty."""
    return sum(values) / len(values) if values else None


class _Cell:
    """The running totals of one (model, segment, group, bucket) cell."""

    def __init__(self):
        self.brier, self.logloss, self.pnl = [], [], []
        self.brier_fair, self.brier_mid, self.ll_fair, self.ll_mid = [], [], [], []

    def add(self, cluster, scores, pnl):
        """Record one scored row: ``scores`` is (fair brier, mid brier, fair logloss, mid logloss)."""
        bf, bm, lf, lm = scores
        self.brier_fair.append(bf)
        self.brier_mid.append(bm)
        self.ll_fair.append(lf)
        self.ll_mid.append(lm)
        self.brier.append((cluster, bf - bm))
        self.logloss.append((cluster, lf - lm))
        if pnl is not None:
            self.pnl.append((cluster, pnl))

    def summary(self):
        """Return the cell's metrics as a dict."""
        pnl = [value for _, value in self.pnl]
        return {
            "n": len(self.brier),
            "brier_fair": _mean(self.brier_fair), "brier_mid": _mean(self.brier_mid),
            "brier_diff": _mean([v for _, v in self.brier]), "brier_diff_se": _cluster_se(self.brier),
            "logloss_fair": _mean(self.ll_fair), "logloss_mid": _mean(self.ll_mid),
            "logloss_diff": _mean([v for _, v in self.logloss]), "logloss_diff_se": _cluster_se(self.logloss),
            "n_trades": len(pnl), "pnl_total": sum(pnl), "pnl_mean": _mean(pnl),
            "pnl_se": _cluster_se(self.pnl),
            "hit_rate": _mean([1.0 if value > 0 else 0.0 for value in pnl]),
        }


class KillTestScore(Node):
    """Score fair values against the mid, by segment, group and mid bucket (role ``report``).

    Input ``records``: rows with ``label``, ``mid``, ``two_sided``, ``yes_bid``, ``yes_ask``,
    ``fee_buy_yes``, ``fee_buy_no``, ``decision_ms``, each fair-value column, each ``by`` field
    and the cluster field. Outputs ``scores`` (one dict per model x segment x group x bucket) and
    ``summary`` (the census, and the parameters the scores were made under).

    Parameters
    ----------
    params : dict
        All REQUIRED. ``fair_fields`` (non-empty list of distinct column names) the models;
        ``mid_edges`` (ascending list of >= 2 numbers) the bucket edges; ``margin`` (number >= 0)
        the take rule's margin over the fee; ``segments`` (non-empty dict) name -> ``{"start"?,
        "end"?}`` ISO instants, a row belongs when ``start <= decision < end``; ``by`` (list of
        field names, may be empty) the extra groupings, each pooled alone; ``cluster_field`` (str)
        the field rows are clustered on.

    Examples
    --------
    Score two models, held out from 15 September::

        node = KillTestScore("kill", {
            "fair_fields": ["fair_rms", "fair_bvol"], "mid_edges": [0, 0.25, 0.5, 0.75, 1],
            "margin": 0.02, "by": ["lead_minutes"], "cluster_field": "event_ticker",
            "segments": {"development": {"end": "2026-09-15T00:00:00Z"},
                         "heldout": {"start": "2026-09-15T00:00:00Z"}}})
        out = node.run(ctx, {"records": rows})
        # -> out["scores"][0]["brier_diff"] < 0 means the model beat the mid on that cell
    """

    role = "report"
    outputs = ("scores", "summary")
    _PARAMS = ("fair_fields", "mid_edges", "margin", "segments", "by", "cluster_field")

    @classmethod
    def _names_problems(cls, name, value, *, allow_empty):
        """Problems with a list of distinct non-empty names."""
        if (not isinstance(value, list) or (not value and not allow_empty)
                or any(not isinstance(v, str) or not v for v in value) or len(set(value)) != len(value)):
            return [f"{name} is required: a {'' if allow_empty else 'non-empty '}list of distinct names, got {value!r}"]
        return []

    @classmethod
    def _segments_problems(cls, segments):
        """Problems with the ``segments`` map."""
        if not isinstance(segments, dict) or not segments:
            return [f"segments is required: a non-empty map name -> {{start, end}}, got {segments!r}"]
        problems = []
        for name, bounds in segments.items():
            if (not isinstance(bounds, dict) or not bounds or set(bounds) - {"start", "end"}
                    or any(instant_ms(v) is None for v in bounds.values())):
                problems.append(f"segments[{name!r}] must be {{start?, end?}} ISO instants, got {bounds!r}")
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
        problems += cls._names_problems("fair_fields", params.get("fair_fields"), allow_empty=False)
        problems += cls._names_problems("by", params.get("by"), allow_empty=True)
        edges = params.get("mid_edges")
        if (not isinstance(edges, list) or len(edges) < 2 or any(not number_ok(e) for e in edges)
                or any(a >= b for a, b in zip(edges, edges[1:]))):
            problems.append(f"mid_edges is required: an ascending list of at least two numbers, got {edges!r}")
        if not (number_ok(params.get("margin")) and params["margin"] >= 0):
            problems.append(f"margin is required: a number >= 0, got {params.get('margin')!r}")
        problems += cls._segments_problems(params.get("segments"))
        if not isinstance(params.get("cluster_field"), str) or not params.get("cluster_field"):
            problems.append(f"cluster_field is required: a field name, got {params.get('cluster_field')!r}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem when ``records`` is not a list.
        """
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of rows, got {type(inputs.get('records')).__name__}"]
        return []

    def _segments_of(self, decision):
        """Name the segments whose ``[start, end)`` holds the decision instant."""
        names = []
        for name, bounds in self.params["segments"].items():
            start, end = instant_ms(bounds.get("start")), instant_ms(bounds.get("end"))
            if (start is None or decision >= start) and (end is None or decision < end):
                names.append(name)
        return names

    def _bucket(self, mid):
        """Label the mid bucket (the last one closed on the right), or None outside every bucket."""
        edges = self.params["mid_edges"]
        for lo, hi, last in zip(edges, edges[1:], [False] * (len(edges) - 2) + [True]):
            if lo <= mid < hi or (last and mid == hi):
                return f"[{lo:g},{hi:g}{']' if last else ')'}"
        return None

    def _groups(self, row):
        """Name the groups a row pools into: all, and one per ``by`` field."""
        return [_ALL] + [f"{name}={row.get(name)}" for name in self.params["by"]]

    def _pnl(self, row, fair):
        """Return one contract's profit under the take rule, 0 trades as None; ``False`` when a fee is missing."""
        fee_yes, fee_no = row.get(f.FEE_BUY_YES), row.get(f.FEE_BUY_NO)
        if not (number_ok(fee_yes) and number_ok(fee_no)):
            return False
        edge, margin, label = fair - row[f.MID], self.params["margin"], row[f.LABEL]
        if edge > fee_yes + margin:
            return label - row[f.YES_ASK] - fee_yes
        if -edge > fee_no + margin:
            return (1 - label) - (1.0 - row[f.YES_BID]) - fee_no
        return None

    def _observe(self, row, model, census):
        """Return ``(scores, pnl)`` for one row and model, or None when the row has no fair value."""
        fair, mid, label = row.get(model), row[f.MID], float(row[f.LABEL])
        if fair is None:
            census["models"][model]["no_fair"] += 1
            return None
        census["models"][model]["scored"] += 1
        pnl = self._pnl(row, fair)
        if pnl is False:
            census["models"][model]["fee_missing"] += 1
            pnl = None
        return (brier(fair, label), brier(mid, label), logloss(fair, label), logloss(mid, label)), pnl

    def _score(self, records):
        """Fold the rows into cells keyed by (model, segment, group, bucket) and a census."""
        census = {"rows": len(records), "not_two_sided": 0, "outside_segments": 0, "outside_buckets": 0,
                  "models": {m: {"no_fair": 0, "scored": 0, "fee_missing": 0} for m in self.params["fair_fields"]}}
        cells = {}
        for row in records:
            if row.get(f.TWO_SIDED) is not True:
                census["not_two_sided"] += 1
                continue
            segments = self._segments_of(row[f.DECISION_MS])
            if not segments:
                census["outside_segments"] += 1
                continue
            bucket = self._bucket(row[f.MID])
            census["outside_buckets"] += bucket is None
            cluster = row.get(self.params["cluster_field"])
            for model in self.params["fair_fields"]:
                observed = self._observe(row, model, census)
                if observed is None:
                    continue
                for segment in segments:
                    for group in self._groups(row):
                        for label in (_ALL, bucket):
                            if label is not None:
                                cells.setdefault((model, segment, group, label), _Cell()).add(cluster, *observed)
        return cells, census

    def _render(self, scores, census):
        """Render the scores as a short markdown report."""
        lines = ["# Kill test: fair value against the market mid, after fees", "",
                 "Negative Brier or log-loss difference = the fair value was closer to the outcome than the mid. "
                 f"The take rule buys at the touch when the edge exceeds the fee plus {self.params['margin']:g}. "
                 "Standard errors are cluster-robust. Read the held-out rows once.", ""]
        pooled = [s for s in scores if s["group"] == _ALL and s["bucket"] == _ALL]
        lines += ["| model | segment | n | Brier fair | Brier mid | diff (se) | logloss diff (se) | trades | mean pnl (se) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for s in pooled:
            lines.append(f"| {s['model']} | {s['segment']} | {s['n']} | {_fmt(s['brier_fair'])} | {_fmt(s['brier_mid'])} "
                         f"| {_fmt(s['brier_diff'])} ({_fmt(s['brier_diff_se'])}) "
                         f"| {_fmt(s['logloss_diff'])} ({_fmt(s['logloss_diff_se'])}) "
                         f"| {s['n_trades']} | {_fmt(s['pnl_mean'])} ({_fmt(s['pnl_se'])}) |")
        lines += ["", f"Rows read {census['rows']}; not two-sided {census['not_two_sided']}; "
                      f"outside every segment {census['outside_segments']}; per model {census['models']}.", ""]
        return "\n".join(lines)

    def run(self, ctx, inputs):
        """Score every model, write the report, return the scores.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; its run directory receives ``kill_test.json`` and ``kill_test.md``.
        inputs : dict
            ``records``: the rows to score.

        Returns
        -------
        dict
            ``{"scores": [...], "summary": {...}}``.
        """
        cells, census = self._score(inputs["records"])
        scores = [{"model": model, "segment": segment, "group": group, "bucket": bucket, **cell.summary()}
                  for (model, segment, group, bucket), cell in sorted(cells.items())]
        summary = {"census": census, "margin": self.params["margin"], "mid_edges": self.params["mid_edges"],
                   "segments": self.params["segments"]}
        self.write_artifact(ctx, "kill_test.json", {"scores": scores, "summary": summary})
        self.write_artifact_text(ctx, "kill_test.md", self._render(scores, census))
        self.log.info("scored %d cell(s) from %d row(s)", len(scores), census["rows"])
        return {"scores": scores, "summary": summary}


def _fmt(value):
    """Format a metric for the report, a dash when undefined."""
    return "-" if value is None else f"{value:.4f}"
