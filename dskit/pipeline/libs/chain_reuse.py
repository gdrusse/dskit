"""Detect suspected symbol reuse in a daily option-chain stream: one root that is two different companies (ADR-0265 Amendment 3).

A vendor files a chain under a ticker root. When a company is delisted and a different one later takes the same
ticker, the root is two series joined at a date, and every statistic over the whole root (a volatility, a
liquidity tercile, a first-quote date) mixes two businesses. :class:`ReuseDetector` reads the per-date tallies a
census already keeps and lists the dates where a root looks like it changed hands, with the evidence for each.
It decides nothing: a candidate is read by a person, and a confirmed break is declared in the stitch map
(:class:`~dskit.pipeline.libs.root_stitch.RootStitch` ``breaks``) so downstream can end the series there.

**Two signals**, either of which makes a candidate:

* ``price_jump`` - the reported underlying price moves by more than ``price_factor`` (up or down) between two
  consecutive priced dates of the root;
* ``rows_shift`` - the median rows per date over the ``window`` dates after a date differs from the median over the
  ``window`` before it by more than ``rows_factor`` (up or down), AND the strikes near the money are discontinuous
  there: between consecutive dates inside the shifted run, the near-spot strike intervals (strikes within ``band``
  of the price) overlap by at most ``overlap_max`` of the shorter interval. A chain that simply grows on the same
  strikes is therefore not flagged.

**A real split is not a reuse.** After a split, contracts are adjusted and strikes off the listed grid appear. When
a ``strike_grid`` is declared, a jump whose off-grid share of rows rises by at least ``split_share`` between the
window before and the window after is explained by a split, counted in ``split_explained``, and not listed.
Without a grid the split signature cannot be read: it is ``None`` in the evidence and the jump is listed. A clean
split with strikes that stay on the grid (a 2:1 on a round-dollar chain) is listed too: the evidence (price ratio,
unchanged rows, overlapping strikes) is what a reader judges it by.

The thresholds are conventions, not derived; every one is a parameter of the census.

Import cost: stdlib only.
"""

from datetime import date
from statistics import median

__all__ = ["ReuseDetector"]


def _ratio(after, before):
    """Return ``after / before``, or ``None`` when either is missing or ``before`` is zero."""
    return None if not before or after is None else after / before


def _factor(ratio):
    """Return how far a ratio is from 1, as a factor of at least 1 (``None`` stays ``None``)."""
    return None if not ratio or ratio <= 0 else max(ratio, 1 / ratio)


def _overlap(a, b):
    """Return the intersection of two ``(low, high)`` intervals over the shorter one; 1 or 0 for a point."""
    if a is None or b is None:
        return None
    low, high = max(a[0], b[0]), min(a[1], b[1])
    if high < low:
        return 0.0
    shorter = min(a[1] - a[0], b[1] - b[0])
    return 1.0 if shorter <= 0 else (high - low) / shorter


