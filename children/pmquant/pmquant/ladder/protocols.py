"""The ladder program's shared vocabularies — declared once, sniffed nowhere.

Four small things every other module reads rather than restates:

* **Venues.** Which venue owns a ticker is a TABLE (:data:`VENUE_PREFIXES`),
  and the strict reading of it refuses an unattributable ticker instead of
  falling through to whichever branch an ``else`` happened to be — in a
  joint two-venue document that fall-through is a Kalshi mirror-encoded
  book read through the Polymarket no-mirror path: not an error, garbage.
* **Settlement laws** (:class:`SettlementLaw`) — how a ladder's rungs
  settle relative to one another: exactly one YES (``partition``) or a
  cumulative family where multiple YES is legal (``threshold``).
* **Ladder types** (:class:`LadderType`) — the finer strike geometry a
  law follows from: a partition, an upper or lower threshold family, or
  the two-tailed hit-price families (two latents; never renormalized).
* **The lead grid** (:class:`LeadGrid`) — the 21 decision instants per
  event at which a point-in-time ladder is read: fractions
  ``f ∈ {0.98, …, 0.02}`` of the event's observable span, shared verbatim
  by the recorder and the PIT build so the two produce byte-identical
  epochs (pinned by test).

Import cost: stdlib only — this module is read at plan time.
"""

from __future__ import annotations

import enum
import math

from dskit.onboarding.leads import LEAD_ROUND_DP, lead_key
from dskit.onboarding.leads import LeadGrid as _LeadGrid

__all__ = [
    "DEFAULT_DUR_CAPS_H",
    "DEFAULT_MIN_ABS_LEAD_S",
    "LEAD_FRACS",
    "LEAD_ROUND_DP",
    "lead_key",
    "VENUE_PREFIXES",
    "VENUES",
    "STRIKE_CODES",
    "LadderType",
    "LeadGrid",
    "SettlementLaw",
    "prefixes_for",
    "rung_sort_key",
    "scope_to_venue",
    "venue_of",
]

#: venue name -> the ticker prefixes that venue owns, DECLARED. Ticker
#: namespaces only: fee rounding, the mirror rule and the read paths hang
#: off the venue NAME elsewhere. A third venue is a row here plus rows in
#: the fee book — never new Python (D-137/D-152).
VENUE_PREFIXES = {
    "kalshi": ("KX",),
    "polymarket": ("POLY",),
}

#: The declared venue names, sorted — the vocabulary a ``venue`` param
#: is validated against.
VENUES = tuple(sorted(VENUE_PREFIXES))

#: The 21 lead fractions, in chronological order: from the earliest
#: decision instant (98% of the observable span still ahead) to the
#: latest (2%). Spelled once; every consumer reads this tuple, and the
#: shipped source configs are pinned against it by test.
LEAD_FRACS = (
    0.98, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50,
    0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10, 0.05, 0.02,
)

#: Default duration caps in hours, keyed by the event's nominal life: a
#: daily market observes at most 48h before close, a weekly 168h, a
#: monthly 744h. The cap bounds the observable span so a market listed
#: months early is still read over the window that carries information.
DEFAULT_DUR_CAPS_H = (48, 168, 744)

#: Default floor on the absolute lead, in seconds — a decision instant
#: closer than this to the close is not a decision anyone could act on.
DEFAULT_MIN_ABS_LEAD_S = 60

_REQUIRED = object()


def _check_table(table):
    """Refuse a prefix table whose venues nest (attribution would depend on order)."""
    owned = []
    for venue, prefixes in table.items():
        if not isinstance(venue, str) or not venue:
            raise ValueError(f"venue names must be non-empty strings, got {venue!r}")
        if not isinstance(prefixes, tuple) or not prefixes:
            raise ValueError(
                f"{venue!r}: prefixes must be a non-empty tuple, got {prefixes!r}"
            )
        for prefix in prefixes:
            if not isinstance(prefix, str) or not prefix:
                raise ValueError(
                    f"{venue!r}: every prefix must be a non-empty string, got {prefix!r}"
                )
            owned.append((prefix, venue))
    for prefix, venue in owned:
        for other, other_venue in owned:
            if venue != other_venue and other.startswith(prefix):
                raise ValueError(
                    f"venue prefixes {prefix!r} ({venue}) and {other!r} "
                    f"({other_venue}) nest — attribution would depend on order"
                )


_check_table(VENUE_PREFIXES)


