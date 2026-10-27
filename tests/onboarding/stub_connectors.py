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
    now = None

    def _now(self):
        """Return a scripted instant when one is configured."""
        return super()._now() if type(self).now is None else type(self).now

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


class _ScriptedResponse:
    """A context-managed HTTP response with a status and a body."""

    def __init__(self, status, body):
        self._status, self._body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def getcode(self):
        """Return the scripted HTTP status."""
        return self._status

    def read(self):
        """Return the scripted body bytes."""
        return self._body


class ScriptedUrlopen:
    """A stand-in for ``urllib.request.urlopen`` that replays scripted outcomes.

    Each outcome is an exception (raised) or a ``(status, body_bytes)`` pair
    (returned as a response). Every request is recorded as ``(url, headers,
    timeout)``; asking for more requests than were scripted raises ``IndexError``,
    which is how a test proves an error was not retried.

    Examples
    --------
    One 503 then a good body::

        fake = ScriptedUrlopen(HTTPError(url, 503, "busy", {}, None), (200, b"[]"))
        monkeypatch.setattr(urllib.request, "urlopen", fake)
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _ScriptedResponse(*outcome)
