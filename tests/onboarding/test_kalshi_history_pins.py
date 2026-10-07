"""kalshi_history pin (ADR-0236): a status the pack does not rank is scanned FIRST, whatever ranked statuses sit beside it.

The documented rule ("one the pack does not rank goes first, as given") was pinned only beside ``settled``, whose rank is the
highest, so an unranked status that sorted as if it were a low-ranked one (``open``) passed. Here it sits beside the lowest
ranks, where the difference shows.
"""

import pytest

from .test_kalshi_history import CONFIG, by_status, connector, read


@pytest.mark.parametrize("configured, scanned", [
    (["open", "zzz", "unopened", "closed"], ["zzz", "unopened", "open", "closed"]),
    (["unopened", "zzz"], ["zzz", "unopened"]),
    (["closed", "open", "aaa", "zzz"], ["aaa", "zzz", "open", "closed"]),
])
def test_an_unranked_status_is_scanned_before_every_ranked_one_in_the_order_given(configured, scanned):
    conn, script, _ = connector({"/markets": by_status()})
    read(conn, ["markets"], config={**CONFIG, "statuses": configured})
    assert [p["status"] for p in script.params("/markets")] == scanned
