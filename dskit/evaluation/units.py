"""Number formatting for the report: money, counts, ratios and declared score units.

A report that prints ``1.82016e-07`` for a forecast has told the reader
nothing. This module is the one place a number becomes display text:
money is two decimals with a currency sign and thousands separators,
counts carry separators, ratios three significant figures, and scores
(forecasts, edges, thresholds) render in the unit ``run_start.units``
declares — a return in basis points, a probability as a probability.

The declaration is optional (ADR-0183 amendment, additive to schema v1):
``run_start.units = {"score": <SCORE_UNITS key>, "money": <ISO code>}``.
Without it scores print at three significant figures and money without a
sign. CSVs never go through here: they keep the raw value and name its
unit in a column, so nothing is lost to rounding.

Import cost: stdlib only.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "CURRENCY_SIGNS",
    "DASH",
    "SCORE_UNITS",
    "ScoreUnit",
    "Units",
    "count",
    "money",
    "percent",
    "ratio",
    "sig",
]

#: What an absent value reads as.
DASH = "—"

#: One cent, the money display's quantum.
_CENT = Decimal("0.01")


class ScoreUnit:
    """How one declared score unit displays: a scale, a suffix, a precision.

    Parameters
    ----------
    name : str
        The declared spelling.
    scale : float
        Multiplies the raw value for display (a return x 1e4 is bp).
    suffix : str
        Appended after a space; empty for none.
    digits : int
        Significant figures.
    meaning : str
        One line for the "how to read" note.

    Examples
    --------
    ::

        SCORE_UNITS["return"].format(1.82016e-07)  # '0.00182 bp'
    """

    def __init__(self, name, scale, suffix, digits, meaning):
        self.name, self.scale, self.suffix = name, scale, suffix
        self.digits, self.meaning = digits, meaning

    def format(self, value, signed=False):
        """Return ``value`` in this unit, or a dash for None.

        Parameters
        ----------
        value : float or None
        signed : bool
            Prefix ``+`` on a positive value (edges read as differences).

        Returns
        -------
        str
        """
        if value is None:
            return DASH
        text = sig(value * self.scale, self.digits, signed=signed)
        return f"{text} {self.suffix}" if self.suffix else text


#: Every declarable score unit, keyed by its spelling — a table, never a branch.
SCORE_UNITS = {
    unit.name: unit
    for unit in (
        ScoreUnit("return", 1e4, "bp", 3, "a forecast simple return, shown in basis points"),
        ScoreUnit("log_return", 1e4, "bp", 3, "a forecast log return, shown in basis points"),
        ScoreUnit("bp", 1.0, "bp", 3, "a forecast in basis points"),
        ScoreUnit("prob", 1.0, "", 3, "a probability in [0, 1]"),
        ScoreUnit("usd", 1.0, "USD", 3, "a forecast amount in US dollars"),
        ScoreUnit("z", 1.0, "σ", 3, "a standardised score"),
        ScoreUnit("raw", 1.0, "", 3, "a model score with no declared unit"),
    )
}

#: Currency codes with a sign; any other code prints as a prefix.
CURRENCY_SIGNS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def _clean(value):
    """Return ``value`` with -0.0 folded to 0.0."""
    return value + 0.0


def sig(value, digits=3, signed=False):
    """Format ``value`` to ``digits`` significant figures, never in e-notation below 1e9.

    Parameters
    ----------
    value : float or None
    digits : int
    signed : bool
        Prefix ``+`` on a positive value.

    Returns
    -------
    str

    Examples
    --------
    ::

        sig(0.00182016)  # '0.00182'
        sig(-15.293)     # '-15.3'
    """
    if value is None:
        return DASH
    value = _clean(float(value))
    if not math.isfinite(value):
        return str(value)
    if value == 0:
        return "0"
    magnitude = math.floor(math.log10(abs(value)))
    if abs(value) >= 1e9 or magnitude < -9:
        text = f"{value:.{digits - 1}e}"
    else:
        decimals = max(0, digits - 1 - magnitude)
        text = f"{value:,.{decimals}f}"
    return ("+" + text) if signed and value > 0 else text


def ratio(value, signed=False):
    """Format a ratio or a statistic to three significant figures.

    Examples
    --------
    ::

        ratio(0.794224)  # '0.794'
    """
    return sig(value, 3, signed=signed)


def count(value):
    """Format a count with thousands separators.

    Examples
    --------
    ::

        count(21583)  # '21,583'
    """
    if value is None:
        return DASH
    return f"{int(value):,}"


def percent(value, digits=3):
    """Format a share in [0, 1] as a percentage to ``digits`` significant figures.

    Examples
    --------
    ::

        percent(0.213953)  # '21.4%'
    """
    if value is None:
        return DASH
    return f"{sig(value * 100, digits)}%"


def money(value, currency=None, signed=False):
    """Format an amount: two decimals, thousands separators, the currency sign.

    Parameters
    ----------
    value : float or None
    currency : str or None
        An ISO code; see :data:`CURRENCY_SIGNS`.
    signed : bool
        Prefix ``+`` on a positive amount (P&L reads as a change).

    Returns
    -------
    str

    Examples
    --------
    ::

        money(-180.465, "USD")          # '-$180.47'
        money(6.0, "USD", signed=True)  # '+$6.00'
    """
    if value is None:
        return DASH
    # Round the decimal the value was written as, half away from zero:
    # -180.465 is -$180.47, not the binary float's -$180.46.
    value = _clean(float(Decimal(repr(float(value))).quantize(_CENT, rounding=ROUND_HALF_UP)))
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    mark = CURRENCY_SIGNS.get(currency, f"{currency} " if currency else "")
    return f"{sign}{mark}{abs(value):,.2f}"


class Units:
    """The run's declared units, read from ``run_start.units``.

    Parameters
    ----------
    declared : dict or None
        ``{"score": <SCORE_UNITS key>, "money": <ISO code>}``, either optional.

    Examples
    --------
    ::

        units = Units({"score": "return", "money": "USD"})
        units.score(1.82e-07)  # '0.00182 bp'
        units.money(-180.465)  # '-$180.47'
    """

    def __init__(self, declared=None):
        declared = declared or {}
        self.score_name = declared.get("score")
        self.currency = declared.get("money")
        self.score_unit = SCORE_UNITS.get(self.score_name or "raw", SCORE_UNITS["raw"])

    def score(self, value, signed=False):
        """Format a forecast, edge or threshold in the declared score unit."""
        return self.score_unit.format(value, signed=signed)

    def money(self, value, signed=False):
        """Format an amount in the declared currency."""
        return money(value, self.currency, signed=signed)

    @property
    def score_label(self):
        """The score unit's CSV label: the declared name, or ``undeclared``."""
        return self.score_name or "undeclared"
