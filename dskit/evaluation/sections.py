"""The report's sections: one class per section, each a pure function of the context.

A :class:`Section` renders its HTML body from a :class:`ReportContext` (the
log, the book, the statistics table, the scorecard and the census, built
once) and may contribute lines to ``summary.md``. The default order is the
reader's: what happened in plain words first, then the card and verdict,
trading P&L, trades on price, the decision log, cash and exposure,
distributions, per-day stability, and provenance last.

Two rules every section keeps. **No look-ahead in the render**: a section
that shows a decision reads only what the decision, its orders and its
fills recorded — never an ``outcome`` event. **Nothing is silently
dropped**: where a render is thinned or capped (markers, panels, the
decision sample), the section says how many were cut and where the full
record lives (``trades.csv``, ``decisions.csv``, ``events.jsonl``).

Numbers display through :mod:`dskit.evaluation.units` — money with its
sign and separators, scores in the run's declared unit — while the CSVs
keep every raw value. HTML is escaped with ``html.escape``; markdown cells
go through :func:`dskit.pipeline.runs.render_cell`, the one owner of the
pipe rule.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import bisect
import html
import json
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cached_property

from dskit.evaluation.diagnostics import ForecastDiagnostics
from dskit.evaluation.narrative import HOW_TO_READ, Narrative
from dskit.evaluation.svg import (
    BarChart,
    Histogram,
    LineChart,
    Marker,
    MarkerLayer,
    Series,
    StepChart,
)
from dskit.evaluation.units import DASH, Units, count, percent, ratio, sig
from dskit.pipeline.runs import render_cell
from dskit.production.guards import max_verdict

__all__ = [
    "DEFAULT_MAX_MARKERS",
    "DEFAULT_MAX_PANELS",
    "DEFAULT_SECTIONS",
    "DECISION_COLUMNS",
    "STAT_FORMATS",
    "CashSection",
    "DecisionLogSection",
    "DistributionSection",
    "EquitySection",
    "InferenceSection",
    "OverviewSection",
    "PeriodSection",
    "ProvenanceSection",
    "ReportContext",
    "Section",
    "SummarySection",
    "TradesOnPriceSection",
]

#: The most trades-on-price panels a report draws; the rest are named.
DEFAULT_MAX_PANELS = 24

#: The most fill markers one trades-on-price panel draws (refusals and
#: skips are never thinned; see :meth:`TradesOnPriceSection.thin`).
DEFAULT_MAX_MARKERS = 40

#: How many reasons a "top reasons" table lists.
_TOP_REASONS = 10

#: How many decisions each "most consequential" list shows.
_SAMPLE = 8

#: The winners and losers per panel that thinning always keeps.
_EXTREMES = 6

#: The decision table's columns, in order (``decisions.csv`` too). Scores,
#: edges and thresholds are RAW here; ``score_unit`` names their unit.
DECISION_COLUMNS = (
    "time", "ts_ms", "decision_id", "candidates", "chosen", "chosen_score", "chosen_rank",
    "runner_up", "runner_up_score", "threshold", "edge", "score_unit", "action", "reason",
    "detail", "model", "orders", "ref_price", "fill_price", "filled_qty", "slippage_bp", "fees",
    "trip_pnl", "rejections", "guard_verdict", "findings",
)

#: How each statistic displays — a table, never a branch.
STAT_FORMATS = {
    **dict.fromkeys(("net_pnl", "gross_pnl", "fees", "max_drawdown", "avg_win", "avg_loss",
                     "trade_mean", "trade_es95"), "money"),
    **dict.fromkeys(("twr", "max_drawdown_pct", "hit_rate", "daily_mean_return", "fill_ratio",
                     "refusal_rate", "skip_rate", "time_in_market"), "percent"),
    **dict.fromkeys(("trades", "days", "decisions", "orders", "fills"), "count"),
    **dict.fromkeys(("max_drawdown_minutes", "holding_median_minutes", "holding_p90_minutes"),
                    "minutes"),
}


@dataclass(frozen=True)
class ReportContext:
    """Everything a section reads, built once per report.

    Parameters
    ----------
    title : str
    log : EventLog
    book : EvaluationBook
    table : StatisticsTable
    scorecard : Scorecard
    census : Census
    max_panels : int
        The trades-on-price panel cap.
    max_markers : int
        The fill-marker cap per trades-on-price panel.

    Examples
    --------
    ::

        context = BacktestReport(log).context
        context.local.tz  # the run's display zone
    """

    title: str
    log: object
    book: object
    table: object
    scorecard: object
    census: object
    max_panels: int = DEFAULT_MAX_PANELS
    max_markers: int = DEFAULT_MAX_MARKERS

    @property
    def local(self):
        """The run's :class:`~dskit.evaluation.events.LocalTime`."""
        return self.log.local_time

    @property
    def links(self):
        """The log's id graph."""
        return self.log.links

    @property
    def units(self):
        """The run's declared :class:`~dskit.evaluation.units.Units`."""
        start = self.log.run_start
        return Units(start.get("units") if start is not None else None)

    def format(self, kind, value):
        """Display ``value`` as ``kind`` (see :func:`display`)."""
        return display(kind, value, self.units)

    @cached_property
    def diagnostics(self):
        """The log's :class:`~dskit.evaluation.diagnostics.ForecastDiagnostics`, built once."""
        return ForecastDiagnostics(self.log)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def display(kind, value, units):
    """Return ``value`` as display text of ``kind``, a dash for None.

    Parameters
    ----------
    kind : str or None
        ``money``, ``pnl`` (signed money), ``count``, ``ratio``,
        ``percent``, ``minutes``, ``score``, ``edge`` (signed score),
        ``bp``, ``text``; None picks by type.
    value : object
    units : Units

    Returns
    -------
    str
    """
    if value is None:
        return DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    formats = {
        "money": lambda v: units.money(v),
        "pnl": lambda v: units.money(v, signed=True),
        "count": count,
        "ratio": ratio,
        "percent": percent,
        "minutes": lambda v: f"{sig(v, 3)} min",
        "score": lambda v: units.score(v),
        "edge": lambda v: units.score(v, signed=True),
        "bp": lambda v: f"{sig(v, 3)} bp",
        "text": str,
    }
    if kind in formats and not isinstance(value, str):
        return formats[kind](value)
    if isinstance(value, int):
        return count(value)
    if isinstance(value, float):
        return ratio(value)
    return str(value)


