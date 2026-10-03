"""``TrailingRank`` - rank entities by an aggregate over a trailing window.

A universe is often chosen by one rule: of every entity, keep the ``top_n``
whose ``value_field`` aggregates highest over the most recent ``window``
sessions, after removing entities already taken. The rule is domain-neutral
(tickers by traded volume, stores by sales, sensors by event count), so it is a
node whose fields, window, aggregate, size and exclusion list are all params.

The window is the last ``window`` DISTINCT values of ``window_field`` seen in
the input (sorted), not a calendar span: a missing day is not a session, so no
holiday table is needed. Ties break on the entity key ascending, so two runs
over the same rows write the same ranking.

Wired by import path (``dskit.pipeline.kinds_rank:TrailingRank``); the module
registers nothing. Stdlib only.
"""

from __future__ import annotations

import json
import os
from statistics import fmean

from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok

__all__ = ["AGGREGATES", "DEFAULT_AGGREGATE", "DEFAULT_ENTITIES_KEY",
           "DEFAULT_ROWS_KEY", "TrailingRank"]

#: Aggregate name -> function of the entity's in-window values.
AGGREGATES = {"sum": sum, "mean": fmean, "max": max, "min": min, "count": len}
DEFAULT_AGGREGATE = "sum"
DEFAULT_ENTITIES_KEY = "entities"
DEFAULT_ROWS_KEY = "ranking"


def _name_ok(value):
    """Return whether value is a non-empty string."""
    return isinstance(value, str) and bool(value)


