"""The tier-2 earnings-like event labeller: front-month implied-vol crushes read from the chain (ADR-0265 node 5, H2).

The repository has no earnings calendar, so H2 ("an earnings straddle bought three sessions before the event and
sold the session after") needs events found in the data. :class:`ChainEventDetector` reads the per-(symbol, date,
target DTE) feature rows :class:`~dskit.pipeline.libs.chain_features.ChainDayFeatures` writes (ATM implied vol,
front straddle ratio, spot, status) and labels a symbol-day ``t`` an event when ALL of these hold, tested in this
order (the first failure is the rejection counted):

1. **crush**: the front-target ATM vol fell between the close of ``t-1`` and the close of ``t`` by at least
   ``crush_min`` in log terms (``log(iv_t / iv_{t-1}) <= -crush_min``);
2. **crush_z**: that change is extreme against the symbol's OWN trailing window: its robust z
   ``(d_t - median(d)) / max(1.4826 * MAD(d), scale_floor)`` over the changes of the ``window`` sessions ending at
   ``t-1`` is at most ``-crush_z_min`` (at least ``min_history`` changes needed);
3. **term**: the front-minus-back vol spread collapsed, ``spread_{t-1} - spread_t >= spread_drop_min`` in vol
   points (a crush that leaves the spread alone is the whole curve moving: a market event, not an announcement).

**The underlying's realized move is NOT a detection criterion.** Selecting events by how far the stock moved
selects them by the outcome an H2 test then measures. The move is carried on each record (``realized_move``) as an
output only, and no score ranks events.

A day passing 1-3 is ``fired``. Two more rules turn fired days into events:

4. **market_wide** (the cross-sectional guard): on a date where at least ``min_symbols_for_guard`` (>= 2) symbols
   reached the rules, the share of those symbols that fired is more than ``max_market_share``: every fired symbol
   of that date is rejected (a market-wide vol collapse is not a set of earnings). With fewer symbols the guard is
   off, which the caller must know: a one-symbol run has no cross-sectional guard.
5. **refractory**: at most one event per symbol per ``refractory`` sessions: a fired day fewer sessions after the
   symbol's last ACCEPTED event is rejected (the first firing day wins; later ones are not a better label because
   nothing after ``t`` may be read). A day rejected by the market guard does not start a window.

**Calendar gaps.** A change from one session to the next is used only when the two are at most ``max_gap_days``
calendar days apart; a longer gap is a window break: the trailing window of every later session starts after the
gap, so a session with fewer than ``min_history`` changes since the gap is not evaluated. The breaks are counted
(``window_breaks``).

**Event date convention.** The announcement is overnight or pre-market, so the move belongs to the OPEN of one
session, and the crush shows in the chain at the CLOSE of that same session. ``event_date`` is that session ``t``:
for an after-the-close announcement on day D it is the session after D; for a pre-market announcement it is D.
(A labelled earnings date A for an after-hours reporter therefore matches ``event_date`` at A or the session after
A; the label params take both offsets.)

**Entry, exit and the look-ahead warning.** ``entry_date`` is ``event_date`` minus ``entry_lead`` sessions (3) and
``exit_date`` is ``event_date`` plus ``exit_lag`` sessions (1), counted in the symbol's own table of sessions.
DETECTION reads only data up to the end of the event day ``t`` (the trailing windows end at ``t-1``; nothing after
``t`` is read, except that ``exit_date`` and the refractory bookkeeping of LATER days exist in the table). But the
entry date is BEFORE the detection date: the detector is an event LABELLER for a retrospective backtest, not a live
signal. An H2(b) entry three sessions before the event is known only ex post, once the event is labelled. Nothing
in this module is a tradable rule, and a document that uses it must say so beside its result.

**Detected events are a subset.** The rules find the announcements with a clear implied-vol crush, which is a
selection on the vol outcome. ``labelled_only`` mode answers it: given an owner-supplied label list it emits ONE
record per label (event date = the label's first session on or after the announcement plus ``label_event_offset``
sessions, whatever the chain did), flagged ``detected`` when the rules accepted an event within ``match_before`` /
``match_after`` sessions of it, so a test can report the result on the detected events, on all labelled
announcements, and the difference, with the coverage per year (``labelled_by_year``).

**No threshold is in this module.** Every rule value is a REQUIRED param: the frozen values and how they were
chosen live in the document that runs the node (ADR-0265 Amendment 4), and tests supply their own. The
:class:`ChainEventCalibrator` node reports precision and recall over a grid of values for labelled dates the owner
supplies; it DROPS (and counts) every row and label on or after ``fit_end_before``, so a grid cannot be scored on
dates later than the fit period, and it never picks the thresholds itself: ``best_by_f1`` is a convenience the
owner reads, not a setting.

**Input rows.** One per symbol, date and target DTE (any other target is ignored, counted); a value is usable when
the row's status equals ``status_ok`` and the vol is a positive finite number. Missing values are counted, never
filled. A repeated (symbol, date, target) refuses; so does a row on or after ``end_before``.

**Counters** (``summary``, all integers): ``sessions`` (distinct symbol-dates), ``missing`` (``no_front``,
``no_prev``, ``gap``, ``no_back``, ``insufficient_history``), ``candidate_days`` (sessions that reached the rules),
``rejected_by`` (``crush``, ``crush_z``, ``term``, ``market_wide``, ``refractory``), ``fired``, ``events``,
``entry_unavailable``, ``exit_unavailable``, ``window_breaks``, ``symbols`` and ``symbols_without_events_count``.
They obey ``sessions = sum(missing) + candidate_days``, ``candidate_days = fired + rejected by 1-3`` and
``fired = events + market_wide + refractory``. The names of the symbols without an event are a separate output.

No node kind is registered: a document names ``dskit.pipeline.libs.chain_events:ChainEventDetector``. Import cost:
stdlib and the pipeline core.
"""