def _html_table(columns, rows, css="", formats=None, units=None):
    """Render an escaped HTML table; numbers right-aligned, formatted by column."""
    formats, units = formats or {}, units or Units()
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body = []
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            cells.append(f'<td{" class=num" if numeric else ""}>'
                         f"{html.escape(display(formats.get(column), value, units))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    klass = f' class="{css}"' if css else ""
    return (f"<div class=scroll><table{klass}><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _md_table(columns, rows, formats=None, units=None):
    """Render a markdown table, every cell formatted then passed through ``render_cell``."""
    formats, units = formats or {}, units or Units()
    lines = ["| " + " | ".join(render_cell(c) for c in columns) + " |",
             "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(render_cell(display(formats.get(c), row.get(c), units))
                                for c in columns) + " |"
              for row in rows]
    return lines


def _note(text):
    """Render a muted paragraph."""
    return f"<p class=note>{html.escape(text)}</p>"


def _top_reasons(log, limit=_TOP_REASONS):
    """Count the commonest refusal/skip reasons: ``[{kind, reason, count}]``."""
    counts = Counter((e.kind, e.get("reason")) for e in log.of_kind("refusal", "skip"))
    return [{"kind": kind, "reason": reason, "count": n}
            for (kind, reason), n in counts.most_common(limit)]


def _why(decision, units):
    """One-line account of a decision: reason, chosen forecast/rank, edge vs threshold."""
    if decision is None:
        return "no linked decision"
    parts = [f"reason {decision.get('reason')}"]
    row = decision.chosen_row()
    if row is not None:
        parts.append(f"forecast {units.score(row.get('score'))} rank "
                     f"{display(None, row.get('rank'), units)}/{len(decision.candidates)}")
    if decision.get("edge") is not None:
        parts.append(f"edge {units.score(decision.get('edge'), signed=True)}")
    if decision.get("threshold") is not None:
        parts.append(f"thr {units.score(decision.get('threshold'))}")
    if decision.get("detail"):
        parts.append(decision.get("detail"))
    return "; ".join(parts)


def _window(log, local, *kinds):
    """``first → last (n sessions)`` for the given kinds, or a dash."""
    span = log.span(*kinds)
    if span is None:
        return DASH
    days = {local.day(e.ts_ms) for e in log.events
            if (e.kind in kinds if kinds else e.kind not in ("run_start", "run_end"))}
    return (f"{local.stamp(span[0])[:16]} → {local.stamp(span[1])[:16]} "
            f"({len(days)} session{'s' if len(days) != 1 else ''})")


def _trips_by_decision(book):
    """``{decision_id: [RoundTrip]}`` for the trips each decision opened or closed."""
    out = defaultdict(list)
    for trip in book.round_trips:
        if trip.entry_decision_id is not None:
            out[trip.entry_decision_id].append(trip)
        if trip.exit_decision_id is not None and trip.exit_decision_id != trip.entry_decision_id:
            out[trip.exit_decision_id].append(trip)
    return out


def _worst_verdict(orders):
    """Return the strictest verdict over ``orders``' findings (``max_verdict``), or None."""
    findings = [f for order in orders for f in order.get("findings") or ()]
    return max_verdict(findings) if findings else None


def _finding_text(finding):
    """``guard measure value/bound verdict`` for one finding."""
    return (f"{finding['guard']} {finding['measure']} {display(None, finding.get('value'), None)}"
            f"/{display(None, finding.get('bound'), None)} {finding['verdict']}")


def _even(items, limit):
    """Up to ``limit`` items evenly spaced through ``items``, first and last kept."""
    items = list(items)
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:limit]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


class Section(ABC):
    """One report section: an HTML body, and optionally summary lines.

    A subclass sets ``title`` and ``anchor`` and implements :meth:`html`;
    :meth:`render` wraps it and is the same for every section.

    Examples
    --------
    A section that prints the event count::

        class Count(Section):
            title, anchor = "Count", "count"

            def html(self, context):
                return f"<p>{len(context.log)} events</p>"
    """

    #: The heading.
    title = ""
    #: The HTML id, for the table of contents.
    anchor = ""

    @abstractmethod
    def html(self, context):
        """Return the section's HTML body.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        str
        """

    def markdown(self, context):
        """Return the section's ``summary.md`` lines; most sections add none.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        list of str
        """
        return []

    def render(self, context):
        """Return the section wrapped with its heading.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        str
        """
        return (f'<section id="{html.escape(self.anchor)}"><h2>{html.escape(self.title)}</h2>'
                f"{self.html(context)}</section>")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


class OverviewSection(Section):
    """What happened, in plain words, and how to read the rest of the report.

    The paragraph is :class:`~dskit.evaluation.narrative.Narrative` — explicit
    rules over the statistics, census and verdict, never generated prose.

    Examples
    --------
    ::

        OverviewSection().markdown(context)[0]  # '## What happened'
    """

    title, anchor = "What happened", "overview"

    def html(self, context):
        """Return the verdict banner, the paragraph and the reader's note."""
        status = context.scorecard.status
        sentences = " ".join(html.escape(s) for s in Narrative(context).sentences())
        tips = "".join(f"<li>{html.escape(t)}</li>" for t in HOW_TO_READ)
        return (f'<p class="verdict v-{status.lower()}">Verdict: <b>{status}</b></p>'
                f"<p class=story>{sentences}</p>"
                f"<details><summary>How to read this report</summary><ul>{tips}</ul></details>")

    def markdown(self, context):
        """Return the paragraph and the reader's note."""
        return (["## What happened", "", " ".join(Narrative(context).sentences()), "",
                 "### How to read this report", ""]
                + [f"- {tip}" for tip in HOW_TO_READ] + [""])