def prefixes_for(venue, table=None):
    """Give the ticker prefixes ``venue`` owns.

    Parameters
    ----------
    venue : str
        A declared venue name.
    table : dict, optional
        An alternative registry (tests); default :data:`VENUE_PREFIXES`.

    Returns
    -------
    tuple of str
        The prefixes.

    Raises
    ------
    ValueError
        On a venue the table does not declare, naming the ones it does.
    """
    table = VENUE_PREFIXES if table is None else table
    if venue not in table:
        raise ValueError(
            f"unknown venue {venue!r} — declared venues are {sorted(table)}; "
            "a new venue is a row in VENUE_PREFIXES plus rows in the fee book"
        )
    return table[venue]


def venue_of(series, *, default=_REQUIRED, table=None):
    """Name the venue owning ``series``.

    Parameters
    ----------
    series : str
        A series or market ticker.
    default : object, optional
        Returned for a ticker no declared prefix matches. WITHOUT it the
        unattributable ticker RAISES — the reading every caller that
        SPLITS a shared store must use.
    table : dict, optional
        An alternative registry (tests); default :data:`VENUE_PREFIXES`.

    Returns
    -------
    str
        The venue name, or ``default``.

    Raises
    ------
    ValueError
        On an unattributable ticker when no ``default`` was given.
    """
    table = VENUE_PREFIXES if table is None else table
    ticker = str(series)
    for prefix, venue in sorted(
        ((p, v) for v, ps in table.items() for p in ps), key=lambda pv: -len(pv[0])
    ):
        if ticker.startswith(prefix):
            return venue
    if default is not _REQUIRED:
        return default
    raise ValueError(
        f"series {ticker!r} matches no declared venue prefix "
        f"({ {v: list(ps) for v, ps in sorted(table.items())} }) — a series "
        "that cannot be attributed is a defect to report, not a row to discard"
    )


def scope_to_venue(names, venue, table=None):
    """Keep the subset of ``names`` that ``venue`` owns, sorted.

    Refuses the whole call when ANY name is unattributable: every venue's
    source filters the same store, so a series nobody claims would vanish
    from every one of them and be indistinguishable from one that is not
    there.

    Parameters
    ----------
    names : iterable of str
        Series tickers.
    venue : str
        The venue to keep.
    table : dict, optional
        An alternative registry (tests).

    Returns
    -------
    list of str
        The owned names, sorted.

    Raises
    ------
    ValueError
        On an unknown venue, or on any unattributable name.
    """
    table = VENUE_PREFIXES if table is None else table
    prefixes_for(venue, table)
    owned, orphans = [], []
    for name in names:
        try:
            if venue_of(name, table=table) == venue:
                owned.append(name)
        except ValueError:
            orphans.append(str(name))
    if orphans:
        raise ValueError(
            f"{len(orphans)} series match no declared venue prefix: "
            f"{sorted(orphans)[:8]}{' ...' if len(orphans) > 8 else ''} — "
            "refusing to scope, because a series nobody claims would vanish "
            "from every venue's source"
        )
    return sorted(owned)


class SettlementLaw(str, enum.Enum):
    """How a ladder's rungs settle relative to one another.

    The strings are what the frozen stores persist (the parent's
    ``manifest.parquet`` carries them), so they are the vocabulary, not
    a display name.

    Parameters
    ----------
    value : str
        ``"partition"`` — the rungs tile the line and EXACTLY ONE settles
        YES (a bracket ladder; the head is a softmax over visible rungs);
        ``"threshold"`` — a cumulative strike family where MULTIPLE YES is
        legal and the law is per-tail monotonicity of the outcome in the
        strike (the head is a monotone logit chain per tail).

    Examples
    --------
    Read a law back from its stored string::

        SettlementLaw("partition") is SettlementLaw.PARTITION   # True
    """

    PARTITION = "partition"
    THRESHOLD = "threshold"