import itertools
import math
from array import array
from bisect import bisect_left, insort
from collections import defaultdict
from datetime import date
from statistics import median

from dskit.pipeline.libs.observation_tables import iso_day
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "RULE_NAMES", "ChainEventCalibrator", "ChainEventDetector", "EventRules"]

#: The rule values every node needs; there is no default for any of them.
RULE_NAMES = ("window", "min_history", "crush_min", "crush_z_min", "spread_drop_min", "refractory",
              "max_market_share", "min_symbols_for_guard", "max_gap_days", "entry_lead", "exit_lag", "scale_floor")

#: No node kind is registered: a document names the class by import path.
NODE_KINDS = ()

_MAD_TO_SD = 1.4826
_MISSING = ("no_front", "no_prev", "gap", "no_back", "insufficient_history")
#: The keys of a session's detail, in :meth:`_Scan.measure` order.
_DETAIL = ("change", "z", "spread_drop", "ret")
_NAN = float("nan")
_RULES_1_3 = ("crush", "crush_z", "term")
_REJECTED = (*_RULES_1_3, "market_wide", "refractory")
_FIELD_PARAMS = ("symbol_field", "date_field", "target_field", "iv_field", "straddle_field", "spot_field",
                 "status_field")
_VOCAB = (*_FIELD_PARAMS, "status_ok", "front_target", "back_target", "end_before")
_LABEL_FIELDS = ("label_symbol_field", "label_date_field", "label_confidence_field")
_LABEL_COUNTS = ("labels_total", "labels_ignored_confidence", "labels_dropped_after_fit_end",
                 "labels_without_symbol_rows", "labels_outside_table", "labels_merged_duplicates", "labels_scored")


def _plain(*values):
    """Say whether every value is an exact int or float: comparing and hashing one runs no user code."""
    return all(type(v) is int or type(v) is float for v in values)


def _is_number(value):
    return number_ok(value) and not isinstance(value, bool)


class EventRules:
    """The thresholds of the detector as one validated value; every one is required."""

    __slots__ = RULE_NAMES

    def __init__(self, **given):
        missing = [n for n in RULE_NAMES if n not in given]
        if missing or set(given) - set(RULE_NAMES):
            raise ValueError(f"EventRules needs exactly {list(RULE_NAMES)}; missing {missing}, "
                             f"unknown {sorted(set(given) - set(RULE_NAMES))}")
        for name in RULE_NAMES:
            setattr(self, name, given[name])

    @classmethod
    def from_params(cls, params):
        """Build the rules from the rule-named entries of ``params`` (all required)."""
        return cls(**{k: params[k] for k in RULE_NAMES})

    def as_dict(self):
        """Return the thresholds as a plain dict in :data:`RULE_NAMES` order."""
        return {name: getattr(self, name) for name in RULE_NAMES}

    @classmethod
    def problems(cls, given):
        """Problems with the rule values in ``given`` (each of :data:`RULE_NAMES` required), empty when none."""
        problems = [f"{name} is required (no default: the frozen value lives in the document)"
                    for name in RULE_NAMES if name not in given]
        v = {name: given[name] for name in RULE_NAMES if name in given}
        for name, lowest in (("window", 2), ("min_history", 2), ("refractory", 1), ("min_symbols_for_guard", 2),
                             ("max_gap_days", 1), ("exit_lag", 1), ("entry_lead", 0)):
            if name in v:
                check_int_param(problems, name, v[name], ge=lowest)
        for name, zero_ok in (("crush_min", False), ("crush_z_min", False), ("scale_floor", False),
                              ("spread_drop_min", True)):
            if name in v and (not _is_number(v[name]) or v[name] < 0 or (v[name] == 0 and not zero_ok)):
                problems.append(f"{name} must be a number {'>= 0' if zero_ok else '> 0'}, got {v[name]!r}")
        if "max_market_share" in v and (not _is_number(v["max_market_share"]) or not 0 < v["max_market_share"] <= 1):
            problems.append(f"max_market_share must be a number in (0, 1], got {v['max_market_share']!r}")
        w, h = v.get("window"), v.get("min_history")
        if all(isinstance(x, int) and not isinstance(x, bool) for x in (w, h)) and h > w:
            problems.append(f"min_history ({h}) cannot exceed window ({w})")
        return problems