class SummarySection(Section):
    """The card, the verdict with each criterion, the statistics and the census.

    Examples
    --------
    ::

        SummarySection().render(context)
    """

    title, anchor = "Summary", "summary"

    def card(self, context):
        """Return the run card as ``[{field, value}]``.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        list of dict
        """
        log, local = context.log, context.local
        start, end = log.run_start, log.run_end
        units = context.units
        return [
            {"field": "run", "value": start.get("run_id")},
            {"field": "project", "value": start.get("project")},
            {"field": "decision window", "value": _window(log, local, "decision")},
            {"field": "data window", "value": _window(log, local)},
            {"field": "time zone", "value": local.tz},
            {"field": "instruments", "value": ", ".join(log.instruments()) or DASH},
            {"field": "score unit", "value": units.score_label},
            {"field": "currency", "value": units.currency or "undeclared"},
            {"field": "events", "value": count(len(log))},
            {"field": "run status", "value": end.get("status") if end else "no run_end"},
            {"field": "bar stamps", "value": "ts_ms = the instant a mark was observed (UTC ms)"},
        ]

    @staticmethod
    def _verdict_rows(context):
        """Criterion rows with the observed value formatted as its statistic."""
        rows = []
        for verdict in context.scorecard.verdicts:
            row = verdict.to_obj()
            kind = STAT_FORMATS.get(row["stat"], "ratio")
            row["observed"] = context.format(kind, row["observed"])
            row["value"] = f"{row['value']:g}"
            rows.append(row)
        return rows

    def html(self, context):
        """Return card, verdict, criteria, statistics and census."""
        card = self.card(context)
        status = context.scorecard.status
        parts = [_html_table(("field", "value"), card, "card"),
                 f'<p class="verdict v-{status.lower()}">Verdict: <b>{status}</b></p>']
        verdicts = self._verdict_rows(context)
        if verdicts:
            parts.append(_html_table(("name", "stat", "op", "value", "min_n", "observed", "n",
                                      "status", "reason"), verdicts))
        else:
            parts.append(_note("No criteria were pre-registered in run_start."))
        parts.append("<h3>Statistics</h3>")
        parts.append(self._stats_html(context))
        parts.append("<h3>Census</h3>")
        parts.append(self._census_html(context.census))
        return "".join(parts)

    @staticmethod
    def _stat_rows(context):
        """Statistic rows, the value formatted, with an ``insufficient n`` flag."""
        return [{**row.to_obj(), "value": context.format(STAT_FORMATS.get(row.name, "ratio"),
                                                         row.value),
                 "flag": "" if row.sufficient else "insufficient n"}
                for row in context.table.rows]

    def _stats_html(self, context):
        """Render the statistics table; values right-aligned although they are text."""
        return _html_table(("name", "value", "n", "min_n", "flag", "note"),
                           self._stat_rows(context), "stats")

    @staticmethod
    def _census_rows(census):
        """Return the census as ``[{item, count}]``."""
        body = census.to_obj()
        rows = [{"item": "decisions", "count": body["decisions"]}]
        rows += [{"item": f"  action {a}", "count": n} for a, n in body["actions"].items()]
        rows += [{"item": name, "count": body[name]}
                 for name in ("orders", "fills", "refusals", "skips")]
        rows += [{"item": f"  orders {s}", "count": n} for s, n in body["order_states"].items()]
        rows.append({"item": "fills with no order", "count": body["unlinked_fills"]})
        return rows

    def _census_html(self, census):
        """Render the census table and its findings."""
        out = _html_table(("item", "count"), self._census_rows(census))
        if census.ok:
            return out + _note("Every decision and order is accounted for.")
        items = "".join(f"<li>{html.escape(p)}</li>" for p in census.problems)
        return out + f"<ul class=problems>{items}</ul>"

    def markdown(self, context):
        """Return the summary card, verdict, statistics, census and top reasons."""
        lines = ["## Run", ""]
        lines += _md_table(("field", "value"), self.card(context)) + [""]
        lines += [f"**Verdict: {context.scorecard.status}**", ""]
        verdicts = self._verdict_rows(context)
        if verdicts:
            lines += _md_table(("name", "stat", "op", "value", "min_n", "observed", "n",
                                "status", "reason"), verdicts) + [""]
        lines += ["## Statistics", ""]
        lines += _md_table(("name", "value", "n", "min_n", "flag"),
                           self._stat_rows(context)) + [""]
        lines += ["## Census", ""]
        lines += _md_table(("item", "count"), self._census_rows(context.census)) + [""]
        lines += [f"- {p}" for p in context.census.problems] or ["All accounted for."]
        lines += ["", "## Top refusal / skip reasons", ""]
        reasons = _top_reasons(context.log)
        lines += (_md_table(("kind", "reason", "count"), reasons) if reasons
                  else ["None recorded."])
        return lines + [""]


class EquitySection(Section):
    """Trading P&L (net and gross, deposits excluded), its drawdown, then account equity.

    The primary chart is trading P&L — equity minus every external cash
    flow — so a deposit can never look like profit. Account equity, which
    does include deposits, is drawn second with each cash flow marked and
    labelled by its amount and rule.

    Examples
    --------
    ::

        EquitySection().render(context)
    """

    title, anchor = "Trading P&L & equity", "equity"

    def html(self, context):
        """Return the P&L chart, the drawdown chart and the account-equity chart."""
        points = context.book.points
        if not points:
            return _note("No fills, marks or cash flows: nothing to value.")
        units, local = context.units, context.local
        pnl = LineChart(
            "Trading P&L (deposits and withdrawals excluded)",
            [Series("net of fees", tuple((p.ts_ms, float(p.trading_pnl)) for p in points), "s0"),
             Series("gross before costs",
                    tuple((p.ts_ms, float(p.gross_equity - p.external)) for p in points), "s1")],
            local=local, y_label="P&L", zero_line=True,
        )
        drawdown = context.book.drawdowns()["series"]
        under = LineChart(
            "Drawdown of trading P&L",
            [Series("drawdown", tuple((t, -d) for t, d in drawdown), "s2")],
            local=local, y_label="below peak", zero_line=True, height=180,
        )
        equity_at = {p.ts_ms: float(p.equity) for p in points}
        flows = [Marker(f.ts_ms, equity_at.get(f.ts_ms, 0.0), "dot", "open",
                        f"{local.stamp(f.ts_ms)}  external cash flow "
                        f"{units.money(f.get('amount'), signed=True)} "
                        f"({f.get('rule') or 'no rule'}) — not profit",
                        label=f"{units.money(f.get('amount'), signed=True)} "
                              f"{f.get('rule') or ''}".strip())
                 for f in context.log.of_kind("cashflow")]
        account = StepChart(
            "Account equity (includes deposits — not a performance measure)",
            [Series("equity", tuple((p.ts_ms, float(p.equity)) for p in points), "s3")],
            [MarkerLayer("◇ cash flow", flows)], local=local, y_label="equity", height=200,
        )
        return (pnl.render() + under.render()
                + _note("P&L and returns exclude external cash flows; a thin break with a date "
                        "marks each new session (market-closed time is collapsed).")
                + account.render())


