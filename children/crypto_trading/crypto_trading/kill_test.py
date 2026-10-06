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

Rows are cut into declared ``segments`` by the instant their LABEL is settled, the market's close
(``development`` and ``heldout`` in the shipped document, one cut, no overlap): every decision row of
one market lands in one segment, even when its decision instants straddle the cut. Choices such as the
volatility window, the leads and the margin belong to the development rows. ``report_segments`` names the
segments whose numbers are computed and written; the shipped document lists ``development`` only, so a run
never prints the held-out numbers by accident, and reading them is a deliberate edit of the document
(a new identity hash, so every held-out read is a recorded run). Within a segment each row also lands in a
mid ``bucket`` (declared edges; the last bucket is closed on the right), a ``group`` per ``by`` field
(e.g. lead time) and pooled ``all``. Each cell reports the mean of the per-row differences with a
CLUSTER-ROBUST standard error, dskit's own, from :func:`dskit.pipeline.stats.cluster_bootstrap_t` (its
``se`` has the ``n / (n - 1)`` small-sample correction and is undefined below two clusters, reported as
None). The PRIMARY error (``*_se``) clusters markets into TIME BLOCKS of ``cluster_block_s`` seconds of
close (``floor(close_ms / block)``): volatility regimes persist, so neighbouring markets' errors are
correlated, and clustering on the market alone understates the error about twofold (simulated coverage
68 to 77 percent). The per-event error (``*_se_event``, rows sharing ``cluster_field``) is reported beside
it for comparison. A pile of near-identical rows must not look like evidence. Each cell also gives the mean fair value, mean mid and base rate: a fair value
whose mean sits away from the base rate is mis-calibrated in the large before any edge is claimed.
Nothing is a pass/fail gate: the report states numbers.

