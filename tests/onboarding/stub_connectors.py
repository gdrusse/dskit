"""Importable network-free doubles for the ADR-0046 vendor packs."""

from dskit.onboarding import parse_utc
from dskit.onboarding.libs.alpaca import AlpacaBarsConnector
from dskit.onboarding.libs.schwab import SchwabBarsConnector


class StubAlpacaBarsConnector(AlpacaBarsConnector):
    """An Alpaca connector whose rows are supplied by its test.

    Parameters
    ----------
    None
        Rows live on the class because acquisition resolves a fresh instance.

    Examples
    --------
    Configure one deterministic row::

        StubAlpacaBarsConnector.rows = [("AAPL", {"symbol": "AAPL",
                                                  "ts": "2026-01-02T14:30:00+00:00"})]
        connector = StubAlpacaBarsConnector()
    """

    rows = []

    def _credentials(self, knobs):
        """Return inert credentials; no vendor client is built."""
        return "stub-key", "stub-secret"

    def check(self, config):
        """Validate knobs without touching a vendor."""
        self.resolve_knobs(config)

    def _fetch(self, knobs, start_dt, end_dt):
        """Yield scripted rows inside the requested window."""
        for symbol, row in type(self).rows:
            stamp = parse_utc(row["ts"])
            if start_dt <= stamp < end_dt:
                yield symbol, dict(row)


class StubSchwabBarsConnector(SchwabBarsConnector):
    """A Schwab connector whose response bodies are supplied by tests.

    Parameters
    ----------
    None
        Responses and calls live on the class for import-path acquisitions.

    Examples
    --------
    Configure an empty response::

        StubSchwabBarsConnector.responses = {"AAPL": {"candles": []}}
        connector = StubSchwabBarsConnector()
    """

    responses = {}
    calls = []

    def _access_token(self, knobs):
        """Return an inert bearer token."""
        return "stub-token"

    def check(self, config):
        """Validate knobs without consuming a scripted response."""
        self.resolve_knobs(config)

    def _fetch(self, token, symbol, params, timeout):
        """Return the scripted body and record sanitized request metadata."""
        type(self).calls.append((symbol, dict(params), timeout))
        return dict(type(self).responses.get(symbol, {"candles": []}))