class TradesOnPriceSection(Section):
    """Per instrument and session day: the price with entries, exits and rejections.

    Entries are ▲, exits ▼, coloured by their round trips' P&L (open trips
    grey); refusals and skips are hollow diamonds. Each marker's tooltip is
    the time, the action, the decision's reason, forecast, rank, edge and
    threshold, the price and the fee. Only instrument-days with a fill or
    a rejection get a panel; panels group per instrument in a collapsible
    block (the first open). A busy panel is thinned by :meth:`thin`.

    Examples
    --------
    ::

        TradesOnPriceSection().render(context)
    """

    title, anchor = "Trades on price", "trades"

    def html(self, context):
        """Return one chart per active (instrument, day), grouped per instrument."""
        fills = self._by_panel(context, context.log.of_kind("fill"), lambda e: e.instrument)
        rejected = self._by_panel(context, context.log.of_kind("refusal", "skip"),
                                  lambda e: self._instrument_of(context, e))
        keys = sorted(set(fills) | set(rejected))
        if not keys:
            return _note("No fills or refusals to draw.")
        prices = self._prices(context, set(keys))
        drawn, thinned, total = defaultdict(list), 0, 0
        for key in keys[:context.max_panels]:
            instrument, day = key
            panel_fills = fills.get(key, ())
            kept = self.thin(context, panel_fills)
            total += len(panel_fills)
            thinned += len(panel_fills) - len(kept)
            shown = (f" — {len(kept)} of {len(panel_fills)} fills drawn"
                     if len(kept) < len(panel_fills) else "")
            layers = [MarkerLayer("▲ entry  ▼ exit", [self._fill_marker(context, f) for f in kept]),
                      MarkerLayer("◇ refused/skipped",
                                  [self._rejection_marker(context, e, prices.get(key, ()))
                                   for e in rejected.get(key, ())])]
            chart = LineChart(f"{instrument} — {day}{shown}",
                              [Series(instrument, tuple(prices.get(key, ())), "s0")],
                              layers, local=context.local, y_label="price", height=220)
            drawn[instrument].append((day, chart, len(panel_fills), len(rejected.get(key, ()))))
        parts = []
        if thinned:
            parts.append(_note(
                f"{thinned} of {total} fill markers thinned (at most {context.max_markers} per "
                f"panel; every refusal/skip and the {_EXTREMES} largest wins and losses per "
                "panel are always drawn, the rest evenly spaced in time). Every fill is in "
                "trades.csv and events.jsonl."))
        for index, (instrument, panels) in enumerate(drawn.items()):
            n_fills = sum(p[2] for p in panels)
            n_rej = sum(p[3] for p in panels)
            summary = (f"{instrument} — {len(panels)} session(s), {count(n_fills)} fills, "
                       f"{count(n_rej)} refused/skipped")
            body = "".join(chart.render() for _day, chart, _f, _r in panels)
            parts.append(f"<details{' open' if index == 0 else ''}><summary>"
                         f"{html.escape(summary)}</summary>{body}</details>")
        if len(keys) > context.max_panels:
            parts.append(_note(f"{len(keys) - context.max_panels} more instrument-day panel(s) "
                               f"not drawn (limit {context.max_panels}); every trade is in "
                               "trades.csv and every event in events.jsonl."))
        placed = sum(len(v) for v in rejected.values())
        unplaced = len(context.log.of_kind("refusal", "skip")) - placed
        if unplaced:
            parts.append(_note(f"{unplaced} refusal/skip event(s) name no instrument, so no "
                               "price panel can show them; they are in the decision log."))
        return "".join(parts)

    @staticmethod
    def thin(context, fills):
        """Return the fills a panel draws, in time order, at most ``context.max_markers``.

        Deterministic: open-position fills and the fills of the
        ``_EXTREMES`` largest winning and losing round trips are always
        kept; the remaining budget is filled evenly through time.

        Parameters
        ----------
        context : ReportContext
        fills : sequence of Fill

        Returns
        -------
        list of Fill
        """
        fills = list(fills)
        if len(fills) <= context.max_markers:
            return fills
        book = context.book

        def pnl(fill):
            return sum(trip.pnl for trip in book.trips_of_fill.get(fill.get("fill_id"), ()))

        keep = {id(f) for f in fills if not book.trips_of_fill.get(f.get("fill_id"))}
        ranked = sorted(fills, key=lambda f: (pnl(f), f.seq))
        keep |= {id(f) for f in ranked[:_EXTREMES] if pnl(f) < 0}
        keep |= {id(f) for f in ranked[-_EXTREMES:] if pnl(f) > 0}
        rest = [f for f in fills if id(f) not in keep]
        keep |= {id(f) for f in _even(rest, max(0, context.max_markers - len(keep)))}
        return [f for f in fills if id(f) in keep]

    @staticmethod
    def _prices(context, keys):
        """Mark points per (instrument, day), only for the panels that will be drawn."""
        out = defaultdict(list)
        for mark in context.log.of_kind("mark"):
            key = (mark.instrument, context.local.day(mark.ts_ms))
            if key in keys:
                out[key].append((mark.ts_ms, float(mark.get("price"))))
        return out

    @staticmethod
    def _by_panel(context, events, instrument_of):
        """Events per (instrument, day), dropping those with no instrument."""
        out = defaultdict(list)
        for event in events:
            instrument = instrument_of(event)
            if instrument is not None:
                out[(instrument, context.local.day(event.ts_ms))].append(event)
        return out

    @staticmethod
    def _instrument_of(context, rejection):
        """Return a rejection's instrument: its own, else its order's, else the decision's pick."""
        if rejection.instrument is not None:
            return rejection.instrument
        order = context.links.orders.get(rejection.get("order_id"))
        if order is not None:
            return order.instrument
        decision = context.links.decision_of_rejection(rejection)
        return None if decision is None else decision.get("chosen")

    @staticmethod
    def _fill_marker(context, fill):
        """Build an entry ▲ or exit ▼ marker coloured by its trips' P&L, with the full "why"."""
        book, links, units = context.book, context.links, context.units
        fill_id = fill.get("fill_id")
        role = book.role_of_fill.get(fill_id, "entry")
        trips = book.trips_of_fill.get(fill_id, ())
        pnl = sum(trip.pnl for trip in trips)
        css = "open" if not trips or pnl == 0 else ("win" if pnl > 0 else "loss")
        slip = fill.slippage_bp(links.ref_price(fill))
        title = "\n".join([
            f"{context.local.stamp(fill.ts_ms)}  {role.upper()} {fill.get('side')} "
            f"{display(None, fill.get('qty'), units)} @ {units.money(fill.get('price'))}",
            _why(links.decision_of_fill(fill), units),
            f"fee {units.money(fill.fee)}; slippage {display('bp', slip, units)}",
            f"round-trip P&L {units.money(pnl, signed=True) if trips else 'open'}",
        ])
        shape = "down" if role == "exit" else "up"
        return Marker(fill.ts_ms, float(fill.get("price")), shape, css, title)

    @staticmethod
    def _rejection_marker(context, event, prices):
        """Build a hollow diamond at the last price at or before the rejection."""
        times = [t for t, _ in prices]
        index = bisect.bisect_right(times, event.ts_ms) - 1
        order = context.links.orders.get(event.get("order_id"))
        if index >= 0:
            price = prices[index][1]
        elif order is not None and order.get("ref_price") is not None:
            price = float(order.get("ref_price"))
        else:
            price = prices[0][1] if prices else 0.0
        decision = context.links.decision_of_rejection(event)
        title = "\n".join([
            f"{context.local.stamp(event.ts_ms)}  {event.kind.upper()}: {event.get('reason')}",
            event.get("detail", "") or DASH,
            _why(decision, context.units),
            f"price {context.units.money(price)}",
        ])
        return Marker(event.ts_ms, price, "dot", "rej", title, hollow=True)


