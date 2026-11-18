"""Close candidates: each open book may be closed at the current bid/ask instead of held to expiry (P2, improvement #4).

Opt-in through ``kelly.close_candidates = {"enabled": true, "price": "bid_ask", "min_dte": N}`` on a stage-one
local-search kelly method (:class:`~index_options.kelly_local.KellyLocalSearch`). Absent (or ``enabled: false``) every
record, order and number is the method's own, byte for byte: nothing below runs.

The model, one decision week ``D`` (every input is date-``D`` data; the fill is the next session ``D+``):

* **A held book is a group of one item.** For every open book ``h`` with at least ``min_dte`` calendar days left and a
  forecast on the week's menu for its (symbol, expiry), the program gains the group ``hold:<id>``: one item of
  capacity one, ``1`` = HOLD to expiry, ``0`` = CLOSE. Its payoff is the book's own remaining payoff, ``-mult x
  structure_payoff(legs, S)``, valued under the SAME forecast masses and the SAME shocked-spot scenarios as every
  candidate (so ``e_j`` "from the new forecast", and the held book carries the name's dispersion charge like any
  other position). It carries no reserve row: the reserve it ties up is already out of ``w0`` and is released only
  when the close actually fills (the next week sees it), a deliberately conservative treatment.
* **Close value.** Closing sells the long legs at the bid and buys back the short legs at the ask of date ``D``'s
  quote, less ``fee x contracts``: ``proceeds_h``. It does not depend on the scenario. Wealth in scenario ``s`` is
  ``w0 + sum_h [ proceeds_h + q_h (H_hs - proceeds_h) ] + new books``, with ``H_hs`` the hold payoff, so the item's
  value is the hold payoff against the close value and the scenario-free constant ``sum_h proceeds_h`` (the entry
  credits cancel) is folded into the utility's ``w0``. ``w0`` is the account's uncommitted capital as before: held
  books enter the scenario wealth through their pay-off to come, never through their reserve.
* **Execution.** A book the climb leaves at ``0`` is closed at the NEXT session's quotes (the entry's own rule: the
  decision never sees the fill): the long legs sell at that session's bid, the shorts buy at its ask, all-or-none
  on the side depth, fees per contract. A close that cannot be quoted or filled leaves the book held and says why.
  The realized book P&L is ``credit + proceeds - fees``; the shared ledger replays the exit at the same quotes
  (``close_rule: "exit"``) and the account refuses a mismatch, as it does for a settlement.

Refused beside it (named codes, :meth:`CloseCandidates.problems`): a hedge block (a closed book would orphan its
forward leg: the static hedge settles WITH its book, and a mid-life unwind of the forward is not modelled), a
calibration block, exposure limits, a sizer, the ADR-0318 edge weights (the held book's edge would be unweighted
while a candidate's is weighted), the multi-start search (its LP-rounded starts know no held book) and any split
adjustment of the study. Coverage: only a week that reaches the solve considers closes (a week that is cash for a
status such as ``no_eligible_candidates``, ``no_entry_fill_session`` or a shock/kelly risk status holds every book),
and a hold group is valued without a robustness radius (a kelly method carries rho 0, so candidates have none
either). The stage-one shock gate and the filled-account shock gate read the books as held (a close
never relaxes them within its week: conservative).
"""

import math

from dskit.pipeline.option_structures import structure_payoff
from dskit.pipeline.records import number_ok

__all__ = ["CloseCandidates"]


