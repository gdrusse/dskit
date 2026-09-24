"""Inference diagnostics: what each forecast turned into (ADR-0183 phase 2).

A decision's ``candidates`` carry the forecast (``score``) for every
instrument it weighed; an ``outcome`` event, logged strictly after that
decision, carries what the forecast's target turned out to be
(``realized``). :class:`ForecastDiagnostics` pairs the two by
(``decision_id``, instrument) and asks three questions of the pairs:

* **Calibration by decile** — equal-count score buckets
  (:func:`dskit.pipeline.stats.quantile_edges`, the one owner of that
  rule) with the mean score beside the mean realised value, and the
  Mincer-Zarnowitz slope (:func:`dskit.pipeline.ordering.calibration_slope`).
* **Hit rate by bucket** — how often the realised value's sign agreed with
  the score's, per bucket.
* **Rank IC over time** — the cross-sectional Spearman at each decision
  instant (:func:`dskit.pipeline.ordering.cross_section_by_stamp`), its
  per-day mean, and the HAC-tested pooled summary
  (:func:`dskit.pipeline.ordering.per_timestamp_ic`).

Nothing here is re-derived: the estimators belong to ``dskit.pipeline``.
The outcome is read ONLY here and by the section that renders this class;
decision rows never see it, so the decision log carries no look-ahead.

Import cost: stdlib plus ``dskit.pipeline``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from dskit.pipeline import ordering
from dskit.pipeline.stats import quantile_bin, quantile_edges

__all__ = ["DEFAULT_BUCKETS", "Bucket", "ForecastDiagnostics", "ForecastPair"]

#: How many equal-count score buckets the calibration table cuts (deciles).
DEFAULT_BUCKETS = 10


@dataclass(frozen=True)
class ForecastPair:
    """One candidate's forecast joined to its realised outcome.

    Parameters
    ----------
    ts_ms : int
        The decision instant.
    instrument : str
    score : float
        The forecast the decision saw.
    realized : float
        What the forecast's target turned out to be.
    chosen : bool
        Whether the decision chose this instrument.

    Examples
    --------
    ::

        pair = ForecastPair(60_000, "AAA", 0.001, -0.0004, True)
        pair.hit  # False
    """

    ts_ms: int
    instrument: str
    score: float
    realized: float
    chosen: bool

    @property
    def hit(self):
        """Whether the realised sign agreed with the forecast's (zero never agrees)."""
        return self.score * self.realized > 0


@dataclass(frozen=True)
class Bucket:
    """One equal-count score bucket of the calibration table.

    Parameters
    ----------
    index : int
        ``0`` is the lowest scores.
    n : int
    score_lo, score_hi : float
        The smallest and largest score in the bucket.
    mean_score, mean_realized : float
    hit_rate : float
        Share of pairs whose realised sign agreed with the score's.

    Examples
    --------
    ::

        bucket = Bucket(0, 2, -0.002, -0.001, -0.0015, -0.0003, 0.5)
        bucket.gap  # -0.0012
    """

    index: int
    n: int
    score_lo: float
    score_hi: float
    mean_score: float
    mean_realized: float
    hit_rate: float

    @property
    def gap(self):
        """Mean score minus mean realised: positive when the forecast ran high."""
        return self.mean_score - self.mean_realized


