"""The pre-registered ThetaData hypothesis documents H0-H7 and their validation twins (ADR-0265 Amendment 5).

Every document is a pure function of two things: the :data:`INPUTS` file (store locations, pins, the frozen
universe) and a :class:`Period`. The development document and the validation document of one hypothesis are the
SAME graph with the period's dates swapped, so the validation run is the development run's pre-registered
statistic on the later years and nothing else; ``configs/theta-hN-validation-args.json`` is that overlay, and
:func:`apply_args` is the only way a validation document is made. The config test pins every committed file to
this module, so a document changes through here, never by hand.

Nothing here runs anything: no hypothesis is evaluated by writing a document. The documents read only the
Parquet export tree, the panel of chain-day features and the Yahoo daily bars (all read-only), write only under
``outputs.run_root`` and refuse any date on or after 2026-01-01.

CLI (from ``children/index_options``)::

    python -m index_options.theta_hypotheses write              # regenerate every committed file
    python -m index_options.theta_hypotheses check              # exit 1 when a committed file differs
    python -m index_options.theta_hypotheses set-pin NAME HASH  # record a registered panel's snapshot pin
    python -m index_options.theta_hypotheses freeze-universe CENSUS_ROOTS_JSONL  # write the frozen universe
"""

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["HYPOTHESES", "INPUTS_FILE", "PERIODS", "Period", "apply_args", "build", "files", "main"]

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
INPUTS_FILE = "theta-hypothesis-inputs.json"
EVENTS_FILE = "run-theta-chain-events.json"
RUN_BASE = "/home/russell/data/index_options/theta-research-20261012/hypotheses"
SEALED = "2026-01-01"
AGREE = "agree"
COMPUTE = "dskit.pipeline.kinds_compute:ComputeFields"
SERIES = "dskit.pipeline.kinds_series:StampSeriesStats"
IC = "dskit.pipeline.kinds_stats:CrossSectionIC"
BOOK = "dskit.pipeline.kinds_stats:QuantileSortBook"
PARQUET_STREAM = "dskit.pipeline.libs.parquet:ParquetStreamRows"
CHAIN_ROWS = "dskit.pipeline.libs.chain_parquet:ParquetChainRows"
BAR_FEATURES = "dskit.pipeline.libs.bar_features:DailyBarFeatures"
LEDGER = "dskit.pipeline.libs.option_ledger:OptionStructureLedger"
ORDERS = "dskit.pipeline.libs.chain_orders:ChainStructureOrders"
DETECTOR = "dskit.pipeline.libs.chain_events:ChainEventDetector"
SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)
SESSIONS_31D = 21
SESSIONS_PER_YEAR = 252
BOOTSTRAP = {"block_days": 90, "replicates": 2000, "seed": 20261012, "level": 0.9}

#: The ADR-0265 item-5 text of each hypothesis, verbatim: the rule the document pre-registers.
ADR_RULE = {
    "H0": "**H0** HAR-versus-implied straddle richness (the open ledger question): per-date Spearman IC of HAR "
          "scale / implied straddle against |31-day move| / implied; pass IC > 0, Newey–West t >= 2.5, quintile "
          "spread net of NBBO costs > 0",
    "H1": "**H1** option-implied features → next 5- and 21-day stock return: 25-delta put-call IV spread, "
          "risk-reversal skew, 30/90-day term slope, open-interest put/call ratio and its 5-day change, volume/OI. "
          "Per-stamp Spearman IC, sign declared per feature before the run, NW t >= 2.5; decile spread > 10 bp round "
          "trip; BH across the 6 features.",
    "H2": "**H2** earnings: (a) implied move (front straddle) minus realized gap move; (b) long ATM straddle bought "
          "3 sessions before the event, sold the session after, at NBBO. Events are detected from the chain itself "
          "(front-month IV collapse above a threshold frozen on 2016–2019 dates). Pass: mean > 0, t clustered by "
          "event week >= 2.5, same sign in both halves, terciles by liquidity.",
    "H3": "**H3** cross-section of 31-day ATM straddle returns sorted by RV−IV gap, term slope, skew, idiosyncratic "
          "vol: quintile long-short net of actual half-spreads > 0, t >= 2.5, BH across 4 sorts.",
    "H4": "**H4** per-name variance risk premium (level, slope, regime via `VolRegimeSignals`) and weekly versus "
          "monthly: descriptive with CIs; the timing signal passes if gated-minus-ungated short-straddle return > 0, "
          "t >= 2.5.",
    "H5": "**H5** index harvests on SPX/SPXW/XSP at NBBO (put-write, 1DTE strangle/condor, 16/5-delta put spread, "
          "tail-hedge cost): report Sharpe with block-bootstrap CI; pass only if the lower 90% bound > 0.5. "
          "Measurement, not proof.",
    "H6": "**H6** dispersion (index versus equal-weight basket of tested names; equal-weight proxy stated): "
          "correlation premium > 0, t >= 2.5 in both halves.",
    "H7": "**H7** pin/expiry (return toward the max-open-interest strike versus a placebo strike) and a spread "
          "census of wide-spread names.",
}
COMMON_RULES = (
    "COMMON (ADR-0265 decisions 1-4): Split. Development 2016-01-04..2020-12-31; validation 2021-01-04..2025-12-31, "
    "run once per hypothesis, unchanged, only if the development run passed; 2024-25 is a labelled subperiod, never "
    "the sole basis of a pass; 2026 stays sealed. Universe gate: a root enters a test only from its first date with "
    "bid > 0 (the census); roots are stitched across renames. Cost model: buy at the ask, sell at the bid (NBBO); no "
    "mid fills; `close` is never a mark; our own Black-76 implied vol from the mid and the parity forward. Multiple "
    "testing: Benjamini-Hochberg 5% within a hypothesis's feature family; Bonferroni across the families for any "
    "headline claim; secondary analyses are reported, never promoted.")


@dataclass(frozen=True)
class Period:
    """One run window: the dates every reader and node of a document is bounded by."""

    name: str
    start: str
    end_before: str
    bars_start: str
    halves_at: str
    suffix: str

    @property
    def args_file(self):
        return None if self.name == "development" else f"{{h}}-{self.name}-args.json"


DEV = Period("development", "2016-01-04", "2021-01-01", "2016-01-04", "2018-07-01", "")
VAL = Period("validation", "2021-01-04", SEALED, "2020-09-01", "2023-07-01", "-validation")
PERIODS = {"development": DEV, "validation": VAL}


def load_inputs(path=None):
    """The inputs file: store locations, snapshot pins, the frozen universe and the event-rule source."""
    return json.loads((Path(path) if path else CONFIGS / INPUTS_FILE).read_text())


# ------------------------------------------------------------------------------------------------------
# the graph builder
# ------------------------------------------------------------------------------------------------------


class Graph:
    """An ordered node map with the few recurring wirings (attach, project, bounded readers) written once."""

    def __init__(self, inputs, period):
        self.inputs, self.period, self.nodes = inputs, period, {}

    def add(self, key, uses, params=None, inputs=None, notes=""):
        if key in self.nodes:
            raise ValueError(f"node {key!r} declared twice")
        node = {"uses": uses}
        if inputs:
            node["inputs"] = inputs
        if params is not None:
            node["params"] = params
        node["notes"] = notes
        self.nodes[key] = node
        return key

    @staticmethod
    def ref(key, port="records"):
        return f"${key}.{port}"

    # -- readers -----------------------------------------------------------------------------------------

    def panel(self, key, which, columns, notes):
        """The date-partitioned panel of chain-day features, read through its registered, pinned snapshot."""
        spec, p = self.inputs[which], self.period
        return self.add(key, PARQUET_STREAM, {"sources": [{
            "root": spec["root"], "source": spec["source"], "stream": "files", "snapshot": spec["snapshot"],
            "columns": {c: c for c in columns}, "include": ["*.parquet"], "iso_fields": ["date", "expiration"]
            if "expiration" in columns else ["date"],
            "read_bounds": {"date": {"start": p.start, "end_before": p.end_before}}}]}, notes=notes)

    def bars(self, key, columns, benchmark=False, notes=""):
        """The Yahoo daily bars (split-adjusted close, split+dividend adjusted close) of the tested names."""
        p, b = self.period, self.inputs["bars"]
        if benchmark:
            sources = [{**b["benchmark"], "include": [b["benchmark"]["include"]]}]
        else:
            sources = [{**s, "include": ["*/underlying_prices.parquet"]} for s in b["tested"]]
        full = [{"root": b["root"], "stream": "files", "columns": columns,
                 "read_bounds": {"date": {"start": p.bars_start, "end_before": p.end_before}}, **s} for s in sources]
        return self.add(key, PARQUET_STREAM, {"sources": full}, notes=notes)

    def chain(self, key, which, symbols, fields, notes):
        """The daily option chain from the Parquet export tree, symbol by symbol, bounded to the period."""
        c, p = self.inputs[which], self.period
        return self.add(key, CHAIN_ROWS, {
            "directories": c["directories"], "symbol_field": "symbol", "date_field": "date", "fields": fields,
            "symbols": symbols, "start": p.start, "end_before": p.end_before, "sealed_from": SEALED,
            **(self.inputs["renames"] if which == "stock_chain" else {})}, notes=notes)

    # -- small verbs ---------------------------------------------------------------------------------------

    def filter(self, key, source, where, notes):
        return self.add(key, "filter", {"where": where}, {"records": self.ref(source)}, notes)

    def compute(self, key, source, fields, notes, keep=None):
        params = {"fields": fields}
        if keep is not None:
            params["keep"] = keep
        return self.add(key, COMPUTE, params, {"records": self.ref(source)}, notes)

    def agreed(self, key, source, notes="Adds the constant the keyed attachments below agree on."):
        return self.compute(key, source, {AGREE: {"const": 1}}, notes)

    def attach(self, key, stream, table, identity, fields, absent, notes, rename=None, stream_port="records"):
        """Attach ``fields`` of ``table`` onto ``stream`` by ``identity``; the table is first projected to exactly them."""
        computed = {AGREE: {"const": 1}}
        keep = list(identity)
        for name in fields:
            source = (rename or {}).get(name)
            if source:
                computed[name] = {"field": source}
            else:
                keep.append(name)
        t = self.compute(f"{key}_table", table, computed, f"The columns of {key} the attachment demands: "
                         f"{identity} + the agreed constant + {fields}, nothing else.", keep=keep)
        return self.add(key, "attach-by-identity", {
            "identity": identity, "agree_fields": [AGREE], "max_absent_fraction": absent,
            "column_prefix": f"{key}_",
            "families": {key: {"fields": fields, "withheld_fields": {}, "clock_note": notes}}},
            {"records": self.ref(stream, stream_port), "table": self.ref(t)}, notes)

    def pairs(self, key, source, horizon, close, out, notes):
        return self.add(key, "horizon-pairs", {
            "horizon_days": horizon, "fallback": "previous", "symbol_field": "symbol", "date_field": "date",
            "close_field": close, "fields": {"symbol": "symbol", "date": "date",
                                             "settlement_date": f"settle_{out}", "entry_close": f"c0_{out}",
                                             "settle_close": f"c1_{out}", "terminal_return": out,
                                             "period": f"year_{out}"}},
            {"records": self.ref(source)}, notes)

    def half(self, key, source, field="date"):
        p = self.period
        return self.add(key, "derive", {"field": "half", "cases": [
            {"when": [{"field": field, "op": "<", "value": p.halves_at}], "value": "first"},
            {"when": [], "value": "second"}]}, {"records": self.ref(source)},
            f"The two halves of the period, split at {p.halves_at} (the same-sign-in-both-halves check).")

    def write(self, key, source, port, name, what):
        return self.add(key, "records-write", {
            "path": f"{self.run_root()}/{name}.jsonl", "overwrite": True, "source": what},
            {"records": self.ref(source, port)}, "Audit rows, overwritten on a rerun: the run directory keeps the hashed record.")

    def report(self, key, title, edges):
        return self.add(key, "run-report", {"title": title, "sections": ["stages"]},
                        {name: self.ref(src, "summary") for name, src in edges.items()},
                        "evidence.md renders the node summaries; evidence.json holds every value.")

    def run_root(self):
        return f"{RUN_BASE}/{self.name}{self.period.suffix}"


