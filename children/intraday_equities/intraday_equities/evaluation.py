"""Replay outputs -> ``dskit-eval-v1`` events (ADR-0183 item 14).

A tier-3 wrapper: :class:`ReplayEvents` only MAPS what ``PortfolioSelect``
and ``DevelopmentReplay`` already produced — candidates, fills, refusals,
skips, cash flows and bars — onto the event schema ``dskit.evaluation``
renders. It computes no statistic, folds no P&L and invents no reason
code: every refusal/skip reason and every exit reason is the replay's own
string. The events are plain dicts so this module does not import
``dskit.evaluation``; the document wires ``events`` into
``dskit.evaluation.nodes:EvaluationReport`` by dotted path.

Joins are exact, never inferred from bar adjacency: the replay stamps
every entry fill and every decision-derived refusal/skip with
``decision_ms`` (the decision bar it answers), and every exit fill with
its ``reason``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params
from dskit.pipeline.runs import CONFIG_FILE

#: The schema tag every emitted event carries.
SCHEMA = "dskit-eval-v1"

#: Kind order within one instant. A cash flow the policy funds at an
#: instant is credited before that tick's fills (``EquityReplay.read_entry``
#: funds, then ``evaluate`` fills), so it sorts ahead of them.
KIND_ORDER = (
    "run_start",
    "cashflow",
    "mark",
    "decision",
    "order",
    "refusal",
    "skip",
    "fill",
    "run_end",
)
_RANK = {kind: i for i, kind in enumerate(KIND_ORDER)}

#: A candidate outside the tradable list is scored but cannot be chosen.
NOT_TRADABLE = "not_tradable"
#: The chosen name's own reason when the replay entered it.
TOP_SCORE = "top_score"
#: A chosen name the replay never answered (no fill, refusal or skip).
NOT_SUBMITTED = "not_submitted"

_MARK_MODES = ("traded", "all")

#: The replay's display units: ``PortfolioSelect`` scores are the model's
#: forecast of ``y_next``, the next bar's LOG return (``ReturnWindows.
#: return_kind``), and the account is in US dollars.
DEFAULT_UNITS = {"score": "log_return", "money": "USD"}
_RESOLVED_FILE = "resolved.json"


def _int_ms(value):
    return None if value is None else int(value)


def _read_json(run_dir, name):
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class ReplayEvents(Node):
    """Map a replay's outputs onto ``dskit-eval-v1`` events.

    Role ``transform`` — the ``intraday_equities-replay-events`` kind.

    Parameters
    ----------
    params : dict
        ``title``, ``project`` (strings), ``tz`` (IANA zone, display only),
        ``price_field`` (bar field used for marks and the decision
        reference price), ``mark_symbols`` (``traded``: only names that
        were chosen or filled; ``all``: every bar). Optional: ``criteria``
        (list of pre-registered criterion objects, ADR-0183),
        ``trials`` (int >= 1, configurations tried), ``model`` (label
        stamped on each decision), ``units`` (``run_start.units``,
        default :data:`DEFAULT_UNITS`).

    Examples
    --------
    ::

        node = ReplayEvents("events", {
            "title": "replay", "project": "intraday_equities",
            "tz": "America/New_York", "price_field": "close",
            "mark_symbols": "traded",
        })
        node.params["mark_symbols"]  # 'traded'
    """

    role = "transform"
    outputs = ("events",)
    _PARAMS = ("title", "project", "tz", "price_field", "mark_symbols")
    _OPTIONAL = ("criteria", "trials", "model", "units")
    _LISTS = ("candidates", "fills", "refused", "skipped", "cash_flows", "bars")

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Classify the node as a pure input/param reader."""
        return "pure"

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            Declared node params.

        Returns
        -------
        list of str
            One problem per broken knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS + cls._OPTIONAL)
        for name in ("title", "project", "price_field"):
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(f"{name} is required and must be a non-empty string, got {value!r}")
        tz = params.get("tz")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            problems.append(f"tz must be an IANA time zone, got {tz!r}")
        if params.get("mark_symbols") not in _MARK_MODES:
            problems.append(
                f"mark_symbols must be one of {list(_MARK_MODES)}, got {params.get('mark_symbols')!r}"
            )
        criteria = params.get("criteria", [])
        if not isinstance(criteria, list) or not all(isinstance(c, dict) for c in criteria):
            problems.append(f"criteria must be a list of objects, got {criteria!r}")
        trials = params.get("trials", 1)
        if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
            problems.append(f"trials must be an int >= 1, got {trials!r}")
        model = params.get("model", "")
        if not isinstance(model, str):
            problems.append(f"model must be a string, got {model!r}")
        units = params.get("units", DEFAULT_UNITS)
        if not isinstance(units, dict) or not all(isinstance(v, str) for v in units.values()):
            problems.append(f"units must be an object of strings, got {units!r}")
        return problems

    def validate_inputs(self, inputs):
        """Require the replay's row lists; ``picks`` is optional.

        Parameters
        ----------
        inputs : dict
            ``candidates``, ``fills``, ``refused``, ``skipped``,
            ``cash_flows``, ``bars``; optional ``picks``.

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        for port in self._LISTS:
            if not isinstance(inputs.get(port), (list, tuple)):
                problems.append(f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}")
        picks = inputs.get("picks")
        if picks is not None and not isinstance(picks, (list, tuple)):
            problems.append(f"picks must be a list of rows when wired, got {type(picks).__name__}")
        return problems

    def run(self, ctx, inputs):
        """Build the ordered event list.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext or None
            Run frame; its ``run_dir`` supplies the document, its hash and
            the data fingerprints for ``run_start`` when present.
        inputs : dict
            See :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``events``: schema-v1 dicts, ``seq`` strictly increasing,
            ordered by ``ts_ms`` then :data:`KIND_ORDER`.
        """
        body = []  # (ts_ms, kind, instrument, event fields past the envelope)
        price_field = self.params["price_field"]
        closes = {}
        for bar in inputs["bars"]:
            price = bar.get(price_field)
            if price is not None:
                closes[(bar["symbol"], int(bar["asof_ms"]))] = float(price)

        # Outcomes the replay produced, keyed by the decision they answer.
        entries = {}
        rejections = defaultdict(list)
        exits, loose = [], []
        for row in inputs["fills"]:
            if row["kind"] == "entry":
                entries[(row["symbol"], _int_ms(row["decision_ms"]))] = row
            else:
                exits.append(row)
        for kind, port in (("refusal", "refused"), ("skip", "skipped")):
            for row in inputs[port]:
                if row.get("decision_ms") is None:
                    loose.append((kind, row))
                else:
                    rejections[(row["symbol"], _int_ms(row["decision_ms"]))].append((kind, row))

        chosen_by_stamp = self._chosen(inputs)
        traded = set(chosen_by_stamp.values())
        by_stamp = defaultdict(list)
        for row in inputs["candidates"]:
            by_stamp[int(row["asof_ms"])].append(row)
        model = self.params.get("model")
        for stamp in sorted(by_stamp):
            rows = sorted(by_stamp[stamp], key=lambda r: (r["rank"], r["symbol"]))
            chosen = chosen_by_stamp.get(stamp)
            decision_id = f"d-{stamp}"
            event = {
                "decision_id": decision_id,
                "candidates": [self._candidate(r) for r in rows],
                "chosen": chosen,
                "threshold": None,
                "edge": self._edge(rows, chosen),
            }
            if model:
                event["model"] = model
            entry = entries.get((chosen, stamp)) if chosen else None
            found = rejections.get((chosen, stamp), []) if chosen else []
            if chosen is None:
                event.update(action="hold", reason="no_eligible_candidate")
            elif entry is not None:
                event.update(
                    action="enter", reason=TOP_SCORE,
                    detail=f"lead {entry['lead']} {entry['side']} {entry['qty']}",
                )
            elif found:
                kind, first = found[0]
                event.update(
                    action="refuse" if kind == "refusal" else "skip",
                    reason=first["reason"],
                    detail=f"lead {first.get('lead')}",
                )
            else:
                event.update(action="hold", reason=NOT_SUBMITTED,
                             detail="the replay produced no fill, refusal or skip")
            body.append((stamp, "decision", chosen, event))
            if entry is not None:
                order_id = f"o-{chosen}-{stamp}-{entry['lead']}"
                body.append((stamp, "order", chosen, {
                    "order_id": order_id,
                    "decision_id": decision_id,
                    "side": entry["side"],
                    "qty": entry["qty"],
                    "ref_price": closes.get((chosen, stamp)),
                }))
                body.append(self._fill(entry, order_id, closes.get((chosen, stamp))))
            for kind, row in found:
                body.append(self._rejection(kind, row, decision_id))

        for row in exits:
            at = int(row["asof_ms"])
            symbol = row["symbol"]
            decision_id = f"x-{symbol}-{row['lead']}-{at}"
            order_id = f"o-{decision_id}"
            body.append((at, "decision", symbol, {
                "decision_id": decision_id,
                "chosen": symbol,
                "action": "exit",
                "reason": row.get("reason") or "exit",
                "detail": f"lead {row['lead']} lot closed",
            }))
            body.append((at, "order", symbol, {
                "order_id": order_id,
                "decision_id": decision_id,
                "side": row["side"],
                "qty": row["qty"],
            }))
            body.append(self._fill(row, order_id, None))
            traded.add(symbol)

        # Refusals/skips of an open lot (no decision bar): its own exit decision.
        for kind, row in loose:
            at = int(row["asof_ms"])
            symbol = row["symbol"]
            decision_id = f"x-{symbol}-{row.get('lead')}-{at}"
            body.append((at, "decision", symbol, {
                "decision_id": decision_id,
                "chosen": symbol,
                "action": "refuse" if kind == "refusal" else "skip",
                "reason": row["reason"],
                "detail": f"lead {row.get('lead')} lot exit",
            }))
            body.append(self._rejection(kind, row, decision_id))

        for flow in inputs["cash_flows"]:
            at = int(flow["asof_ms"])
            event = {"amount": flow["amount"], "rule": flow["rule"]}
            if flow.get("detail"):
                event["detail"] = flow["detail"]
            body.append((at, "cashflow", None, event))

        marked = None if self.params["mark_symbols"] == "all" else traded
        for (symbol, at), price in closes.items():
            if marked is not None and symbol not in marked:
                continue
            if price > 0:
                body.append((at, "mark", symbol, {"price": price}))

        return {"events": self._envelope(ctx, body)}

    def _chosen(self, inputs):
        """``{asof_ms: symbol}`` from ``picks`` when wired, else the candidates' flag."""
        picks = inputs.get("picks")
        if picks is not None:
            return {int(p["asof_ms"]): p["symbol"] for p in picks}
        return {
            int(r["asof_ms"]): r["symbol"] for r in inputs["candidates"] if r.get("chosen")
        }

    @staticmethod
    def _candidate(row):
        out = {
            "instrument": row["symbol"],
            "score": row["score"],
            "rank": row["rank"],
            "eligible": bool(row["eligible"]),
        }
        if not row["eligible"]:
            out["reason"] = NOT_TRADABLE
        return out

    @staticmethod
    def _edge(rows, chosen):
        """Chosen score less the best other ELIGIBLE score; None without both."""
        mine = next((r["score"] for r in rows if r["symbol"] == chosen), None)
        other = next(
            (r["score"] for r in rows if r["eligible"] and r["symbol"] != chosen), None
        )
        if mine is None or other is None:
            return None
        return float(mine) - float(other)

    @staticmethod
    def _fill(row, order_id, ref_price):
        at = int(row["asof_ms"])
        event = {
            "fill_id": f"f-{row['kind']}-{row['symbol']}-{row['lead']}-{at}",
            "order_id": order_id,
            "side": row["side"],
            "qty": row["qty"],
            "price": row["price"],
            "fee": row["fee"],
            "tag": row["kind"],
        }
        if ref_price is not None:
            event["ref_price"] = ref_price
        return (at, "fill", row["symbol"], event)

    @staticmethod
    def _rejection(kind, row, decision_id):
        at = int(row["asof_ms"])
        event = {
            "decision_id": decision_id,
            "reason": row["reason"],
            "detail": f"lead {row.get('lead')}",
        }
        return (at, kind, row["symbol"], event)

    def _envelope(self, ctx, body):
        """Sort, bracket with run_start/run_end and stamp the envelope."""
        body.sort(key=lambda item: (
            item[0], _RANK[item[1]], item[2] or "", item[3].get("decision_id", ""),
        ))
        first = body[0][0] if body else 0
        last = body[-1][0] if body else 0
        items = [(first, "run_start", None, self._run_start(ctx))]
        items.extend(body)
        items.append((last, "run_end", None, {"status": "ok"}))
        events = []
        for seq, (ts, kind, instrument, event) in enumerate(items):
            events.append({
                "schema": SCHEMA,
                "seq": seq,
                "kind": kind,
                "ts_ms": ts,
                "known_ms": ts,
                "instrument": instrument,
                **event,
            })
        return events

    def _run_start(self, ctx):
        params = self.params
        event = {
            "run_id": params["title"],
            "title": params["title"],
            "project": params["project"],
            "tz": params["tz"],
            "criteria": list(params.get("criteria", [])),
            "trials": params.get("trials", 1),
            "units": dict(params.get("units", DEFAULT_UNITS)),
        }
        run_dir = getattr(ctx, "run_dir", None)
        if run_dir:
            resolved = _read_json(run_dir, _RESOLVED_FILE) or {}
            config = _read_json(run_dir, CONFIG_FILE)
            if resolved.get("run_hash"):
                event["run_id"] = resolved["run_hash"]
            if resolved.get("document_hash"):
                event["config_hash"] = resolved["document_hash"]
            if isinstance(resolved.get("data_fingerprint"), dict):
                event["data"] = resolved["data_fingerprint"]
            if isinstance(config, dict):
                event["config"] = config
        return event


NODE_KINDS = {
    "intraday_equities-replay-events": ReplayEvents,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
