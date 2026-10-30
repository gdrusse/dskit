"""Tax lots from trade rows, and lots joined to a positions snapshot (ADR-0279, research A0004).

A broker's positions carry one aggregate row per holding: shares, average price,
value. The tax of selling depends on each purchase instead: its date decides short
or long term, its price the gain. This module rebuilds those purchases and nothing
else; where the trade rows come from and which tax applies are declared in the
document.

:class:`TaxLots` (role ``transform``) replays trade rows (one per fill: a date, a
signed share quantity, a price and optionally the signed cash) per declared
``key_fields`` (an account and a symbol, say) up to ``as_of``. A buy opens a lot
whose basis is the cash it paid (``-amount``, fees included) when ``amount_field``
is declared and the row's amount is not null, else ``quantity x price`` (a fallback the
summary counts under ``amount_fallbacks``; a row without the declared key refuses). A buy
whose amount is exactly 0.0 moved no cash (a transfer in, a gift, an award): it refuses
unless ``allow_zero_cash`` is declared, when it too is valued at ``quantity x price`` and
counted under ``zero_cash_buys``; a positive amount on a buy refuses either way (a sign
error), and so does a zero-cash buy at price 0.0 (nothing values it). A sell
consumes open lots in the order the declared ``method`` gives (:data:`LOT_METHODS`:
``fifo``, the rule of 26 CFR 1.1012-1(c)(1) when no lot is identified); the last
lot it touches is split pro rata, keeping its basis per share. Trades are replayed
by date, then by the ``order_fields`` in turn (an execution time, then a transaction
id, so a same-day buy precedes the sell that closes it however the ids run) or else in
input order. A trade dated ON ``as_of`` is replayed (the snapshot of that day holds that
day's fills); only a later one is counted, not replayed.

A sell beyond the open quantity means the history is incomplete (a purchase before
the trades begin, a transfer in) or a short sale, which is out of scope: the node
refuses by trade and key, unless ``allow_incomplete`` is declared, when the key is
listed ``incomplete`` in the summary and ALL its lots are withheld (a later buy
included: once the history is known to be wrong, none of the key's lots is trusted).
Each open lot is one row: the key fields, ``lot_id`` (:func:`format_lot_id`:
``<key...>:<acquired>:<n>``, ``n`` the 1-based count of that day's buys for the key,
so ids are stable as later trades arrive), ``acquired``, ``quantity``, ``basis`` and
``basis_per_share``.

:class:`LotPositions` (role ``transform``) joins those lots to a positions snapshot
on ``key_fields`` and turns each lot into a position row the liquidation kinds read
unchanged: the snapshot's ``quantity_field`` holds the lot's shares,
``value_field`` the lot's share of the position's value (``lot quantity x
position value / position quantity``), ``basis_field`` the lot's basis; ``lot_id``,
``acquired`` and the declared ``carry_fields`` ride along. Every snapshot position
must be reconciled share for share: its lots' total quantity within ``tolerance``
shares (default 0; differences below :data:`QUANTITY_RTOL` of the position are
float noise) of the snapshot's. A position whose lots disagree, or which has no
lots, refuses unless ``allow_incomplete`` is declared, when its aggregate row passes
through with ``lot_id`` ``<key...>:aggregate`` and a null ``acquired`` (so the
liquidation's ``default_holding`` applies, as before lots existed) and is listed
under ``fallback``. Lots of a key the snapshot does not carry (a holding the
document filtered out) are counted under ``unpositioned`` and not emitted. The
summary reports, per reconciled position, ``basis_gap`` = lot basis - position
basis: zero when the declared method matches the broker's books, and ``basis_gap_max``,
the largest |gap|; a gap beyond float noise (:data:`BASIS_RTOL` of the position's basis)
logs one warning naming the largest.

Limits, disclosed rather than modelled. Only long lots exist: a short sale and its
cover are an oversell and a buy. Splits, spin-offs, transfers and reinvestments the
trade rows do not carry are not modelled; each shows as a reconciliation failure,
never as a silent number. Average-cost basis (a broker default for some funds) is
not a method here yet: such an account shows as a basis gap. Wash sales are not
modelled either (a loss disallowed by a repurchase within 30 days raises the new lot's
basis and tacks the old holding period onto it), so a wash-sale account also shows as a
basis gap, and the lot's holding period here starts at its purchase (at the row's date for
a zero-cash buy: a transfer between one's own accounts or a gift keeps the earlier holding
period and an inheritance is always long-term, so such a lot may read short-term when it is
long-term, its tax overstated). Its basis errs the other way: quantity x price is the value
on the day the shares arrived, not the basis they carried over, so shares transferred in
after being bought long ago at a lower price show an overstated basis, and the embedded
gain and the tax to sell are understated; ``basis_gap`` shows it only when the broker's
books carry the true basis.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

import abc
import math
from collections import namedtuple
from datetime import date

from dskit.pipeline.binary_curve import fields_key, name_ok, row_key
from dskit.pipeline.decision_outcomes import check_field_list_param, check_key_fields_param
from dskit.pipeline.document import date_problem
from dskit.pipeline.liquidation import check_name_params, require_number, require_rows
from dskit.pipeline.node import Node, check_number_param, reject_unknown_params

__all__ = [
    "AGGREGATE_LOT", "ALLOW_ZERO_CASH_DEFAULT", "BASIS_RTOL", "LOT_FIELDS", "LOT_METHODS",
    "QUANTITY_RTOL", "FifoOrder", "LotOrder", "LotPositions", "TaxLots", "format_lot_id",
]

#: The fields :class:`TaxLots` writes on every lot row, beside the key fields.
LOT_FIELDS = ("lot_id", "acquired", "quantity", "basis", "basis_per_share")
#: The ``lot_id`` suffix of a position that passes through unlotted.
AGGREGATE_LOT = "aggregate"
#: Share quantities closer than this fraction of the trade or position are float noise, not
#: shares: 0.1 + 0.2 fractional shares sold as 0.3 close both lots and oversell nothing.
QUANTITY_RTOL = 1e-9
#: A basis gap smaller than this fraction of the position's basis is float noise, not a lot
#: method that differs from the broker's: it raises no warning.
BASIS_RTOL = 1e-9
#: What ``allow_zero_cash`` means when a document does not declare it: a buy that moved no cash
#: refuses, so a transfer in or a gift is never silently valued at quantity x price.
ALLOW_ZERO_CASH_DEFAULT = False

#: One parsed trade row: the replay order is ``(day, order, index)``, ``estimated`` marks a buy
#: whose cash was declared but null, so its basis is quantity x price; ``zero_cash`` marks a buy
#: whose cash was exactly 0.0 (allowed by ``allow_zero_cash``), valued the same way.
_Trade = namedtuple("_Trade", "day order index key quantity basis estimated zero_cash")

_JOIN_FIELDS = ("lot_id", "acquired")
_TOLERANCE_BOUNDS = (0.0, 1.0)


def _label(key):
    """Return a key's values joined by ``:``, the spelling a lot id and a message use."""
    return ":".join(str(part) for part in key)