class DecisionLogSection(Section):
    """What was decided, in three depths: by reason, the consequential few, then all.

    First a summary per (action, reason) — how many, on which names, the
    mean forecast and the P&L of the round trips those decisions opened or
    closed. Then the most consequential decisions: the largest losing and
    winning entries and an even sample of refusals and skips. Then every
    decision in a collapsed table (``decisions.csv`` has every column raw).

    Examples
    --------
    ::

        rows = DecisionLogSection().rows(context)
    """

    title, anchor = "Decision log", "decisions"

    #: The compact columns of the in-page tables, and how each displays.
    COLUMNS = ("time", "action", "chosen", "chosen_score", "chosen_rank", "edge", "reason",
               "fill_price", "fees", "trip_pnl", "rejections", "guard_verdict")
    FORMATS = {"chosen_score": "score", "edge": "edge", "fill_price": "money", "fees": "money",
               "trip_pnl": "pnl", "chosen_rank": "count"}

    def rows(self, context):
        """Return one row per decision, in time order, over :data:`DECISION_COLUMNS`.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        list of dict
        """
        links = context.links
        trips = _trips_by_decision(context.book)
        unit = context.units.score_label
        decisions = sorted(context.log.of_kind("decision"), key=lambda d: (d.ts_ms, d.seq))
        return [self._row(context, links, trips, unit, decision) for decision in decisions]

    @staticmethod
    def _row(context, links, trips, unit, decision):
        """One decision's row: candidates, choice, action, and its linked execution."""
        decision_id = decision.get("decision_id")
        chosen, runner = decision.chosen_row() or {}, decision.runner_up() or {}
        orders = links.orders_of.get(decision_id, ())
        fills = links.fills_of_decision(decision_id)
        qty = sum(f.get("qty") for f in fills)
        slips = [(f.get("qty"), f.slippage_bp(links.ref_price(f))) for f in fills]
        slips = [(q, s) for q, s in slips if s is not None]
        slip_qty = sum(q for q, _ in slips)
        linked = trips.get(decision_id)
        return {
            "time": context.local.stamp(decision.ts_ms),
            "ts_ms": decision.ts_ms,
            "decision_id": decision_id,
            "candidates": len(decision.candidates),
            "chosen": decision.get("chosen"),
            "chosen_score": chosen.get("score"),
            "chosen_rank": chosen.get("rank"),
            "runner_up": runner.get("instrument"),
            "runner_up_score": runner.get("score"),
            "threshold": decision.get("threshold"),
            "edge": decision.get("edge"),
            "score_unit": unit,
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "detail": decision.get("detail"),
            "model": decision.get("model"),
            "orders": len(orders),
            "ref_price": orders[0].get("ref_price") if orders else None,
            "fill_price": (sum(f.get("qty") * f.get("price") for f in fills) / qty
                           if qty else None),
            "filled_qty": qty if fills else None,
            "slippage_bp": sum(q * s for q, s in slips) / slip_qty if slip_qty else None,
            "fees": sum(f.fee for f in fills) if fills else None,
            "trip_pnl": sum(t.pnl for t in linked) if linked else None,
            "rejections": "; ".join(f"{r.kind}:{r.get('reason')}"
                                    for r in links.rejections_of(decision_id)) or None,
            "guard_verdict": _worst_verdict(orders),
            "findings": "; ".join(_finding_text(f) for o in orders
                                  for f in o.get("findings") or ()) or None,
        }

    @staticmethod
    def summary(rows):
        """Group decisions by (action, reason): count, names, mean forecast, trip P&L.

        Parameters
        ----------
        rows : list of dict
            From :meth:`rows`.

        Returns
        -------
        list of dict
            ``action``, ``reason``, ``count``, ``instruments``,
            ``mean_forecast``, ``trip_pnl`` (sum over the trips those
            decisions opened or closed), most frequent first.
        """
        groups = defaultdict(list)
        for row in rows:
            groups[(row["action"], row["reason"])].append(row)
        out = []
        for (action, reason), group in groups.items():
            scores = [r["chosen_score"] for r in group if r["chosen_score"] is not None]
            pnls = [r["trip_pnl"] for r in group if r["trip_pnl"] is not None]
            names = Counter(r["chosen"] for r in group if r["chosen"])
            out.append({
                "action": action, "reason": reason, "count": len(group),
                "instruments": ", ".join(f"{n} {c}" for n, c in sorted(
                    names.items(), key=lambda kv: (-kv[1], kv[0]))[:5]) or DASH,
                "mean_forecast": sum(scores) / len(scores) if scores else None,
                "trip_pnl": sum(pnls) if pnls else None,
            })
        return sorted(out, key=lambda r: (-r["count"], r["action"], str(r["reason"])))

    @staticmethod
    def consequential(rows, limit=_SAMPLE):
        """Return the largest losing and winning entries and a sample of rejections.

        Parameters
        ----------
        rows : list of dict
        limit : int
            Per list.

        Returns
        -------
        list of (str, list of dict, int)
            ``(heading, rows, out_of)`` per list.
        """
        entries = [r for r in rows if r["action"] == "enter" and r["trip_pnl"] is not None]
        losses = sorted((r for r in entries if r["trip_pnl"] < 0),
                        key=lambda r: (r["trip_pnl"], r["ts_ms"]))
        wins = sorted((r for r in entries if r["trip_pnl"] > 0),
                      key=lambda r: (-r["trip_pnl"], r["ts_ms"]))
        rejected = [r for r in rows if r["action"] in ("refuse", "skip")]
        return [
            ("Largest losing entries", losses[:limit], len(losses)),
            ("Largest winning entries", wins[:limit], len(wins)),
            ("Refused or skipped (evenly spaced in time)", _even(rejected, limit), len(rejected)),
        ]

    def html(self, context):
        """Return the summary, the consequential decisions and the full log in ``<details>``."""
        rows = self.rows(context)
        if not rows:
            return _note("No decision events.")
        units = context.units
        parts = ["<h3>Decisions by action and reason</h3>",
                 _html_table(("action", "reason", "count", "instruments", "mean_forecast",
                              "trip_pnl"), self.summary(rows),
                             formats={"mean_forecast": "score", "trip_pnl": "pnl"}, units=units),
                 _note("trip_pnl sums the round trips those decisions opened (enter) or closed "
                       "(exit), so the enter and exit rows each total the run's round-trip P&L."),
                 "<h3>Most consequential decisions</h3>"]
        for heading, sample, out_of in self.consequential(rows):
            if not sample:
                continue
            parts.append(f"<h4>{html.escape(heading)} — {len(sample)} of {count(out_of)}</h4>")
            parts.append(_html_table(self.COLUMNS, sample, "decisions", self.FORMATS, units))
        parts.append("<h3>Commonest refusal / skip reasons</h3>")
        parts.append(_reasons_table(context.log))
        parts.append("<h3>Guard findings (pre-trade checks from the ledger)</h3>")
        parts.append(self._findings_table(context))
        parts.append(f"<details><summary>All {count(len(rows))} decisions (every column raw in "
                     "decisions.csv)</summary>" + self._compact(context, rows) + "</details>")
        return "".join(parts)

    @staticmethod
    def findings_summary(log):
        """Group every order's guard findings by (guard, measure, verdict).

        Parameters
        ----------
        log : EventLog

        Returns
        -------
        list of dict
            ``guard``, ``measure``, ``verdict``, ``count``, ``min_value``,
            ``max_value``, ``bound`` (the tightest one seen) — commonest first.
        """
        groups = defaultdict(list)
        for order in log.of_kind("order"):
            for finding in order.get("findings") or ():
                groups[(finding["guard"], finding["measure"], finding["verdict"])].append(finding)
        out = []
        for (guard, measure, verdict), group in groups.items():
            values = [f["value"] for f in group if f.get("value") is not None]
            bounds = [f["bound"] for f in group if f.get("bound") is not None]
            out.append({"guard": guard, "measure": measure, "verdict": verdict,
                        "count": len(group), "min_value": min(values) if values else None,
                        "max_value": max(values) if values else None,
                        "bound": min(bounds) if bounds else None})
        return sorted(out, key=lambda r: (-r["count"], r["guard"], r["measure"], r["verdict"]))

    def _findings_table(self, context):
        """Render the findings summary, or say why there is none."""
        rows = self.findings_summary(context.log)
        if not rows:
            return _note("No guard findings: the producer recorded no pre-trade checks "
                         "(no guards declared, or no ledger mapped into the log).")
        return _html_table(("guard", "measure", "verdict", "count", "min_value", "max_value",
                            "bound"), rows, units=context.units)

    def _compact(self, context, rows):
        """Render the full log as a light table: formatted cells, no per-cell attributes."""
        units = context.units
        head = "".join(f"<th>{html.escape(c)}</th>" for c in self.COLUMNS)
        body = []
        for row in rows:
            cells = []
            for column in self.COLUMNS:
                value = row[column]
                if column == "time":
                    value = value[5:]
                cells.append(html.escape(display(self.FORMATS.get(column), value, units)))
            body.append("<tr><td>" + "<td>".join(cells))
        return (f"<div class=scroll><table class=log><thead><tr>{head}</tr></thead><tbody>"
                + "\n".join(body) + "</tbody></table></div>")


