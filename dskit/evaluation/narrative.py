"""The plain-English "What happened" paragraph, written by explicit rules.

A reader should not need the statistics table to learn whether the run
made money and why not. :class:`Narrative` turns the statistics, the
census, the scorecard and the book into short sentences, each produced by
a stated rule over numbers the report already shows — no model writes a
word of it, so two renders of one log say exactly the same thing and a
sentence can be traced to the row it came from.

The rules, in order: the activity (round trips, sessions, decisions,
window); the money (gross, fees, net, and how costs compare with gross);
the biggest driver (costs vs the forecasts' edge); when scores are
returns, the mean chosen forecast against the round-trip fee in basis
points; the hit rate and the win/loss sizes; the best and worst instrument; the drawdown; external
cash (never profit); refusals and skips by reason; the census; the
verdict with each failing or inconclusive criterion.

:data:`HOW_TO_READ` is the fixed reader's note printed beside it.

Import cost: stdlib only (reads the objects the report built).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from dskit.evaluation.units import count, percent, ratio, sig

__all__ = ["HOW_TO_READ", "Narrative"]

#: How many refusal/skip reasons the paragraph names before "and N more".
_REASONS = 3

#: Criterion statistics that read as money in the verdict sentence.
_MONEY_STATS = frozenset({"net_pnl", "gross_pnl", "fees", "trade_mean", "avg_win", "avg_loss",
                          "max_drawdown", "trade_es95"})

#: The reader's note — fixed text, the same on every report.
HOW_TO_READ = (
    "Money is in the account currency. Trading P&L never includes deposits or withdrawals; "
    "account equity does, so compare P&L, not the balance.",
    "Gross means before fees and costs; net means after. A round trip is one entry matched "
    "FIFO to the exit that closed it.",
    "Forecasts, edges and thresholds print in the unit the run declared (a return in basis "
    "points: 1 bp = 0.01%).",
    "The verdict applies pre-registered criteria. INCONCLUSIVE means the sample is below the "
    "criterion's min_n, not that the result is borderline; statistics flagged "
    "'insufficient n' are shown but should not be trusted.",
    "Charts collapse market-closed time: a thin vertical break with a date marks each new "
    "session. Hover a marker for the decision behind it: its reason, forecast, rank and edge.",
    "Every decision is in decisions.csv, every round trip in trades.csv and every event in "
    "events.jsonl; the page thins what it draws and says so where it does.",
)


class Narrative:
    """Build the "What happened" sentences for one report.

    Parameters
    ----------
    context : ReportContext
        The report's context (book, table, scorecard, census, log, units).

    Examples
    --------
    ::

        Narrative(report.context).sentences()[0]
        # -> '1,290 round trips over 5 sessions (2,726 decisions).'
    """

    def __init__(self, context):
        self.context = context
        self.units = context.units
        self.stat = {row.name: row.value for row in context.table.rows}

    def sentences(self):
        """Return the paragraph as a list of sentences, rule by rule.

        Returns
        -------
        list of str
        """
        rules = (self._activity, self._money, self._driver, self._forecast_vs_cost, self._hits,
                 self._instruments, self._drawdown, self._flows, self._rejections, self._census, self._verdict)
        return [sentence for rule in rules for sentence in rule()]

    # -- rules ---------------------------------------------------------------

    def _activity(self):
        """Round trips, decision sessions and the decision count."""
        log, local = self.context.log, self.context.local
        decisions = log.of_kind("decision")
        sessions = len({local.day(d.ts_ms) for d in decisions})
        trips = self.stat.get("trades") or 0
        out = [f"{count(trips)} round trip{'s' if trips != 1 else ''} over "
               f"{count(sessions)} session{'s' if sessions != 1 else ''} "
               f"({count(len(decisions))} decisions)."]
        decided, data = log.span("decision"), log.span()
        if decided is not None:
            text = (f"Decisions ran from {local.stamp(decided[0])[:16]} to "
                    f"{local.stamp(decided[1])[:16]} ({local.tz})")
            if data is not None and (data[0] < decided[0] or data[1] > decided[1]):
                text += (f"; the data (prices, fills, cash flows) span "
                         f"{local.stamp(data[0])[:16]} to {local.stamp(data[1])[:16]}")
            out.append(text + ".")
        return out

    def _money(self):
        """Gross, fees and net, with how the costs compare with the gross."""
        gross, fees, net = (self.stat.get(k) for k in ("gross_pnl", "fees", "net_pnl"))
        if gross is None or net is None:
            return []
        money = self.units.money
        verb = "made" if gross >= 0 else "lost"
        text = (f"Before costs the strategy {verb} {money(gross, signed=True)}; fees were "
                f"{money(fees)}, so net was {money(net, signed=True)}")
        if gross > 0 and fees:
            text += f" (costs were {ratio(fees / gross)}x gross)"
        return [text + "."]

    def _driver(self):
        """Name the biggest driver: costs, the forecasts, or neither."""
        gross, fees, net = (self.stat.get(k) for k in ("gross_pnl", "fees", "net_pnl"))
        if gross is None or net is None:
            return []
        money = self.units.money
        if net >= 0:
            if gross > 0 and fees:
                return [f"Biggest driver: the forecasts' edge; fees took "
                        f"{percent(fees / gross)} of the gross."]
            return []
        if gross >= 0:
            return [f"Biggest driver: costs. The forecasts earned {money(gross, signed=True)} "
                    f"before costs, but fees of {money(fees)} turned that into a loss."]
        if fees and fees > abs(gross):
            return [f"Biggest driver: costs ({money(fees)} in fees), on top of forecasts that "
                    f"lost {money(abs(gross))} before costs."]
        return [f"Biggest driver: the forecasts lost {money(abs(gross))} before costs; fees "
                f"added {money(fees)}."]

    def _forecast_vs_cost(self):
        """When scores are returns: the mean chosen forecast against the round-trip cost, in bp."""
        unit = self.units.score_unit
        trips = self.context.book.round_trips
        if unit.suffix != "bp" or not trips:
            return []
        scores = [row.get("score") for d in self.context.log.of_kind("decision")
                  if d.get("action") == "enter" and (row := d.chosen_row()) is not None
                  and row.get("score") is not None]
        notional = sum(t.qty * t.entry_price for t in trips)
        if not scores or not notional:
            return []
        forecast_bp = sum(scores) / len(scores) * unit.scale
        cost_bp = sum(t.fees for t in trips) / notional * 1e4
        verdict = ("could not cover" if forecast_bp < cost_bp else "exceeded")
        return [f"The average chosen forecast was {sig(forecast_bp, 3)} bp against round-trip "
                f"fees of {sig(cost_bp, 3)} bp of notional: the forecasts {verdict} costs."]

    def _hits(self):
        """Hit rate and the average win against the average loss."""
        hit, win, loss = (self.stat.get(k) for k in ("hit_rate", "avg_win", "avg_loss"))
        if hit is None:
            return []
        text = f"{percent(hit)} of round trips won"
        if win is not None and loss is not None:
            size = "smaller" if win < abs(loss) else "larger" if win > abs(loss) else "the same"
            than = "" if size == "the same" else " than"
            text += (f"; the average win ({self.units.money(win)}) was {size}{than} the "
                     f"average loss ({self.units.money(abs(loss))})")
        return [text + "."]

    def _instruments(self):
        """Name the best and worst instrument by net round-trip P&L."""
        by = defaultdict(lambda: [0.0, 0])
        for trip in self.context.book.round_trips:
            by[trip.instrument][0] += trip.pnl
            by[trip.instrument][1] += 1
        if len(by) < 2:
            return []
        ranked = sorted(by.items(), key=lambda kv: (kv[1][0], kv[0]))
        money = self.units.money
        (worst, (w_pnl, w_n)), (best, (b_pnl, b_n)) = ranked[0], ranked[-1]
        best_text = f"{best} ({money(b_pnl, signed=True)} over {count(b_n)} trips)"
        worst_text = f"{worst} ({money(w_pnl, signed=True)} over {count(w_n)})"
        if b_pnl < 0:
            return [f"All {len(by)} instruments lost money net of fees; the smallest loss was "
                    f"{best_text}, the largest {worst_text}."]
        if w_pnl > 0:
            return [f"All {len(by)} instruments made money net of fees; the most was "
                    f"{best_text}, the least {worst_text}."]
        return [f"Best instrument {best_text}; worst {worst_text}."]

    def _drawdown(self):
        """State the worst fall of trading P&L from a peak."""
        depth, pct = self.stat.get("max_drawdown"), self.stat.get("max_drawdown_pct")
        if not depth:
            return []
        tail = f" ({percent(pct)} of equity at the peak)" if pct is not None else ""
        return [f"Worst drawdown of trading P&L: {self.units.money(depth)}{tail}."]

    def _flows(self):
        """External cash: named, summed, and said not to be profit."""
        flows = self.context.log.of_kind("cashflow")
        if not flows:
            return []
        total = sum(f.get("amount") for f in flows)
        return [f"External cash flows of {self.units.money(total, signed=True)} "
                f"({count(len(flows))} flow{'s' if len(flows) != 1 else ''}) are in the "
                "account balance but are not profit."]

    def _rejections(self):
        """Refusals and skips, by kind and commonest reasons."""
        counts = Counter((e.kind, e.get("reason"))
                         for e in self.context.log.of_kind("refusal", "skip"))
        out = []
        for kind, verb in (("refusal", "refused"), ("skip", "skipped")):
            reasons = [(reason, n) for (k, reason), n in counts.most_common() if k == kind]
            if not reasons:
                continue
            total = sum(n for _r, n in reasons)
            named = ", ".join(f"{reason} ({count(n)})" for reason, n in reasons[:_REASONS])
            more = f" and {len(reasons) - _REASONS} more" if len(reasons) > _REASONS else ""
            out.append(f"{count(total)} action{'s' if total != 1 else ''} "
                       f"{'were' if total != 1 else 'was'} {verb}: {named}{more}.")
        return out

    def _census(self):
        """Census anomalies, or that everything is accounted for."""
        problems = self.context.census.problems
        if not problems:
            return ["Every decision and order is accounted for."]
        return [f"Census anomalies ({len(problems)}): " + "; ".join(problems) + "."]

    def _verdict(self):
        """State the verdict with every failing and inconclusive criterion, in words."""
        card = self.context.scorecard
        if not card.verdicts:
            return ["No criteria were pre-registered, so there is no verdict (UNJUDGED)."]
        failed = [self._failed(v) for v in card.verdicts if v.status == "FAIL"]
        unsure = [f"{v.criterion.name} ({v.reason})" for v in card.verdicts
                  if v.status == "INCONCLUSIVE"]
        text = f"Verdict {card.status}"
        if failed:
            text += ": " + " and ".join(failed)
        elif card.status == "PASS":
            text += ": every pre-registered criterion passed"
        if unsure:
            text += "; inconclusive: " + ", ".join(unsure)
        return [text + "."]

    def _failed(self, verdict):
        """``net_pnl = -$180.47 (needed > 0)`` for one failed criterion."""
        criterion, observed = verdict.criterion, verdict.observed
        shown = (self.units.money(observed) if criterion.stat in _MONEY_STATS
                 else sig(observed, 3))
        return f"{criterion.stat} = {shown} (needed {criterion.op} {criterion.value:g})"
