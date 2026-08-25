"""The tier-2 matplotlib pack (ADR-0029): the mark grammar, rendering,
skip-and-count, the subclass base, conformance."""

from __future__ import annotations

import math
import os

import pytest

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.matplotlib import (
    NODE_KINDS,
    PALETTE,
    DeclaredFigure,
    FigureNode,
    register,
)
from dskit.pipeline.node import NodeContext, NodeKindRegistry, node_class_errors

pytest.importorskip("matplotlib")


def rows(n=12):
    return [
        {"t": float(i), "a": math.sin(i / 3.0), "b": 0.1 * i, "bucket": f"b{i % 3}"}
        for i in range(n)
    ]


PARAMS = {
    "title": "demo",
    "xlabel": "t",
    "ylabel": "value",
    "marks": [
        {"kind": "line", "x": "t", "y": "a", "label": "sin"},
        {"kind": "scatter", "x": "t", "y": "b", "label": "drift"},
    ],
}


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def render(tmp_path, *, sub="run", params=None, data=None):
    node = DeclaredFigure("fig", dict(params if params is not None else PARAMS))
    return node.run(ctx(tmp_path, sub), {"rows": rows() if data is None else data})


# -- the mark grammar ----------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"marks": None}, "marks is required"),
        ({"marks": []}, "marks is required"),
        ({"marks": [{"kind": "pie", "x": "t"}]}, "kind"),
        ({"marks": [{"kind": "line", "y": "a"}]}, "x"),
        ({"marks": [{"kind": "line", "x": "t"}]}, "y"),
        ({"marks": [{"kind": "hist", "x": "a", "y": "b"}]}, "unknown key"),
        ({"marks": [{"kind": "hist", "x": "a", "bins": 0}]}, "bins"),
        ({"marks": [{"kind": "line", "x": "t", "y": "a", "lw": 3}]}, "lw"),
        ({"marks": [{"kind": "line", "x": "t", "y": "a", "color": ""}]}, "color"),
        ({"title": ""}, "title"),
        ({"filename": "sub/dir.png"}, "filename"),
        ({"dpi": 0}, "dpi"),
        ({"warm": 1}, "warm"),
    ],
)
def test_param_validation_refuses_by_name(override, needle):
    params = {**PARAMS, **override}
    params = {k: v for k, v in params.items() if v is not None}
    problems = DeclaredFigure.validate_params(params)
    assert any(needle in p for p in problems)


def test_more_marks_than_palette_slots_refuse():
    marks = [
        {"kind": "line", "x": "t", "y": "a"} for _ in range(len(PALETTE) + 1)
    ]
    problems = DeclaredFigure.validate_params({"marks": marks})
    assert any("two figures" in p for p in problems)


def test_reference_params_validate_clean():
    assert DeclaredFigure.validate_params(dict(PARAMS)) == []
    hist = {"marks": [{"kind": "hist", "x": "a", "bins": 5, "notes": "why"}]}
    assert DeclaredFigure.validate_params(hist) == []


# -- rendering -----------------------------------------------------------------


