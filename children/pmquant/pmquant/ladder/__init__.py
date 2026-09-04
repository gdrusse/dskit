"""``pmquant.ladder`` — the ladder data engine (proposal §8.1).

The vocabularies every other module speaks (venues, settlement laws,
ladder types, the lead grid) live in :mod:`.protocols`; the store,
manifest, split and panel mechanics live beside it. Nothing here imports
a heavy library at module top — documents naming the child's kinds must
plan on a machine with only ``dskit`` installed.
"""

from .protocols import (
    LEAD_FRACS,
    STRIKE_CODES,
    VENUE_PREFIXES,
    VENUES,
    LadderType,
    LeadGrid,
    SettlementLaw,
    lead_key,
    rung_sort_key,
    scope_to_venue,
    venue_of,
)

__all__ = [
    "LEAD_FRACS",
    "STRIKE_CODES",
    "VENUE_PREFIXES",
    "VENUES",
    "LadderType",
    "LeadGrid",
    "SettlementLaw",
    "lead_key",
    "rung_sort_key",
    "scope_to_venue",
    "venue_of",
]
