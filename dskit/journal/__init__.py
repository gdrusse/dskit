"""``dskit.journal`` — the child action ledger (ADR-0056).

Every child action is labeled: acquire, research, execute, or
production. CSV is the store; ``docs/decisioning/README.md`` is
generated. Path to production is owner ``promote`` only.

Import cost: stdlib only. This package never imports pipeline,
onboarding, or assets — those function-import the hooks.
"""

from .base import (
    ACTION_FIELDS,
    CATEGORIES,
    CRITERIA,
    PATH_FIELDS,
    JournalError,
    MARKER,
    utc_now,
)
from .hooks import (
    production,
    record_acquire,
    record_execute,
    record_production,
    record_research,
)
from .locate import JournalRoot, find_journal, init_journal, load_root
from .model import Action, JournalConfig, PathRow, next_id
from .record import append_action, promote
from .render import render
from .research import write_research

__all__ = [
    "ACTION_FIELDS",
    "CATEGORIES",
    "CRITERIA",
    "MARKER",
    "PATH_FIELDS",
    "Action",
    "JournalConfig",
    "JournalError",
    "JournalRoot",
    "PathRow",
    "append_action",
    "find_journal",
    "init_journal",
    "load_root",
    "next_id",
    "production",
    "promote",
    "record_acquire",
    "record_execute",
    "record_production",
    "record_research",
    "render",
    "utc_now",
    "write_research",
]
