"""``family-availability`` — per-date availability of declared field families.

Why this kind exists
--------------------
A panel's columns usually arrive in families that are only useful together
(every quantile of a curve, every lag of a history). Asking "on which dates is
each family fully usable, and on which dates are all of several usable at
once" the verb-by-verb way costs a check, a summary and a fan-out join PER
family, with every family and field name typed into the graph. A new source
with different families then means editing, or generating, the graph.

This kind reads the families as DATA (``params.families``) and reduces a
``records`` stream to four tables: per-date flags, a per-family summary, the
count of dates for every family subset, and the cohort bounds. Nothing here
names a family, a field or a family count.

The all-rows rule
-----------------
A date (one group-key tuple plus the date value) is ``"yes"`` for a family only
when EVERY row on it satisfies the family: each field finite, each ``require``
condition true, and, where the schema carries them, the companion columns
(``<field><missing_suffix>`` equal to 0, ``<field><age_suffix>`` within
``[0, max_age_days]``). One bad row makes the date ``"no"``, so a date always
has exactly one pattern.

Combinations without 2^n joins
------------------------------
Each date becomes an n-bit mask over the families in sorted-name order. Dates
are counted per distinct mask, then a superset sum over one flat integer array
of length 2^n turns "dates whose mask equals M" into "dates whose mask contains
C" for every subset C, in O(n * 2^n) time and no row fan-out.

``sources`` is provenance only: it is echoed to ``summary`` and the node never
opens a stream.

Admission
---------
``admit {min_rate}`` (ADR-0226 amendment) adds an ``admission`` port: per group,
the distinct dates, ``rate_<family>`` (the share of dates the family is
available), ``required_<family>`` (1 when the rate reaches ``min_rate``) and the
sorted ``admitted`` names; ``dates`` then also carries ``complete`` (every
admitted family available). A ``flag`` family reads the yes/no column an earlier
availability run wrote, so a later step can admit from that output.
"""

from __future__ import annotations

import operator

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_flow import _MISSING, _field
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["FamilyAvailability", "register"]

#: The one name of each default and vocabulary this module owns.
DEFAULT_MAX_FAMILIES = 16
EMIT_MODES = ("all", "nonzero")
DEFAULT_EMIT = EMIT_MODES[0]
YES, NO = "yes", "no"
FLAG_PREFIX = "available_"

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}
_ORDERING = frozenset(_OPS) - {"==", "!="}
_FAMILY_KEYS = ("sources", "fields", "max_age_days", "require", "flag")
_RAW_KEYS = ("fields", "max_age_days", "require")
_ADMIT_KEYS = ("min_rate",)
RATE_PREFIX = "rate_"
REQUIRED_PREFIX = "required_"
COMPLETE_FIELD = "complete"
ADMITTED_FIELD = "admitted"
DEFAULT_DATES_FIELD = "dates"
_SOURCE_KEYS = ("source", "stream", "relpath")
_REQUIRE_KEYS = ("field", "op", "value")
_COMBINATION_KEYS = ("emit", "max_families")
_PARAMS = (
    "families",
    "schema_fields",
    "date_field",
    "group_keys",
    "missing_suffix",
    "age_suffix",
    "combinations",
    "admit",
    "dates_count_field",
)


def _usable(cell):
    """Say whether a cell carries a value: not absent, None or NaN."""
    return cell is not _MISSING and cell is not None and cell == cell


def _is_real(cell):
    """Say whether cell is a non-bool int or float; infinity compares, NaN is screened by _usable."""
    return isinstance(cell, (int, float)) and not isinstance(cell, bool)


def _holds(cell, op, value):
    """Say whether ``cell <op> value``; an unusable cell fails every op."""
    if not _usable(cell) or isinstance(cell, bool):
        return False
    if op in _ORDERING and not (_is_real(cell) and number_ok(value)):
        return False
    return _OPS[op](cell, value)


