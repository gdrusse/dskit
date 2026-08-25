"""matplotlib library pack — declared evidence figures (ADR-0029, tier 2).

Both child gap reports found the same pattern: every project hand-rolls
"plot named numeric fields of a row stream" — hardcoded figure
functions, duplicated across rendering stacks, aligned only by
discipline. The pattern is domain-free, so it becomes config:

* ``mpl-figure`` (:class:`DeclaredFigure`, role ``report``) — the
  DOCUMENT declares a list of ``marks`` (``line | scatter | bar |
  hist``) over named fields of the ``rows`` input, plus title/axis
  labels, and the node renders one PNG into the run's artifacts. What a
  chart shows becomes part of the run's hashed identity, like
  everything else.
* :class:`FigureNode` — the abstract base for figures the declaration
  cannot express: subclass, implement ``build_figure(inputs, params) ->
  matplotlib Figure``, and the base owns the headless backend, the
  save, and the artifact path.

Rendering rules (deliberate, not defaults-by-accident):

* **Series colors are assigned in FIXED declaration order** over an
  8-slot categorical palette validated for colorblind separation on a
  light surface — never cycled, never generated: a 9th mark is refused
  at plan (a busier figure is two figures). A mark may override with an
  explicit ``color`` (the child's brand is config, not toolkit code).
* **One y axis.** There is deliberately no twin-axis knob — two
  measures of different scale are two figures.
* A legend renders whenever two or more marks share the axes (or any
  mark is labeled); the grid is recessive; lines are 2px.
* Rows that lack a finite value for a mark's fields are SKIPPED and
  COUNTED into the metrics, never fabricated — the torch pack's row
  rule. Rows plot in STREAM ORDER; sort upstream if the x field is not
  already ordered.

Import cost: stdlib + ``dskit.pipeline`` only — matplotlib is imported
strictly inside run-path methods, under the ``Agg`` backend (no display
is ever needed; ``tests/pipeline/test_purity.py`` enforces the import
rule).
"""

from __future__ import annotations

import math
import os
from abc import abstractmethod
from collections.abc import Mapping

from dskit.pipeline.kinds_stats import _check_int, _reject_unknown
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = [
    "DeclaredFigure",
    "FigureNode",
    "MARK_KINDS",
    "NODE_KINDS",
    "PALETTE",
    "register",
]

#: The mark grammar — a closed vocabulary.
MARK_KINDS = ("bar", "hist", "line", "scatter")

#: The fixed categorical order (8 slots, light surface) — the dataviz
#: reference palette, validated for adjacent-pair colorblind separation.
#: Assigned by DECLARATION order, never cycled; a child re-brands per
#: mark via ``color``, never by editing this tuple.
PALETTE = (
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
)

DEFAULT_DPI = 150
DEFAULT_FILENAME = "figure.png"
DEFAULT_BINS = 10

#: Per-kind allowed mark keys (default-deny inside each mark, I-227).
#: ``notes`` is the config standard's documentation field, everywhere.
_MARK_KEYS = {
    "line": ("color", "kind", "label", "notes", "x", "y"),
    "scatter": ("color", "kind", "label", "notes", "x", "y"),
    "bar": ("color", "kind", "label", "notes", "x", "y"),
    "hist": ("bins", "color", "kind", "label", "notes", "x"),
}


