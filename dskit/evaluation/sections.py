"""The report's sections: one class per section, each a pure function of the context.

A :class:`Section` renders its HTML body from a :class:`ReportContext` (the
log, the book, the statistics table, the scorecard and the census, built
once) and may contribute lines to ``summary.md``. The default order is the
research spec's reading order (ADR-0183 item 7): the summary card and
verdict first, then equity, trades on price, the decision log, cash and
exposure, distributions, per-day stability, and provenance last.

Two rules every section keeps. **No look-ahead in the render**: a section
that shows a decision reads only what the decision, its orders and its
fills recorded — never an ``outcome`` event. **Nothing is silently
dropped**: where a render is capped (trade panels), the section says how
many were cut and where the full record lives (``trades.csv``,
``decisions.csv``, ``events.jsonl``).

HTML is escaped with ``html.escape``; markdown cells go through
:func:`dskit.pipeline.runs.render_cell`, the one owner of the pipe rule.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

import bisect
import html
import json
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass

from dskit.evaluation.svg import (
    BarChart,
    Histogram,
    LineChart,
    Marker,
    MarkerLayer,
    Series,
    StepChart,
)
from dskit.pipeline.runs import render_cell

__all__ = [
    "DEFAULT_MAX_PANELS",
    "DEFAULT_SECTIONS",
    "DECISION_COLUMNS",
    "CashSection",
    "DecisionLogSection",
    "DistributionSection",
    "EquitySection",
    "PeriodSection",
    "ProvenanceSection",
    "ReportContext",
    "Section",
    "SummarySection",
    "TradesOnPriceSection",
]

#: The most trades-on-price panels a report draws; the rest are named.
DEFAULT_MAX_PANELS = 24

#: How many reasons a "top reasons" table lists.
_TOP_REASONS = 10

#: The decision table's columns, in order (``decisions.csv`` too).
DECISION_COLUMNS = (
    "time", "ts_ms", "decision_id", "candidates", "chosen", "chosen_score", "chosen_rank",
    "runner_up", "runner_up_score", "threshold", "edge", "action", "reason", "detail",
    "model", "orders", "ref_price", "fill_price", "filled_qty", "slippage_bp", "fees",
    "rejections",
)

_DASH = "—"


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

    @property
    def local(self):
        """The run's :class:`~dskit.evaluation.events.LocalTime`."""
        return self.log.local_time

    @property
    def links(self):
        """The log's id graph."""
        return self.log.links


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _display(value):
    """Return a cell's HTML text before escaping: dashes for absence, grouped money."""
    if value is None:
        return _DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        value = value + 0.0  # -0.0 reads as 0
        return f"{value:,.2f}" if abs(value) >= 1e5 else f"{value:.6g}"
    return str(value)