def _series_node(g, key, source, value, stamp, notes, **over):
    params = {"stamp_field": stamp, "value_field": value, **over}
    return g.add(key, SERIES, params, {"records": g.ref(source)}, notes)


# ------------------------------------------------------------------------------------------------------
# shared study fragments
# ------------------------------------------------------------------------------------------------------

PANEL_BASE = ["symbol", "date", "target_dte", "status", "after_first_quote", "liquidity_tercile"]
PANEL_FEATURES = ["expiration", "dte", "spot", "forward", "atm_strike", "atm_iv", "put25_iv", "call25_iv",
                  "risk_reversal", "put_call_iv_spread", "term_slope", "straddle_ratio",
                  "half_spread_over_premium", "oi_call", "oi_put", "volume_call", "volume_put",
                  "oi_put_call_ratio", "oi_change_5d", "oi_put_call_ratio_change_5d"]
BAR_COLUMNS = {"symbol": "symbol", "date": "date", "open": "open", "close": "close",
               "adjusted_close": "adjusted_close"}
GATE = [{"field": "after_first_quote", "op": "==", "value": True}, {"field": "status", "op": "==", "value": "ok"}]


def _f(name):
    return {"field": name}


def _op(op, *args):
    return {"op": op, "args": list(args)}


def _c(value):
    return {"const": value}


def _panel_30(g, columns=None):
    """The 30-DTE rows of the panel past each name's first quote and with a resolved forward."""
    cols = PANEL_BASE + (columns or PANEL_FEATURES)
    g.panel("panel", "panel", cols, "The panel of chain-day features (ChainPanelBuild), every landed root, both targets, one file a "
            "date, read through its registered snapshot pin; `date` and `expiration` as ISO text. Bounded to the period's "
            "dates by predicate pushdown.")
    g.filter("panel_ok", "panel", GATE, "Rows from each name's first quoted date (after_first_quote) whose forward and vols resolved.")
    return g.filter("p30", "panel_ok", [{"field": "target_dte", "op": "==", "value": 30}],
                    "The 30-DTE target (the nearest listed expiry within 7 days of 30).")


def _bar_features(g, windows=(5, 22, 66), idio=66):
    """Trailing realized vol, the 1-bar return and the idiosyncratic vol of every tested name, from adjusted closes."""
    g.bars("bars_raw", BAR_COLUMNS, notes="Yahoo daily bars of the tested names (stock-daily-bars + stock-daily-bars-300 pinned "
           "snapshots, split-adjusted close and split+dividend adjusted close), read from bars_start so the trailing windows are "
           "full on the first day of the period; nothing at or after the period's end_before is read.")
    g.bars("bench_raw", BAR_COLUMNS, benchmark=True, notes="SPY daily bars (benchmark-daily-bars), the benchmark of the idiosyncratic vol.")
    g.filter("bars_ok", "bars_raw", [{"field": "adjusted_close", "op": ">", "value": 0}, {"field": "close", "op": ">", "value": 0}],
             "A bar with a missing or non-positive close carries no price; dropped here, counted in the log.")
    g.filter("bench_ok", "bench_raw", [{"field": "adjusted_close", "op": ">", "value": 0}], "Benchmark bars with a price.")
    return g.add("bar_feats", BAR_FEATURES, {
        "entity_field": "symbol", "date_field": "date", "close_field": "adjusted_close",
        "benchmark_date_field": "date", "benchmark_close_field": "adjusted_close",
        "realized_vol": {"windows": list(windows), "field": "rv_{window}", "return_field": "ret_1d",
                         "idio_windows": [idio], "idio_field": "idio_vol_{window}"}},
        {"bars": g.ref("bars_ok"), "benchmark": g.ref("bench_ok")},
        "Trailing realized volatility (root mean square of the last w 1-bar log returns of the ADJUSTED close, per session, zero-mean, "
        "null until the window is full), the 1-bar log return and the idiosyncratic vol (residual std of the return on SPY's over "
        "66 bars). Same-day: the value at t reads bars up to t.")


# ------------------------------------------------------------------------------------------------------
# the hypotheses
# ------------------------------------------------------------------------------------------------------


def build_h0(inputs, period):
    g = Graph(inputs, period)
    g.name = "h0-har-implied"
    p30 = _panel_30(g)
    feats = _bar_features(g)
    g.pairs("move31", "bars_ok", 31, "close", "move31", "The realized 31-calendar-day log move of the (split-adjusted) close from every close, settled on the "
            "target day's close or the latest earlier one; an entry whose target lies past the last close of the period is dropped, so "
            "no outcome crosses the period's end.")
    base = g.agreed("p30_agree", p30)
    g.attach("rv", base, feats, ["symbol", "date"], ["rv_5", "rv_22", "rv_66"], 0.2,
             "Trailing realized vols known at the close of the date (same-day with the NBBO the straddle is read from).")
    g.attach("fwd", "rv", "move31", ["symbol", "date"], ["move31"], 0.2,
             "The 31-day move settles after the date; label-time value, scored only.")
    g.filter("scored_in", "fwd", [{"field": f, "op": ">", "value": 0} for f in ("straddle_ratio", "rv_5", "rv_22", "rv_66")]
             + [{"field": "move31", "op": "!=", "value": None}],
             "The one row set every measure scores: a two-sided straddle, three realized vols and a settled move.")
    expected = _op("mul", _c(SQRT_2_OVER_PI * math.sqrt(SESSIONS_31D)), _op("exp", _op("div", _op("add", _op("log", _f("rv_5")), _op("log", _f("rv_22")), _op("log", _f("rv_66"))), _c(3))))
    rv_only = _op("mul", _c(SQRT_2_OVER_PI * math.sqrt(SESSIONS_31D)), _f("rv_22"))
    short = _op("div", _op("sub", _f("straddle_ratio"), _op("abs", _op("sub", _op("exp", _f("move31")), _c(1)))), _f("straddle_ratio"))
    g.compute("scored", "scored_in", {
        "har_scale": expected, "rv22_scale": rv_only,
        "richness_har": _op("log", _op("div", _f("straddle_ratio"), expected)),
        "richness_rv22": _op("log", _op("div", _f("straddle_ratio"), rv_only)),
        "short_straddle_ret": short},
        "HAR scale = the geometric mean of the 5-, 22- and 66-session realized vols (a no-fit HAR: equal log weights, nothing estimated, "
        "so nothing can leak) times sqrt(2/pi) sqrt(21), the expected absolute 31-day move of a normal; rv22_scale is the single-window "
        "control. richness = log(implied straddle / scale). short_straddle_ret is the MID-basis proxy (straddle - |31-day move|)/straddle "
        "of a short straddle held 31 calendar days; it is a measurement of the ordering, not a tradable P&L (H3 trades the ledger).")
    g.half("phased", "scored")
    g.add("ic", IC, {"stamp_field": "date", "series_field": "symbol", "measures": {
        "har_beyond_market": {"forecast_field": "har_scale", "outcome_field": "move31", "control_field": "straddle_ratio", "outcome_transform": "abs"},
        "rv22_beyond_market": {"forecast_field": "rv22_scale", "outcome_field": "move31", "control_field": "straddle_ratio", "outcome_transform": "abs"},
        "har_alone": {"forecast_field": "har_scale", "outcome_field": "move31", "outcome_transform": "abs"},
        "implied_alone": {"forecast_field": "straddle_ratio", "outcome_field": "move31", "outcome_transform": "abs"}},
        "h_steps": SESSIONS_31D, "splits": ["half"], "by_month": False}, {"records": g.ref("phased")},
        "PRIMARY: har_beyond_market, the per-date partial Spearman of the HAR scale with |31-day move| given the implied straddle (pass: mean IC > 0, "
        "Newey-West t >= 2.5). AMENDMENT 5, recorded before any run: the ADR wrote the IC on the ratio HAR/implied against |move|/implied; the node refuses a "
        "ratio form because dividing both sides by one noisy field correlates two independent numerators (ADR-0259 amendment 1), so a noise forecast "
        "passes it. The partial form answers the same question without that artifact. rv22_beyond_market is the single-window control (does HAR add over rv22); "
        "har_alone and implied_alone are the market's own ordering skill, the bar to clear. h_steps 21: a 31-day label overlaps about 21 daily stamps.")
    g.add("book", BOOK, {"stamp_field": "date", "series_field": "symbol", "quantiles": 5, "min_names": 50, "alpha": 0.05,
                         "signals": {"har": {"signal_field": "richness_har", "outcome_field": "short_straddle_ret", "cost_field": "half_spread_over_premium"},
                                     "rv22": {"signal_field": "richness_rv22", "outcome_field": "short_straddle_ret", "cost_field": "half_spread_over_premium"}},
                         "sides_per_leg": 1, "h_steps": SESSIONS_31D, "bootstrap": BOOTSTRAP, "splits": ["half"]},
          {"records": g.ref("phased")},
          "PRIMARY: the quintile spread of the har signal, long the richest quintile's SHORT straddle and short the cheapest quintile's (sign +1: the top bucket pays the "
          "short-straddle return), net of each leg's mean half-spread over premium paid once at entry (the proxy is held to a 31-day close, no exit crossing). Pass: "
          "net mean > 0. The rv22 signal is the control. Overlapping 31-day labels on daily stamps: h_steps 21.")
    g.write("ic_by_stamp", "ic", "records", "ic-by-stamp", "one row per measure and date: the cross-sectional Spearman rho and names ranked")
    g.write("book_by_stamp", "book", "records", "book-by-stamp", "one row per signal and date: quintile means, gross, cost, net")
    g.report("report", "H0 HAR versus implied straddle richness", {"ic": "ic", "book": "book"})
    return g