def _reasons_table(log):
    """Render the top refusal/skip reasons, or say there were none."""
    reasons = _top_reasons(log)
    return _html_table(("kind", "reason", "count"), reasons) if reasons else _note("None.")


class CashSection(Section):
    """Cash balance with every external flow marked, gross exposure, and the flow table.

    Examples
    --------
    ::

        CashSection().render(context)
    """

    title, anchor = "Cash & exposure", "cash"

    def html(self, context):
        """Return the cash steps with flow markers, exposure, and the flow table."""
        points = context.book.points
        if not points:
            return _note("Nothing to value.")
        units, local = context.units, context.local
        flows = context.log.of_kind("cashflow")
        cash_at = {p.ts_ms: float(p.cash) for p in points}
        markers = [Marker(f.ts_ms, cash_at.get(f.ts_ms, 0.0), "dot", "open",
                          f"{local.stamp(f.ts_ms)}  cash flow "
                          f"{units.money(f.get('amount'), signed=True)} "
                          f"({f.get('rule') or 'no rule'})",
                          label=units.money(f.get("amount"), signed=True))
                   for f in flows]
        cash = StepChart("Cash balance (deposits in, purchases and fees out)",
                         [Series("cash", tuple((p.ts_ms, float(p.cash)) for p in points), "s0")],
                         [MarkerLayer("◇ cash flow", markers)], local=local,
                         y_label="cash", height=200)
        exposure = StepChart("Gross exposure (gross notional / equity)",
                             [Series("exposure", tuple((p.ts_ms, p.exposure) for p in points),
                                     "s2")],
                             local=local, y_label="× equity", height=180)
        rows = [{"time": local.stamp(f.ts_ms), "amount": f.get("amount"),
                 "rule": f.get("rule"), "detail": f.get("detail")} for f in flows]
        table = (_html_table(("time", "amount", "rule", "detail"), rows,
                             formats={"amount": "pnl"}, units=units) if rows
                 else _note("No external cash flows."))
        return cash.render() + exposure.render() + "<h3>Cash flows</h3>" + table


