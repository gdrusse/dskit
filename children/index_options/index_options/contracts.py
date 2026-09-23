"""Exact, synthetic European cash-index contracts and intact condor cashflows.

This is domain validation, not a pricing, ingestion or execution engine.
Immutable snapshots prevent caller mutation from changing a validated position.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import re
from types import MappingProxyType

from dskit.onboarding.base import parse_utc
from dskit.pipeline.node import check_int_param, reject_unknown_params

__all__ = ["CashIndexContract", "DefinedRiskCondor", "leg_intrinsic"]

_SCHEMA = "index-options-synthetic-v1"
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_COMMON = (
    "schema_version", "corpus_id", "row_version", "provenance",
    "effective_at", "known_at", "known_at_basis",
)


def _decimal(value, name):
    """Read a finite base-ten option amount without float coercion."""
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError(f"{name} must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite decimal string") from exc
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def _integer(value, name, minimum):
    """Apply the shared bound, with the domain's stricter JSON integer type."""
    problems = []
    check_int_param(problems, name, value, ge=minimum)
    if not isinstance(value, int):
        problems.append(f"{name} must use an integer, not a float or string")
    if problems:
        raise ValueError("; ".join(problems))
    return value


def _instant(value, name):
    """Require a qualified instant before using the shared UTC parser."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timezone-qualified ISO instant")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone is required")
        return parse_utc(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a timezone-qualified ISO instant") from exc


def _day(value, name):
    """Require an explicit ISO calendar date, not a guessed expiry."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return parsed


