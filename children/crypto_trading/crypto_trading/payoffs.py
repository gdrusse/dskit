"""The three binary payoff geometries a Kalshi crypto contract can have.

A contract pays 1 dollar when the settlement value ``A`` lands in a region and 0
otherwise. Every region is a union of half-lines on the strikes, so each geometry is
an object that names the strike fields it reads and combines ``survival(K) = P(A >= K)``
into the probability of YES. A new geometry is a new subclass in :data:`PAYOFFS`,
never a branch in a node.

- ``above``   (Kalshi ``greater`` / ``greater_or_equal``): YES when ``A >= floor``.
- ``below``   (Kalshi ``less`` / ``less_or_equal``): YES when ``A < cap``.
- ``between`` (Kalshi ``between``): YES when ``floor <= A < cap``.

INTERIM HOME (PROPOSED ADR-0243): the geometries are generic to any binary contract on a value; they move to
dskit with the pricer in :mod:`crypto_trading.fair_value`.

Which Kalshi ``strike_type`` maps to which geometry is configuration (the document's
``payoff_by_strike_type``), so the venue's vocabulary never lives in code.

Import cost: stdlib only.
"""

from abc import ABC, abstractmethod

from .fields import CAP, FLOOR

__all__ = ["ABOVE", "BELOW", "BETWEEN", "PAYOFFS", "Above", "Below", "Between", "BinaryPayoff", "payoff"]

ABOVE = "above"
BELOW = "below"
BETWEEN = "between"


class BinaryPayoff(ABC):
    """One payoff geometry: which strikes it reads and how it prices from a survival function.

    Examples
    --------
    Price a range from a survival function ``P(A >= K)``::

        survival = lambda strike: 0.6 if strike < 100 else 0.3
        PAYOFFS["between"].yes_probability(survival, floor=99.0, cap=101.0)
        # -> 0.3
    """

    #: The geometry's name, the value a document's ``payoff_by_strike_type`` maps to.
    name = ""

    @property
    @abstractmethod
    def strike_fields(self):
        """Name the strike fields this geometry reads, in order (tuple of str)."""

    @abstractmethod
    def yes_probability(self, survival, floor, cap):
        """Return P(YES) given ``survival(K) = P(A >= K)`` and the contract's strikes.

        Parameters
        ----------
        survival : callable
            ``survival(strike) -> float``, the probability the settlement value is at
            or above ``strike``.
        floor : float or None
            The contract's floor strike.
        cap : float or None
            The contract's cap strike.

        Returns
        -------
        float
            The probability the contract pays.
        """


class Above(BinaryPayoff):
    """YES when the value is at or above the floor strike.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["above"].yes_probability(lambda k: 0.4, floor=100.0, cap=None)
        # -> 0.4
    """

    name = ABOVE

    @property
    def strike_fields(self):
        """Name the one strike field read."""
        return (FLOOR,)

    def yes_probability(self, survival, floor, cap):
        """Return ``P(A >= floor)``."""
        return survival(floor)


class Below(BinaryPayoff):
    """YES when the value is below the cap strike.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["below"].yes_probability(lambda k: 0.4, floor=None, cap=100.0)
        # -> 0.6
    """

    name = BELOW

    @property
    def strike_fields(self):
        """Name the one strike field read."""
        return (CAP,)

    def yes_probability(self, survival, floor, cap):
        """Return ``P(A < cap) = 1 - P(A >= cap)``."""
        return 1.0 - survival(cap)


class Between(BinaryPayoff):
    """YES when the value is at or above the floor and below the cap.

    Examples
    --------
    Price it from a survival function::

        PAYOFFS["between"].yes_probability(lambda k: 0.6 if k < 100 else 0.3, floor=99.0, cap=101.0)
        # -> 0.3
    """

    name = BETWEEN

    @property
    def strike_fields(self):
        """Name both strike fields read."""
        return (FLOOR, CAP)

    def yes_probability(self, survival, floor, cap):
        """Return ``P(floor <= A < cap)``."""
        return survival(floor) - survival(cap)


#: name -> the one instance of that geometry.
PAYOFFS = {cls.name: cls() for cls in (Above, Below, Between)}


def payoff(name):
    """Return the geometry called ``name``.

    Parameters
    ----------
    name : str
        One of :data:`PAYOFFS`.

    Returns
    -------
    BinaryPayoff
        The geometry.

    Raises
    ------
    ValueError
        When ``name`` is not a known geometry (the message lists the known ones).
    """
    try:
        return PAYOFFS[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown payoff {name!r}; known: {sorted(PAYOFFS)}") from None