class _Series:
    """One symbol's sessions in date order, with the values the rules read."""

    def __init__(self, symbol, cells):
        self.symbol = symbol
        self.dates = sorted(cells)
        self.front = [cells[d].get("front") for d in self.dates]
        self.back = [cells[d].get("back") for d in self.dates]
        self.straddle = [cells[d].get("straddle") for d in self.dates]
        self.spot = [cells[d].get("spot") for d in self.dates]
        self.ordinals = [date.fromisoformat(d).toordinal() for d in self.dates]
        self._stats = {}
        self._measures = (None, None)   # (rule key last scanned, its _Measures once a second scan reuses it)

    def __len__(self):
        return len(self.dates)

    def gap(self, i):
        """Return the calendar days between session ``i-1`` and ``i``."""
        return self.ordinals[i] - self.ordinals[i - 1]

    def breaks(self, max_gap_days):
        """Return how many consecutive-session pairs are more than ``max_gap_days`` apart."""
        o = self.ordinals
        return sum(1 for a, b in zip(o, o[1:]) if b - a > max_gap_days)

    def change(self, i, max_gap_days):
        """Return ``log(front_i / front_{i-1})``; ``None`` when either is unusable or the pair spans a gap."""
        if i < 1 or self.front[i] is None or self.front[i - 1] is None or self.gap(i) > max_gap_days:
            return None
        return math.log(self.front[i] / self.front[i - 1])

    def ret(self, i):
        """Return ``spot_i / spot_{i-1} - 1`` or ``None`` when either is unusable (an output only)."""
        if i < 1 or self.spot[i] is None or self.spot[i - 1] is None:
            return None
        return self.spot[i] / self.spot[i - 1] - 1

    def trailing(self, window, max_gap_days):
        """Return per index ``(n_changes, median, scale)`` of the changes of the ``window`` sessions ending at i-1.

        The window starts after the latest gap longer than ``max_gap_days``. An exact int ``window`` takes
        :meth:`_sliding`; any other (an int subclass may hook the index arithmetic) reads each window in date
        order, as it always did (:meth:`_in_order`).
        """
        key = (window, max_gap_days)
        if key not in self._stats:
            changes = [self.change(i, max_gap_days) for i in range(len(self))]
            if type(window) is int:
                out = self._sliding(changes, window, max_gap_days)
            else:
                out = self._in_order(changes, window, max_gap_days)
            self._stats[key] = out
        return self._stats[key]

    def _in_order(self, changes, window, max_gap_days):
        """Return :meth:`trailing`'s list, each window's changes read in date order."""
        out, last_break = [], 0
        for i in range(len(self)):
            if i >= 2 and self.gap(i - 1) > max_gap_days:
                last_break = i - 1
            c = [x for x in changes[max(1, i - window, last_break + 1):i] if x is not None]
            med = median(c) if c else None
            out.append((len(c), med, _MAD_TO_SD * median([abs(x - med) for x in c]) if c else None))
        return out

    def _sliding(self, changes, window, max_gap_days):
        """Return :meth:`trailing`'s list for an exact int ``window``, sliding one sorted window along the series.

        ``held`` keeps the non-NaN changes of ``[lo, hi)`` sorted, so each median sorts presorted data, and
        ``nans`` counts the NaN ones. A change is the log of a ratio of positive floats: finite or +inf, never
        -0.0 nor -inf, so equal changes are the same bits and the held order equals any sort of the window. A NaN
        change (an infinite front over another) sorts by position, as does the NaN deviation of an infinite
        median: then the window is read in date order, as :meth:`_in_order` does.
        """
        out, last_break, held, nans, lo, hi = [], 0, [], 0, 1, 1
        for i in range(len(self)):
            if i >= 2 and self.gap(i - 1) > max_gap_days:
                last_break = i - 1
            start = max(1, i - window, last_break + 1)
            for x in changes[lo:min(start, hi)]:
                if x is None:
                    continue
                if x != x:
                    nans -= 1
                else:
                    del held[bisect_left(held, x)]
            for x in changes[max(start, hi):i]:
                if x is None:
                    continue
                if x != x:
                    nans += 1
                else:
                    insort(held, x)
            lo, hi = start, max(start, i)
            if nans:
                c = [x for x in changes[start:i] if x is not None]
                med = median(c)
                out.append((len(c), med, _MAD_TO_SD * median([abs(x - med) for x in c])))
                continue
            if not held:
                out.append((0, None, None))
                continue
            med = median(held)
            c = held if math.isfinite(med) else [x for x in changes[start:i] if x is not None]
            out.append((len(held), med, _MAD_TO_SD * median([abs(x - med) for x in c])))
        return out


class _Measures:
    """One series' :meth:`_Scan.measure` outcomes under one rule key, packed per session.

    The missing name (or ``None``), four float columns in :data:`_DETAIL` order and a byte saying ``ret`` is
    ``None``; a missing session's columns hold NaN and are never read. A candidate's ``change``, ``z`` and
    ``spread_drop`` are always exact floats (parsed cells are), and its ``ret`` an exact float or ``None``, which
    the byte keeps apart from a NaN ``ret`` (an infinite spot over another). An ``array("d")`` keeps every
    float's bits, -0.0 and NaN included.
    """

    __slots__ = ("missing", "_columns", "_no_ret")

    def __init__(self):
        self.missing, self._columns, self._no_ret = [], tuple(array("d") for _ in _DETAIL), bytearray()

    def append(self, missing, detail):
        """Pack one :meth:`_Scan.measure` outcome."""
        self.missing.append(missing)
        change, z, spread_drop, ret = (_NAN,) * 4 if missing else detail.values()
        self._no_ret.append(ret is None)
        for column, value in zip(self._columns, (change, z, spread_drop, _NAN if ret is None else ret)):
            column.append(value)

    def values(self, i):
        """Return candidate session ``i``'s detail values in :data:`_DETAIL` order."""
        change, z, spread_drop, ret = self._columns
        return change[i], z[i], spread_drop[i], None if self._no_ret[i] else ret[i]