def _text(value, name):
    """Require a nonempty identity component."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def leg_intrinsic(right, strike, level):
    """Return one European cash-settled leg's payoff per unit at ``level``.

    The one owner of the put/call intrinsic rule: exact Decimal cashflows
    and float scenario payoffs both call it.

    Parameters
    ----------
    right : str
        ``"put"`` or ``"call"``.
    strike, level : Decimal or float
        Strike and settlement level, of one numeric type.

    Returns
    -------
    Decimal or float
        ``max(strike - level, 0)`` for a put, ``max(level - strike, 0)``
        for a call, in the inputs' type.
    """
    gap = strike - level if right == "put" else level - strike
    return max(type(gap)(0), gap)


def _amount_text(value):
    """Render exact USD/point decimals without context-sensitive normalization."""
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


@dataclass(frozen=True, init=False)
class _SyntheticIndexRow(ABC):
    """Immutable canonical index-option observation, never a vendor adapter."""

    data: object
    _FIELDS = ()
    _IDENTITIES = ()

    def __init__(self, record):
        if not isinstance(record, dict):
            raise ValueError("index-option record must be a dict")
        problems = []
        reject_unknown_params(problems, record, _COMMON + self._FIELDS + ("observation_ms",))
        missing = [key for key in _COMMON + self._FIELDS if key not in record]
        if missing:
            problems.append(f"missing fields: {missing}")
        if problems:
            raise ValueError("; ".join(problems))
        data = dict(record)
        for key in ("corpus_id", "row_version") + self._IDENTITIES:
            _text(data[key], key)
        if data["schema_version"] != _SCHEMA:
            raise ValueError("unsupported schema_version")
        if data["provenance"] != "synthetic" or data["known_at_basis"] != "synthetic":
            raise ValueError("S0 requires synthetic provenance and known_at_basis")
        _instant(data["effective_at"], "effective_at")
        _instant(data["known_at"], "known_at")
        if "observation_ms" in data:
            _integer(data["observation_ms"], "observation_ms", 0)
        self._validate(data)
        object.__setattr__(self, "data", MappingProxyType(data))

    @abstractmethod
    def _validate(self, data):
        """Validate this particular index-option observation."""

    def _same_instant(self, data, field):
        """Require the domain timestamp to equal the observation timestamp."""
        if _instant(data[field], field) != _instant(data["effective_at"], "effective_at"):
            raise ValueError(f"{field} must equal effective_at")


class CashIndexContract(_SyntheticIndexRow):
    """Validate immutable synthetic cash-index option reference data.

    Parameters
    ----------
    record : dict
        Complete canonical contract row; no missing product terms are inferred.

    Examples
    --------
    From the child's working directory::

        import json
        from pathlib import Path
        row = json.loads(Path("fixtures/contracts.jsonl").read_text().splitlines()[0])
        contract = CashIndexContract(row)
    """

    _FIELDS = (
        "contract_id", "reference_version", "underlying_id", "product", "right",
        "strike", "multiplier", "currency", "expiry", "last_trade_at",
        "settlement_at", "exercise_style", "settlement_type",
        "settlement_style", "settlement_id",
    )
    _IDENTITIES = (
        "contract_id", "reference_version", "underlying_id", "product", "settlement_id",
    )

    def _validate(self, data):
        """Validate supported exercise, settlement and monetary scale."""
        required = {
            "currency": "USD", "exercise_style": "european",
            "settlement_type": "cash", "settlement_style": "pm",
        }
        for key, value in required.items():
            if data[key] != value:
                raise ValueError(f"unsupported {key}: expected {value}")
        if data["right"] not in ("put", "call"):
            raise ValueError("right must be put or call")
        _decimal(data["strike"], "strike")
        _integer(data["multiplier"], "multiplier", 1)
        _day(data["expiry"], "expiry")
        if _instant(data["last_trade_at"], "last_trade_at") > _instant(
            data["settlement_at"], "settlement_at"
        ):
            raise ValueError("last_trade_at must not follow settlement_at")


class _IndexQuote(_SyntheticIndexRow):
    """One synthetic NBBO observation, not a claim that a spread could fill."""

    _FIELDS = (
        "contract_id", "reference_version", "quote_at",
        "bid", "ask", "bid_size", "ask_size", "condition_valid",
    )
    _IDENTITIES = ("contract_id", "reference_version")

    def _validate(self, data):
        """Refuse invalid, crossed, negative or ill-typed quotes."""
        self._same_instant(data, "quote_at")
        bid, ask = (_decimal(data[key], key) for key in ("bid", "ask"))
        if bid < 0 or ask < bid:
            raise ValueError("quote must be nonnegative and uncrossed")
        for key in ("bid_size", "ask_size"):
            _integer(data[key], key, 0)
        if data["condition_valid"] is not True:
            raise ValueError("condition_valid must be true")


class _IndexSettlement(_SyntheticIndexRow):
    """Synthetic analogue of one version of an official PM settlement value."""

    _FIELDS = (
        "settlement_id", "underlying_id", "expiry", "settlement_style",
        "value", "official_value_at",
    )
    _IDENTITIES = ("settlement_id", "underlying_id")

    def _validate(self, data):
        """Validate settlement metadata without inventing a closing-price proxy."""
        self._same_instant(data, "official_value_at")
        _day(data["expiry"], "expiry")
        _decimal(data["value"], "value")
        if data["settlement_style"] != "pm":
            raise ValueError("settlement_style must be pm")


@dataclass(frozen=True, init=False)
class DefinedRiskCondor:
    """Own four intact European cash-index legs and their expiry cashflows.

    Parameters
    ----------
    contracts, quotes : list of dict
        Ordered long put, short put, short call, long call; exact versions.
    settlement : dict
        Selected synthetic official-settlement analogue.
    count : int
        Positive number of complete spreads.
    fees_usd : str
        Nonnegative all-in fee for the whole hypothetical outcome.
    quantities : list of int
        Per-spread signed leg quantities, exactly [1, -1, -1, 1].

    Examples
    --------
    With validated fixture streams and the chosen settlement row::

        condor = DefinedRiskCondor(contracts, quotes, settlement, 1, "8.00",
                                  [1, -1, -1, 1])
        report = condor.evaluate()
    """

    contracts: tuple
    quotes: tuple
    settlement: object
    count: int
    fees: object
    quantities: tuple

    def __init__(self, contracts, quotes, settlement, count, fees_usd, quantities):
        if not isinstance(contracts, list) or len(contracts) != 4:
            raise ValueError("exactly four contract rows are required")
        if not isinstance(quotes, list) or len(quotes) != 4:
            raise ValueError("exactly four quote rows are required")
        if not isinstance(quantities, (list, tuple)) or len(quantities) != 4:
            raise ValueError("exactly four signed quantities are required")
        for quantity in quantities:
            _integer(quantity, "quantity", -1)
        if tuple(quantities) != (1, -1, -1, 1):
            raise ValueError("quantities must be [1, -1, -1, 1]")
        _integer(count, "count", 1)
        fees = _decimal(fees_usd, "fees_usd")
        if fees < 0:
            raise ValueError("fees_usd must be nonnegative")
        object.__setattr__(self, "contracts", tuple(CashIndexContract(r) for r in contracts))
        object.__setattr__(self, "quotes", tuple(_IndexQuote(r) for r in quotes))
        object.__setattr__(self, "settlement", _IndexSettlement(settlement))
        object.__setattr__(self, "count", count)
        object.__setattr__(self, "fees", fees)
        object.__setattr__(self, "quantities", tuple(quantities))
        self._check_identity()
        with localcontext() as context:
            self._precision(context)
            self._credit()

    def _check_identity(self):
        """Match every leg, quote and settlement before computing any cashflow."""
        contracts = [r.data for r in self.contracts]
        quotes = [r.data for r in self.quotes]
        settlement = self.settlement.data
        first = contracts[0]
        common = (
            "corpus_id", "underlying_id", "product", "expiry", "settlement_id",
            "settlement_style", "currency", "multiplier", "reference_version",
        )
        if tuple(c["right"] for c in contracts) != ("put", "put", "call", "call"):
            raise ValueError("leg rights/order must be put, put, call, call")
        strikes = [_decimal(c["strike"], "strike") for c in contracts]
        if not all(a < b for a, b in zip(strikes, strikes[1:])):
            raise ValueError("strikes must be strictly ordered")
        if len({c["contract_id"] for c in contracts}) != 4:
            raise ValueError("four distinct contract identities are required")
        for contract, quote in zip(contracts, quotes):
            if any(contract[k] != first[k] for k in common):
                raise ValueError("condor contract identities/terms must match")
            if any(quote[k] != contract[k] for k in (
                "corpus_id", "contract_id", "reference_version",
            )):
                raise ValueError("quote must match its contract/reference version")
            if any(quote[k] < self.count for k in ("bid_size", "ask_size")):
                raise ValueError("quote sizes must cover every leg")
            if _instant(quote["quote_at"], "quote_at") != _instant(
                quotes[0]["quote_at"], "quote_at"
            ):
                raise ValueError("quote instants must match")
            if _instant(quote["quote_at"], "quote_at") > _instant(
                contract["last_trade_at"], "last_trade_at"
            ):
                raise ValueError("quote_at must not follow last_trade_at")
            if _instant(contract["settlement_at"], "settlement_at") != _instant(
                settlement["official_value_at"], "official_value_at"
            ):
                raise ValueError("settlement instant must match the reference terms")
        for key in ("corpus_id", "underlying_id", "settlement_id", "expiry", "settlement_style"):
            if settlement[key] != first[key]:
                raise ValueError(f"settlement {key} must match contracts")

    def _precision(self, context):
        """Keep decimal cashflow operations exact, independent of ambient precision."""
        values = [self.fees, _decimal(self.settlement.data["value"], "value")]
        values += [_decimal(c.data["strike"], "strike") for c in self.contracts]
        values += [_decimal(q.data[k], k) for q in self.quotes for k in ("bid", "ask")]
        integer = max(max(0, value.adjusted() + 1) for value in values)
        fractional = max(max(0, -value.as_tuple().exponent) for value in values)
        scale_digits = len(str(self.count)) + len(str(self.contracts[0].data["multiplier"]))
        context.prec = max(28, integer + fractional + scale_digits + 8)
        context.traps[Inexact] = True

    def _credit(self):
        """Check the conservative quote-side credit against both wing widths."""
        credit = sum(
            -sign * _decimal(q.data["ask" if sign > 0 else "bid"], "premium")
            for sign, q in zip(self.quantities, self.quotes)
        )
        strikes = [_decimal(c.data["strike"], "strike") for c in self.contracts]
        widths = (strikes[1] - strikes[0], strikes[3] - strikes[2])
        if not 0 < credit < min(widths):
            raise ValueError("credit must be positive and below the narrower wing width")
        return credit, widths

    def evaluate(self):
        """Compute exact signed cashflows, never expected returns or filled orders.

        Returns
        -------
        dict
            JSON-safe synthetic diagnostic with decimal-string USD amounts.

        Raises
        ------
        ArithmeticError
            If an extreme input cannot be represented exactly by Decimal.
        """
        with localcontext() as context:
            self._precision(context)
            credit, widths = self._credit()
            multiplier = self.contracts[0].data["multiplier"]
            scale = self.count * multiplier
            level = _decimal(self.settlement.data["value"], "value")
            legs = []
            for contract, quote, sign in zip(self.contracts, self.quotes, self.quantities):
                data = contract.data
                strike = _decimal(data["strike"], "strike")
                intrinsic = leg_intrinsic(data["right"], strike, level)
                entry = -sign * scale * _decimal(
                    quote.data["ask" if sign > 0 else "bid"], "premium"
                )
                terminal = sign * scale * intrinsic
                legs.append({
                    "contract_id": data["contract_id"],
                    "contract_version": data["row_version"],
                    "quote_at": quote.data["quote_at"],
                    "quote_version": quote.data["row_version"],
                    "quantity": sign * self.count,
                    "entry_cashflow_usd": _amount_text(entry),
                    "settlement_cashflow_usd": _amount_text(terminal),
                })
            entry = scale * credit
            terminal = sum(Decimal(leg["settlement_cashflow_usd"]) for leg in legs)
            gross = entry + terminal
            loss = scale * (max(widths) - credit)
            return {
                "kind": "synthetic_ex_post_diagnostic", "decision_eligible": False,
                "corpus_id": self.contracts[0].data["corpus_id"], "currency": "USD",
                "count": self.count, "multiplier": multiplier, "legs": legs,
                "settlement": dict(self.settlement.data),
                "entry_credit_points": _amount_text(credit),
                "entry_cashflow_usd": _amount_text(entry),
                "settlement_cashflow_usd": _amount_text(terminal),
                "fees_usd": _amount_text(self.fees),
                "gross_pnl_usd": _amount_text(gross),
                "net_pnl_usd": _amount_text(gross - self.fees),
                "max_loss_before_fees_usd": _amount_text(loss),
                "max_loss_after_fees_usd": _amount_text(loss + self.fees),
            }
