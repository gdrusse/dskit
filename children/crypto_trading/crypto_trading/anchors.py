"""Strike anchors: observations of the settlement index, in the units the contracts settle in.

A 15-minute up/down market's strike is not a quoted level: Kalshi sets it to the PREVIOUS window's
settlement value, the index's 60-second average ending at this market's open, and publishes it a few
seconds later (checked on live data: the strike equals the previous market's ``expiration_value``).
That makes it the only observation of the settlement index (CF Benchmarks' BRTI, in USD) in the data.
Binance's price is a different instrument (USDT-quoted, on average a few bp above the index), so a
strike compared with a Binance price is a unit mismatch: it biases every fair value in one direction.
Comparing the two at the SAME instant gives the basis ``index / Binance``, which :class:`SpotFeatures`
applies to the Binance spot.

Each anchor row is ``{ticker, series, anchor_ms, known_ms, anchor_value}``: the average ended at
``anchor_ms`` (the market's open) and the value counted as known from ``known_ms`` (the open plus the
declared publication lag, ``strike_known_ms``). Anchors are a stream of rows, not tied to the market
they came from: an hourly row can use the 15-minute series' anchors of the same asset. The row carries
nothing from the market's own outcome.

Import cost: stdlib + dskit.
"""

from dskit.pipeline.node import Node, reject_unknown_params

from . import fields as f

__all__ = ["StrikeAnchors"]


class StrikeAnchors(Node):
    """Turn the up/down series' strikes into anchor rows (role ``transform``).

    Input ``records``: :class:`~crypto_trading.kalshi_rows.MarketRows` rows. Output ``records``: one
    anchor per market of an anchor series that has a floor strike, ordered by when it became known.

    Parameters
    ----------
    params : dict
        ``anchor_series`` (non-empty list of str, REQUIRED) the series whose strike is the previous
        window's settlement value (the 15-minute up/down series; a fixed-strike series is not one).

    Examples
    --------
    Anchors from the two 15-minute series::

        node = StrikeAnchors("anchors", {"anchor_series": ["KXBTC15M", "KXETH15M"]})
        out = node.run(ctx, {"records": markets})
        # -> out["records"][0] == {"ticker": ..., "series": "KXBTC15M", "anchor_ms": open_ms,
        #    "known_ms": open_ms + lag, "anchor_value": floor_strike}
    """

    role = "transform"
    outputs = ("records",)
    _PARAMS = ("anchor_series",)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        series = params.get("anchor_series")
        if not isinstance(series, list) or not series or any(not isinstance(v, str) or not v for v in series):
            problems.append(f"anchor_series is required: a non-empty list of series names, got {series!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: a function of the market rows and the params.

        Parameters
        ----------
        params : dict
            Unused.
        verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem when ``records`` is not a list.
        """
        if not isinstance(inputs.get("records"), list):
            return [f"records must be a list of market rows, got {type(inputs.get('records')).__name__}"]
        return []

    def run(self, ctx, inputs):
        """Emit the anchors.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the market rows.

        Returns
        -------
        dict
            ``{"records": [...]}``, ordered by ``known_ms`` then ticker.
        """
        wanted = set(self.params["anchor_series"])
        anchors = [{f.TICKER: m[f.TICKER], f.SERIES: m[f.SERIES], f.ANCHOR_MS: m[f.OPEN_MS],
                    f.ANCHOR_KNOWN_MS: m[f.STRIKE_KNOWN_MS], f.ANCHOR_VALUE: m[f.FLOOR]}
                   for m in inputs["records"] if m[f.SERIES] in wanted and m.get(f.FLOOR) is not None]
        anchors.sort(key=lambda a: (a[f.ANCHOR_KNOWN_MS], a[f.TICKER]))
        self.log.info("%d anchor(s) from %d market(s)", len(anchors), len(inputs["records"]))
        return {"records": anchors}