def _entries(g, p30, features_attached=None):
    """One straddle entry per (symbol, expiration): the first session on which the 30-DTE target expiry is at most 31 days out."""
    g.filter("near31", p30, [{"field": "dte", "op": "<=", "value": 31}, {"field": "atm_strike", "op": "!=", "value": None},
                             {"field": "atm_iv", "op": ">", "value": 0}],
             "Rows whose 30-DTE target expiry is at most 31 calendar days out and whose ATM strike resolved.")
    keep = {"date": "date", "atm_strike": "atm_strike", "atm_iv": "atm_iv", "term_slope": "term_slope",
            "risk_reversal": "risk_reversal", "half_spread_over_premium": "half_spread_over_premium",
            "liquidity_tercile": "liquidity_tercile", "dte_open": "dte"}
    aggregates = {out: {"op": "last", "field": src} for out, src in keep.items()}
    return g.add("entries", "groupby", {"keys": ["symbol", "expiration"], "order_field": "dte", "aggregates": aggregates},
                 {"records": g.ref("near31")},
                 "ONE entry per (symbol, expiry): ordered by days-to-expiry, the last row is the earliest session with the expiry at most 31 days out "
                 "(DTE 31 or the first later session when the symbol's cycle skips it). Entries therefore never overlap on one expiry.")


def _straddle_orders(g, source, structure, notes):
    return g.compute("orders", source, {
        "open_date": _f("date"), "strikes": {"list": [_f("atm_strike"), _f("atm_strike")]},
        "structure": {"text": structure}},
        notes, keep=["symbol", "expiration", "date", "term_slope", "risk_reversal", "half_spread_over_premium",
                     "atm_iv", "liquidity_tercile", "atm_strike"])


def _ledger(g, key, orders, chain, structures, fill, close_rule, notes, order_exit=False, extra=None, orders_port="records"):
    fields = {"date": "open_date", "symbol": "symbol", "expiration": "expiration", "strikes": "strikes", "structure": "structure"}
    if order_exit:
        fields["exit"] = "exit"
    params = {"chain_fields": {"symbol": "symbol", "date": "date", "expiration": "expiration", "strike": "strike", "right": "right",
                               "bid": "bid", "ask": "ask", "underlying": "underlying_price"},
              "order_fields": fields, "structures": structures, "multiplier": 100, "fill": fill, "close_rule": close_rule}
    params.update(extra or {})
    return g.add(key, LEDGER, params, {"chain": g.ref(chain), "orders": g.ref(orders, orders_port)}, notes)


STRADDLE_LEGS = [["call", 1], ["put", 1]]
SHORT_LEGS = [["call", -1], ["put", -1]]
LEDGER_FIELDS = ["symbol", "expiration", "strike", "right", "bid", "ask", "underlying_price"]


def build_h3(inputs, period):
    g = Graph(inputs, period)
    g.name = "h3-straddle-sorts"
    p30 = _panel_30(g)
    feats = _bar_features(g)
    entries = _entries(g, p30)
    g.compute("entries_k", entries, {"open_date": _f("date")}, "The entry date as the ledger's open_date.")
    base = g.agreed("entries_agree", "entries_k")
    g.attach("rv", base, feats, ["symbol", "date"], ["rv_22", "idio_vol_66"], 0.3,
             "Trailing realized vol and idiosyncratic vol known at the entry close.")
    g.compute("signals_in", "rv", {
        "rv_iv_gap": _op("sub", _op("mul", _f("rv_22"), _c(math.sqrt(SESSIONS_PER_YEAR))), _f("atm_iv")),
        "idio_ann": _op("mul", _f("idio_vol_66"), _c(math.sqrt(SESSIONS_PER_YEAR)))},
        "RV-IV gap = annualised 22-session realized vol minus the 30-day ATM implied vol (positive: the straddle is cheap). Term slope and skew (risk reversal, "
        "put 25-delta IV minus call 25-delta IV) come from the panel.")
    g.compute("orders", "signals_in", {"strikes": {"list": [_f("atm_strike"), _f("atm_strike")]}, "structure": {"text": "long_straddle"}},
              "One ATM long straddle per entry: both legs at the panel's ATM strike (nearest the parity forward among strikes quoted on both sides), the "
              "target expiry. Held to expiry and settled at intrinsic.")
    g.chain("chain", "stock_chain", inputs["universe"], ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying_price"],
            "The daily chain of the frozen universe from the Parquet tree (symbol by symbol, dates ascending), bounded to the period; the ledger reads each needed "
            "contract once.")
    _ledger(g, "ledger", "orders", "chain", {"long_straddle": STRADDLE_LEGS}, "mid", "expiry",
            "Opens each long straddle and settles it at intrinsic on the expiration date. FILL IS THE MID here by design: QuantileSortBook subtracts each leg's actual "
            "half-spread over premium (cost_field) once at entry, which is the ask/bid cost of the ADR (a bought straddle pays the half-spread, a sold one gives it up; "
            "settlement at intrinsic crosses nothing). The NBBO sides of the open are checked by the ledger either way (an unusable quote refuses the order). The expiry "
            "settle reads the vendor underlying_price of the expiration date; days the panel flagged as stale vendor spots are not removed (disclosed).")
    g.filter("closed", "ledger", [{"field": "status", "op": "==", "value": "closed"}], "Only settled trades have an outcome; trades the period ends before are dropped.")
    ledger_trades = "closed"
    g.nodes["closed"]["inputs"] = {"records": "$ledger.trades"}
    g.attach("outcome", "signals_in", ledger_trades, ["symbol", "open_date"], ["pnl_over_premium", "close_date"], 0.3,
             "The long straddle's return over its premium (mid fill, settled at intrinsic) and when it closed: label-time values.")
    g.filter("scored", "outcome", [{"field": "pnl_over_premium", "op": "!=", "value": None}, {"field": "half_spread_over_premium", "op": ">=", "value": 0}],
             "Entries with a settled outcome and a measured half-spread.")
    g.half("phased", "scored", "open_date")
    signals = {
        "rv_iv_gap": {"signal_field": "rv_iv_gap", "outcome_field": "pnl_over_premium", "sign": 1, "cost_field": "half_spread_over_premium"},
        "term_slope": {"signal_field": "term_slope", "outcome_field": "pnl_over_premium", "sign": 1, "cost_field": "half_spread_over_premium"},
        "skew": {"signal_field": "risk_reversal", "outcome_field": "pnl_over_premium", "sign": -1, "cost_field": "half_spread_over_premium"},
        "idio_vol": {"signal_field": "idio_ann", "outcome_field": "pnl_over_premium", "sign": -1, "cost_field": "half_spread_over_premium"}}
    g.add("book", BOOK, {"stamp_field": "open_date", "series_field": "symbol", "quantiles": 5, "min_names": 50, "alpha": 0.05, "signals": signals,
                         "sides_per_leg": 1, "h_steps": 1, "bootstrap": BOOTSTRAP, "splits": ["half"], "outcome_after_field": "close_date"},
          {"records": g.ref("phased")},
          "PRIMARY: for each of the four sorts the quintile long-short of the 31-day ATM straddle return, long the declared-direction extreme (sign +1 longs the highest "
          "signal's straddle: RV-IV gap, term slope; sign -1 longs the lowest: skew, idiosyncratic vol) and short the other (a short straddle), net of each leg's mean actual "
          "half-spread over premium paid once at entry. Pass: net mean > 0, Newey-West t >= 2.5, Benjamini-Hochberg across the 4 sorts at 5% (the node's family; "
          "Bonferroni is reported beside it). The signs are the pre-declared directions: a cheap straddle (RV above IV, upward slope) is bought; steep skew and high "
          "idiosyncratic vol (expensive tails, lottery demand) are sold. outcome_after_field makes the node refuse an outcome that did not start after its stamp.")
    g.write("book_by_stamp", "book", "records", "book-by-stamp", "one row per sort and entry date: quintile means, gross, cost, net")
    g.report("report", "H3 cross-section of 31-day ATM straddle returns", {"book": "book", "ledger": "ledger"})
    return g