class DistributionSection(Section):
    """Trade P&L, holding time and slippage distributions, and P&L attribution.

    Examples
    --------
    ::

        DistributionSection().render(context)
    """

    title, anchor = "Distributions & attribution", "distributions"

    def html(self, context):
        """Return three histograms and the by-instrument / by-reason tables."""
        trips = context.book.round_trips
        links = context.links
        slips = [s for f in context.log.of_kind("fill")
                 if (s := f.slippage_bp(links.ref_price(f))) is not None]
        units = context.units
        parts = [
            Histogram("Round-trip P&L (net of fees)", [t.pnl for t in trips]).render(),
            Histogram("Holding time", [t.holding_ms / 60_000 for t in trips],
                      unit="m").render(),
            Histogram("Slippage vs reference", slips, unit="bp").render(),
        ]
        parts.append("<h3>P&amp;L by instrument</h3>")
        parts.append(self._attribution(trips, lambda t: t.instrument, "instrument", units))
        parts.append("<h3>P&amp;L by entry reason</h3>")
        parts.append(self._attribution(trips, lambda t: t.entry_reason, "entry_reason", units))
        return "".join(parts)

    @staticmethod
    def _attribution(trips, key_of, name, units):
        """Tabulate trips, hit rate, gross, fees and net P&L grouped by ``key_of``."""
        groups = defaultdict(list)
        for trip in trips:
            groups[key_of(trip)].append(trip)
        rows = [{name: key, "trips": len(group),
                 "won": sum(1 for t in group if t.pnl > 0) / len(group),
                 "gross_pnl": sum(t.gross_pnl for t in group),
                 "fees": sum(t.fees for t in group), "pnl": sum(t.pnl for t in group)}
                for key, group in sorted(groups.items(), key=lambda kv: str(kv[0]))]
        if not rows:
            return _note("No closed round trips.")
        return _html_table((name, "trips", "won", "gross_pnl", "fees", "pnl"), rows,
                           formats={"won": "percent", "gross_pnl": "pnl", "fees": "money",
                                    "pnl": "pnl"}, units=units)


class InferenceSection(Section):
    """What the forecasts turned into: calibration, hit rate and rank IC.

    The ONE section that reads ``outcome`` events, through
    :class:`~dskit.evaluation.diagnostics.ForecastDiagnostics`; it shows
    forecast/outcome aggregates, never a decision row, so the decision log
    stays free of look-ahead.

    Examples
    --------
    ::

        InferenceSection().render(context)
    """

    title, anchor = "Inference diagnostics", "inference"

    def html(self, context):
        """Return the headline, the decile table and charts, and rank IC over time."""
        diagnostics = context.diagnostics
        if not diagnostics.pairs:
            return _note(self._missing(diagnostics))
        return (self._headline(context) + self._calibration(context)
                + self._rank_ic(context))

    @staticmethod
    def _missing(diagnostics):
        """Say why there is nothing to diagnose."""
        return (f"No forecast could be scored: {diagnostics.unresolved} scored candidate(s) "
                "have no outcome event. A producer logs one outcome per candidate "
                "(decision_id, instrument, realized) once the target is known.")

    @staticmethod
    def _headline(context):
        """Return pair counts, hit rates, the pooled rank IC and the calibration slope."""
        diagnostics = context.diagnostics
        summary = diagnostics.rank_ic_summary() or {}
        slope = diagnostics.slope() or {}
        rows = [
            {"measure": "scored pairs", "value": count(len(diagnostics.pairs))},
            {"measure": "candidates without an outcome", "value": count(diagnostics.unresolved)},
            {"measure": "hit rate, all candidates", "value": percent(diagnostics.hit_rate())},
            {"measure": "hit rate, chosen only",
             "value": percent(diagnostics.hit_rate(chosen_only=True))},
            {"measure": "mean rank IC per instant (HAC t)",
             "value": f"{ratio(summary.get('ic'), signed=True)} "
                      f"(t {ratio(summary.get('ic_t'), signed=True)}, "
                      f"{count(summary.get('n_stamps'))} instants)"},
            {"measure": "calibration slope (t vs 0 / vs 1)",
             "value": f"{ratio(slope.get('slope'), signed=True)} "
                      f"(t {ratio(slope.get('t_vs_0'), signed=True)} / "
                      f"{ratio(slope.get('t_vs_1'), signed=True)})"},
        ]
        note = ""
        if summary and not summary.get("usable"):
            note = _note(f"Rank IC is not evidence here: {summary.get('unusable_reason')}")
        return _html_table(("measure", "value"), rows, css="stats") + note

    @staticmethod
    def _calibration(context):
        """Return the calibration chart, the hit-rate bars and the decile table."""
        buckets = context.diagnostics.calibration()
        if not buckets:
            return _note("Calibration needs at least two scored forecasts.")
        unit = context.units.score_unit
        points = [(b.mean_score * unit.scale, b.mean_realized * unit.scale) for b in buckets]
        lo = min(min(p) for p in points)
        hi = max(max(p) for p in points)
        chart = LineChart(
            f"Calibration: mean realised vs mean forecast per bucket ({unit.suffix or 'raw'})",
            [Series("realised", tuple(points), "s0"),
             Series("perfect calibration", ((lo, lo), (hi, hi)), "s2")],
            y_label=unit.suffix, gap_ms=False, zero_line=True)
        hits = BarChart("Hit rate by forecast bucket (lowest forecasts first)",
                        [f"B{b.index + 1}" for b in buckets], [b.hit_rate for b in buckets],
                        y_label="hit rate")
        rows = [{"bucket": f"B{b.index + 1}", "n": b.n, "score_lo": b.score_lo,
                 "score_hi": b.score_hi, "mean_score": b.mean_score,
                 "mean_realized": b.mean_realized, "hit_rate": b.hit_rate}
                for b in buckets]
        table = _html_table(
            ("bucket", "n", "score_lo", "score_hi", "mean_score", "mean_realized", "hit_rate"),
            rows, formats={"score_lo": "score", "score_hi": "score", "mean_score": "score",
                           "mean_realized": "edge", "hit_rate": "percent"},
            units=context.units)
        return "<h3>Calibration by forecast bucket</h3>" + chart.render() + hits.render() + table

    @staticmethod
    def _rank_ic(context):
        """Return rank IC per instant and per day."""
        diagnostics = context.diagnostics
        cs = diagnostics.rank_ic()
        if not cs["rho"]:
            return _note(f"No instant had enough names to rank ({cs['n_skipped']} skipped).")
        line = LineChart("Rank IC per decision instant",
                         [Series("rank IC", tuple(zip(cs["stamps"], cs["rho"])), "s0")],
                         local=context.local, y_label="Spearman", zero_line=True)
        days = diagnostics.rank_ic_by_day(context.local)
        bars = BarChart("Mean rank IC per day", [d[5:] for d, _, _ in days],
                        [rho for _, rho, _ in days], y_label="Spearman")
        rows = [{"day": d, "mean_rank_ic": rho, "instants": n} for d, rho, n in days]
        note = (_note(f"{cs['n_skipped']} instant(s) had too few names to rank.")
                if cs["n_skipped"] else "")
        return ("<h3>Rank IC over time</h3>" + line.render() + bars.render()
                + _html_table(("day", "mean_rank_ic", "instants"), rows,
                              formats={"mean_rank_ic": "ratio"}, units=context.units) + note)

    def markdown(self, context):
        """Return one line: pairs, hit rate and the pooled rank IC."""
        diagnostics = context.diagnostics
        if not diagnostics.pairs:
            return ["", f"**Inference:** {self._missing(diagnostics)}"]
        summary = diagnostics.rank_ic_summary() or {}
        return ["", f"**Inference:** {count(len(diagnostics.pairs))} scored forecasts; hit rate "
                    f"{percent(diagnostics.hit_rate())} (chosen "
                    f"{percent(diagnostics.hit_rate(chosen_only=True))}); mean rank IC "
                    f"{ratio(summary.get('ic'), signed=True)} "
                    f"(t {ratio(summary.get('ic_t'), signed=True)})."]


