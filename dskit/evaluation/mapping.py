"""Rows -> ``dskit-eval-v1`` events from a JSON field map (ADR-0183 phase 3a).

Most backtests already emit plain row lists — decisions, fills, marks,
cash flows. :class:`EventMapping` turns them into one valid event log from
a declaration alone, so a pipeline document can plug the evaluator in
without a project-specific mapper node:

* one :class:`RowMap` per input port says which row field is the event's
  instant (``ts``, default ``asof_ms``), when it was known (``known``,
  default the instant), its ``instrument``, and each body field;
* a body field is a row field name, ``{"const": value}`` or
  ``{"template": "d-{asof_ms}"}`` (``str.format`` over the row) — the
  three :class:`FieldSource` kinds;
* the allowed targets are the event kind's own ``FIELDS`` names, so the
  map is closed (default-deny) without restating the schema, and a
  required field left unmapped is refused before the run;
* ``explode`` fans one row out over a list field, or over a dict field as
  ``{"key": k, **value}`` rows — a per-book or per-contract nesting (a
  nested field wins over a parent field or ``key`` of the same name);
* ``candidates`` rows are grouped into their decision by ``decision_id``.

Rows merge into one log ordered by instant, then
:data:`~dskit.evaluation.events.KIND_ORDER`, then input order; the log is
bracketed with ``run_start``/``run_end`` and validated once through
:class:`~dskit.evaluation.events.EventLog`, so every problem is reported
together. Field names are flat: shaping nested rows is an upstream
``derive``/``join`` node's job, and a domain join (which fill answers
which decision) stays in the project's own code.

Import cost: stdlib plus ``dskit.pipeline`` and ``dskit.production``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict

from dskit.evaluation.events import (
    CANDIDATE_FIELDS,
    ENVELOPE,
    EVENT_KINDS,
    KIND_ORDER,
    SCHEMA,
    EvaluationError,
    EventLog,
    RunStart,
    instant_ok,
)

__all__ = [
    "CANDIDATES_PORT",
    "PORTS",
    "Const",
    "EventMapping",
    "FieldSource",
    "RowField",
    "RowMap",
    "Template",
]

#: Input port -> the event kind its rows become.
PORTS = {
    "decisions": "decision",
    "orders": "order",
    "refusals": "refusal",
    "skips": "skip",
    "fills": "fill",
    "marks": "mark",
    "cashflows": "cashflow",
    "outcomes": "outcome",
    "solves": "solve",
}

#: The port whose rows become ``candidates`` entries of their decision.
CANDIDATES_PORT = "candidates"

#: The row field an instant is read from when a map names none.
DEFAULT_TS_FIELD = "asof_ms"

_RANK = {kind: i for i, kind in enumerate(KIND_ORDER)}

#: The ``run_end`` status a mapped log closes with (the rows ran to the end).
_RUN_END_STATUS = "ok"


class FieldSource(ABC):
    """Where one mapped value comes from: a row field, a constant or a template.

    Examples
    --------
    ::

        source = FieldSource.parse("symbol", "map.fills.instrument", [])
        source.value({"symbol": "AAA"}, "fills[0]")  # 'AAA'
    """

    @abstractmethod
    def value(self, row, where):
        """Return this source's value for ``row``.

        Parameters
        ----------
        row : dict
        where : str
            The row's location, for the error.

        Returns
        -------
        object

        Raises
        ------
        EvaluationError
            When the row lacks what the source reads.
        """

    @staticmethod
    def parse(spec, where, problems):
        """Return the source a spec declares, or None after appending the problem.

        Parameters
        ----------
        spec : object
            A field name (non-empty str), ``{"const": v}`` or
            ``{"template": "text {field}"}``.
        where : str
        problems : list of str
            Appended to on a malformed spec.

        Returns
        -------
        FieldSource or None
        """
        if isinstance(spec, str) and spec:
            return RowField(spec)
        if isinstance(spec, dict) and len(spec) == 1:
            (name, arg), = spec.items()
            maker = _SOURCES.get(name)
            if maker is not None:
                return maker.build(arg, where, problems)
        problems.append(f"{where} must be a row field name, {{\"const\": value}} or "
                        f"{{\"template\": \"...{{field}}...\"}}, got {spec!r}")
        return None


class RowField(FieldSource):
    """Read one field of the row; a missing field fails loudly.

    Parameters
    ----------
    name : str

    Examples
    --------
    ::

        RowField("price").value({"price": 10.5}, "fills[0]")  # 10.5
    """

    def __init__(self, name):
        self.name = name

    def value(self, row, where):
        """Return ``row[name]``; see :meth:`FieldSource.value`."""
        if self.name not in row:
            raise EvaluationError([f"{where} has no field {self.name!r} "
                                   f"(fields: {sorted(row)})"])
        return row[self.name]


class Const(FieldSource):
    """The same value for every row.

    Parameters
    ----------
    constant : object

    Examples
    --------
    ::

        Const("buy").value({}, "fills[0]")  # 'buy'
    """

    def __init__(self, constant):
        self.constant = constant

    @classmethod
    def build(cls, arg, where, problems):
        """Return the source for ``{"const": arg}`` (any JSON value)."""
        return cls(arg)

    def value(self, row, where):
        """Return the constant; see :meth:`FieldSource.value`."""
        return self.constant


class Template(FieldSource):
    """``str.format`` over the row, for ids built from several fields.

    Parameters
    ----------
    text : str
        E.g. ``"d-{asof_ms}"``.

    Examples
    --------
    ::

        Template("o-{symbol}-{asof_ms}").value({"symbol": "AAA", "asof_ms": 60000}, "r")
        # -> 'o-AAA-60000'
    """

    def __init__(self, text):
        self.text = text

    @classmethod
    def build(cls, arg, where, problems):
        """Return the source for ``{"template": arg}``, or None on a non-string."""
        if not isinstance(arg, str) or not arg:
            problems.append(f"{where}.template must be a non-empty string, got {arg!r}")
            return None
        return cls(arg)

    def value(self, row, where):
        """Return the formatted text; see :meth:`FieldSource.value`."""
        try:
            return self.text.format_map(row)
        except KeyError as exc:
            raise EvaluationError([f"{where} has no field {exc.args[0]!r} for template "
                                   f"{self.text!r}"]) from None
        except (ValueError, IndexError) as exc:
            raise EvaluationError([f"{where}: template {self.text!r} failed: {exc}"]) from None


#: The dict-shaped sources, by their one key — a table, never a branch.
_SOURCES = {"const": Const, "template": Template}


class RowMap:
    """How one port's rows become events of one kind.

    Parameters
    ----------
    kind : str
        An event kind (``fill``, ``decision``, ...), or ``"candidate"`` for
        the candidates port.
    spec : dict
        ``fields`` (``{target: source}``, required), optional ``ts``,
        ``known``, ``instrument`` (sources), ``explode`` (a row field),
        and for candidates ``decision_id`` (a source). Validate first with
        :meth:`problems`.

    Examples
    --------
    ::

        fills = RowMap("fill", {"instrument": "symbol", "fields": {
            "fill_id": {"template": "f-{symbol}-{asof_ms}"}, "side": "side",
            "qty": "qty", "price": "price"}})
        fills.items([{"symbol": "AAA", "asof_ms": 60000, "side": "buy", "qty": 1,
                      "price": 10.0}], "fills")[0][1]
        # -> {'fill_id': 'f-AAA-60000', 'side': 'buy', 'qty': 1, 'price': 10.0}
    """

    #: The spec's own keys; ``decision_id`` only for candidates.
    KEYS = ("fields", "ts", "known", "instrument", "explode", "notes")
    CANDIDATE_KEYS = KEYS + ("decision_id",)

    def __init__(self, kind, spec):
        self.kind = kind
        scratch = []
        self.fields = {target: FieldSource.parse(source, target, scratch)
                       for target, source in spec["fields"].items()}
        self.ts = FieldSource.parse(spec.get("ts", DEFAULT_TS_FIELD), "ts", scratch)
        self.known = FieldSource.parse(spec["known"], "known", scratch) if "known" in spec \
            else self.ts
        self.instrument = (FieldSource.parse(spec["instrument"], "instrument", scratch)
                           if spec.get("instrument") is not None else None)
        self.explode = spec.get("explode")
        self.decision_id = (FieldSource.parse(spec["decision_id"], "decision_id", scratch)
                            if "decision_id" in spec else None)
        if scratch:
            raise EvaluationError(scratch)

    @classmethod
    def problems(cls, kind, spec, where):
        """Return every problem with one port's spec; empty when acceptable.

        Parameters
        ----------
        kind : str
            The event kind, or ``"candidate"``.
        spec : object
        where : str

        Returns
        -------
        list of str
        """
        if not isinstance(spec, dict):
            return [f"{where} must be an object, got {spec!r}"]
        candidate = kind == "candidate"
        problems = []
        allowed = cls.CANDIDATE_KEYS if candidate else cls.KEYS
        unknown = sorted(set(spec) - set(allowed))
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown} — allowed: {list(allowed)}")
        for key in ("ts", "known", "instrument", "decision_id"):
            if spec.get(key) is not None:
                FieldSource.parse(spec[key], f"{where}.{key}", problems)
        if "explode" in spec and (not isinstance(spec["explode"], str) or not spec["explode"]):
            problems.append(f"{where}.explode must be a row field name, got {spec['explode']!r}")
        fields = spec.get("fields")
        if not isinstance(fields, dict):
            return problems + [f"{where}.fields must be an object of target -> source, "
                               f"got {fields!r}"]
        targets = CANDIDATE_FIELDS if candidate else tuple(
            f.name for f in EVENT_KINDS[kind].FIELDS)
        unknown = sorted(set(fields) - set(targets))
        if unknown:
            problems.append(f"{where}.fields: {unknown} are not {kind} fields — "
                            f"allowed: {list(targets)}")
        for target, source in fields.items():
            FieldSource.parse(source, f"{where}.fields.{target}", problems)
        problems.extend(cls._required_problems(kind, spec, fields, where))
        return problems

    @staticmethod
    def _required_problems(kind, spec, fields, where):
        """Refuse a spec that leaves a required field or envelope instrument unmapped."""
        if kind == "candidate":
            missing = [] if "instrument" in fields else ["instrument"]
            if "decision_id" not in spec:
                missing.append("decision_id")
        else:
            cls = EVENT_KINDS[kind]
            missing = [f.name for f in cls.FIELDS if f.required and f.name not in fields]
            if cls.NEEDS_INSTRUMENT and spec.get("instrument") is None:
                missing.append("instrument")
        return [f"{where} must map {missing} (required for {kind})"] if missing else []

    def rows(self, rows, port):
        """Return ``rows`` with :attr:`explode` applied.

        Parameters
        ----------
        rows : list of dict
        port : str

        Returns
        -------
        list of (str, dict)
            ``(where, row)`` pairs.
        """
        out = []
        for index, row in enumerate(rows):
            where = f"{port}[{index}]"
            if not isinstance(row, dict):
                raise EvaluationError([f"{where} must be an object, got {row!r}"])
            if self.explode is None:
                out.append((where, row))
                continue
            out.extend(self._exploded(row, where))
        return out

    def _exploded(self, row, where):
        """Fan one row out over its list or dict :attr:`explode` field."""
        nested = RowField(self.explode).value(row, where)
        parent = {k: v for k, v in row.items() if k != self.explode}
        if isinstance(nested, dict):
            items = [(f"{where}.{self.explode}[{k!r}]",
                      {**parent, "key": k, **(v if isinstance(v, dict) else {"value": v})})
                     for k, v in nested.items()]
        elif isinstance(nested, list):
            items = [(f"{where}.{self.explode}[{i}]",
                      {**parent, **(v if isinstance(v, dict) else {"value": v})})
                     for i, v in enumerate(nested)]
        else:
            raise EvaluationError([f"{where}.{self.explode} must be a list or an object to "
                                   f"explode, got {type(nested).__name__}"])
        return items

    def body(self, row, where):
        """Return the mapped body fields of one row.

        Parameters
        ----------
        row : dict
        where : str

        Returns
        -------
        dict
        """
        return {target: source.value(row, f"{where} ({target})")
                for target, source in self.fields.items()}

    def items(self, rows, port):
        """Return one ``(ts_ms, body, known_ms, instrument, where)`` per (exploded) row.

        Parameters
        ----------
        rows : list of dict
        port : str

        Returns
        -------
        list of tuple
        """
        out = []
        for where, row in self.rows(rows, port):
            ts = _instant(self.ts.value(row, f"{where} (ts)"), f"{where} (ts)")
            known = _instant(self.known.value(row, f"{where} (known)"), f"{where} (known)")
            instrument = (self.instrument.value(row, f"{where} (instrument)")
                          if self.instrument is not None else None)
            out.append((ts, self.body(row, where), known, instrument, where))
        return out


def _instant(value, where):
    """Return ``value`` as epoch ms, refusing what ``events.instant_ok`` refuses."""
    if not instant_ok(value):
        raise EvaluationError([f"{where} must be an int epoch-ms instant >= 0, got {value!r}"])
    return value


class EventMapping:
    """Every port's rows -> one ordered, validated ``dskit-eval-v1`` event list.

    Parameters
    ----------
    run_start : dict
        The ``run_start`` body a document declares (``tz`` required;
        ``title``, ``project``, ``criteria``, ``trials``, ``units``, ...).
        Run-directory facts passed to :meth:`events` override it.
    maps : dict
        ``{port: spec}`` over :data:`PORTS` and :data:`CANDIDATES_PORT`.
        Validate first with :meth:`problems`.

    Examples
    --------
    ::

        mapping = EventMapping({"run_id": "r1", "tz": "UTC"}, {
            "marks": {"instrument": "symbol", "fields": {"price": "close"}}})
        events = mapping.events({"marks": [{"symbol": "AAA", "asof_ms": 60000,
                                            "close": 10.0}]})
        [e["kind"] for e in events]  # ['run_start', 'mark', 'run_end']
    """

    def __init__(self, run_start, maps):
        self.run_start = dict(run_start)
        self.maps = {port: RowMap(self.kind_of(port), spec) for port, spec in maps.items()}

    @staticmethod
    def kind_of(port):
        """Return the event kind a port's rows become (``candidate`` for candidates)."""
        return "candidate" if port == CANDIDATES_PORT else PORTS[port]

    @classmethod
    def problems(cls, run_start, maps):
        """Return every problem with a declaration; empty when acceptable.

        Parameters
        ----------
        run_start : object
        maps : object

        Returns
        -------
        list of str
        """
        problems = []
        if not isinstance(run_start, dict):
            problems.append(f"run_start must be an object, got {run_start!r}")
        elif set(run_start) & set(ENVELOPE):
            problems.append(f"run_start may not declare envelope field(s) "
                            f"{sorted(set(run_start) & set(ENVELOPE))} — the mapping stamps them")
        else:
            # The body a document declares is checked by the kind's own
            # rules; run_id may come from the run directory, so a stand-in
            # fills it when the document leaves it out.
            probe = {"schema": SCHEMA, "seq": 0, "kind": RunStart.kind, "ts_ms": 0,
                     "known_ms": 0, "instrument": None, "run_id": "run", **run_start}
            problems.extend(RunStart.problems(probe, "run_start"))
        if not isinstance(maps, dict) or not maps:
            return problems + [f"map must be a non-empty object of port -> spec, got {maps!r}"]
        ports = (*PORTS, CANDIDATES_PORT)
        unknown = sorted(set(maps) - set(ports))
        if unknown:
            problems.append(f"map: unknown port(s) {unknown} — allowed: {list(ports)}")
        for port, spec in maps.items():
            if port in ports:
                problems.extend(RowMap.problems(cls.kind_of(port), spec, f"map.{port}"))
        if CANDIDATES_PORT in maps and "decisions" not in maps:
            problems.append("map.candidates needs map.decisions: candidates join a decision")
        decision_fields = (maps.get("decisions") or {}).get("fields")
        if (CANDIDATES_PORT in maps and isinstance(decision_fields, dict)
                and "candidates" in decision_fields):
            problems.append("map.decisions.fields.candidates and map.candidates both supply "
                            "a decision's candidates — declare one")
        return problems

    def events(self, inputs, provenance=None):
        """Return the event list for the wired ports.

        Parameters
        ----------
        inputs : dict
            ``{port: list of rows}``; an absent or None port adds nothing.
        provenance : dict or None
            Run-directory facts (``run_id``, ``config_hash``, ``data``,
            ``config``) overriding the declared ``run_start``.

        Returns
        -------
        list of dict
            Validated schema-v1 events, ``seq`` 0..n-1.

        Raises
        ------
        EvaluationError
            On a row that lacks a mapped field, candidates of an unknown
            decision, or any event :class:`EventLog` refuses.
        """
        grouped = self._candidates(inputs.get(CANDIDATES_PORT))
        body = []
        for port, rowmap in self.maps.items():
            rows = inputs.get(port)
            if port == CANDIDATES_PORT or rows is None:
                continue
            for order, (ts, fields, known, instrument, _where) in enumerate(
                    rowmap.items(rows, port)):
                if rowmap.kind == "decision" and fields.get("decision_id") in grouped:
                    # Plan time refuses a decisions map that also maps
                    # candidates, so a grouped list is never discarded.
                    fields["candidates"] = grouped.pop(fields["decision_id"])
                body.append((ts, _RANK[rowmap.kind], order, rowmap.kind, known, instrument,
                             fields))
        if grouped:
            raise EvaluationError([f"candidates name decision(s) no decision row carries: "
                                   f"{sorted(map(str, grouped))[:5]}"])
        return self._envelope(body, provenance or {})

    def _candidates(self, rows):
        """``{decision_id: [candidate]}`` from the candidates port, in row order."""
        rowmap = self.maps.get(CANDIDATES_PORT)
        grouped = defaultdict(list)
        if rowmap is None or rows is None:
            return grouped
        for where, row in rowmap.rows(rows, CANDIDATES_PORT):
            decision_id = rowmap.decision_id.value(row, f"{where} (decision_id)")
            grouped[decision_id].append(rowmap.body(row, where))
        return grouped

    def _envelope(self, body, provenance):
        """Sort, bracket with run_start/run_end, stamp the envelope and validate."""
        body.sort(key=lambda item: item[:3])
        first = body[0][0] if body else 0
        last = body[-1][0] if body else 0
        start = {key: value for key, value in {**self.run_start, **provenance}.items()
                 if key not in ENVELOPE}
        items = [(first, RunStart.kind, first, None, start)]
        items.extend((ts, kind, known, instrument, fields)
                     for ts, _rank, _order, kind, known, instrument, fields in body)
        items.append((last, "run_end", last, None, {"status": _RUN_END_STATUS}))
        events = [{"schema": SCHEMA, "seq": seq, "kind": kind, "ts_ms": ts, "known_ms": known,
                   "instrument": instrument, **fields}
                  for seq, (ts, kind, known, instrument, fields) in enumerate(items)]
        EventLog(events)
        return events
