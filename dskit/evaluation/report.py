"""The backtest report: one event log in, five files out.

:class:`BacktestReport` is the pure function ADR-0183 names — ``events ->
artifacts`` — so any past run can be re-rendered from its ``events.jsonl``
alone. It builds the context once (book, statistics, scorecard, census),
hands it to each :class:`~dskit.evaluation.sections.Section`, and writes:

``events.jsonl``
    The log, canonical and re-readable — the source of truth travels with
    the report.
``summary.md``
    The plain-English "What happened" paragraph and reader's note, then
    the card, verdict, statistics, census, top refusal reasons and
    provenance, for a terminal or a PR.
``report.html``
    Every section, one self-contained file: inline CSS and SVG, no
    script, no URL of any kind.
``decisions.csv`` / ``trades.csv``
    The full decision log and every FIFO round trip.

Every file lands through :func:`dskit.pipeline.node.atomic_write`.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import html
import os

from dskit.evaluation.book import EvaluationBook, RoundTrip
from dskit.evaluation.criteria import Scorecard
from dskit.evaluation.sections import (
    DECISION_COLUMNS,
    DEFAULT_MAX_MARKERS,
    DEFAULT_MAX_PANELS,
    DEFAULT_SECTIONS,
    DecisionLogSection,
    ReportContext,
    caveats_html,
    caveats_markdown,
)
from dskit.evaluation.names import is_bare_file_name
from dskit.evaluation.statistics import DEFAULT_MIN_N, STAT_NAMES, StatisticsTable
from dskit.evaluation.svg import CHART_CSS
from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_report import csv_text
from dskit.pipeline.node import atomic_write
from dskit.pipeline.runs import render_cell

__all__ = ["FILENAMES", "KEY_STATS", "PAGE_CSS", "BacktestReport"]

#: What :meth:`BacktestReport.write` writes, keyed by the name it returns.
FILENAMES = {
    "events": "events.jsonl",
    "summary": "summary.md",
    "html": "report.html",
    "decisions": "decisions.csv",
    "trades": "trades.csv",
}

#: The statistics :meth:`BacktestReport.metrics` surfaces to a tracking sink.
KEY_STATS = ("net_pnl", "gross_pnl", "fees", "twr", "max_drawdown", "trades", "hit_rate",
             "profit_factor", "trade_t", "daily_sharpe", "psr", "dsr", "decisions", "fills")

#: The page's own styling; the chart palette is :data:`~dskit.evaluation.svg.CHART_CSS`.
PAGE_CSS = """
body { margin: 0 auto; max-width: 1000px; padding: 16px;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--ev-fg); background: var(--ev-bg); }
h1 { font-size: 22px; margin: 8px 0; } h2 { font-size: 18px; margin-top: 32px;
  border-bottom: 1px solid var(--ev-grid); padding-bottom: 4px; } h3 { font-size: 15px; }
