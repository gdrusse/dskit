"""Walk-forward edge calibration, tier 2 (ADR-0311 stage 3).

:class:`EdgeReliabilityWalk` replays a decision calendar week by week. At each decision
session ``t`` it (a) settles every pending row that settled strictly before ``t`` (the fill-session
quote and the settlement level are read HERE and only here), (b) folds the settled clusters into
four states, ``{expanding, decayed} x {fill price, decision price}`` (the core classes of
:mod:`dskit.pipeline.edge_reliability`, no arithmetic of its own), (c) snapshots one frozen map per
state, then (d) decides: the day's rows get their bucket and the snapshot's calibrated edge and wait,
keyed by settlement date, in an on-disk shard. The map at ``t`` is written before a single row of
``t`` is read, so nothing dated at or after ``t`` can reach it.

The domain enters only through six ``@abstractmethod`` hooks, here synthetic in the tests and a thin
child in a real run. A row is a mapping with ``group, name, expiry, settle_date, fill_date, factor,
right, strike, sign, e, dte, abs_z, spread, v, credit, multiplier`` (``credit`` is the decision-price
credit in dollars, ``right`` is ``call`` or ``put``, ``sign`` is +1 to buy and -1 to sell). Realized
edge is ``sign * payoff * multiplier + credit`` with the fill-session credit (primary) or the decision
credit (secondary).

Checkpoint and resume: the checkpoint holds the four states, the name sums, the regime history, the
pending index (the shards stay on disk), the counts, the cursor, the params hash and the input pins.
The checkpoint also holds the manifest of the map and row files this run wrote, and the summary digest
covers exactly those, so a stray file in ``out_dir`` cannot move it. A resume refuses
``checkpoint_mismatch`` on a changed param, pin or checkpoint version and ``state_from_future`` when the
checkpoint is later than the requested window. Every file lands through ``atomic_write``.

Wiring is by import path; :data:`NODE_KINDS` is empty. Import cost: stdlib and the pipeline core.
"""

import hashlib
import json
import os
from abc import abstractmethod
from collections import namedtuple

from dskit.pipeline.edge_reliability import (
    EdgeCalibrationMap,
    LinearEdgeState,
    NameAdjustment,
    PartialPooling,
    ReliabilityBuckets,
    SettledBefore,
    TrailingRegime,
    _iso,
    _Refusal,
    block_index,
)
from dskit.pipeline.node import Node, atomic_write, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "EdgeReliabilityWalk"]

#: No node kind is registered: a document names ``dskit.pipeline.libs.edge_walk:EdgeReliabilityWalk``.
NODE_KINDS = ()

#: Bump on ANY change to the checkpoint's content or meaning; a checkpoint of another version refuses.
_VERSION = 3
#: The four primary one-regressor states, in the order every stored per-row list holds them.
_PRIMARY = ("expanding_fill", "decayed_fill", "expanding_decision", "decayed_decision")
#: X1-X4: the same four cells with the two-regressor map (direction and shape edge).
_DIRECTION = tuple("dir_" + k for k in _PRIMARY)
#: R1, R2, R4, S1, S3, D1 (ADR-0311 Amendment 1 D7).
_EXTRA = ("reported_fill", "reported_dir", "forward", "fill_sensitivity", "reported_sensitivity", "dollar")
#: The fourteen states of D7 (nine when ``direction_regressor`` is false: no X1-X4 and no R2).
_STATES = _PRIMARY + _DIRECTION + _EXTRA
_Spec = namedtuple("_Spec", "k decayed price rows adjust top")
#: Per state: regressors, decayed, price, which rows it takes, a name adjustment, a ``top`` unit.
_SPECS = {
    **{k: _Spec(1, "decayed" in k, "decision" if "decision" in k else "fill", "all", True, False) for k in _PRIMARY},
    **{k: _Spec(2, "decayed" in k, "decision" if "decision" in k else "fill", "all", False, False)
       for k in _DIRECTION},
    "reported_fill": _Spec(1, False, "fill", "reported", False, True),
    "reported_dir": _Spec(2, False, "fill", "reported", False, False),
    "forward": _Spec(1, False, "fill", "forward", False, False),
    "fill_sensitivity": _Spec(1, False, "fill", "sens", False, False),
    "reported_sensitivity": _Spec(1, False, "fill", "sens_reported", False, True),
    "dollar": _Spec(1, False, "fill", "all", False, False),
}
_FILL_CODES = ("session", "basis", "quote", "size")
_LEVEL_CODES = ("basis_changed", "no_settlement_bar")
_DIRECTION_CODES = ("leg_no_iv", "leg_below_min_years", "no_quote", "duplicate_quote")
_ROW_KEYS = ("group", "name", "expiry", "settle_date", "fill_date", "factor", "right", "strike", "sign", "e",
             "dte", "abs_z", "spread", "v", "credit", "multiplier")