INTERIM HOME (PROPOSED ADR-0243): bucketed binary scoring with a cluster-robust error is generic; dskit
has per-observation ``brier``/``logloss`` and ``cluster_bootstrap_t`` but no public cluster-robust mean
(used here with one replicate, for its ``se``) and no bucketed scorer. A held-out gate for research reads
does not exist either (``dskit.production``'s consume-once gate is for deployment).

Rows that cannot be scored are counted, never dropped quietly, on ``summary["census"]``.
Outputs land in the run directory: ``kill_test.json`` (every score) and ``kill_test.md``.

Import cost: stdlib + dskit.
"""

from dskit.pipeline.metrics import brier, logloss
from dskit.pipeline.node import check_int_param, reject_unknown_params
from dskit.pipeline.stats import cluster_bootstrap_t
from dskit.pipeline.records import number_ok

from . import fields as f
from .clock import instant_ms
from .ports import ListPortsNode

__all__ = ["KillTestScore"]

_ALL = "all"
_MS_PER_S = 1000

#: ``cluster_bootstrap_t`` is used only for its cluster-robust ``se`` (dskit has no public cluster-robust
#: mean without it), so one replicate is the minimum it accepts and all it needs.
_SE_ONLY_REPLICATES = 1
_SE_SEED = 0


def _cluster_se(triples, which):
    """Return dskit's cluster-robust standard error of the mean of ``(event, block, value)`` triples.

    ``which`` is 0 to cluster on the event, 1 on the time block; None below two clusters.
    """
    scores = {}
    for triple in triples:
        scores.setdefault(str(triple[which]), []).append(triple[2])
    if len(scores) < 2:
        return None
    return cluster_bootstrap_t(scores, _SE_ONLY_REPLICATES, _SE_SEED)["se"]


_EVENT, _BLOCK = 0, 1


def _mean(values):
    """Return the mean of ``values``, or None when empty."""
    return sum(values) / len(values) if values else None


class _Cell:
    """The running totals of one (model, segment, group, bucket) cell."""

    def __init__(self):
        self.brier, self.logloss, self.pnl = [], [], []
        self.brier_fair, self.brier_mid, self.ll_fair, self.ll_mid = [], [], [], []
        self.fair, self.mid, self.label = [], [], []

    def add(self, clusters, scores, pnl, level):
        """Record one scored row: ``clusters`` is (event, block); ``scores`` is (fair brier, mid brier, fair logloss, mid logloss)."""
        bf, bm, lf, lm = scores
        self.brier_fair.append(bf)
        self.brier_mid.append(bm)
        self.ll_fair.append(lf)
        self.ll_mid.append(lm)
        self.brier.append((*clusters, bf - bm))
        self.logloss.append((*clusters, lf - lm))
        self.fair.append(level[0])
        self.mid.append(level[1])
        self.label.append(level[2])
        if pnl is not None:
            self.pnl.append((*clusters, pnl))

    def summary(self):
        """Return the cell's metrics as a dict."""
        pnl = [triple[2] for triple in self.pnl]
        return {
            "n": len(self.brier), "n_clusters": len({str(t[_BLOCK]) for t in self.brier}),
            "n_event_clusters": len({str(t[_EVENT]) for t in self.brier}),
            "mean_fair": _mean(self.fair), "mean_mid": _mean(self.mid), "base_rate": _mean(self.label),
            "brier_fair": _mean(self.brier_fair), "brier_mid": _mean(self.brier_mid),
            "brier_diff": _mean([t[2] for t in self.brier]), "brier_diff_se": _cluster_se(self.brier, _BLOCK),
            "brier_diff_se_event": _cluster_se(self.brier, _EVENT),
            "logloss_fair": _mean(self.ll_fair), "logloss_mid": _mean(self.ll_mid),
            "logloss_diff": _mean([t[2] for t in self.logloss]), "logloss_diff_se": _cluster_se(self.logloss, _BLOCK),
            "logloss_diff_se_event": _cluster_se(self.logloss, _EVENT),
            "n_trades": len(pnl), "pnl_total": sum(pnl), "pnl_mean": _mean(pnl),
            "pnl_se": _cluster_se(self.pnl, _BLOCK), "pnl_se_event": _cluster_se(self.pnl, _EVENT),
            "hit_rate": _mean([1.0 if value > 0 else 0.0 for value in pnl]),
        }


class KillTestScore(ListPortsNode):
    """Score fair values against the mid, by segment, group and mid bucket (role ``report``).

    Input ``records``: rows with ``label``, ``mid``, ``two_sided``, ``yes_bid``, ``yes_ask``,
    ``fee_buy_yes``, ``fee_buy_no``, ``close_ms``, each fair-value column, each ``by`` field
    and the cluster field. Outputs ``scores`` (one dict per model x segment x group x bucket) and
    ``summary`` (the census, and the parameters the scores were made under).

    Parameters
    ----------
    params : dict
        All REQUIRED. ``fair_fields`` (non-empty list of distinct column names) the models;
        ``mid_edges`` (ascending list of >= 2 numbers) the bucket edges; ``margin`` (number >= 0)
        the take rule's margin over the fee; ``segments`` (non-empty dict) name -> ``{"start"?,
        "end"?}`` ISO instants WITH a zone, a row belongs when ``start <= close < end``; ``by`` (list of
        field names, may be empty) the extra groupings, each pooled alone; ``cluster_field`` (str)
        the field rows are clustered on for the per-event error; ``cluster_block_s`` (int >= 1) the length in
        seconds of the time blocks of close that the primary error clusters on; ``report_segments`` (non-empty list of distinct names of
        ``segments``) the segments computed and written, development only until a held-out read is
        deliberately declared.

    Examples
    --------
    Score two models, held out from 15 September::

        node = KillTestScore("kill", {
            "fair_fields": ["fair_rms", "fair_bvol"], "mid_edges": [0, 0.25, 0.5, 0.75, 1],
            "margin": 0.02, "by": ["lead_minutes"], "cluster_field": "event_ticker",
            "cluster_block_s": 86400, "segments": {"development": {"end": "2026-09-15T00:00:00Z"},
                         "heldout": {"start": "2026-09-15T00:00:00Z"}},
            "report_segments": ["development"]})
        out = node.run(ctx, {"records": rows})
        # -> out["scores"][0]["brier_diff"] < 0 means the model beat the mid on that cell
    """

    role = "report"
    outputs = ("scores", "summary")
    _PARAMS = ("fair_fields", "mid_edges", "margin", "segments", "by", "cluster_field", "cluster_block_s",
               "report_segments")

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
    def _report_problems(cls, report, segments):
        """Problems with ``report_segments``: distinct names of declared segments, at least one."""
        declared = list(segments) if isinstance(segments, dict) else []
        names = cls._names_problems("report_segments", report, allow_empty=False)
        unknown = [] if names else [r for r in report if r not in declared]
        return names + ([f"report_segments names {unknown}, which are not among the declared segments {declared}"]
                        if unknown else [])

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
        if "cluster_block_s" not in params:
            problems.append("cluster_block_s is required: the length in seconds of the time blocks the error clusters on")
        else:
            check_int_param(problems, "cluster_block_s", params["cluster_block_s"], ge=1)
        problems += cls._report_problems(params.get("report_segments"), params.get("segments"))
        if not isinstance(params.get("cluster_field"), str) or not params.get("cluster_field"):
            problems.append(f"cluster_field is required: a field name, got {params.get('cluster_field')!r}")
        return problems

    def _segments_of(self, close_ms):
        """Name the declared segments whose ``[start, end)`` holds the instant the label was settled."""
        names = []
        for name, bounds in self.params["segments"].items():
            start, end = instant_ms(bounds.get("start")), instant_ms(bounds.get("end"))
            if (start is None or close_ms >= start) and (end is None or close_ms < end):
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
        """Return ``(scores, pnl, (fair, mid, label))`` for one row and model, or None when it has no fair value."""
        fair, mid, label = row.get(model), row[f.MID], float(row[f.LABEL])
        if fair is None:
            census["models"][model]["no_fair"] += 1
            return None
        census["models"][model]["scored"] += 1
        pnl = self._pnl(row, fair)
        if pnl is False:
            census["models"][model]["fee_missing"] += 1
            pnl = None
        return (brier(fair, label), brier(mid, label), logloss(fair, label), logloss(mid, label)), pnl, (
            fair, mid, label)

    def _score(self, records):
        """Fold the rows into cells keyed by (model, segment, group, bucket) and a census."""
        census = {"rows": len(records), "not_two_sided": 0, "outside_segments": 0, "unreported": 0,
                  "no_cluster": 0, "outside_buckets": 0,
                  "models": {m: {"no_fair": 0, "scored": 0, "fee_missing": 0} for m in self.params["fair_fields"]}}
        cells = {}
        for row in records:
            segments = self._row_segments(row, census)
            if not segments:
                continue
            bucket = self._bucket(row[f.MID])
            census["outside_buckets"] += bucket is None
            clusters = (row[self.params["cluster_field"]], row[f.CLOSE_MS] // (self.params["cluster_block_s"] * _MS_PER_S))
            for model in self.params["fair_fields"]:
                observed = self._observe(row, model, census)
                if observed is None:
                    continue
                for segment in segments:
                    for group in self._groups(row):
                        for label in (_ALL, bucket):
                            if label is not None:
                                cells.setdefault((model, segment, group, label), _Cell()).add(clusters, *observed)
        return cells, census

    def _row_segments(self, row, census):
        """Return the REPORTED segments a scorable row belongs to, counting why it is not scorable otherwise."""
        if row.get(f.TWO_SIDED) is not True:
            census["not_two_sided"] += 1
            return []
        declared = self._segments_of(row[f.CLOSE_MS])
        if not declared:
            census["outside_segments"] += 1
            return []
        reported = [name for name in declared if name in self.params["report_segments"]]
        if not reported:
            census["unreported"] += 1
        elif row.get(self.params["cluster_field"]) is None:
            census["no_cluster"] += 1
            return []
        return reported

    def _render(self, scores, census):
        """Render the scores as a short markdown report."""
        shown = ", ".join(self.params["report_segments"])
        lines = ["# Kill test: fair value against the market mid, after fees", "",
                 "Negative Brier or log-loss difference = the fair value was closer to the outcome than the mid. "
                 f"The take rule buys at the touch when the edge exceeds the fee plus {self.params['margin']:g}. "
                 f"Standard errors cluster on {self.params['cluster_block_s']} s blocks of close (blank below two clusters; the "
                 f"per-event error follows it after the semicolon). Segments reported: {shown}. "
                 "Read the held-out rows once.", ""]
        pooled = [s for s in scores if s["group"] == _ALL and s["bucket"] == _ALL]
        lines += ["| model | segment | n | Brier fair | Brier mid | diff (se block; event) | logloss diff (se block; event) | trades | mean pnl (se block; event) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for s in pooled:
            lines.append(f"| {s['model']} | {s['segment']} | {s['n']} | {_fmt(s['brier_fair'])} | {_fmt(s['brier_mid'])} "
                         f"| {_fmt(s['brier_diff'])} ({_fmt(s['brier_diff_se'])}; {_fmt(s['brier_diff_se_event'])}) "
                         f"| {_fmt(s['logloss_diff'])} ({_fmt(s['logloss_diff_se'])}; {_fmt(s['logloss_diff_se_event'])}) "
                         f"| {s['n_trades']} | {_fmt(s['pnl_mean'])} ({_fmt(s['pnl_se'])}; {_fmt(s['pnl_se_event'])}) |")
        lines += ["", "## Calibration in the large", "",
                  "Mean fair value minus the base rate (the share of YES), per lead. A model far from zero here is "
                  "biased before any edge is claimed.", "",
                  "| model | segment | group | n | mean fair | mean mid | base rate | fair - base | mid - base |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for s in (c for c in scores if c["bucket"] == _ALL):
            lines.append(f"| {s['model']} | {s['segment']} | {s['group']} | {s['n']} | {_fmt(s['mean_fair'])} "
                         f"| {_fmt(s['mean_mid'])} | {_fmt(s['base_rate'])} "
                         f"| {s['mean_fair'] - s['base_rate']:+.4f} | {s['mean_mid'] - s['base_rate']:+.4f} |")
        lines += ["", f"Rows read {census['rows']}; not two-sided {census['not_two_sided']}; "
                      f"outside every segment {census['outside_segments']}; in an unreported segment "
                      f"{census['unreported']}; no cluster {census['no_cluster']}; per model {census['models']}.", ""]
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
                   "segments": self.params["segments"], "report_segments": self.params["report_segments"]}
        self.write_artifact(ctx, "kill_test.json", {"scores": scores, "summary": summary})
        self.write_artifact_text(ctx, "kill_test.md", self._render(scores, census))
        self.log.info("scored %d cell(s) from %d row(s)", len(scores), census["rows"])
        return {"scores": scores, "summary": summary}


def _fmt(value):
    """Format a metric for the report, a dash when undefined."""
    return "-" if value is None else f"{value:.4f}"
