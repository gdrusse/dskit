"""The ``ObservationTables`` transform: keyed stores attached onto a record stream (ADR-0226 amendment).

A feature family whose data is a table keyed by (entity, date) in an onboarded
store should be added by configuration, not code. This pack reads each such
table through the existing read seam (:class:`~dskit.pipeline.libs.observations.ObservationRows`,
so dedup, the vintage bound and the store token stay its one implementation)
and writes the declared columns onto the rows of an incoming stream by the
declared ``key``. The downstream ``attach-by-identity`` kind is unchanged; this
node only makes the stream it attaches from carry the extra columns.

A table matches EXACTLY on ``key`` unless it declares ``max_age_days``: then the
LAST key field is an ISO date and the match is the latest table row of the same
other-key values at or before (``strict_prior`` false) or strictly before
(default) the stream row's date, at most ``max_age_days`` old. An as-of table
also emits, per column, an age and a missing companion; nothing is imputed.

A table whose rows live in several onboarded sources (one per entity universe,
say) declares ``parts`` in place of ``source``/``stream``: each part is read
through the same seam, the rows are concatenated, a key held by two parts is
refused, and the provenance lists every part's row count and content digest
(ADR-0236). Nothing is copied into a union source.

Why tier 2: it names the onboarding read seam only inside ``run()``, as the
``observations`` pack does. An empty ``tables`` map is a lawful pass-through, so
a document can keep this node whether or not any keyed family is declared.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from datetime import date

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node, check_int_param, reject_unknown_params

__all__ = ["AGE_SUFFIX", "MISSING_SUFFIX", "NODE_KINDS", "ObservationTables", "iso_day", "register"]

#: The ONE spelling of the as-of companions' suffixes, read by validation and run alike.
AGE_SUFFIX = "_age_days"
MISSING_SUFFIX = "_missing"

_TABLE_KEYS = ("root", "source", "stream", "parts", "key_fields", "columns", "max_age_days",
               "strict_prior")
_PART_KEYS = ("root", "source", "stream")
_MATCH_NOTE = "exact key match"


def _names_ok(value):
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v for v in value)


def iso_day(value, where):
    """Return the ISO date a key cell holds, or raise naming ``where``."""
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as err:
        raise ValueError(f"{where}: {value!r} is not an ISO date") from err


class ObservationTables(Node):
    """Attach the declared columns of keyed onboarded tables onto a record stream.

    Role ``transform``; input ``records``; outputs ``records`` (every input row
    in order, with each table's columns, null where no table row matches) and
    ``provenance`` (per table its rows, content digest, matched and unmatched
    counts).

    Parameters
    ----------
    params : dict
        ``root`` (str, the default store root; a table may name its own), ``key`` (non-empty list of stream field names
        every input row holds, required), ``tables`` (dict, required, may be
        empty: ``{name: {"source", "stream", "root" (default the node's), "columns": {stream column ->
        output field}, "key_fields" (stream fields matching ``key`` in order,
        default ``key``), "max_age_days" (int >= 0, as-of match), "strict_prior"
        (bool, default true)}}``; in place of ``source``/``stream`` a table may
        declare ``parts``, two or more distinct ``{"source", "stream", "root"
        (default the table's, then the node's)}`` read and concatenated, no key
        in two parts), ``age_suffix`` and ``missing_suffix`` (str, default
        ``AGE_SUFFIX`` / ``MISSING_SUFFIX``).

    Examples
    --------
    Attach a one-column daily table, as-of within 5 days::

        node = ObservationTables("extras", {
            "root": "./ob", "key": ["symbol", "date"],
            "tables": {"fam": {"source": "src", "stream": "daily",
                               "columns": {"px": "fam_px"}, "max_age_days": 5}}})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0] carries fam_px, fam_px_age_days, fam_px_missing
    """

    role = "transform"
    outputs = ("records", "provenance")

    _PARAMS = ("root", "key", "tables", "age_suffix", "missing_suffix")

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block, possibly carrying ``$`` references.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in ("key", "tables"):
            if name not in params:
                problems.append(f"{name} is required")
        for name in ("root", "age_suffix", "missing_suffix"):
            value = params.get(name, "x")
            if not is_node_ref(value) and not (isinstance(value, str) and value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        key = params.get("key")
        if "key" in params and not is_node_ref(key) and not _names_ok(key):
            problems.append(f"key must be a non-empty list of names, got {key!r}")
        tables = params.get("tables")
        if "tables" in params and not is_node_ref(tables):
            if not isinstance(tables, dict):
                problems.append(f"tables must be a dict, got {tables!r}")
            else:
                for name, spec in tables.items():
                    problems += cls._table_problems(name, spec, key, params.get("root"))
        return problems

    @classmethod
    def _table_problems(cls, name, spec, key, root):
        """Problems with one table's declaration."""
        where = f"tables.{name}"
        if not isinstance(spec, dict):
            return [f"{where} must be an object, got {spec!r}"]
        problems = []
        reject_unknown_params(problems, spec, _TABLE_KEYS)
        if "parts" in spec:
            problems += cls._parts_problems(where, spec, root)
        else:
            for field in ("source", "stream"):
                if not isinstance(spec.get(field), str) or not spec.get(field):
                    problems.append(f"{where}.{field} is required: a non-empty string")
            if not spec.get("root") and not root:
                problems.append(f"{where}.root is required when the node declares no root")
        columns = spec.get("columns")
        if not isinstance(columns, dict) or not columns or not all(
                isinstance(k, str) and isinstance(v, str) and v for k, v in columns.items()):
            problems.append(f"{where}.columns must map stream columns to output fields")
        elif len(set(columns.values())) < len(columns):
            problems.append(f"{where}.columns maps two stream columns to one output field")
        fields = spec.get("key_fields", key)
        if not is_node_ref(fields) and not _names_ok(fields):
            problems.append(f"{where}.key_fields must be a non-empty list of names")
        elif _names_ok(fields) and _names_ok(key) and len(fields) != len(key):
            problems.append(f"{where}.key_fields must have one name per key field")
        if "max_age_days" in spec:
            check_int_param(problems, f"{where}.max_age_days", spec["max_age_days"], ge=0)
        if "strict_prior" in spec and not isinstance(spec["strict_prior"], bool):
            problems.append(f"{where}.strict_prior must be a boolean")
        return problems

    @staticmethod
    def _parts_problems(where, spec, root):
        """Problems with a ``parts`` declaration: two or more distinct, rooted sources."""
        parts = spec["parts"]
        problems = [f"{where}.{k} cannot sit beside parts; each part names its own"
                    for k in ("source", "stream") if k in spec]
        if not isinstance(parts, list) or len(parts) < 2:
            return problems + [f"{where}.parts must list two or more sources, got {parts!r}"]
        seen = set()
        for i, part in enumerate(parts):
            at = f"{where}.parts[{i}]"
            if not isinstance(part, dict):
                problems.append(f"{at} must be an object, got {part!r}")
                continue
            problems += [f"{at}: unknown key {k!r}" for k in part if k not in _PART_KEYS]
            problems += [f"{at}.{k} is required: a non-empty string" for k in ("source", "stream")
                         if not isinstance(part.get(k), str) or not part.get(k)]
            if "root" in part and not (isinstance(part["root"], str) and part["root"]):
                problems.append(f"{at}.root must be a non-empty string")
            if not part.get("root") and not spec.get("root") and not root:
                problems.append(f"{at}.root is required when neither the table nor the node "
                                "declares one")
            identity = tuple(str(part.get(k)) for k in _PART_KEYS)
            if identity in seen:
                problems.append(f"{at} repeats an earlier part")
            seen.add(identity)
        return problems

    # -- reading --------------------------------------------------------------

    def _read_part(self, ctx, spec, part):
        """Return (rows, fingerprint) of one source through the observations seam."""
        from dskit.pipeline.libs.observations import ObservationRows

        reader = ObservationRows("reader", {
            "root": part.get("root", spec.get("root", self.params.get("root"))),
            "source": part["source"], "stream": part["stream"],
            "key_fields": list(spec.get("key_fields", self.params["key"]))})
        return reader.run(ctx, {})["records"], reader.fingerprint()

    def _read(self, ctx, name, spec):
        """Return (rows, fingerprint) of one table: its source, or its parts concatenated.

        A parts fingerprint lists every part's source, stream, rows and digest
        and digests that list; a key two parts both hold is refused.
        """
        if "parts" not in spec:
            return self._read_part(ctx, spec, spec)
        fields = spec.get("key_fields", self.params["key"])
        rows, prints, owner = [], [], {}
        for index, part in enumerate(spec["parts"]):
            part_rows, finger = self._read_part(ctx, spec, part)
            for row in part_rows:
                key = tuple(row[f] for f in fields)
                if owner.setdefault(key, index) != index:
                    raise ValueError(f"{self.key}.{name}: parts {owner[key]} and {index} both "
                                     f"hold key {key}")
            rows.extend(part_rows)
            prints.append({"source": part["source"], "stream": part["stream"],
                           "rows": finger["rows"], "sha256": finger["sha256"]})
        digest = hashlib.sha256(json.dumps(prints, sort_keys=True).encode()).hexdigest()
        return rows, {"rows": len(rows), "sha256": digest, "parts": prints}

    # -- matching -------------------------------------------------------------

    def _exact_finder(self, rows, spec):
        """Return ``find(key_tuple) -> (row or None, age)`` matching the key exactly."""
        fields = spec.get("key_fields", self.params["key"])
        index = {tuple(r[f] for f in fields): r for r in rows}
        return lambda values: (index.get(tuple(values)), None)

    def _asof_finder(self, name, rows, spec):
        """Return ``find(key_tuple) -> (row or None, age_days)`` as-of on the last key field."""
        fields = spec.get("key_fields", self.params["key"])
        strict = spec.get("strict_prior", True)
        limit = spec["max_age_days"]
        groups = {}
        for row in rows:
            groups.setdefault(tuple(row[f] for f in fields[:-1]), []).append(
                (iso_day(row[fields[-1]], f"{self.key}.{name}"), row))
        for entries in groups.values():
            entries.sort(key=lambda pair: pair[0])
        days = {group: [d for d, _ in entries] for group, entries in groups.items()}

        def find(values):
            group = groups.get(tuple(values[:-1]))
            if not group:
                return None, None
            at = iso_day(values[-1], f"{self.key}.{name} stream row")
            cut = (bisect_left if strict else bisect_right)(days[tuple(values[:-1])], at)
            if cut == 0:
                return None, None
            found, row = group[cut - 1]
            age = (at - found).days
            return (row, age) if age <= limit else (None, None)

        return find

    def _finder(self, name, rows, spec):
        """Pick the exact or as-of matcher by the table's declaration."""
        if "max_age_days" in spec:
            return self._asof_finder(name, rows, spec)
        return self._exact_finder(rows, spec)

    # -- attaching ------------------------------------------------------------

    def _outputs_of(self, name, spec):
        """Return the output fields a table adds, companions included."""
        age = self.params.get("age_suffix", AGE_SUFFIX)
        miss = self.params.get("missing_suffix", MISSING_SUFFIX)
        fields = list(spec["columns"].values())
        if "max_age_days" in spec:
            fields += [f + age for f in spec["columns"].values()]
            fields += [f + miss for f in spec["columns"].values()]
        return fields

    def _attach_one(self, ctx, name, spec, out):
        """Write one table's columns onto the output rows; return its provenance."""
        rows, finger = self._read(ctx, name, spec)
        find = self._finder(name, rows, spec)
        asof = "max_age_days" in spec
        age_s = self.params.get("age_suffix", AGE_SUFFIX)
        miss_s = self.params.get("missing_suffix", MISSING_SUFFIX)
        matched = 0
        for record in out:
            hit, age = find([record[k] for k in self.params["key"]])
            matched += hit is not None
            for source_col, field in spec["columns"].items():
                value = hit.get(source_col) if hit is not None else None
                record[field] = value
                if asof:
                    record[field + age_s] = age if value is not None else None
                    record[field + miss_s] = 0 if value is not None else 1
        report = {"rows": finger["rows"], "sha256": finger["sha256"], "matched": matched,
                  "unmatched": len(out) - matched,
                  "match": f"as-of within {spec['max_age_days']} days" if asof else _MATCH_NOTE}
        if "parts" in finger:
            report["parts"] = finger["parts"]
        return report

    def _check_collisions(self, base):
        """Refuse an output field that is already on the stream or declared twice."""
        seen = set(base[0]) if base else set()
        for name, spec in self.params["tables"].items():
            for field in self._outputs_of(name, spec):
                if field in seen:
                    raise ValueError(f"{self.key}: table {name!r} would write {field!r}, which already exists")
                seen.add(field)

    def run(self, ctx, inputs):
        """Attach every declared table's columns to the input rows.

        Parameters
        ----------
        ctx : NodeContext
            The run frame, passed on to the store readers.
        inputs : dict
            ``records``: the stream, a list of mapping rows holding every ``key`` field.

        Returns
        -------
        dict
            ``{"records": rows with the columns, "provenance": {"tables": ...}}``.

        Raises
        ------
        ValueError
            A row lacks a ``key`` field, or an output field collides.
        """
        base = inputs["records"]
        missing = [k for k in self.params["key"] if base and k not in base[0]]
        if missing:
            raise ValueError(f"{self.key}: input rows lack key field(s) {missing}")
        self._check_collisions(base)
        out = [dict(row) for row in base]
        report = {name: self._attach_one(ctx, name, spec, out)
                  for name, spec in self.params["tables"].items()}
        self.log.info("attached %d keyed table(s) to %d row(s)", len(report), len(out))
        return {"records": out, "provenance": {"tables": report}}


#: The pack's kinds.
NODE_KINDS = (("observation-tables", ObservationTables),)


def register(registry=None) -> None:
    """Claim the pack's kind names in ``registry`` (default the toolkit's).

    Parameters
    ----------
    registry : NodeKindRegistry or None
        Where to register; ``None`` means the toolkit default. Idempotent.

    Returns
    -------
    None
        Registration is the effect.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls)
