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
)
from dskit.pipeline.node import Node, atomic_write, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "EdgeReliabilityWalk"]

#: No node kind is registered: a document names ``dskit.pipeline.libs.edge_walk:EdgeReliabilityWalk``.
NODE_KINDS = ()

#: Bump on ANY change to the checkpoint's content or meaning; a checkpoint of another version refuses.
_VERSION = 2
#: The four states, in the order every stored per-row list holds them.
_STATES = ("expanding_fill", "decayed_fill", "expanding_decision", "decayed_decision")
_FILL_CODES = ("session", "basis", "quote")
_LEVEL_CODES = ("basis_changed", "no_settlement_bar")
_ROW_KEYS = ("group", "name", "expiry", "settle_date", "fill_date", "factor", "right", "strike", "sign", "e",
             "dte", "abs_z", "spread", "v", "credit", "multiplier")
#: Params that cannot change a number of the output (where it lands, how often it is saved, the RAM cap).
_OPERATIONAL = ("checkpoint_every", "ram_gb_limit", "out_dir")
_REQUIRED = ("window", "report_from", "out_dir", "buckets", "regime", "pooling", "name_adjust")
_DEFAULTS = {"half_life_weeks": 26, "checkpoint_every": 1, "ram_gb_limit": 6}


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
                    "pooling", "name_adjust", "checkpoint_every", "ram_gb_limit")
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
        every = params.get("checkpoint_every", 1)
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            problems.append(f"checkpoint_every must be an integer >= 1, got {every!r}")
        return problems

    def _param(self, key):
        return self.params.get(key, _DEFAULTS.get(key))

    def _params_hash(self):
        """Hash every param that can move an output (not ``window.end`` and not the operational knobs)."""
        body = {k: v for k, v in self.params.items() if k not in _OPERATIONAL}
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
        self._buckets = ReliabilityBuckets(p["buckets"])
        self._pool = PartialPooling(p["pooling"])
        self._regime = TrailingRegime(p["regime"])
        lam = 2 ** (-1 / self._param("half_life_weeks"))
        self._states = {}
        for name in _STATES:
            decay = lam if name.startswith("decayed") else None
            self._states[name] = (LinearEdgeState(24, decay), NameAdjustment(p["name_adjust"], decay=decay))
        self._pending = {}
        self._counts = {}

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
        pins = self._input_pins()
        if (blob.get("version") != _VERSION or blob.get("params_hash") != self._params_hash()
                or blob.get("pins") != json.loads(_canon(pins))):
            raise _Refusal("checkpoint_mismatch", "params or input pins differ from the checkpoint's")
        cursor = blob["cursor"]
        if cursor > weeks[-1]:
            raise _Refusal("state_from_future", f"the checkpoint is at {cursor}, after the window end {weeks[-1]}")
        if cursor not in weeks:
            raise _Refusal("calendar_mismatch", f"checkpoint cursor {cursor} is not a decision date")
        for name, rec in blob["states"].items():
            self._states[name] = (LinearEdgeState.from_dict(rec["state"]), NameAdjustment.from_dict(rec["adjust"]))
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
            "states": {k: {"state": st.to_dict(), "adjust": adj.to_dict()} for k, (st, adj) in self._states.items()},
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
        for st, adj in self._states.values():
            st.advance(t)
            adj.advance(t)
        self._settle(t)
        self._check_ram()
        maps = {k: EdgeCalibrationMap.build(t, st, self._pool, adj) for k, (st, adj) in self._states.items()}
        atomic_write(os.path.join(self._out, f"map-{t}.json"),
                     (_canon({"t": t, "states": {k: m.to_dict() for k, m in maps.items()}}) + "\n").encode())
        self._manifest.add(f"map-{t}.json")
        self._decide(t, maps)

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

    def _admit(self, t, s, rows):
        """Join one settlement date's rows to their fill quote and settlement level, then fold clusters."""
        views = [{"group": r[1], "name": r[2], "expiry": r[3], "settle_date": s, "fill_date": r[4],
                  "factor": r[5], "right": r[6], "strike": r[7], "sign": r[8], "decision_date": r[0]}
                 for r in rows]
        fills = list(self._fill_credits(views))
        levels = list(self._settlement_levels(views))
        if len(fills) != len(rows) or len(levels) != len(rows):
            raise ValueError("the fill and settlement hooks must return one answer per row")
        self._check_ram()
        self._count("rows_admitted", len(rows))
        clusters, out = {}, []
        for r, fill, level in zip(rows, fills, levels):
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
                out.append((r, code, None, None))
                continue
            strike = r[7]
            payoff = max(level - strike, 0.0) if r[6][:1].lower() == "c" else max(strike - level, 0.0)
            intrinsic = r[8] * payoff * r[12]
            cell = (r, None, intrinsic + fill, intrinsic + r[11])
            out.append(cell)
            clusters.setdefault(r[2], []).append(cell)
        for name in sorted(clusters):
            self._fold(t, s, name, clusters[name])
        self._write_rows(s, out)

    def _fold(self, t, s, name, cells):
        """Fold one whole ``(name, s)`` cluster into the four states and name sums."""
        buckets = [c[0][10] for c in cells]
        e = [c[0][9] for c in cells]
        fill_date = max(c[0][4] for c in cells)
        for idx, key in enumerate(_STATES):
            st, adj = self._states[key]
            y = [c[2] if key.endswith("fill") else c[3] for c in cells]
            st.add_cluster(t, name, s, fill_date, buckets, e, y)
            adj.add_cluster(t, name, s, e, y, [c[0][13][idx] for c in cells])

    def _write_rows(self, s, cells):
        """Write the reported rows of one settlement date (decided at or after ``report_from``)."""
        keep = sorted((c for c in cells if c[0][0] >= self._report),
                      key=lambda c: (c[0][0], c[0][2], c[0][3], c[0][6], c[0][7]))
        if not keep:
            return
        lines = [_canon({"t": r[0], "name": r[2], "expiry": r[3], "right": r[6], "strike": r[7], "sign": r[8],
                         "bucket": r[10], "e": r[9], "c": r[14], "r": rf, "r_dec": rd, "code": code})
                 for r, code, rf, rd in keep]
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
        if row["fill_date"] <= t:
            raise _Refusal("calendar_mismatch", f"fill session {row['fill_date']} is not after the decision {t}")
        if row["fill_date"] != nxt:
            raise _Refusal("fill_not_next_session", f"fill session {row['fill_date']} is not the next session {nxt} after {t}")
        return row

    def _decide(self, t, maps):
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
        shards = {}
        warm = maps["expanding_fill"]
        for key in sorted(best):
            row = best[key]
            if row["settle_date"] >= self._gate.sealed_from:
                self._count("beyond_seal")
                continue
            if row["fill_date"] >= row["settle_date"]:
                self._count("no_fill_quote")
                self._count("no_fill_quote.session")
                continue
            regime, cold = regimes[row["name"]]
            if cold:
                self._count("regime_warmup")
            b = self._buckets.of(row["dte"], row["abs_z"], row["spread"], regime)
            if warm.warm and warm.buckets[b].pooled:
                self._count("pooled_bucket")
            e, name = row["e"], row["name"]
            cb = [maps[k].bucket_only(b, e) for k in _STATES]
            cf = [maps[k].calibrated(b, name, e) for k in _STATES]
            shards.setdefault(row["settle_date"], []).append(
                [row["group"], name, row["expiry"], row["fill_date"], row["factor"], row["right"], row["strike"],
                 row["sign"], e, b, row["credit"], row["multiplier"], cb, cf])
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
                "last": weeks[-1], "weeks": len(weeks), "counts": dict(sorted(self._counts.items())),
                "output_sha256": digest.hexdigest()}
