"""One home for turning the venue's ISO instants into epoch milliseconds.

Everything downstream of a reader speaks epoch ms (the pipeline's and Binance's unit), so the
conversion is written once, in exact integer arithmetic (no float ``timestamp()`` round trip),
and imported by every module that parses an instant.

Import cost: stdlib + dskit.
"""

from datetime import datetime, timedelta, timezone

from dskit.onboarding.base import AssetError, parse_utc

__all__ = ["instant_ms"]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


def instant_ms(text):
    """Return an ISO-8601 instant (naive means UTC) as epoch milliseconds.

    Parameters
    ----------
    text : str
        The instant, e.g. ``"2026-09-02T00:15:00Z"``.

    Returns
    -------
    int or None
        Epoch milliseconds, or None when ``text`` is empty, not a string or not an instant.

    Examples
    --------
    Convert the venue's spelling::

        instant_ms("1970-01-01T00:00:01Z")   # -> 1000
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = parse_utc(text)
    except AssetError:
        return None
    return (moment - _EPOCH) // _MS
