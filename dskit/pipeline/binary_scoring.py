"""Score a model's probability of a binary outcome against the market's own, with a held-out gate.

A model that says P(YES) is only worth something if it beats what the market already says. For
each scored row (a model probability, the market's probability, a 0/1 label) the node compares
what each believed with what happened:

- **Brier and log-loss** of the model and of the market, per row, using the toolkit's own
  per-observation rules (:func:`dskit.pipeline.metrics.brier` and ``logloss``, imported, not
  rewritten; log-loss clips at their ``CLIP``). Lower is better; the DIFFERENCE model minus
  market is what is read: negative means the model was closer to the truth.
- **A naive take rule**: buy YES at the ask when ``model - market > fee_yes + margin``, or buy NO
  at ``1 - bid`` when ``market - model > fee_no + margin``; one contract's profit is the payout
  minus the price minus the fee. It trades at the TOUCH, so the half-spread is paid. The fees are
  columns, per contract, supplied by whatever fee model the project runs upstream (for instance
  :mod:`dskit.pipeline.fee_mechanics`), never a rate in here. A row with a missing fee or quote
  takes no trade and is counted.

Timing is a contract the columns carry, not one this node can check: the model value, the market
value and the quote are all as of ONE information instant, and a trade can only fill later (see
:mod:`dskit.pipeline.binary_pricing`). A row whose fill is not before settlement was never priced
there, so it carries no model value and is counted here as ``no_forecast``.

The held-out gate. Rows are cut into declared ``segments`` by the instant their LABEL is settled
(epoch milliseconds, half-open ``[start_ms, end_ms)``): every row of one event lands in one
segment even when its information instants straddle a cut, and segments may not overlap, so no
row is in two of them. Choices such as a volatility window or a margin belong to the development
rows. ``report_segments`` names the segments whose numbers are computed and written; a
held-out segment left out of it is never printed by accident, and reading it is a deliberate edit
of the document (a new identity hash, so every held-out read is a recorded run).

Within a segment each row also lands in a market ``bucket`` (declared edges; the last bucket is
closed on the right), a ``group`` per ``by`` field and pooled ``all``. Each cell reports the mean of
the per-row differences with a CLUSTER-ROBUST standard error, the toolkit's own, from
:func:`dskit.pipeline.stats.cluster_bootstrap_t` (its ``se`` carries the ``n / (n - 1)``
small-sample correction and is undefined below two clusters, reported as None). The PRIMARY error
(``*_se``) clusters events into TIME BLOCKS of ``cluster_block_s`` seconds of the settlement
instant: events that settle close together share one price path, so their errors are correlated,
and clustering on the event alone understates the error (about twofold in a simulation). The
per-event error (``*_se_event``) is reported beside it, never as the headline. A pile of
near-identical rows must not look like evidence. Each cell also gives the mean model, mean market
and base rate: a model whose mean sits away from the base rate is mis-calibrated in the large
before any edge is claimed. Nothing is a pass/fail gate: the report states numbers.

Rows that cannot be scored are counted by reason, never dropped quietly, on ``summary["census"]``.
Outputs land in the run directory: :data:`JSON_ARTIFACT` (every score) and :data:`MARKDOWN_ARTIFACT`.

Import cost: stdlib only.
"""

import copy

from dskit.pipeline.metrics import brier, logloss
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import cluster_bootstrap_t

__all__ = ["JSON_ARTIFACT", "MARKDOWN_ARTIFACT", "BucketedBinaryScore"]

#: The artifacts the node writes under its own artifact directory in the run directory.
JSON_ARTIFACT = "binary_score.json"
MARKDOWN_ARTIFACT = "binary_score.md"

_ALL = "all"
_MS_PER_S = 1000
_BOUND_KEYS = ("start_ms", "end_ms")

#: ``cluster_bootstrap_t`` is used only for its cluster-robust ``se`` (the toolkit has no public
#: cluster-robust mean without it), so one replicate is the minimum it accepts and all it needs.
_SE_ONLY_REPLICATES = 1
_SE_SEED = 0
_EVENT, _BLOCK = 0, 1