nav a { margin-right: 12px; color: var(--ev-s0); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; margin: 8px 0; font-size: 12.5px; }
th, td { border-bottom: 1px solid var(--ev-grid); padding: 3px 8px; text-align: left;
  vertical-align: top; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
th { position: sticky; top: 0; background: var(--ev-bg); }
.note { color: var(--ev-muted); font-size: 12.5px; }
.verdict { font-size: 16px; padding: 6px 10px; border-left: 4px solid var(--ev-muted); }
.v-pass { border-color: var(--ev-win); } .v-fail { border-color: var(--ev-loss); }
.v-inconclusive { border-color: var(--ev-rej); }
p.story { font-size: 15px; line-height: 1.55; max-width: 860px; }
table.stats td:nth-child(2) { text-align: right; font-variant-numeric: tabular-nums; }
table.log { font-size: 12px; } table.log td { white-space: nowrap; padding: 1px 6px; }
h4 { font-size: 13.5px; margin: 14px 0 4px; }
ul.problems { color: var(--ev-loss); }
details summary { cursor: pointer; margin: 8px 0; }
pre { overflow-x: auto; font-size: 12px; }
"""


class BacktestReport:
    """Render an :class:`~dskit.evaluation.events.EventLog` as a report.

    Parameters
    ----------
    log : EventLog
        A validated log.
    sections : sequence of Section classes or instances, optional
        Defaults to :data:`~dskit.evaluation.sections.DEFAULT_SECTIONS`.
    title : str or None
        Defaults to ``run_start.title``, then the run id.
    min_n : int
        The "insufficient n" floor for sample-dependent statistics.
    max_panels : int
        The trades-on-price panel cap.
    max_markers : int
        The fill-marker cap per trades-on-price panel.
    caveats : sequence of str
        Non-empty labels for a report whose figures differ in basis from the
        authoritative source. Each prints in a banner under the title (page
        and ``summary.md``) and again above the Statistics table. Default
        ``()`` prints nothing and leaves every file unchanged.
    statistic_labels : dict or None
        ``{name in STAT_NAMES: label}``: the label replaces the statistic's
        name in the Statistics table (display only; criteria, metrics and
        CSVs keep the name).
    narrative_driver : bool
        False drops the "Biggest driver" clause from "What happened".
        Default True.

    Raises
    ------
    ConfigError
        When a pre-registered criterion names an unknown statistic, two
        sections share an anchor, or an option fails :meth:`option_problems`.

    Examples
    --------
    ::

        report = BacktestReport(EventLog.read("events.jsonl"))
        paths = report.write("out/report")
        paths["html"]
        # -> 'out/report/report.html'
    """

    def __init__(self, log, sections=None, *, title=None, min_n=DEFAULT_MIN_N,
                 max_panels=DEFAULT_MAX_PANELS, max_markers=DEFAULT_MAX_MARKERS,
                 caveats=(), statistic_labels=None, narrative_driver=True):
        problems = self.option_problems(caveats, statistic_labels, narrative_driver)
        if problems:
            raise ConfigError(problems)
        self.log = log
        chosen = DEFAULT_SECTIONS if sections is None else sections
        self.sections = tuple(s() if isinstance(s, type) else s for s in chosen)
        anchors = [section.anchor for section in self.sections]
        repeated = sorted({a for a in anchors if anchors.count(a) > 1})
        if repeated:
            raise ConfigError([f"section anchor(s) {repeated} are used twice — an anchor is "
                               f"the HTML id and must be unique"])
        start = log.run_start
        book = EvaluationBook(log)
        table = StatisticsTable(book, min_n=min_n)
        self.context = ReportContext(
            title=title or start.get("title") or start.get("run_id"),
            log=log,
            book=book,
            table=table,
            scorecard=Scorecard(start.criteria, table),
            census=log.census(),
            max_panels=max_panels,
            max_markers=max_markers,
            caveats=tuple(caveats),
            statistic_labels=dict(statistic_labels or {}),
            narrative_driver=narrative_driver,
        )

    @staticmethod
    def option_problems(caveats=(), statistic_labels=None, narrative_driver=True):
        """Return every problem with the display options; empty when acceptable.

        Parameters
        ----------
        caveats : object
            A list of non-blank strings.
        statistic_labels : object
            None, or an object of ``STAT_NAMES`` member -> non-blank label.
        narrative_driver : object
            A bool.

        Returns
        -------
        list of str
        """
        problems = []
        if not isinstance(caveats, (list, tuple)):
            problems.append(f"caveats must be a list of strings, got {caveats!r}")
        else:
            problems.extend(f"caveats[{i}] must be a non-empty string, got {text!r}"
                            for i, text in enumerate(caveats)
                            if not isinstance(text, str) or not text.strip())
        if statistic_labels is not None:
            if not isinstance(statistic_labels, dict):
                problems.append(f"statistic_labels must be an object of statistic -> label, "
                                f"got {statistic_labels!r}")
            else:
                unknown = sorted(str(name) for name in statistic_labels
                                 if name not in STAT_NAMES)
                if unknown:
                    problems.append(f"statistic_labels names unknown statistic(s) {unknown} — "
                                    f"allowed: {list(STAT_NAMES)}")
                problems.extend(f"statistic_labels.{name} must be a non-empty string, got "
                                f"{label!r}" for name, label in statistic_labels.items()
                                if not isinstance(label, str) or not label.strip())
        if not isinstance(narrative_driver, bool):
            problems.append(f"narrative_driver must be a boolean, got {narrative_driver!r}")
        return problems

    def html(self):
        """Return the self-contained HTML report.

        Returns
        -------
        str
        """
        title = html.escape(self.context.title)
        nav = " ".join(f'<a href="#{html.escape(s.anchor)}">{html.escape(s.title)}</a>'
                       for s in self.sections)
        body = "\n".join(section.render(self.context) for section in self.sections)
        return (
            "<!DOCTYPE html>\n<html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{CHART_CSS}{PAGE_CSS}</style></head>"
            f"<body><h1>{title}</h1>{caveats_html(self.context)}<nav>{nav}</nav><main>{body}</main></body></html>\n"
        )

    def summary_markdown(self):
        """Return ``summary.md``: the title, then every section's markdown lines, in order.

        Returns
        -------
        str
        """
        lines = [f"# {render_cell(self.context.title)}", "", *caveats_markdown(self.context)]
        for section in self.sections:
            lines.extend(section.markdown(self.context))
        return "\n".join(lines).rstrip() + "\n"

    def decisions_csv(self):
        """Return every decision as CSV over :data:`~dskit.evaluation.sections.DECISION_COLUMNS`.

        Returns
        -------
        str
        """
        return csv_text(DECISION_COLUMNS, DecisionLogSection().rows(self.context))

    def trades_csv(self):
        """Return every FIFO round trip as CSV over ``RoundTrip.COLUMNS``.

        Returns
        -------
        str
        """
        return csv_text(RoundTrip.COLUMNS,
                        [trip.to_obj() for trip in self.context.book.round_trips])

    def metrics(self):
        """Return the verdict and the key statistics for a run record.

        Returns
        -------
        dict
            ``verdict`` (str), ``census_ok`` (bool), and each
            :data:`KEY_STATS` member whose value is a number.
        """
        out = {"verdict": self.context.scorecard.status, "census_ok": self.context.census.ok}
        for name in KEY_STATS:
            value = self.context.table.get(name).value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[name] = value
        return out

    def write(self, out_dir):
        """Write the five report files, plus each section's own, into ``out_dir``.

        Parameters
        ----------
        out_dir : str
            Created if needed — after every section's file names are checked, so
            a refused write creates nothing.

        Returns
        -------
        dict
            ``{name: path}`` over :data:`FILENAMES`, then ``{file name: path}``
            for each file a section adds (:meth:`Section.files`).

        Raises
        ------
        ConfigError
            When two files share a name, or a section's file name is empty
            or not a bare name.
        """
        payloads = {
            "events": self.log.to_jsonl(),
            "summary": self.summary_markdown().encode("utf-8"),
            "html": self.html().encode("utf-8"),
            "decisions": self.decisions_csv().encode("utf-8"),
            "trades": self.trades_csv().encode("utf-8"),
        }
        names = {name: FILENAMES[name] for name in payloads}
        seen = set(names) | set(names.values())
        for section in self.sections:
            for filename, text in section.files(self.context).items():
                if not is_bare_file_name(filename):
                    raise ConfigError([f"section {section.anchor!r} file name {filename!r} must "
                                       f"be a bare file name"])
                if filename in seen:
                    raise ConfigError([f"section {section.anchor!r} file {filename!r} collides "
                                       f"with another file of the report"])
                seen.add(filename)
                names[filename] = filename
                payloads[filename] = text.encode("utf-8")
        os.makedirs(out_dir, exist_ok=True)
        paths = {}
        for name, raw in payloads.items():
            paths[name] = os.path.join(out_dir, names[name])
            atomic_write(paths[name], raw)
        return paths