def build_h1(inputs, period):
    g = Graph(inputs, period)
    g.name = "h1-implied-features-ic"
    p30 = _panel_30(g)
    g.bars("bars_raw", BAR_COLUMNS, notes="Yahoo daily bars of the tested names (pinned snapshots); the forward returns use the ADJUSTED close (split + dividend adjusted: total return).")
    g.filter("bars_ok", "bars_raw", [{"field": "adjusted_close", "op": ">", "value": 0}], "Bars with a price.")
    g.pairs("fwd5", "bars_ok", 7, "adjusted_close", "fwd5", "The next 5 sessions: the log return from the close to the close 7 calendar days later (or the latest earlier close). An entry whose "
            "target lies past the period's last close is dropped, so no outcome crosses the period's end.")
    g.pairs("fwd21", "bars_ok", 30, "adjusted_close", "fwd21", "The next 21 sessions: 30 calendar days. Dropped past the period's last close like fwd5.")
    base = g.compute("features", p30, {"volume_over_oi": _op("div", _op("add", _f("volume_call"), _f("volume_put")), _op("add", _f("oi_call"), _f("oi_put")))},
                     "volume / open interest = (call + put volume) / (call + put open interest) of the target expiry; null when open interest is zero.")
    agreed = g.agreed("features_agree", base)
    g.attach("ret5", agreed, "fwd5", ["symbol", "date"], ["fwd5"], 0.3, "The 5-session forward return, known after the date.")
    g.attach("ret21", "ret5", "fwd21", ["symbol", "date"], ["fwd21"], 0.3, "The 21-session forward return, known after the date.")
    g.half("rows", "ret21")
    features = {"put_call_iv_spread": ("put_call_iv_spread", -1), "risk_reversal": ("risk_reversal", -1), "term_slope": ("term_slope", -1),
                "oi_put_call_ratio": ("oi_put_call_ratio", -1), "oi_put_call_ratio_change": ("oi_put_call_ratio_change_5d", -1),
                "volume_over_oi": ("volume_over_oi", -1)}
    sign_note = ("SIGNS declared before the run, all -1 (higher feature, lower next return): downside demand (put-call IV spread, risk reversal, put/call open interest and its "
                 "change) prices or predicts weakness; a steeper term slope is calm that mean-reverts (stress premium sits at inverted slopes); heavy option activity relative to open "
                 "interest marks informed trading that leans bearish (Johnson-So).")
    for tag, horizon, steps in (("5", "fwd5", 5), ("21", "fwd21", 21)):
        measures = {name: {"forecast_field": field, "outcome_field": horizon, "sign": sign} for name, (field, sign) in features.items()}
        g.add(f"ic{tag}", IC, {"stamp_field": "date", "series_field": "symbol", "measures": measures, "h_steps": steps, "splits": ["half"],
                                "family": {"alpha": 0.05, "corrections": ["bh", "bonferroni"]}}, {"records": g.ref("rows")},
              f"PRIMARY ({tag}-session return): per-date Spearman IC of each feature with the next-{tag}-session return, tested in its declared direction (Newey-West, h_steps {steps}); "
              "pass per feature: signed IC mean > 0 with t >= 2.5; Benjamini-Hochberg at 5% across the 6 features (Bonferroni beside it; across the two horizons Bonferroni "
              "applies for any headline). " + sign_note)
        signals = {name: {"signal_field": field, "outcome_field": horizon, "sign": sign} for name, (field, sign) in features.items()}
        g.add(f"decile{tag}", BOOK, {"stamp_field": "date", "series_field": "symbol", "quantiles": 10, "min_names": 100, "alpha": 0.05, "signals": signals,
                                     "cost_per_side": 0.0005, "sides_per_leg": 2, "h_steps": steps, "bootstrap": BOOTSTRAP, "splits": ["half"]},
              {"records": g.ref("rows")},
              f"The decile long-short of each feature on the next-{tag}-session return, long the declared-direction extreme decile, net of 10 bp round trip on EACH leg "
              "(cost_per_side 5 bp, two sides per leg: 20 bp for the book; the ADR's 10 bp round trip read as per leg, the stricter reading). Pass: net mean > 0 "
              "with Benjamini-Hochberg across the 6 features. Overlapping labels on daily stamps: h_steps = the horizon.")
        g.write(f"ic{tag}_by_stamp", f"ic{tag}", "records", f"ic{tag}-by-stamp", "one row per feature and date: the cross-sectional Spearman rho and names ranked")
        g.write(f"decile{tag}_by_stamp", f"decile{tag}", "records", f"decile{tag}-by-stamp", "one row per feature and date: decile means, gross, cost, net")
    g.report("report", "H1 option-implied features and the next 5- and 21-session return",
             {"ic5": "ic5", "decile5": "decile5", "ic21": "ic21", "decile21": "decile21"})
    return g


def _short_orders(g, source):
    return g.compute("orders", source, {"strikes": {"list": [_f("atm_strike"), _f("atm_strike")]}, "structure": {"text": "short_straddle"}},
                     "One ATM short straddle per entry at the panel's ATM strike and target expiry, held to expiry.")


def build_h4(inputs, period):
    g = Graph(inputs, period)
    g.name = "h4-vrp"
    p30 = _panel_30(g)
    feats = _bar_features(g, windows=(22,), idio=66)
    entries = _entries(g, p30)
    g.compute("entries_k", entries, {"open_date": _f("date")}, "The entry date as the ledger's open_date.")
    base = g.agreed("entries_agree", "entries_k")
    g.attach("rv", base, feats, ["symbol", "date"], ["rv_22"], 0.3, "Trailing 22-session realized vol known at the entry close.")
    g.filter("rv_ok", "rv", [{"field": "rv_22", "op": ">", "value": 0}, {"field": "atm_iv", "op": ">", "value": 0}, {"field": "term_slope", "op": "!=", "value": None}],
             "Entries with an implied vol, a realized vol and a term slope.")
    g.compute("signals", "rv_ok", {
        "vrp": _op("sub", _op("mul", _f("atm_iv"), _f("atm_iv")), _op("mul", _c(SESSIONS_PER_YEAR), _f("rv_22"), _f("rv_22"))),
        "term_ratio": _op("div", _f("atm_iv"), _op("add", _f("atm_iv"), _f("term_slope")))},
        "The two regime readings of VolRegimeSignals (ADR-0194), restated per name because that node takes ONE date-keyed term series: vrp = iv^2 - 252 rv22^2 (annualised "
        "variance premium; its sign is gate_vrp_nonpositive) and term_ratio = 30-day IV / 90-day IV (>= 1 is gate_term_inverted). The 90-day IV is atm_iv + term_slope. Both use the "
        "entry close's own data, the same quotes the entry is filled at (the node's one-session lag guards a VIX close that arrives after the fill; these do not). "
        "A test pins these formulas to the node's on a single-name fixture.")
    g.filter("signals_ok", "signals", [{"field": "vrp", "op": "!=", "value": None}, {"field": "term_ratio", "op": "!=", "value": None}], "Both readings defined.")
    g.add("gated", "derive", {"field": "gate", "cases": [
        {"when": [{"field": "vrp", "op": ">", "value": 0}, {"field": "term_ratio", "op": "<", "value": 1.0}], "value": "on"},
        {"when": [], "value": "off"}]}, {"records": g.ref("signals_ok")},
        "The timing gate, declared before the run: trade the short straddle when the variance premium is positive AND the term structure is not inverted; stand aside "
        "otherwise. Thresholds are VolRegimeSignals' own (0 and ratio 1.0).")
    _short_orders(g, "gated")
    g.chain("chain", "stock_chain", inputs["universe"], ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying_price"],
            "The daily chain of the frozen universe, bounded to the period.")
    _ledger(g, "ledger", "orders", "chain", {"short_straddle": SHORT_LEGS}, "nbbo", "expiry",
            "Short straddles sold at the BID of both legs and settled at intrinsic on the expiration date (the vendor underlying of that date; stale-spot days are not removed, disclosed).")
    g.filter("closed", "ledger", [{"field": "status", "op": "==", "value": "closed"}], "Settled trades only.")
    g.nodes["closed"]["inputs"] = {"records": "$ledger.trades"}
    g.attach("outcome", "gated", "closed", ["symbol", "open_date"], ["pnl_over_premium"], 0.3, "The short straddle's return over its credit, NBBO fills, known at expiry.")
    g.filter("scored", "outcome", [{"field": "pnl_over_premium", "op": "!=", "value": None}], "Entries with a settled outcome.")
    g.half("phased", "scored", "open_date")
    _series_node(g, "vrp_level", "phased", "vrp", "open_date", "DESCRIPTIVE: the per-name variance risk premium (iv^2 - 252 rv22^2) by liquidity tercile, monthly means with a Newey-West t and a "
                 "calendar-block bootstrap interval (terciles are null before 2021-01-01 by the panel's no-look-ahead rule).",
                 by=["liquidity_tercile"], bucket="month", h_steps=1, bootstrap=BOOTSTRAP)
    _series_node(g, "slope_level", "phased", "term_slope", "open_date", "DESCRIPTIVE: the 90-minus-30-day IV slope, monthly, by tercile.",
                 by=["liquidity_tercile"], bucket="month", h_steps=1, bootstrap=BOOTSTRAP)
    _series_node(g, "short_level", "phased", "pnl_over_premium", "open_date", "DESCRIPTIVE: the short straddle's return over credit at NBBO (every entry), by gate and tercile, monthly means with intervals.",
                 by=["gate"], bucket="month", h_steps=1, bootstrap=BOOTSTRAP, contrast={"field": "gate", "plus": "on", "minus": "off", "paired": True})
    g.nodes["short_level"]["notes"] = ("PRIMARY (timing): the monthly mean short-straddle return over credit of the gated entries minus the ungated (stood-aside) entries, paired on the months both "
                                      "exist (contrast on/off). Pass: mean > 0 with Newey-West t >= 2.5. Bootstrap interval, half and tercile splits are reported beside it. " + g.nodes["short_level"]["notes"])
    _series_node(g, "short_halves", "phased", "pnl_over_premium", "open_date", "The same contrast within each half of the period (the same-sign check).",
                 by=["half", "gate"], bucket="month", h_steps=1)
    g.write("level_series", "vrp_level", "records", "vrp-level-series", "one row per tercile and month: the mean variance premium")
    g.report("report", "H4 per-name variance risk premium", {"vrp": "vrp_level", "slope": "slope_level", "timing": "short_level", "halves": "short_halves", "ledger": "ledger"})
    return g