class FeatureTable:
    """The parsed feature rows: one :class:`_Series` per symbol, plus the row counters."""

    def __init__(self, series, counts):
        self.series, self.counts = series, counts

    @classmethod
    def parse(cls, key, rows, params, drop_from=None):
        """Parse the feature rows: a row at or after ``end_before`` refuses, at or after ``drop_from`` is dropped."""
        p = params
        cells = defaultdict(dict)
        seen = set()
        counts = {"rows": 0, "rows_other_target": 0, "rows_dropped_after_fit_end": 0}
        days = {}

        def get(name, row, where):
            if not isinstance(row, dict) or p[name] not in row:
                raise ValueError(f"{where}: missing field {p[name]!r} ({name})")
            return row[p[name]]

        for index, row in enumerate(rows):
            where = f"{key}: row {index}"
            counts["rows"] += 1
            symbol = get("symbol_field", row, where)
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"{where}: symbol must be a non-empty string, got {symbol!r}")
            text = get("date_field", row, where)
            day = days.get(text) if type(text) is str else None
            if day is None:
                day = iso_day(text, f"{where} date").isoformat()
                if type(text) is str:  # iso_day reads a str by its text alone
                    days[text] = day
            if day >= p["end_before"]:
                raise ValueError(f"{where}: date {day} is at or after end_before {p['end_before']}")
            target = get("target_field", row, where)
            if target not in (p["front_target"], p["back_target"]):
                counts["rows_other_target"] += 1
                continue
            if drop_from is not None and day >= drop_from:
                counts["rows_dropped_after_fit_end"] += 1
                continue
            if (symbol, day, target) in seen:
                raise ValueError(f"{where}: ({symbol!r}, {day}, target {target}) repeats an earlier row")
            seen.add((symbol, day, target))
            usable = get("status_field", row, where) == p["status_ok"]
            iv, spot, straddle = (cls._number(get(n, row, where), p[n], where)
                                  for n in ("iv_field", "spot_field", "straddle_field"))
            cell = cells[symbol].setdefault(day, {})
            side = "front" if target == p["front_target"] else "back"
            cell[side] = iv if usable and iv is not None and iv > 0 else None
            if side == "front":
                cell["straddle"] = straddle if usable and straddle is not None and straddle > 0 else None
            if usable and spot is not None and spot > 0:
                cell["spot"] = spot
        if not seen:
            raise ValueError(f"{key}: the records input is empty (or holds no row of the front or back target) - "
                             "events of nothing are refused, not passed")
        return cls({s: _Series(s, c) for s, c in sorted(cells.items())}, counts)

    @staticmethod
    def _number(value, name, where):
        if type(value) is float and math.isfinite(value):  # the hot case: float() of an exact float is itself
            return value
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        if not _is_number(value):
            raise ValueError(f"{where}: {name} must be a finite number or null, got {value!r}")
        return float(value)


class LabelSet:
    """The owner-supplied labelled dates mapped onto the sessions of a :class:`FeatureTable`.

    ``anchors[symbol]`` is the sorted list of ``(first session index on or after the announcement, announcement date)``,
    one per distinct session; the counters reconcile: ``labels_total`` is the sum of every other ``labels_*``
    counter.
    """

    def __init__(self, anchors, counts):
        self.anchors, self.counts = anchors, counts

    @staticmethod
    def rows(labels, labels_key, key):
        """Return the label rows: the input itself, or its ``labels_key`` entry when the input is a mapping."""
        if isinstance(labels, dict):
            if labels_key is None or not isinstance(labels.get(labels_key), list):
                raise ValueError(f"{key}: the labels input is a mapping: name the list it holds with labels_key "
                                 f"(got {labels_key!r}; keys {sorted(labels)})")
            return labels[labels_key]
        return labels

    @classmethod
    def parse(cls, key, labels, params, table, drop_from=None):
        """Parse and place the labels; a label at or after ``end_before`` refuses, at or after ``drop_from`` is dropped."""
        p = params
        counts = dict.fromkeys(_LABEL_COUNTS, 0)
        parsed = []
        for index, row in enumerate(cls.rows(labels, p.get("labels_key"), key)):
            where = f"{key}: label {index}"
            try:
                symbol, day, conf = (row[p[n]] for n in _LABEL_FIELDS)
            except (KeyError, TypeError) as err:
                raise ValueError(f"{where}: missing field {err}") from err
            day = iso_day(day, f"{where} date").isoformat()
            if day >= p["end_before"]:
                raise ValueError(f"{where}: date {day} is at or after end_before {p['end_before']}")
            parsed.append((symbol, day, conf))
        anchors = {}
        for symbol, day, conf in sorted(parsed, key=lambda t: (str(t[0]), t[1], str(t[2]))):
            counts["labels_total"] += 1
            if conf not in p["confidences"]:
                counts["labels_ignored_confidence"] += 1
            elif drop_from is not None and day >= drop_from:
                counts["labels_dropped_after_fit_end"] += 1
            elif symbol not in table.series:
                counts["labels_without_symbol_rows"] += 1
            else:
                dates = table.series[symbol].dates
                at = bisect_left(dates, day)
                if at >= len(dates) or (at == 0 and day < dates[0]):
                    counts["labels_outside_table"] += 1
                elif any(a == at for a, _ in anchors.get(symbol, [])):
                    counts["labels_merged_duplicates"] += 1
                else:
                    anchors.setdefault(symbol, []).append((at, day))
                    counts["labels_scored"] += 1
        return cls(anchors, counts)