def format_lot_id(key, suffix):
    """Return ``<key values joined by ':'>:<suffix>``, the one spelling of a lot id.

    ``suffix`` is ``<acquired>:<n>`` for a lot and :data:`AGGREGATE_LOT` for a position
    that passes through unlotted. ``format_lot_id(("A1", "VTI"), "2024-01-10:1")`` is
    ``"A1:VTI:2024-01-10:1"``.
    """
    return f"{_label(key)}:{suffix}"


class _Lot:
    """One open lot: mutable while its trades replay."""

    __slots__ = ("lot_id", "acquired", "quantity", "basis")

    def __init__(self, lot_id, acquired, quantity, basis):
        self.lot_id, self.acquired = lot_id, acquired
        self.quantity, self.basis = quantity, basis


class LotOrder(abc.ABC):
    """Which open lots a sell consumes first: the one hook a lot-matching method overrides.

    A method is one subclass and one :data:`LOT_METHODS` entry; the replay, the split of
    the last lot and the refusals are :class:`TaxLots`'s and never change.
    """

    @abc.abstractmethod
    def order(self, lots):
        """Return the open ``lots`` (in the order they were opened) in consumption order.

        Each lot exposes ``acquired``, ``quantity`` and ``basis``.
        """


class FifoOrder(LotOrder):
    """First in, first out: the earliest lot first (26 CFR 1.1012-1(c)(1))."""

    def order(self, lots):
        """Return the lots as opened: the earliest first."""
        return list(lots)


#: Lot-matching methods by the name a document declares.
LOT_METHODS = {"fifo": FifoOrder}