class ForecastDiagnostics:
    """Calibration, hit rate and rank IC over a log's forecast/outcome pairs.

    Parameters
    ----------
    log : EventLog
        Decisions with ``candidates`` and their ``outcome`` events.
    buckets : int
        Equal-count score buckets for the calibration table.

    Examples
    --------
    ::

        diagnostics = ForecastDiagnostics(log)
        diagnostics.pairs          # tuple of ForecastPair
        diagnostics.calibration()  # tuple of Bucket, lowest scores first
        diagnostics.rank_ic()      # {"stamps": [...], "rho": [...], ...}
    """

    def __init__(self, log, buckets=DEFAULT_BUCKETS):
        self._log = log
        self._buckets = buckets
        self.pairs, self.unresolved = self._pair(log)

    @staticmethod
    def _pair(log):
        """Join every scored candidate to its outcome; count the ones without."""
        outcomes_of = log.links.outcomes_of
        pairs, unresolved = [], 0
        for decision in log.of_kind("decision"):
            realized = {o.instrument: o.get("realized")
                        for o in outcomes_of.get(decision.get("decision_id"), ())}
            chosen = decision.get("chosen")
            for candidate in decision.get("candidates") or ():
                score = candidate.get("score")
                if score is None:
                    continue
                instrument = candidate.get("instrument")
                if instrument not in realized:
                    unresolved += 1
                    continue
                pairs.append(ForecastPair(decision.ts_ms, instrument, float(score),
                                          float(realized[instrument]), instrument == chosen))
        return tuple(pairs), unresolved

    def calibration(self, chosen_only=False):
        """Return the equal-count score buckets, lowest scores first.

        Parameters
        ----------
        chosen_only : bool
            Restrict to the instruments the decisions chose.

        Returns
        -------
        tuple of Bucket
            Empty below two pairs.
        """
        pairs = [p for p in self.pairs if p.chosen or not chosen_only]
        if len(pairs) < 2:
            return ()
        edges = quantile_edges([p.score for p in pairs], self._buckets)
        grouped = defaultdict(list)
        for pair in pairs:
            grouped[quantile_bin(pair.score, edges)].append(pair)
        return tuple(self._bucket(index, grouped[index]) for index in sorted(grouped))

    @staticmethod
    def _bucket(index, pairs):
        """Summarise one bucket's pairs."""
        n = len(pairs)
        scores = [p.score for p in pairs]
        return Bucket(index, n, min(scores), max(scores), sum(scores) / n,
                      sum(p.realized for p in pairs) / n, sum(p.hit for p in pairs) / n)

    def hit_rate(self, chosen_only=False):
        """Return the overall sign-agreement rate, or None without pairs.

        Parameters
        ----------
        chosen_only : bool

        Returns
        -------
        float or None
        """
        pairs = [p for p in self.pairs if p.chosen or not chosen_only]
        return sum(p.hit for p in pairs) / len(pairs) if pairs else None

    def slope(self):
        """Return the Mincer-Zarnowitz slope of realised on score, or None.

        Returns
        -------
        dict or None
            :func:`dskit.pipeline.ordering.calibration_slope`'s result in
            decision-time order; None below three pairs or for a constant
            forecast (a slope against a constant is undefined).
        """
        ordered = sorted(self.pairs, key=lambda p: (p.ts_ms, p.instrument))
        try:
            return ordering.calibration_slope([p.realized for p in ordered],
                                              [p.score for p in ordered])
        except ValueError:
            return None

    def _columns(self):
        """Return the pairs as the four aligned columns ``ordering`` takes."""
        return ([p.ts_ms for p in self.pairs], [p.instrument for p in self.pairs],
                [p.realized for p in self.pairs], [p.score for p in self.pairs])

    def rank_ic(self):
        """Return the cross-sectional Spearman per decision instant.

        Returns
        -------
        dict
            :func:`dskit.pipeline.ordering.cross_section_by_stamp`'s result.
        """
        return ordering.cross_section_by_stamp(*self._columns())

    def rank_ic_by_day(self, local):
        """Return the mean rank IC per local day.

        Parameters
        ----------
        local : LocalTime
            The run's display zone (the day boundary).

        Returns
        -------
        list of tuple
            ``(day, mean_rho, n_stamps)`` in day order.
        """
        cs = self.rank_ic()
        days = defaultdict(list)
        for stamp, rho in zip(cs["stamps"], cs["rho"]):
            days[local.day(stamp)].append(rho)
        return [(day, sum(r) / len(r), len(r)) for day, r in sorted(days.items())]

    def rank_ic_summary(self):
        """Return the HAC-tested pooled rank IC, or None without pairs.

        Returns
        -------
        dict or None
            :func:`dskit.pipeline.ordering.per_timestamp_ic`'s result
            (``ic``, ``ic_t``, ``usable`` and why not, ...).
        """
        if not self.pairs:
            return None
        return ordering.per_timestamp_ic(*self._columns())
