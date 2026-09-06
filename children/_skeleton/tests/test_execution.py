"""The child's venue executor under the toolkit's conformance battery.

`executor_conformance_suite` is the same closed battery that proves the
toolkit's own paper executor: client refs echoed, a repeated ref answered
identically or refused as a duplicate, terminal states absorbed,
`filled_qty + remaining_qty == qty`, no duplicate fill ids, units pinned,
no initiated replace verb, and a permit that does not authorise refused
BY TYPE rather than by raising.

Until `yourproject/execution.py` is implemented the battery cannot run
against a venue — a template that answered it would be a template that
sends orders. What this file pins meanwhile is the shape a child must
keep: the class is a `SubmittingExecutor`, it declares a fencing token,
and the moment `_submit_native` is real, ONE line here turns the whole
battery on.
"""

import pytest

from dskit.production.executor import (
    Executor,
    LiveExecutor,
    SubmittingExecutor,
    executor_conformance_suite,
)

from yourproject.execution import LiveVenue


def test_the_venue_is_a_submitting_executor():
    assert issubclass(LiveVenue, LiveExecutor)
    assert issubclass(LiveVenue, SubmittingExecutor)
    assert issubclass(LiveVenue, Executor)


def test_read_query_and_cancel_are_declared_apart_from_submit():
    # §5.7's Liskov split: an executor is always constructible for reading
    # and cancelling, and only a SubmittingExecutor can send.
    for verb in ("order", "cancel", "open_orders", "fills", "balances"):
        assert callable(getattr(LiveVenue, verb)), verb


def test_no_initiated_replace_verb_exists():
    # A price or size change is cancel-to-terminal then a NEW proposal
    # through the whole guard, plan, intent and authority path.
    assert not hasattr(LiveVenue, "replace")


def test_the_conformance_battery_is_one_line_away():
    """The battery is the child's acceptance test; wire it when the venue is real.

    Replace this test with::

        TestVenueConformance = executor_conformance_suite(
            LiveVenue, {"endpoint_env": "VENUE_URL", "key_env": "VENUE_KEY"}, quotes
        )

    against a sandbox venue. Until then the template's `_submit_native`
    refuses, and running the battery would prove nothing about a venue
    that does not exist yet.
    """
    assert callable(executor_conformance_suite)
    with pytest.raises(NotImplementedError):
        LiveVenue._submit_native(LiveVenue.__new__(LiveVenue), object(), object(), 1000)