def _is_count(value):
    """Say whether value is a non-bool int >= 1."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _name_problems(label, value):
    """Problems for a param that must be a non-empty string."""
    if isinstance(value, str) and value:
        return []
    return [f"{label} must be a non-empty string, got {value!r}"]


class _Family:
    """One compiled family spec: the row-level tests for a single family."""

    def __init__(self, fields, require, max_age_days, missing_suffix, age_suffix, schema):
        self.fields = tuple(fields)
        self.require = tuple((r["field"], r["op"], r["value"]) for r in require)
        self.missing = tuple(
            f + missing_suffix
            for f in self.fields
            if missing_suffix is not None and f + missing_suffix in schema
        )
        self.max_age = max_age_days
        self.ages = tuple(
            f + age_suffix
            for f in self.fields
            if age_suffix is not None
            and max_age_days is not None
            and f + age_suffix in schema
        )

    def present(self, row):
        cells = [_field(row, f) for f in self.fields]
        return all(c is not _MISSING and c is not None for c in cells)

    def finite(self, row):
        return all(number_ok(_field(row, f)) for f in self.fields)

    def satisfied(self, row):
        """Say whether one row meets every condition of the family."""
        return (
            self.finite(row)
            and all(_holds(_field(row, f), op, v) for f, op, v in self.require)
            and all(self._companion_ok(row, c, self._zero) for c in self.missing)
            and all(self._companion_ok(row, c, self._in_age) for c in self.ages)
        )

    def _zero(self, cell):
        return cell == 0

    def _in_age(self, cell):
        return 0 <= cell <= self.max_age

    @staticmethod
    def _companion_ok(row, column, test):
        """Pass an absent companion cell unchecked; a present one must pass ``test``."""
        cell = _field(row, column)
        return cell is _MISSING or (number_ok(cell) and test(cell))


class _FlagFamily(_Family):
    """A family read from a yes/no column an earlier availability run wrote."""

    def __init__(self, column):
        self.column = column
        self.fields = (column,)

    def _cell(self, row):
        cell = _field(row, self.column)
        if cell not in (YES, NO):
            raise ValueError(f"flag column {self.column!r} must hold {YES!r} or {NO!r}, got {cell!r}")
        return cell

    def present(self, row):
        return self._cell(row) is not None

    def finite(self, row):
        return self._cell(row) is not None

    def satisfied(self, row):
        """Say whether the row's flag column reads yes; refuse any other value."""
        return self._cell(row) == YES


