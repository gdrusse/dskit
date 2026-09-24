"""Iron-condor outcomes under a sample-set distribution forecast (ADR-0168).

Path A0001 fixes the modeling unit: the index's horizon log return divided
by a reference scale known at entry, ``z = ln(S_T / F) / scale``. A strike
maps into the same unit by :func:`strike_z`, so a condor's payoff regions
are fixed intervals of ``z`` whatever the index level or volatility.

This module turns one forecast (draws of ``z``) into what the condor would
earn under it — expected P&L, probability of keeping the whole credit,
probability of settling beyond a wing (its full loss), and the CVaR of the loss tail — and into
the realized P&L at the observed outcome. Everything is in units of the
narrower wing width, so rows at different index levels aggregate. It is a
synthetic research diagnostic: no quotes, no fills, never decision-eligible.
"""

import math

from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import lower_tail_mean

from .contracts import leg_intrinsic

__all__ = ["CondorGeometry", "condor_payoff", "strike_z"]

#: Leg order and signed quantities, matching ``DefinedRiskCondor``.
_LEGS = (("put", 1), ("put", -1), ("call", -1), ("call", 1))


def condor_payoff(level, strikes):
    """Return a long-wing condor's settlement payoff per unit, credit excluded.

    The one owner of the four-leg payoff sum: the standardized geometry
    and the USD backtest (ADR-0182) both call it.

    Parameters
    ----------
    level : float
        Settlement level.
    strikes : sequence of float
        Long put, short put, short call, long call.

    Returns
    -------
    float
        Between ``-max(wing)`` and ``0``.
    """
    return sum(sign * leg_intrinsic(right, k, level)
               for (right, sign), k in zip(_LEGS, strikes))


def strike_z(strike, forward, reference_scale):
    """Map a strike into standardized log-moneyness.

    Parameters
    ----------
    strike, forward : float
        Positive price levels.
    reference_scale : float
        The positive horizon scale the forecast is standardized by.

    Returns
    -------
    float
        ``ln(strike / forward) / reference_scale``.

    Raises
    ------
    ValueError
        When any input is not a positive finite number.
    """
    for name, value in (("strike", strike), ("forward", forward),
                        ("reference_scale", reference_scale)):
        if not number_ok(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    return math.log(strike / forward) / reference_scale


class CondorGeometry:
    """A condor declared in standardized units, with its credit.

    Parameters
    ----------
    strikes_z : sequence of float
        Long put, short put, short call, long call, strictly increasing.
    credit_fraction : float
        Entry credit as a fraction of the narrower wing, in ``(0, 1)`` —
        the same bound ``DefinedRiskCondor`` enforces on quoted credit.
    cvar_alpha : float
        Tail level in ``(0, 1)``; CVaR averages the worst ``1 - alpha``.

    Examples
    --------
    Shorts one reference standard deviation out, wings at two::

        geometry = CondorGeometry([-2.0, -1.0, 1.0, 2.0], 0.3, 0.95)
        report = geometry.evaluate(draws, outcome_z=0.4, forward=100.0, scale=0.05)
    """

    def __init__(self, strikes_z, credit_fraction, cvar_alpha):
        problems = self.problems(strikes_z, credit_fraction, cvar_alpha)
        if problems:
            raise ValueError("; ".join(problems))
        self.strikes_z = tuple(float(z) for z in strikes_z)
        self.credit_fraction = float(credit_fraction)
        self.cvar_alpha = float(cvar_alpha)

    @staticmethod
    def problems(strikes_z, credit_fraction, cvar_alpha):
        """List what is wrong with a declaration.

        Parameters
        ----------
        strikes_z, credit_fraction, cvar_alpha : object
            The candidate declaration.

        Returns
        -------
        list of str
            Empty when usable.
        """
        problems = []
        if not isinstance(strikes_z, (list, tuple)) or len(strikes_z) != 4 \
                or not all(number_ok(z) for z in strikes_z) \
                or not all(a < b for a, b in zip(strikes_z, strikes_z[1:])):
            problems.append(f"strikes_z must be four strictly increasing numbers, got {strikes_z!r}")
        for name, value in (("credit_fraction", credit_fraction), ("cvar_alpha", cvar_alpha)):
            if not number_ok(value) or not 0 < value < 1:
                problems.append(f"{name} must be in (0, 1), got {value!r}")
        return problems

    def strikes(self, forward, scale):
        """Return the four strike levels for one entry.

        Parameters
        ----------
        forward, scale : float
            The entry's forward level and reference scale.

        Returns
        -------
        tuple of float
            Strike levels in leg order.
        """
        return tuple(forward * math.exp(scale * z) for z in self.strikes_z)

    def pnl_per_width(self, level, strikes):
        """Return settlement P&L, credit included, per unit of narrower wing.

        Parameters
        ----------
        level : float
            Settlement level.
        strikes : tuple of float
            Strike levels in leg order.

        Returns
        -------
        float
            Between ``-(wider / narrower - credit_fraction)`` and
            ``credit_fraction``.
        """
        narrower = min(strikes[1] - strikes[0], strikes[3] - strikes[2])
        return self.credit_fraction + condor_payoff(level, strikes) / narrower

    def cvar(self, pnls):
        """Return the mean of the worst ``1 - cvar_alpha`` share of P&L.

        Parameters
        ----------
        pnls : sequence of float
            P&L values; at least one.

        Returns
        -------
        float
            The tail mean (lower is worse), dskit's
            :func:`~dskit.pipeline.stats.lower_tail_mean`.
        """
        return lower_tail_mean(pnls, self.cvar_alpha)

    def evaluate(self, draws_z, outcome_z, forward, scale):
        """Evaluate one entry under its forecast and, if known, its outcome.

        Parameters
        ----------
        draws_z : sequence of float
            The forecast's draws of standardized log return.
        outcome_z : float or None
            The realized standardized log return, ``None`` when unknown.
        forward, scale : float
            The entry's forward level and reference scale.

        Returns
        -------
        dict
            ``expected_pnl``, ``p_full_credit``, ``p_beyond_wings``, ``cvar``
            (all under the forecast) and ``realized_pnl`` (``None`` when
            the outcome is unknown), in narrower-wing units.
        """
        strikes = self.strikes(forward, scale)
        pnls = [self.pnl_per_width(forward * math.exp(scale * z), strikes) for z in draws_z]
        inner, outer = self.strikes_z[1:3], (self.strikes_z[0], self.strikes_z[3])
        n = len(pnls)
        return {
            "expected_pnl": sum(pnls) / n,
            "p_full_credit": sum(inner[0] <= z <= inner[1] for z in draws_z) / n,
            "p_beyond_wings": sum(z <= outer[0] or z >= outer[1] for z in draws_z) / n,
            "cvar": self.cvar(pnls),
            "realized_pnl": None if outcome_z is None else self.pnl_per_width(
                forward * math.exp(scale * outcome_z), strikes),
        }