def build_h2(inputs, period):
    g = Graph(inputs, period)
    g.name = "h2-earnings"
    rules = json.loads((CONFIGS / EVENTS_FILE).read_text())["pipeline"]["events"]["params"]
    cols = PANEL_BASE + ["expiration", "dte", "spot", "atm_strike", "atm_iv", "straddle_ratio", "half_spread_over_premium"]
    g.panel("panel", "panel", cols, "The panel of chain-day features, both targets (30 and 90 DTE), read through its pinned snapshot, bounded to the period.")
    g.filter("panel_ok", "panel", [{"field": "after_first_quote", "op": "==", "value": True}], "Rows from each name's first quoted date.")
    detector = {k: v for k, v in rules.items() if k != "end_before"}
    g.add("events", DETECTOR, {**detector, "end_before": period.end_before}, {"records": g.ref("panel_ok")},
          "The frozen event rules of run-theta-chain-events.json (ADR-0265 Amendment 4), chosen on AAPL 2016-2019 announcements, applied unchanged to the whole universe: "
          "a front-30 ATM vol crush of >= 12 log points that is 3 robust sigmas below the name's own trailing 60 sessions, with the 30-90 spread collapsing, no more than 40% of "
          "the symbols of a date, at most one event per 50 sessions. The realized move is an OUTPUT, not a criterion. THIS IS A RETROSPECTIVE LABELLER, NOT A LIVE SIGNAL: the "
          "entry (event - 3 sessions) precedes the detection date. The detected subset is selected on the vol outcome (EXPLORATORY per Amendment 4).")
    labelled = "labels" in inputs
    if labelled and period.name == "development":
        g.add("labels", "table-file", {"path": "configs/theta-earnings-labels-2016-2020.json", "source": "Announcement dates of quarterly results for AAPL, MSFT, AMZN, GOOGL and "
              "NVDA 2016-2020, recalled from the public releases and not cross-checked in the repository; each carries a confidence", "retrieved": "2026-10-12",
              "sha256": inputs["labels"]["sha256"], "expect": 2}, notes="The owner-supplied labelled announcement dates, development years only.")
        g.add("events_labelled", DETECTOR, {**detector, "end_before": period.end_before, "labelled_only": True, "label_symbol_field": "symbol",
              "label_date_field": "earnings_date", "label_confidence_field": "confidence", "confidences": ["high"], "labels_key": "labels",
              "match_before": 0, "match_after": 1, "label_event_offset": 1}, {"records": g.ref("panel_ok"), "labels": "$labels.table"},
              "One record per labelled announcement (event session = announcement + 1 session), flagged `detected`: the result on ALL labelled announcements, which is "
              "not selected on the vol outcome. The labels cover 5 names in 2016-2020 only; coverage by year is in the summary (labelled_by_year).")
    common = ["symbol", "event_date", "entry_date", "exit_date", "front_iv_before", "front_iv_after", "implied_move", "realized_move"]
    g.compute("detected_rows", "events", {"event_source": {"text": "detected"}}, "Tags the detected events and projects them to the fields both sources share.", keep=common)
    rows, port = "detected_rows", "records"
    if labelled and period.name == "development":
        g.compute("labelled_rows", "events_labelled", {"event_source": {"text": "labelled"}}, "Tags the labelled announcements the same way.", keep=common)
        g.add("events_all", "concat", {"shape": "records", "provenance": "event_source"},
              {"detected": g.ref("detected_rows"), "labelled": g.ref("labelled_rows")},
              "The detected events and the labelled announcements in one table, each row agreeing with its port name.")
        rows, port = "events_all", "merged"
    # (a) implied versus realized
    g.add("move_gap", COMPUTE, {"fields": {
        "gap_30d": _op("sub", _f("implied_move"), _op("abs", _f("realized_move"))),
        "event_implied": _op("mul", _c(SQRT_2_OVER_PI), _op("sqrt", _op("mul", _op("max", _c(0), _op("sub", _op("mul", _f("front_iv_before"), _f("front_iv_before")), _op("mul", _f("front_iv_after"), _f("front_iv_after")))), _c(30 / 365)))),
        "realized_abs": _op("abs", _f("realized_move"))}}, {"records": g.ref(rows, port)},
        "PRIMARY (a), as written: gap_30d = the front (30-DTE) straddle ratio at the close before the event minus the absolute close-to-close move of the event day. CAVEAT recorded "
        "before the run: the straddle spans 30 days and the move one, so this difference is positive by construction; the informative SECONDARY is event_implied, the one-day "
        "move the crush itself removes, sqrt(2/pi) sqrt((iv_before^2 - iv_after^2) 30/365), against realized_abs.")
    g.compute("move_gap2", "move_gap", {"event_gap": _op("sub", _f("event_implied"), _f("realized_abs"))}, "Secondary difference: event-variance implied move minus realized move.")
    g.filter("move_ok", "move_gap2", [{"field": "event_date", "op": "!=", "value": None}, {"field": "gap_30d", "op": "!=", "value": None}, {"field": "event_gap", "op": "!=", "value": None}],
             "Events with both moves.")
    _series_node(g, "gap_primary", "move_ok", "gap_30d", "event_date", "(a) PRIMARY as written, clustered by event week. Pass: mean > 0, Newey-West t >= 2.5.", by=["event_source"], bucket="week", h_steps=1, bootstrap=BOOTSTRAP)
    _series_node(g, "gap_event", "move_ok", "event_gap", "event_date", "(a) SECONDARY (reported, never promoted): event-variance implied move minus realized move, by week.", by=["event_source"], bucket="week", h_steps=1, bootstrap=BOOTSTRAP)
    # (b) the straddle through the ledger
    g.panel("entry_panel", "panel", PANEL_BASE + ["expiration", "atm_strike", "half_spread_over_premium"], "The panel again, for the entry session's expiry and ATM strike.")
    g.filter("entry_30", "entry_panel", GATE + [{"field": "target_dte", "op": "==", "value": 30}, {"field": "atm_strike", "op": "!=", "value": None}],
             "The 30-DTE ATM strike and expiry known at each entry session's close.")
    sources = ["detected"] + (["labelled"] if port == "merged" else [])
    structures = {}
    for tag in sources:
        g.filter(f"{tag}_dated", "move_ok", [{"field": "event_source", "op": "==", "value": tag}, {"field": "entry_date", "op": "!=", "value": None},
                                             {"field": "exit_date", "op": "!=", "value": None}],
                 f"The {tag} events whose entry (event - 3 sessions) and exit (event + 1) sessions exist in the symbol's own table.")
        g.compute(f"{tag}_keyed", f"{tag}_dated", {"date": _f("entry_date")}, "The entry date as the join key.")
        g.agreed(f"{tag}_stream", f"{tag}_keyed")
        g.attach(f"{tag}_entry", f"{tag}_stream", "entry_30", ["symbol", "date"], ["expiration", "atm_strike", "half_spread_over_premium", "liquidity_tercile"], 0.5,
                 "The straddle's expiry (the 30-DTE target at the entry close) and ATM strike: known when the order is placed.")
        g.filter(f"{tag}_entry_ok", f"{tag}_entry", [{"field": "atm_strike", "op": "!=", "value": None}], "Events with a resolved straddle.")
        structures[f"long_straddle_{tag}"] = STRADDLE_LEGS
        g.compute(f"{tag}_orders", f"{tag}_entry_ok", {
            "open_date": _f("date"), "exit": _f("exit_date"), "strikes": {"list": [_f("atm_strike"), _f("atm_strike")]},
            "structure": {"text": f"long_straddle_{tag}"}},
            keep=["symbol", "expiration", "date", "event_date", "event_source", "exit_date", "half_spread_over_premium", "liquidity_tercile", AGREE],
            notes="Orders: a long ATM straddle at the entry session, exited at the event+1 session at the NBBO. One ledger structure name per event source (the same legs), "
            "so a detected event that is also a labelled announcement stays two distinct trades.")
    if len(sources) > 1:
        g.add("orders_all", "concat", {"shape": "records", "provenance": "event_source"},
              {tag: g.ref(f"{tag}_orders") for tag in sources}, "The detected and labelled orders in one table, each row agreeing with its port name.")
        orders_key, orders_port = "orders_all", "merged"
    else:
        orders_key, orders_port = "detected_orders", "records"
    g.chain("chain", "stock_chain", inputs["universe"], ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying_price"],
            "The daily chain of the frozen universe, bounded to the period.")
    _ledger(g, "ledger", orders_key, "chain", structures, "nbbo", "exit",
            "Buys both legs at the ASK on the entry session and sells at the BID on the exit session (an exit that cannot be quoted is counted and the trade runs to expiry, never a free exit).",
            order_exit=True, orders_port=orders_port)
    g.filter("closed", "ledger", [{"field": "status", "op": "==", "value": "closed"}, {"field": "reason_closed", "op": "==", "value": "exit"}],
             "Trades that exited on the event+1 session at the NBBO; a trade that fell back to expiry is not the pre-registered holding and is excluded (counted in the ledger summary).")
    g.nodes["closed"]["inputs"] = {"records": "$ledger.trades"}
    g.attach("outcome", orders_key, "closed", ["symbol", "open_date", "structure"], ["pnl_over_premium"], 0.5, "The trade's return over its debit, NBBO both sides.",
             stream_port=orders_port)
    g.filter("scored", "outcome", [{"field": "pnl_over_premium", "op": "!=", "value": None}], "Events with a closed trade.")
    g.half("phased", "scored", "event_date")
    contrast = {"field": "event_source", "plus": "detected", "minus": "labelled", "paired": False} if port == "merged" else None
    over = {"contrast": contrast} if contrast else {}
    _series_node(g, "pnl", "phased", "pnl_over_premium", "event_date", "PRIMARY (b): the long straddle's return over premium per event, clustered by event week (bucket week, equal weight within a week), reported on the DETECTED events, on ALL "
                 "labelled announcements and the unpaired difference of the two (contrast). Pass: mean > 0, Newey-West t >= 2.5 (clustered by event week), same sign in both halves, terciles by liquidity. "
                 "EXPLORATORY on the detected subset (Amendment 4).", by=["event_source"], bucket="week", h_steps=1, bootstrap=BOOTSTRAP, **over)
    _series_node(g, "pnl_halves", "phased", "pnl_over_premium", "event_date", "The same by half of the period (same-sign check).", by=["event_source", "half"], bucket="week", h_steps=1)
    _series_node(g, "pnl_terciles", "phased", "pnl_over_premium", "event_date", "The same by liquidity tercile (null before 2021-01-01 by the panel's no-look-ahead rule).",
                 by=["event_source", "liquidity_tercile"], bucket="week", h_steps=1)
    g.write("events_table", "detected_rows", "records", "events", "one row per detected event: dates, front IV before and after, implied and realized move")
    summaries = {"events": "events", "gap_primary": "gap_primary", "gap_event": "gap_event", "pnl": "pnl", "halves": "pnl_halves", "terciles": "pnl_terciles", "ledger": "ledger"}
    if labelled and period.name == "development":
        summaries["labelled"] = "events_labelled"
    g.report("report", "H2 earnings events: implied versus realized and the event straddle", summaries)
    return g