def _html_table(columns, rows, css=""):
    """Render an escaped HTML table; numbers right-aligned."""
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body = []
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            cells.append(f'<td{" class=num" if numeric else ""}>'
                         f"{html.escape(_display(value))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    klass = f' class="{css}"' if css else ""
    return (f"<div class=scroll><table{klass}><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _md_table(columns, rows):
    """Render a markdown table, every cell through ``render_cell``."""
    lines = ["| " + " | ".join(render_cell(c) for c in columns) + " |",
             "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(render_cell(row.get(c)) for c in columns) + " |"
              for row in rows]
    return lines


def _note(text):
    """Render a muted paragraph."""
    return f"<p class=note>{html.escape(text)}</p>"


def _top_reasons(log, limit=_TOP_REASONS):
    """Count the commonest refusal/skip reasons: ``[{kind, reason, count}]``."""
    counts = Counter((e.kind, e.get("reason")) for e in log.of_kind("refusal", "skip"))
    return [{"kind": kind, "reason": reason, "count": count}
            for (kind, reason), count in counts.most_common(limit)]


def _why(decision):
    """One-line account of a decision: reason, chosen forecast/rank, edge vs threshold."""
    if decision is None:
        return "no linked decision"
    parts = [f"reason {decision.get('reason')}"]
    row = decision.chosen_row()
    if row is not None:
        parts.append(f"forecast {_display(row.get('score'))} rank "
                     f"{_display(row.get('rank'))}/{len(decision.candidates)}")
    if decision.get("edge") is not None:
        parts.append(f"edge {_display(decision.get('edge'))}")
    if decision.get("threshold") is not None:
        parts.append(f"thr {_display(decision.get('threshold'))}")
    if decision.get("detail"):
        parts.append(decision.get("detail"))
    return "; ".join(parts)


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
        events = log.events
        inner = [e for e in events if e.kind not in ("run_start", "run_end")] or events
        return [
            {"field": "run", "value": start.get("run_id")},
            {"field": "project", "value": start.get("project")},
            {"field": "window", "value": f"{local.stamp(inner[0].ts_ms)} → "
                                         f"{local.stamp(inner[-1].ts_ms)} ({local.tz})"},
            {"field": "instruments", "value": ", ".join(log.instruments()) or _DASH},
            {"field": "events", "value": len(events)},
            {"field": "run status", "value": end.get("status") if end else "no run_end"},
            {"field": "bar stamps", "value": "ts_ms = the instant a mark was observed (UTC ms)"},
        ]

    def html(self, context):
        """Return card, verdict, criteria, statistics and census."""
        card = self.card(context)
        status = context.scorecard.status
        parts = [_html_table(("field", "value"), card, "card"),
                 f'<p class="verdict v-{status.lower()}">Verdict: <b>{status}</b></p>']
        verdicts = [v.to_obj() for v in context.scorecard.verdicts]
        if verdicts:
            parts.append(_html_table(("name", "stat", "op", "value", "min_n", "observed", "n",
                                      "status", "reason"), verdicts))
        else:
            parts.append(_note("No criteria were pre-registered in run_start."))
        parts.append("<h3>Statistics</h3>")
        parts.append(_html_table(("name", "value", "n", "min_n", "flag", "note"),
                                 self._stat_rows(context)))
        parts.append("<h3>Census</h3>")
        parts.append(self._census_html(context.census))
        return "".join(parts)

    @staticmethod
    def _stat_rows(context):
        """Statistic rows with an ``insufficient n`` flag."""
        return [{**row.to_obj(), "flag": "" if row.sufficient else "insufficient n"}
                for row in context.table.rows]

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
        lines = [f"# {render_cell(context.title)}", ""]
        lines += _md_table(("field", "value"), self.card(context)) + [""]
        lines += [f"**Verdict: {context.scorecard.status}**", ""]
        verdicts = [v.to_obj() for v in context.scorecard.verdicts]
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
    """Net and gross-before-costs equity with session boundaries, and the drawdown.

    Examples
    --------
    ::

        EquitySection().render(context)
    """

    title, anchor = "Equity & P&L", "equity"

    def html(self, context):
        """Return the equity chart and the drawdown chart."""
        points = context.book.points
        if not points:
            return _note("No fills, marks or cash flows: nothing to value.")
        boundaries = _day_boundaries(points, context.local)
        equity = LineChart(
            "Equity (net of fees) vs gross before costs",
            [Series("net", tuple((p.ts_ms, float(p.equity)) for p in points), "s0"),
             Series("gross", tuple((p.ts_ms, float(p.gross_equity)) for p in points), "s1")],
            local=context.local, y_label="equity", boundaries=boundaries,
        )
        drawdown = context.book.drawdowns()["series"]
        under = LineChart(
            "Drawdown of trading P&L",
            [Series("drawdown", tuple((t, -d) for t, d in drawdown), "s2")],
            local=context.local, y_label="below peak", boundaries=boundaries, zero_line=True,
            height=180,
        )
        return (equity.render() + under.render()
                + _note("Equity includes external cash flows (see Cash); P&L and returns "
                        "exclude them. Dashed lines mark session-day changes."))


def _day_boundaries(points, local):
    """Return the first instant of each local day after the first."""
    out, last = [], None
    for point in points:
        day = local.day(point.ts_ms)
        if last is not None and day != last:
            out.append(point.ts_ms)
        last = day
    return out


class TradesOnPriceSection(Section):
    """Per instrument and session day: the price with every entry, exit and rejection.

    Entries are ▲, exits ▼, coloured by their round trips' P&L (open trips
    grey); refusals and skips are hollow diamonds. Each marker's tooltip is
    the time, the action, the decision's reason, forecast, rank, edge and
    threshold, the price and the fee.

    Examples
    --------
    ::

        TradesOnPriceSection().render(context)
    """

    title, anchor = "Trades on price", "trades"

    def html(self, context):
        """Return one chart per (instrument, day), capped at the context's limit."""
        prices = self._prices(context)
        fills = self._by_panel(context, context.log.of_kind("fill"), lambda e: e.instrument)
        rejected = self._by_panel(context, context.log.of_kind("refusal", "skip"),
                                  lambda e: self._instrument_of(context, e))
        keys = sorted(set(prices) | set(fills) | set(rejected))
        if not keys:
            return _note("No marks or fills to draw.")
        parts = []
        for key in keys[:context.max_panels]:
            instrument, day = key
            layers = [MarkerLayer("▲ entry  ▼ exit",
                                  [self._fill_marker(context, f) for f in fills.get(key, ())]),
                      MarkerLayer("◇ refused/skipped",
                                  [self._rejection_marker(context, e, prices.get(key, ()))
                                   for e in rejected.get(key, ())])]
            chart = LineChart(f"{instrument} — {day}",
                              [Series(instrument, tuple(prices.get(key, ())), "s0")],
                              layers, local=context.local, y_label="price", height=220)
            parts.append(chart.render())
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
    def _prices(context):
        """Mark points per (instrument, day)."""
        out = defaultdict(list)
        for mark in context.log.of_kind("mark"):
            out[(mark.instrument, context.local.day(mark.ts_ms))].append(
                (mark.ts_ms, float(mark.get("price"))))
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
        book, links = context.book, context.links
        fill_id = fill.get("fill_id")
        role = book.role_of_fill.get(fill_id, "entry")
        trips = book.trips_of_fill.get(fill_id, ())
        pnl = sum(trip.pnl for trip in trips)
        css = "open" if not trips or pnl == 0 else ("win" if pnl > 0 else "loss")
        slip = fill.slippage_bp(links.ref_price(fill))
        title = "\n".join([
            f"{context.local.stamp(fill.ts_ms)}  {role.upper()} {fill.get('side')} "
            f"{_display(fill.get('qty'))} @ {_display(fill.get('price'))}",
            _why(links.decision_of_fill(fill)),
            f"fee {_display(fill.fee)}; slippage {_display(slip)} bp",
            f"round-trip P&L {_display(pnl) if trips else 'open'}",
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
            event.get("detail", "") or _DASH,
            _why(decision),
            f"price {_display(price)}",
        ])
        return Marker(event.ts_ms, price, "dot", "rej", title, hollow=True)


class DecisionLogSection(Section):
    """Every decision, what it saw and what became of it — table and CSV.

    Examples
    --------
    ::

        rows = DecisionLogSection().rows(context)
    """

    title, anchor = "Decision log", "decisions"

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
        decisions = sorted(context.log.of_kind("decision"), key=lambda d: (d.ts_ms, d.seq))
        return [self._row(context, links, decision) for decision in decisions]

    @staticmethod
    def _row(context, links, decision):
        """One decision's row: candidates, choice, action, and its linked execution."""
        decision_id = decision.get("decision_id")
        chosen, runner = decision.chosen_row() or {}, decision.runner_up() or {}
        orders = links.orders_of.get(decision_id, ())
        fills = links.fills_of_decision(decision_id)
        qty = sum(f.get("qty") for f in fills)
        slips = [(f.get("qty"), f.slippage_bp(links.ref_price(f))) for f in fills]
        slips = [(q, s) for q, s in slips if s is not None]
        slip_qty = sum(q for q, _ in slips)
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
            "rejections": "; ".join(f"{r.kind}:{r.get('reason')}"
                                    for r in links.rejections_of(decision_id)) or None,
        }

    def html(self, context):
        """Return the action counts, top reasons and the full table in ``<details>``."""
        rows = self.rows(context)
        if not rows:
            return _note("No decision events.")
        actions = Counter(row["action"] for row in rows)
        counts = [{"action": a, "count": n} for a, n in sorted(actions.items())]
        by_reason = Counter((row["action"], row["reason"]) for row in rows)
        reasons = [{"action": a, "reason": r, "count": n}
                   for (a, r), n in by_reason.most_common(_TOP_REASONS)]
        return (_html_table(("action", "count"), counts)
                + "<h3>Commonest decision reasons</h3>"
                + _html_table(("action", "reason", "count"), reasons)
                + "<h3>Commonest refusal / skip reasons</h3>"
                + _reasons_table(context.log)
                + f"<details><summary>All {len(rows)} decisions (also decisions.csv)</summary>"
                + _html_table(DECISION_COLUMNS, rows, "decisions") + "</details>")


def _reasons_table(log):
    """Render the top refusal/skip reasons, or say there were none."""
    reasons = _top_reasons(log)
    return _html_table(("kind", "reason", "count"), reasons) if reasons else _note("None.")


class CashSection(Section):
    """Cash and equity, gross exposure, and every external cash flow.

    Examples
    --------
    ::

        CashSection().render(context)
    """

    title, anchor = "Cash & exposure", "cash"

    def html(self, context):
        """Return the cash/equity steps with flow markers, exposure, and the flow table."""
        points = context.book.points
        if not points:
            return _note("Nothing to value.")
        flows = context.log.of_kind("cashflow")
        cash_at = {p.ts_ms: float(p.cash) for p in points}
        markers = [Marker(f.ts_ms, cash_at.get(f.ts_ms, 0.0), "dot", "open",
                          f"{context.local.stamp(f.ts_ms)}  cash flow "
                          f"{_display(f.get('amount'))} ({f.get('rule') or 'no rule'})")
                   for f in flows]
        cash = StepChart("Cash and equity",
                         [Series("cash", tuple((p.ts_ms, float(p.cash)) for p in points), "s0"),
                          Series("equity", tuple((p.ts_ms, float(p.equity)) for p in points),
                                 "s3")],
                         [MarkerLayer("◇ cash flow", markers)], local=context.local,
                         y_label="money")
        exposure = StepChart("Gross exposure (gross notional / equity)",
                             [Series("exposure", tuple((p.ts_ms, p.exposure) for p in points),
                                     "s2")],
                             local=context.local, y_label="× equity", height=180)
        rows = [{"time": context.local.stamp(f.ts_ms), "amount": f.get("amount"),
                 "rule": f.get("rule"), "detail": f.get("detail")} for f in flows]
        table = (_html_table(("time", "amount", "rule", "detail"), rows) if rows
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
        parts = [
            Histogram("Round-trip P&L", [t.pnl for t in trips]).render(),
            Histogram("Holding time", [t.holding_ms / 60_000 for t in trips],
                      unit="m").render(),
            Histogram("Slippage vs reference", slips, unit="bp").render(),
        ]
        parts.append("<h3>P&amp;L by instrument</h3>")
        parts.append(self._attribution(trips, lambda t: t.instrument, "instrument"))
        parts.append("<h3>P&amp;L by entry reason</h3>")
        parts.append(self._attribution(trips, lambda t: t.entry_reason, "entry_reason"))
        return "".join(parts)

    @staticmethod
    def _attribution(trips, key_of, name):
        """Tabulate trips, gross, fees and net P&L grouped by ``key_of``."""
        groups = defaultdict(list)
        for trip in trips:
            groups[key_of(trip)].append(trip)
        rows = [{name: key, "trips": len(group), "gross_pnl": sum(t.gross_pnl for t in group),
                 "fees": sum(t.fees for t in group), "pnl": sum(t.pnl for t in group)}
                for key, group in sorted(groups.items(), key=lambda kv: str(kv[0]))]
        if not rows:
            return _note("No closed round trips.")
        return _html_table((name, "trips", "gross_pnl", "fees", "pnl"), rows)


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
        )


class ProvenanceSection(Section):
    """Where the run came from: ids, config, code, data, environment, event counts.

    Examples
    --------
    ::

        ProvenanceSection().render(context)
    """

    title, anchor = "Provenance", "provenance"

    def rows(self, context):
        """Return the provenance facts as ``[{field, value}]``.

        Parameters
        ----------
        context : ReportContext

        Returns
        -------
        list of dict
        """
        start, end = context.log.run_start, context.log.run_end
        code, env = start.get("code", {}), start.get("env", {})
        return [
            {"field": "schema", "value": start.to_obj()["schema"]},
            {"field": "run_id", "value": start.get("run_id")},
            {"field": "config_hash", "value": start.get("config_hash")},
            {"field": "code commit", "value": code.get("commit")},
            {"field": "code dirty", "value": code.get("dirty")},
            {"field": "trials", "value": start.get("trials", 1)},
            {"field": "python", "value": env.get("python")},
            {"field": "platform", "value": env.get("platform")},
            {"field": "packages", "value": len(env.get("packages", {}))},
            {"field": "wall_s", "value": end.get("wall_s") if end else None},
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
        return (_html_table(("field", "value"), self.rows(context))
                + "<h3>Data</h3>"
                + (_html_table(("name", "fingerprint"), data) if data
                   else _note("No data fingerprints recorded."))
                + f"<details><summary>{len(packages)} package(s)</summary>"
                + _html_table(("package", "version"), packages) + "</details>"
                + "<details><summary>Config</summary><pre>"
                + html.escape(config) + "</pre></details>")

    def markdown(self, context):
        """Return the provenance table."""
        return ["## Provenance", "", *_md_table(("field", "value"), self.rows(context)), ""]


#: The default section order — the research spec's reading order.
DEFAULT_SECTIONS = (SummarySection, EquitySection, TradesOnPriceSection, DecisionLogSection,
                    CashSection, DistributionSection, PeriodSection, ProvenanceSection)