class ReuseDetector:
    """Scan a root's per-date tallies for suspected reuse.

    Parameters
    ----------
    price_factor : float
        Required, above 1: the price jump between consecutive priced dates that makes a candidate.
    rows_factor : float, optional
        Above 1: the shift of median rows per date that makes a candidate (with a strike discontinuity). Absent
        switches the rows signal off.
    window : int
        Dates on each side of a date for the rows medians and the off-grid shares.
    overlap_max : float
        The near-spot interval overlap, in ``[0, 1]``, at or below which strikes count as discontinuous.
    split_share : float
        The rise in the off-grid row share, in ``[0, 1]``, that signals a split.

    Examples
    --------
    One root whose price goes from 3 to 100 and whose chain grows sevenfold on new strikes::

        found, explained = ReuseDetector(2.0, rows_factor=4.0).scan("AI", days)
        found[0]["date"]        # the first date of the new regime
    """

    def __init__(self, price_factor, rows_factor=None, window=5, overlap_max=0.1, split_share=0.05):
        self.price_factor, self.rows_factor, self.window = price_factor, rows_factor, window
        self.overlap_max, self.split_share = overlap_max, split_share

    # -- the two signals -----------------------------------------------------------------------

    def _jumps(self, days):
        """Return the indices whose price differs from the previous priced date's by more than the factor."""
        return [i for i in range(1, len(days))
                if _factor(_ratio(days[i]["price"], days[i - 1]["price"])) >= self.price_factor]

    def _medians(self, days, i):
        """Return the median rows over the ``window`` dates before and from index ``i`` (``None`` if short)."""
        w = self.window
        before, after = days[max(0, i - w):i], days[i:i + w]
        full = len(before) == w and len(after) == w
        return (median(d["rows"] for d in before) if before else None,
                median(d["rows"] for d in after) if after else None, full)

    def _breaks(self, days, i):
        """Say whether the near-spot strikes are discontinuous between index ``i - 1`` and ``i``."""
        overlap = _overlap(days[i - 1]["near"], days[i]["near"])
        return overlap is not None and overlap <= self.overlap_max

    def _shifts(self, days):
        """Return the indices that start a run of shifted median rows with discontinuous strikes."""
        if self.rows_factor is None:
            return []
        flagged = []
        for i in range(1, len(days)):
            before, after, full = self._medians(days, i)
            if full and (_factor(_ratio(after, before)) or 0) >= self.rows_factor:
                flagged.append((i, _factor(_ratio(after, before))))
        runs, current = [], []
        for item in flagged:
            if current and item[0] - current[-1][0] > 1:
                runs.append(current)
                current = []
            current.append(item)
        runs.append(current) if current else None
        return [max(run, key=lambda x: x[1])[0] for run in runs
                if any(self._breaks(days, j) for j in range(run[0][0], run[-1][0] + 1))]

    # -- the evidence --------------------------------------------------------------------------

    def _share(self, days, lo, hi):
        """Return the off-grid share of rows over ``days[lo:hi]``, or ``None`` when no grid was read."""
        chosen = days[max(0, lo):hi]
        rows = sum(d["rows"] for d in chosen)
        if not chosen or not rows or any(d["odd"] is None for d in chosen):
            return None
        return sum(d["odd"] for d in chosen) / rows

    def _evidence(self, symbol, days, i, reasons):
        """Build one candidate's row of evidence at index ``i``."""
        w = self.window
        before_rows, after_rows, _ = self._medians(days, i)
        share_before, share_after = self._share(days, i - w, i), self._share(days, i, i + w)
        signature = None if share_before is None or share_after is None \
            else share_after - share_before >= self.split_share
        return {"symbol": symbol, "date": days[i]["date"], "previous_date": days[i - 1]["date"],
                "gap_days": (date.fromisoformat(days[i]["date"]) - date.fromisoformat(days[i - 1]["date"])).days,
                "reasons": sorted(reasons), "price_before": days[i - 1]["price"], "price_after": days[i]["price"],
                "price_ratio": _ratio(days[i]["price"], days[i - 1]["price"]),
                "rows_median_before": before_rows, "rows_median_after": after_rows,
                "rows_ratio": _ratio(after_rows, before_rows),
                "near_spot_overlap": _overlap(days[i - 1]["near"], days[i]["near"]),
                "split_signature": signature, "nonstandard_share_before": share_before,
                "nonstandard_share_after": share_after}

    def scan(self, symbol, days):
        """Return ``(candidates, split_explained)`` for one root.

        Parameters
        ----------
        symbol : str
            The root.
        days : list of dict
            One per priced date in date order: ``date`` (ISO text), ``rows``, ``price`` (the day's reported
            price), ``near`` (``(low, high)`` of the strikes near the price, or ``None``) and ``odd`` (rows at
            an off-grid strike, or ``None`` when no grid was declared).

        Returns
        -------
        tuple
            The candidate rows (see the module docstring) and the count of jumps a split explains.
        """
        hits = {i: {"price_jump"} for i in self._jumps(days)}
        for i in self._shifts(days):
            hits.setdefault(i, set()).add("rows_shift")
        found, explained, group = [], 0, []
        for i in sorted(hits):
            if group and i - group[-1] > self.window:
                found.append(group)
                group = []
            group.append(i)
        found.append(group) if group else None
        out = []
        for group in found:
            reasons = set().union(*(hits[i] for i in group))
            anchor = next((i for i in group if "price_jump" in hits[i]), group[0])
            row = self._evidence(symbol, days, anchor, reasons)
            if row["split_signature"]:
                explained += 1
            else:
                out.append(row)
        return out, explained