def build_h5(inputs, period):
    g = Graph(inputs, period)
    g.name = "h5-index-harvests"
    fields = ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "underlying_price"]
    g.chain("chain", "index_chain", inputs["index_symbols"], fields,
            "The daily chain of the index roots SPX, SPXW and XSP from the Parquet export tree (the index tier), symbol by symbol with dates ascending, bounded to the period.")
    monthly = {"dte": 30, "tolerance": 7, "min_dte": 1}
    daily = {"dte": 1, "tolerance": 2, "min_dte": 1}
    put, call = "put", "call"

    def leg(right, quantity, delta=None):
        return {"right": right, "quantity": quantity, "select": {"kind": "atm"} if delta is None else {"kind": "delta", "target": delta}}
    structures = {
        "put_write": {"legs": [leg(put, -1)], "expiry": monthly, "entry": {"kind": "after_expiry"}, "symbols": ["SPX", "XSP"]},
        "strangle_1dte": {"legs": [leg(put, -1, 0.16), leg(call, -1, 0.16)], "expiry": daily, "entry": {"kind": "after_expiry"}, "symbols": ["SPXW", "XSP"]},
        "condor_1dte": {"legs": [leg(put, -1, 0.16), leg(call, -1, 0.16), leg(put, 1, 0.05), leg(call, 1, 0.05)], "expiry": daily,
                        "entry": {"kind": "after_expiry"}, "symbols": ["SPXW", "XSP"]},
        "put_spread_16_5": {"legs": [leg(put, -1, 0.16), leg(put, 1, 0.05)], "expiry": monthly, "entry": {"kind": "after_expiry"}, "symbols": ["SPX", "XSP"]},
        "tail_hedge": {"legs": [leg(put, 1, 0.05)], "expiry": monthly, "entry": {"kind": "after_expiry"}, "symbols": ["SPX", "XSP"]}}
    g.add("orders", ORDERS, {
        "chain_fields": {"symbol": "symbol", "date": "date", "expiration": "expiration", "strike": "strike", "right": "right", "bid": "bid", "ask": "ask", "underlying": "underlying_price"},
        "order_fields": {"date": "open_date", "symbol": "symbol", "expiration": "expiration", "strikes": "strikes", "structure": "structure"},
        "call_values": ["call"], "put_values": ["put"], "start": period.start, "end_before": period.end_before, "rate": 0.0, "structures": structures},
        {"records": g.ref("chain")},
        "Chooses each structure's expiry and strikes from the day's chain. Put-write: the ATM put (strike nearest the parity forward) of the expiry nearest 30 days (within 7), rolled on the "
        "prior expiration. 1DTE strangle and condor: the 16-delta put and call (short) and, for the condor, the 5-delta put and call (long) of the nearest listed expiry 1 to 3 days out, a new "
        "order every session after the prior expires. 16/5-delta put spread: short the 16-delta put, long the 5-delta put, 30 days, rolled. Tail hedge: long the 5-delta put, 30 days, rolled. "
        "Delta is the Black-76 forward delta magnitude from each strike's own mid-implied vol on the parity forward (rate 0: it picks strikes only; nothing is marked with it). SPX (monthly cycle) "
        "carries the 30-day structures, SPXW (daily expiries) the 1DTE ones, XSP both.")
    vocabulary = {name: [[l["right"], l["quantity"]] for l in spec["legs"]] for name, spec in structures.items()}
    _ledger(g, "ledger", "orders", "chain", vocabulary, "nbbo", "expiry",
            "Every order opened at the NBBO sides (sold legs at the bid, bought legs at the ask) and settled at intrinsic on the expiration date from the chain's underlying. No fees "
            "(none are in the ADR; an owner decision, see the run book).")
    g.filter("closed", "ledger", [{"field": "status", "op": "==", "value": "closed"}], "Settled trades only.")
    g.nodes["closed"]["inputs"] = {"records": "$ledger.trades"}
    g.agreed("closed_agree", "closed")
    g.attach("sized", "closed_agree", "orders", ["symbol", "open_date", "structure"], ["spot"], 0.0,
             "The underlying at the open, from the order: the notional the return is measured on.")
    g.compute("returns", "sized", {"ret_notional": _op("div", _f("pnl_usd"), _op("mul", _f("spot"), _c(100)))},
              "Return on notional = net P&L over (underlying level x 100) at the open. Margin is not modelled: the notional is the common base, so structures are comparable.")
    _series_node(g, "sharpe", "returns", "ret_notional", "open_date",
                 "PRIMARY: per structure and root, the trades' returns on notional summed to calendar months, the annualised Sharpe ratio (sqrt 12 on the monthly series) and its calendar-block "
                 "bootstrap interval (90-day blocks, 2000 resamples, 90% two-sided). Pass only if the lower bound of that interval is above 0.5. The one-sided 10th-percentile bound is in sharpe_ci too. "
                 "Measurement, not proof (a 10-year Sharpe of 0.8 reaches t 2.5 only barely).",
                 by=["structure", "symbol"], bucket="month", bucket_agg="sum", periods_per_year=12, h_steps=1, bootstrap=BOOTSTRAP, hurdle=0.5)
    g.write("trades_table", "returns", "records", "trades", "one row per settled trade: structure, root, dates, strikes, premium, P&L, return on notional")
    g.report("report", "H5 index harvests at NBBO", {"sharpe": "sharpe", "orders": "orders", "ledger": "ledger"})
    return g