def _value(record, name):
    """Key-or-attr numeric lookup — the torch pack's rule: a finite
    float, or ``None`` (skip and count, never fabricate)."""
    if isinstance(record, Mapping):
        value = record.get(name)
    else:
        value = getattr(record, name, None)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _raw_value(record, name):
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _mark_problems(index, mark):
    """Problems with one declared mark — default-deny inside."""
    where = f"marks[{index}]"
    if not isinstance(mark, dict) or any(not isinstance(k, str) for k in mark):
        return [f"{where} must be a dict with string keys, got {mark!r}"]
    problems = []
    kind = mark.get("kind")
    if kind not in MARK_KINDS:
        problems.append(
            f"{where}.kind must be one of {sorted(MARK_KINDS)}, got {kind!r}"
        )
        return problems  # the allowed-key table needs a real kind
    unknown = sorted(set(mark) - set(_MARK_KEYS[kind]))
    if unknown:
        problems.append(
            f"{where}: unknown key(s) {unknown} for a {kind!r} mark — allowed: "
            f"{sorted(_MARK_KEYS[kind])} (default-deny inside the block, I-227)"
        )
    x = mark.get("x")
    if not isinstance(x, str) or not x:
        problems.append(f"{where}.x must be a non-empty row-key string, got {x!r}")
    if kind == "hist":
        if "bins" in mark:
            _check_int(problems, f"{where}.bins", mark["bins"], ge=1)
    else:
        y = mark.get("y")
        if not isinstance(y, str) or not y:
            problems.append(
                f"{where}.y must be a non-empty row-key string, got {y!r}"
            )
    for key in ("label", "color", "notes"):
        if key in mark and (not isinstance(mark[key], str) or not mark[key]):
            problems.append(
                f"{where}.{key} must be a non-empty string, got {mark[key]!r}"
            )
    return problems


class FigureNode(Node):
    """The abstract figure doorway (role ``report``): implement
    ``build_figure(inputs, params)`` returning a matplotlib ``Figure``;
    the base forces the headless ``Agg`` backend, saves the PNG into the
    run's artifacts, closes the figure, and answers ``figure_path``.

    Knobs the base owns: ``filename`` (default ``figure.png``) and
    ``dpi`` (default 150). Subclasses extend ``_PARAMS`` and override
    ``validate_params`` calling ``super()`` first — the numpy-pack
    pattern. A subclass may stash a ``self._metrics`` dict of numeric
    evidence (points plotted, rows skipped) before returning its figure;
    the base emits it through the ``metrics`` output (``{}`` otherwise).
    """

    role = "report"
    outputs = ("figure_path", "metrics")

    _PARAMS = ("dpi", "filename")

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        filename = params.get("filename", DEFAULT_FILENAME)
        if (
            not isinstance(filename, str)
            or not filename
            or os.path.basename(filename) != filename
        ):
            problems.append(
                f"filename must be a bare file name (no directories), "
                f"got {filename!r}"
            )
        _check_int(problems, "dpi", params.get("dpi", DEFAULT_DPI), ge=1)
        return problems

    @abstractmethod
    def build_figure(self, inputs, params):
        """Return the matplotlib ``Figure`` to save. The subclass hook —
        import matplotlib INSIDE it (the base has already selected the
        Agg backend by the time it runs)."""
        raise NotImplementedError

    def run(self, ctx, inputs):
        import matplotlib

        matplotlib.use("Agg")  # headless everywhere; never a display
        import matplotlib.pyplot as plt

        figure = self.build_figure(inputs, self.params)
        path = os.path.join(
            self.artifact_dir(ctx), self.params.get("filename", DEFAULT_FILENAME)
        )
        try:
            figure.savefig(path, dpi=self.params.get("dpi", DEFAULT_DPI))
        finally:
            plt.close(figure)
        self.log.info("rendered %s", path)
        metrics = getattr(self, "_metrics", None) or {}
        return {"figure_path": path, "metrics": metrics}


