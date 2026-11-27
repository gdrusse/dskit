"""Check the declarations of one parameterized optimization model.

The grammar in ADR-0329 records symbols, dependencies and index scopes. Formula
text stays opaque so the same declaration can describe a linear or nonlinear
formulation without becoming an executable language.
"""

import argparse
from collections import deque
from decimal import Decimal
import math
import re
import sys

from .base import ConfigError
from .document import _load_strict_json

__all__ = ["validate_formulation"]

_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MISSING = object()
_DECLARATION_FIELDS = {
    "sets": ("id", "indices", "definition"),
    "given": ("id", "axes", "domain", "units", "definition"),
    "decisions": ("id", "axes", "domain", "units", "lower", "upper", "definition"),
    "derived": ("id", "axes", "units", "expression"),
    "constraints": ("id", "forall", "lhs", "relation", "rhs", "definition"),
}
_ROOT_FIELDS = ("schema_version", "purpose", *_DECLARATION_FIELDS, "objective")
_REFERENCE_KINDS = {"sets", "given", "decisions", "derived"}
_DOMAINS = ("real", "integer", "binary")
_RELATIONS = ("<=", "=", ">=", "<", ">", "in")


def _parse_integer(token):
    """Convert an exact signed JSON integer without changing global limits."""
    return int(Decimal(token))


def _finite_number(value):
    """Check a JSON number without coercing an arbitrary-size integer to float."""
    return type(value) is int or (type(value) is float and math.isfinite(value))