def _cluster_se(triples, which):
    """Return the toolkit's cluster-robust standard error of the mean of ``(event, block, value)`` triples.

    ``which`` is ``_EVENT`` to cluster on the event, ``_BLOCK`` on the time block; None below two clusters.
    """
    scores = {}
    for triple in triples:
        scores.setdefault(str(triple[which]), []).append(triple[2])
    if len(scores) < 2:
        return None
    return cluster_bootstrap_t(scores, _SE_ONLY_REPLICATES, _SE_SEED)["se"]


def _mean(values):
    """Return the mean of ``values``, or None when empty."""
    return sum(values) / len(values) if values else None


def _fmt(value):
    """Format a metric for the report, a dash when undefined."""
    return "-" if value is None else f"{value:.4f}"


class _Cell:
    """The running totals of one (model, segment, group, bucket) cell."""

    def __init__(self):
        self.brier, self.logloss, self.pnl = [], [], []
        self.brier_model, self.brier_market, self.ll_model, self.ll_market = [], [], [], []
        self.model, self.market, self.label = [], [], []

    def add(self, clusters, scores, pnl, level):
        """Record one scored row: ``clusters`` is (event, block); ``scores`` is (model brier, market brier, model logloss, market logloss)."""
        bm, bk, lm, lk = scores
        self.brier_model.append(bm)
        self.brier_market.append(bk)
        self.ll_model.append(lm)
        self.ll_market.append(lk)
        self.brier.append((*clusters, bm - bk))
        self.logloss.append((*clusters, lm - lk))
        self.model.append(level[0])
        self.market.append(level[1])
        self.label.append(level[2])
        if pnl is not None:
            self.pnl.append((*clusters, pnl))

    def summary(self):
        """Return the cell's metrics as a dict."""
        pnl = [triple[2] for triple in self.pnl]
        return {
            "n": len(self.brier), "n_clusters": len({str(t[_BLOCK]) for t in self.brier}),
            "n_event_clusters": len({str(t[_EVENT]) for t in self.brier}),
            "mean_model": _mean(self.model), "mean_market": _mean(self.market), "base_rate": _mean(self.label),
            "brier_model": _mean(self.brier_model), "brier_market": _mean(self.brier_market),
            "brier_diff": _mean([t[2] for t in self.brier]), "brier_diff_se": _cluster_se(self.brier, _BLOCK),
            "brier_diff_se_event": _cluster_se(self.brier, _EVENT),
            "logloss_model": _mean(self.ll_model), "logloss_market": _mean(self.ll_market),
            "logloss_diff": _mean([t[2] for t in self.logloss]),
            "logloss_diff_se": _cluster_se(self.logloss, _BLOCK),
            "logloss_diff_se_event": _cluster_se(self.logloss, _EVENT),
            "n_trades": len(pnl), "pnl_total": sum(pnl), "pnl_mean": _mean(pnl),
            "pnl_se": _cluster_se(self.pnl, _BLOCK), "pnl_se_event": _cluster_se(self.pnl, _EVENT),
            "hit_rate": _mean([1.0 if value > 0 else 0.0 for value in pnl]),
        }