class FamilyAvailability(Node):
    """Reduce a record stream to per-date availability of declared families.

    Parameters
    ----------
    params : dict
        ``families`` (required): ``{name: {"fields": [...], "sources": [...],
        "max_age_days": n | null, "require": [{"field", "op", "value"}]}}``
        or ``{name: {"flag": column}}`` for a yes/no column already written.
        ``admit`` (optional): ``{"min_rate": number in (0, 1]}`` adds the
        ``admission`` port and the ``complete`` flag on ``dates``.
        ``schema_fields`` (required): the panel's column names.
        ``date_field`` (required): the column that dates a row.
        ``group_keys`` (list, default none): columns that split dates.
        ``missing_suffix`` / ``age_suffix`` (str, default off): companion
        column suffixes. ``combinations``: ``{"emit": "all"|"nonzero",
        "max_families": int}``.

    Examples
    --------
    One family over two dated rows::

        node = FamilyAvailability("fa", params={
            "families": {"curve": {"fields": ["q1", "q2"]}},
            "schema_fields": ["day", "q1", "q2"],
            "date_field": "day",
        })
        out = node.run(None, {"records": [{"day": "d1", "q1": 1.0, "q2": 2.0}]})
        # -> out["dates"] == [{"day": "d1", "available_curve": "yes"}]
    """

    role = "transform"
    outputs = ("dates", "summary", "combinations", "cohort", "admission")

    _PARAMS = _PARAMS

    @classmethod
    def validate_params(cls, params):
        """Return the problems with ``params`` (default-deny); empty when valid."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for key in ("families", "schema_fields", "date_field"):
            if key not in params:
                problems.append(f"{key} is required")
        schema = params.get("schema_fields")
        if "schema_fields" in params and not is_node_ref(schema):
            if not isinstance(schema, (list, tuple)) or not all(
                isinstance(s, str) for s in schema
            ):
                problems.append("schema_fields must be a list of strings")
                schema = ()
        else:
            schema = ()
        if "date_field" in params and not is_node_ref(params["date_field"]):
            problems += _name_problems("date_field", params["date_field"])
        problems += cls._group_problems(params)
        problems += cls._count_field_problems(params)
        for key in ("missing_suffix", "age_suffix"):
            if key in params and params[key] is not None and not is_node_ref(params[key]):
                problems += _name_problems(key, params[key])
        families = params.get("families")
        if "families" in params and not is_node_ref(families):
            problems += cls._families_problems(families, set(schema))
        comb = cls._combination_problems(params.get("combinations"), families)
        return problems + comb + cls._admit_problems(params.get("admit"))

    @classmethod
    def _count_field_problems(cls, params):
        name = params.get("dates_count_field")
        if name is None or is_node_ref(name):
            return []
        problems = _name_problems("dates_count_field", name)
        keys = params.get("group_keys")
        if not problems and isinstance(keys, (list, tuple)) and name in keys:
            problems.append(f"dates_count_field {name!r} collides with a group_keys entry")
        return problems

    @classmethod
    def _admit_problems(cls, admit):
        if admit is None or is_node_ref(admit):
            return []
        if not isinstance(admit, dict):
            return [f"admit must be a mapping, got {admit!r}"]
        problems = []
        reject_unknown_params(problems, admit, _ADMIT_KEYS)
        if "min_rate" not in admit:
            return problems + ["admit.min_rate is required"]
        rate = admit["min_rate"]
        if not is_node_ref(rate) and not (number_ok(rate) and 0 < rate <= 1):
            problems.append(f"admit.min_rate must be a number in (0, 1], got {rate!r}")
        return problems

    @classmethod
    def _group_problems(cls, params):
        keys = params.get("group_keys")
        if keys is None or is_node_ref(keys):
            return []
        if not isinstance(keys, (list, tuple)) or not all(
            isinstance(k, str) and k for k in keys
        ):
            return [f"group_keys must be a list of non-empty strings, got {keys!r}"]
        return []

    @classmethod
    def _families_problems(cls, families, schema):
        if not isinstance(families, dict) or not families:
            return [f"families must be a non-empty mapping, got {families!r}"]
        problems = []
        for name, spec in families.items():
            problems += _name_problems("family name", name)
            problems += cls._spec_problems(name, spec, schema)
        return problems

    @classmethod
    def _spec_problems(cls, name, spec, schema):
        if not isinstance(spec, dict):
            return [f"family {name!r} must be a mapping, got {spec!r}"]
        problems = []
        reject_unknown_params(problems, spec, _FAMILY_KEYS)
        problems = [f"family {name!r}: {p}" for p in problems]
        if "flag" in spec:
            return problems + cls._flag_problems(name, spec, schema)
        fields = spec.get("fields")
        if not isinstance(fields, (list, tuple)) or not fields:
            problems.append(f"family {name!r}: fields must be a non-empty list")
        else:
            problems += [
                f"family {name!r}: field {f!r} is not in schema_fields"
                for f in fields
                if f not in schema
            ]
        age = spec.get("max_age_days")
        if age is not None and not (number_ok(age) and age >= 0):
            problems.append(f"family {name!r}: max_age_days must be a number >= 0 or null")
        problems += cls._sources_problems(name, spec.get("sources"))
        for entry in spec.get("require") or ():
            problems += cls._require_problems(name, entry, schema)
        if spec.get("require") is not None and not isinstance(spec["require"], (list, tuple)):
            problems.append(f"family {name!r}: require must be a list")
        return problems

    @classmethod
    def _flag_problems(cls, name, spec, schema):
        problems = [
            f"family {name!r}: flag cannot be combined with {k!r}" for k in _RAW_KEYS if k in spec
        ]
        column = spec["flag"]
        if not isinstance(column, str) or not column:
            problems.append(f"family {name!r}: flag must be a non-empty string, got {column!r}")
        elif column not in schema:
            problems.append(f"family {name!r}: flag {column!r} is not in schema_fields")
        return problems + cls._sources_problems(name, spec.get("sources"))

    @classmethod
    def _sources_problems(cls, name, sources):
        if sources is None:
            return []
        if not isinstance(sources, (list, tuple)) or not all(
            isinstance(s, dict) for s in sources
        ):
            return [f"family {name!r}: sources must be a list of mappings"]
        problems = []
        for entry in sources:
            reject_unknown_params(problems, entry, _SOURCE_KEYS)
        return [f"family {name!r}: {p}" for p in problems]

    @classmethod
    def _require_problems(cls, name, entry, schema):
        if not isinstance(entry, dict):
            return [f"family {name!r}: require entry must be a mapping, got {entry!r}"]
        problems = []
        reject_unknown_params(problems, entry, _REQUIRE_KEYS)
        problems += [f"missing {k!r}" for k in _REQUIRE_KEYS if k not in entry]
        if entry.get("field") not in schema and "field" in entry:
            problems.append(f"field {entry['field']!r} is not in schema_fields")
        op = entry.get("op")
        if "op" in entry and op not in _OPS:
            problems.append(f"op {op!r} is not one of {sorted(_OPS)}")
        elif op in _ORDERING and "value" in entry and not number_ok(entry["value"]):
            problems.append(f"op {op!r} needs a numeric value, got {entry['value']!r}")
        return [f"family {name!r}: require: {p}" for p in problems]

    @classmethod
    def _combination_problems(cls, comb, families):
        if comb is None or is_node_ref(comb):
            return []
        if not isinstance(comb, dict):
            return [f"combinations must be a mapping, got {comb!r}"]
        problems = []
        reject_unknown_params(problems, comb, _COMBINATION_KEYS)
        if comb.get("emit", DEFAULT_EMIT) not in EMIT_MODES:
            problems.append(f"combinations.emit must be one of {EMIT_MODES}, got {comb['emit']!r}")
        limit = comb.get("max_families", DEFAULT_MAX_FAMILIES)
        if not _is_count(limit):
            problems.append(f"combinations.max_families must be an int >= 1, got {limit!r}")
        elif isinstance(families, dict) and len(families) > limit:
            problems.append(
                f"{len(families)} families exceed combinations.max_families={limit}: "
                "the combination table has 2**n rows"
            )
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"``; it reads its stream and params only.

        Parameters
        ----------
        params : dict
            The declared params; unused, the answer holds for every document.
        verified_run_evidence : dict
            The release's evidence; unused, a pure node needs none.

        Returns
        -------
        str
            ``"pure"``.
        """
        return "pure"

    def validate_inputs(self, inputs):
        """Return a problem when the ``records`` input is not wired."""
        return [] if "records" in inputs else ["family-availability needs a 'records' input"]

    def run(self, ctx, inputs):
        """Return the ``dates``, ``summary``, ``combinations`` and ``cohort`` tables.

        Parameters
        ----------
        ctx : NodeContext
            Unused; the node is pure.
        inputs : dict
            ``{"records": list of mapping rows}``.

        Returns
        -------
        dict
            Output port name -> list of records.

        Raises
        ------
        ValueError
            A row lacks the date field or a group key.
        """
        rows = list(inputs["records"])
        names = sorted(self.params["families"])
        fams = self._compile(names)
        flags = self._date_flags(rows, names, fams)
        keys = sorted(flags)
        dates = [self._date_record(k, flags[k], names) for k in keys]
        admission = self._admission(keys, flags, names)
        if admission:
            self._mark_complete(dates, keys, flags, admission)
        return {
            "dates": dates,
            "admission": list(admission.values()),
            "summary": [self._summary(n, fams[n], rows, keys, flags, i) for i, n in enumerate(names)],
            "combinations": self._combinations(names, [flags[k] for k in keys]),
            "cohort": [self._cohort(keys, len(rows))],
        }

    def _compile(self, names):
        p = self.params
        schema = set(p["schema_fields"])
        out = {}
        for n in names:
            spec = p["families"][n]
            if "flag" in spec:
                out[n] = _FlagFamily(spec["flag"])
                continue
            out[n] = _Family(
                spec["fields"], spec.get("require") or (), spec.get("max_age_days"),
                p.get("missing_suffix"), p.get("age_suffix"), schema,
            )  # fmt: skip
        return out

    def _group_fields(self):
        return tuple(self.params.get("group_keys") or ())

    def _key_of(self, row):
        """Return the ``(group values..., date)`` key of a row; refuse a row lacking one."""
        parts = []
        for f in (*self._group_fields(), self.params["date_field"]):
            v = _field(row, f)
            if v is _MISSING:
                raise ValueError(f"{self.key}: a row has no {f!r} field")
            parts.append(v)
        return tuple(parts)

    def _date_flags(self, rows, names, fams):
        flags = {}
        for row in rows:
            cur = flags.setdefault(self._key_of(row), [True] * len(names))
            for i, n in enumerate(names):
                cur[i] = cur[i] and fams[n].satisfied(row)
        return flags

    def _date_record(self, key, bits, names):
        fields = (*self._group_fields(), self.params["date_field"])
        rec = dict(zip(fields, key))
        for n, bit in zip(names, bits):
            rec[FLAG_PREFIX + n] = YES if bit else NO
        return rec

    def _summary(self, name, fam, rows, keys, flags, idx):
        yes = [k[-1] for k in keys if flags[k][idx]]
        present = sum(fam.present(r) for r in rows)
        spec = self.params["families"][name]
        return {
            "family": name,
            "sources": list(spec.get("sources") or []),
            "rows": len(rows),
            "present_rows": present,
            "finite_rows": sum(fam.finite(r) for r in rows),
            "missing_rows": len(rows) - present,
            "dates_yes": len(yes),
            "dates_no": len(keys) - len(yes),
            "first_date": min(yes) if yes else None,
            "last_date": max(yes) if yes else None,
        }

    def _admit_rate(self):
        """Return the admission floor, or None when no admission was asked for."""
        admit = self.params.get("admit")
        return None if admit is None else admit["min_rate"]

    def _admission(self, keys, flags, names):
        """Per group: distinct dates, rate_/required_ per family and the admitted names."""
        floor = self._admit_rate()
        if floor is None:
            return {}
        width = len(self._group_fields())
        tallies = {}
        for key in keys:
            tally = tallies.setdefault(key[:width], [0, [0] * len(names)])
            tally[0] += 1
            tally[1][:] = [t + bool(b) for t, b in zip(tally[1], flags[key])]
        return {g: self._admission_record(g, t, names, floor) for g, t in tallies.items()}

    def _admission_record(self, group, tally, names, floor):
        total, counts = tally
        rec = dict(zip(self._group_fields(), group))
        rec[self.params.get("dates_count_field") or DEFAULT_DATES_FIELD] = total
        rates = [c / total for c in counts]
        for n, rate in zip(names, rates):
            rec[RATE_PREFIX + n] = rate
        for n, rate in zip(names, rates):
            rec[REQUIRED_PREFIX + n] = 1 if rate >= floor else 0
        rec[ADMITTED_FIELD] = [n for n, rate in zip(names, rates) if rate >= floor]
        return rec

    def _mark_complete(self, dates, keys, flags, admission):
        """Add ``complete`` to each date record: every admitted family is available."""
        width = len(self._group_fields())
        order = {n: i for i, n in enumerate(sorted(self.params["families"]))}
        for rec, key in zip(dates, keys):
            admitted = admission[key[:width]][ADMITTED_FIELD]
            ok = all(flags[key][order[n]] for n in admitted)
            rec[COMPLETE_FIELD] = YES if ok else NO

    def _cohort(self, keys, n_rows):
        days = [k[-1] for k in keys]
        return {
            "dates": len(keys),
            "rows": n_rows,
            "first_date": min(days) if days else None,
            "last_date": max(days) if days else None,
        }

    def _combinations(self, names, patterns):
        """Count dates per family subset by a superset sum over one flat array."""
        n = len(names)
        counts = [0] * (1 << n)
        for bits in patterns:
            counts[sum(1 << i for i, b in enumerate(bits) if b)] += 1
        for i in range(n):
            step = 1 << i
            for m in range(len(counts)):
                if not m & step:
                    counts[m] += counts[m | step]
        emit = (self.params.get("combinations") or {}).get("emit", DEFAULT_EMIT)
        out = []
        for m, total in enumerate(counts):
            if emit == DEFAULT_EMIT or total > 0 or m == 0:
                combo = [names[i] for i in range(n) if m >> i & 1]
                out.append({"combination": combo, "size": len(combo), "dates": total})
        return out


#: The kinds this module ships.
_KINDS = (("family-availability", FamilyAvailability),)


def register(registry=None):
    """Register ``family-availability`` into ``registry``, ``owned=False``.

    Idempotent by SKIPPING a name already present, matching
    :func:`dskit.pipeline.kinds_table.register`.

    Parameters
    ----------
    registry : NodeKindRegistry, optional
        Target; the default registry when omitted.

    Returns
    -------
    NodeKindRegistry
        The registry registered into.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry
