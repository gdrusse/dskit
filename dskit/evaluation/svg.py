"""Stdlib inline-SVG charts for the report: axes, grid, legend, tooltips, no JS.

A chart is an object that renders one ``<svg>`` string (ADR-0183 item 6).
:class:`XYChart` owns the frame — scales, tick labels, gridlines, the
legend, session boundaries — and its two subclasses differ only in how a
series becomes a path (:class:`LineChart` joins points, :class:`StepChart`
holds each value until the next). :class:`MarkerLayer` overlays event
markers whose ``<title>`` is the browser's tooltip, so the "why" of each
trade is one hover away without a line of script. :class:`BarChart` and
:class:`Histogram` cover distributions.

Nothing here names a colour. Every element carries a class, and
:data:`CHART_CSS` maps the classes to CSS variables with a light palette
and a ``prefers-color-scheme: dark`` one; the report inlines it in its
``<style>``. The output never references a URL, so the report stays one
self-contained file.

Long series are thinned deterministically by :func:`downsample` — per
pixel-wide bucket it keeps the first, lowest, highest and last point, so
spikes survive and a week of one-minute marks costs a few thousand
points, not tens of thousands. The time axis formats ticks in the run's
zone through :class:`~dskit.evaluation.events.LocalTime`.

Market-closed time is not drawn. By default a timed chart uses a
:class:`SessionScale`: gaps between observations longer than a threshold
(declared, or :func:`auto_gap_ms`) collapse to a thin break line with the
next session's date under it, series paths lift the pen across every
break, and ticks read as session dates plus intraday clock times -- so an
overnight or a weekend never shows as a straight line through nothing.

Import cost: stdlib only.
"""

from __future__ import annotations

import bisect
import html
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = [
    "CHART_CSS",
    "BarChart",
    "Chart",
    "Histogram",
    "LineChart",
    "LinearScale",
    "Marker",
    "MarkerLayer",
    "Series",
    "SessionScale",
    "StepChart",
    "TimeScale",
    "auto_gap_ms",
    "XYChart",
    "downsample",
]

#: The chart palette: classes -> CSS variables, light by default, dark
#: under ``prefers-color-scheme``. Series classes are ``s0`` .. ``s3``.
CHART_CSS = """
:root {
  --ev-fg: #1f2328; --ev-muted: #59636e; --ev-grid: #e1e6eb; --ev-bg: #ffffff;
  --ev-s0: #0969da; --ev-s1: #bc4c00; --ev-s2: #8250df; --ev-s3: #1a7f37;
  --ev-win: #1a7f37; --ev-loss: #cf222e; --ev-open: #6e7781; --ev-rej: #9a6700;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ev-fg: #e6edf3; --ev-muted: #9198a1; --ev-grid: #30363d; --ev-bg: #0d1117;
    --ev-s0: #4493f8; --ev-s1: #f0883e; --ev-s2: #ab7df8; --ev-s3: #3fb950;
    --ev-win: #3fb950; --ev-loss: #f85149; --ev-open: #9198a1; --ev-rej: #d29922;
  }
}
svg.chart { width: 100%; height: auto; max-width: 960px; display: block;
  font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; }
svg.chart text { fill: var(--ev-fg); }
svg.chart text.muted { fill: var(--ev-muted); }
svg.chart text.title { font-weight: 600; font-size: 12px; }
svg.chart .axis { stroke: var(--ev-muted); stroke-width: 1; }
svg.chart .grid { stroke: var(--ev-grid); stroke-width: 1; }
svg.chart .zero { stroke: var(--ev-muted); stroke-width: 1; }
svg.chart .boundary { stroke: var(--ev-muted); stroke-width: 1; stroke-dasharray: 3 3; }
svg.chart .break { stroke: var(--ev-muted); stroke-width: 1.5; }
svg.chart text.session { fill: var(--ev-fg); font-weight: 600; }
svg.chart text.mklabel { font-size: 10px; fill: var(--ev-muted); }
svg.chart .line { fill: none; stroke-width: 1.5; }
svg.chart .s0 { stroke: var(--ev-s0); } svg.chart .s1 { stroke: var(--ev-s1); }
svg.chart .s2 { stroke: var(--ev-s2); } svg.chart .s3 { stroke: var(--ev-s3); }
svg.chart rect.swatch.s0 { fill: var(--ev-s0); } svg.chart rect.swatch.s1 { fill: var(--ev-s1); }
svg.chart rect.swatch.s2 { fill: var(--ev-s2); } svg.chart rect.swatch.s3 { fill: var(--ev-s3); }
svg.chart .mk { stroke-width: 1.3; }
svg.chart .mk.win { fill: var(--ev-win); stroke: var(--ev-win); }
svg.chart .mk.loss { fill: var(--ev-loss); stroke: var(--ev-loss); }
svg.chart .mk.open { fill: var(--ev-open); stroke: var(--ev-open); }
svg.chart .mk.rej { fill: none; stroke: var(--ev-rej); }
svg.chart .mk.hollow { fill: var(--ev-bg); }
svg.chart .bar { fill: var(--ev-s0); }
svg.chart .bar.neg { fill: var(--ev-loss); }
"""

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_MINUTE_MS = 60_000

