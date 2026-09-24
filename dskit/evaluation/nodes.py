"""The pipeline doorway: an ``EvaluationReport`` node (role ``report``).

A run document wires a node that emits schema-v1 event dicts into this
one by dotted path (``dskit.evaluation.nodes:EvaluationReport``) — no
registration, so importing the package registers nothing (ADR-0183 item
9). The node validates the events into an
:class:`~dskit.evaluation.events.EventLog`, renders
:class:`~dskit.evaluation.report.BacktestReport` into ``out_dir`` and
returns the verdict and key statistics as ``metrics`` (the driver forwards
numeric leaves to the tracking sinks) and the written ``paths``. Before
rendering it fills the provenance the producer left out — git revision,
environment, wall time — through
:func:`~dskit.evaluation.provenance.fill_provenance`, which records where
each came from.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import os

from dskit.evaluation.events import EventLog
from dskit.evaluation.mapping import CANDIDATES_PORT, PORTS, EventMapping
from dskit.evaluation.provenance import fill_provenance, run_dir_provenance
from dskit.evaluation.report import BacktestReport
from dskit.evaluation.sections import chosen_sections, section_problems
from dskit.pipeline.node import Node, reject_unknown_params

__all__ = ["RUNS_DIR_NAME", "EvaluationReport", "RowsToEvents"]

#: The driver's runs directory name; a relative ``out_dir`` must not repeat
#: it, because it already resolves inside ``<cwd>/pipeline_runs/<run>/``.
RUNS_DIR_NAME = "pipeline_runs"


class EvaluationReport(Node):
    """Render a backtest report from an ``events`` list (role ``report``).

    Parameters
    ----------
    params : dict
        ``out_dir`` (str, REQUIRED) — where the five files land. A
        relative path resolves against the RUN directory
        (``<cwd>/pipeline_runs/<run>/``), so ``"report"`` lands at
        ``pipeline_runs/<run>/report/``; a relative path beginning with
        ``pipeline_runs`` is refused because it would nest a second runs
        directory inside the run. An absolute (or ``~``) path is used as
        given. ``title`` (str, optional) — overrides ``run_start.title``.
        ``sections`` (list of str, optional) — which sections to render, by
        anchor (:data:`~dskit.evaluation.sections.SECTIONS`); ``summary``
        and ``provenance`` always render; omitted renders all of them.

    Inputs
    ------
    ``events`` (REQUIRED)
        A list of schema-v1 event objects, in log order.

    Outputs
    -------
    ``metrics``
        ``verdict``, ``census_ok`` and the key statistics.
    ``paths``
        ``{events, summary, html, decisions, trades}`` -> file path.

    Examples
    --------
    Wire it after a node that emits the events::

        node = EvaluationReport("report", {"out_dir": "evaluation", "title": "Replay"})
        out = node.run(ctx, {"events": events})
        out["metrics"]["verdict"]  # 'PASS', 'FAIL', 'INCONCLUSIVE' or 'UNJUDGED'
    """

    role = "report"
    outputs = ("metrics", "paths")
    _PARAMS = ("out_dir", "title", "sections", "notes")

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs, a missing ``out_dir`` and a non-string title.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        out_dir = params.get("out_dir")
        if not isinstance(out_dir, str) or not out_dir:
            problems.append(f"out_dir must be a non-empty string, got {out_dir!r}")
        elif not os.path.isabs(os.path.expanduser(out_dir)):
            first = os.path.normpath(out_dir).split(os.sep)[0]
            if first == RUNS_DIR_NAME:
                problems.append(
                    f"out_dir {out_dir!r} would nest {RUNS_DIR_NAME}/ inside the run directory "
                    f"(a relative out_dir already resolves under {RUNS_DIR_NAME}/<run>/); "
                    f"use e.g. 'report'")
        if "title" in params and not isinstance(params["title"], str):
            problems.append(f"title must be a string, got {params['title']!r}")
        if "sections" in params:
            problems.extend(section_problems(params["sections"]))
        return problems

    def validate_inputs(self, inputs):
        """Refuse an ``events`` port that is not a list of objects.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
        """
        events = inputs.get("events")
        if not isinstance(events, (list, tuple)):
            return [f"events must be a list of event objects, got {type(events).__name__}"]
        bad = [i for i, e in enumerate(events) if not isinstance(e, dict)]
        return [f"events[{bad[0]}] is not an object ({len(bad)} such)"] if bad else []

    def run(self, ctx, inputs):
        """Validate the events, write the report, return metrics and paths.

        Parameters
        ----------
        ctx : NodeContext
        inputs : dict

        Returns
        -------
        dict
        """
        run_dir = getattr(ctx, "run_dir", None)
        log = EventLog(fill_provenance(inputs["events"], run_dir))
        report = BacktestReport(log, chosen_sections(self.params.get("sections")),
                                title=self.params.get("title"))
        out_dir = os.path.expanduser(self.params["out_dir"])
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(run_dir, out_dir)
        return {"metrics": report.metrics(), "paths": report.write(out_dir)}


class RowsToEvents(Node):
    """Map a pipeline's row lists onto ``dskit-eval-v1`` events from JSON alone.

    Role ``transform``, used by dotted path
    (``dskit.evaluation.nodes:RowsToEvents``). Each wired port's rows
    become events of one kind through its ``map`` entry
    (:class:`~dskit.evaluation.mapping.EventMapping`); wire ``events``
    into :class:`EvaluationReport`.

    Parameters
    ----------
    params : dict
        ``run_start`` (object, REQUIRED) — the ``run_start`` body: ``tz``
        (required), ``title``, ``project``, ``criteria``, ``trials``,
        ``units``, ``run_id`` …; inside a run the run directory's
        ``run_id``, ``config_hash``, ``data`` and ``config`` override it.
        ``map`` (object, REQUIRED) — ``{port: {fields, ts?, known?,
        instrument?, explode?}}`` over the input ports (plus
        ``decision_id`` for ``candidates``); see
        :class:`~dskit.evaluation.mapping.RowMap`.

    Inputs
    ------
    ``decisions``, ``orders``, ``refusals``, ``skips``, ``fills``, ``marks``,
    ``cashflows``, ``outcomes``, ``solves``, ``candidates`` (each optional)
        Lists of row objects; every wired port needs a ``map`` entry.

    Outputs
    -------
    ``events``
        The validated event list, ``seq`` 0..n-1.

    Examples
    --------
    Fills and marks, one instrument field::

        node = RowsToEvents("events", {
            "run_start": {"tz": "America/New_York", "title": "Backtest"},
            "map": {
                "fills": {"instrument": "symbol", "fields": {
                    "fill_id": {"template": "f-{symbol}-{asof_ms}"},
                    "side": "side", "qty": "qty", "price": "price", "fee": "fee"}},
                "marks": {"instrument": "symbol", "fields": {"price": "close"}},
            }})
        out = node.run(ctx, {"fills": fills, "marks": bars})
    """

    role = "transform"
    outputs = ("events",)
    _PARAMS = ("run_start", "map", "notes")

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs and a malformed ``run_start`` or ``map``.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("run_start", "map"):
            if name not in params:
                problems.append(f"{name} is required")
        if not problems:
            problems.extend(EventMapping.problems(params["run_start"], params["map"]))
        return problems

    def validate_inputs(self, inputs):
        """Refuse an unknown port, a non-list port, or a wired port with no map entry.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
        """
        ports = (*PORTS, CANDIDATES_PORT)
        problems = [f"unknown input port {port!r} — allowed: {list(ports)}"
                    for port in inputs if port not in ports]
        for port, rows in inputs.items():
            if port not in ports or rows is None:
                continue
            if not isinstance(rows, (list, tuple)):
                problems.append(f"{port} must be a list of rows, got {type(rows).__name__}")
            elif port not in self.params["map"]:
                problems.append(f"{port} is wired but map declares no {port!r} entry")
        return problems

    def run(self, ctx, inputs):
        """Map every wired port and return the event list.

        Parameters
        ----------
        ctx : NodeContext or None
        inputs : dict

        Returns
        -------
        dict
        """
        mapping = EventMapping(self.params["run_start"], self.params["map"])
        provenance = run_dir_provenance(getattr(ctx, "run_dir", None))
        return {"events": mapping.events(inputs, provenance)}
