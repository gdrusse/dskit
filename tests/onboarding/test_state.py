"""state.py: cursor round-trips, mode independence, corrupt refusal."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import load_state, save_state


def test_first_pull_is_empty_dict(root):
    assert load_state(root, "vendor", "prices", "live") == {}


def test_round_trip_returns_only_the_opaque_state(root):
    save_state(root, "vendor", "prices", "live", {"cursor": "2026-01-05"})
    assert load_state(root, "vendor", "prices", "live") == {"cursor": "2026-01-05"}


def test_modes_hold_independent_cursors(root):
    # THE mode ruling (ADR-0014): backfill and live never share a cursor.
    save_state(root, "vendor", "prices", "live", {"cursor": "2026-01-05"})
    save_state(root, "vendor", "prices", "backfill", {"cursor": "2001-12-31"})
    assert load_state(root, "vendor", "prices", "live") == {"cursor": "2026-01-05"}
    assert load_state(root, "vendor", "prices", "backfill") == {"cursor": "2001-12-31"}


def test_streams_hold_independent_cursors(root):
    save_state(root, "vendor", "prices", "live", {"cursor": "a"})
    assert load_state(root, "vendor", "volumes", "live") == {}


def test_corrupt_checkpoint_halts_never_restarts_from_zero(root):
    path = save_state(root, "vendor", "prices", "live", {"cursor": "x"})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(AssetError, match="unreadable"):
        load_state(root, "vendor", "prices", "live")


def test_state_must_be_a_dict(root):
    with pytest.raises(AssetError):
        save_state(root, "vendor", "prices", "live", ["not", "a", "dict"])