class PeriodSection(Section):
    """Per session day: P&L, fees, flows, return, fills and trips.

    Examples
    --------
    ::

        PeriodSection().render(context)
    """

    title, anchor = "Per day", "periods"

    def html(self, context):
        """Return the daily P&L bars and the per-day table."""
        days = [row.to_obj() for row in context.book.days()]
        if not days:
            return _note("No activity.")
        bars = BarChart("Trading P&L per day", [d["day"][5:] for d in days],
                        [d["pnl"] for d in days], y_label="P&L")
        return bars.render() + _html_table(
            ("day", "pnl", "gross_pnl", "fees", "flows", "end_equity", "ret", "fills", "trips"),
            days,
            formats={"pnl": "pnl", "gross_pnl": "pnl", "fees": "money", "flows": "pnl",
                     "end_equity": "money", "ret": "percent"},
            units=context.units,
        )


class ProvenanceSection(Section):
    """Where the run came from: ids, config, code, data, environment, event counts.

    A fact the report node filled (rather than the producer) says so in
    its ``source`` column, from ``run_start.sources``.

    Examples
    --------
    ::

        ProvenanceSection().render(context)
    """

    title, anchor = "Provenance", "provenance"

    def rows(self, context):
        """Return the provenance facts as ``[{field, value, source}]``.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        list of dict
        """
        start, end = context.log.run_start, context.log.run_end
        code, env = start.get("code", {}), start.get("env", {})
        sources = start.get("sources", {})
        wall = end.get("wall_s") if end else None
        return [
            {"field": "schema", "value": start.to_obj()["schema"]},
            {"field": "run_id", "value": start.get("run_id")},
            {"field": "config_hash", "value": start.get("config_hash")},
            {"field": "code commit", "value": code.get("commit"), "source": sources.get("code")},
            {"field": "code dirty", "value": code.get("dirty"), "source": sources.get("code")},
            {"field": "trials", "value": start.get("trials", 1)},
            {"field": "python", "value": env.get("python"), "source": sources.get("env")},
            {"field": "platform", "value": env.get("platform"), "source": sources.get("env")},
            {"field": "packages", "value": len(env.get("packages", {})),
             "source": sources.get("env")},
            {"field": "wall time", "value": None if wall is None else f"{sig(wall, 4)} s",
             "source": sources.get("wall_s")},
            {"field": "events", "value": ", ".join(
                f"{k}={n}" for k, n in sorted(context.census.kinds.items()))},
        ]

    def html(self, context):
        """Return the facts, data fingerprints, packages and the config."""
        start = context.log.run_start
        data = [{"name": k, "fingerprint": v} for k, v in sorted(start.get("data", {}).items())]
        packages = [{"package": k, "version": v}
                    for k, v in sorted(start.get("env", {}).get("packages", {}).items())]
        config = json.dumps(start.get("config", {}), indent=2, sort_keys=True)
        return (_html_table(("field", "value", "source"), self.rows(context))
                + "<h3>Data</h3>"
                + (_html_table(("name", "fingerprint"), data) if data
                   else _note("No data fingerprints recorded."))
                + f"<details><summary>{len(packages)} package(s)</summary>"
                + _html_table(("package", "version"), packages) + "</details>"
                + "<details><summary>Config</summary><pre>"
                + html.escape(config) + "</pre></details>")

    def markdown(self, context):
        """Return the provenance table."""
        return ["## Provenance", "",
                *_md_table(("field", "value", "source"), self.rows(context)), ""]


#: The default section order — the reader's order.
DEFAULT_SECTIONS = (OverviewSection, SummarySection, EquitySection, TradesOnPriceSection,
                    DecisionLogSection, CashSection, DistributionSection, InferenceSection,
                    PeriodSection, ProvenanceSection)
