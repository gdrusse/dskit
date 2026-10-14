"""The tier-2 order builder: strikes and expiries chosen from a daily option chain (ADR-0265 Amendment 5).

:class:`~dskit.pipeline.libs.option_ledger.OptionStructureLedger` opens the orders it is given; something
has to decide, from the chain of the day, WHICH expiration and WHICH strikes. :class:`ChainStructureOrders`
is that step, so a document names a structure's leg rules and the ledger trades exactly what they select.
It reads the chain once, symbol by symbol with dates ascending (the order ``ParquetChainRows`` yields; a date
going backwards or a symbol reappearing refuses) and holds one symbol-date.

**A structure** (``structures``: name -> spec) has ``legs`` (``right``, a signed ``quantity``, a ``select``
rule), an ``entry`` rule, an ``expiry`` rule, and optionally ``symbols`` (only those) and ``expiry_rule``
(``weekday`` 0-6 and ``day_of_month`` ``[lo, hi]``: only expirations on such a day, e.g. the standard monthly).

* ``expiry``: ``dte`` (the target calendar days), ``tolerance`` (how far from it) and ``min_dte``; the
  listed expiration nearest the target within the tolerance and at least ``min_dte`` out (a tie to the
  earlier). None inside the tolerance is a counted refusal.
* ``entry``: ``after_expiry`` (the first eligible date, then the first date on or after the previous order's
  expiration: a serial roll), ``every_n`` (the first eligible date, then every ``n``-th session of the symbol
  in this chain) or ``days_before_expiry`` (one order for each listed expiration, passing ``expiry_rule``,
  exactly ``days`` calendar days after the date; the expiry is then the entry's, so no ``expiry`` block).
* ``select``: ``atm`` (the strike nearest the parity forward in log terms among those quoted usably on both
  sides, ties to the lower), ``delta`` (``target``: of the out-of-the-money strikes of the leg's right, below the
  forward for puts and above for calls, the one whose Black-76 forward delta magnitude from its own mid-implied
  vol is nearest ``target``, ties to the lower), ``max_oi`` (the strike with the largest open interest summed over
  calls and puts, ties to the lower) and ``mirror`` (``of``: an earlier leg; the strike of the leg's right with a
  usable quote nearest ``2 * spot - strike_of_that_leg``, ties to the lower).

The forward, the vols and the delta are :class:`~dskit.pipeline.libs.chain_features.QuoteBook`'s, from the usable
quotes (:func:`~dskit.pipeline.libs.chain_census.quote_state`) at one constant ``rate`` that serves only to pick
strikes. Nothing is filled: a day with no usable expiry, forward, underlying or strike yields no order and is
counted in ``summary.refused`` as ``<structure>:<reason>``.

**Output.** ``orders``: one row per order, named by ``order_fields`` (the ledger's ``date``, ``symbol``,
``expiration``, ``strikes``, ``structure``), plus ``spot`` (the median positive underlying of the day),
``forward``, ``dte`` and ``leg_quantities`` (the signed quantities in leg order). The structure's leg vocabulary
for the ledger is the document's own ``structures`` map there; this node only chooses strikes.

Import cost: stdlib and the pipeline core; pandas is imported inside the forward step.
"""

import math
from collections import Counter
from datetime import date
from statistics import median

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.chain_census import USABLE_STATES, quote_state
from dskit.pipeline.libs.chain_features import DEFAULT_DAYS_PER_YEAR, DEFAULT_IV_MAX, DEFAULT_IV_MIN, QuoteBook
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.option_pricing import RIGHTS, black76_delta
from dskit.pipeline.records import number_ok

__all__ = ["ENTRY_KINDS", "NODE_KINDS", "SELECT_KINDS", "ChainStructureOrders"]

NODE_KINDS = ()
ENTRY_KINDS = ("after_expiry", "every_n", "days_before_expiry")
SELECT_KINDS = ("atm", "delta", "max_oi", "mirror")
_REQUIRED_CHAIN = ("symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying")
_ORDER_FIELDS = ("date", "symbol", "expiration", "strikes", "structure")
_STRUCTURE_KEYS = ("legs", "entry", "expiry", "symbols", "expiry_rule")


def _day(value):
    """A ``date`` for a date or an ISO ``YYYY-MM-DD`` string, else ``None``."""
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _num(value):
    return float(value) if number_ok(value) and not isinstance(value, bool) else None