class BucketedBinaryScore(Node):
    """Score models against the market's probability, by segment, group and market bucket (role ``report``).

    Input ``records``: rows holding, under the declared column names, a label, the market's
    probability, each model's probability, the quote and the per-contract fees, the settlement
    instant, the cluster field and each ``by`` field. Outputs ``scores`` (one dict per model x
    segment x group x bucket, with the metrics named in the module docstring) and ``summary`` (the
    census, and the parameters the scores were made under).

    Parameters
    ----------
    params : dict
        All REQUIRED except ``eligible_field``. ``model_fields`` (non-empty list of distinct
        column names) the models, none of them another declared column; ``market_field`` the
        market's probability; ``label_field`` the 0/1 outcome; ``settle_field`` the epoch-ms
        instant the label is settled; ``bid_field`` / ``ask_field`` the YES side's best bid and
        ask as of the information instant; ``fee_yes_field`` / ``fee_no_field`` the fee per
        contract of buying YES at the ask and NO at ``1 - bid``; ``eligible_field`` (optional)
        a boolean column, a row is scored only when it is exactly True (absent: every row);
        ``bucket_edges`` (ascending list of at least two numbers) the market buckets;
        ``margin`` (number >= 0) the take rule's margin over the fee; ``by`` (list of column
        names, may be empty) the extra groupings, each pooled alone; ``segments`` (non-empty
        dict) name -> ``{"start_ms"?, "end_ms"?}`` integer epoch milliseconds, a row belongs when
        ``start_ms <= settle < end_ms``, and no two may overlap; ``report_segments`` (non-empty
        list of distinct names of ``segments``) the segments computed and written;
        ``cluster_field`` the column rows are clustered on for the per-event error;
        ``cluster_block_s`` (int >= 1) the length in seconds of the time blocks of settlement
        the primary error clusters on.

    Examples
    --------
    Score two models, holding out everything settling at or after one instant::

        node = BucketedBinaryScore("score", {
            "model_fields": ["fair_a", "fair_b"], "market_field": "mid", "label_field": "label",
            "settle_field": "settle_ms", "bid_field": "yes_bid", "ask_field": "yes_ask",
            "fee_yes_field": "fee_yes", "fee_no_field": "fee_no", "eligible_field": "two_sided",
            "bucket_edges": [0, 0.25, 0.5, 0.75, 1], "margin": 0.02, "by": ["lead"],
            "segments": {"development": {"end_ms": 1789430400000},
                         "heldout": {"start_ms": 1789430400000}},
            "report_segments": ["development"], "cluster_field": "event",
            "cluster_block_s": 86400})
        out = node.run(ctx, {"records": rows})
        # -> out["scores"][0]["brier_diff"] < 0 means the model beat the market on that cell
    """

    role = "report"
    outputs = ("scores", "summary")
    _FIELD_PARAMS = ("market_field", "label_field", "settle_field", "bid_field", "ask_field",
                     "fee_yes_field", "fee_no_field", "cluster_field")
    _PARAMS = ("model_fields", *_FIELD_PARAMS, "eligible_field", "bucket_edges", "margin", "by", "segments",
               "report_segments", "cluster_block_s")

    @classmethod
    def _names_problems(cls, name, value, *, allow_empty):
        """Problems with a list of distinct non-empty names."""
        if (not isinstance(value, list) or (not value and not allow_empty)
                or any(not isinstance(v, str) or not v for v in value) or len(set(value)) != len(value)):
            return [f"{name} is required: a {'' if allow_empty else 'non-empty '}list of distinct names, got {value!r}"]
        return []

    @classmethod
    def _field_problems(cls, params):
        """Problems with the single-column params, and a model column that is also another column."""
        problems = []
        for name in cls._FIELD_PARAMS:
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} is required: a column name, got {params.get(name)!r}")
        eligible = params.get("eligible_field")
        if "eligible_field" in params and (not isinstance(eligible, str) or not eligible):
            problems.append(f"eligible_field must be a column name when given, got {eligible!r}")
        problems += cls._names_problems("model_fields", params.get("model_fields"), allow_empty=False)
        if not problems:
            others = {params[k] for k in cls._FIELD_PARAMS} | ({eligible} if eligible else set())
            clash = sorted(set(params["model_fields"]) & others)
            if clash:
                problems.append(f"model_fields {clash} are also declared as another column, so one column "
                                "would be both a model and its own market, label, quote, fee or cluster")
        return problems

    @classmethod
    def _bounds_problem(cls, name, bounds):
        """Return the problem with one segment's bounds, or None when it is a usable half-open interval."""
        if (not isinstance(bounds, dict) or set(bounds) - set(_BOUND_KEYS)
                or any(isinstance(v, bool) or not isinstance(v, int) for v in bounds.values())):
            return (f"segments[{name!r}] must be {{start_ms?, end_ms?}} integer epoch milliseconds, "
                    f"got {bounds!r}")
        if len(bounds) == 2 and bounds["start_ms"] >= bounds["end_ms"]:
            return f"segments[{name!r}] is empty: start_ms {bounds['start_ms']} is not before end_ms {bounds['end_ms']}"
        return None

    @classmethod
    def _overlap_problems(cls, segments):
        """Problems with any two segments that share an instant (a row may be in only one)."""
        names, problems = list(segments), []
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                start = max(segments[first].get("start_ms", float("-inf")), segments[second].get("start_ms", float("-inf")))
                end = min(segments[first].get("end_ms", float("inf")), segments[second].get("end_ms", float("inf")))
                if start < end:
                    problems.append(f"segments {first!r} and {second!r} overlap: a row may be in only one, or "
                                    "a development segment could contain held-out rows")
        return problems

    @classmethod
    def _segments_problems(cls, segments):
        """Problems with the ``segments`` map."""
        if not isinstance(segments, dict) or not segments:
            return [f"segments is required: a non-empty map name -> {{start_ms?, end_ms?}}, got {segments!r}"]
        problems = [p for p in (cls._bounds_problem(n, b) for n, b in segments.items()) if p]
        return problems or cls._overlap_problems(segments)

    @classmethod
    def _report_problems(cls, report, segments):
        """Problems with ``report_segments``: distinct names of declared segments, at least one."""
        declared = list(segments) if isinstance(segments, dict) else []
        names = cls._names_problems("report_segments", report, allow_empty=False)
        unknown = [] if names else [r for r in report if r not in declared]
        return names + ([f"report_segments names {unknown}, which are not among the declared segments {declared}"]
                        if unknown else [])

    @classmethod
    def _shape_problems(cls, params):
        """Problems with the bucket edges, the margin and the block length."""
        problems = []
        edges = params.get("bucket_edges")
        if (not isinstance(edges, list) or len(edges) < 2 or any(not number_ok(e) for e in edges)
                or any(a >= b for a, b in zip(edges, edges[1:]))):
            problems.append(f"bucket_edges is required: an ascending list of at least two numbers, got {edges!r}")
        if not (number_ok(params.get("margin")) and params["margin"] >= 0):
            problems.append(f"margin is required: a number >= 0, got {params.get('margin')!r}")
        if "cluster_block_s" not in params:
            problems.append("cluster_block_s is required: the length in seconds of the time blocks the error clusters on")
        else:
            check_int_param(problems, "cluster_block_s", params["cluster_block_s"], ge=1)
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
        problems += cls._field_problems(params)
        problems += cls._names_problems("by", params.get("by"), allow_empty=True)
        problems += cls._shape_problems(params)
        problems += cls._segments_problems(params.get("segments"))
        return problems + cls._report_problems(params.get("report_segments"), params.get("segments"))

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list (a one-shot iterable would arrive consumed).

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            A problem when ``records`` is not a list; empty otherwise.
        """
        if isinstance(inputs.get("records"), list):
            return []
        return [f"records must be a list of rows, got {type(inputs.get('records')).__name__}"]

    def _segment_of(self, settle):
        """Name the declared segment whose ``[start_ms, end_ms)`` holds the settlement instant, or None."""
        for name, bounds in self.params["segments"].items():
            start, end = bounds.get("start_ms"), bounds.get("end_ms")
            if (start is None or settle >= start) and (end is None or settle < end):
                return name
        return None

    def _bucket(self, market):
        """Label the market bucket (the last one closed on the right), or None outside every bucket."""
        edges = self.params["bucket_edges"]
        for lo, hi, last in zip(edges, edges[1:], [False] * (len(edges) - 2) + [True]):
            if lo <= market < hi or (last and market == hi):
                return f"[{lo:g},{hi:g}{']' if last else ')'}"
        return None

    def _groups(self, row):
        """Name the groups a row pools into: all, and one per ``by`` field."""
        return [_ALL] + [f"{name}={row.get(name)}" for name in self.params["by"]]

    def _eligible(self, row):
        """Say whether the row passes the optional eligibility column (every row does when none is declared)."""
        field = self.params.get("eligible_field")
        return field is None or row.get(field) is True

    def _gate(self, row):
        """Return ``(reason, segment)``: why a row cannot be scored (a census key), or None and its reported segment."""
        p = self.params
        settle = row.get(p["settle_field"])
        if not self._eligible(row):
            return "not_eligible", None
        if not number_ok(settle):
            return "no_settle", None
        if not number_ok(row.get(p["market_field"])):
            return "no_market", None
        segment = self._segment_of(settle)
        if segment is None:
            return "outside_segments", None
        if segment not in p["report_segments"]:
            return "unreported", None
        if row.get(p["cluster_field"]) is None:
            return "no_cluster", None
        return None, segment

    def _admit(self, row, census):
        """Return the reported segment a scorable row belongs to, counting why it is not scorable otherwise."""
        reason, segment = self._gate(row)
        if reason is not None:
            census[reason] += 1
        return segment

    def _label(self, index, row):
        """Return the row's label as 0.0 or 1.0, refusing anything else by row and field."""
        name, value = self.params["label_field"], row.get(self.params["label_field"])
        if isinstance(value, bool) or (number_ok(value) and value in (0, 1)):
            return float(value)
        raise ValueError(f"row {index}: label_field {name!r} must be 0 or 1 (a number or a bool), got {value!r}")

    @staticmethod
    def _losses(index, field, probability, label):
        """Return ``(brier, logloss)`` of one probability, refusing an unusable one by row and field."""
        try:
            return brier(probability, label), logloss(probability, label)
        except ValueError as exc:
            raise ValueError(f"row {index}: field {field!r}: {exc}") from exc

    def _take(self, row, model, market, label):
        """Return ``(profit, problem)`` of the take rule on one row: profit None when no trade, problem None unless unpriceable."""
        p = self.params
        fee_yes, fee_no = row.get(p["fee_yes_field"]), row.get(p["fee_no_field"])
        if not (number_ok(fee_yes) and number_ok(fee_no)):
            return None, "fee_missing"
        bid, ask = row.get(p["bid_field"]), row.get(p["ask_field"])
        if not (number_ok(bid) and number_ok(ask)):
            return None, "quote_missing"
        edge, margin = model - market, p["margin"]
        if edge > fee_yes + margin:
            return label - ask - fee_yes, None
        if -edge > fee_no + margin:
            return (1 - label) - (1.0 - bid) - fee_no, None
        return None, None

    def _observe(self, index, row, field, label, census):
        """Return ``(scores, profit, (model, market, label))`` for one row and model, or None when it has no model value."""
        counters, model, market = census["models"][field], row.get(field), row[self.params["market_field"]]
        if model is None:
            counters["no_forecast"] += 1
            return None
        counters["scored"] += 1
        profit, problem = self._take(row, model, market, label)
        if problem is not None:
            counters[problem] += 1
        model_losses = self._losses(index, field, model, label)
        market_losses = self._losses(index, self.params["market_field"], market, label)
        scores = (model_losses[0], market_losses[0], model_losses[1], market_losses[1])
        return scores, profit, (model, market, label)

    def _new_census(self, rows):
        """Return an empty census for ``rows`` input rows."""
        reasons = ("not_eligible", "no_settle", "no_market", "outside_segments", "unreported", "no_cluster",
                   "outside_buckets")
        return {"rows": rows, **dict.fromkeys(reasons, 0),
                "models": {m: {"no_forecast": 0, "scored": 0, "fee_missing": 0, "quote_missing": 0}
                           for m in self.params["model_fields"]}}

    def _score(self, records):
        """Fold the rows into cells keyed by (model, segment, group, bucket) and a census."""
        p, census, cells = self.params, self._new_census(len(records)), {}
        block_ms = p["cluster_block_s"] * _MS_PER_S
        for index, row in enumerate(records):
            segment = self._admit(row, census)
            if segment is None:
                continue
            label, bucket = self._label(index, row), self._bucket(row[p["market_field"]])
            census["outside_buckets"] += bucket is None
            clusters = (row[p["cluster_field"]], int(row[p["settle_field"]] // block_ms))
            for field in p["model_fields"]:
                observed = self._observe(index, row, field, label, census)
                if observed is None:
                    continue
                for group in self._groups(row):
                    for tag in (_ALL, bucket):
                        if tag is not None:
                            cells.setdefault((field, segment, group, tag), _Cell()).add(clusters, *observed)
        return cells, census

    def _pooled_table(self, scores):
        """Render the pooled per-segment table."""
        lines = ["| model | segment | n | Brier model | Brier market | diff (se block; event) "
                 "| logloss diff (se block; event) | trades | mean pnl (se block; event) |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for s in (c for c in scores if c["group"] == _ALL and c["bucket"] == _ALL):
            lines.append(f"| {s['model']} | {s['segment']} | {s['n']} | {_fmt(s['brier_model'])} | {_fmt(s['brier_market'])} "
                         f"| {_fmt(s['brier_diff'])} ({_fmt(s['brier_diff_se'])}; {_fmt(s['brier_diff_se_event'])}) "
                         f"| {_fmt(s['logloss_diff'])} ({_fmt(s['logloss_diff_se'])}; {_fmt(s['logloss_diff_se_event'])}) "
                         f"| {s['n_trades']} | {_fmt(s['pnl_mean'])} ({_fmt(s['pnl_se'])}; {_fmt(s['pnl_se_event'])}) |")
        return lines

    def _calibration_table(self, scores):
        """Render the calibration-in-the-large table, one row per model, segment and group."""
        lines = ["| model | segment | group | n | mean model | mean market | base rate | model - base | market - base |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for s in (c for c in scores if c["bucket"] == _ALL):
            lines.append(f"| {s['model']} | {s['segment']} | {s['group']} | {s['n']} | {_fmt(s['mean_model'])} "
                         f"| {_fmt(s['mean_market'])} | {_fmt(s['base_rate'])} "
                         f"| {s['mean_model'] - s['base_rate']:+.4f} | {s['mean_market'] - s['base_rate']:+.4f} |")
        return lines

    def _render(self, scores, census):
        """Render the scores as a short markdown report."""
        p = self.params
        lines = ["# Binary scoring: model against the market", "",
                 "Negative Brier or log-loss difference = the model was closer to the outcome than the market. "
                 f"The take rule buys at the touch when the edge over the market exceeds the fee plus {p['margin']:g}. "
                 f"Standard errors cluster on {p['cluster_block_s']} s blocks of settlement (blank below two clusters; "
                 "the per-event error follows it after the semicolon). "
                 f"Segments reported: {', '.join(p['report_segments'])}.", ""]
        lines += self._pooled_table(scores)
        lines += ["", "## Calibration in the large", "",
                  "Mean model probability minus the base rate (the share of YES), per group. A model far from "
                  "zero here is biased before any edge is claimed.", ""]
        lines += self._calibration_table(scores)
        lines += ["", f"Rows read {census['rows']}; not eligible {census['not_eligible']}; no settle instant "
                      f"{census['no_settle']}; no market value {census['no_market']}; outside every segment "
                      f"{census['outside_segments']}; in an unreported segment {census['unreported']}; "
                      f"no cluster {census['no_cluster']}; per model {census['models']}.", ""]
        return "\n".join(lines)

    def run(self, ctx, inputs):
        """Score every model, write the report, return the scores.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; its run directory receives :data:`JSON_ARTIFACT` and :data:`MARKDOWN_ARTIFACT`.
        inputs : dict
            ``records``: the rows to score.

        Returns
        -------
        dict
            ``{"scores": [...], "summary": {...}}``.

        Raises
        ------
        ValueError
            When a scorable row's label is not 0 or 1, or a probability is outside ``[0, 1]``; the
            message names the row and the field.
        """
        cells, census = self._score(inputs["records"])
        scores = [{"model": model, "segment": segment, "group": group, "bucket": bucket, **cell.summary()}
                  for (model, segment, group, bucket), cell in sorted(cells.items())]
        p = self.params
        summary = {"census": census, "margin": p["margin"], "bucket_edges": list(p["bucket_edges"]),
                   "cluster_block_s": p["cluster_block_s"], "segments": copy.deepcopy(p["segments"]),
                   "report_segments": list(p["report_segments"])}
        self.write_artifact(ctx, JSON_ARTIFACT, {"scores": scores, "summary": summary})
        self.write_artifact_text(ctx, MARKDOWN_ARTIFACT, self._render(scores, census))
        self.log.info("scored %d cell(s) from %d row(s)", len(scores), census["rows"])
        return {"scores": scores, "summary": summary}