def _bool_param(problems, params, name):
    """Append a problem when ``params[name]`` is present and not a bool."""
    if name in params and not isinstance(params[name], bool):
        problems.append(f"{name} must be a bool, got {params[name]!r}")


def _missing(problems, params, required):
    """Append the one missing-required problem, naming every absent param."""
    missing = sorted(set(required) - set(params))
    if missing:
        problems.append(f"missing required params {missing}")


class TaxLots(Node):
    """Open lots per key from trade rows, by a declared lot method.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``method`` (a :data:`LOT_METHODS` name, REQUIRED); ``key_fields``
        (non-empty list of distinct field names, REQUIRED): what one holding is;
        ``date_field`` / ``quantity_field`` / ``price_field`` (field names,
        REQUIRED): the trade date (ISO), the signed shares (+ bought, - sold) and
        the price a share; ``amount_field`` (field name, optional): the signed cash
        (negative = paid), a buy's basis when its value is not null (the key must be on
        EVERY row); ``order_fields`` (non-empty list of distinct field names, optional):
        orders the trades of a day, in turn (each a str or int on every trade, one type per
        field: an execution time, then a transaction id);
        ``allow_incomplete`` (bool, REQUIRED): withhold an oversold key's lots
        instead of refusing; ``allow_zero_cash`` (bool, optional, default
        :data:`ALLOW_ZERO_CASH_DEFAULT`): value a buy whose amount is exactly 0.0 (a
        transfer in, a gift, an award) at quantity x price instead of refusing it, counted
        under the summary's ``zero_cash_buys`` (null without ``amount_field``);
        ``as_of`` (ISO date, optional): the last trade date replayed, absent means the
        run's as-of date.

    Inputs
    ------
    ``records`` (REQUIRED)
        Trade rows carrying the declared fields.

    Examples
    --------
    Two buys and a partial sell::

        node = TaxLots("lots", {"method": "fifo", "key_fields": ["account", "symbol"],
            "date_field": "trade_date", "quantity_field": "quantity", "price_field": "price",
            "allow_incomplete": False})
        out = node.run(ctx, {"records": trades})
        out["records"][0]["lot_id"]   # "A1:VTI:2024-01-10:1"
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = (
        "as_of", "method", "key_fields", "date_field", "quantity_field", "price_field",
        "amount_field", "order_fields", "allow_incomplete", "allow_zero_cash",
    )
    _REQUIRED = (
        "method", "key_fields", "date_field", "quantity_field", "price_field", "allow_incomplete",
    )

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _missing(problems, params, cls._REQUIRED)
        if "as_of" in params and date_problem(params["as_of"]):
            problems.append(f"as_of must be an ISO date, got {params['as_of']!r}")
        method = params.get("method")
        if "method" in params and not (isinstance(method, str) and method in LOT_METHODS):
            problems.append(f"method must be one of {sorted(LOT_METHODS)}, got {method!r}")
        if check_key_fields_param(problems, params):
            taken = sorted(set(params["key_fields"]) & set(LOT_FIELDS))
            if taken:
                problems.append(f"key_fields may not name an output field: {taken}")
        check_name_params(problems, params, (
            "date_field", "quantity_field", "price_field", "amount_field"))
        check_field_list_param(problems, params, "order_fields")
        _bool_param(problems, params, "allow_incomplete")
        _bool_param(problems, params, "allow_zero_cash")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _as_of(self, ctx):
        """Return the last trade date replayed: the param, else the run's as-of."""
        value = self.params.get("as_of", ctx.asof)
        if date_problem(value):
            raise ValueError(f"{self.key}: no usable as_of date (param or run), got {value!r}")
        return date.fromisoformat(value)

    def _order(self, index, row):
        """Return the row's values of the declared ``order_fields``, each refused by name."""
        values = []
        for field in self.params.get("order_fields", ()):
            value, problem = row_key(row.get(field))
            if problem is not None:
                raise ValueError(
                    f"{self.key}: trade {index} field {field!r} is unusable: {problem}")
            values.append(value)
        return tuple(values)

    def _basis(self, index, row, key, quantity, price):
        """Return ``(basis, estimated, zero_cash)`` of a buy.

        The basis is its cash; when the declared amount is null it is quantity x price
        (``estimated``); when the amount is exactly 0.0 and ``allow_zero_cash`` is declared
        true it is quantity x price too (``zero_cash``), unless the price is 0.0 and nothing
        values the shares. Any other amount that is not negative refuses.
        """
        field = self.params.get("amount_field")
        if field is None or row[field] is None:
            return quantity * price, field is not None, False
        amount = require_number(self.key, "trade", index, row, field)
        if amount == 0.0 and self.params.get("allow_zero_cash", ALLOW_ZERO_CASH_DEFAULT):
            # shares that moved no cash (a transfer in, a gift, an award): the books value
            # them at quantity x price, so a price of 0.0 would open a lot worth nothing
            if price == 0.0:
                raise ValueError(
                    f"{self.key}: trade {index} buys {quantity!r} {_label(key)!r} with no cash "
                    f"and a {self.params['price_field']!r} of 0.0; nothing values it")
            return quantity * price, False, True
        if amount >= 0.0:
            # a zero amount names the param that would admit it, unless the price is 0.0 too
            # (the param would only refuse again: nothing values the shares); a positive amount
            # is a sign error that no param admits
            hint = ""
            if amount == 0.0 and price == 0.0:
                hint = f"; its {self.params['price_field']!r} is 0.0 too, so nothing values it"
            elif amount == 0.0:
                hint = ("; allow_zero_cash admits a buy that moved no cash (a transfer in, a gift "
                        "or an award) at quantity x price")
            raise ValueError(
                f"{self.key}: trade {index} buys {quantity!r} {_label(key)!r} but its "
                f"{field!r} is {amount!r}; a buy pays cash, so its amount is negative{hint}")
        return -amount, False, False

    def _trade(self, index, row):
        """Return one parsed :class:`_Trade`, every field refused by name."""
        p = self.params
        key, problem = fields_key(row, p["key_fields"])
        if problem is not None:
            raise ValueError(f"{self.key}: trade {index} key {p['key_fields']!r} is unusable: {problem}")
        day = row.get(p["date_field"])
        if date_problem(day):
            raise ValueError(
                f"{self.key}: trade {index} field {p['date_field']!r} is not an ISO date, got {day!r}")
        quantity = require_number(self.key, "trade", index, row, p["quantity_field"])
        if quantity == 0.0:
            raise ValueError(
                f"{self.key}: trade {index} field {p['quantity_field']!r} is zero; a trade moves shares")
        price = require_number(self.key, "trade", index, row, p["price_field"])
        if price < 0.0:
            raise ValueError(
                f"{self.key}: trade {index} field {p['price_field']!r} is negative, got {price!r}")
        field = p.get("amount_field")
        if field is not None and field not in row:
            raise ValueError(
                f"{self.key}: trade {index} has no {field!r} key; amount_field declares it on "
                "every trade (a null amount falls back to quantity x price, an absent key is "
                "a misspelt field)")
        basis, estimated, zero_cash = (self._basis(index, row, key, quantity, price)
                                       if quantity > 0.0 else (None, False, False))
        return _Trade(date.fromisoformat(day), self._order(index, row), index, key, quantity,
                      basis, estimated, zero_cash)

    def _ordered(self, records, as_of):
        """Return ``(trades, after)``: the trades up to ``as_of`` in replay order, and the rest's count."""
        trades, after = [], 0
        for index, row in enumerate(records):
            trade = self._trade(index, row)
            if trade.day > as_of:
                after += 1
                continue
            trades.append(trade)
        for position, field in enumerate(self.params.get("order_fields", ())):
            kinds = sorted({type(trade.order[position]).__name__ for trade in trades})
            if len(kinds) > 1:
                raise ValueError(
                    f"{self.key}: field {field!r} values mix types {kinds}; "
                    "one order field orders every trade of a day")
        trades.sort(key=lambda trade: (trade.day, trade.order, trade.index))
        return trades, after

    @staticmethod
    def _sell(sold, book, order):
        """Consume ``sold`` shares from the book's lots in method order; return the shortfall."""
        noise = QUANTITY_RTOL * sold
        need = sold
        for lot in order.order(book["lots"]):
            if need <= noise:
                break
            take = min(lot.quantity, need)
            lot.basis *= (lot.quantity - take) / lot.quantity
            lot.quantity -= take
            need -= take
        book["lots"] = [lot for lot in book["lots"] if lot.quantity > noise]
        return need if need > noise else 0.0

    def run(self, ctx, inputs):
        """Replay every trade up to ``as_of``; return the open lots and a summary."""
        records = inputs.get("records")
        require_rows(self.key, records, ())
        as_of = self._as_of(ctx)
        order = LOT_METHODS[self.params["method"]]()
        trades, after = self._ordered(records, as_of)
        books, ignored = {}, 0
        for trade in trades:
            day, index, key, quantity = trade.day, trade.index, trade.key, trade.quantity
            book = books.setdefault(key, {"lots": [], "days": {}, "incomplete": None})
            if book["incomplete"] is not None:
                ignored += 1
                continue
            if quantity > 0.0:
                n = book["days"][day] = book["days"].get(day, 0) + 1
                lot_id = format_lot_id(key, f"{day.isoformat()}:{n}")
                book["lots"].append(_Lot(lot_id, day.isoformat(), quantity, trade.basis))
                continue
            held = math.fsum(lot.quantity for lot in book["lots"])
            short = self._sell(-quantity, book, order)
            if short:
                reason = (f"sells {-quantity!r} on {day.isoformat()} with {round(held, 9)!r} open "
                          f"(oversold by {round(short, 9)!r})")
                if not self.params["allow_incomplete"]:
                    raise ValueError(
                        f"{self.key}: trade {index} sells {-quantity!r} of {_label(key)!r} on "
                        f"{day.isoformat()} but {round(held, 9)!r} share(s) are open (oversold by "
                        f"{round(short, 9)!r}): history before the trades' start, a transfer in "
                        "or a short sale; declare allow_incomplete to withhold the key's lots "
                        "instead")
                book["incomplete"], book["lots"] = reason, []
        out, incomplete, closed = [], [], 0
        key_fields = self.params["key_fields"]
        for key in sorted(books, key=repr):
            book = books[key]
            named = dict(zip(key_fields, key))
            if book["incomplete"] is not None:
                incomplete.append({**named, "reason": book["incomplete"]})
                continue
            if not book["lots"]:
                closed += 1
            for lot in book["lots"]:
                out.append({**named, "lot_id": lot.lot_id, "acquired": lot.acquired,
                            "quantity": lot.quantity, "basis": lot.basis,
                            "basis_per_share": lot.basis / lot.quantity})
        keys = sum(1 for book in books.values() if book["incomplete"] is None and book["lots"])
        # both counts are n/a (None) without an amount_field: no buy has a cash to be null or zero
        declared = "amount_field" in self.params
        fallbacks = sum(trade.estimated for trade in trades) if declared else None
        zero_cash = sum(trade.zero_cash for trade in trades) if declared else None
        self.log.info("%s: %d lot(s) over %d key(s), %d incomplete, %s buy(s) on quantity x price "
                      "(null amount), %s zero-cash buy(s)",
                      self.key, len(out), keys, len(incomplete), fallbacks, zero_cash)
        return {"records": out, "summary": {
            "stage": "tax lots", "method": self.params["method"], "as_of": as_of.isoformat(),
            "trades": len(trades), "after_as_of": after, "lots": len(out), "keys": keys,
            "closed": closed, "incomplete": incomplete, "ignored": ignored,
            "amount_fallbacks": fallbacks, "zero_cash_buys": zero_cash}}