class _Scan:
    """One pass of the rules over a :class:`FeatureTable`: events and counters."""

    def __init__(self, table, rules):
        self.table, self.rules = table, rules
        self.counts = {"sessions": 0, "missing": dict.fromkeys(_MISSING, 0), "candidate_days": 0,
                       "rejected_by": dict.fromkeys(_REJECTED, 0), "fired": 0, "events": 0,
                       "entry_unavailable": 0, "exit_unavailable": 0, "symbols": len(table.series),
                       "window_breaks": sum(s.breaks(rules.max_gap_days) for s in table.series.values()),
                       "symbols_without_events_count": 0}
        self.without_events = []
        self.event_index = defaultdict(list)

    def run(self):
        """Return ``(events, counts)``; ``without_events`` and ``event_index`` are set."""
        fired, candidates = defaultdict(list), defaultdict(int)
        counts, missed, rejected = self.counts, self.counts["missing"], self.counts["rejected_by"]
        for series in self.table.series.values():
            measured = self._measured(series)
            for i in range(len(series)):
                counts["sessions"] += 1
                if measured is None:
                    missing, detail = self.measure(series, i)
                    values = None if missing else tuple(detail.values())
                else:
                    missing, detail = measured.missing[i], None
                    values = None if missing else measured.values(i)
                if missing:
                    missed[missing] += 1
                    continue
                counts["candidate_days"] += 1
                candidates[series.dates[i]] += 1
                failed = self._failed_rule(values)
                if failed:
                    rejected[failed] += 1
                else:
                    counts["fired"] += 1
                    fired[series.dates[i]].append((series, i, dict(zip(_DETAIL, values)) if detail is None else detail))
        events = self._accept(fired, candidates)
        self.counts["events"] = len(events)
        events.sort(key=lambda e: (e["event_date"], e["symbol"]))
        with_events = {e["symbol"] for e in events}
        self.without_events = [s for s in self.table.series if s not in with_events]
        self.counts["symbols_without_events_count"] = len(self.without_events)
        return events, self.counts

    def _measured(self, series):
        """Return the series' :class:`_Measures` under this scan's rules, or ``None`` to measure each session in turn.

        :meth:`measure` reads only the window, gap, history and floor rules, so consecutive cells of a calibration
        grid that share them can share one measurement per series. The series holds the last key only: a key seen
        for the first time is measured in the loop as before and remembered; the next scan with the same key packs
        the outcomes (about 41 bytes a session) and later scans reuse them, until the key changes. The key holds
        each value's type so ``1`` and ``1.0`` stay apart; ``None`` unless all four, and every key of the series'
        trailing cache, are a plain int or float, whose arithmetic and comparison run no user code.

        The contract this buys: measurements are taken once per rule key per series and reused, only while
        :meth:`measure` provably runs no user code: the four rule values and every key of the series' trailing
        cache are exact ints or floats. So :class:`ChainEventCalibrator`, which parses one table and scans it once
        per grid cell, reuses measurements across cells only for plain-typed keys; a cell or an earlier cell whose
        window or gap is a subclass measures every session in turn, as before. The rest holds only for direct
        reuse of one :class:`FeatureTable`, which no node builds: the second scan of an unchanged key measures each
        series whole before scanning its sessions, so a refusal inside :meth:`measure` comes before that series'
        session counters and rule hooks (a node run never gets there: the same refusal ends it at the first scan
        of that key); and from the third scan :meth:`measure` is not called again, so a change to the series data
        or to the rules between or during scans, and a rules object that hooks its attribute reads, are not
        re-observed. A refusal while packing stores nothing, so the next scan measures again.
        """
        r = self.rules
        values = (r.window, r.max_gap_days, r.min_history, r.scale_floor)
        if not all(_plain(v) for v in values) or not all(_plain(*k) for k in series._stats):
            return None
        key = tuple((type(v), v) for v in values)
        seen, packed = series._measures
        if seen != key:
            series._measures = (key, None)
            return None
        if packed is None:
            packed = _Measures()
            for i in range(len(series)):
                packed.append(*self.measure(series, i))
            series._measures = (key, packed)   # only once complete: a refusal mid-way leaves nothing half-filled
        return packed

    def measure(self, series, i):
        """Return ``(missing-data name or None, detail)`` for session ``i``; detail holds what could be computed."""
        r = self.rules
        detail = {"change": None, "z": None, "spread_drop": None, "ret": series.ret(i)}
        if series.front[i] is None:
            return "no_front", detail
        if i < 1 or series.front[i - 1] is None:
            return "no_prev", detail
        if series.gap(i) > r.max_gap_days:
            return "gap", detail
        detail["change"] = series.change(i, r.max_gap_days)
        if series.back[i] is None or series.back[i - 1] is None:
            return "no_back", detail
        detail["spread_drop"] = (series.front[i - 1] - series.back[i - 1]) - (series.front[i] - series.back[i])
        n_changes, med, scale = series.trailing(r.window, r.max_gap_days)[i]
        if n_changes < r.min_history:
            return "insufficient_history", detail
        detail["z"] = (detail["change"] - med) / max(scale, r.scale_floor)
        return None, detail

    def _failed_rule(self, values):
        """Return the first of rules 1-3 the session's detail values (in ``_DETAIL`` order) fail, else ``None``."""
        r = self.rules
        change, z, spread_drop, _ = values
        crush, crush_z, term = change <= -r.crush_min, z <= -r.crush_z_min, spread_drop >= r.spread_drop_min
        if not crush:
            return "crush"
        if not crush_z:
            return "crush_z"
        if not term:
            return "term"
        return None

    def _accept(self, fired, candidates):
        """Apply the market guard, then the refractory window, to the fired days; return the events."""
        r = self.rules
        survivors = defaultdict(list)
        for day, hits in fired.items():
            if candidates[day] >= r.min_symbols_for_guard and len(hits) / candidates[day] > r.max_market_share:
                self.counts["rejected_by"]["market_wide"] += len(hits)
                continue
            for series, i, detail in hits:
                survivors[series.symbol].append((i, detail))
        events = []
        for symbol, hits in survivors.items():
            series, last = self.table.series[symbol], None
            for i, detail in sorted(hits, key=lambda h: h[0]):
                if last is not None and i - last < r.refractory:
                    self.counts["rejected_by"]["refractory"] += 1
                    continue
                last = i
                self.event_index[symbol].append(i)
                events.append(self.record(series, i, detail))
                self.counts["entry_unavailable"] += i - r.entry_lead < 0
                self.counts["exit_unavailable"] += i + r.exit_lag >= len(series)
        return events

    def record(self, series, i, detail):
        """Return the output fields of the session ``i`` (``None`` where the table does not hold a value)."""
        r = self.rules
        entry, exit_ = i - r.entry_lead, i + r.exit_lag
        before = i - 1 if i >= 1 else None

        def at(values, k):
            return values[k] if k is not None else None

        return {
            "symbol": series.symbol, "event_date": series.dates[i],
            "entry_date": series.dates[entry] if entry >= 0 else None,
            "exit_date": series.dates[exit_] if exit_ < len(series) else None,
            "front_iv_before": at(series.front, before), "front_iv_after": series.front[i],
            "back_iv_before": at(series.back, before), "back_iv_after": series.back[i],
            "implied_move": at(series.straddle, before), "realized_move": detail["ret"],
            "crush": detail["change"], "crush_z": detail["z"], "spread_drop": detail["spread_drop"],
        }