def test_renders_a_png_with_metrics(tmp_path):
    out = render(tmp_path)
    assert set(out) == {"figure_path", "metrics"}
    path = out["figure_path"]
    assert os.path.isfile(path)
    assert os.path.basename(path) == "figure.png"
    with open(path, "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
    assert out["metrics"] == {"n_points": 24, "n_skipped": 0}


@pytest.mark.parametrize(
    "mark",
    [
        {"kind": "line", "x": "t", "y": "a"},
        {"kind": "scatter", "x": "t", "y": "a"},
        {"kind": "bar", "x": "bucket", "y": "b", "label": "by bucket"},
        {"kind": "hist", "x": "a", "bins": 4},
    ],
)
def test_every_mark_kind_renders(tmp_path, mark):
    out = render(tmp_path, params={"marks": [mark], "filename": "one.png"})
    assert os.path.isfile(out["figure_path"])
    assert out["metrics"]["n_points"] == 12


def test_gapped_rows_are_skipped_and_counted_never_fabricated(tmp_path):
    data = rows() + [{"t": 99.0}, {"t": float("nan"), "a": 1.0, "b": 1.0}]
    out = render(tmp_path, data=data)
    assert out["metrics"]["n_points"] == 24
    assert out["metrics"]["n_skipped"] == 4  # 2 bad rows x 2 marks


def test_an_all_gap_series_still_renders_and_reports(tmp_path):
    params = {"marks": [{"kind": "line", "x": "t", "y": "missing"}]}
    out = render(tmp_path, params=params)
    assert os.path.isfile(out["figure_path"])
    assert out["metrics"] == {"n_points": 0, "n_skipped": 12}


def test_rows_input_must_be_a_list():
    node = DeclaredFigure("fig", dict(PARAMS))
    problems = node.validate_inputs({"rows": iter(())})
    assert problems and any("LIST" in p for p in problems)


def test_filename_and_dpi_are_honored(tmp_path):
    params = {**PARAMS, "filename": "curve.png", "dpi": 60}
    out = render(tmp_path, params=params)
    assert os.path.basename(out["figure_path"]) == "curve.png"


def test_unlabeled_multi_mark_figures_get_default_labels_and_a_legend():
    """The skeptic finding: legend() over zero labeled artists warns and
    renders nothing — with two or more marks, unlabeled marks default to
    the field they draw, so identity is never color-alone."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    node = DeclaredFigure(
        "fig",
        {
            "marks": [
                {"kind": "line", "x": "t", "y": "a"},
                {"kind": "line", "x": "t", "y": "b"},
            ]
        },
    )
    figure = node.build_figure({"rows": rows()}, node.params)
    try:
        legend = figure.axes[0].get_legend()
        assert legend is not None
        assert [t.get_text() for t in legend.get_texts()] == ["a", "b"]
    finally:
        plt.close(figure)
    single = DeclaredFigure("fig", {"marks": [{"kind": "line", "x": "t", "y": "a"}]})
    figure = single.build_figure({"rows": rows()}, single.params)
    try:
        assert figure.axes[0].get_legend() is None  # one series needs no box
    finally:
        plt.close(figure)


# -- the subclass base ---------------------------------------------------------


class TwoPanel(FigureNode):
    """A bespoke figure the declaration cannot express."""

    _PARAMS = FigureNode._PARAMS + ("fields",)

    def build_figure(self, inputs, params):
        import matplotlib.pyplot as plt

        figure, axes = plt.subplots(2, 1)
        for axis, field in zip(axes, params["fields"]):
            axis.plot([r[field] for r in inputs["rows"]], linewidth=2)
        self._metrics = {"panels": len(params["fields"])}
        return figure


def test_subclass_base_owns_backend_save_and_metrics(tmp_path):
    node = TwoPanel("panels", {"fields": ["a", "b"], "filename": "panels.png"})
    out = node.run(ctx(tmp_path), {"rows": rows()})
    assert os.path.isfile(out["figure_path"])
    assert out["metrics"] == {"panels": 2}


def test_the_abstract_base_stays_out_of_registries():
    problems = node_class_errors(FigureNode, "mpl pack")
    assert any("abstract" in p for p in problems)


# -- the shipped example -------------------------------------------------------


def test_shipped_example_loads_hashes_and_runs(tmp_path):
    import json
    import pathlib

    from dskit.pipeline.document import PipelineDocument, load_document
    from dskit.pipeline.driver import run_document

    example = (
        pathlib.Path(__file__).parents[2] / "examples" / "pipeline" / "mpl-figure.json"
    )
    assert load_document(str(example)).hash == load_document(str(example)).hash
    obj = json.loads(example.read_text(encoding="utf-8"))
    obj["outputs"]["run_root"] = str(tmp_path / "runs")
    result = run_document(PipelineDocument.from_obj(obj), asof="2026-01-01")
    assert result.state == "ran"
    assert os.path.isfile(result.outputs["figure"]["figure_path"])


# -- registration and conformance ----------------------------------------------


def test_register_is_explicit_and_idempotent():
    registry = NodeKindRegistry()
    register(registry)
    register(registry)
    assert registry.kinds() == ("mpl-figure",)
    assert registry.get("mpl-figure") == (DeclaredFigure, False)


def probes(tmp_path):
    return {
        "mpl-figure": NodeProbe(
            params=dict(PARAMS),
            required=("marks",),
            inputs={"rows": rows()},
            stream_ports=("rows",),
            runnable=True,
        ),
    }


TestMplConformance = conformance_suite(
    registry=NODE_KINDS,
    module="dskit.pipeline.libs.matplotlib",
    probes=probes,
    expected_roles={"mpl-figure": "report"},
    name="TestMplConformance",
)
