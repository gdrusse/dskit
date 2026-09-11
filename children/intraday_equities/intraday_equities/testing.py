"""Deterministic doubles for the child's own tests.

Each stub keeps the real knob gate and message envelope and replaces
only the vendor-touching fetch.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

from dskit.onboarding import parse_utc
from dskit.pipeline.node import Node

from .connectors import AlpacaBars, SchwabBars
from .final_model import HEADS
from .forecast_bundle import BUNDLE_UNIT

__all__ = [
    "DEFAULT_BARS_PER_SYMBOL",
    "StubAlpacaBars",
    "StubSchwabBars",
    "SyntheticMioSource",
    "synthetic_bar",
]

#: How many bars each symbol yields when the config declares none.
DEFAULT_BARS_PER_SYMBOL = 12


def synthetic_bar(symbol, stamp, index=0):
    """Build one normalized bar payload.

    Parameters
    ----------
    symbol : str
        Ticker.
    stamp : datetime.datetime
        Bar open instant.
    index : int
        Deterministic walk offset.

    Returns
    -------
    dict
        Common bar fields.
    """
    anchor = 100.0 + sum(ord(ch) for ch in symbol) % 50
    close = anchor * (1.0 + 0.001 * math.sin(index / 3.0))
    return {
        "symbol": symbol,
        "ts": stamp.astimezone(timezone.utc).isoformat(),
        "open": round(close * 0.999, 6),
        "high": round(close * 1.001, 6),
        "low": round(close * 0.998, 6),
        "close": round(close, 6),
        "volume": 1000.0 + 10 * index,
        "trade_count": 10 + index,
        "vwap": round(close, 6),
    }


def _walk(knobs, start_dt, end_dt):
    """Yield a smooth minute walk inside the requested window."""
    base = parse_utc(knobs["start"])
    count = knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)
    for symbol in sorted(knobs["symbols"]):
        for index in range(count):
            stamp = base + timedelta(minutes=index)
            if stamp < start_dt or stamp >= end_dt:
                continue
            yield symbol, synthetic_bar(symbol, stamp, index)


class _StubMixin:
    """Shared test-only knob and credential seams."""

    def spec(self):
        """Extend the production catalogue with the stub knob.

        Returns
        -------
        dict
            Production spec plus ``bars_per_symbol``.
        """
        spec = super().spec()
        spec["params"]["bars_per_symbol"] = {
            "notes": "TEST-ONLY: bars each symbol yields from start; "
            f"default {DEFAULT_BARS_PER_SYMBOL}.",
        }
        return spec

    def resolve_knobs(self, config):
        """Resolve production knobs plus the stub's own.

        Parameters
        ----------
        config : dict
            Source config, optionally carrying ``bars_per_symbol``.

        Returns
        -------
        dict
            Resolved knobs including ``bars_per_symbol``.
        """
        knobs = super().resolve_knobs(
            {key: value for key, value in config.items() if key != "bars_per_symbol"}
        )
        knobs["bars_per_symbol"] = config.get(
            "bars_per_symbol", DEFAULT_BARS_PER_SYMBOL
        )
        return knobs

    def check(self, config):
        """Run the knob gate only.

        Parameters
        ----------
        config : dict
            Config to check.
        """
        self.resolve_knobs(config)


class StubAlpacaBars(_StubMixin, AlpacaBars):
    """Production Alpaca connector minus the network.

    Parameters
    ----------
    None
        Stateless, like the class it doubles.

    Examples
    --------
    Pull a short deterministic history without credentials::

        conn = StubAlpacaBars()
        msgs = list(conn.read(
            {"symbols": ["AAPL"], "start": "2026-01-05T14:30:00+00:00",
             "bars_per_symbol": 3},
            ["bars"], {}, "backfill",
        ))
    """

    def _credentials(self, knobs):
        """No vendor, no credentials."""
        return "stub-key", "stub-secret"

    def _fetch(self, knobs, start_dt, end_dt):
        """Yield the deterministic walk."""
        yield from _walk(knobs, start_dt, end_dt)


class StubSchwabBars(_StubMixin, SchwabBars):
    """Production Schwab connector minus the network.

    Parameters
    ----------
    None
        Stateless, like the class it doubles.

    Examples
    --------
    Resolve knobs without OAuth::

        conn = StubSchwabBars()
        knobs = conn.resolve_knobs({
            "symbols": ["AAPL"],
            "start": "2026-01-05T14:30:00+00:00",
            "bars_per_symbol": 3,
        })
        knobs["symbols"]  # ['AAPL']
    """

    def _access_token(self, knobs):
        """No vendor, no token file."""
        return "stub-token"

    def _now(self):
        """Pin the live window just after the synthetic walk."""
        knobs = getattr(self, "_last_knobs", None)
        if knobs is None:
            return parse_utc("2026-01-05T16:00:00+00:00")
        count = knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)
        return parse_utc(knobs["start"]) + timedelta(minutes=count + 1)

    def resolve_knobs(self, config):
        """Remember knobs so ``_now`` can close the synthetic window.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        dict
            Resolved knobs.
        """
        knobs = super().resolve_knobs(config)
        self._last_knobs = knobs
        return knobs

    def oauth_service(self, config):
        """Return the stub authorization surface.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        _StubOAuth
            Object with the authorize/exchange surface.
        """
        self.resolve_knobs(config)
        return _StubOAuth()

    def _get_json(self, token, symbol, params, knobs):
        """Return vendor-shaped candles from the deterministic walk.

        Parameters
        ----------
        token : str
            Unused stub bearer.
        symbol : str
            Requested ticker.
        params : dict
            Schwab query including start/end milliseconds.
        knobs : dict
            Resolved connector knobs.

        Returns
        -------
        dict
            ``{"candles": [...]}`` in Schwab's price-history shape.
        """
        start = parse_utc(knobs["start"])
        start_ms = params.get("startDate", 0)
        end_ms = params.get("endDate", 0)
        candles = []
        for index in range(knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)):
            stamp = start + timedelta(minutes=index)
            stamp_ms = int(stamp.timestamp() * 1000)
            if stamp_ms < start_ms or stamp_ms >= end_ms:
                continue
            bar = synthetic_bar(symbol, stamp, index)
            candles.append(
                {
                    "datetime": stamp_ms,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                }
            )
        return {"candles": candles}


class _StubOAuth:
    """Authorization surface that never leaves the process."""

    def authorization_url(self):
        """Return a fake URL an operator would open."""
        return "https://example.invalid/oauth/authorize?response_type=code"

    def exchange(self, returned):
        """Accept any non-empty code.

        Parameters
        ----------
        returned : str
            Callback URL or raw code.

        Returns
        -------
        dict
            Token metadata.
        """
        if not returned:
            from dskit.onboarding import AssetError

            raise AssetError(["authorization code must be a non-empty string"])
        return {"access_token": "stub", "refresh_token": "stub"}


class SyntheticMioSource(Node):
    """A deterministic toy source for the ADR-0111 MIO demo pipeline.

    Role ``data``. Emits per-instrument cluster evidence for the owned
    ``stat_test`` gate (three names, each with a clear positive edge in
    every cluster, so all three survive) plus a synthetic forecast
    ``bundle``, ``portfolio`` state, and a release-matched synthetic
    confirmed ``cap`` shaped for
    :class:`~intraday_equities.nodes_capital.EquityKellyMIO`. Referenced
    by import path from ``configs/run-mio-demo.json`` — the same "swap
    this one node for a real source" pattern
    ``examples/pipeline/pyomo-solve.json`` documents for the toolkit's
    own ``PyomoSolve`` example. NOT real market data; every number here
    is illustrative and reproducible, never fetched. Its cap is explicitly
    ``deployment_eligible=false`` and is usable only with the demo's
    ``deployment_mode=false`` plus exact artifact/producer/evidence pins.

    Parameters
    ----------
    params : dict
        ``seed`` (int >= 0, default 0) — the only knob; jitters both the
        stat_test evidence and the scenario draws deterministically.

    Examples
    --------
    ::

        node = SyntheticMioSource("source", {"seed": 0})
        out = node.run(ctx, {})
        sorted(out["bundle"][0])   # the bundle row's own field names
    """

    role = "data"
    outputs = ("scores", "bundle", "portfolio", "cap")

    #: Three names with a clear, deterministic positive edge — enough to
    #: prove the whole gate-then-size loop end to end without needing real
    #: market data.
    _NAMES = ("AAPL", "MSFT", "XOM")
    _N_CLUSTERS = 8
    _N_SCENARIOS = 64
    _PRICES = {"AAPL": 190.0, "MSFT": 410.0, "XOM": 110.0}
    _MU = {"AAPL": 0.006, "MSFT": 0.004, "XOM": 0.002}
    _SIGMA = {"AAPL": 0.012, "MSFT": 0.010, "XOM": 0.008}
    _PI_HAT = {"AAPL": 0.08, "MSFT": 0.12, "XOM": 0.18}
    _PI_UPPER = {"AAPL": 0.15, "MSFT": 0.20, "XOM": 0.25}
    _RELEASE_ID = "synthetic-mio-demo-release"
    _LEAD = 3
    _CAP_PRODUCER_DOCUMENT_SHA256 = "c" * 64
    _CAP_EVIDENCE_SHA256 = "d" * 64
    #: A fixed epoch — the decision tick is a reproducible instant, not
    #: wall-clock "now"; EquityKellyMIO reads freshness off portfolio vs.
    #: bundle timestamps alone, never off ctx.asof.
    _ASOF_MS = 1_700_000_000_000

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = []
        unknown = sorted(set(params) - {"seed"})
        if unknown:
            problems.append(f"unknown param(s) {unknown} — allowed: ['seed']")
        seed = params.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            problems.append(f"seed must be an int >= 0, got {seed!r}")
        return problems

    def fingerprint(self):
        """Identity: the kind name plus ``seed`` (dict)."""
        return {"kind": "synthetic-mio-source", "seed": self.params.get("seed", 0)}

    def run(self, ctx, inputs):
        """Build the deterministic evidence, bundle, and portfolio.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            Unused — every value here is seed-derived, never wall-clock.
        inputs : dict
            Unused — this is a source node.

        Returns
        -------
        dict
            ``scores`` (the stat_test's food), plus ``bundle``,
            ``portfolio``, and a deterministic nonproduction ``cap``
            (EquityKellyMIO's inputs).
        """
        import numpy as np

        seed = int(self.params.get("seed", 0))
        rng = np.random.default_rng(seed)
        weights = [1.0 / self._N_SCENARIOS] * self._N_SCENARIOS

        scores, bundle = {}, []
        for i, name in enumerate(self._NAMES):
            scores[name] = {
                f"c{c}": 0.02 + 0.001 * ((seed + i + c) % 5) for c in range(self._N_CLUSTERS)
            }
            draws = rng.normal(
                (1.0 - self._PI_HAT[name]) * self._MU[name],
                self._SIGMA[name],
                self._N_SCENARIOS,
            )
            bundle.append(
                {
                    "entity": name,
                    "decision_ts": self._ASOF_MS - 1000,
                    "lead": self._LEAD,
                    "model_release_id": self._RELEASE_ID,
                    "unit": BUNDLE_UNIT,
                    "price": self._PRICES[name],
                    "pi_hat": self._PI_HAT[name],
                    "pi_upper": self._PI_UPPER[name],
                    "weights": weights,
                    "scenarios": [float(v) for v in draws],
                }
            )

        portfolio = {
            "asof_ms": self._ASOF_MS,
            "cash": 20000.0,
            "buying_power": 20000.0,
            "positions": {},
            "cash_reserve": 0.0,
            "gross_limit": 12000.0,
            "sale_credit": 1.0,
        }
        cap = {
            "schema_version": 2,
            "model_release_id": self._RELEASE_ID,
            "deployment_eligible": False,
            "evidence_scope": "synthetic_mio_demo",
            "evidence_end_ms": self._ASOF_MS - 2000,
            "generated_ms": self._ASOF_MS - 1000,
            "producer": {
                "document_sha256": self._CAP_PRODUCER_DOCUMENT_SHA256,
                "node": "source",
                "output": "cap",
            },
            "evidence": {
                "sha256": self._CAP_EVIDENCE_SHA256,
                "scope": "synthetic_mio_demo",
                "end_ms": self._ASOF_MS - 2000,
            },
            "caps": [
                {"symbol": name, "capped_horizon": len(HEADS)}
                for name in self._NAMES
            ],
        }
        return {
            "scores": scores,
            "bundle": bundle,
            "portfolio": portfolio,
            "cap": cap,
        }