class _EventNode(Node):
    """The shared vocabulary of the detector and the calibrator: field names, targets, label matching."""

    role = "transform"

    @classmethod
    def _vocabulary_problems(cls, problems, params):
        for name in (*_FIELD_PARAMS, "status_ok"):
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string, got {params.get(name)!r}")
        for name in ("front_target", "back_target"):
            check_int_param(problems, name, params.get(name), ge=1)
        front, back = params.get("front_target"), params.get("back_target")
        if isinstance(front, int) and isinstance(back, int) and not back > front:
            problems.append(f"back_target ({back}) must exceed front_target ({front})")
        cls._iso_problem(problems, params, "end_before")

    @staticmethod
    def _iso_problem(problems, params, name):
        value = params.get(name)
        try:
            ok = isinstance(value, str) and bool(date.fromisoformat(value))
        except ValueError:
            ok = False
        if not ok:
            problems.append(f"{name} must be an ISO date string, got {value!r}")

    @classmethod
    def _label_problems(cls, problems, params):
        for name in _LABEL_FIELDS:
            if not isinstance(params.get(name), str) or not params.get(name):
                problems.append(f"{name} must be a non-empty string, got {params.get(name)!r}")
        conf = params.get("confidences")
        if not isinstance(conf, list) or not conf or not all(isinstance(c, str) and c for c in conf):
            problems.append(f"confidences must be a non-empty list of strings, got {conf!r}")
        for name in ("match_before", "match_after"):
            check_int_param(problems, name, params.get(name), ge=0)
        if "labels_key" in params and (not isinstance(params["labels_key"], str) or not params["labels_key"]):
            problems.append(f"labels_key must be a non-empty string, got {params['labels_key']!r}")

    @staticmethod
    def _matched(announced, events, p):
        """Return the first of the sorted ``events`` indexes inside the match window of session ``announced``."""
        return next((e for e in events if announced - p["match_before"] <= e <= announced + p["match_after"]), None)