class LadderType(str, enum.Enum):
    """The finer strike GEOMETRY of a ladder, from which its law follows.

    Parameters
    ----------
    value : str
        ``"partition"`` — ``between`` buckets with optional bottom
        ``less`` / top ``greater`` tails; ``"upper_threshold"`` — nested
        ``greater`` rungs on one latent (``P(X > s)`` non-increasing in
        ``s``); ``"lower_threshold"`` — nested ``less`` rungs
        (``P(X < s)`` non-decreasing); ``"two_tailed"`` — both tails on
        TWO latents (a path maximum and a path minimum — the hit-price
        families), so the rungs do not tile one line and multiple YES is
        legal.

    Examples
    --------
    Classify a ladder by its strike types, then ask for its law::

        kind = LadderType.classify(["less", "between", "greater"])
        kind                       # LadderType.PARTITION
        kind.law                   # SettlementLaw.PARTITION
        LadderType.classify(["greater", "greater"]).tails   # ('greater',)
    """

    PARTITION = "partition"
    UPPER_THRESHOLD = "upper_threshold"
    LOWER_THRESHOLD = "lower_threshold"
    TWO_TAILED = "two_tailed"

    @property
    def law(self):
        """Give the :class:`SettlementLaw` this geometry settles under."""
        return (
            SettlementLaw.PARTITION
            if self is LadderType.PARTITION
            else SettlementLaw.THRESHOLD
        )

    @property
    def tails(self):
        """Give the threshold tails this geometry carries.

        A tuple drawn from ``("less", "greater")``, empty for a partition.
        """
        return {
            LadderType.PARTITION: (),
            LadderType.UPPER_THRESHOLD: ("greater",),
            LadderType.LOWER_THRESHOLD: ("less",),
            LadderType.TWO_TAILED: ("less", "greater"),
        }[self]

    @classmethod
    def classify(cls, strike_types):
        """Classify a ladder from its rungs' strike types.

        The parent's rule verbatim: a ``between`` anywhere, or exactly a
        ``less``/``greater`` pair, or no strike types at all, is a
        partition; all-``greater`` is an upper threshold; all-``less`` a
        lower threshold; anything else is two-tailed.

        Parameters
        ----------
        strike_types : iterable of str
            One entry per rung (``"less"`` | ``"between"`` | ``"greater"``);
            empty or non-string entries are ignored.

        Returns
        -------
        LadderType
            The geometry.
        """
        ts = [t for t in strike_types if isinstance(t, str) and t]
        kinds = set(ts)
        if "between" in kinds or (len(ts) == 2 and kinds == {"less", "greater"}):
            return cls.PARTITION
        if not ts:
            return cls.PARTITION
        if kinds == {"greater"}:
            return cls.UPPER_THRESHOLD
        if kinds == {"less"}:
            return cls.LOWER_THRESHOLD
        return cls.TWO_TAILED


#: Strike-type -> the integer code the token panels carry per rung.
#: ``between`` (1) is also the PADDING value: it is exactly the code the
#: settlement head treats as "neither tail", so a padded rung falls
#: through the monotone chains untouched. Keep that alignment.
STRIKE_CODES = {"less": 0, "between": 1, "greater": 2}


def rung_sort_key(strike_type, floor_strike, cap_strike):
    """Order rungs canonically.

    Less-tail (cap ascending), then between (floor, else cap), then
    greater-tail (floor ascending). Load-bearing for the settlement head: each threshold tail must be
    one CONTIGUOUS run in rung order, which this order guarantees.

    Parameters
    ----------
    strike_type : str
        ``"less"`` | ``"between"`` | ``"greater"``.
    floor_strike : float or None
        The lower strike.
    cap_strike : float or None
        The upper strike.

    Returns
    -------
    tuple
        ``(group, value)`` — sortable across a ladder's rungs.
    """

    def _num(value, default):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    if strike_type == "less":
        return (0, _num(cap_strike, -1e9))
    if strike_type == "greater":
        return (2, _num(floor_strike, 1e9))
    return (1, _num(floor_strike, _num(cap_strike, 0.0)))


class LeadGrid(_LeadGrid):
    """The decision instants at which one event's ladder is read.

    The MECHANISM — cap selection, observable span, floored lead
    instants, the period spelling — is dskit's
    :class:`dskit.onboarding.leads.LeadGrid` (ADR-0075), where every
    knob is required. This subclass supplies only what is pmquant's to
    decide: the 21 fractions, the daily/weekly/monthly caps and the
    60-second floor, so a document that declares none of them gets the
    grid the parent project's recorder and PIT build agreed on.

    Parameters
    ----------
    lead_fracs : sequence of float, optional
        The fractions, each in (0, 1); default :data:`LEAD_FRACS`.
    dur_caps_h : sequence of int or float, optional
        The duration caps in hours; default :data:`DEFAULT_DUR_CAPS_H`.
    min_abs_lead_s : int or float, optional
        The absolute lead floor in seconds; default
        :data:`DEFAULT_MIN_ABS_LEAD_S`.

    Examples
    --------
    The 21 epochs of a daily market listed one day before it closes::

        grid = LeadGrid()
        epochs = grid.epochs(open_ms=0, close_ms=86_400_000)
        len(epochs)              # 21
        epochs[0]                # (0.98, 1728000)
    """

    def __init__(self, lead_fracs=None, dur_caps_h=None, min_abs_lead_s=None):
        super().__init__(
            tuple(LEAD_FRACS if lead_fracs is None else lead_fracs),
            tuple(DEFAULT_DUR_CAPS_H if dur_caps_h is None else dur_caps_h),
            DEFAULT_MIN_ABS_LEAD_S if min_abs_lead_s is None else min_abs_lead_s,
        )
