"""Replay outputs -> ``dskit-eval-v1`` events (ADR-0183 item 14).

A tier-3 wrapper: :class:`ReplayEvents` only MAPS what ``PortfolioSelect``
and ``DevelopmentReplay`` already produced — candidates, fills, refusals,
skips, cash flows, bars and optimizer solves (ADR-0183 phase 2: each
``solves`` row is one ``solve`` event at its tick, carrying only the
``libs.pyomo.SolveRecord`` keys) — onto the event schema ``dskit.evaluation``
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

from dskit.pipeline.libs.pyomo import SolveRecord
from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params
from dskit.pipeline.runs import CONFIG_FILE

#: The schema tag every emitted event carries.
SCHEMA = "dskit-eval-v1"

#: Kind order within one instant. A cash flow the policy funds at an
#: instant is credited before that tick's fills (``EquityReplay.read_entry``
#: funds, then ``evaluate`` fills), so it sorts ahead of them. A solve is
#: what a decision at the same instant is read from, so it precedes it.
KIND_ORDER = (
    "run_start",
    "cashflow",
    "mark",
    "solve",
    "decision",
    "order",
    "refusal",
    "skip",
    "fill",
    "outcome",
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
        stamped on each decision, and on each solve whose row names no
        ``model`` of its own), ``units`` (``run_start.units``,
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
    _OPTIONAL = ("criteria", "trials", "model", "units", "realized_field", "outcome_lead_bars")
    _LISTS = ("candidates", "fills", "refused", "skipped", "cash_flows", "bars")
    _OPTIONAL_LISTS = ("picks", "solves", "labeled", "findings")
    #: What ``labeled`` needs declared: the realised field and how many bars
    #: after the decision it becomes known.
    _OUTCOME_PARAMS = ("realized_field", "outcome_lead_bars")

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
        if "realized_field" in params and (
                not isinstance(params["realized_field"], str) or not params["realized_field"]):
            problems.append(
                f"realized_field must be a non-empty string, got {params['realized_field']!r}")
        lead = params.get("outcome_lead_bars")
        if "outcome_lead_bars" in params and (
                isinstance(lead, bool) or not isinstance(lead, int) or lead < 1):
            problems.append(f"outcome_lead_bars must be an int >= 1, got {lead!r}")
        return problems

    def validate_inputs(self, inputs):
        """Require the replay's row lists; ``picks`` and ``solves`` are optional.

        Parameters
        ----------
        inputs : dict
            ``candidates``, ``fills``, ``refused``, ``skipped``,
            ``cash_flows``, ``bars``; optional ``picks``, ``solves``
            (rows with ``asof_ms``, the ``SolveRecord`` keys and an
            optional ``model`` string), ``labeled`` (rows keyed by
            ``asof_ms`` + ``symbol`` carrying ``realized_field``; needs
            ``realized_field`` and ``outcome_lead_bars``), ``findings``
            (``DevelopmentReplay.findings`` rows) and ``ledger``
            (``DevelopmentReplay.ledger``: its head names the ledger in
            ``run_start.data``).

        Returns
        -------
        list of str
            Input problems.
        """
        problems = []
        for port in self._LISTS:
            if not isinstance(inputs.get(port), (list, tuple)):
                problems.append(f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}")
        for port in self._OPTIONAL_LISTS:
            rows = inputs.get(port)
            if rows is not None and not isinstance(rows, (list, tuple)):
                problems.append(f"{port} must be a list of rows when wired, got {type(rows).__name__}")
        if inputs.get("labeled") is not None:
            missing = [name for name in self._OUTCOME_PARAMS if name not in self.params]
            if missing:
                problems.append(f"labeled is wired, so params {missing} must be declared")
        ledger = inputs.get("ledger")
        if ledger is not None and not isinstance(ledger, dict):
            problems.append(f"ledger must be an object or null, got {type(ledger).__name__}")
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

        findings = self._findings_by_fill(inputs.get("findings") or ())
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
                body.append((stamp, "order", chosen, self._with_findings({
                    "order_id": order_id,
                    "decision_id": decision_id,
                    "side": entry["side"],
                    "qty": entry["qty"],
                    "ref_price": closes.get((chosen, stamp)),
                }, findings, entry)))
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
            body.append((at, "order", symbol, self._with_findings({
                "order_id": order_id,
                "decision_id": decision_id,
                "side": row["side"],
                "qty": row["qty"],
            }, findings, row)))
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

        body.extend(self._solve(row, model) for row in inputs.get("solves") or ())
        if inputs.get("labeled") is not None:
            body.extend(self._outcomes(by_stamp, inputs["labeled"], inputs["bars"]))

        marked = None if self.params["mark_symbols"] == "all" else traded
        for (symbol, at), price in closes.items():
            if marked is not None and symbol not in marked:
                continue
            if price > 0:
                body.append((at, "mark", symbol, {"price": price}))

        return {"events": self._envelope(ctx, body, inputs.get("ledger"))}

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
    def _findings_by_fill(rows):
        """``{(symbol, lead, kind, fill asof_ms): [finding]}`` from the replay's judged legs."""
        out = {}
        for row in rows:
            if row.get("symbol") is None:
                continue  # a refused leg has no fill; the replay fails loudly on it
            key = (row["symbol"], row["lead"], row["kind"], int(row["asof_ms"]))
            out.setdefault(key, []).extend(_finding(f) for f in row["findings"])
        return out

    @staticmethod
    def _with_findings(order, findings, fill):
        """Attach the ledger findings of the leg that produced ``fill`` to ``order``."""
        found = findings.get((fill["symbol"], fill["lead"], fill["kind"], int(fill["asof_ms"])))
        if found:
            order["findings"] = found
        return order

    def _outcomes(self, by_stamp, labeled, bars):
        """One ``outcome`` per candidate: its realised label, known ``outcome_lead_bars`` later.

        The outcome's instant is the candidate symbol's ``outcome_lead_bars``-th
        later bar in ``bars`` — when the label's target bar has closed. A
        candidate with no such bar, or no label, gets none (the report counts
        it as unscored).
        """
        field, lead = self.params["realized_field"], self.params["outcome_lead_bars"]
        realized = {(int(r["asof_ms"]), r["symbol"]): r.get(field) for r in labeled}
        times = defaultdict(list)
        for bar in bars:
            times[bar["symbol"]].append(int(bar["asof_ms"]))
        index = {}
        for symbol, stamps in times.items():
            stamps.sort()
            index[symbol] = {at: i for i, at in enumerate(stamps)}
        out = []
        for stamp, rows in by_stamp.items():
            for row in rows:
                symbol, value = row["symbol"], realized.get((stamp, row["symbol"]))
                position = index.get(symbol, {}).get(stamp)
                if value is None or position is None or position + lead >= len(times[symbol]):
                    continue
                out.append((times[symbol][position + lead], "outcome", symbol, {
                    "decision_id": f"d-{stamp}", "horizon": f"{lead} bar(s)",
                    "realized": float(value),
                }))
        return out

    @staticmethod
    def _solve(row, model):
        """One solve row -> a ``solve`` event at its tick: the record's keys, then the label."""
        at = int(row["asof_ms"])
        event = {name: row[name] for name in SolveRecord.field_names() if name in row}
        label = row.get("model") or model
        if label:
            event["model"] = label
        return (at, "solve", None, event)

    @staticmethod
    def _rejection(kind, row, decision_id):
        at = int(row["asof_ms"])
        event = {
            "decision_id": decision_id,
            "reason": row["reason"],
            "detail": f"lead {row.get('lead')}",
        }
        return (at, kind, row["symbol"], event)

    def _envelope(self, ctx, body, ledger=None):
        """Sort, bracket with run_start/run_end and stamp the envelope."""
        body.sort(key=lambda item: (
            item[0], _RANK[item[1]], item[2] or "", item[3].get("decision_id", ""),
        ))
        first = body[0][0] if body else 0
        last = body[-1][0] if body else 0
        items = [(first, "run_start", None, self._run_start(ctx, ledger))]
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

    def _run_start(self, ctx, ledger=None):
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
        if ledger:
            event.setdefault("data", {})["ledger"] = f"seq {ledger['seq']} {ledger['hash']}"
        return event


def _finding(stored):
    """Return a stored ``Finding`` object as an event finding, decimals as numbers."""
    out = {key: stored.get(key) for key in ("guard", "measure", "verdict", "reason", "window",
                                            "scope_key")}
    for key in ("value", "bound"):
        out[key] = None if stored.get(key) is None else float(stored[key])
    return out


NODE_KINDS = {
    "intraday_equities-replay-events": ReplayEvents,
}

for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