def build_h6(inputs, period):
    g = Graph(inputs, period)
    g.name = "h6-dispersion"
    p30 = _panel_30(g, ["atm_iv"])
    g.panel("index_panel", "panel_index", ["symbol", "date", "target_dte", "status", "atm_iv"],
            "The panel of chain-day features of the index root SPX (a panel of its own, ChainPanelBuild on the index tier), read through its pinned snapshot.")
    g.filter("spx30", "index_panel", [{"field": "symbol", "op": "==", "value": "SPX"}, {"field": "target_dte", "op": "==", "value": 30}, {"field": "status", "op": "==", "value": "ok"},
                                       {"field": "atm_iv", "op": ">", "value": 0}], "SPX's 30-DTE ATM implied vol: the index leg.")
    _bar_features(g, windows=(22,), idio=66)
    g.filter("rv_names", "bar_feats", [{"field": "rv_22", "op": ">", "value": 0}], "Names with a 22-session realized vol.")
    g.add("spy_feats", BAR_FEATURES, {"entity_field": "symbol", "date_field": "date", "close_field": "adjusted_close", "realized_vol": {"windows": [22], "field": "rv_{window}"}},
          {"bars": g.ref("bench_ok")}, "SPY's trailing 22-session realized vol: the index's realized leg (the index realized proxy; SPY tracks SPX).")
    g.filter("rv_spy", "spy_feats", [{"field": "rv_22", "op": ">", "value": 0}], "SPY dates with a realized vol.")
    g.pairs("fwd_rv", "rv_names", 31, "rv_22", "fwdrv", "The 22-session realized vol read at the close 31 calendar days later (or the previous close): the vol realized over the following ~31 days. Dropped past the period's last close.")
    g.pairs("fwd_spy", "rv_spy", 31, "rv_22", "fwdspy", "The same for SPY.")
    base = g.agreed("names_agree", "p30")
    g.attach("names_fwd", base, "fwd_rv", ["symbol", "date"], ["c1_fwdrv"], 0.3, "Forward realized vol: label-time, known 31 days later.")
    g.filter("names_ok", "names_fwd", [{"field": "atm_iv", "op": ">", "value": 0}, {"field": "c1_fwdrv", "op": ">", "value": 0}], "Names with an implied and a forward realized vol.")
    ann = math.sqrt(SESSIONS_PER_YEAR)
    g.compute("names_sq", "names_ok", {"rv_ann": _op("mul", _f("c1_fwdrv"), _c(ann))}, "Forward realized vol annualised (sqrt 252).")
    g.compute("names_sq2", "names_sq", {"iv2": _op("mul", _f("atm_iv"), _f("atm_iv")), "rv2": _op("mul", _f("rv_ann"), _f("rv_ann"))}, "Squares for the equal-weight sums.")
    g.add("daily", "groupby", {"keys": ["date"], "aggregates": {
        "n": {"op": "count"}, "s_iv": {"op": "sum", "field": "atm_iv"}, "s_iv2": {"op": "sum", "field": "iv2"},
        "s_rv": {"op": "sum", "field": "rv_ann"}, "s_rv2": {"op": "sum", "field": "rv2"}}}, {"records": g.ref("names_sq2")},
        "Per date: the number of names and the sums of implied vol, implied variance, forward realized vol and variance over the equal-weight basket of the tested names.")
    g.agreed("daily_agree", "daily")
    g.attach("with_spx", "daily_agree", "spx30", ["date"], ["iv_spx"], 0.2, "SPX's 30-day implied vol on the date.", rename={"iv_spx": "atm_iv"})
    g.attach("with_spy", "with_spx", "fwd_spy", ["date"], ["spy_rv"], 0.2, "SPY's forward realized vol (annualised below).", rename={"spy_rv": "c1_fwdspy"})
    g.filter("daily_ok", "with_spy", [{"field": "n", "op": ">=", "value": 50}, {"field": "iv_spx", "op": ">", "value": 0}, {"field": "spy_rv", "op": ">", "value": 0}],
             "Dates with at least 50 names (the basket) and both index legs.")
    n2 = _op("mul", _f("n"), _f("n"))
    denom_iv = _op("div", _op("sub", _op("mul", _f("s_iv"), _f("s_iv")), _f("s_iv2")), n2)
    denom_rv = _op("div", _op("sub", _op("mul", _f("s_rv"), _f("s_rv")), _f("s_rv2")), n2)
    spy_ann = _op("mul", _f("spy_rv"), _c(ann))
    g.compute("rho", "daily_ok", {
        "rho_implied": _op("div", _op("sub", _op("mul", _f("iv_spx"), _f("iv_spx")), _op("div", _f("s_iv2"), n2)), denom_iv),
        "rho_realized": _op("div", _op("sub", _op("mul", spy_ann, spy_ann), _op("div", _f("s_rv2"), n2)), denom_rv)},
        "Average pairwise correlation implied by an index variance over an equal-weight basket: rho = (sigma_I^2 - sum w^2 sigma^2) / (sum_{i!=j} w w sigma_i sigma_j) with w = 1/n, "
        "so rho = (sigma_I^2 - S2/n^2) / ((S1^2 - S2)/n^2), S1 = sum of vols and S2 = sum of variances. Implied: SPX's 30-day ATM vol and the names' 30-day ATM vols; realized: SPY's and the "
        "names' forward realized vols. EQUAL-WEIGHT PROXY STATED: the basket is the equal-weight tested names, not the index's cap-weighted constituents (no constituent list is "
        "onboarded), so the premium carries a basis that is not zero; the pre-registered statistic is the premium itself.")
    g.compute("premium", "rho", {"corr_premium": _op("sub", _f("rho_implied"), _f("rho_realized"))}, "Correlation premium = implied minus realized average correlation.")
    g.half("phased", "premium")
    _series_node(g, "premium_series", "phased", "corr_premium", "date",
                 "PRIMARY: the daily correlation premium, tested one-sided E <= 0 with the Newey-West band over the 21 overlapping daily stamps of a 31-day label, overall and by half. Pass: mean > 0, t >= 2.5 in "
                 "BOTH halves.", by=["half"], bucket="day", h_steps=SESSIONS_31D, bootstrap=BOOTSTRAP)
    _series_node(g, "implied_series", "phased", "rho_implied", "date", "DESCRIPTIVE: the implied average correlation.", bucket="day", h_steps=SESSIONS_31D)
    _series_node(g, "realized_series", "phased", "rho_realized", "date", "DESCRIPTIVE: the realized average correlation.", bucket="day", h_steps=SESSIONS_31D)
    g.write("premium_by_date", "premium", "records", "premium-by-date", "one row per date: names, vol sums, SPX vol, SPY forward vol, implied and realized correlation and the premium")
    g.report("report", "H6 dispersion: the correlation premium", {"premium": "premium_series", "implied": "implied_series", "realized": "realized_series"})
    return g


def build_h7(inputs, period):
    g = Graph(inputs, period)
    g.name = "h7-pin-spread"
    fields = ["symbol", "date", "expiration", "strike", "right", "bid", "ask", "open_interest", "underlying_price"]
    g.chain("chain", "stock_chain", inputs["universe"], fields, "The daily chain of the frozen universe (open interest included), bounded to the period.")
    g.add("orders", ORDERS, {
        "chain_fields": {"symbol": "symbol", "date": "date", "expiration": "expiration", "strike": "strike", "right": "right", "bid": "bid", "ask": "ask",
                         "underlying": "underlying_price", "open_interest": "open_interest"},
        "order_fields": {"date": "open_date", "symbol": "symbol", "expiration": "expiration", "strikes": "strikes", "structure": "structure"},
        "call_values": ["call"], "put_values": ["put"], "start": period.start, "end_before": period.end_before, "rate": 0.0,
        "structures": {"pin": {"legs": [{"right": "call", "quantity": 1, "select": {"kind": "max_oi"}}, {"right": "put", "quantity": 1, "select": {"kind": "mirror", "of": 0}}],
                               "entry": {"kind": "days_before_expiry", "days": 7}, "expiry_rule": {"weekday": 4, "day_of_month": [15, 21]}}}},
        {"records": g.ref("chain")},
        "For each standard monthly expiration (a Friday on the 15th to 21st) seven calendar days before it: the strike with the largest open interest (calls plus puts) and a PLACEBO strike, "
        "the listed strike with a usable put quote nearest the mirror image of it through the underlying (2 spot - strike): the same distance on the other side, chosen by the same "
        "rule and before any outcome. Monthly expiries that fall on a Thursday (a Friday holiday) are not ordered.")
    g.bars("bars_raw", BAR_COLUMNS, notes="Yahoo daily bars of the tested names (pinned snapshots), for the underlying's move between the open and the expiration.")
    g.filter("bars_ok", "bars_raw", [{"field": "close", "op": ">", "value": 0}], "Bars with a price.")
    g.compute("stream0", "orders", {"date": _f("open_date")}, "The open date as the join key.")
    g.agreed("stream1", "stream0")
    g.attach("at_open", "stream1", "bars_ok", ["symbol", "date"], ["close_open"], 0.3, "The split-adjusted close of the open date.", rename={"close_open": "close"})
    g.add("stream2", COMPUTE, {"fields": {"date": _f("expiration")}, "overwrite": True}, {"records": g.ref("at_open")}, "The expiration as the join key.")
    g.attach("at_expiry", "stream2", "bars_ok", ["symbol", "date"], ["close_expiry"], 0.3, "The split-adjusted close of the expiration date.", rename={"close_expiry": "close"})
    g.filter("pins_ok", "at_expiry", [{"field": "close_open", "op": ">", "value": 0}, {"field": "close_expiry", "op": ">", "value": 0}], "Orders with both closes.")
    level = _op("mul", _f("spot"), _op("div", _f("close_expiry"), _f("close_open")))
    dist = lambda strike, at: _op("abs", _op("sub", at, strike))
    k_star, k_mirror = {"item": {"field": "strikes", "index": 0}}, {"item": {"field": "strikes", "index": 1}}
    g.compute("moves", "pins_ok", {"k_star": k_star, "k_mirror": k_mirror, "expiry_level": level},
              "The strikes out of the list, and the underlying's level at expiry = spot at the open x the split-adjusted close ratio (a ratio is split-invariant; a split inside the 7 days would "
              "break it, rare and disclosed).")
    g.compute("toward", "moves", {
        "toward_star": _op("div", _op("sub", dist(_f("k_star"), _f("spot")), dist(_f("k_star"), _f("expiry_level"))), _f("spot")),
        "toward_placebo": _op("div", _op("sub", dist(_f("k_mirror"), _f("spot")), dist(_f("k_mirror"), _f("expiry_level"))), _f("spot"))},
        "Movement toward a strike over the 7 days as a fraction of the spot: (|spot - K| - |level at expiry - K|) / spot. Positive: the underlying ended closer to K.")
    g.compute("excess", "toward", {"pin_excess": _op("sub", _f("toward_star"), _f("toward_placebo"))}, "PIN EXCESS = movement toward the max-open-interest strike minus movement toward the mirror placebo.")
    g.compute("sided_in", "excess", {"above": _op("sub", _f("k_star"), _f("spot"))}, "K* minus the spot: positive when the max-open-interest strike lies above the underlying.")
    g.add("sided", "derive", {"field": "side", "cases": [{"when": [{"field": "above", "op": ">", "value": 0}], "value": "above"}, {"when": [], "value": "below"}]},
          {"records": g.ref("sided_in")}, "Which side of the underlying the max-open-interest strike sits on: a drift in the underlying moves it toward one side, so the effect is read on each side.")
    g.half("phased", "sided", "open_date")
    _series_node(g, "pin", "phased", "pin_excess", "open_date",
                 "PRIMARY: the monthly mean pin excess (toward the max-open-interest strike minus toward the placebo), overall and by side of the underlying and by half, with a Newey-West t and a "
                 "calendar-block bootstrap interval. A pin pulls on both sides; a drift pulls on one. Descriptive (the ADR gives no pass number).",
                 by=["side", "half"], bucket="month", h_steps=1, bootstrap=BOOTSTRAP)
    _series_node(g, "toward_star_series", "phased", "toward_star", "open_date", "DESCRIPTIVE: movement toward the max-open-interest strike.", bucket="month", h_steps=1)
    _series_node(g, "toward_placebo_series", "phased", "toward_placebo", "open_date", "DESCRIPTIVE: movement toward the placebo strike.", bucket="month", h_steps=1)
    # (b) the spread census
    p30 = _panel_30(g, ["atm_iv", "half_spread_over_premium"])
    g.filter("spreads", p30, [{"field": "half_spread_over_premium", "op": ">=", "value": 0}], "Rows with a measured half-spread over premium.")
    _series_node(g, "spread_series", "spreads", "half_spread_over_premium", "date",
                 "SPREAD CENSUS: the monthly mean half-spread over premium of the 30-DTE ATM straddle, by liquidity tercile (null before 2021-01-01 by the panel's rule), with intervals.",
                 by=["liquidity_tercile"], bucket="month", h_steps=1, bootstrap=BOOTSTRAP)
    g.add("spread_by_name", "groupby", {"keys": ["symbol"], "aggregates": {
        "dates": {"op": "count"}, "mean_half_spread": {"op": "mean", "field": "half_spread_over_premium"}, "max_half_spread": {"op": "max", "field": "half_spread_over_premium"}}},
        {"records": g.ref("spreads")}, "Per name: dates, mean and maximum half-spread over premium: the table a reader sorts to see the wide-spread names.")
    g.write("spread_table", "spread_by_name", "records", "spread-by-name", "one row per name: dates, mean and max half-spread over premium")
    g.report("report", "H7 pin and expiry, spread census", {"pin": "pin", "star": "toward_star_series", "placebo": "toward_placebo_series", "spread": "spread_series", "orders": "orders"})
    return g


