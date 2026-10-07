"""One home for turning the venue's ISO instants into epoch milliseconds.

Everything downstream of a reader speaks epoch ms (the pipeline's and Binance's unit). The parser is
dskit's own, :func:`dskit.production.base.parse_utc_ms`, which floors to whole milliseconds in exact
integer arithmetic and REFUSES a stamp that carries no zone (a naive stamp is a guess). This module only
turns its refusal into ``None``, because a row with an unreadable instant is excluded by name, not a
crash. Kalshi sends ``Z``-suffixed stamps and the pack's retrieval stamps carry ``+00:00``, so nothing
the child reads is naive.

Import cost: stdlib + dskit.
"""

from dskit.production.base import ProductionError, parse_utc_ms

__all__ = ["instant_ms"]


def instant_ms(text):
    """Return an ISO-8601 instant that states its zone as epoch milliseconds, or None.

    Parameters
    ----------
    text : str
        The instant, e.g. ``"2026-09-02T00:15:00Z"``.

    Returns
    -------
    int or None
        Epoch milliseconds, or None when ``text`` is empty, not a string, not an instant, or has no zone.

    Examples
    --------
    Convert the venue's spelling::

        instant_ms("1970-01-01T00:00:01Z")   # -> 1000
        instant_ms("1970-01-01T00:00:01")    # -> None, no zone
    """
    try:
        return parse_utc_ms(text)
    except ProductionError:
        return None