class ChainEventDetector(_EventNode):
    """Label earnings-like events from a feature table of front and back implied vols (ADR-0265 node 5).

    Role ``transform``; input ``records`` (the per-symbol, date, target-DTE rows of
    :class:`~dskit.pipeline.libs.chain_features.ChainDayFeatures`: the table is read once, in any order) and, in
    ``labelled_only`` mode, ``labels``. Outputs ``records`` (one event per row: ``symbol``, ``event_date``,
    ``entry_date``, ``exit_date``, ``front_iv_before``, ``front_iv_after``, ``back_iv_before``, ``back_iv_after``,
    ``implied_move`` (the front straddle ratio at the close before the event), ``realized_move`` (the close-to-close
    return of the event day, an OUTPUT only), ``crush``, ``crush_z``, ``spread_drop``; in ``labelled_only`` mode one row
    per label with ``announcement_date`` and ``detected`` added), sorted by event date then symbol; ``summary`` (the
    integer counters, the rules in force; in ``labelled_only`` mode the ``labels_*`` counters, which reconcile,
    ``labelled_detected``, ``labelled_event_unavailable`` and ``labelled_by_year``); and ``symbols_without_events``
    (names). See the module docstring for the rules, the date convention and the look-ahead warning: this is a
    retrospective labeller, not a live signal, and its events are the subset with a clear vol crush.

    Parameters
    ----------
    params : dict
        REQUIRED vocabulary: ``symbol_field``, ``date_field``, ``target_field``, ``iv_field``, ``straddle_field``,
        ``spot_field``, ``status_field`` (non-empty strings), ``status_ok`` (the status text of a usable row),
        ``front_target`` and ``back_target`` (ints, ``back_target > front_target``: the target DTEs the front and back
        rows carry), ``end_before`` (ISO date: a row on or after it refuses). REQUIRED rule values, none with a
        default: ``window``, ``min_history``, ``crush_min``, ``crush_z_min``, ``spread_drop_min``, ``refractory``,
        ``max_market_share`` (in (0, 1]), ``min_symbols_for_guard`` (>= 2), ``max_gap_days``, ``entry_lead``,
        ``exit_lag``, ``scale_floor``. Optional ``labelled_only`` (bool, default false): then also REQUIRED
        ``label_symbol_field``, ``label_date_field``, ``label_confidence_field``, ``confidences`` (the accepted
        values), ``match_before`` and ``match_after`` (ints >= 0), ``label_event_offset`` (int >= 0: sessions after the
        announcement's first session that the event session lies; 1 for an after-the-close reporter) and optionally
        ``labels_key`` (the entry of a mapping-valued ``labels`` input that holds the rows).

    Examples
    --------
    Label events in a features table written with 30- and 90-day targets::

        node = ChainEventDetector("events", {
            "symbol_field": "symbol", "date_field": "date", "target_field": "target_dte",
            "iv_field": "atm_iv", "straddle_field": "straddle_ratio", "spot_field": "spot",
            "status_field": "status", "status_ok": "ok", "front_target": 30, "back_target": 90,
            "end_before": "2021-01-01", **rule_values})     # rule_values: the document's frozen thresholds
        events = node.run(ctx, {"records": feature_rows})["records"]
        # -> events[0]["event_date"] == "2018-02-02", events[0]["entry_date"] == "2018-01-30"
    """

    outputs = ("records", "summary", "symbols_without_events")
    _PARAMS = (*_VOCAB, *RULE_NAMES, "labelled_only", *_LABEL_FIELDS, "confidences", "match_before", "match_after",
               "label_event_offset", "labels_key")

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
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._vocabulary_problems(problems, params)
        problems.extend(EventRules.problems(params))
        labelled = params.get("labelled_only", False)
        if not isinstance(labelled, bool):
            problems.append(f"labelled_only must be a boolean, got {labelled!r}")
        elif labelled:
            cls._label_problems(problems, params)
            check_int_param(problems, "label_event_offset", params.get("label_event_offset"), ge=0)
        else:
            extra = sorted(set(params) & {*_LABEL_FIELDS, "confidences", "match_before", "match_after",
                                          "label_event_offset", "labels_key"})
            if extra:
                problems.append(f"{extra} are label params and need labelled_only true")
        return problems

    @property
    def rules(self):
        """The thresholds in force."""
        return EventRules.from_params(self.params)

    def run(self, ctx, inputs):
        """Label the events of the feature table.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is a function of its inputs and params.
        inputs : dict
            ``records``: the feature rows; ``labels`` (``labelled_only`` mode): the labelled dates.

        Returns
        -------
        dict
            ``records``, ``summary`` and ``symbols_without_events`` (see the class).

        Raises
        ------
        ValueError
            The input is empty, a row is malformed, repeats a (symbol, date, target) or is dated at or after
            ``end_before``; or ``labelled_only`` is set and the labels are missing, malformed or dated at or after
            ``end_before``.
        """
        p = self.params
        table = FeatureTable.parse(self.key, inputs["records"], p)
        scan = _Scan(table, self.rules)
        events, counts = scan.run()
        records, extra = events, {}
        if p.get("labelled_only", False):
            if "labels" not in inputs:
                raise ValueError(f"{self.key}: labelled_only needs a 'labels' input")
            labelset = LabelSet.parse(self.key, inputs["labels"], p, table)
            records, extra = self._labelled(scan, labelset)
            extra.update(labelset.counts)
        self.log.info("events: %d from %d symbol(s), %d session(s)", len(events), counts["symbols"], counts["sessions"])
        return {"records": records, "summary": {**counts, "rules": self.rules.as_dict(), **table.counts, **extra},
                "symbols_without_events": scan.without_events}

    def _labelled(self, scan, labelset):
        """Return one record per label (flagged ``detected``) and the labelled counters."""
        p = self.params
        out, by_year, unavailable = [], defaultdict(lambda: {"labels": 0, "detected": 0}), 0
        for symbol, anchors in sorted(labelset.anchors.items()):
            series = scan.table.series[symbol]
            for at, announced in anchors:
                i = at + p["label_event_offset"]
                if i >= len(series):
                    unavailable += 1
                    continue
                detected = self._matched(at, scan.event_index.get(symbol, []), p) is not None
                _, detail = scan.measure(series, i)
                out.append({**scan.record(series, i, detail), "announcement_date": announced, "detected": detected})
                year = by_year[announced[:4]]
                year["labels"] += 1
                year["detected"] += detected
        out.sort(key=lambda r: (r["event_date"], r["symbol"]))
        return out, {"labelled_detected": sum(r["detected"] for r in out), "labelled_event_unavailable": unavailable,
                     "labelled_by_year": dict(sorted(by_year.items()))}


