"""Shape one MIO method's saved result into the evaluator's row ports (ADR-0283).

A saved ``result.json`` holds, per method, the books the run filled
(``trades``), the books it refused at fill time (``rejected``), the weekly
``decisions`` with their solver ``certificates``, the ``deposits`` and the
marked daily ``curve``. :class:`MioRunRows` turns one method of it into the
row lists :class:`dskit.evaluation.nodes.RowsToEvents` maps, so the generic
evaluator renders the run; nothing here computes a result, it only restates
what the run saved.

The mapping (each book is one instrument, one unit):

* entry: a ``buy`` of 1 at ``-premium`` (the book's net premium, so a debit
  is a positive price and a credit a negative one) with the saved ``fees``,
  on ``fill_date``;
* expiry: a ``sell`` of 1 at ``payoff = pnl - credit`` on ``close_date``.
  Net P&L of the pair is ``payoff - (-premium) - fees = credit + payoff =
  pnl`` exactly, whatever the signs; an option expiring worthless closes at
  0. The logs this feeds declare ``run_start.signed_prices``;
* ``rejected`` rows are refusals with their saved reason; each decision is a
  decision event whose ``status`` maps to an action; its stage refusals are
  ``skip`` rows; its ``certificates`` stay on the decision row for the
  ``solves`` map.

The multiplier and the cent tolerance are params. Before shaping, every
trade's saved ``premium`` must equal ``-sum(sign * fill_price * quantity *
multiplier)`` over its legs and its saved ``credit`` must equal
``premium - fees``, each within ``tolerance``; any mismatch refuses.

Import cost: stdlib plus ``dskit.pipeline``. Wired by dotted path.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["MioRunRows"]

#: The event-time slots a document sets, each an ``HH:MM`` in ``tz``.
TIME_SLOTS = ("decision", "fill", "expiry", "deposit")
#: The cash-flow rules the rows carry.
OPENING_RULE, DEPOSIT_RULE = "initial_capital", "scheduled_deposit"
#: The marked figures shown beside the event ones, in order.
MARKED_FIGURES = ("pnl", "final_equity", "contributions", "fees", "twr", "max_drawdown")
#: The saved keys each kind of row must carry (a missing one refuses, listed).
REQUIRED = {
    "result": ("trades", "rejected", "decisions", "deposits", "curve", "unfilled",
               "ledger_summary", *MARKED_FIGURES),
    "trades": ("id", "symbol", "expiry", "decision_date", "fill_date", "close_date", "contracts",
               "premium", "fees", "credit", "pnl", "legs"),
    "rejected": ("id", "symbol", "expiry", "decision_date", "fill_date", "contracts", "reason"),
    "decisions": ("date", "status", "funding", "open_reserve"),
    "deposits": ("date", "amount"),
    "curve": ("date", "deposit", "cash", "reserve", "funding", "open_ids", "realized", "equity",
              "nav", "drawdown", "contributions"),
    "ledger_summary": ("closed", "orders_opened"),
}
#: The keys a trade's leg must carry.
LEG_KEYS = ("sign", "fill_price", "quantity")
#: How many rows of one kind a missing-key refusal names before it counts the rest.
MISSING_SHOWN = 3


class _Clock:
    """ISO dates -> UTC epoch ms at a declared local time of day."""

    def __init__(self, tz, times):
        self.zone = ZoneInfo(tz)
        self.times = {slot: time.fromisoformat(text) for slot, text in times.items()}

    def at(self, slot, day):
        """Return epoch ms of ``day`` (ISO text) at the ``slot``'s local time."""
        moment = datetime.combine(date.fromisoformat(day), self.times[slot], tzinfo=self.zone)
        return int(moment.timestamp() * 1000)