class TrailingRank(Node):
    """Top-``top_n`` entities by an aggregate over the trailing ``window`` sessions.

    Parameters
    ----------
    params : dict
        ``entity_field``, ``window_field``, ``value_field`` (str, required):
        the row fields naming the entity, the session label (any sortable
        scalar) and the number to aggregate. ``window`` (int >= 1, required):
        how many most recent distinct session labels. ``top_n`` (int >= 1,
        required). ``aggregate`` (``sum|mean|max|min|count``, default
        ``sum``). ``min_sessions`` (int >= 1, default 1): fewest in-window
        sessions an entity needs. ``exclude`` (list of str, default empty)
        and ``exclude_file`` + ``exclude_key``: entities removed before
        ranking; the file is JSON holding a list under ``exclude_key``.
        ``require_top_n`` (bool, default false): refuse when fewer than
        ``top_n`` candidates remain. ``entities_key`` / ``rows_key`` (str):
        names of the two lists in the ``table`` output.

    Examples
    --------
    Rank tickers by 20-session total volume, skipping a held-out list::

        node = TrailingRank("rank", {
            "entity_field": "symbol", "window_field": "session",
            "value_field": "volume", "window": 20, "top_n": 300,
            "exclude_file": "configs/stock_universe.json",
            "exclude_key": "tickers"})
        out = node.run(ctx, {"records": rows})
        # -> out["records"][0] == {"rank": 1, "symbol": ..., "volume_sum": ...}
    """

    role = "transform"
    outputs = ("records", "table", "metrics")

    _PARAMS = ("entity_field", "window_field", "value_field", "window", "top_n",
               "aggregate", "min_sessions", "exclude", "exclude_file",
               "exclude_key", "require_top_n", "entities_key", "rows_key")

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
        for name in ("entity_field", "window_field", "value_field"):
            if not _name_ok(params.get(name)):
                problems.append(f"{name} is required and must be a non-empty string")
        check_int_param(problems, "window", params.get("window"), ge=1)
        check_int_param(problems, "top_n", params.get("top_n"), ge=1)
        if "min_sessions" in params:
            check_int_param(problems, "min_sessions", params["min_sessions"], ge=1)
        if params.get("aggregate", DEFAULT_AGGREGATE) not in AGGREGATES:
            problems.append(f"aggregate must be one of {sorted(AGGREGATES)}")
        exclude = params.get("exclude", [])
        if not isinstance(exclude, list) or any(not _name_ok(e) for e in exclude):
            problems.append("exclude must be a list of non-empty strings")
        if ("exclude_file" in params) != ("exclude_key" in params):
            problems.append("exclude_file and exclude_key go together")
        for name in ("exclude_file", "exclude_key", "entities_key", "rows_key"):
            if name in params and not _name_ok(params[name]):
                problems.append(f"{name} must be a non-empty string")
        window, min_sessions = params.get("window"), params.get("min_sessions", 1)
        if (isinstance(window, int) and isinstance(min_sessions, int)
                and min_sessions > window):
            problems.append("min_sessions must not exceed window")
        if (params.get("entities_key", DEFAULT_ENTITIES_KEY)
                == params.get("rows_key", DEFAULT_ROWS_KEY)):
            problems.append("entities_key and rows_key must differ")
        if not isinstance(params.get("require_top_n", False), bool):
            problems.append("require_top_n must be a bool")
        return problems

    def _excluded(self):
        """Return the entities removed before ranking (inline plus file)."""
        names = set(self.params.get("exclude", []))
        path = self.params.get("exclude_file")
        if path is None:
            return names
        path = os.path.expanduser(path)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self.key}: cannot read exclude_file {path!r}: {exc}") from exc
        listed = doc.get(self.params["exclude_key"]) if isinstance(doc, dict) else None
        if not isinstance(listed, list) or any(not _name_ok(e) for e in listed):
            raise ValueError(
                f"{self.key}: {path!r} holds no list of names under "
                f"{self.params['exclude_key']!r}")
        return names | set(listed)

    def _check_rows(self, rows):
        """Refuse a row that is not a mapping or lacks a configured field, by index."""
        need = [self.params[n] for n in ("entity_field", "window_field", "value_field")]
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or any(f not in row for f in need):
                raise ValueError(
                    f"{self.key}: row {index} must be a mapping holding {need}, "
                    f"got {row!r}")

    def _window(self, rows):
        """Return the last ``window`` distinct session labels, ascending."""
        field = self.params["window_field"]
        labels = sorted({row[field] for row in rows})
        if len(labels) < self.params["window"]:
            raise ValueError(
                f"{self.key}: window is {self.params['window']} but the input has "
                f"only {len(labels)} distinct {field!r} values")
        return labels[-self.params["window"]:]

    def _values(self, rows, labels):
        """Entity -> in-window values; refuses a non-number by row index."""
        p = self.params
        keep = set(labels)
        values = {}
        for index, row in enumerate(rows):
            if row[p["window_field"]] not in keep:
                continue
            value = row.get(p["value_field"])
            if not number_ok(value):
                raise ValueError(
                    f"{self.key}: row {index} {p['value_field']!r} is {value!r}, "
                    "not a finite number")
            values.setdefault(row[p["entity_field"]], []).append(value)
        return values

    def run(self, ctx, inputs):
        """Rank the entities and cut to ``top_n``.

        Parameters
        ----------
        ctx : NodeContext
            The run frame (unused).
        inputs : dict
            ``records``: a list of mapping rows.

        Returns
        -------
        dict
            ``records`` (ranked rows), ``table`` (``{entities_key: ordered
            names, rows_key: ranked rows}``) and ``metrics`` (``candidates``,
            ``excluded``, ``selected``, ``window_first``, ``window_last``).

        Raises
        ------
        ValueError
            On too short a history, a non-numeric value, an unreadable
            exclude file, or ``require_top_n`` unmet.
        """
        p = self.params
        rows = list(inputs["records"])
        self._check_rows(rows)
        labels = self._window(rows)
        values = self._values(rows, labels)
        excluded = self._excluded()
        taken = sorted(set(values) & excluded)
        agg_name = p.get("aggregate", DEFAULT_AGGREGATE)
        column = f"{p['value_field']}_{agg_name}"
        scored = [
            (AGGREGATES[agg_name](vals), len(vals), name)
            for name, vals in values.items()
            if name not in excluded and len(vals) >= p.get("min_sessions", 1)]
        scored.sort(key=lambda item: (-item[0], item[2]))
        if p.get("require_top_n", False) and len(scored) < p["top_n"]:
            raise ValueError(
                f"{self.key}: only {len(scored)} candidates remain for top_n "
                f"{p['top_n']}")
        ranked = [
            {"rank": rank, p["entity_field"]: name, column: value, "sessions": count}
            for rank, (value, count, name) in enumerate(scored[:p["top_n"]], start=1)]
        table = {p.get("entities_key", DEFAULT_ENTITIES_KEY):
                 [r[p["entity_field"]] for r in ranked],
                 p.get("rows_key", DEFAULT_ROWS_KEY): ranked}
        metrics = {"candidates": len(scored), "excluded": len(taken),
                   "selected": len(ranked), "window_first": labels[0],
                   "window_last": labels[-1]}
        return {"records": ranked, "table": table, "metrics": metrics}
