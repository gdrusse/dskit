"""The pipeline doorway: an ``EvaluationReport`` node (role ``report``).

A run document wires a node that emits schema-v1 event dicts into this
one by dotted path (``dskit.evaluation.nodes:EvaluationReport``) — no
registration, so importing the package registers nothing (ADR-0183 item
9). The node validates the events into an
:class:`~dskit.evaluation.events.EventLog`, renders
:class:`~dskit.evaluation.report.BacktestReport` into ``out_dir`` and
returns the verdict and key statistics as ``metrics`` (the driver forwards
numeric leaves to the tracking sinks) and the written ``paths``.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import os

from dskit.evaluation.events import EventLog
from dskit.evaluation.report import BacktestReport
from dskit.pipeline.node import Node, reject_unknown_params

__all__ = ["EvaluationReport"]


class EvaluationReport(Node):
    """Render a backtest report from an ``events`` list (role ``report``).

    Parameters
    ----------
    params : dict
        ``out_dir`` (str, REQUIRED) — where the five files land; a
        relative path is taken under the run directory. ``title`` (str,
        optional) — overrides ``run_start.title``.

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
    _PARAMS = ("out_dir", "title", "notes")

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
        if "title" in params and not isinstance(params["title"], str):
            problems.append(f"title must be a string, got {params['title']!r}")
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
        log = EventLog(inputs["events"])
        report = BacktestReport(log, title=self.params.get("title"))
        out_dir = os.path.expanduser(self.params["out_dir"])
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(ctx.run_dir, out_dir)
        return {"metrics": report.metrics(), "paths": report.write(out_dir)}