#: Candidate time-axis steps, smallest first.
_TIME_STEPS = (
    _MINUTE_MS, 2 * _MINUTE_MS, 5 * _MINUTE_MS, 10 * _MINUTE_MS, 15 * _MINUTE_MS,
    30 * _MINUTE_MS, _HOUR_MS, 2 * _HOUR_MS, 3 * _HOUR_MS, 6 * _HOUR_MS, 12 * _HOUR_MS,
    _DAY_MS, 2 * _DAY_MS, 7 * _DAY_MS, 14 * _DAY_MS, 30 * _DAY_MS,
)

#: The default chart canvas, in SVG user units.
DEFAULT_WIDTH, DEFAULT_HEIGHT = 900, 260

#: Plot-area margins: top, right, bottom, left.
_MARGIN = (30, 18, 46, 70)

#: The auto gap threshold's floor, and its multiple of the median step.
_GAP_FLOOR_MS = 30 * _MINUTE_MS
_GAP_STEPS = 20

#: The fewest pixels between two intraday tick labels.
_TICK_PX = 56


def _esc(text):
    """Escape text for an SVG/XML attribute or node."""
    return html.escape(str(text), quote=True)


def _number_label(value):
    """Format a compact tick label: k/M suffixes, four significant figures."""
    magnitude = abs(value)
    if magnitude >= 1e6:
        return f"{value / 1e6:.3g}M"
    if magnitude >= 1e4:
        return f"{value / 1e3:.3g}k"
    if magnitude and magnitude < 1e-3:
        return f"{value:.2e}"
    return f"{value:.4g}"


