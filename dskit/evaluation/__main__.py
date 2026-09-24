"""``python -m dskit.evaluation render <events.jsonl> --out <dir>``.

Re-renders a report from its event log alone (ADR-0183 item 10). Exit 0
on success, 1 when the log or its criteria are invalid — every problem is
printed, one per line.
"""

from __future__ import annotations

import argparse
import sys

from dskit.evaluation.events import EvaluationError, EventLog
from dskit.evaluation.report import BacktestReport
from dskit.pipeline.base import ConfigError

__all__ = ["main"]


def main(argv=None):
    """Run the CLI.

    Parameters
    ----------
    argv : list of str or None
        Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit code.
    """
    parser = argparse.ArgumentParser(prog="python -m dskit.evaluation")
    verbs = parser.add_subparsers(dest="verb", required=True)
    render = verbs.add_parser("render", help="render a report from an events.jsonl")
    render.add_argument("events", help="path to a schema-v1 events.jsonl")
    render.add_argument("--out", required=True, help="output directory")
    render.add_argument("--title", default=None, help="override the run's title")
    args = parser.parse_args(argv)
    try:
        report = BacktestReport(EventLog.read(args.events), title=args.title)
        paths = report.write(args.out)
    except (EvaluationError, ConfigError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"verdict {report.context.scorecard.status}")
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
