"""SVG charts are well-formed XML, readable (ticks, legend, tooltips) and bounded in size."""

import xml.etree.ElementTree as ET

import pytest

from dskit.evaluation.events import LocalTime
from dskit.evaluation.svg import (
    BarChart,
    Histogram,
    LinearScale,
    LineChart,
    Marker,
    MarkerLayer,
    Series,
    StepChart,
    TimeScale,
    downsample,
)
from tests.evaluation.conftest import DAY1, MIN


def _parse(svg):
    return ET.fromstring(svg)


def _texts(root):
    return [el.text for el in root.iter("text")]


def test_a_line_chart_has_axes_ticks_legend_and_marker_tooltips():
    local = LocalTime("America/New_York")
    points = tuple((DAY1 + i * MIN, 100.0 + i % 5) for i in range(120))
    layer = MarkerLayer("fills", [Marker(DAY1, 100.0, "up", "win", "ENTRY <why> & fee"),
                                  Marker(DAY1 + 60 * MIN, 101.0, "dot", "rej", "REFUSED",
                                         hollow=True)])
    root = _parse(LineChart("AAA & co", [Series("AAA", points), Series("B", points, "s1")],
                            [layer], local=local, y_label="price",
                            boundaries=[DAY1 + 30 * MIN]).render())
    texts = _texts(root)
    assert "10:30" in texts and "11:00" in texts  # ticks in the run's zone, not UTC
    assert "time (America/New_York)" in texts
    assert {"AAA", "B", "fills"} <= set(texts)  # legend
    titles = [el.text for el in root.iter("title")]
    assert "ENTRY <why> & fee" in titles and "REFUSED" in titles
    assert any("hollow" in el.get("class", "") for el in root.iter("path"))
    assert len([el for el in root.iter("line") if el.get("class") == "grid"]) >= 4
    assert len([el for el in root.iter("line") if el.get("class") == "boundary"]) == 1


def test_step_bar_histogram_and_empty_charts_parse():
    for chart in (
        StepChart("cash", [Series("cash", ((0, 1.0), (5, 3.0), (9, 2.0)))]),
        BarChart("by day", ["a", "b"], [1.0, -2.0]),
        Histogram("pnl", [1.0, 2.0, 2.5, -1.0], bins=3, unit="bp"),
        Histogram("flat", [1.0, 1.0]),
        LineChart("empty", []),
        BarChart("none", [], []),
    ):
        root = _parse(chart.render())
        assert root.tag == "svg" and root.get("viewBox")


def test_step_paths_hold_then_move():
    root = _parse(StepChart("s", [Series("s", ((0, 1.0), (10, 2.0)))]).render())
    path = next(el for el in root.iter("path") if "line" in el.get("class", ""))
    assert "H" in path.get("d") and "V" in path.get("d")


def test_negative_bars_take_the_loss_class():
    root = _parse(BarChart("b", ["x", "y"], [1.0, -1.0]).render())
    classes = [el.get("class") for el in root.iter("rect")]
    assert classes == ["bar", "bar neg"]


def test_downsample_is_deterministic_bounded_and_keeps_extremes():
    points = [(i, float(i % 97)) for i in range(20000)]
    points[12345] = (12345, 10_000.0)
    thin = downsample(points, 200)
    assert len(thin) <= 800
    assert thin == downsample(points, 200)
    assert (12345, 10_000.0) in thin and thin[0] == points[0] and thin[-1] == points[-1]
    assert downsample(points[:10], 200) == points[:10]


def test_a_week_of_minutes_renders_small():
    local = LocalTime("UTC")
    points = tuple((DAY1 + i * MIN, 100.0 + (i % 13) * 0.1) for i in range(7 * 24 * 60))
    svg = LineChart("week", [Series("x", points)], local=local).render()
    assert len(svg) < 120_000
    labels = _texts(_parse(svg))
    assert any("-" in (t or "") for t in labels)  # multi-day ticks carry the date


def test_scales_map_and_tick():
    scale = LinearScale(0.0, 10.0, 0.0, 100.0)
    assert scale(5.0) == 50.0
    assert scale.ticks(5) == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    flat = LinearScale(3.0, 3.0, 0.0, 10.0)
    assert flat.lo < 3.0 < flat.hi
    times = TimeScale(DAY1, DAY1 + 60 * MIN, 0.0, 600.0, LocalTime("UTC"))
    ticks = times.ticks(6)
    assert all((t - DAY1) % (10 * MIN) == 0 for t in ticks)
    assert times.label(DAY1) == "14:30"


@pytest.mark.parametrize("value, label", [(1234567.0, "1.23M"), (25000.0, "25k"),
                                          (0.00012, "1.20e-04"), (3.14159, "3.142")])
def test_tick_labels_are_compact(value, label):
    assert LinearScale(0.0, 1.0, 0.0, 1.0).label(value) == label