def auto_gap_ms(xs):
    """Return the gap above which a time axis breaks: 20 median steps, at least 30 minutes.

    Parameters
    ----------
    xs : sequence of int
        Epoch ms, sorted.

    Returns
    -------
    int

    Examples
    --------
    ::

        auto_gap_ms([0, 60_000, 120_000])  # 1800000 (the 30-minute floor)
    """
    steps = sorted(b - a for a, b in zip(xs, xs[1:]) if b > a)
    median = steps[len(steps) // 2] if steps else 0
    return max(_GAP_FLOOR_MS, _GAP_STEPS * median)


def downsample(points, buckets):
    """Thin an x-sorted series to at most four points per x bucket.

    Deterministic min/max decimation: each of ``buckets`` equal-width x
    ranges keeps its first, lowest, highest and last point, in x order,
    so extremes and step edges survive.

    Parameters
    ----------
    points : sequence of (float, float)
        Sorted by x.
    buckets : int
        Bucket count, usually the plot width in pixels.

    Returns
    -------
    list of (float, float)

    Examples
    --------
    ::

        len(downsample([(i, i % 7) for i in range(10000)], 100)) <= 400  # True
    """
    points = list(points)
    if buckets < 1 or len(points) <= 4 * buckets:
        return points
    lo, hi = points[0][0], points[-1][0]
    width = (hi - lo) / buckets or 1.0
    out, group, current = [], [], None
    for point in points:
        bucket = min(buckets - 1, int((point[0] - lo) / width))
        if bucket != current and group:
            out.extend(_bucket_keep(group))
            group = []
        current = bucket
        group.append(point)
    out.extend(_bucket_keep(group))
    return out


def _bucket_keep(group):
    """First, min-y, max-y and last of a bucket, deduplicated, in x order."""
    keep = {0, len(group) - 1}
    keep.add(min(range(len(group)), key=lambda i: group[i][1]))
    keep.add(max(range(len(group)), key=lambda i: group[i][1]))
    return [group[i] for i in sorted(keep)]


# ---------------------------------------------------------------------------
# Scales
# ---------------------------------------------------------------------------


class LinearScale:
    """Map a numeric domain onto a pixel range, with nice ticks.

    Parameters
    ----------
    lo, hi : float
        The domain; a degenerate one is widened so a flat series still plots.
    start, stop : float
        The pixel range (``stop < start`` for a y axis).

    Examples
    --------
    ::

        scale = LinearScale(0.0, 10.0, 0.0, 100.0)
        scale(5.0)  # 50.0
    """

    def __init__(self, lo, hi, start, stop):
        if hi <= lo:
            pad = abs(lo) * 0.05 or 1.0
            lo, hi = lo - pad, hi + pad
        self.lo, self.hi, self.start, self.stop = lo, hi, start, stop

    def __call__(self, value):
        """Return the pixel position of ``value``."""
        return self.start + (value - self.lo) / (self.hi - self.lo) * (self.stop - self.start)

    def ticks(self, count=5):
        """Return round tick values inside the domain.

        Parameters
        ----------
        count : int
            Roughly how many.

        Returns
        -------
        list of float
        """
        raw = (self.hi - self.lo) / max(count, 1)
        magnitude = 10 ** math.floor(math.log10(raw))
        step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
        first = math.ceil(self.lo / step) * step
        ticks, value = [], first
        while value <= self.hi + step * 1e-9:
            ticks.append(round(value, 12))
            value += step
        return ticks

    def label(self, value):
        """Return the tick label of ``value``."""
        return _number_label(value)

    def segment_of(self, value):
        """Return the index of the contiguous segment holding ``value``; one here."""
        return 0

    def breaks(self):
        """Return ``[(pixel_x, label)]`` per session start; none on a linear scale."""
        return []


class TimeScale(LinearScale):
    """A linear scale over epoch ms whose ticks sit on local clock boundaries.

    Parameters
    ----------
    lo, hi : int
        Epoch ms.
    start, stop : float
        Pixel range.
    local : LocalTime
        The display zone.

    Examples
    --------
    ::

        scale = TimeScale(0, 3_600_000, 0.0, 600.0, LocalTime("UTC"))
        scale.label(1_800_000)  # '00:30'
    """

    def __init__(self, lo, hi, start, stop, local):
        super().__init__(lo, hi, start, stop)
        self.local = local
        self.step = _TIME_STEPS[-1]

    def ticks(self, count=6):
        """Return tick instants aligned to the local clock.

        Parameters
        ----------
        count : int
            The most ticks wanted.

        Returns
        -------
        list of int
        """
        span = self.hi - self.lo
        self.step = next((s for s in _TIME_STEPS if span / s <= count), _TIME_STEPS[-1])
        offset = self.local.offset_ms(int(self.lo))
        first = math.ceil((self.lo + offset) / self.step) * self.step - offset
        return list(range(int(first), int(self.hi) + 1, self.step))

    def label(self, value):
        """Return ``value`` as local clock text sized to the tick step."""
        if self.step >= _DAY_MS:
            return self.local.label(value, "%m-%d")
        if self.hi - self.lo > _DAY_MS:
            return self.local.label(value, "%m-%d %H:%M")
        return self.local.label(value, "%H:%M")


class SessionScale(TimeScale):
    """A time scale that collapses gaps longer than ``gap_ms`` (market-closed time).

    The observed instants split into sessions wherever two neighbours are
    more than ``gap_ms`` apart. Each session gets pixels in proportion to
    its duration; each gap gets :attr:`GAP_PX`. An instant inside a gap
    maps to the end of the session before it. Ticks are clock times inside
    each session; :meth:`breaks` gives each session's start pixel and date
    for the break line and the date label.

    Parameters
    ----------
    xs : sequence of int
        Every instant the chart will place, any order.
    start, stop : float
        Pixel range.
    local : LocalTime
    gap_ms : int or None
        The break threshold; None uses :func:`auto_gap_ms`.

    Examples
    --------
    ::

        day = 86_400_000
        scale = SessionScale([0, 60_000, day, day + 60_000], 0.0, 100.0, LocalTime("UTC"))
        len(scale.sessions)  # 2
    """

    #: Pixels a collapsed gap occupies.
    GAP_PX = 10

    def __init__(self, xs, start, stop, local, gap_ms=None):
        values = sorted({int(x) for x in xs})
        super().__init__(values[0], values[-1], start, stop, local)
        self.gap_ms = auto_gap_ms(values) if gap_ms is None else gap_ms
        sessions, first = [], values[0]
        for before, after in zip(values, values[1:]):
            if after - before > self.gap_ms:
                sessions.append((first, before))
                first = after
        sessions.append((first, values[-1]))
        total = sum(hi - lo for lo, hi in sessions)
        floor = max(total // 100, _MINUTE_MS)
        spans = [max(hi - lo, floor) for lo, hi in sessions]
        usable = (stop - start) - self.GAP_PX * (len(sessions) - 1)
        per_ms = usable / sum(spans)
        self.sessions, pixel = [], start
        for (lo, hi), span in zip(sessions, spans):
            self.sessions.append((lo, hi, span, pixel, pixel + span * per_ms))
            pixel += span * per_ms + self.GAP_PX
        self.per_ms = per_ms
        self._starts = [session[0] for session in self.sessions]

    def segment_of(self, value):
        """Return the index of the session holding ``value`` (or the one before its gap)."""
        return max(0, bisect.bisect_right(self._starts, value) - 1)

    def __call__(self, value):
        """Return the pixel position of ``value``."""
        lo, hi, span, left, right = self.sessions[self.segment_of(value)]
        clamped = min(max(value, lo), hi)
        return left + (clamped - lo) / span * (right - left)

    def ticks(self, count=6):
        """Return clock-aligned instants inside each session, at least ``_TICK_PX`` apart.

        Parameters
        ----------
        count : int
            Ignored; spacing is set by pixels, not by a count.

        Returns
        -------
        list of int
        """
        self.step = next((s for s in _TIME_STEPS if s * self.per_ms >= _TICK_PX),
                         _TIME_STEPS[-1])
        if self.step >= _DAY_MS:
            return []
        out = []
        for lo, hi, _span, left, _right in self.sessions:
            offset = self.local.offset_ms(lo)
            first = math.ceil((lo + offset) / self.step) * self.step - offset
            out.extend(t for t in range(int(first), int(hi) + 1, self.step)
                       if self(t) - left >= _TICK_PX / 2)
        return out

    def label(self, value):
        """Return ``value`` as local ``HH:MM`` (the date rides on the session label)."""
        return self.local.label(value, "%H:%M")

    def breaks(self):
        """Return ``[(pixel_x, "Mon 10-13")]`` for every session, the first included."""
        return [(left, self.local.label(lo, "%a %m-%d"))
                for lo, _hi, _span, left, _right in self.sessions]


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """One named line.

    Parameters
    ----------
    name : str
    points : sequence of (float, float)
        x-sorted; a ``None`` y is skipped.
    css : str
        A series class, ``s0`` .. ``s3``.

    Examples
    --------
    ::

        Series("equity", [(0, 100.0), (60000, 101.0)], "s0").name  # 'equity'
    """

    name: str
    points: tuple
    css: str = "s0"


@dataclass(frozen=True)
class Marker:
    """One event marker.

    Parameters
    ----------
    x, y : float
    shape : str
        ``up``, ``down`` or ``dot``.
    css : str
        ``win``, ``loss``, ``open`` or ``rej``.
    title : str
        The tooltip text.
    hollow : bool
        Draw the outline only.
    label : str
        Short text drawn beside the marker (a cash-flow amount); empty for none.

    Examples
    --------
    ::

        Marker(0, 100.0, "up", "win", "entry at 100").shape  # 'up'
    """

    x: float
    y: float
    shape: str
    css: str
    title: str
    hollow: bool = False
    label: str = ""


class MarkerLayer:
    """A set of markers drawn over an :class:`XYChart`, with a legend label.

    Parameters
    ----------
    name : str
        The legend label.
    markers : sequence of Marker

    Examples
    --------
    ::

        layer = MarkerLayer("fills", [Marker(0, 100.0, "up", "win", "entry")])
        len(layer.markers)  # 1
    """

    #: Marker half-size in SVG units.
    SIZE = 5

    def __init__(self, name, markers):
        self.name, self.markers = name, tuple(markers)

    def render(self, xs, ys):
        """Return the layer's SVG elements under the given scales.

        Parameters
        ----------
        xs, ys : LinearScale

        Returns
        -------
        list of str
        """
        out = []
        for marker in self.markers:
            x, y = xs(marker.x), ys(marker.y)
            css = f"mk {marker.css}" + (" hollow" if marker.hollow else "")
            out.append(f'<path class="{css}" d="{self._shape(marker.shape, x, y)}">'
                       f"<title>{_esc(marker.title)}</title></path>")
            if marker.label:
                out.append(f'<text class="mklabel" x="{x + self.SIZE + 2:.1f}" '
                           f'y="{y - self.SIZE:.1f}">{_esc(marker.label)}</text>')
        return out

    def _shape(self, shape, x, y):
        """Return the path of one marker shape centred on ``(x, y)``."""
        s = self.SIZE
        shapes = {
            "up": f"M{x:.1f},{y - s:.1f}L{x + s:.1f},{y + s * 0.8:.1f}L{x - s:.1f},{y + s * 0.8:.1f}Z",
            "down": f"M{x:.1f},{y + s:.1f}L{x + s:.1f},{y - s * 0.8:.1f}L{x - s:.1f},{y - s * 0.8:.1f}Z",
            "dot": f"M{x - s:.1f},{y:.1f}L{x:.1f},{y - s:.1f}L{x + s:.1f},{y:.1f}L{x:.1f},{y + s:.1f}Z",
        }
        return shapes[shape]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


class Chart(ABC):
    """One self-contained ``<svg>`` with a title; subclasses draw the body.

    Parameters
    ----------
    title : str
    width, height : int
        The canvas, in SVG user units (the chart scales to its container).

    Examples
    --------
    ::

        LineChart("equity", [Series("net", ((0, 1.0), (1, 2.0)))]).render().startswith("<svg")
    """

    def __init__(self, title, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
        self.title, self.width, self.height = title, width, height
        top, right, bottom, left = _MARGIN
        self.x0, self.x1 = left, width - right
        self.y0, self.y1 = top, height - bottom

    def render(self):
        """Return the chart as an ``<svg>`` string.

        Returns
        -------
        str
        """
        head = (f'<svg class="chart" viewBox="0 0 {self.width} {self.height}" '
                f'role="img" aria-label="{_esc(self.title)}">')
        parts = [head, f"<title>{_esc(self.title)}</title>",
                 f'<text class="title" x="{self.x0}" y="16">{_esc(self.title)}</text>']
        parts.extend(self.body())
        parts.append("</svg>")
        return "\n".join(parts)

    @abstractmethod
    def body(self):
        """Return the chart's SVG elements.

        Returns
        -------
        list of str
        """

    def _empty(self, text="no data"):
        """Return the body of a chart with nothing to plot."""
        cx, cy = (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2
        return [f'<text class="muted" x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle">'
                f"{_esc(text)}</text>"]


class XYChart(Chart):
    """Series and marker layers on shared x/y axes, with grid and legend.

    Parameters
    ----------
    title : str
    series : sequence of Series
    layers : sequence of MarkerLayer
    local : LocalTime or None
        Given, x is epoch ms rendered as local time; else plain numbers.
    y_label : str
    boundaries : sequence of float
        x positions of dashed vertical lines (used on an uncompressed axis).
    zero_line : bool
        Draw y = 0 when it is in range.
    gap_ms : int or None or False
        Timed charts only: collapse gaps longer than this (None = auto,
        :func:`auto_gap_ms`); ``False`` keeps the plain linear time axis.

    Examples
    --------
    ::

        chart = LineChart("price", [Series("AAA", ((0, 10.0), (60000, 10.5)))],
                          local=LocalTime("UTC"))
        svg = chart.render()
    """

    #: Pixels per downsampling bucket (four points kept per bucket).
    BUCKET_PX = 2

    def __init__(self, title, series=(), layers=(), *, local=None, y_label="",
                 boundaries=(), zero_line=False, gap_ms=None, width=DEFAULT_WIDTH,
                 height=DEFAULT_HEIGHT):
        super().__init__(title, width, height)
        self.series = tuple(
            Series(s.name, tuple(p for p in s.points if p[1] is not None), s.css) for s in series
        )
        self.layers, self.local, self.y_label = tuple(layers), local, y_label
        self.boundaries, self.zero_line, self.gap_ms = tuple(boundaries), zero_line, gap_ms

    @abstractmethod
    def path(self, points, xs, ys):
        """Return the SVG path data of one series.

        Parameters
        ----------
        points : sequence of (float, float)
        xs, ys : LinearScale

        Returns
        -------
        str
        """

    def body(self):
        """Return grid, axes, boundaries, series, markers and legend."""
        xs_all = [p[0] for s in self.series for p in s.points]
        ys_all = [p[1] for s in self.series for p in s.points]
        for layer in self.layers:
            xs_all.extend(m.x for m in layer.markers)
            ys_all.extend(m.y for m in layer.markers)
        if not xs_all:
            return self._empty()
        xs, ys = self._scales(xs_all, ys_all)
        parts = self._grid(xs, ys)
        parts.extend(self._boundaries(xs))
        if self.zero_line and ys.lo < 0 < ys.hi:
            parts.append(f'<line class="zero" x1="{self.x0}" x2="{self.x1}" '
                         f'y1="{ys(0):.1f}" y2="{ys(0):.1f}"/>')
        buckets = max(1, int((self.x1 - self.x0) / self.BUCKET_PX))
        for s in self.series:
            if not s.points:
                continue
            # Thin in PIXEL space, so a collapsed gap costs no buckets.
            thin = [(x, y) for _px, y, x in
                    downsample([(xs(x), y, x) for x, y in s.points], buckets)]
            runs = [[]]
            for point in thin:
                if runs[-1] and xs.segment_of(point[0]) != xs.segment_of(runs[-1][-1][0]):
                    runs.append([])
                runs[-1].append(point)
            d = "".join(self.path(run, xs, ys) for run in runs)
            parts.append(f'<path class="line {s.css}" d="{d}">'
                         f"<title>{_esc(s.name)}</title></path>")
        for layer in self.layers:
            parts.extend(layer.render(xs, ys))
        parts.extend(self._legend())
        return parts

    def _scales(self, xs_all, ys_all):
        """Build the x and y scales over the data, y padded 5%."""
        lo, hi = min(ys_all), max(ys_all)
        pad = (hi - lo) * 0.05
        ys = LinearScale(lo - pad, hi + pad, self.y1, self.y0)
        if self.local is not None and self.gap_ms is not False:
            scale = SessionScale(xs_all, self.x0, self.x1, self.local, self.gap_ms)
            if len(scale.sessions) > 1:
                return scale, ys
        if self.local is not None:
            return TimeScale(min(xs_all), max(xs_all), self.x0, self.x1, self.local), ys
        return LinearScale(min(xs_all), max(xs_all), self.x0, self.x1), ys

    def _grid(self, xs, ys):
        """Horizontal and vertical gridlines, the two axes, and tick labels."""
        parts = []
        for value in ys.ticks(5):
            y = ys(value)
            parts.append(f'<line class="grid" x1="{self.x0}" x2="{self.x1}" '
                         f'y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<text class="muted" x="{self.x0 - 6}" y="{y + 3:.1f}" '
                         f'text-anchor="end">{_esc(ys.label(value))}</text>')
        for value in xs.ticks(6 if self.local is not None else 5):
            x = xs(value)
            parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" '
                         f'y1="{self.y0}" y2="{self.y1}"/>')
            parts.append(f'<text class="muted" x="{x:.1f}" y="{self.y1 + 14}" '
                         f'text-anchor="middle">{_esc(xs.label(value))}</text>')
        for index, (x, text) in enumerate(xs.breaks()):
            if index:
                gap = x - SessionScale.GAP_PX / 2
                parts.append(f'<line class="break" x1="{gap:.1f}" x2="{gap:.1f}" '
                             f'y1="{self.y0}" y2="{self.y1 + 4}"/>')
            parts.append(f'<text class="session" x="{x:.1f}" y="{self.y1 + 27}">'
                         f"{_esc(text)}</text>")
        parts.append(f'<line class="axis" x1="{self.x0}" x2="{self.x0}" '
                     f'y1="{self.y0}" y2="{self.y1}"/>')
        parts.append(f'<line class="axis" x1="{self.x0}" x2="{self.x1}" '
                     f'y1="{self.y1}" y2="{self.y1}"/>')
        caption = f"time ({self.local.tz})" if self.local is not None else ""
        if xs.breaks():
            caption += "; market-closed gaps collapsed at the breaks"
        parts.append(f'<text class="muted" x="{self.x1}" y="{self.height - 4}" '
                     f'text-anchor="end">{_esc(caption)}</text>')
        if self.y_label:
            parts.append(f'<text class="muted" x="4" y="{self.y0 - 8}">{_esc(self.y_label)}</text>')
        return parts

    def _boundaries(self, xs):
        """Dashed vertical lines at the declared x positions; a session axis draws breaks instead."""
        if xs.breaks():
            return []
        return [
            f'<line class="boundary" x1="{xs(b):.1f}" x2="{xs(b):.1f}" '
            f'y1="{self.y0}" y2="{self.y1}"/>'
            for b in self.boundaries
            if xs.lo <= b <= xs.hi
        ]

    def _legend(self):
        """One swatch and name per series and per marker layer, top right."""
        entries = [(s.name, f'<rect class="swatch {s.css}" width="10" height="3"')
                   for s in self.series]
        entries += [(layer.name, None) for layer in self.layers if layer.markers]
        parts, x = [], self.x1
        for name, swatch in reversed(entries):
            width = 7 * len(name) + (16 if swatch else 0)
            x -= width + 10
            if swatch:
                parts.append(f'{swatch} x="{x:.0f}" y="{self.y0 - 12}"/>')
                parts.append(f'<text x="{x + 14:.0f}" y="{self.y0 - 8}">{_esc(name)}</text>')
            else:
                parts.append(f'<text class="muted" x="{x:.0f}" y="{self.y0 - 8}">'
                             f"{_esc(name)}</text>")
        return parts


class LineChart(XYChart):
    """Series drawn as straight segments between points.

    Examples
    --------
    ::

        LineChart("equity", [Series("net", ((0, 1.0), (1, 2.0)))]).render()
    """

    def path(self, points, xs, ys):
        """Return ``M x y L x y ...`` through every point."""
        return "M" + "L".join(f"{xs(x):.1f},{ys(y):.1f}" for x, y in points)


class StepChart(XYChart):
    """Series that hold each value until the next point (cash, exposure).

    Examples
    --------
    ::

        StepChart("cash", [Series("cash", ((0, 100.0), (5, 80.0)))]).render()
    """

    def path(self, points, xs, ys):
        """Return a horizontal-then-vertical path through the points."""
        first_x, first_y = points[0]
        parts = [f"M{xs(first_x):.1f},{ys(first_y):.1f}"]
        for x, y in points[1:]:
            parts.append(f"H{xs(x):.1f}V{ys(y):.1f}")
        return "".join(parts)


class BarChart(Chart):
    """Labelled vertical bars; negative bars take the loss colour.

    Parameters
    ----------
    title : str
    labels : sequence of str
    values : sequence of float
    y_label : str

    Examples
    --------
    ::

        BarChart("P&L by day", ["09-01", "09-02"], [12.0, -3.0]).render()
    """

    def __init__(self, title, labels, values, *, y_label="", width=DEFAULT_WIDTH,
                 height=DEFAULT_HEIGHT):
        super().__init__(title, width, height)
        self.labels, self.values, self.y_label = tuple(labels), tuple(values), y_label

    def body(self):
        """Return the bars, a zero line, the y grid and thinned x labels."""
        if not self.values:
            return self._empty()
        ys = LinearScale(min(0.0, *self.values), max(0.0, *self.values), self.y1, self.y0)
        parts = []
        for value in ys.ticks(5):
            y = ys(value)
            parts.append(f'<line class="grid" x1="{self.x0}" x2="{self.x1}" '
                         f'y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<text class="muted" x="{self.x0 - 6}" y="{y + 3:.1f}" '
                         f'text-anchor="end">{_esc(ys.label(value))}</text>')
        band = (self.x1 - self.x0) / len(self.values)
        every = max(1, math.ceil(len(self.values) / 12))
        for index, (label, value) in enumerate(zip(self.labels, self.values)):
            x = self.x0 + index * band
            top, bottom = sorted((ys(value), ys(0.0)))
            css = "bar neg" if value < 0 else "bar"
            parts.append(f'<rect class="{css}" x="{x + band * 0.1:.1f}" y="{top:.1f}" '
                         f'width="{band * 0.8:.1f}" height="{max(bottom - top, 0.5):.1f}">'
                         f"<title>{_esc(label)}: {_esc(_number_label(value))}</title></rect>")
            if index % every == 0:
                parts.append(f'<text class="muted" x="{x + band / 2:.1f}" y="{self.y1 + 14}" '
                             f'text-anchor="middle">{_esc(label)}</text>')
        parts.append(f'<line class="zero" x1="{self.x0}" x2="{self.x1}" '
                     f'y1="{ys(0.0):.1f}" y2="{ys(0.0):.1f}"/>')
        if self.y_label:
            parts.append(f'<text class="muted" x="4" y="{self.y0 - 8}">{_esc(self.y_label)}</text>')
        return parts


class Histogram(BarChart):
    """Counts of ``values`` in equal-width bins.

    Parameters
    ----------
    title : str
    values : sequence of float
    bins : int
    unit : str
        Appended to the bin labels.

    Examples
    --------
    ::

        Histogram("trade P&L", [1.0, -2.0, 0.5], bins=4).render()
    """

    def __init__(self, title, values, bins=20, *, unit="", width=DEFAULT_WIDTH,
                 height=DEFAULT_HEIGHT):
        labels, counts = self._binned(list(values), bins, unit)
        super().__init__(title, labels, counts, y_label="count", width=width, height=height)

    @staticmethod
    def _binned(values, bins, unit):
        """Bin labels and counts over ``values``."""
        if not values:
            return (), ()
        lo, hi = min(values), max(values)
        if hi == lo:
            return (f"{_number_label(lo)}{unit}",), (len(values),)
        width = (hi - lo) / bins
        counts = [0] * bins
        for value in values:
            counts[min(bins - 1, int((value - lo) / width))] += 1
        labels = [f"{_number_label(lo + i * width)}{unit}" for i in range(bins)]
        return tuple(labels), tuple(counts)