#: A pending row is a list in this order (the decision date ``t`` leads it, the settlement date is its shard's key).
(_T, _GROUP, _NAME, _EXPIRY, _FILL, _FACTOR, _RIGHT, _STRIKE, _SIGN, _E, _BUCKET, _CREDIT, _MULT, _CB, _CF, _EDIR,
 _DCODE, _SIGMA2, _SCALE, _BASE, _TOP, _REPAIRED, _WARM, _MID, _FEE, _C2) = range(26)
#: Params that cannot change a number of the output (where it lands, how often it is saved, the RAM cap).
_OPERATIONAL = ("checkpoint_every", "ram_gb_limit", "out_dir")
_REQUIRED = ("window", "report_from", "out_dir", "buckets", "regime", "pooling", "name_adjust")
_DEFAULTS = {
    "half_life_weeks": 26, "checkpoint_every": 4, "ram_gb_limit": 6, "direction_regressor": False,
    "edge_scale": "notional", "row_weight": "group_equal", "time_block_weeks": [4, 13], "min_fill_rate": 0.9,
    "min_fill_sample": 20, "ci_level": 0.95, "bh_q": 0.10, "bootstrap_draws": 999, "bootstrap_seed": 20261109,
    "tail_level_floor": 0.5, "b0_floor": 0.5, "hac_lags": 2,
}
_EDGE_SCALES = ("notional", "scale", "none")
_ROW_WEIGHTS = ("group_equal", "none")


def _canon(obj):
    """Canonical JSON text: sorted keys, no NaN, so equal content dumps equal."""
    return json.dumps(obj, sort_keys=True, allow_nan=False, separators=(",", ":"))