class _Validator:
    """Collect shape and reference problems for one declaration."""

    def __init__(self):
        self.errors = []
        self.symbols = {}

    def _object(self, obj, path, fields):
        """Report bad object type and missing or unknown fields."""
        if obj is _MISSING:
            return False
        if not isinstance(obj, dict):
            self.errors.append(f"{path}: expected an object")
            return False
        for field in fields:
            if field not in obj:
                self.errors.append(f"{path}.{field}: is required")
        for field in obj:
            if field not in fields:
                self.errors.append(f"{path}.{field}: unknown field")
        return True

    def _array(self, value, path, *, nonempty=False):
        """Return an array to inspect or an empty sequence after a type error."""
        if value is _MISSING:
            return ()
        if not isinstance(value, list):
            self.errors.append(f"{path}: expected an array")
            return ()
        if nonempty and not value:
            self.errors.append(f"{path}: expected at least one decision")
        return value

    def _text(self, value, path):
        """Require a nonblank string when present."""
        if value is not _MISSING and (not isinstance(value, str) or not value.strip()):
            self.errors.append(f"{path}: expected nonblank text")

    def _enum(self, value, path, choices):
        """Report values outside a declared vocabulary."""
        if value is not _MISSING and (not isinstance(value, str) or value not in choices):
            self.errors.append(f"{path}: expected one of {', '.join(choices)}")

    def _identifier(self, value, path):
        """Return a valid identifier, leaving missing-field errors to the object check."""
        if value is _MISSING:
            return None
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            self.errors.append(f"{path}: expected an ASCII identifier")
            return None
        return value

    def _names(self, value, path):
        """Read distinct valid names with their element locations."""
        seen = set()
        for index, item in enumerate(self._array(value, path)):
            location = f"{path}[{index}]"
            name = self._identifier(item, location)
            if name is None:
                continue
            if name in seen:
                self.errors.append(f"{location}: duplicate identifier {name!r}")
                continue
            seen.add(name)
            yield name, location

    def _declare(self, name, path, kind):
        """Reserve a name once within the global namespace."""
        if name is None:
            return None
        if name in self.symbols:
            self.errors.append(f"{path}: duplicate declaration of {name!r}")
            return None
        self.symbols[name] = kind
        return name

    def _register(self, obj):
        """Read declaration shapes and index all IDs before checking references."""
        declarations = {}
        for section, fields in _DECLARATION_FIELDS.items():
            path = f"$.{section}"
            declarations[section] = records = []
            entries = self._array(obj.get(section, _MISSING), path, nonempty=section == "decisions")
            for index, entry in enumerate(entries):
                location = f"{path}[{index}]"
                if not self._object(entry, location, fields):
                    continue
                id_path = f"{location}.id"
                name = self._identifier(entry.get("id", _MISSING), id_path)
                name = self._declare(name, id_path, section)
                records.append((entry, location, name))
                if section == "sets":
                    aliases = self._names(entry.get("indices", _MISSING), f"{location}.indices")
                    for alias, alias_path in aliases:
                        self._declare(alias, alias_path, "indices")
        return declarations

    def _indices(self, value, path, outer=()):
        """Read declared index IDs and refuse locally bound names that shadow outer ones."""
        indices = set()
        for name, location in self._names(value, path):
            if self.symbols.get(name) != "indices":
                self.errors.append(f"{location}: unknown index ID {name!r}")
            elif name in outer:
                self.errors.append(f"{location}: local index {name!r} shadows an outer index")
            else:
                indices.add(name)
        return indices

    def _expression(self, obj, path, outer=()):
        """Check expression metadata and keep dependencies for the fixed-bound pass."""
        if not self._object(obj, path, ("text", "refs", "over")):
            return []
        self._text(obj.get("text", _MISSING), f"{path}.text")
        self._indices(obj.get("over", _MISSING), f"{path}.over", outer)
        dependencies = []
        for name, location in self._names(obj.get("refs", _MISSING), f"{path}.refs"):
            kind = self.symbols.get(name)
            if kind is None:
                self.errors.append(f"{location}: unknown reference {name!r}")
            elif kind not in _REFERENCE_KINDS:
                self.errors.append(f"{location}: reference to {name!r} is forbidden in an expression")
            else:
                dependencies.append((name, location))
        return dependencies

    def _bounds(self, entry, path, axes):
        """Check numeric, open and expression bounds and return dependencies."""
        lower = entry.get("lower", _MISSING)
        upper = entry.get("upper", _MISSING)
        dependencies = []
        for value, side in ((lower, "lower"), (upper, "upper")):
            location = f"{path}.{side}"
            if isinstance(value, dict):
                dependencies.extend(self._expression(value, location, axes))
            elif value is not _MISSING and value is not None and not _finite_number(value):
                self.errors.append(f"{location}: expected a finite number, null, or an expression")
        if _finite_number(lower) and _finite_number(upper) and lower > upper:
            self.errors.append(f"{path}.lower: must be no greater than {path}.upper")
        return dependencies

    def _derived_dependencies(self, definitions):
        """Find cyclic dependencies and propagate decision dependence through reverse edges."""
        dependents = {name: [] for name in definitions}
        remaining = dict.fromkeys(definitions, 0)
        dependent = set()
        for name, (_, refs) in definitions.items():
            for ref, _ in refs:
                if ref in definitions:
                    dependents[ref].append(name)
                    remaining[name] += 1
                elif self.symbols[ref] == "decisions":
                    dependent.add(name)

        # Peel definitions whose derived inputs have all been checked.
        ready = deque(name for name, count in remaining.items() if not count)
        while ready:
            for name in dependents[ready.popleft()]:
                remaining[name] -= 1
                if not remaining[name]:
                    ready.append(name)
        for name, count in remaining.items():
            if count:
                path, _ = definitions[name]
                self.errors.append(f"{path}.refs: derived dependency reaches a cycle")

        # Traverse independently so a decision dependency remains visible
        # even if it also passes through a malformed cyclic subgraph.
        ready = deque(dependent)
        while ready:
            for name in dependents[ready.popleft()]:
                if name not in dependent:
                    dependent.add(name)
                    ready.append(name)
        return dependent

    def _validate(self, obj):
        """Inspect the declaration in structural, scope and dependency passes."""
        if not self._object(obj, "$", _ROOT_FIELDS):
            return
        version = obj.get("schema_version", _MISSING)
        if version is not _MISSING and (type(version) is not int or version != _VERSION):
            self.errors.append(f"$.schema_version: expected integer {_VERSION}")
        self._text(obj.get("purpose", _MISSING), "$.purpose")

        declarations = self._register(obj)
        for section in ("sets", "given", "decisions", "constraints"):
            for entry, location, _ in declarations[section]:
                self._text(entry.get("definition", _MISSING), f"{location}.definition")
        for entry, location, _ in declarations["given"]:
            self._indices(entry.get("axes", _MISSING), f"{location}.axes")
            for field in ("domain", "units"):
                self._text(entry.get(field, _MISSING), f"{location}.{field}")

        bounds = []
        for entry, location, _ in declarations["decisions"]:
            axes = self._indices(entry.get("axes", _MISSING), f"{location}.axes")
            self._enum(entry.get("domain", _MISSING), f"{location}.domain", _DOMAINS)
            self._text(entry.get("units", _MISSING), f"{location}.units")
            bounds.extend(self._bounds(entry, location, axes))

        definitions = {}
        for entry, location, name in declarations["derived"]:
            axes = self._indices(entry.get("axes", _MISSING), f"{location}.axes")
            self._text(entry.get("units", _MISSING), f"{location}.units")
            path = f"{location}.expression"
            dependencies = self._expression(entry.get("expression", _MISSING), path, axes)
            if name is not None:
                definitions[name] = path, dependencies

        objective = obj.get("objective", _MISSING)
        if self._object(objective, "$.objective", ("sense", "units", "expression", "definition")):
            self._enum(objective.get("sense", _MISSING), "$.objective.sense", ("min", "max"))
            self._text(objective.get("units", _MISSING), "$.objective.units")
            self._text(objective.get("definition", _MISSING), "$.objective.definition")
            self._expression(objective.get("expression", _MISSING), "$.objective.expression")

        for entry, location, _ in declarations["constraints"]:
            scope = self._indices(entry.get("forall", _MISSING), f"{location}.forall")
            self._enum(entry.get("relation", _MISSING), f"{location}.relation", _RELATIONS)
            for side in ("lhs", "rhs"):
                self._expression(entry.get(side, _MISSING), f"{location}.{side}", scope)

        decision_dependent = self._derived_dependencies(definitions)
        for name, location in bounds:
            if self.symbols[name] == "decisions" or name in decision_dependent:
                self.errors.append(f"{location}: bound depends on a decision through {name!r}")


def validate_formulation(obj):
    """Validate one decoded optimization formulation declaration.

    Parameters
    ----------
    obj : object
        Decoded JSON value to check. The declaration remains unchanged.

    Returns
    -------
    None
        A valid declaration has no result. Formula text remains opaque.

    Raises
    ------
    ConfigError
        The declaration violates the grammar, scope or dependency rules.
        The exception contains the accumulated field-located problems.
    """
    validator = _Validator()
    validator._validate(obj)
    if validator.errors:
        raise ConfigError(validator.errors)


def _main(argv=None):
    """Read strict JSON, check its declaration and report the result."""
    parser = argparse.ArgumentParser(description="Validate a static optimization formulation.")
    parser.add_argument("file", metavar="FILE", help="UTF-8 JSON declaration")
    args = parser.parse_args(argv)
    try:
        obj = _load_strict_json(args.file, parse_int=_parse_integer)
        validate_formulation(obj)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Could not validate {args.file}\n{exc}", file=sys.stderr)
        return 1
    print(f"{args.file}: valid formulation")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