class CloseCandidates:
    """The close-candidate declaration and the week's held-book groups and closing fills."""

    KEY = "close_candidates"
    KEYS = frozenset({"enabled", "price", "min_dte"})
    PRICE = "bid_ask"
    #: Method blocks and kelly keys the feature cannot sit beside, by the code the refusal carries.
    BLOCKS = ("hedge", "calibration", "exposure_limits", "sizer")
    EDGE = ("accuracy", "dte_tilt", "name_spread")
    #: A hold group's id prefix.
    PREFIX = "hold:"

    @classmethod
    def declared(cls, kelly):
        """Whether a kelly block carries the key at all (well formed or not)."""
        return isinstance(kelly, dict) and cls.KEY in kelly

    @classmethod
    def well_formed(cls, kelly):
        """Whether the declaration, if present, is exactly ``{enabled: bool, price: "bid_ask", min_dte: int >= 1}``."""
        if not cls.declared(kelly):
            return True
        spec = kelly[cls.KEY]
        return (isinstance(spec, dict) and set(spec) == cls.KEYS and type(spec["enabled"]) is bool
                and spec["price"] == cls.PRICE and type(spec["min_dte"]) is int and spec["min_dte"] >= 1)

    @classmethod
    def spec(cls, kelly):
        """The validated declaration of an ENABLED block, else None (absent and ``enabled: false`` are the same)."""
        if not cls.declared(kelly) or not cls.well_formed(kelly) or not kelly[cls.KEY]["enabled"]:
            return None
        return dict(kelly[cls.KEY])

    @classmethod
    def problems(cls, method):
        """Return the refusal codes of an enabled declaration beside what it cannot combine with (empty: fine)."""
        kelly = method.get("kelly")
        if cls.spec(kelly) is None:
            return []
        codes = [f"close_candidates_with_{key}" for key in cls.BLOCKS if key in method]
        search = kelly.get("local_search")
        if not isinstance(search, dict) or "start" not in search or "multistart" in search:
            codes.append("close_candidates_needs_stage_one_local_search")
        if any(key in kelly for key in cls.EDGE):
            codes.append("close_candidates_with_edge_weights")
        if "joint_scenarios" in kelly:
            # The hold groups' scenario terms are the 17 basket moves; the sampled joint atoms cover candidates only.
            codes.append("close_candidates_with_joint_scenarios")
        return codes

    def __init__(self, study, spec):
        self.study, self.spec_ = study, dict(spec)
        if study.adjustments:
            raise ValueError("close_candidates_with_split_adjustment: a split-converted book is not closable")

    # --- the week: held-book groups ------------------------------------------------------------------------

    def _leg_quotes(self, book, day):
        """The ``(bid, ask)`` of every active leg of ``book`` on ``day`` (the usable rule), None when any is missing."""
        study = self.study
        node, symbol, expiry, factor = study._nodes[book["symbol"]], book["symbol"], book["expiry"], book["factor"]
        out = []
        for leg in book["legs"]:
            row = study._quotes[symbol].get((day, expiry, leg["right"], leg["strike"]))
            market = node._usable_quote(row, factor)
            if market is None:
                return None
            out.append((row, market))
        return out

    @staticmethod
    def _proceeds(book, markets, mult, fee):
        """Net cash of closing the whole book at ``markets`` (long legs at the bid, short legs at the ask), fees off."""
        gross = math.fsum(leg["sign"]*(bid if leg["sign"] > 0 else ask)*mult*leg["quantity"]
                          for leg, (bid, ask) in zip(book["legs"], markets, strict=True))
        return gross-fee*sum(leg["quantity"] for leg in book["legs"])

    def week(self, day, account, raw, mult, betas, moves, w0, wealth_lo):
        """Return the week's held-book groups, their scenario terms and the record.

        Parameters
        ----------
        day : str
            The decision date; every quote read is that date's.
        account : FundedOptionAccount
            The account after today's settlements and fills.
        raw : tuple
            ``(groups, metadata)`` of the week's menu BEFORE any filter, the source of each (symbol, expiry) forecast.
        mult : float
            The option multiplier.
        betas : dict
            Each name's move loading (a name without one cannot be shocked, so its books stay held).
        moves : list of float
            The move cells including the no-move cell ``0.``.
        w0, wealth_lo : float
            The kelly wealth and its floor: the folded constant must keep the floor below the base wealth.

        Returns
        -------
        dict
            ``groups`` (grid groups), ``meta`` (``{id, symbol}``), ``books`` (open-book ids, in group order),
            ``terms`` (``{move: {group id: [row]}}``), ``shift`` (the folded constant), ``record`` and ``program``.
        """
        from datetime import date as _date

        study = self.study
        fee = study.params["bridge"]["solver"]["fee_per_contract_usd"]
        index = {(m["symbol"], m["expiry"]): g for g, m in zip(*raw)}
        meta_of = {(m["symbol"], m["expiry"]): m for m in raw[1]}
        groups, meta, books, shift, candidates, skipped = [], [], [], 0., [], []
        terms = {move: {} for move in moves}
        for key in sorted(account.open):
            book = account.open[key]
            symbol, expiry = book["symbol"], book["expiry"]
            dte = (_date.fromisoformat(expiry)-_date.fromisoformat(day)).days
            reason = None
            if dte < self.spec_["min_dte"]:
                reason = "below_min_dte"
            elif symbol not in betas:
                reason = "no_beta"
            elif (symbol, expiry) not in index:
                reason = "no_forecast_for_expiry"
            elif not math.isclose(meta_of[symbol, expiry]["factor"], book["factor"], rel_tol=1e-9):
                reason = "basis_changed"
            else:
                quoted = self._leg_quotes(book, day)
                if quoted is None:
                    reason = "no_close_quote"
            if reason is not None:
                skipped.append({"id": key, "reason": reason})
                continue
            group, info = index[symbol, expiry], meta_of[symbol, expiry]
            markets = [market for _row, market in quoted]
            proceeds = self._proceeds(book, markets, mult, fee)
            legs = [(leg["right"], leg["sign"]*leg["quantity"]) for leg in book["legs"]]
            strikes = [leg["strike"] for leg in book["legs"]]

            def loss(spot, legs=legs, strikes=strikes):
                return -mult*structure_payoff(legs, spot, strikes)
            credit = -proceeds                         # item value = hold payoff - close value (the credits cancel)
            hold_id = self.PREFIX+key
            rows = [[loss(s)] for s in group["grid"]]
            groups.append({"id": hold_id, "grid": list(group["grid"]), "masses": list(group["masses"]),
                           "radius": 0., "scale": group["scale"], "credit": [credit], "losses": rows,
                           "capacity": [1], "exclusive": [], "reserve_rows": [[0.]], "linear_rows": []})
            meta.append({"id": hold_id, "symbol": symbol})
            books.append(key)
            for move in moves:
                spot = max(0., info["spot"]*(1.+betas[symbol]*move))
                terms[move][hold_id] = [loss(spot)-credit]
            shift += proceeds+book["credit"]
            expected = -math.fsum(m*r[0] for m, r in zip(group["masses"], rows))   # mult x E[payoff] under the forecast
            candidates.append({"id": key, "symbol": symbol, "expiry": expiry, "dte": dte,
                               "contracts": sum(leg["quantity"] for leg in book["legs"]),
                               "close_proceeds": proceeds, "hold_expected": expected,
                               "close_minus_hold": proceeds-expected})
        record = {"min_dte": self.spec_["min_dte"], "candidates": candidates, "skipped": skipped, "shift": shift,
                  "status": "candidates" if groups else "none"}
        if groups and not wealth_lo < w0+shift:
            groups, meta, books, terms = [], [], [], {move: {} for move in moves}
            record["status"] = "base_wealth_below_floor"
        return {"groups": groups, "meta": meta, "books": books, "terms": terms,
                "shift": shift if groups else 0., "record": record}

    # --- the fill: next session's quotes ---------------------------------------------------------------------

    def fill(self, book, day):
        """Price closing ``book`` at ``day``'s quotes: ``(fields, None)`` or ``(None, reason)``; nothing is mutated.

        All-or-none on the closing side's depth (a long leg sells into the bid size, a short leg buys from the ask
        size), the entry's quote-usability rule, fees per contract. ``fields`` carry ``pnl`` (credit + proceeds -
        fees, the shared ledger's ``premium + close cash - open fees - close fees``) and the audit.
        """
        study = self.study
        symbol, expiry, factor = book["symbol"], book["expiry"], book["factor"]
        if day >= expiry or not math.isclose(study._factors[symbol].get(day, math.nan), factor, rel_tol=1e-9):
            return None, "close_basis_or_session"
        node = study._nodes[symbol]
        mult = node.params["solver"]["multiplier"]
        fee = node.params["solver"]["fee_per_contract_usd"]
        quoted = self._leg_quotes(book, day)
        if quoted is None:
            return None, "missing_close_quote"
        stress = 0.
        for leg, (row, (bid, ask)) in zip(book["legs"], quoted, strict=True):
            depth = row["bid_size" if leg["sign"] > 0 else "ask_size"]
            if not number_ok(depth) or depth < leg["quantity"]:
                return None, "insufficient_close_size"
            stress += (ask-bid)*mult*leg["quantity"]*node.params["stress_spread_fraction"]
        markets = [market for _row, market in quoted]
        proceeds = self._proceeds(book, markets, mult, fee)
        fees = fee*sum(leg["quantity"] for leg in book["legs"])
        if book["credit"]+proceeds < -book["reserve"]-fees-1e-5:
            return None, "close_exceeds_reserve"   # the account would refuse it: the book stays held
        return {"pnl": book["credit"]+proceeds, "close_proceeds": proceeds, "close_fees": fees,
                "close_fill_prices": [bid if leg["sign"] > 0 else ask
                                      for leg, (bid, ask) in zip(book["legs"], markets, strict=True)],
                "stress_extra": book["stress_extra"]+stress}, None