class EdgeReliabilityWalk(Node):
    """Walk-forward map from predicted edge to realized edge, with checkpoint and resume (ADR-0311).

    Role ``transform``; no inputs (the hooks read the data). Output ``summary``: counts per code, the
    params hash, the input pins and a digest of the written maps and row shards; the same dict is
    written to ``summary.json``. Files under ``out_dir``: ``map-<t>.json`` (all four maps at ``t``),
    ``rows/rows-<s>.jsonl`` (rows decided at or after ``report_from``, written when they settle),
    ``pending/<s>/<t>.json`` (rows waiting for settlement), ``checkpoint.json``, ``summary.json``.

    Parameters
    ----------
    params : dict
        ``window`` (``start``, ``end`` decision dates), ``report_from`` (after ``window.start``; earlier
        decisions are a seed: state only), ``sealed_from`` (required at run, at most the development
        seal), ``out_dir``, ``half_life_weeks`` (default 26; the decayed states age by ``2 ** (-1 /
        half_life_weeks)`` per decision step), ``buckets``, ``regime``, ``pooling``, ``name_adjust``
        (the params of the core classes), ``checkpoint_every`` (weeks, default 1), ``ram_gb_limit``
        (default 6).

    Raises
    ------
    ValueError
        Run-level refusals carry a stable ``code``: ``sealed_from_missing``, ``development_seal_exceeded``,
        ``window_outside_data_rights`` (before any hook runs), ``report_from_invalid``,
        ``bucket_edges_invalid``, ``calendar_mismatch``, ``fill_not_next_session``, ``checkpoint_mismatch``,
        ``state_from_future``,
        ``ram_limit``.

    Examples
    --------
    A child supplies the four hooks and a JSON params block::

        class MyWalk(EdgeReliabilityWalk):
            def _decision_calendar(self): ...
            def _decision_rows(self, t): ...
            def _fill_credits(self, rows): ...
            def _settlement_levels(self, rows): ...
            def _next_session(self, t): ...
            def _input_pins(self): ...

        MyWalk("walk", params).run(ctx, {})["summary"]["counts"]
    """

    role = "transform"
    outputs = ("summary",)
    _BASE_PARAMS = ("window", "report_from", "sealed_from", "out_dir", "half_life_weeks", "buckets", "regime",
                    "pooling", "name_adjust", "checkpoint_every", "ram_gb_limit", "direction_regressor", "edge_scale",
                    "row_weight", "time_block_weeks", "min_fill_rate", "min_fill_sample", "ci_level", "bh_q",
                    "bootstrap_draws", "bootstrap_seed", "tail_level_floor", "b0_floor", "hac_lags")
    _EXTRA_PARAMS = ()

    # -- the hooks -------------------------------------------------------------------------

    @abstractmethod
    def _decision_calendar(self):
        """Return every decision date of the study's calendar, ISO text, strictly increasing."""
        raise NotImplementedError

    @abstractmethod
    def _decision_rows(self, t):
        """Return the rows decided at session ``t`` (one dict per option and side; see the module text).

        Reads only data dated at or before ``t``. The walk keeps the larger-``e`` side of an option.
        """
        raise NotImplementedError

    @abstractmethod
    def _fill_credits(self, rows):
        """Return, per settled row, its fill-session credit or why it has none.

        A credit in dollars, or one of ``session``, ``basis``, ``quote`` (the reason no usable fill
        quote exists).

        Each row also carries ``decision_date``. Called once per settlement date, only for rows
        already admitted, so every fill quote it reads is dated before the decision week.
        """
        raise NotImplementedError

    @abstractmethod
    def _settlement_levels(self, rows):
        """Return, per settled row, the settlement level of its underlying or why it has none.

        A level, or ``basis_changed`` or ``no_settlement_bar``. Called with the rows of
        :meth:`_fill_credits`, same order.
        """
        raise NotImplementedError

    @abstractmethod
    def _next_session(self, t):
        """Return the first session of the study's own calendar after decision session ``t`` (ISO text).

        Every row's ``fill_date`` must equal it; anything else refuses ``fill_not_next_session``.
        """
        raise NotImplementedError

    @abstractmethod
    def _input_pins(self):
        """Return the input pins (name -> sha256) the checkpoint must match.

        A child pins every input file its hooks read; a child with no input files returns ``{}``
        on purpose, never by omission.
        """
        raise NotImplementedError

    # -- params ----------------------------------------------------------------------------

    @classmethod
    def _allowed(cls):
        return tuple(cls._BASE_PARAMS) + tuple(cls._EXTRA_PARAMS)

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._allowed())
        for key in _REQUIRED:
            if key not in params:
                problems.append(f"{key} is required")
        for key in ("window", "buckets", "regime", "pooling", "name_adjust"):
            if key in params and not isinstance(params[key], dict):
                problems.append(f"{key} must be an object, got {params[key]!r}")
        window = params.get("window")
        if isinstance(window, dict) and (set(window) != {"start", "end"}):
            problems.append(f"window must hold exactly start and end, got {sorted(window)}")
        if "out_dir" in params and (not isinstance(params["out_dir"], str) or not params["out_dir"]):
            problems.append(f"out_dir must be a non-empty path, got {params['out_dir']!r}")
        for key in ("half_life_weeks", "ram_gb_limit"):
            v = params.get(key, 1)
            if isinstance(v, bool) or not number_ok(v) or v <= 0:
                problems.append(f"{key} must be a number > 0, got {v!r}")
        every = params.get("checkpoint_every", _DEFAULTS["checkpoint_every"])
        if "direction_regressor" in params and not isinstance(params["direction_regressor"], bool):
            problems.append(f"direction_regressor must be true or false, got {params['direction_regressor']!r}")
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            problems.append(f"checkpoint_every must be an integer >= 1, got {every!r}")
        return problems

    def _param(self, key):
        return self.params.get(key, _DEFAULTS.get(key))

    def _params_hash(self):
        """Hash every param that can move an output, defaults resolved (not ``window.end``, not the operational knobs)."""
        body = {k: self._param(k) for k in self._allowed()
                if k not in _OPERATIONAL and (k in self.params or k in _DEFAULTS)}
        body["window"] = {"start": self.params["window"]["start"]}
        return hashlib.sha256(_canon(body).encode()).hexdigest()

    # -- run -------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Walk the window, resuming from a checkpoint in ``out_dir`` if one exists.

        Parameters
        ----------
        ctx : NodeContext
            Unused beyond the node contract.
        inputs : dict
            Unused (the hooks read the data).

        Returns
        -------
        dict
            ``{"summary": ...}``.

        Raises
        ------
        ValueError
            A coded refusal, see the class text.
        """
        self._setup()
        weeks = self._weeks()
        self._out = self.params["out_dir"]
        for sub in ("rows", "pending"):
            os.makedirs(os.path.join(self._out, sub), exist_ok=True)
        self._consumed = []
        self._manifest = set()
        cursor = self._resume(weeks)
        todo = [t for t in weeks if cursor is None or t > cursor]
        every = self._param("checkpoint_every")
        for k, t in enumerate(todo):
            self._week(t)
            if (k + 1) % every == 0 or k == len(todo) - 1:
                self._checkpoint(t)
        if todo == [] and cursor is not None:
            self._checkpoint(cursor)
        summary = self._summary(weeks)
        atomic_write(os.path.join(self._out, "summary.json"), (_canon(summary) + "\n").encode())
        return {"summary": summary}

    def _setup(self):
        """Validate everything that needs no data, before any hook runs."""
        p = self.params
        self._gate = SettledBefore(p.get("sealed_from"))
        self._gate.check_window([p["window"]["start"], p["window"]["end"], p["report_from"]])
        start, end, rep = (_iso(x, n) for x, n in ((p["window"]["start"], "window.start"),
                                                   (p["window"]["end"], "window.end"),
                                                   (p["report_from"], "report_from")))
        if end < start:
            raise _Refusal("calendar_mismatch", f"window.end {end} is before window.start {start}")
        if rep <= start:
            raise _Refusal("report_from_invalid", f"report_from {rep} is not after window.start {start}")
        self._start, self._end, self._report = start, end, rep
        self._check_knobs()
        self._buckets = ReliabilityBuckets(p["buckets"])
        self._pool = PartialPooling(p["pooling"])
        self._regime = TrailingRegime(p["regime"])
        lam = 2 ** (-1 / self._param("half_life_weeks"))
        self._blocks = list(self._param("time_block_weeks"))
        self._direction = bool(self._param("direction_regressor"))
        self._states, self._adjust = {}, {}
        for name in self._state_names():
            spec = _SPECS[name]
            decay = lam if spec.decayed else None
            self._states[name] = LinearEdgeState(24, decay, spec.k, len(self._blocks))
            if spec.adjust:
                self._adjust[name] = NameAdjustment(p["name_adjust"], decay=decay)
        self._pending = {}
        self._counts = {}

    def _state_names(self):
        """Return the states this run holds, in order: all fourteen, or nine without ``direction_regressor``."""
        return tuple(k for k in _STATES if self._direction or not (k in _DIRECTION or k == "reported_dir"))

    def _check_knobs(self):
        """Refuse a bad knob with its stable code, before any hook runs."""
        p, get = self.params, self._param

        def bad(code, what):
            raise _Refusal(code, what)

        def num(v):
            return not isinstance(v, bool) and number_ok(v)

        def whole(v, low):
            return isinstance(v, int) and not isinstance(v, bool) and v >= low

        if get("edge_scale") not in _EDGE_SCALES:
            bad("edge_scale_invalid", f"edge_scale must be one of {list(_EDGE_SCALES)}, got {get('edge_scale')!r}")
        if get("row_weight") not in _ROW_WEIGHTS:
            bad("row_weight_invalid", f"row_weight must be one of {list(_ROW_WEIGHTS)}, got {get('row_weight')!r}")
        weeks = get("time_block_weeks")
        if (not isinstance(weeks, list) or not weeks or not all(whole(w, 1) for w in weeks)
                or any(b <= a for a, b in zip(weeks, weeks[1:]))):
            bad("time_block_invalid", f"time_block_weeks must be a strictly increasing list of integers >= 1, got {weeks!r}")
        pool = p["pooling"]
        if "band" in pool and pool["band"] not in PartialPooling._BANDS:
            bad("band_invalid", f"pooling.band must be one of {list(PartialPooling._BANDS)}, got {pool['band']!r}")
        if "min_blocks" in pool and not whole(pool["min_blocks"], 2):
            bad("min_blocks_invalid", f"pooling.min_blocks must be an integer >= 2, got {pool['min_blocks']!r}")
        if "ci_levels" in pool:
            lv = pool["ci_levels"]
            if not isinstance(lv, (list, tuple)) or not lv or not all(num(x) and 0.0 < x < 1.0 for x in lv):
                bad("ci_levels_invalid", f"pooling.ci_levels must be a non-empty list in (0, 1), got {lv!r}")
        for key, code in (("ci_level", "ci_level_invalid"), ("bh_q", "bh_q_invalid")):
            if not (num(get(key)) and 0.0 < get(key) < 1.0):
                bad(code, f"{key} must be a number in (0, 1), got {get(key)!r}")
        if not (num(get("min_fill_rate")) and 0.0 < get("min_fill_rate") <= 1.0):
            bad("min_fill_rate_invalid", f"min_fill_rate must be in (0, 1], got {get('min_fill_rate')!r}")
        if not whole(get("min_fill_sample"), 2):
            bad("min_fill_sample_invalid", f"min_fill_sample must be an integer >= 2, got {get('min_fill_sample')!r}")
        if not whole(get("bootstrap_draws"), 99) or not whole(get("bootstrap_seed"), -(2 ** 63)):
            bad("bootstrap_invalid", "bootstrap_draws must be an integer >= 99 and bootstrap_seed an integer")
        for key in ("tail_level_floor", "b0_floor"):
            if not (num(get(key)) and 0.0 < get(key) <= 1.0):
                bad(f"{key}_invalid", f"{key} must be a number in (0, 1], got {get(key)!r}")
        if not whole(get("hac_lags"), 0):
            bad("hac_lags_invalid", f"hac_lags must be an integer >= 0, got {get('hac_lags')!r}")

    def _weeks(self):
        """Read the calendar and return the decision dates inside the window."""
        cal = list(self._decision_calendar())
        days = [_iso(d, "calendar date") for d in cal]
        if any(b <= a for a, b in zip(days, days[1:])):
            raise _Refusal("calendar_mismatch", "the decision calendar is not strictly increasing")
        weeks = [d for d in days if self._start <= d <= self._end]
        if not weeks:
            raise _Refusal("calendar_mismatch", "no decision date in the window")
        self._gate.check_window(weeks)
        return weeks

    def _count(self, code, n=1):
        self._counts[code] = self._counts.get(code, 0) + n

    # -- checkpoint ------------------------------------------------------------------------

    def _resume(self, weeks):
        """Load a matching checkpoint and return its cursor, or ``None`` for a fresh run."""
        path = os.path.join(self._out, "checkpoint.json")
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            blob = json.load(fh)
        if blob.get("version") != _VERSION:
            raise _Refusal("checkpoint_mismatch", f"the checkpoint is version {blob.get('version')!r}, "
                           f"this build writes version {_VERSION}")
        pins = self._input_pins()
        if blob.get("params_hash") != self._params_hash() or blob.get("pins") != json.loads(_canon(pins)):
            raise _Refusal("checkpoint_mismatch", "params or input pins differ from the checkpoint's")
        if sorted(blob["states"]) != sorted(self._states):
            raise _Refusal("checkpoint_mismatch", "the state layout differs from the checkpoint's")
        cursor = blob["cursor"]
        if cursor > weeks[-1]:
            raise _Refusal("state_from_future", f"the checkpoint is at {cursor}, after the window end {weeks[-1]}")
        if cursor not in weeks:
            raise _Refusal("calendar_mismatch", f"checkpoint cursor {cursor} is not a decision date")
        for name, rec in blob["states"].items():
            self._states[name] = LinearEdgeState.from_dict(rec["state"])
            if rec["adjust"] is not None:
                self._adjust[name] = NameAdjustment.from_dict(rec["adjust"])
        self._regime = TrailingRegime.from_dict(blob["regime"])
        self._pending = {s: list(ts) for s, ts in blob["pending"].items()}
        self._counts = dict(blob["counts"])
        self._manifest = set(blob["manifest"])
        for rel in sorted(self._manifest):
            if not os.path.exists(os.path.join(self._out, rel)):
                raise _Refusal("checkpoint_mismatch", f"written file {rel} is missing")
        for s, ts in self._pending.items():
            for t in ts:
                if not os.path.exists(self._shard(s, t)):
                    raise _Refusal("checkpoint_mismatch", f"pending shard {s}/{t} is missing")
        return cursor

    def _checkpoint(self, cursor):
        """Write the checkpoint atomically, then drop the shards settled up to it."""
        blob = {
            "version": _VERSION, "params_hash": self._params_hash(), "pins": self._input_pins(),
            "cursor": cursor,
            "states": {k: {"state": st.to_dict(), "adjust": self._adjust[k].to_dict() if k in self._adjust else None}
                       for k, st in self._states.items()},
            "regime": self._regime.to_dict(), "pending": self._pending, "counts": self._counts,
            "manifest": sorted(self._manifest),
        }
        atomic_write(os.path.join(self._out, "checkpoint.json"), (_canon(blob) + "\n").encode())
        for path in self._consumed:
            if os.path.exists(path):
                os.unlink(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass
        self._consumed = []

    def _shard(self, s, t):
        return os.path.join(self._out, "pending", s, f"{t}.json")

    def _check_ram(self):
        """Refuse ``ram_limit`` when the resident set exceeds ``ram_gb_limit``."""
        gb = self._rss_gb()
        limit = self._param("ram_gb_limit")
        if gb > limit:
            raise _Refusal("ram_limit", f"resident memory {gb:.3f} GB exceeds ram_gb_limit {limit}")

    @staticmethod
    def _rss_gb():
        """Return the resident set of this process in GB (the peak where the current is unavailable)."""
        try:
            with open("/proc/self/statm") as fh:
                return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e9
        except (OSError, ValueError, IndexError):
            import resource
            import sys

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return peak / (1e9 if sys.platform == "darwin" else 1e6)

    # -- one week --------------------------------------------------------------------------

    def _week(self, t):
        """Age the states, settle, snapshot, then decide, in that order."""
        for st in self._states.values():
            st.advance(t)
        for adj in self._adjust.values():
            adj.advance(t)
        self._settle(t)
        self._check_ram()
        maps = {k: EdgeCalibrationMap.build(t, self._states[k], self._pool, self._adjust[k]) for k in _PRIMARY}
        fits = {k: EdgeCalibrationMap.build(t, self._states[k], self._pool, None)
                for k in _DIRECTION if k in self._states}
        body = {"t": t, "states": {k: m.to_dict() for k, m in maps.items()}}
        if fits:
            body["direction"] = {k: m.to_dict() for k, m in fits.items()}
        atomic_write(os.path.join(self._out, f"map-{t}.json"), (_canon(body) + "\n").encode())
        self._manifest.add(f"map-{t}.json")
        self._decide(t, maps, fits)

    def _settle(self, t):
        """Join and fold every pending shard whose settlement date is strictly before ``t``."""
        for s in sorted(s for s in self._pending if self._gate.admits(s, t)):
            rows = []
            for dt in sorted(self._pending[s]):
                with open(self._shard(s, dt)) as fh:
                    rows.extend([dt, *r] for r in json.load(fh)["rows"])
                self._consumed.append(self._shard(s, dt))
            self._admit(t, s, rows)
            del self._pending[s]

    @staticmethod
    def _fill_answer(fill):
        """Split a fill hook answer into ``(credit or code, fill mid or None)``."""
        if isinstance(fill, (tuple, list)):
            if len(fill) != 2:
                raise ValueError(f"a fill answer is a credit, a code, or (credit, fill mid), got {fill!r}")
            return fill[0], fill[1]
        return fill, None

    def _admit(self, t, s, rows):
        """Join one settlement date's rows to their fill quote and settlement level, then fold clusters."""
        views = [{"group": r[_GROUP], "name": r[_NAME], "expiry": r[_EXPIRY], "settle_date": s,
                  "fill_date": r[_FILL], "factor": r[_FACTOR], "right": r[_RIGHT],
                  "strike": r[_STRIKE], "sign": r[_SIGN], "multiplier": r[_MULT], "decision_date": r[0]} for r in rows]
        fills = list(self._fill_credits(views))
        levels = list(self._settlement_levels(views))
        if len(fills) != len(rows) or len(levels) != len(rows):
            raise ValueError("the fill and settlement hooks must return one answer per row")
        self._check_ram()
        self._count("rows_admitted", len(rows))
        clusters, out = {}, []
        for raw, answer, level in zip(rows, fills, levels):
            r = raw
            fill, mid_f = self._fill_answer(answer)
            code = None
            if isinstance(fill, str):
                if fill not in _FILL_CODES:
                    raise ValueError(f"unknown fill code {fill!r}; allowed {list(_FILL_CODES)}")
                code = f"no_fill_quote.{fill}"
                self._count("no_fill_quote")
            elif isinstance(level, str):
                if level not in _LEVEL_CODES:
                    raise ValueError(f"unknown settlement code {level!r}; allowed {list(_LEVEL_CODES)}")
                code = level
            if code is None and (isinstance(fill, bool) or not number_ok(fill)
                                 or isinstance(level, bool) or not number_ok(level)):
                raise ValueError(f"fill credit and settlement level must be finite numbers, got {fill!r}, {level!r}")
            if code is not None:
                self._count(code)
                out.append((r, code, None, None, None))
                continue
            strike = r[_STRIKE]
            payoff = max(level - strike, 0.0) if r[_RIGHT][:1].lower() == "c" else max(strike - level, 0.0)
            intrinsic = r[_SIGN] * payoff * r[_MULT]
            cell = (r, None, intrinsic + fill, intrinsic + r[_CREDIT], mid_f)
            out.append(cell)
            clusters.setdefault(r[_NAME], []).append(cell)
        for name in sorted(clusters):
            self._fold(t, s, name, clusters[name])
        self._write_rows(s, out)

    def _takes(self, spec, row):
        """Whether a state with ``spec`` holds this settled row (the row sets of D7)."""
        if spec.rows == "all":
            return True
        rep = row[_T] >= self._report
        return rep if spec.rows == "reported" else rep and not row[_WARM]

    def _fold(self, t, s, name, cells):
        """Fold one whole ``(name, s)`` cluster into every state that takes rows of it, and the name sums."""
        blocks = [block_index(s, self._start, w) for w in self._blocks]
        group_equal = self._param("row_weight") == "group_equal"
        for key, state in self._states.items():
            spec = _SPECS[key]
            if spec.rows in ("sens", "sens_reported"):
                continue
            use = [c for c in cells if self._takes(spec, c[0])
                   and (spec.k == 1 or c[0][_EDIR] is not None)]
            if not use:
                continue
            dollar = key == "dollar"
            sc = [1.0 if dollar else c[0][_SCALE] for c in use]
            idx = 3 if spec.price == "decision" else 2
            e = [c[0][_E] / f for c, f in zip(use, sc)]
            y = [c[idx] / f for c, f in zip(use, sc)]
            if key == "forward":
                e = [c[0][_CB][0] for c in use]
            weights = [c[0][_BASE] for c in use] if group_equal else None
            e_dir = [c[0][_EDIR] / f for c, f in zip(use, sc)] if spec.k == 2 else None
            sigma2 = [c[0][_SIGMA2] for c in use] if spec.k == 2 else None
            top = [bool(c[0][_TOP]) for c in use] if spec.top else None
            state.add_cluster(t, name, s, max(c[0][_FILL] for c in use), [c[0][_BUCKET] for c in use], e, y,
                              blocks, e_dir=e_dir, weights=weights, sides=[c[0][_SIGN] for c in use], top=top,
                              sigma2=sigma2)
            if key in self._adjust:
                self._adjust[key].add_cluster(t, name, s, e, y, [c[0][_CB][_PRIMARY.index(key)] for c in use],
                                              weights)

    def _write_rows(self, s, cells):
        """Write the reported rows of one settlement date (decided at or after ``report_from``)."""
        keep = sorted((c for c in cells if c[0][_T] >= self._report),
                      key=lambda c: (c[0][_T], c[0][_NAME], c[0][_EXPIRY], c[0][_RIGHT], c[0][_STRIKE]))
        if not keep:
            return
        lines = [_canon({"t": r[_T], "name": r[_NAME], "expiry": r[_EXPIRY], "right": r[_RIGHT],
                         "strike": r[_STRIKE], "sign": r[_SIGN], "bucket": r[_BUCKET], "e": r[_E],
                         "c": r[_CF][0], "r": rf, "r_dec": rd, "code": code, "scale": r[_SCALE], "w": r[_BASE],
                         "e_dir": r[_EDIR], "top": r[_TOP], "map_warmup": r[_WARM], "c_bucket": r[_CB][0]})
                 for r, code, rf, rd, _ in keep]
        self._count("rows_reported", len(lines))
        atomic_write(os.path.join(self._out, "rows", f"rows-{s}.jsonl"), ("\n".join(lines) + "\n").encode())
        self._manifest.add(f"rows/rows-{s}.jsonl")

    # -- decide ----------------------------------------------------------------------------

    def _row(self, raw, t, nxt):
        """Validate one hook row (its fill session must be ``nxt``, the next session) and return it as a clean dict."""
        if not isinstance(raw, dict) or any(k not in raw for k in _ROW_KEYS):
            raise ValueError(f"a decision row needs {list(_ROW_KEYS)}, got {raw!r}")
        row = dict(raw)
        for key in ("expiry", "settle_date", "fill_date"):
            row[key] = _iso(row[key], key)
        for key in ("name", "group", "right"):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(f"{key} must be a non-empty string, got {row[key]!r}")
        if row["right"][:1].lower() not in ("c", "p"):
            raise ValueError(f"right must be a call or a put, got {row['right']!r}")
        if row["sign"] not in (1, -1):
            raise ValueError(f"sign must be 1 or -1, got {row['sign']!r}")
        for key in ("factor", "strike", "e", "dte", "abs_z", "spread", "v", "credit", "multiplier"):
            if isinstance(row[key], bool) or not number_ok(row[key]):
                raise ValueError(f"{key} must be a finite number, got {row[key]!r}")
        if self._param("edge_scale") == "none":
            row["scale"] = 1.0
        else:
            sc = row.get("scale")
            if isinstance(sc, bool) or not number_ok(sc) or not sc > 0:
                raise ValueError(f"scale must be a finite number > 0 under edge_scale {self._param('edge_scale')}, got {sc!r}")
        row.setdefault("e_dir", None)
        row.setdefault("direction_code", None)
        row.setdefault("sigma2", None)
        row.setdefault("mid", None)
        row.setdefault("fee", 0.0)
        row.setdefault("cdf_repaired", False)
        if self._direction:
            if row["e_dir"] is None:
                if row["direction_code"] not in _DIRECTION_CODES:
                    raise ValueError(f"a row without e_dir needs direction_code in {list(_DIRECTION_CODES)}, "
                                     f"got {row['direction_code']!r}")
            elif isinstance(row["e_dir"], bool) or not number_ok(row["e_dir"]):
                raise ValueError(f"e_dir must be a finite number or None, got {row['e_dir']!r}")
        for key in ("mid", "sigma2"):
            v = row[key]
            if v is not None and (isinstance(v, bool) or not number_ok(v) or v < 0):
                raise ValueError(f"{key} must be None or a finite number >= 0, got {v!r}")
        if isinstance(row["fee"], bool) or not number_ok(row["fee"]):
            raise ValueError(f"fee must be a finite number, got {row['fee']!r}")
        if row["fill_date"] <= t:
            raise _Refusal("calendar_mismatch", f"fill session {row['fill_date']} is not after the decision {t}")
        if row["fill_date"] != nxt:
            raise _Refusal("fill_not_next_session", f"fill session {row['fill_date']} is not the next session {nxt} after {t}")
        return row

    def _decide(self, t, maps, fits):
        """Bucket and map the day's rows, then store them in shards keyed by settlement date."""
        nxt = _iso(self._next_session(t), "next session")
        raw = self._decision_rows(t)
        self._check_ram()
        best = {}
        for item in raw:
            row = self._row(item, t, nxt)
            key = (row["name"], row["expiry"], row["right"], row["strike"])
            old = best.get(key)
            if old is None:
                best[key] = row
                continue
            self._count("side_duplicate")
            if (row["e"], row["sign"]) > (old["e"], old["sign"]):
                best[key] = row
        del raw
        vols = {}
        for key in sorted(best):
            row = best[key]
            if vols.setdefault(row["name"], row["v"]) != row["v"]:
                raise ValueError(f"{row['name']}: v differs within one decision session")
        regimes = {n: self._regime.of(n, t, v) for n, v in sorted(vols.items())}
        sizes = {}
        for row in best.values():
            sizes[row["group"]] = sizes.get(row["group"], 0) + 1
        kept = []
        for key in sorted(best):
            row = best[key]
            if row["settle_date"] >= self._gate.sealed_from:
                self._count("beyond_seal")
                continue
            if row["fill_date"] >= row["settle_date"]:
                self._count("no_fill_quote")
                self._count("no_fill_quote.session")
                continue
            kept.append(key)
        order = sorted(kept, key=lambda k: (best[k]["e"], k))
        n_kept = len(order)
        top = {k: (10 * i) // n_kept == 9 for i, k in enumerate(order)}
        shards = {}
        warm = maps["expanding_fill"]
        group_equal = self._param("row_weight") == "group_equal"
        for key in kept:
            row = best[key]
            regime, cold = regimes[row["name"]]
            if cold:
                self._count("regime_warmup")
            b = self._buckets.of(row["dte"], row["abs_z"], row["spread"], regime)
            if warm.warm and warm.buckets[b].pooled:
                self._count("pooled_bucket")
            e, name, sc = row["e"] / row["scale"], row["name"], row["scale"]
            cb = [maps[k].bucket_only(b, e) for k in _PRIMARY]
            cf = [maps[k].calibrated(b, name, e) for k in _PRIMARY]
            c2 = None
            if self._direction:
                if row["e_dir"] is None:
                    self._count("no_direction_edge")
                    self._count(f"no_direction_edge.{row['direction_code']}")
                else:
                    d = row["e_dir"] / sc
                    c2 = []
                    for k in _DIRECTION:
                        c2.append(fits[k].bucket_only_two(b, d, e - d))
            shards.setdefault(row["settle_date"], []).append(
                [row["group"], name, row["expiry"], row["fill_date"], row["factor"], row["right"], row["strike"],
                 row["sign"], row["e"], b, row["credit"], row["multiplier"], cb, cf, row["e_dir"],
                 row["direction_code"], row["sigma2"], sc, 1.0 / sizes[row["group"]] if group_equal else 1.0,
                 top[key], bool(row["cdf_repaired"]), not warm.warm, row["mid"], row["fee"], c2])
            self._count("rows_decided")
        self._check_ram()
        for name, v in vols.items():
            self._regime.add(name, t, v)
        for s, rows in shards.items():
            os.makedirs(os.path.join(self._out, "pending", s), exist_ok=True)
            atomic_write(self._shard(s, t), _canon({"rows": rows}).encode())
            self._pending[s] = sorted({*self._pending.get(s, []), t})

    # -- summary ---------------------------------------------------------------------------

    def _summary(self, weeks):
        digest = hashlib.sha256()
        for rel in sorted(self._manifest):
            digest.update(os.path.basename(rel).encode())
            with open(os.path.join(self._out, rel), "rb") as fh:
                digest.update(fh.read())
        return {"params_hash": self._params_hash(), "pins": self._input_pins(), "first": weeks[0],
                "last": weeks[-1], "weeks": len(weeks), "states": list(self._states),
                "counts": dict(sorted(self._counts.items())), "output_sha256": digest.hexdigest()}