class MioRunRows(Node):
    """One method of a saved MIO ``result.json`` as event rows (role ``transform``).

    Parameters
    ----------
    params : dict
        ``method`` (str) — the ``results[].id`` to shape. ``tz`` (IANA str)
        and ``times`` (``{decision, fill, expiry, deposit: "HH:MM"}``) — the
        local instants events carry. ``multiplier`` (positive number) and
        ``tolerance`` (number >= 0) — the contract multiplier and the
        accepted saved-premium discrepancy in dollars. ``actions``
        (``{status: action}``) — each decision status the run may save and
        the event action it becomes; an unlisted status refuses.

    Inputs
    ------
    ``table``
        The parsed ``result.json`` (``table-file``'s output).

    Outputs
    -------
    ``decisions``, ``orders``, ``refusals``, ``skips``, ``fills``, ``cashflows``
        Row lists for ``RowsToEvents`` (each row has ``asof_ms``).
    ``filled``, ``refused``
        Flat (no nested field) copies of the trades and of the fill-stage
        refusals, for ticker x DTE tables.
    ``ledger``
        One row per marked-curve date: the day-by-day account.
    ``marked``
        ``{figure, value}`` rows of the saved marked figures.
    ``expected``
        ``{pnl, final_equity, contributions, fees, twr, max_drawdown}``, the
        saved values the exact-match gate compares to.

    Raises
    ------
    ValueError
        On a missing or repeated method, an unknown decision status, a book
        with no decision, a duplicated book id, an event date outside the
        curve, or a saved premium/credit that does not add up.

    Examples
    --------
    ::

        node = MioRunRows("rows", {"method": "nominal_lp", "tz": "America/New_York",
            "multiplier": 100, "tolerance": 0.005,
            "times": {"decision": "16:00", "fill": "09:30",
                      "expiry": "16:00", "deposit": "08:00"},
            "actions": {"selected": "enter", "cash": "hold",
                        "no_eligible_candidates": "hold"}})
        rows = node.run(ctx, {"table": result_json})
        rows["fills"][0]["side"]  # 'buy'
    """

    role = "transform"
    outputs = ("decisions", "orders", "refusals", "skips", "fills", "cashflows", "filled",
               "refused", "ledger", "marked", "expected")
    _PARAMS = ("method", "tz", "times", "multiplier", "tolerance", "actions", "notes")

    @classmethod
    def validate_params(cls, params):
        """Refuse unknown knobs and malformed method, zone, times, numbers or actions.

        Parameters
        ----------
        params : dict

        Returns
        -------
        list of str
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        method = params.get("method")
        if not isinstance(method, str) or not method:
            problems.append(f"method must be a non-empty string, got {method!r}")
        try:
            ZoneInfo(params.get("tz"))
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            problems.append(f"tz {params.get('tz')!r} is not an IANA time zone")
        times = params.get("times")
        if not isinstance(times, dict) or set(times) != set(TIME_SLOTS):
            problems.append(f"times must hold exactly {list(TIME_SLOTS)} as HH:MM, got {times!r}")
        else:
            for slot, text in times.items():
                try:
                    if len(text) != 5:
                        raise ValueError(text)
                    time.fromisoformat(text)
                except (TypeError, ValueError):
                    problems.append(f"times.{slot} must be HH:MM, got {text!r}")
        multiplier = params.get("multiplier")
        if not number_ok(multiplier) or multiplier <= 0:
            problems.append(f"multiplier must be a finite number > 0, got {multiplier!r}")
        tolerance = params.get("tolerance")
        if not number_ok(tolerance) or tolerance < 0:
            problems.append(f"tolerance must be a finite number >= 0, got {tolerance!r}")
        actions = params.get("actions")
        if not isinstance(actions, dict) or not actions or not all(
                isinstance(k, str) and isinstance(v, str) and v for k, v in actions.items()):
            problems.append(f"actions must be a non-empty object of status -> action, "
                            f"got {actions!r}")
        return problems

    def validate_inputs(self, inputs):
        """Refuse a ``table`` that holds no ``results`` list.

        Parameters
        ----------
        inputs : dict

        Returns
        -------
        list of str
        """
        table = inputs.get("table")
        if not isinstance(table, dict) or not isinstance(table.get("results"), list):
            return ["table must be a result.json object holding a 'results' list"]
        return []

    def run(self, ctx, inputs):
        """Shape the method's saved result into the row ports.

        Parameters
        ----------
        ctx : NodeContext or None
        inputs : dict

        Returns
        -------
        dict
        """
        result = self._result(inputs["table"])
        clock = _Clock(self.params["tz"], self.params["times"])
        self._require(result)
        trades, rejected = result["trades"], result["rejected"]
        decisions = result["decisions"]
        self._check_books(result)
        selected = {book["id"]: book for d in decisions for book in d.get("books", [])}
        return {
            "decisions": self._decisions(decisions, clock),
            "orders": self._orders(trades, rejected, clock),
            "refusals": self._refusals(rejected, clock),
            "skips": self._skips(decisions, clock),
            "fills": self._fills(trades, clock),
            "cashflows": self._cashflows(result, clock),
            "filled": [self._flat(t) for t in trades],
            "refused": [self._flat(r) for r in rejected],
            "ledger": self._ledger(result, selected),
            "marked": [{"figure": name, "value": result[name]} for name in MARKED_FIGURES],
            "expected": self._expected(result),
        }

    # -- selection and checks ------------------------------------------------

    def _result(self, table):
        """Return the one method's ``result`` object, or refuse."""
        method = self.params["method"]
        found = [r for r in table["results"] if r.get("id") == method]
        if len(found) != 1:
            ids = [r.get("id") for r in table["results"]]
            raise ValueError(f"{self.key}: {'no result' if not found else f'{len(found)} results'}"
                             f" with id {method!r} (ids: {ids})")
        return found[0]["result"]

    def _require(self, result):
        """Refuse a saved result that lacks keys this node reads, listing every one."""
        problems = self._missing([result], REQUIRED["result"], "result")
        if not problems:
            problems += self._missing([result["ledger_summary"]], REQUIRED["ledger_summary"],
                                      "ledger_summary")
        for kind in ("trades", "rejected", "decisions", "deposits", "curve"):
            problems += self._missing(result.get(kind, ()), REQUIRED[kind], kind)
        legs = [leg for trade in result.get("trades", ()) for leg in trade.get("legs", ())]
        problems += self._missing(legs, LEG_KEYS, "trade legs")
        if problems:
            raise ValueError(f"{self.key}: saved result is missing keys: " + "; ".join(problems))

    @staticmethod
    def _missing(rows, keys, label):
        """Return one problem per row (the first few) lacking any of ``keys``, then a count."""
        absent = [(i, [k for k in keys if k not in row]) for i, row in enumerate(rows)]
        absent = [(i, ks) for i, ks in absent if ks]
        shown = [f"{label}[{i}] missing {ks}" for i, ks in absent[:MISSING_SHOWN]]
        if len(absent) > MISSING_SHOWN:
            shown.append(f"{label}: {len(absent) - MISSING_SHOWN} more row(s) missing keys")
        return shown

    def _expected(self, result):
        """Return the saved totals and counts the gate compares to the events."""
        summary = result["ledger_summary"]
        return {**{name: result[name] for name in MARKED_FIGURES},
                "fills": summary["orders_opened"] + sum(summary["closed"].values()),
                "refusals": result["unfilled"], "decisions": len(result["decisions"])}

    def _check_books(self, result):
        """Refuse a saved result whose books, dates, premiums or counts do not add up."""
        trades, rejected = result["trades"], result["rejected"]
        decisions, curve = result["decisions"], result["curve"]
        problems = []
        ids = Counter(row["id"] for row in (*trades, *rejected))
        problems += [f"book {i!r} appears twice among trades and rejected" for i, n in
                     ids.items() if n > 1]
        dated = {d["date"] for d in decisions}
        days = {row["date"] for row in curve}
        for row in (*trades, *rejected):
            if row["decision_date"] not in dated:
                problems.append(f"book {row['id']!r} has no decision on {row['decision_date']}")
        for row in trades:
            problems += self._premium_problems(row)
            problems += [f"book {row['id']!r} {name} {row[name]} is outside the curve"
                         for name in ("fill_date", "close_date") if row[name] not in days]
            if not row["decision_date"] < row["fill_date"]:
                problems.append(f"book {row['id']!r} fill_date {row['fill_date']} is not after "
                                f"its decision_date {row['decision_date']}")
            if not row["fill_date"] <= row["close_date"]:
                problems.append(f"book {row['id']!r} fill_date {row['fill_date']} is not on or "
                                f"before its close_date {row['close_date']}")
        for row in rejected:
            if row["fill_date"] not in days:
                problems.append(f"refused book {row['id']!r} fill_date {row['fill_date']} is "
                                f"outside the curve")
            if not row["decision_date"] < row["fill_date"]:
                problems.append(f"refused book {row['id']!r} fill_date {row['fill_date']} is not "
                                f"after its decision_date {row['decision_date']}")
        problems += self._count_problems(result)
        unknown = sorted({d["status"] for d in decisions} - set(self.params["actions"]))
        problems += [f"decision status {status!r} has no action in params.actions"
                     for status in unknown]
        if problems:
            raise ValueError(f"{self.key}: {len(problems)} problem(s): " + "; ".join(problems))

    @staticmethod
    def _count_problems(result):
        """Problems where a saved count disagrees with the saved rows."""
        summary = result["ledger_summary"]
        closed = sum(summary["closed"].values())
        trades, rejected = len(result["trades"]), len(result["rejected"])
        problems = []
        for name, saved, found in (("ledger_summary.closed total", closed, trades),
                                   ("ledger_summary.orders_opened", summary["orders_opened"],
                                    trades),
                                   ("unfilled", result["unfilled"], rejected)):
            if saved != found:
                problems.append(f"saved {name} is {saved} but the rows number {found}")
        return problems

    def _premium_problems(self, trade):
        """Problems with one trade's saved premium and credit."""
        tolerance = self.params["tolerance"]
        from_legs = -sum(leg["sign"] * leg["fill_price"] * leg["quantity"]
                         for leg in trade["legs"]) * self.params["multiplier"]
        problems = []
        if abs(from_legs - trade["premium"]) > tolerance:
            problems.append(f"book {trade['id']!r} saved premium {trade['premium']} differs "
                            f"from its legs' {from_legs:.6f}")
        if abs(trade["credit"] - (trade["premium"] - trade["fees"])) > tolerance:
            problems.append(f"book {trade['id']!r} saved credit {trade['credit']} differs "
                            f"from premium - fees {trade['premium'] - trade['fees']:.6f}")
        return problems

    # -- the ports -----------------------------------------------------------

    @staticmethod
    def _decision_id(day):
        return f"d-{day}"

    def _decisions(self, decisions, clock):
        """One decision row per saved weekly decision, certificates kept."""
        rows = []
        for d in decisions:
            reasons = Counter(r["reason"] for r in d.get("refusals", ()))
            summary = ", ".join(f"{reason} {n}" for reason, n in sorted(reasons.items()))
            rows.append({
                "asof_ms": clock.at("decision", d["date"]),
                "decision_id": self._decision_id(d["date"]),
                "action": self.params["actions"][d["status"]],
                "reason": d["status"],
                "model": self.params["method"],
                "detail": (f"{d.get('candidate_books', 0)} candidate books, "
                           f"{len(d.get('books', ()))} selected, funding {d['funding']:.2f}, "
                           f"open reserve {d['open_reserve']:.2f}; stage refusals: "
                           f"{summary or 'none'}"),
                "certificates": list(d.get("certificates", ())),
            })
        return rows

    def _skips(self, decisions, clock):
        """Each decision-stage refusal (no dte, no order) as a skip of its symbol."""
        return [{"asof_ms": clock.at("decision", d["date"]),
                 "instrument": r["symbol"], "decision_id": self._decision_id(d["date"]),
                 "reason": r["reason"],
                 "detail": f"expiry {r['expiry']}" if "expiry" in r else "no expiry"}
                for d in decisions for r in d.get("refusals", ())]

    def _orders(self, trades, rejected, clock):
        """One order per attempted book: the unit bought at its net premium."""
        return [{"asof_ms": clock.at("fill", row["fill_date"]), "instrument": row["id"],
                 "order_id": row["id"], "decision_id": self._decision_id(row["decision_date"]),
                 "side": "buy", "qty": 1}
                for row in (*trades, *rejected)]

    def _refusals(self, rejected, clock):
        """Each fill-stage refusal with its saved reason."""
        return [{"asof_ms": clock.at("fill", row["fill_date"]), "instrument": row["id"],
                 "order_id": row["id"], "decision_id": self._decision_id(row["decision_date"]),
                 "reason": row["reason"],
                 "detail": f"{row['symbol']} {row['contracts']} contracts, expiry {row['expiry']}"}
                for row in rejected]

    def _fills(self, trades, clock):
        """Per trade an entry at ``-premium`` with fees and an expiry at ``pnl - credit``."""
        rows = []
        for t in trades:
            label = f"{t['symbol']} {t['contracts']} contracts, expiry {t['expiry']}"
            rows.append({"asof_ms": clock.at("fill", t["fill_date"]), "instrument": t["id"],
                         "fill_id": f"{t['id']}:open", "order_id": t["id"], "side": "buy",
                         "qty": 1, "price": 0.0 - t["premium"], "fee": t["fees"],
                         "tag": f"open {label}"})
            rows.append({"asof_ms": clock.at("expiry", t["close_date"]), "instrument": t["id"],
                         "fill_id": f"{t['id']}:close", "order_id": None, "side": "sell",
                         "qty": 1, "price": t["pnl"] - t["credit"], "fee": 0,
                         "tag": f"settle {label}"})
        return rows

    def _cashflows(self, result, clock):
        """Return the opening capital (the first curve row's contributions), then each deposit."""
        first = result["curve"][0]
        opening = first["contributions"] - first["deposit"]
        rows = [{"asof_ms": clock.at("deposit", first["date"]), "amount": opening,
                 "rule": OPENING_RULE, "detail": "initial cash"}]
        rows += [{"asof_ms": clock.at("deposit", d["date"]), "amount": d["amount"],
                  "rule": DEPOSIT_RULE, "detail": "scheduled deposit"}
                 for d in result["deposits"]]
        return rows

    @staticmethod
    def _flat(row):
        """Return the row without its nested (list or object) fields."""
        return {k: v for k, v in row.items() if not isinstance(v, (list, dict))}

    def _ledger(self, result, selected):
        """One row per curve date: the attempts, fills, refusals and expiries of the day."""
        by_day = defaultdict(lambda: defaultdict(float))
        reasons = defaultdict(Counter)
        for t in result["trades"]:
            fill, close = by_day[t["fill_date"]], by_day[t["close_date"]]
            fill["selected_books"] += 1
            fill["selected_contracts"] += selected[t["id"]]["contracts"]
            fill["filled_books"] += 1
            fill["filled_contracts"] += t["contracts"]
            fill["entry_fees"] += t["fees"]
            close["expired_books"] += 1
            close["expiry_pnl"] += t["pnl"]
        for r in result["rejected"]:
            day = by_day[r["fill_date"]]
            day["selected_books"] += 1
            day["selected_contracts"] += r["contracts"]
            day["refused_books"] += 1
            reasons[r["fill_date"]][r["reason"]] += 1
        columns = ("selected_books", "selected_contracts", "filled_books", "filled_contracts",
                   "refused_books", "entry_fees", "expired_books", "expiry_pnl")
        return [{
            "date": row["date"], "deposit": row["deposit"],
            **{name: _tidy(by_day[row["date"]][name]) for name in columns},
            "refusal_reasons": ", ".join(f"{k} {n}" for k, n in
                                         sorted(reasons[row["date"]].items())),
            "cash": row["cash"], "reserve": row["reserve"], "funding": row["funding"],
            "open_books": len(row["open_ids"]), "realized": row["realized"],
            "equity": row["equity"], "nav": row["nav"], "drawdown": row["drawdown"],
        } for row in result["curve"]]


def _tidy(value):
    """Return a whole float as an int (a count), else the float rounded to the cent."""
    return int(value) if float(value).is_integer() else round(value, 2) if math.isfinite(
        value) else value
