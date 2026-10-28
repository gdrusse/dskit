"""ComputeFields: per-row arithmetic over declared fields — by import path (ADR-0265 Amendment 5).

``derive`` projects a field from CASES; nothing in the record-flow family adds a number that
is the ratio, difference or log of two others, so a study that sorts names by ``rv - iv`` or by
``log(straddle / expected move)`` had to do it in child code. :class:`ComputeFields` is that
missing verb: a document writes each output as a small expression tree and the node evaluates
it on every row.

An expression is exactly one of ``{"field": name}`` (the row's value, any JSON type when it is
the whole expression, so a bare field is a copy and a rename), ``{"const": number}``, or
``{"op": name, "args": [expression, ...]}`` over the closed table :data:`OPS`. ``{"item": {"field": name, "index": i}}``
is the i-th number of a list field, and ``{"coalesce": [expression, ...]}`` is the first of its expressions that has a value (a
null or out-of-domain one is skipped; none left is null, counted). A bare ``field`` whose value is NaN or infinite is missing. Two kinds may only be the
WHOLE expression of an output: ``{"text": "..."}`` (a constant string, such as a structure name),
``{"integer": n}`` (a constant int, such as a window length: ``const`` computes in floats, so a count it
writes would read ``252.0``) and ``{"list": [expression, ...]}`` (a list of numbers, such as the strikes
of an order; null when any element is). Inside an op
every operand must be a finite real number; a missing key, ``None``, NaN, infinity, boolean or
text operand makes the result ``None`` and is counted as ``null_input``; an operation outside
its domain (division by zero, log or square root of a non-positive, an overflow) makes it
``None`` and is counted as ``domain``. Nothing is filled, clipped or guessed.

``records`` are copies of the input rows with the new fields added in declared order; a field
the row already carries refuses unless ``overwrite`` is declared. ``summary`` counts the rows
and, per output field, how many values were computed, null or out of domain.

Import cost: stdlib and the pipeline core only. Wired by import path
(``dskit.pipeline.kinds_compute:ComputeFields``); :data:`NODE_KINDS` is empty and nothing registers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from dskit.pipeline.document import is_node_ref
from dskit.pipeline.node import Node, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["NODE_KINDS", "OPS", "ComputeFields"]

NODE_KINDS = ()


def _div(a, b):
    return a / b


def _log(a):
    return math.log(a)


def _sqrt(a):
    return math.sqrt(a)


#: The closed operator table: name -> (arity rule, function). An arity rule is ``("exactly", n)`` or
#: ``("at_least", n)``; the function raises ``ValueError`` / ``ZeroDivisionError`` / ``OverflowError``
#: outside its domain, which the node reports as a ``domain`` count.
OPS = {
    "add": (("at_least", 2), lambda *a: math.fsum(a)),
    "sub": (("exactly", 2), lambda a, b: a - b),
    "mul": (("at_least", 2), lambda *a: math.prod(a)),
    "div": (("exactly", 2), _div),
    "neg": (("exactly", 1), lambda a: -a),
    "abs": (("exactly", 1), abs),
    "log": (("exactly", 1), _log),
    "exp": (("exactly", 1), math.exp),
    "sqrt": (("exactly", 1), _sqrt),
    "min": (("at_least", 2), lambda *a: min(a)),
    "max": (("at_least", 2), lambda *a: max(a)),
}
_KINDS = ("field", "const", "op", "text", "integer", "list", "item", "coalesce")
_NULL = "null_input"
_DOMAIN = "domain"


class _Fail(Exception):
    """An expression has no value on this row; ``reason`` is :data:`_NULL` or :data:`_DOMAIN`."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ComputeFields(Node):
    """Add computed fields to every row from declared expression trees — by import path.

    Role ``transform``. See the module docstring for the expression grammar, the null rule and
    the outputs.

    Parameters
    ----------
    params : dict
        ``fields`` (non-empty ``{output name: expression}``, REQUIRED); ``overwrite`` (bool,
        default false: a row already carrying an output name refuses); ``keep`` (non-empty list of
        distinct field names: each output row then holds exactly those fields, in that order, then
        the computed ones, so the node also projects a table to the columns a keyed attachment
        demands; a row lacking a kept field refuses).

    Examples
    --------
    The log of a ratio, and a rename::

        node = ComputeFields("calc", {"fields": {
            "richness": {"op": "log", "args": [{"op": "div", "args": [
                {"field": "straddle"}, {"field": "expected"}]}]},
            "date": {"field": "entry_date"}}})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0]["richness"], out["summary"]["fields"]["richness"]
    """

    role = "transform"
    outputs = ("records", "summary")
    _PARAMS = ("fields", "overwrite", "keep")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the kind for serving: ``"pure"`` — a computed field is a function of its own row."""
        return "pure"

    @classmethod
    def _expression_problems(cls, where, expr, nested=False):
        """Problems with one expression tree, empty when it is lawful."""
        if not isinstance(expr, dict):
            return [f"{where}: an expression is a dict, got {expr!r}"]
        problems = []
        kinds = [k for k in _KINDS if k in expr]
        if len(kinds) != 1:
            return [f"{where}: an expression has exactly one of {list(_KINDS)}, got {sorted(expr)}"]
        kind = kinds[0]
        allowed = {"op": {"op", "args"}}.get(kind, {kind})
        if kind in ("text", "integer", "list") and nested:
            return [f"{where}: {kind} is only allowed as the whole expression of an output"]
        problems += [f"{where}: unknown key {k!r}" for k in sorted(set(expr) - allowed)]
        if kind == "field":
            if not isinstance(expr["field"], str) or not expr["field"]:
                problems.append(f"{where}: field must be a non-empty string, got {expr['field']!r}")
        elif kind == "const":
            if isinstance(expr["const"], bool) or not number_ok(expr["const"]):
                problems.append(f"{where}: const must be a finite number, got {expr['const']!r}")
        elif kind == "item":
            spec = expr["item"]
            ok = (isinstance(spec, dict) and set(spec) == {"field", "index"} and isinstance(spec["field"], str)
                  and spec["field"] and isinstance(spec["index"], int) and not isinstance(spec["index"], bool)
                  and spec["index"] >= 0)
            if not ok:
                problems.append(f"{where}: item is {{\"field\": name, \"index\": int >= 0}}, got {spec!r}")
        elif kind == "coalesce":
            items = expr["coalesce"]
            if not isinstance(items, list) or len(items) < 2:
                problems.append(f"{where}: coalesce is a list of at least 2 expressions, got {items!r}")
            else:
                for position, item in enumerate(items):
                    problems += cls._expression_problems(f"{where}.coalesce[{position}]", item, nested)
        elif kind == "text":
            if not isinstance(expr["text"], str) or not expr["text"]:
                problems.append(f"{where}: text must be a non-empty string, got {expr['text']!r}")
        elif kind == "integer":
            if not isinstance(expr["integer"], int) or isinstance(expr["integer"], bool):
                problems.append(f"{where}: integer must be an int, got {expr['integer']!r}")
        elif kind == "list":
            items = expr["list"]
            if not isinstance(items, list) or not items:
                problems.append(f"{where}: list must be a non-empty list of expressions, got {items!r}")
            else:
                for position, item in enumerate(items):
                    problems += cls._expression_problems(f"{where}.list[{position}]", item, nested=True)
        else:
            problems += cls._op_problems(where, expr)
        return problems

    @classmethod
    def _op_problems(cls, where, expr):
        """Problems with an ``op`` expression: its name, its arity and each operand."""
        name, args = expr["op"], expr.get("args")
        if name not in OPS:
            return [f"{where}: unknown op {name!r} — the table is {sorted(OPS)}"]
        if not isinstance(args, list):
            return [f"{where}: args must be a list of expressions, got {args!r}"]
        rule, count = OPS[name][0]
        if (rule == "exactly" and len(args) != count) or (rule == "at_least" and len(args) < count):
            return [f"{where}: op {name!r} takes {rule.replace('_', ' ')} {count} operand(s), "
                    f"got {len(args)}"]
        problems = []
        for position, arg in enumerate(args):
            problems += cls._expression_problems(f"{where}.args[{position}]", arg, nested=True)
        return problems

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        fields = params.get("fields")
        if is_node_ref(fields):
            pass
        elif not isinstance(fields, dict) or not fields:
            problems.append(f"fields must be a non-empty dict of output name -> expression, got {fields!r}")
        else:
            for name, expr in fields.items():
                if not isinstance(name, str) or not name:
                    problems.append(f"fields: an output name is a non-empty string, got {name!r}")
                else:
                    problems += cls._expression_problems(f"fields[{name!r}]", expr)
        overwrite = params.get("overwrite", False)
        if not is_node_ref(overwrite) and not isinstance(overwrite, bool):
            problems.append(f"overwrite must be a boolean, got {overwrite!r}")
        keep = params.get("keep")
        if "keep" in params and not is_node_ref(keep) and (
                not isinstance(keep, list) or not keep or not all(isinstance(k, str) and k for k in keep)
                or len(set(keep)) < len(keep)):
            problems.append(f"keep must be a non-empty list of distinct field names, got {keep!r}")
        return problems

    @staticmethod
    def _evaluate(expr, row, top):
        """Return the value of ``expr`` on ``row``; raises :class:`_Fail` when it has none.

        A bare ``field`` that is the whole expression (``top``) passes any non-null value through;
        a field inside an op must be a finite real number.
        """
        if "const" in expr:
            return float(expr["const"])
        if "text" in expr:
            return expr["text"]
        if "integer" in expr:
            return expr["integer"]
        if "coalesce" in expr:
            last = _Fail(_NULL)
            for item in expr["coalesce"]:
                try:
                    return ComputeFields._evaluate(item, row, top)
                except _Fail as failure:
                    last = failure
            raise last
        if "item" in expr:
            items = row.get(expr["item"]["field"])
            index = expr["item"]["index"]
            if not isinstance(items, (list, tuple)) or index >= len(items):
                raise _Fail(_NULL)
            value = items[index]
            if isinstance(value, bool) or not number_ok(value):
                raise _Fail(_NULL)
            return float(value)
        if "list" in expr:
            return [ComputeFields._evaluate(item, row, False) for item in expr["list"]]
        if "field" in expr:
            value = row.get(expr["field"])
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                raise _Fail(_NULL)
            if top:
                return value
            if isinstance(value, bool) or not number_ok(value):
                raise _Fail(_NULL)
            return float(value)
        args = [ComputeFields._evaluate(arg, row, False) for arg in expr["args"]]
        try:
            result = OPS[expr["op"]][1](*args)
        except (ValueError, ZeroDivisionError, OverflowError):
            raise _Fail(_DOMAIN) from None
        if not math.isfinite(result):
            raise _Fail(_DOMAIN)
        return result

    def run(self, ctx, inputs):
        """Evaluate every declared expression on every row.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            ``records`` (REQUIRED): mapping rows.

        Returns
        -------
        dict
            ``records`` (copies with the computed fields) and ``summary`` (``rows_in`` and, per
            field, ``computed`` / ``null_input`` / ``domain``).

        Raises
        ------
        ValueError
            A row that is not a mapping, or that already carries an output field without ``overwrite``.
        """
        fields, overwrite = self.params["fields"], bool(self.params.get("overwrite", False))
        keep = self.params.get("keep")
        counts = {name: {"computed": 0, _NULL: 0, _DOMAIN: 0} for name in fields}
        records = []
        for index, row in enumerate(inputs["records"]):
            if not isinstance(row, Mapping):
                raise ValueError(f"{self.key}: row {index} must be a mapping, got {type(row).__name__}")
            if keep is not None:
                lacking = [k for k in keep if k not in row]
                if lacking:
                    raise ValueError(f"{self.key}: row {index} lacks the keep field(s) {lacking}")
                out = {k: row[k] for k in keep}
            else:
                out = dict(row)
            clash = [name for name in fields if name in out]
            if clash and not overwrite:
                raise ValueError(f"{self.key}: row {index} already carries {clash}: declare overwrite "
                                 "to replace a field")
            for name, expr in fields.items():
                try:
                    out[name] = self._evaluate(expr, row, True)
                    counts[name]["computed"] += 1
                except _Fail as failure:
                    out[name] = None
                    counts[name][failure.reason] += 1
            records.append(out)
        self.log.info("%s: %d row(s), %d field(s)", self.key, len(records), len(fields))
        return {"records": records, "summary": {
            "stage": "compute " + ", ".join(fields), "rows_in": len(records), "fields": counts}}