class _Day:
    """The rows of one symbol-date: per expiration, per strike, the quotes and open interest of each side."""

    def __init__(self, symbol, day):
        self.symbol, self.day = symbol, day
        self.expirations = {}
        self.underlyings = []

    def add(self, expiration, strike, side, bid, ask, oi, underlying):
        self.expirations.setdefault(expiration, {}).setdefault(strike, {})[side] = (bid, ask, oi)
        if underlying is not None and underlying > 0:
            self.underlyings.append(underlying)

    def spot(self):
        return median(self.underlyings) if self.underlyings else None


class ChainStructureOrders(Node):
    """Choose expirations and strikes from a daily chain and emit ledger orders — by import path.

    Role ``transform``; input ``records`` (chain rows); outputs ``orders`` and ``summary``. See the module
    docstring for the structure vocabulary, the entry, expiry and select rules and the counted refusals.

    Parameters
    ----------
    params : dict
        REQUIRED: ``chain_fields`` (columns :data:`_REQUIRED_CHAIN`, plus ``open_interest`` when a leg selects
        ``max_oi``), ``order_fields`` (the ledger's :data:`_ORDER_FIELDS`), ``call_values`` and ``put_values``
        (the right spellings), ``end_before`` (ISO; a row on or after it refuses), ``start`` (ISO; no order before
        it) and ``structures``. Optional: ``rate`` (default 0.0), ``days_per_year`` (default 365),
        ``min_forward_pairs`` (default 5), ``iv_min`` and ``iv_max``.
    """

    role = "transform"
    outputs = ("orders", "summary")
    _PARAMS = ("chain_fields", "order_fields", "call_values", "put_values", "end_before", "start", "structures",
               "rate", "days_per_year", "min_forward_pairs", "iv_min", "iv_max")
    _REQUIRED = ("chain_fields", "order_fields", "call_values", "put_values", "end_before", "start", "structures")

    # -- declaration --------------------------------------------------------------------------

    @staticmethod
    def _date_problem(problems, name, value):
        if not is_node_ref(value) and _day(value) is None:
            problems.append(f"{name} must be an ISO date YYYY-MM-DD, got {value!r}")

    @classmethod
    def _select_problems(cls, where, select, legs_before):
        if not isinstance(select, dict) or select.get("kind") not in SELECT_KINDS:
            return [f"{where}.select.kind must be one of {list(SELECT_KINDS)}, got {select!r}"]
        kind, problems = select["kind"], []
        allowed = {"atm": {"kind"}, "max_oi": {"kind"}, "delta": {"kind", "target"}, "mirror": {"kind", "of"}}[kind]
        problems += [f"{where}.select: unknown key {k!r}" for k in sorted(set(select) - allowed)]
        if kind == "delta" and not (number_ok(select.get("target")) and 0 < select["target"] < 0.5):
            problems.append(f"{where}.select.target must be a delta magnitude in (0, 0.5), got {select.get('target')!r}")
        if kind == "mirror":
            of = select.get("of")
            if isinstance(of, bool) or not isinstance(of, int) or not 0 <= of < legs_before:
                problems.append(f"{where}.select.of must name an earlier leg (0..{legs_before - 1}), got {of!r}")
        return problems

    @classmethod
    def _legs_problems(cls, where, legs, oi_available):
        if not isinstance(legs, list) or not legs:
            return [f"{where}.legs must be a non-empty list"]
        problems = []
        for i, leg in enumerate(legs):
            at = f"{where}.legs[{i}]"
            if not isinstance(leg, dict) or set(leg) - {"right", "quantity", "select"}:
                problems.append(f"{at} is a dict of right, quantity, select, got {leg!r}")
                continue
            if leg.get("right") not in RIGHTS:
                problems.append(f"{at}.right must be one of {list(RIGHTS)}, got {leg.get('right')!r}")
            q = leg.get("quantity")
            if isinstance(q, bool) or not isinstance(q, int) or q == 0:
                problems.append(f"{at}.quantity must be a nonzero int, got {q!r}")
            problems += cls._select_problems(at, leg.get("select"), i)
            if isinstance(leg.get("select"), dict) and leg["select"].get("kind") == "max_oi" and not oi_available:
                problems.append(f"{at}: max_oi needs chain_fields.open_interest")
        return problems

    @classmethod
    def _entry_expiry_problems(cls, where, spec):
        entry, expiry, problems = spec.get("entry"), spec.get("expiry"), []
        if not isinstance(entry, dict) or entry.get("kind") not in ENTRY_KINDS:
            return [f"{where}.entry.kind must be one of {list(ENTRY_KINDS)}, got {entry!r}"]
        kind = entry["kind"]
        extra = {"after_expiry": set(), "every_n": {"n"}, "days_before_expiry": {"days"}}[kind]
        problems += [f"{where}.entry: unknown key {k!r}" for k in sorted(set(entry) - {"kind"} - extra)]
        for key in sorted(extra):
            check_int_param(problems, f"{where}.entry.{key}", entry.get(key), ge=1)
        if kind == "days_before_expiry":
            if expiry is not None:
                problems.append(f"{where}: days_before_expiry fixes the expiry; declare no expiry block")
        elif not isinstance(expiry, dict) or set(expiry) != {"dte", "tolerance", "min_dte"}:
            problems.append(f"{where}.expiry needs exactly dte, tolerance and min_dte, got {expiry!r}")
        else:
            for key in ("dte", "min_dte"):
                check_int_param(problems, f"{where}.expiry.{key}", expiry[key], ge=1)
            check_int_param(problems, f"{where}.expiry.tolerance", expiry["tolerance"], ge=0)
        return problems

    @classmethod
    def _structure_problems(cls, name, spec, oi_available):
        where = f"structures[{name!r}]"
        if not isinstance(spec, dict):
            return [f"{where} must be a dict, got {spec!r}"]
        problems = [f"{where}: unknown key {k!r}" for k in sorted(set(spec) - set(_STRUCTURE_KEYS))]
        problems += cls._legs_problems(where, spec.get("legs"), oi_available)
        problems += cls._entry_expiry_problems(where, spec)
        symbols = spec.get("symbols")
        if "symbols" in spec and (not isinstance(symbols, list) or not symbols
                                  or not all(isinstance(s, str) and s for s in symbols)):
            problems.append(f"{where}.symbols must be a non-empty list of names")
        rule = spec.get("expiry_rule")
        if "expiry_rule" in spec:
            ok = isinstance(rule, dict) and set(rule) <= {"weekday", "day_of_month"} and rule
            if ok and "weekday" in rule:
                ok = isinstance(rule["weekday"], int) and not isinstance(rule["weekday"], bool) and 0 <= rule["weekday"] <= 6
                if not ok:
                    problems.append(f"{where}.expiry_rule.weekday must be an int 0-6, got {rule['weekday']!r}")
                    ok = True
            if ok and "day_of_month" in rule:
                dom = rule["day_of_month"]
                if not (isinstance(dom, list) and len(dom) == 2 and all(isinstance(v, int) and not isinstance(v, bool)
                                                                        for v in dom) and 1 <= dom[0] <= dom[1] <= 31):
                    problems.append(f"{where}.expiry_rule.day_of_month must be [lo, hi] within 1-31, got {dom!r}")
            if not ok:
                problems.append(f"{where}.expiry_rule is a non-empty dict of weekday and/or day_of_month")
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems += [f"{name} is required" for name in cls._REQUIRED if name not in params]
        fields = params.get("chain_fields")
        oi_available = isinstance(fields, dict) and "open_interest" in fields
        if "chain_fields" in params and not is_node_ref(fields):
            if not isinstance(fields, dict):
                problems.append(f"chain_fields must be a dict, got {fields!r}")
            else:
                problems += [f"chain_fields.{k} is required" for k in _REQUIRED_CHAIN if k not in fields]
                problems += [f"chain_fields: unknown key {k!r}" for k in sorted(set(fields) - {*_REQUIRED_CHAIN, "open_interest"})]
        order = params.get("order_fields")
        if "order_fields" in params and not is_node_ref(order) and (
                not isinstance(order, dict) or set(order) != set(_ORDER_FIELDS)):
            problems.append(f"order_fields must name exactly {list(_ORDER_FIELDS)}, got {order!r}")
        for name in ("call_values", "put_values"):
            if name in params and not is_node_ref(params[name]) and (
                    not isinstance(params[name], list) or not params[name]):
                problems.append(f"{name} must be a non-empty list")
        for name in ("start", "end_before"):
            if name in params:
                cls._date_problem(problems, name, params[name])
        if "rate" in params and not is_node_ref(params["rate"]) and _num(params["rate"]) is None:
            problems.append(f"rate must be a finite number, got {params['rate']!r}")
        for name, floor in (("days_per_year", 1), ("min_forward_pairs", 1)):
            if name in params and not is_node_ref(params[name]):
                check_int_param(problems, name, params[name], ge=floor)
        structures = params.get("structures")
        if "structures" in params and not is_node_ref(structures):
            if not isinstance(structures, dict) or not structures:
                problems.append(f"structures must be a non-empty dict, got {structures!r}")
            else:
                for name, spec in structures.items():
                    problems += cls._structure_problems(name, spec, oi_available)
        return problems

    # -- intake ------------------------------------------------------------------------------

    def _blocks(self, rows):
        """Yield one :class:`_Day` per symbol-date, refusing a backwards date or a reappearing symbol."""
        p = self.params
        f, end = p["chain_fields"], _day(p["end_before"])
        oi_field = f.get("open_interest")
        sides = {**{v: "call" for v in p["call_values"]}, **{v: "put" for v in p["put_values"]}}
        closed, held, last_day, seen = set(), None, None, 0
        for index, row in enumerate(rows):
            seen += 1
            where = f"{self.key}: row {index}"
            symbol = row.get(f["symbol"])
            day, exp = _day(row.get(f["date"])), _day(row.get(f["expiration"]))
            if not isinstance(symbol, str) or not symbol or day is None or exp is None:
                raise ValueError(f"{where}: symbol, date and expiration must be a name and ISO dates")
            if day >= end:
                raise ValueError(f"{where}: date {day} is at or after end_before {end}")
            side = sides.get(row.get(f["right"]))
            strike = _num(row.get(f["strike"]))
            if side is None or strike is None or strike <= 0:
                raise ValueError(f"{where}: right {row.get(f['right'])!r} / strike {row.get(f['strike'])!r} unusable")
            if held is None or symbol != held.symbol or day != held.day:
                if held is not None:
                    yield held
                if held is not None and symbol == held.symbol and day < held.day:
                    raise ValueError(f"{where}: dates go backwards within {symbol!r} ({day} after {held.day}): "
                                     "the chain must arrive symbol by symbol with dates ascending")
                if held is not None and symbol != held.symbol:
                    closed.add(held.symbol)
                if symbol in closed:
                    raise ValueError(f"{where}: symbol {symbol!r} reappears after its rows ended")
                held = _Day(symbol, day)
            oi = _num(row.get(oi_field)) if oi_field else None
            held.add(exp, strike, side, _num(row.get(f["bid"])), _num(row.get(f["ask"])), oi,
                     _num(row.get(f["underlying"])))
        if not seen:
            raise ValueError(f"{self.key}: no chain rows")
        if held is not None:
            yield held

    # -- selection ---------------------------------------------------------------------------

    @staticmethod
    def _eligible(spec, exp):
        rule = spec.get("expiry_rule")
        if not rule:
            return True
        if "weekday" in rule and exp.weekday() != rule["weekday"]:
            return False
        if "day_of_month" in rule and not rule["day_of_month"][0] <= exp.day <= rule["day_of_month"][1]:
            return False
        return True

    def _expirations(self, spec, block, state):
        """The ``(expiration, reason)`` choices a structure takes on this day; a reason when none."""
        entry, day = spec["entry"], block.day
        if entry["kind"] == "days_before_expiry":
            picks = [e for e in sorted(block.expirations) if (e - day).days == entry["days"] and self._eligible(spec, e)]
            return picks, None
        if entry["kind"] == "after_expiry" and state["next"] is not None and day < state["next"]:
            return [], "wait"
        if entry["kind"] == "every_n":
            state["seen"] += 1
            if (state["seen"] - 1) % entry["n"] != 0:
                return [], "wait"
        rule = spec["expiry"]
        listed = [e for e in sorted(block.expirations) if (e - day).days >= rule["min_dte"] and self._eligible(spec, e)]
        near = [e for e in listed if abs((e - day).days - rule["dte"]) <= rule["tolerance"]]
        if not near:
            return [], "no_expiry"
        return [min(near, key=lambda e: (abs((e - day).days - rule["dte"]), e))], None

    def _book(self, block, exp):
        """The usable-quote book of one expiration, its forward solved; ``(book, reason)``."""
        p = self.params
        years = (exp - block.day).days / p.get("days_per_year", DEFAULT_DAYS_PER_YEAR)
        book = QuoteBook(years, p.get("iv_min", DEFAULT_IV_MIN), p.get("iv_max", DEFAULT_IV_MAX), p.get("rate", 0.0))
        for strike, sides in block.expirations[exp].items():
            for side, (bid, ask, _oi) in sides.items():
                if quote_state(bid, ask) in USABLE_STATES:
                    book.add(side, strike, bid, ask)
        if book.solve_forward(p.get("min_forward_pairs", 5)) is None:
            return book, "no_forward"
        return book, None

    @staticmethod
    def _nearest(candidates, target):
        """The candidate nearest ``target`` (ties to the lower), or ``None``."""
        return min(candidates, key=lambda k: (abs(k - target), k)) if candidates else None

    def _pick(self, kind_spec, right, book, block, exp, chosen):
        """The strike one leg selects, or ``None``."""
        kind = kind_spec["kind"]
        if kind == "atm":
            both = [k for k in book.quotes["call"] if k in book.quotes["put"]]
            return min(both, key=lambda k: (abs(math.log(k / book.forward)), k)) if both else None
        if kind == "max_oi":
            totals = {}
            for strike, sides in block.expirations[exp].items():
                values = [oi for (_b, _a, oi) in sides.values() if oi is not None]
                if values:
                    totals[strike] = sum(values)
            return min(totals, key=lambda k: (-totals[k], k)) if totals else None
        if kind == "mirror":
            return self._nearest(list(book.quotes[right]), 2.0 * block.spot() - chosen[kind_spec["of"]])
        out_of_money = [k for k in book.quotes[right] if (k < book.forward if right == "put" else k > book.forward)]
        scored = []
        for k in out_of_money:
            vol = book.iv(right, k)
            if vol is not None:
                scored.append((abs(abs(black76_delta(right, book.forward, k, vol, book.years, book.rate))
                                   - kind_spec["target"]), k))
        return min(scored)[1] if scored else None

    def _order(self, name, spec, block, exp):
        """``(order, None)`` for one structure on one expiration, else ``(None, reason)``."""
        spot = block.spot()
        if spot is None:
            return None, "no_underlying"
        book, reason = self._book(block, exp)
        if reason:
            return None, reason
        chosen = []
        for leg in spec["legs"]:
            strike = self._pick(leg["select"], leg["right"], book, block, exp, chosen)
            if strike is None:
                return None, "no_strike"
            chosen.append(strike)
        o = self.params["order_fields"]
        return {o["date"]: block.day.isoformat(), o["symbol"]: block.symbol, o["expiration"]: exp.isoformat(),
                o["strikes"]: chosen, o["structure"]: name, "spot": spot, "forward": book.forward,
                "dte": (exp - block.day).days, "leg_quantities": [leg["quantity"] for leg in spec["legs"]]}, None

    def run(self, ctx, inputs):
        """Read the chain once and emit every order its structures' rules select.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``records`` (REQUIRED): chain rows, symbol by symbol with dates ascending.

        Returns
        -------
        dict
            ``orders`` (in symbol, date, structure declaration order) and ``summary`` (``days``,
            ``before_start_days``, ``orders`` and ``refused`` by ``<structure>:<reason>``).

        Raises
        ------
        ValueError
            A row that cannot be read, on or after ``end_before``, a date going backwards within a symbol, a
            symbol that reappears, or an empty chain.
        """
        p, start = self.params, _day(self.params["start"])
        orders, refused, per_structure = [], Counter(), Counter()
        states, days, before_start = {}, 0, 0
        for block in self._blocks(inputs["records"]):
            days += 1
            if block.day < start:
                before_start += 1
                continue
            for name, spec in p["structures"].items():
                if spec.get("symbols") and block.symbol not in spec["symbols"]:
                    continue
                state = states.setdefault((block.symbol, name), {"next": None, "seen": 0})
                picks, why = self._expirations(spec, block, state)
                if why == "no_expiry":
                    refused[f"{name}:{why}"] += 1
                for exp in picks:
                    order, reason = self._order(name, spec, block, exp)
                    if order is None:
                        refused[f"{name}:{reason}"] += 1
                        continue
                    orders.append(order)
                    per_structure[name] += 1
                    state["next"] = exp
        summary = {"stage": "chain structure orders: " + ", ".join(p["structures"]), "days": days,
                   "before_start_days": before_start, "orders": dict(per_structure), "refused": dict(sorted(refused.items()))}
        self.log.info("%s: %d symbol-day(s), %d order(s)", self.key, days, len(orders))
        return {"orders": orders, "summary": summary}
