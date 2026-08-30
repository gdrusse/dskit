"""The child's thin policy wrapper over dskit's Alpaca bars pack.

ADR-0046 graduated transport, credentials, windows, normalization, and
checkpointing to :mod:`dskit.onboarding.libs.alpaca`. This child keeps only
its model-facing policies: adjusted prices by default and minute-only bars,
because its serving cadence is minute-derived.
"""

from __future__ import annotations

from dskit.onboarding import MODES, AssetError
from dskit.onboarding.libs import alpaca as _alpaca

__all__ = [
    "BACKFILL_MODE",
    "BAR_INTERVAL",
    "BAR_KEY_FIELDS",
    "BAR_STREAM",
    "DEFAULT_ADJUSTMENT",
    "DEFAULT_FEED",
    "DEFAULT_KEY_ENV",
    "DEFAULT_LIVE_LOOKBACK_MINUTES",
    "DEFAULT_SECRET_ENV",
    "LIVE_MODE",
    "TIMEFRAME_UNITS",
    "AlpacaBarsConnector",
    "bar_timeframe",
    "resolve_credentials",
]

BACKFILL_MODE, LIVE_MODE = MODES
BAR_STREAM = _alpaca.BAR_STREAM
BAR_KEY_FIELDS = _alpaca.BAR_KEY_FIELDS
TIMEFRAME_UNITS = _alpaca.TIMEFRAME_UNITS
BAR_INTERVAL = _alpaca.BAR_INTERVAL
DEFAULT_LIVE_LOOKBACK_MINUTES = _alpaca.DEFAULT_LIVE_LOOKBACK_MINUTES
DEFAULT_FEED = _alpaca.DEFAULT_FEED
DEFAULT_ADJUSTMENT = "all"
DEFAULT_KEY_ENV = _alpaca.DEFAULT_KEY_ENV
DEFAULT_SECRET_ENV = _alpaca.DEFAULT_SECRET_ENV

_FEEDS = ("sip", "iex")
_ADJUSTMENTS = ("raw", "split", "dividend", "all")
_SIP_FEED, _IEX_FEED = _FEEDS
_SIP_LAG = _alpaca._SIP_LAG
_SIP_LAG_MINUTES = _SIP_LAG.total_seconds() / 60


def bar_timeframe(interval=None):
    """Build Alpaca's timeframe using the child's shared interval default.

    Parameters
    ----------
    interval : sequence or None
        ``(amount, unit)``; ``None`` uses :data:`BAR_INTERVAL`.

    Returns
    -------
    alpaca.data.timeframe.TimeFrame
        Vendor request interval.

    Raises
    ------
    ImportError
        If the optional ``alpaca-py`` package is unavailable.
    """
    return _alpaca.bar_timeframe(BAR_INTERVAL if interval is None else interval)


def resolve_credentials(knobs, lookup=None):
    """Resolve the named key pair through dskit's single credential rule.

    Parameters
    ----------
    knobs : dict
        Resolved connector knobs.
    lookup : callable or None
        Environment lookup used by the serving path when provided.

    Returns
    -------
    tuple
        Alpaca key id and secret.

    Raises
    ------
    AssetError
        If either named value is absent or empty.
    """
    return _alpaca.resolve_credentials(knobs, lookup)


def _timeframe_problems(value):
    """Return child-policy problems with a minute-only interval."""
    problems = _alpaca._timeframe_problems(value)
    if not problems and value[1] != "Minute":
        problems.append(
            "config.timeframe unit must be 'Minute' for this PoC "
            f"(the serving cadence is minute-derived), got {value[1]!r}"
        )
    return problems


class AlpacaBarsConnector(_alpaca.AlpacaBarsConnector):
    """Alpaca bars with the PoC's adjusted, minute-only defaults.

    Parameters
    ----------
    None
        The connector is stateless; config supplies every setting.

    Examples
    --------
    Resolve the child policy without touching Alpaca::

        connector = AlpacaBarsConnector()
        knobs = connector.resolve_knobs({
            "symbols": ["AAPL"],
            "start": "2026-01-01",
        })
        knobs["adjustment"]  # 'all'
    """

    def spec(self):
        """Declare upstream knobs with child-specific default notes.

        Returns
        -------
        dict
            The upstream catalogue, narrowed to minute bars and adjusted
            prices by default.
        """
        spec = super().spec()
        params = spec["params"]
        params["feed"]["notes"] = (
            f"Which tape to pull: {_SIP_FEED} is consolidated and "
            f"{_IEX_FEED} is real-time eligible. Default {DEFAULT_FEED}."
        )
        params["adjustment"]["notes"] = (
            f"Corporate-action adjustment: {'|'.join(_ADJUSTMENTS)}. "
            f"Default {DEFAULT_ADJUSTMENT}."
        )
        params["timeframe"]["notes"] = (
            "Bar interval as [amount, 'Minute']; serving is minute-aligned. "
            f"Default {BAR_INTERVAL}."
        )
        params["live_lookback_minutes"]["notes"] = (
            "First live pull's bounded history window. "
            f"Default {DEFAULT_LIVE_LOOKBACK_MINUTES}. On feed {_SIP_FEED} "
            f"it must exceed the {_SIP_LAG_MINUTES:g}-minute lag."
        )
        params["key_env"]["notes"] = (
            "Environment-variable name holding the Alpaca key id. "
            f"Default {DEFAULT_KEY_ENV}."
        )
        params["secret_env"]["notes"] = (
            "Environment-variable name holding the Alpaca secret. "
            f"Default {DEFAULT_SECRET_ENV}."
        )
        return spec

    def resolve_knobs(self, config):
        """Apply child defaults and refuse non-minute intervals.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        dict
            Fully resolved upstream knobs with child policy applied.

        Raises
        ------
        AssetError
            If upstream validation or minute-only policy fails.
        """
        if not isinstance(config, dict):
            return super().resolve_knobs(config)
        timeframe = config.get("timeframe", BAR_INTERVAL)
        problems = _timeframe_problems(timeframe)
        if problems:
            raise AssetError(problems)
        declared = dict(config)
        declared.setdefault("feed", DEFAULT_FEED)
        declared.setdefault("adjustment", DEFAULT_ADJUSTMENT)
        declared.setdefault("timeframe", BAR_INTERVAL)
        declared.setdefault(
            "live_lookback_minutes", DEFAULT_LIVE_LOOKBACK_MINUTES
        )
        declared.setdefault("key_env", DEFAULT_KEY_ENV)
        declared.setdefault("secret_env", DEFAULT_SECRET_ENV)
        return super().resolve_knobs(declared)

    def _credentials(self, knobs):
        """Resolve credentials through the function the live path imports."""
        return resolve_credentials(knobs)

    def discover(self, config):
        """Describe the stream using child-exported shared constants.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        list
            One bar-stream declaration.

        Raises
        ------
        AssetError
            If config or child interval policy is invalid.
        """
        stream = super().discover(config)[0]
        stream["stream"] = BAR_STREAM
        stream["primary_key"] = list(BAR_KEY_FIELDS)
        return [stream]