class LotPositions(Node):
    """Each open lot as a position row, reconciled share for share against a snapshot.

    Role ``transform``. See the module docstring for the rule.

    Parameters
    ----------
    params : dict
        ``key_fields`` (non-empty list of distinct field names, REQUIRED): what joins a
        lot to its position, carried by both inputs; ``quantity_field`` /
        ``value_field`` / ``basis_field`` (field names, REQUIRED): the snapshot's
        shares, value and basis, which each output row restates for its lot;
        ``carry_fields`` (list of distinct field names, optional): snapshot fields
        copied onto every row (a day, an asset type); ``tolerance`` (number in
        ``[0, 1]`` shares, default 0): the reconciliation's allowance;
        ``allow_incomplete`` (bool, REQUIRED): pass an unreconciled position through
        as its aggregate row instead of refusing.

    Inputs
    ------
    ``lots`` (REQUIRED)
        :class:`TaxLots` rows.
    ``positions`` (REQUIRED)
        One snapshot: one row per key, each a long holding (quantity above zero).

    Examples
    --------
    Value each lot at the position's price::

        node = LotPositions("lot_positions", {"key_fields": ["account", "symbol"],
            "quantity_field": "long_quantity", "value_field": "market_value",
            "basis_field": "cost_basis", "allow_incomplete": False})
        out = node.run(ctx, {"lots": lots, "positions": snapshot})
        out["records"][0]["acquired"]   # "2024-01-10"
    """

    role = "transform"
    outputs = ("records", "summary")

    _PARAMS = (
        "key_fields", "quantity_field", "value_field", "basis_field", "carry_fields",
        "tolerance", "allow_incomplete",
    )
    _REQUIRED = ("key_fields", "quantity_field", "value_field", "basis_field", "allow_incomplete")
    _AMOUNT_FIELDS = ("quantity_field", "value_field", "basis_field")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        _missing(problems, params, cls._REQUIRED)
        keys = []
        if check_key_fields_param(problems, params):
            keys = params["key_fields"]
            taken = sorted(set(keys) & set(_JOIN_FIELDS))
            if taken:
                problems.append(f"key_fields may not name an output field: {taken}")
        check_name_params(problems, params, cls._AMOUNT_FIELDS)
        amounts = [params[n] for n in cls._AMOUNT_FIELDS if n in params and name_ok(params[n])]
        for name in cls._AMOUNT_FIELDS:
            if name in params and params[name] in keys:
                problems.append(f"{name} may not be a key field, got {params[name]!r}")
        if "carry_fields" in params:
            carry = params["carry_fields"]
            if (not isinstance(carry, list) or not all(name_ok(c) for c in carry)
                    or len(set(carry)) != len(carry)):
                problems.append(
                    f"carry_fields must be a list of distinct field names, got {carry!r}")
            else:
                taken = sorted(set(carry) & set([*keys, *_JOIN_FIELDS, *amounts]))
                if taken:
                    problems.append(f"carry_fields may not name a key or output field: {taken}")
        if "tolerance" in params:
            check_number_param(problems, "tolerance", params["tolerance"],
                               lower=_TOLERANCE_BOUNDS[0], upper=_TOLERANCE_BOUNDS[1])
        _bool_param(problems, params, "allow_incomplete")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def _key(self, what, index, row):
        """Return the row's key tuple; refuse a non-mapping row or an unusable key."""
        if not isinstance(row, dict):
            raise ValueError(f"{self.key}: {what} {index} must be a mapping, got {type(row).__name__}")
        key, problem = fields_key(row, self.params["key_fields"])
        if problem is not None:
            raise ValueError(
                f"{self.key}: {what} {index} key {self.params['key_fields']!r} is unusable: {problem}")
        return key

    def _amount(self, what, index, row, field, *, positive):
        """Return ``row[field]`` as a float above zero (``positive``) or at or above it."""
        value = require_number(self.key, what, index, row, field)
        if value < 0.0 or (positive and value == 0.0):
            raise ValueError(
                f"{self.key}: {what} {index} field {field!r} must be "
                f"{'positive' if positive else 'non-negative'}, got {value!r}; a lot joins a "
                "long holding")
        return value

    def _lots(self, lots):
        """Return ``{key: [lot, ...]}`` in input order, each lot field refused by name."""
        if not isinstance(lots, list):
            raise ValueError(f"{self.key}: lots must be a list")
        grouped = {}
        for index, row in enumerate(lots):
            key = self._key("lot", index, row)
            if not name_ok(row.get("lot_id")):
                raise ValueError(
                    f"{self.key}: lot {index} field 'lot_id' is not a non-empty string, "
                    f"got {row.get('lot_id')!r}")
            if date_problem(row.get("acquired")):
                raise ValueError(
                    f"{self.key}: lot {index} field 'acquired' is not an ISO date, "
                    f"got {row.get('acquired')!r}")
            grouped.setdefault(key, []).append({
                "lot_id": row["lot_id"], "acquired": row["acquired"],
                "quantity": self._amount("lot", index, row, "quantity", positive=True),
                "basis": self._amount("lot", index, row, "basis", positive=False)})
        return grouped

    def _positions(self, positions):
        """Return ``[(index, key, row, quantity, value, basis, carried)]``, one per key."""
        if not isinstance(positions, list):
            raise ValueError(f"{self.key}: positions must be a list")
        p, seen, out = self.params, set(), []
        for index, row in enumerate(positions):
            key = self._key("position", index, row)
            if key in seen:
                raise ValueError(
                    f"{self.key}: positions carry {key!r} twice; one snapshot carries a "
                    "position once")
            seen.add(key)
            quantity = self._amount("position", index, row, p["quantity_field"], positive=True)
            value = require_number(self.key, "position", index, row, p["value_field"])
            basis = require_number(self.key, "position", index, row, p["basis_field"])
            carried = {}
            for name in p.get("carry_fields", ()):
                if name not in row:
                    raise ValueError(f"{self.key}: position {index} has no carry field {name!r}")
                carried[name] = row[name]
            out.append((index, key, quantity, value, basis, carried))
        return out

    def _unreconciled(self, index, key, quantity, lots):
        """Return why ``lots`` do not reconcile with ``quantity`` shares, or None when they do."""
        tolerance = float(self.params.get("tolerance", 0.0))
        if not lots:
            return "no lots", f"position {index} {key!r} has no lots"
        total = math.fsum(lot["quantity"] for lot in lots)
        difference = total - quantity
        if abs(difference) <= tolerance + QUANTITY_RTOL * quantity:
            return None
        return (f"lots hold {round(total, 9)!r} of {quantity!r} share(s)",
                f"position {index} {key!r} holds {quantity!r} share(s) but its lots hold "
                f"{round(total, 9)!r} (difference {round(difference, 9)!r}, tolerance "
                f"{tolerance!r})")

    def _warn_basis_gaps(self, gapped, reconciled):
        """Log one warning naming the largest of the ``gapped`` ``(|gap|, key)`` pairs, if any."""
        if not gapped:
            return
        size, key = max(gapped, key=lambda pair: pair[0])
        self.log.warning(
            "%s: %d of %d reconciled position(s) differ from the broker's cost basis; max "
            "|basis_gap| %s on %r: the account may use another lot method (average cost, "
            "specific lots) or a wash sale adjusted the basis",
            self.key, len(gapped), reconciled, size, key)

    def run(self, ctx, inputs):
        """Return one row per lot (or per unreconciled position, when allowed) and a summary."""
        p = self.params
        grouped = self._lots(inputs.get("lots"))
        positions = self._positions(inputs.get("positions"))
        out, fallback, reconciled, gapped = [], [], [], []
        for index, key, quantity, value, basis, carried in positions:
            named = dict(zip(p["key_fields"], key))
            lots = grouped.get(key, [])
            problem = self._unreconciled(index, key, quantity, lots)
            if problem is not None:
                reason, message = problem
                if not p["allow_incomplete"]:
                    raise ValueError(
                        f"{self.key}: {message}: the lot history is incomplete (a purchase "
                        "before the trades' start, a transfer in, a split or a reinvestment "
                        "the pull did not cover); declare allow_incomplete to fall back to the "
                        "aggregate row")
                fallback.append({**named, "reason": reason})
                out.append({**named, **carried, "lot_id": format_lot_id(key, AGGREGATE_LOT),
                            "acquired": None, p["quantity_field"]: quantity,
                            p["value_field"]: value, p["basis_field"]: basis})
                continue
            price = value / quantity
            lot_basis = math.fsum(lot["basis"] for lot in lots)
            gap = lot_basis - basis
            reconciled.append({**named, "lots": len(lots), "quantity": quantity,
                               "lot_basis": lot_basis, "position_basis": basis,
                               "basis_gap": gap})
            if abs(gap) > BASIS_RTOL * abs(basis):
                gapped.append((abs(gap), key))
            for lot in lots:
                out.append({**named, **carried, "lot_id": lot["lot_id"],
                            "acquired": lot["acquired"], p["quantity_field"]: lot["quantity"],
                            p["value_field"]: lot["quantity"] * price,
                            p["basis_field"]: lot["basis"]})
        positioned = {key for _, key, *_ in positions}
        unpositioned = [{**dict(zip(p["key_fields"], key)), "lots": len(grouped[key])}
                        for key in sorted(set(grouped) - positioned, key=repr)]
        self.log.info("%s: %d row(s) from %d position(s), %d fallback, %d unpositioned",
                      self.key, len(out), len(positions), len(fallback), len(unpositioned))
        self._warn_basis_gaps(gapped, len(reconciled))
        return {"records": out, "summary": {
            "stage": "lot positions", "positions": len(positions), "lots": len(out),
            "fallback": fallback, "unpositioned": unpositioned, "reconciled": reconciled,
            "basis_gap_max": max((abs(r["basis_gap"]) for r in reconciled), default=None)}}