HYPOTHESES = {"H0": build_h0, "H1": build_h1, "H2": build_h2, "H3": build_h3, "H4": build_h4, "H5": build_h5,
              "H6": build_h6, "H7": build_h7}
PANEL_DOC = "run-theta-chain-panel.json"
INDEX_PANEL_DOC = "run-theta-chain-panel-index.json"
RUN_INSTRUCTION = ("Run from children/index_options, niced, after the census, the universe freeze and the panel (ADR-0265 Amendment 5 run book): "
                   "nice -n 10 python -m dskit.pipeline run configs/{file} --asof 2025-12-31")
OVERLAY_NOTE = ("The validation overlay of {h}: only the dates change ({start} .. {end_before}, bars read from {bars_start}, halves split at {halves_at}); "
                "{doc} is {base} with this file applied by index_options.theta_hypotheses.apply_args. RUN ONCE, unchanged, only if the development run passed; a ledger row is "
                "written for the run whatever it shows.")


def build(h, period, inputs=None):
    """The document of hypothesis ``h`` for ``period``: ``{name, notes, outputs, pipeline}``."""
    inputs = inputs or load_inputs()
    graph = HYPOTHESES[h](inputs, period)
    file = doc_file(h, period)
    notes = (f"PRE-REGISTERED {h} (ADR-0265, Amendment 5), {period.name} period {period.start} .. {period.end_before} (end_before; nothing on or after 2026-01-01 is read). "
             f"ADR RULE, verbatim: {ADR_RULE[h]} {COMMON_RULES} "
             + ("RUN ONCE: this is the validation run; it is not repeated, tuned or re-read. " if period.name == "validation" else "This is the development run. ")
             + RUN_INSTRUCTION.format(file=file))
    return {"name": file.removesuffix(".json").removeprefix("run-"), "notes": notes,
            "outputs": {"run_root": graph.run_root(), "notes": "Beside the other research runs; nothing is written under /home/russell/data/thetadata. Excluded from the identity hash."},
            "pipeline": {k: {kk: vv for kk, vv in v.items()} for k, v in graph.nodes.items()}}


def slug(h):
    return {"H0": "h0-har-implied", "H1": "h1-implied-features-ic", "H2": "h2-earnings", "H3": "h3-straddle-sorts", "H4": "h4-vrp",
            "H5": "h5-index-harvests", "H6": "h6-dispersion", "H7": "h7-pin-spread"}[h]


def doc_file(h, period):
    return f"run-theta-{slug(h)}{period.suffix}.json"


def args_file(h):
    return f"theta-{h.lower()}-validation-args.json"


def args_for(h, period=VAL):
    """The overlay file of one hypothesis' validation run."""
    return {"notes": OVERLAY_NOTE.format(h=h, start=period.start, end_before=period.end_before, bars_start=period.bars_start,
                                         halves_at=period.halves_at, doc=doc_file(h, period), base=doc_file(h, DEV)),
            "period": period.name, "start": period.start, "end_before": period.end_before, "bars_start": period.bars_start,
            "halves_at": period.halves_at, "suffix": period.suffix}


def apply_args(args):
    """The :class:`Period` an overlay file declares; refuses a sealed or inconsistent one."""
    period = Period(args["period"], args["start"], args["end_before"], args["bars_start"], args["halves_at"], args["suffix"])
    if period.end_before > SEALED:
        raise ValueError(f"end_before {period.end_before} is past the sealed date {SEALED}")
    if not period.start < period.halves_at < period.end_before:
        raise ValueError("halves_at must lie inside the period")
    return period


def panel_documents():
    """The index-tier panel document: the stock panel document with the index tier, the SPX root and no stock-only gates."""
    base = json.loads((CONFIGS / PANEL_DOC).read_text())
    doc = json.loads(json.dumps(base))
    doc["name"] = "theta-chain-panel-index"
    doc["notes"] = ("The panel of chain-day features of the index root SPX for H6 (ADR-0265 Amendment 5): run-theta-chain-panel.json with the index tier "
                    "(/home/russell/data/thetadata/parquet-v2/options-index/theta), the single root SPX and none of the stock gates (no first-quote file, no census). Same features, "
                    "same targets (30 and 90 days), same rate table, sealed at 2026-01-01. READ-ONLY on the Parquet tree. Run from children/index_options after the index tier has landed and "
                    "been exported: nice -n 10 python -m dskit.pipeline run configs/run-theta-chain-panel-index.json --asof 2025-12-31. Register panel/ as a localblobs source "
                    "(theta-chain-panel-index) and record its snapshot pin with theta_hypotheses set-pin panel_index <hash>.")
    doc["outputs"]["run_root"] = "/home/russell/data/index_options/theta-research-20261012/chain-panel-index-run"
    chain = doc["pipeline"]["chain"]["params"]
    chain["directories"] = ["/home/russell/data/thetadata/parquet-v2/options-index/theta"]
    chain["symbols"] = ["SPX"]
    params = doc["pipeline"]["panel"]["params"]
    params["path"] = "/home/russell/data/index_options/theta-research-20261012/chain-panel-index"
    for key in ("first_quote_file", "first_quote_field", "gate_field", "census_file", "census_columns", "census_asof"):
        params.pop(key, None)
    return {INDEX_PANEL_DOC: doc}


def files(inputs=None):
    """Every generated file: ``{name: object}`` (documents, overlays and the index panel document)."""
    inputs = inputs or load_inputs()
    out = dict(panel_documents())
    for h in HYPOTHESES:
        out[doc_file(h, DEV)] = build(h, DEV, inputs)
        args = args_for(h)
        out[args_file(h)] = args
        out[doc_file(h, VAL)] = build(h, apply_args(args), inputs)
    return out


def dump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def freeze_universe(census_jsonl, symbols_out):
    """The frozen universe: every root the census saw with a bid above zero, with its first bid date and tercile."""
    roots = {}
    for line in Path(census_jsonl).read_text().splitlines():
        row = json.loads(line)
        if row.get("first_bid_date"):
            roots[row["root"] if "root" in row else row["symbol"]] = {
                "first_bid_date": row["first_bid_date"], "liquidity_tercile": row.get("liquidity_tercile"),
                "median_quoted_development": row.get("median_quoted_development")}
    return {"notes": "The frozen universe of ADR-0265 decision 2: every root with a first date of a bid above zero in the census "
                     f"({Path(census_jsonl).name}, sha256 {hashlib.sha256(Path(census_jsonl).read_bytes()).hexdigest()}). Terciles are those of the census, fixed on development years.",
            "census_sha256": hashlib.sha256(Path(census_jsonl).read_bytes()).hexdigest(), "roots": dict(sorted(roots.items()))}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv.pop(0) if argv else ""
    if verb in ("write", "check"):
        stale = []
        for name, obj in files().items():
            path = CONFIGS / name
            text = dump(obj)
            if verb == "write":
                path.write_text(text)
            elif not path.exists() or path.read_text() != text:
                stale.append(name)
        if stale:
            print("differs from the generator:", *stale, sep="\n  ")
            return 1
        return 0
    if verb == "set-pin" and len(argv) == 2:
        name, pin = argv
        inputs = load_inputs()
        if name not in inputs or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
            print("usage: set-pin panel|panel_index <64 hex>")
            return 1
        inputs[name]["snapshot"] = pin
        (CONFIGS / INPUTS_FILE).write_text(dump(inputs))
        return 0
    if verb == "freeze-universe" and len(argv) == 1:
        frozen = freeze_universe(argv[0], None)
        (CONFIGS / "theta-universe.json").write_text(dump(frozen))
        inputs = load_inputs()
        inputs["universe"] = sorted(frozen["roots"])
        inputs["universe_sha256"] = hashlib.sha256((CONFIGS / "theta-universe.json").read_bytes()).hexdigest()
        (CONFIGS / INPUTS_FILE).write_text(dump(inputs))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