class DeclaredFigure(FigureNode):
    """The declared figure (kind ``mpl-figure``): ``marks`` say what to
    draw from the ``rows`` input; ``title``/``xlabel``/``ylabel`` frame
    it. See the module docstring for the rendering rules.
    """

    _PARAMS = FigureNode._PARAMS + ("marks", "title", "xlabel", "ylabel")

    @classmethod
    def validate_params(cls, params):
        problems = super().validate_params(params)
        marks = params.get("marks")
        if not isinstance(marks, list) or not marks:
            problems.append(
                f"marks is required — a non-empty list of mark dicts "
                f"(kinds: {sorted(MARK_KINDS)}), got {marks!r}"
            )
        else:
            if len(marks) > len(PALETTE):
                problems.append(
                    f"marks: {len(marks)} declared, but the fixed categorical "
                    f"order has {len(PALETTE)} slots — a busier figure is two "
                    "figures (colors are never cycled or generated)"
                )
            for i, mark in enumerate(marks):
                problems.extend(_mark_problems(i, mark))
        for key in ("title", "xlabel", "ylabel"):
            if key in params and (
                not isinstance(params[key], str) or not params[key]
            ):
                problems.append(
                    f"{key} must be a non-empty string, got {params[key]!r}"
                )
        return problems

    def validate_inputs(self, inputs):
        rows = inputs.get("rows")
        if not isinstance(rows, list):
            return [
                "rows must be a LIST of rows — a one-shot iterable is "
                f"refused by name, got {type(rows).__name__}"
            ]
        return []

    def _series(self, mark, rows):
        """``(xs, ys, skipped)`` for one mark — ``ys`` is ``None`` for a
        hist. Bar x values stay RAW (categories are legal); everything
        numeric goes through the finite screen."""
        kind = mark["kind"]
        xs, ys, skipped = [], [], 0
        for row in rows:
            if kind == "hist":
                value = _value(row, mark["x"])
                if value is None:
                    skipped += 1
                    continue
                xs.append(value)
            elif kind == "bar":
                category = _raw_value(row, mark["x"])
                value = _value(row, mark["y"])
                if category is None or value is None:
                    skipped += 1
                    continue
                xs.append(str(category))
                ys.append(value)
            else:
                x = _value(row, mark["x"])
                y = _value(row, mark["y"])
                if x is None or y is None:
                    skipped += 1
                    continue
                xs.append(x)
                ys.append(y)
        return xs, (None if kind == "hist" else ys), skipped

    def build_figure(self, inputs, params):
        import matplotlib.pyplot as plt

        rows = inputs["rows"]
        marks = params["marks"]
        figure, axis = plt.subplots(figsize=(8, 4.5))
        totals = {"n_points": 0, "n_skipped": 0}
        labeled = False
        for i, mark in enumerate(marks):
            color = mark.get("color", PALETTE[i])  # fixed order, never cycled
            label = mark.get("label")
            if label is None and len(marks) >= 2:
                # Identity is never color-alone: with two or more marks a
                # legend MUST have entries, so an unlabeled mark defaults
                # to the field it draws (calling legend() with no labeled
                # artists just warns and renders nothing — skeptic pass).
                label = mark.get("y", mark["x"])
            labeled = labeled or label is not None
            xs, ys, skipped = self._series(mark, rows)
            totals["n_points"] += len(xs)
            totals["n_skipped"] += skipped
            if not xs:
                # An empty series is a finding, not a crash: the figure
                # still renders and the metrics say what was missing.
                self.log.warning(
                    "marks[%d] (%s): no plottable rows (%d skipped)",
                    i,
                    mark["kind"],
                    skipped,
                )
                continue
            try:
                if mark["kind"] == "line":
                    axis.plot(xs, ys, color=color, linewidth=2, label=label)
                elif mark["kind"] == "scatter":
                    axis.scatter(xs, ys, color=color, s=36, label=label)
                elif mark["kind"] == "bar":
                    axis.bar(xs, ys, color=color, label=label)
                else:  # hist
                    axis.hist(
                        xs,
                        bins=mark.get("bins", DEFAULT_BINS),
                        color=color,
                        label=label,
                    )
            except ValueError as exc:
                raise ValueError(
                    f"{self.key}: marks[{i}] ({mark['kind']!r}) failed to "
                    f"render: {exc}"
                ) from exc
        if params.get("title"):
            axis.set_title(params["title"])
        if params.get("xlabel"):
            axis.set_xlabel(params["xlabel"])
        if params.get("ylabel"):
            axis.set_ylabel(params["ylabel"])
        axis.grid(True, alpha=0.25, linewidth=0.5)  # recessive, behind marks
        axis.set_axisbelow(True)
        if labeled:
            axis.legend(loc="best")  # identity is never color-alone
        figure.tight_layout()
        self._metrics = totals
        return figure


#: The pack's registerable kind — the abstract base stays subclass
#: material.
NODE_KINDS = (("mpl-figure", DeclaredFigure),)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit
    registry) — explicit and idempotent, the libs doctrine."""
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