class ChainEventCalibrator(_EventNode):
    """Precision and recall of the detector over a threshold grid, against labelled event dates (ADR-0265 H2).

    Role ``transform``; inputs ``records`` (the feature table) and ``labels`` (rows of a symbol, an announcement date
    and a confidence the OWNER supplies; the list must be COMPLETE for the symbols it names over the table's span,
    or precision is not defined). Every row and label on or after ``fit_end_before`` is dropped and counted before
    anything is scored, so the grid never sees later dates. Outputs ``records`` (one row per grid cell: ``params`` of
    the cell, ``events``, ``matched``, ``labels``, ``precision`` (``None`` with no event), ``recall``, ``f1``,
    ``events_unlabelled_symbols``) and ``summary`` (the integer counters, the label counters, which reconcile, the
    base rules, ``best_by_f1`` for reading, never applied).

    An event matches a label when its ``event_date`` is at most ``match_after`` sessions after, and at most
    ``match_before`` sessions before, the first session on or after the announcement date; each label matches one
    event. Events of a symbol with no label at all are excluded from precision and counted.

    Parameters
    ----------
    params : dict
        The detector's vocabulary, plus ``label_symbol_field``, ``label_date_field``, ``label_confidence_field``,
        ``confidences`` (the confidence values accepted), ``fit_end_before`` (ISO date), ``match_before`` and
        ``match_after`` (ints >= 0), ``grid`` (non-empty map of a rule name to a non-empty list of values) and
        ``base`` (the rule values under every cell: EVERY rule not in the grid is required here), ``max_cells``
        (default 5000), ``labels_key`` (the entry of a mapping-valued ``labels`` input that holds the rows, as a
        ``table-file`` supplies).

    Examples
    --------
    Score a crush-size grid on 2016-2019 dates, the way the thresholds were frozen::

        node = ChainEventCalibrator("calibrate", {
            **vocabulary, "label_symbol_field": "symbol", "label_date_field": "earnings_date",
            "label_confidence_field": "confidence", "confidences": ["high"], "fit_end_before": "2020-01-01",
            "match_before": 0, "match_after": 1, "base": rule_values,
            "grid": {"crush_min": [0.08, 0.12, 0.16], "crush_z_min": [2, 3, 4]}})
        cells = node.run(ctx, {"records": feature_rows, "labels": label_rows})["records"]
        # -> cells[0] == {"params": {"crush_min": 0.08, "crush_z_min": 2}, "events": 15, "matched": 13, ...}
    """

    outputs = ("records", "summary")
    _PARAMS = (*_VOCAB, *_LABEL_FIELDS, "confidences", "fit_end_before", "match_before", "match_after", "grid",
               "base", "max_cells", "labels_key")

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
        reject_unknown_params(problems, params, cls._PARAMS)
        cls._vocabulary_problems(problems, params)
        cls._label_problems(problems, params)
        cls._iso_problem(problems, params, "fit_end_before")
        if "max_cells" in params:
            check_int_param(problems, "max_cells", params["max_cells"], ge=1)
        base = params.get("base")
        if not isinstance(base, dict) or set(base) - set(RULE_NAMES):
            problems.append(f"base must be a map of rule names {list(RULE_NAMES)}, got {base!r}")
            base = {}
        grid = params.get("grid")
        if not isinstance(grid, dict) or not grid:
            problems.append(f"grid must be a non-empty map of rule name -> list of values, got {grid!r}")
            return problems
        for name, values in grid.items():
            if name not in RULE_NAMES or not isinstance(values, list) or not values:
                problems.append(f"grid[{name!r}] must be a non-empty list for a rule in {list(RULE_NAMES)}")
        if problems:
            return problems
        cells = math.prod(len(v) for v in grid.values())
        if cells > params.get("max_cells", 5000):
            problems.append(f"grid has {cells} cells, more than max_cells {params.get('max_cells', 5000)}")
            return problems
        seen = []
        for cell in cls._cells(grid):
            for problem in EventRules.problems({**base, **cell}):
                if problem not in seen:
                    seen.append(problem)
        return problems + seen

    @staticmethod
    def _cells(grid):
        names = list(grid)
        return [dict(zip(names, values)) for values in itertools.product(*(grid[n] for n in names))]

    def run(self, ctx, inputs):
        """Score every grid cell.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records`` (the feature rows) and ``labels`` (the labelled dates).

        Returns
        -------
        dict
            ``records`` and ``summary`` (see the class).

        Raises
        ------
        ValueError
            The feature table is empty or malformed, or a label row is malformed or dated at or after ``end_before``.
        """
        p = self.params
        table = FeatureTable.parse(self.key, inputs["records"], p, drop_from=p["fit_end_before"])
        labelset = LabelSet.parse(self.key, inputs["labels"], p, table, drop_from=p["fit_end_before"])
        out = []
        for cell in self._cells(p["grid"]):
            scan = _Scan(table, EventRules(**{**p["base"], **cell}))
            scan.run()
            out.append({"params": cell, **self._score(scan, labelset)})
        best = max(out, key=lambda r: r["f1"])        # a tie goes to the first cell of the grid
        return {"records": out, "summary": {**labelset.counts, **table.counts, "base": dict(p["base"]),
                                            "cells": len(out), "best_by_f1": best}}

    def _score(self, scan, labelset):
        p = self.params
        n_events = unlabelled = matched = 0
        for symbol, indexes in scan.event_index.items():
            if symbol in labelset.anchors:
                n_events += len(indexes)
            else:
                unlabelled += len(indexes)
        n_labels = sum(len(v) for v in labelset.anchors.values())
        for symbol, anchors in labelset.anchors.items():
            free = sorted(scan.event_index.get(symbol, []))
            for at, _ in anchors:
                hit = self._matched(at, free, p)
                if hit is not None:
                    free.remove(hit)
                    matched += 1
        precision = matched / n_events if n_events else None
        recall = matched / n_labels if n_labels else None
        f1 = 0.0 if not matched else 2 * precision * recall / (precision + recall)
        return {"events": n_events, "matched": matched, "labels": n_labels, "precision": precision,
                "recall": recall, "f1": f1, "events_unlabelled_symbols": unlabelled}
