"""The toolkit-owned run evaluator: ``run-report`` (role ``report``).

Why this kind exists (I-232). A run can exit 0 with every node green, a
model that beats its baseline, and every instrument surviving the edge
test at p ~ 1e-4 — and still deploy ZERO lots, with no artifact anywhere
saying so. The node table in ``report.md`` cannot say it: "all 14 node(s)
completed" is true of both the healthy run and the broken one. This kind
is the surface that tells them apart.

It is deliberately an EVALUATOR, not a computer of new numbers. Every
stage already computes far more than it returns; the fix is to stop
throwing that away at the wrapper boundary, hand it here, and render it.
Nothing in this module recomputes a loss, a p-value, an edge or a lot
count — a second answer to a question the machinery already answered is
the defect this kind exists to prevent, not a feature.

Why it is GENERIC (D-146). "Why did capital not move?" is a question any
venue's pipeline must answer; the ANSWER (what a particular ladder's
mutual-exclusivity projection did to a particular book) is venue-specific
and belongs in that venue's adapter. So the adapters build the evidence
and this kind renders it, flags it, and writes it down.

The evidence convention
-----------------------
Each stage hands this node one plain dict. To be RENDERED as a table
rather than a blob, it should carry any of:

``stage``
    Human label for the stage (defaults to the port name).
``split``
    Which split the stage read — the per-split axis. A stage that spans
    splits may instead key ``instruments`` rows by split.
``instruments``
    ``{instrument: {field: scalar}}`` — the per-instrument axis, rendered
    as one markdown table whose columns are the union of the row keys.
``totals``
    ``{field: scalar}`` — rendered as a bullet list.
``notes``
    List of strings — rendered verbatim. Where a stage says in words what
    a number cannot ("47 cells declined to fit: below min_train").

Any OTHER key is still rendered — as a bullet when it is a scalar, and as
its own table when it is itself ``{key: {field: ...}}`` (reliability by
price bucket, per-event solver detail). So a stage can never silently
drop evidence by handing over a key this module has not heard of. Long
tables are truncated in the markdown with a pointer to ``evidence.json``,
which always holds every row.

The five owner surfaces
-----------------------
Above the per-stage sections sit five NAMED reads, each switched on by
the document's ``sections`` param and each rendering only from evidence
some other component already produced:

``summary``
    Performance, headed by P&L and a RETURN — never by final bankroll.
    Once capital is deposited into a running bankroll, the closing
    balance is a balance and not a result: a deposit reads as a gain.
    So ``final_bankroll`` is rendered LABELLED as a balance, and the
    return metric named by ``return_metric`` is what the section leads
    on. A performance section with money in and no return metric wired
    raises a note saying exactly which name it looked for.
``trades``
    Every fill: when, where, which market, which side, how big, at what
    price, for what fee, and what P&L. The MARKDOWN is the scannable
    read (capped at ``max_rows``, chronological); the exhaustive list is
    the CSV named by ``trades_artifact``. A canonical column no producer
    supplied is reported as missing BY NAME rather than left blank —
    the reader must never mistake "not recorded" for "zero".
``edge_test``
    The edge test as a test: which test, what statistic, what p-value,
    how many events, at what alpha under which correction — per market,
    never pooled, because which market has the edge IS the deliverable.
``family``
    Round over round: which markets NEWLY cleared the ``min_events`` bar
    and entered the family, which left, and who is closest to the bar.
    The prior round arrives as a ``$prev`` PARAM (carries are legal only
    in params — ``dskit.pipeline.document``), read out of the previous
    run's ``carry.json``.
``decisions``
    Which markets reached the optimizer, each with the model's ``q`` and
    the market's price SIDE BY SIDE at the decision instant — the
    comparison the whole edge claim rests on — stamped with that instant
    so it cannot be mistaken for a lookahead read.

Every section renders per market. Nothing here pools cells, leads or
markets into a single number, and no venue is privileged (D-137): a
market's row is a market's row.

Flags
-----
``flags`` is a list of ``{"level", "code", "message"}``. ``level``
``"LOUD"`` is the one a human must not miss; the driver lifts every flag
to the top of ``report.md``.

The LOUD flag is ``survivors > 0 and lots == 0``. It is a FINDING, never
a verdict: it may be a genuine economic result (an edge too thin to clear
the fee at any size) or a broken wire, and this node's whole job is to
make the two distinguishable rather than to prejudge which occurred. Per
the owner ruling of 2026-08-15 it is REPORT ONLY — it does not fail the
run, does not halt descendants, and does not change the driver's
exit-code contract (0 ran / 3 NO-GO / 1 error). The same is true of
``family-bar-mismatch``, the second LOUD: it fires when the bar this
report NAMES disagrees with the gate that actually ran, which would make
every family number on the page a lie about a different threshold.

Import cost: stdlib only.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    Node,
    reject_unknown_params,
)

__all__ = [
    "DECISION_COLUMNS",
    "EVIDENCE_PORTS",
    "SECTIONS",
    "TRADE_COLUMNS",
    "RunReport",
    "register",
]

#: The stage ports, in the order a run executes them — which is also the
#: order the report renders. Every one is OPTIONAL: a document that has no
#: sizing stage yet still gets a report of the stages it does have.
EVIDENCE_PORTS = ("training", "validation", "edge", "sizing", "replay")

#: The named surfaces, in render order. ``sections`` selects from these;
#: the deployment block and the flags are NOT selectable — they are the
#: reason this kind exists and a document must not be able to switch off
#: the line that separates a healthy run from a broken one.
SECTIONS = ("summary", "trades", "edge_test", "family", "decisions", "stages")


#: Default-deny on this class's own knobs. One definition, in ``node.py``
#: beside the ``validate_params`` protocol it serves; this alias keeps
#: the module-local spelling every kind in this file already uses.
_reject_unknown = reject_unknown_params


def _fmt(value):
    """One cell of a rendered table."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"({len(value)} item(s))"
    if isinstance(value, dict):
        return f"({len(value)} key(s))"
    text = str(value)
    return text if len(text) <= 120 else text[:120] + "…"


def _table(rows, key_header):
    """Markdown table from ``{key: {field: scalar}}``; columns are the
    union of every row's keys so a field only some rows carry still
    shows (as ``—`` elsewhere) instead of vanishing."""
    columns = sorted(
        {field for row in rows.values() if isinstance(row, dict) for field in row}
    )
    if not columns:
        return [f"- {key}" for key in sorted(rows)]
    lines = [
        "| " + " | ".join([key_header, *columns]) + " |",
        "|" + "---|" * (len(columns) + 1),
    ]
    for key in sorted(rows, key=str):
        row = rows[key] if isinstance(rows[key], dict) else {}
        lines.append(
            "| " + " | ".join([str(key), *(_fmt(row.get(c)) for c in columns)]) + " |"
        )
    return lines


def _fixed_table(columns, rows):
    """Markdown table over an ORDERED column list and a list of dict rows,
    preserving row order. :func:`_table` sorts by key and unions columns,
    which is right for an evidence blob whose shape is unknown; a trade
    ledger and a decision sheet have a stated column order and a
    meaningful row order (chronological, ranked), and neither survives an
    alphabetical sort."""
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row.get(c)) for c in columns) + " |")
    return lines


#: Rows of one table rendered into the markdown before it is truncated with
#: a pointer to the JSON. The read has to stay a read; the RECORD is never
#: truncated. Overridable per document via the ``max_rows`` param.
_MAX_TABLE_ROWS = 25

#: What ``return_metric``/``deposits_metric`` default to — the names a
#: shipping flow-aware capital stage publishes (a ``capital_returns``
#: block carrying ``twr``, ``mwr``, ``cumulative_contributions``,
#: ``trading_pnl``, …). They are params, not constants, precisely so
#: reconciling with a stage that renames them stays a document edit and
#: never a code edit.
_DEFAULT_RETURN_METRIC = "twr"
_DEFAULT_DEPOSITS_METRIC = "cumulative_contributions"

#: Returns rendered BESIDE the declared one, when the capital stage
#: publishes them. ``total_return_naive`` is the deposit-inflated reading
#: and ``trading_pnl`` the deposit-stripped one; showing them next to
#: ``twr`` is how the gap a deposit opens becomes visible instead of
#: arguable. Overridable per document via ``companion_metrics``.
_DEFAULT_COMPANION_METRICS = ("mwr", "total_return_naive", "trading_pnl")


def _is_row_table(value):
    """``True`` for a non-empty ``{key: {field: ...}}`` — a table of rows,
    not a scalar and not a bag of values."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(row, dict) for row in value.values())
    )


def _iso(ms):
    """An epoch-ms stamp as a readable UTC instant, or ``None``. A trade
    ledger read by a human is read in time, not in milliseconds — but the
    raw ``t_ms`` stays in the CSV, because that is the audit value."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (OverflowError, OSError, ValueError):
        return None


def _pick(row, names, default=None):
    """The first of ``names`` this row actually carries.

    Producers name the same quantity differently (``filled``/``qty``,
    ``avg_price``/``vwap_fill``), and a report that recognised only one
    spelling would silently blank a column that was in fact recorded.
    Aliasing is READING, never renaming: the CSV keeps the canonical
    name so downstream tooling has one vocabulary."""
    for name in names:
        if isinstance(row, dict) and name in row and row[name] is not None:
            return row[name]
    return default


def _num(value):
    """``value`` as a float when it is a real number, else ``None`` — so a
    missing field never enters a sum as a zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _total(rows, key):
    """Sum of ``key`` over ``rows``, or ``None`` when NO row carries it.
    The distinction is the point: a zero is a result, a blank is an
    absence, and a ledger that renders both as ``0.00`` is lying about
    one of them."""
    values = [_num(row.get(key)) for row in rows]
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _csv_text(columns, rows):
    """``rows`` as CSV text over an ordered column list."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c) for c in columns})
    return buffer.getvalue()


def _truncation_note(rows, shown, where):
    """The one line that keeps a truncated RENDER honest about the whole
    RECORD."""
    if len(rows) <= shown:
        return []
    return [f"_… {len(rows) - shown} more row(s) — the full list is in `{where}`._"]


# ---------------------------------------------------------------------------
# Trades (requirement 8)
# ---------------------------------------------------------------------------

#: The canonical trade columns, in render order, each with the producer
#: spellings it is read from. ``t_ms`` is kept beside ``when`` in the CSV
#: because the millisecond stamp is the audit value and the ISO string is
#: the human one — a ledger needs both.
_TRADE_FIELDS = (
    ("t_ms", ("t_ms", "asof_ms", "timestamp_ms", "ts_ms")),
    ("kind", ("kind",)),
    ("venue", ("venue", "exchange")),
    ("series", ("series", "series_ticker", "instrument")),
    ("event", ("event", "event_ticker")),
    ("contract", ("contract", "ticker", "contract_ticker")),
    ("side", ("side",)),
    ("size", ("size", "filled", "qty")),
    ("price", ("price", "avg_price", "vwap_fill")),
    ("fee", ("fee",)),
    ("pnl", ("pnl", "realized_pnl")),
)

#: The canonical trade column names, in order.
TRADE_COLUMNS = tuple(name for name, _ in _TRADE_FIELDS)

#: Columns whose absence is a REPORTING gap worth naming out loud — the
#: nine fields the owner asked every fill to carry. ``kind`` is not among
#: them: a ledger that records only fills legitimately omits it (the
#: fills-log convention is that a missing ``kind`` means ``"fill"``).
_TRADE_REQUIRED = (
    "t_ms",
    "venue",
    "series",
    "event",
    "side",
    "size",
    "price",
    "fee",
    "pnl",
)


def _trade_rows(trades):
    """Every trade as a canonical row. Nothing is computed: each cell is
    a field some producer recorded, read through its known spellings."""
    rows = []
    for entry in trades:
        row = {name: _pick(entry, names) for name, names in _TRADE_FIELDS}
        if row.get("kind") is None:
            # The fills-log convention (the replay loop's own ledger): a
            # fill carries no "kind" key, a trim carries kind="reduce".
            row["kind"] = "fill"
        row["when"] = _iso(row.get("t_ms"))
        rows.append(row)
    return rows


def _by_market(rows):
    """``(key_name, roll-up)`` — the scannable view of a 90-market run.

    Per market and ONLY per market: markets are never summed together
    here, because a pooled trade count answers no question anyone asked
    and hides the market that did all the trading.

    The grouping key is the SERIES when the ledger records one and the
    contract when it does not, and which was used is RETURNED rather than
    assumed — a table headed "market" whose rows are really contracts
    would silently multiply the universe by its strike count.

    ``lots_bought`` and ``lots_reduced`` are kept apart. Adding a trim's
    quantity to a fill's would report a position that was opened and half
    closed as a bigger position than one that was only opened.
    """
    keyed_by = "market" if any(row.get("series") for row in rows) else "contract"
    out = {}
    for row in rows:
        key = row.get("series") or row.get("contract") or "(unattributed)"
        agg = out.setdefault(
            key,
            {
                "trades": 0,
                "reduces": 0,
                "lots_bought": 0,
                "lots_reduced": 0,
                "fees": 0.0,
                "pnl": None,
            },
        )
        size = int(_num(row.get("size")) or 0)
        if row.get("kind") == "reduce":
            agg["reduces"] += 1
            agg["lots_reduced"] += size
        else:
            agg["trades"] += 1
            agg["lots_bought"] += size
        fee = _num(row.get("fee"))
        if fee is not None:
            agg["fees"] += fee
        pnl = _num(row.get("pnl"))
        if pnl is not None:
            agg["pnl"] = (agg["pnl"] or 0.0) + pnl
    return keyed_by, out


def _hit_rate(rows):
    """``(rate, n_with_pnl)`` over trades that actually carry a P&L.

    A hit rate over trades whose P&L was never recorded would be a rate
    over an unknown denominator, so the denominator is reported beside
    it and a ledger with no P&L at all yields ``None`` rather than a
    confident ``0%``."""
    scored = [_num(row.get("pnl")) for row in rows]
    scored = [v for v in scored if v is not None]
    if not scored:
        return None, 0
    return sum(1 for v in scored if v > 0) / len(scored), len(scored)


# ---------------------------------------------------------------------------
# Decisions (requirement 12)
# ---------------------------------------------------------------------------

_DECISION_FIELDS = (
    ("instrument", ("instrument", "series", "series_ticker")),
    ("event", ("event", "event_ticker")),
    ("decided_at_ms", ("asof_ms", "decided_at_ms", "t_ms")),
    ("lead_frac", ("lead_frac",)),
    ("price", ("mid", "price", "p_mid")),
    ("q", ("belief", "q", "q_hat")),
    ("edge", ("belief_edge", "edge")),
    ("fee_rate", ("fee_rate",)),
    ("lots", ("lots",)),
    ("disposition", ("disposition",)),
)

#: The canonical decision columns, in order — ``contract`` is the row key.
DECISION_COLUMNS = ("contract", *(name for name, _ in _DECISION_FIELDS))


def _decision_rows(decisions):
    """Every optimizer candidate as a canonical row, ranked by the size of
    the belief edge so the render's first screen carries the decisions
    that drove the sizing. Ranking is ORDERING, not selection: the CSV
    carries every row, and no market is excluded from the comparison."""
    rows = []
    for contract, entry in decisions.items():
        row = {"contract": contract}
        row.update({name: _pick(entry, names) for name, names in _DECISION_FIELDS})
        row["decided_at"] = _iso(row.get("decided_at_ms"))
        rows.append(row)
    rows.sort(
        key=lambda r: (-abs(_num(r.get("edge")) or 0.0), str(r.get("contract"))),
    )
    return rows


# ---------------------------------------------------------------------------
# Stage rendering (the evidence convention)
# ---------------------------------------------------------------------------


def _render_stage(port, evidence, max_rows, skip=()):
    """One stage's markdown section.

    ``skip`` names keys this stage's evidence carries that a dedicated
    section above has ALREADY rendered in full (the fills, the sizing
    candidates). They stay in ``evidence.json`` untouched — only the
    duplicate render is suppressed, because printing the same table twice
    teaches a reader to skim both.
    """
    label = evidence.get("stage") or port
    split = evidence.get("split")
    head = f"### {label}" + (f" — split `{split}`" if split else "")
    lines = [head, ""]
    totals = evidence.get("totals")
    if isinstance(totals, dict) and totals:
        lines += [f"- **{k}**: {_fmt(v)}" for k, v in sorted(totals.items())]
        lines.append("")
    # Everything the convention does not name, so a stage cannot drop
    # evidence by inventing a key this renderer has not heard of. A key
    # whose value is itself a table of rows (reliability by price bucket,
    # per-event solver detail) is RENDERED as one — collapsing it to
    # "(10 key(s))" would hide the very breakdown it was handed over for.
    extra = {
        k: v
        for k, v in evidence.items()
        if k not in ("stage", "split", "instruments", "totals", "notes")
        and k not in skip
    }
    scalars = {k: v for k, v in extra.items() if not _is_row_table(v)}
    if scalars:
        lines += [f"- {k}: {_fmt(v)}" for k, v in sorted(scalars.items())]
        lines.append("")
    instruments = evidence.get("instruments")
    if isinstance(instruments, dict) and instruments:
        lines += [f"per instrument ({len(instruments)}):", ""]
        lines += _table(instruments, "instrument")
        lines.append("")
    for key, rows in sorted(extra.items()):
        if not _is_row_table(rows):
            continue
        lines += [f"{key} ({len(rows)}):", ""]
        if len(rows) > max_rows:
            # Truncating the RENDER, never the record: evidence.json holds
            # every row, and the reader is told where to find them.
            head = dict(sorted(rows.items(), key=lambda kv: str(kv[0]))[:max_rows])
            lines += _table(head, key)
            lines.append("")
            lines.append(
                f"_… {len(rows) - max_rows} more row(s) — the full table "
                f"is in `evidence.json` under `stages.{port}.{key}`._"
            )
        else:
            lines += _table(rows, key)
        lines.append("")
    notes = evidence.get("notes")
    if isinstance(notes, (list, tuple)) and notes:
        lines += [f"- _{note}_" for note in notes]
        lines.append("")
    if len(lines) == 2:
        lines += ["_(stage reported no evidence)_", ""]
    return lines


class RunReport(Node):
    """The run evaluator (role ``report``) — the toolkit's ``run-report``
    kind: one artifact that says, per stage, per instrument and per
    split, what the run actually did with its data and its capital.

    Stage inputs (ALL optional, so a partial pipeline still reports on
    itself): ``training``, ``validation``, ``edge``, ``sizing``,
    ``replay`` — each one stage's evidence dict (see the module docstring
    for the convention); plus ``survivors`` (the edge test's surviving
    instruments) and ``lots`` (the lots the sizing stage actually
    deployed).

    Those last two are the LOUD flag's operands and are wired DIRECTLY
    from the producing nodes' real outputs rather than read out of an
    evidence dict — so the flag cannot be silenced by an evidence payload
    going missing. The failure mode being fixed is exactly "the number
    was computed and then nobody looked at it".

    Surface inputs (also all optional):

    ``trades``
        The fill ledger — a list of dicts (requirement 8).
    ``decisions``
        ``{contract: {...}}``, the optimizer's candidates with the belief
        and the price it was formed against (requirement 12).
    ``capital``
        The capital stage's own dict: deposits, the return metric named
        by ``return_metric``, and optionally ``equity_curve`` or
        ``max_drawdown`` (requirement 10).
    ``banked`` / ``family``
        This round's ``{instrument: n}`` counts and the admitted family
        (requirement 11).

    Params: ``title`` (heading); ``sections`` (which of
    :data:`SECTIONS` to render, default all); ``max_rows`` (render cap
    per table, default 25 — the RECORD is never capped); ``min_events``
    (the stated admission bar, needed to report the family delta);
    ``prev_family`` / ``prev_banked`` (the PREVIOUS round's family and
    counts, wired as ``$prev`` carries — carries are legal only in
    params); ``return_metric`` / ``deposits_metric`` (what the capital
    stage calls its return and its deposits); ``trades_artifact`` /
    ``decisions_artifact`` (CSV filenames for the exhaustive lists).

    Writes ``evidence.json`` (the whole structured record) and
    ``evidence.md`` (the human read) into this node's artifact dir, plus
    the two CSVs when their inputs are wired.

    Outputs: ``path`` — the JSON artifact; ``summary`` — ``{"stages": k,
    "flags": n, "loud": m}``; ``flags`` — the findings list, which the
    driver lifts to the top of ``report.md``.
    """

    role = "report"
    outputs = ("path", "summary", "flags")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = (
        "title",
        "sections",
        "max_rows",
        "min_events",
        "prev_family",
        "prev_banked",
        "return_metric",
        "deposits_metric",
        "companion_metrics",
        "trades_artifact",
        "decisions_artifact",
    )

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        title = params.get("title", "")
        if not isinstance(title, str):
            problems.append(f"title must be a string, got {title!r}")
        sections = params.get("sections", list(SECTIONS))
        if not isinstance(sections, (list, tuple)):
            problems.append(
                f"sections must be a list drawn from {list(SECTIONS)}, got {sections!r}"
            )
        else:
            unknown = sorted(set(sections) - set(SECTIONS))
            if unknown:
                problems.append(
                    f"unknown section(s) {unknown} — allowed: {list(SECTIONS)}"
                )
        max_rows = params.get("max_rows", _MAX_TABLE_ROWS)
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
            problems.append(f"max_rows must be an int >= 1, got {max_rows!r}")
        bar = params.get("min_events")
        # No default, ever. The admission bar is stated by the gate that
        # applies it (dskit.pipeline.kinds_flow), and a report that
        # invented one would be describing a threshold no run used.
        if bar is not None and (
            isinstance(bar, bool) or not isinstance(bar, int) or bar < 1
        ):
            problems.append(f"min_events must be an int >= 1, got {bar!r}")
        prev_family = params.get("prev_family")
        if prev_family is not None and not isinstance(prev_family, (list, tuple)):
            problems.append(
                f"prev_family must be the previous round's family (a list) or "
                f"null on the first run of the series, got {prev_family!r}"
            )
        prev_banked = params.get("prev_banked")
        if prev_banked is not None and not isinstance(prev_banked, dict):
            problems.append(
                f"prev_banked must be the previous round's counts "
                f"({{instrument: n}}) or null, got {prev_banked!r}"
            )
        for name in (
            "return_metric",
            "deposits_metric",
            "trades_artifact",
            "decisions_artifact",
        ):
            value = params.get(name)
            if value is not None and (not isinstance(value, str) or not value):
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        companions = params.get("companion_metrics")
        if companions is not None and (
            not isinstance(companions, (list, tuple))
            or any(not isinstance(name, str) or not name for name in companions)
        ):
            problems.append(
                f"companion_metrics must be a list of capital-block key names "
                f"to render beside the declared return, got {companions!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        problems = []
        for port in EVIDENCE_PORTS:
            evidence = inputs.get(port)
            if evidence is not None and not isinstance(evidence, dict):
                problems.append(
                    f"{port} must be that stage's evidence dict when wired, "
                    f"got {evidence!r}"
                )
        survivors = inputs.get("survivors")
        if survivors is not None and not isinstance(survivors, (list, tuple)):
            problems.append(
                f"survivors must be the stat test's list of surviving "
                f"instruments when wired, got {survivors!r}"
            )
        lots = inputs.get("lots")
        if lots is not None and (isinstance(lots, bool) or not isinstance(lots, int)):
            problems.append(
                f"lots must be the integer count of lots deployed when wired, "
                f"got {lots!r}"
            )
        trades = inputs.get("trades")
        if trades is not None:
            if isinstance(trades, (str, bytes, dict)) or not isinstance(
                trades, (list, tuple)
            ):
                problems.append(
                    f"trades must be the fill ledger (a list of dicts) when "
                    f"wired, got {trades!r}"
                )
            elif any(not isinstance(entry, dict) for entry in trades):
                problems.append("trades entries must each be a dict (one fill)")
        decisions = inputs.get("decisions")
        if decisions is not None and not isinstance(decisions, dict):
            problems.append(
                f"decisions must be {{contract: {{...}}}} — the optimizer's "
                f"candidates with their q and price — got {decisions!r}"
            )
        capital = inputs.get("capital")
        if capital is not None and not isinstance(capital, dict):
            problems.append(
                f"capital must be the capital stage's metrics dict when wired, "
                f"got {capital!r}"
            )
        banked = inputs.get("banked")
        if banked is not None and not isinstance(banked, dict):
            problems.append(
                f"banked must be a counts dict ({{instrument: n}}) when wired, "
                f"got {banked!r}"
            )
        family = inputs.get("family")
        if family is not None and not isinstance(family, (list, tuple)):
            problems.append(
                f"family must be this round's eligible instruments (a list) "
                f"when wired, got {family!r}"
            )
        return problems

    # -- flags -------------------------------------------------------------

    def _flags(self, survivors, lots):
        """The findings. Kept apart from rendering so the condition is
        readable on its own and testable without an artifact dir."""
        flags = []
        if survivors is None or lots is None:
            # A flag that cannot be evaluated must SAY it cannot be
            # evaluated. Silence here is precisely the defect: the run
            # that shipped I-232 was silent because nobody had joined
            # these two numbers, not because they were equal.
            missing = [
                name
                for name, value in (("survivors", survivors), ("lots", lots))
                if value is None
            ]
            flags.append(
                {
                    "level": "note",
                    "code": "deploy-check-not-evaluable",
                    "message": (
                        f"survivors-vs-lots check NOT evaluated: {missing} not "
                        f"wired into this report node — wire both to make a "
                        f"zero-deployment run detectable"
                    ),
                }
            )
            return flags
        n = len(survivors)
        if n > 0 and lots == 0:
            flags.append(
                {
                    "level": "LOUD",
                    "code": "survivors-but-zero-lots",
                    "message": (
                        f"{n} instrument(s) SURVIVED the edge test and the run "
                        f"deployed ZERO lots ({sorted(survivors)}). This is "
                        f"either a real economic result (no candidate cleared "
                        f"its fee at any size) or a broken wire between the "
                        f"edge test and sizing. Read the sizing stage's "
                        f"entered-vs-lots and fee-net edge rows below before "
                        f"treating this run as healthy."
                    ),
                }
            )
        elif n == 0 and lots > 0:
            flags.append(
                {
                    "level": "LOUD",
                    "code": "zero-survivors-but-lots",
                    "message": (
                        f"NO instrument survived the edge test and the run "
                        f"still deployed {lots} lot(s) — capital moved on an "
                        f"edge that was not declared"
                    ),
                }
            )
        return flags

    # -- requirement 10: performance summary --------------------------------

    def _summary(self, replay, trades, rows, capital):
        """Performance, as ``(headline, metric rows, notes, flags)``.

        The rule this method exists to enforce: once capital is deposited
        into a running bankroll, ``final_bankroll`` STOPS being a
        performance number — a $1 deposit and a $1 profit move it
        identically. So the closing balance is rendered LABELLED as a
        balance, and the section leads on P&L and on the return metric
        the capital stage publishes. When money went in and no return
        metric came out, that is a note naming the key that was looked
        for, never a blank the reader might read as zero.
        """
        return_key = self.params.get("return_metric") or _DEFAULT_RETURN_METRIC
        deposits_key = self.params.get("deposits_metric") or _DEFAULT_DEPOSITS_METRIC
        companions = self.params.get("companion_metrics")
        if companions is None:
            companions = _DEFAULT_COMPANION_METRICS
        totals = replay.get("totals") if isinstance(replay, dict) else None
        totals = totals if isinstance(totals, dict) else {}
        capital = capital if isinstance(capital, dict) else {}

        net = _num(totals.get("net_pnl"))
        gross = _num(totals.get("gross_pnl"))
        final = _num(totals.get("final_bankroll"))
        deposited = _num(capital.get(deposits_key))
        returned = capital.get(return_key)
        fees_implied = None if (net is None or gross is None) else gross - net
        fees_ledger = _total(rows, "fee") if rows is not None else None
        realized = _total(rows, "pnl") if rows is not None else None
        rate, n_scored = _hit_rate(rows or [])
        drawdown, drawdown_src = self._drawdown(capital, replay)

        n_fills = sum(1 for r in (rows or []) if r.get("kind") != "reduce")
        n_reduces = sum(1 for r in (rows or []) if r.get("kind") == "reduce")

        metrics = [
            ("net P&L (after fees)", net, "replay.totals.net_pnl"),
            ("gross P&L (before fees)", gross, "replay.totals.gross_pnl"),
            ("fees paid (gross − net)", fees_implied, "replay.totals"),
            ("fees paid (Σ over trades)", fees_ledger, "trades"),
            ("realized P&L (Σ over trades)", realized, "trades"),
            ("trades — fills", n_fills if rows else None, "trades"),
            ("trades — reduces", n_reduces if rows else None, "trades"),
            (
                f"hit rate (of {n_scored} trade(s) carrying a P&L)",
                rate,
                "trades" if n_scored else "no trade carries a P&L field",
            ),
            ("max drawdown", drawdown, drawdown_src),
            (f"total deposited ({deposits_key})", deposited, "capital"),
            (f"RETURN — {return_key}", returned, "capital"),
            *(
                # The companion returns the capital stage publishes beside
                # the headline one — money-weighted, deposit-inflated,
                # deposit-stripped. DECLARED (``companion_metrics``), so a
                # stage that renames or adds one is a document edit. They
                # belong up here rather than in the stage dump because
                # comparing them to the declared return IS the read: when
                # total_return_naive sits visibly above twr, the gap is the
                # deposits, and that is the whole point of the section.
                (f"— {key}", capital.get(key), "capital")
                for key in companions
                if key in capital
            ),
            (
                "final bankroll — A BALANCE, NOT PERFORMANCE "
                "(deposits move it exactly like profits do)",
                final,
                "replay.totals.final_bankroll",
            ),
        ]
        # A LIST, not a dict: these rows have a reading order (P&L first,
        # the balance last and labelled) and ``_table`` sorts its keys.
        rendered = [
            {"metric": label, "value": value, "source": source}
            for label, value, source in metrics
        ]

        headline = "net P&L " + ("—" if net is None else f"{net:,.2f}")
        if returned is not None:
            headline += f" · {return_key} {_fmt(returned)}"
        else:
            headline += f" · {return_key} NOT WIRED"

        notes, flags = [], []
        if returned is None:
            message = (
                f"no return metric on the capital input: this report reads "
                f"`capital.{return_key}` (set by the `return_metric` param). "
                f"Until it is wired, P&L is the only performance number on "
                f"this page and the closing bankroll is NOT one — with "
                f"deposits flowing, a balance that rose may simply have been "
                f"paid into."
            )
            notes.append(message)
            flags.append(
                {
                    "level": "note",
                    "code": "return-metric-not-wired",
                    "message": message,
                }
            )
        if deposited:
            notes.append(
                f"{deposited:,.2f} was DEPOSITED during this run — compare "
                f"P&L and {return_key}, never the change in bankroll"
            )
        if (
            fees_implied is not None
            and fees_ledger is not None
            and abs(fees_implied - fees_ledger) > 0.005
        ):
            notes.append(
                f"fee cross-check: gross − net = {fees_implied:,.2f} but the "
                f"trade ledger sums to {fees_ledger:,.2f} — the difference is "
                f"fees charged outside the fill ledger (or a ledger gap), not "
                f"a second opinion on either number"
            )
        return headline, rendered, notes, flags

    @staticmethod
    def _drawdown(capital, replay=None):
        """``(max_drawdown, source)``.

        The producer's own number wins. Falling back to the equity curve
        is a READ of a series some other stage recorded — a running peak
        and the worst distance below it — never a re-simulation of the
        path that produced it. The curve is looked for on the capital
        input first and on the replay evidence second, because the loop
        records it and the two wirings are both legitimate.
        """
        stated = _num(capital.get("max_drawdown"))
        if stated is not None:
            return stated, "capital.max_drawdown"
        curve = capital.get("equity_curve")
        source = "capital.equity_curve"
        if not isinstance(curve, (list, tuple)) or not curve:
            curve = (replay or {}).get("equity_curve")
            source = "replay.equity_curve"
        if not isinstance(curve, (list, tuple)) or not curve:
            return None, "no equity curve or max_drawdown was wired"
        peak, worst = None, 0.0
        for point in curve:
            value = _num(point)
            if value is None:
                continue
            peak = value if peak is None else max(peak, value)
            worst = min(worst, value - peak)
        return worst, f"derived from {source} (running peak)"

    # -- requirement 11: the round-over-round family delta -------------------

    def _family_delta(self, banked, family):
        """Who NEWLY cleared the bar, who left, and who is closest.

        The previous round arrives as ``$prev`` params — carries are legal
        only in params, and ``carry.json`` already persists the gate's
        family and the bank's counts, so the durable record this needs
        exists the moment the document names it. ``None`` (the first run
        of a series) is reported as "no prior round", which is NOT the
        same statement as an empty prior family and must not render like
        one.
        """
        bar = self.params.get("min_events")
        prev_family = self.params.get("prev_family")
        prev_banked = self.params.get("prev_banked") or {}
        family = sorted(family)
        rows, notes, flags = {}, [], []

        if bar is None:
            notes.append(
                "min_events is not declared on this report node — the family "
                "delta names a bar, so it cannot be rendered without one. "
                "Declare the SAME bar the eligibility gate applies."
            )
        if prev_family is None:
            notes.append(
                "no prior round wired: this is the first run of the series, or "
                "params.prev_family / params.prev_banked are unset. Wire "
                '{"$prev": "<gate-node>.instruments", "default": null} and '
                '{"$prev": "<bank-node>.counts", "default": null} to make the '
                "round-over-round delta computable — carry.json already "
                "persists both."
            )
            entered, exited = [], []
        else:
            previous = set(prev_family)
            entered = sorted(set(family) - previous)
            exited = sorted(previous - set(family))
            for instrument in entered:
                rows[instrument] = {
                    "change": "ENTERED",
                    "events_now": banked.get(instrument),
                    "events_prev": prev_banked.get(instrument),
                    "bar": bar,
                }
            for instrument in exited:
                rows[instrument] = {
                    "change": "exited",
                    "events_now": banked.get(instrument),
                    "events_prev": prev_banked.get(instrument),
                    "bar": bar,
                }
            if exited:
                notes.append(
                    f"{len(exited)} market(s) LEFT the family — a count that "
                    f"falls means the counter's window moved (a trailing cut) "
                    f"or its input changed; it is not a settlement being "
                    f"un-banked"
                )

        pending = {}
        if bar is not None:
            in_family = set(family)
            for instrument, n in banked.items():
                if instrument in in_family:
                    continue
                count = n if isinstance(n, int) and not isinstance(n, bool) else 0
                pending[instrument] = {"events": count, "gap": max(0, bar - count)}
            mismatched = sorted(
                [i for i in family if (banked.get(i) or 0) < bar]
                + [
                    i
                    for i, n in banked.items()
                    if isinstance(n, int)
                    and not isinstance(n, bool)
                    and n >= bar
                    and i not in in_family
                ]
            )
            if mismatched and banked:
                flags.append(
                    {
                        "level": "LOUD",
                        "code": "family-bar-mismatch",
                        "message": (
                            f"this report names min_events={bar} but the family "
                            f"that actually ran disagrees on "
                            f"{len(mismatched)} market(s) ({mismatched[:10]}). "
                            f"Every family number on this page describes a "
                            f"different threshold from the gate's until the "
                            f"two are reconciled — fix the report's declared "
                            f"bar, never the gate's."
                        ),
                    }
                )
        return {
            "bar": bar,
            "n_family": len(family),
            "n_entered": len(entered),
            "n_exited": len(exited),
            "entered": entered,
            "exited": exited,
            "changes": rows,
            "pending": pending,
            "prior_round_wired": prev_family is not None,
            "notes": notes,
        }, flags

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def _render_summary(headline, metrics, notes, capital_block=None):
        lines = ["## Performance summary", "", f"**{headline}**", ""]
        lines += _fixed_table(("metric", "value", "source"), metrics)
        lines.append("")
        if capital_block:
            lines += ["the capital stage's own block, as published:", ""]
            lines += _fixed_table(
                ("key", "value"),
                [{"key": k, "value": v} for k, v in sorted(capital_block.items())],
            )
            lines.append("")
        lines += [f"- _{note}_" for note in notes]
        if notes:
            lines.append("")
        return lines

    def _render_trades(self, rows, missing, artifact_name, max_rows):
        lines = ["## Trades", "", f"{len(rows)} trade(s) recorded.", ""]
        if not rows:
            lines += [
                "_(no trade was recorded — read the replay stage's skip "
                "reasons below before reading a flat P&L as a flat result)_",
                "",
            ]
            return lines
        keyed_by, by_market = _by_market(rows)
        lines += [f"per {keyed_by} ({len(by_market)}):", ""]
        if keyed_by == "contract":
            lines += [
                "_(rolled up per CONTRACT, not per market — the ledger records "
                "no series on a fill)_",
                "",
            ]
        shown = dict(sorted(by_market.items(), key=lambda kv: str(kv[0]))[:max_rows])
        lines += _table(shown, keyed_by)
        lines.append("")
        lines += _truncation_note(by_market, len(shown), artifact_name)
        if len(by_market) > len(shown):
            lines.append("")
        columns = (
            "when",
            "venue",
            "series",
            "event",
            "contract",
            "kind",
            "side",
            "size",
            "price",
            "fee",
            "pnl",
        )
        lines += [f"the trades, in order ({len(rows)}):", ""]
        lines += _fixed_table(columns, rows[:max_rows])
        lines.append("")
        lines += _truncation_note(rows, min(max_rows, len(rows)), artifact_name)
        lines.append("")
        if missing:
            lines += [
                f"- _the trade ledger records no {sorted(missing)} — those "
                f"columns are BLANK because the producer never supplied them, "
                f"which is not the same as a zero_",
                "",
            ]
        return lines

    def _render_edge(self, edge, max_rows):
        totals = edge.get("totals") if isinstance(edge, dict) else None
        totals = totals if isinstance(totals, dict) else {}
        instruments = edge.get("instruments") if isinstance(edge, dict) else None
        instruments = instruments if isinstance(instruments, dict) else {}
        test = totals.get("test") or (
            "one-sided cluster bootstrap on the paired improvement "
            "(H0: mean improvement <= 0)"
        )
        unit = totals.get("independence_unit") or "event (the statistical cluster)"
        lines = [
            "## Edge test",
            "",
            f"- test: **{test}**",
            f"- independence unit: **{unit}** — resampled, never records",
            f"- alpha: {_fmt(totals.get('alpha'))} · "
            f"family correction: {_fmt(totals.get('correction'))} · "
            f"family size: {_fmt(totals.get('family_size'))}",
            f"- bootstrap replicates: {_fmt(totals.get('n_boot'))} · "
            f"seed: {_fmt(totals.get('seed'))}",
            f"- survivors: {_fmt(totals.get('n_survivors'))} "
            f"(uncorrected: {_fmt(totals.get('n_survivors_uncorrected'))}, "
            f"correction cost: {_fmt(totals.get('correction_cost'))})",
            "",
            "One hypothesis PER MARKET — nothing here is pooled across "
            "markets, leads or cells; the family correction borrows strength "
            "across the p-values while every market keeps its own decision.",
            "",
        ]
        if not instruments:
            lines += ["_(the edge stage reported no per-market rows)_", ""]
            return lines
        shown = dict(sorted(instruments.items(), key=lambda kv: str(kv[0]))[:max_rows])
        lines += [f"per market ({len(instruments)}):", ""]
        lines += _table(shown, "market")
        lines.append("")
        lines += _truncation_note(
            instruments, len(shown), "evidence.json under `stages.edge.instruments`"
        )
        if len(instruments) > len(shown):
            lines.append("")
        if not any(
            "ci_low" in row or "ci_high" in row
            for row in instruments.values()
            if isinstance(row, dict)
        ):
            lines += [
                "- _no bootstrap confidence interval was computed by the edge "
                "stage; the statistic column is the observed mean improvement "
                "and the p-value is the add-one bootstrap tail. This report "
                "does not compute an interval — a second answer to a question "
                "the test already answered is exactly what it must not do._",
                "",
            ]
        return lines

    def _render_family(self, delta, max_rows):
        lines = [
            "## Family — round over round",
            "",
            f"- bar: **>= {_fmt(delta['bar'])} usable events**",
            f"- in the family this round: {delta['n_family']}",
            f"- NEWLY entered: **{delta['n_entered']}** · exited: {delta['n_exited']}",
            "",
        ]
        if delta["changes"]:
            lines += _table(delta["changes"], "market")
            lines.append("")
        elif delta["prior_round_wired"]:
            lines += ["_(no market entered or left the family this round)_", ""]
        pending = delta["pending"]
        if pending:
            ordered = sorted(pending.items(), key=lambda kv: (kv[1]["gap"], str(kv[0])))
            shown = dict(ordered[:max_rows])
            lines += [f"closest to the bar ({len(pending)} pending):", ""]
            lines += _table(shown, "market")
            lines.append("")
            lines += _truncation_note(
                pending, len(shown), "evidence.json under `family.pending`"
            )
            if len(pending) > len(shown):
                lines.append("")
        lines += [f"- _{note}_" for note in delta["notes"]]
        if delta["notes"]:
            lines.append("")
        return lines

    def _render_decisions(self, rows, artifact_name, max_rows):
        entered = sum(1 for r in rows if (_num(r.get("lots")) or 0) > 0)
        priced = sum(1 for r in rows if _num(r.get("q")) is not None)
        lines = [
            "## Optimization — q vs price at the decision instant",
            "",
            f"- candidates reaching the optimizer: {len(rows)}",
            f"- priced by the model (q present): {priced}",
            f"- took lots: {entered}",
            "",
            "`price` and `q` are BOTH read at `decided_at` — the same instant "
            "the sizing decision was made. Neither is a settlement value and "
            "neither is read forward of that stamp. `edge = q − price`. Rows "
            "are ordered by |edge| so the largest disagreements read first; "
            "the ordering selects nothing, and every candidate is in the CSV.",
            "",
        ]
        if not rows:
            lines += ["_(no candidate reached the optimizer)_", ""]
            return lines
        columns = (
            "contract",
            "instrument",
            "event",
            "decided_at",
            "lead_frac",
            "price",
            "q",
            "edge",
            "fee_rate",
            "lots",
            "disposition",
        )
        lines += _fixed_table(columns, rows[:max_rows])
        lines.append("")
        lines += _truncation_note(rows, min(max_rows, len(rows)), artifact_name)
        lines.append("")
        return lines

    # -- execution ----------------------------------------------------------

    def run(self, ctx, inputs):
        params = self.params
        sections = tuple(params.get("sections", SECTIONS))
        max_rows = params.get("max_rows", _MAX_TABLE_ROWS)
        trades_artifact = params.get("trades_artifact") or "trades.csv"
        decisions_artifact = params.get("decisions_artifact") or "decisions.csv"

        survivors = inputs.get("survivors")
        survivors = None if survivors is None else list(survivors)
        lots = inputs.get("lots")
        stages = {
            port: inputs[port]
            for port in EVIDENCE_PORTS
            if isinstance(inputs.get(port), dict)
        }
        flags = self._flags(survivors, lots)

        payload = {
            "title": params.get("title", "") or self.key,
            "deployment": {
                "survivors": survivors,
                "n_survivors": None if survivors is None else len(survivors),
                "lots": lots,
            },
            "flags": flags,
            "stages": stages,
        }

        # -- requirement 8: the trades -----------------------------------
        # The ``trades`` PORT wins; a ``fills`` key on the replay stage's
        # evidence is the fallback. Both exist because the loop already
        # HAS the ledger — it writes every fill to its own artifact — and
        # the cheapest way for it to reach this report is the evidence
        # dict it already returns. Neither path reconstructs a trade.
        trades = inputs.get("trades")
        if trades is None:
            carried = (stages.get("replay") or {}).get("fills")
            if isinstance(carried, (list, tuple)) and all(
                isinstance(entry, dict) for entry in carried
            ):
                trades = list(carried)
        rows = None if trades is None else _trade_rows(trades)
        missing_columns = ()
        if rows is not None:
            missing_columns = (
                tuple(
                    name
                    for name in _TRADE_REQUIRED
                    if all(row.get(name) is None for row in rows)
                )
                if rows
                else ()
            )
            rollup_key, rollup = _by_market(rows)
            payload["trades"] = {
                "n": len(rows),
                "rolled_up_by": rollup_key,
                "by_market": rollup,
                "columns_not_recorded": list(missing_columns),
                "artifact": trades_artifact,
            }
            self.write_artifact_text(
                ctx, trades_artifact, _csv_text(("when", *TRADE_COLUMNS), rows)
            )
            if missing_columns:
                flags.append(
                    {
                        "level": "note",
                        "code": "trade-columns-not-recorded",
                        "message": (
                            f"the trade ledger records no {list(missing_columns)} "
                            f"— those columns render blank, which is NOT a zero. "
                            f"The producing stage has to carry them onto each "
                            f"fill for the trade list to be complete."
                        ),
                    }
                )

        # -- requirement 10: performance ---------------------------------
        # Only when there is money to report on. A predict-only pipeline
        # has no performance, and a table of dashes claiming otherwise is
        # noise a reader has to learn to skip — which is how a real blank
        # eventually gets skipped too.
        replay_totals = (stages.get("replay") or {}).get("totals")
        # Same seam once more: the ``capital`` PORT wins, and a ``returns``
        # key on the replay evidence is the fallback — a replay loop
        # already builds that whole flow-aware ``capital_returns`` block,
        # and it is the named output that has to replace the closing
        # bankroll.
        capital = inputs.get("capital")
        dedicated = isinstance(capital, dict)
        if not dedicated:
            carried = (stages.get("replay") or {}).get("returns")
            if isinstance(carried, dict):
                capital, dedicated = carried, True
            elif (
                isinstance(replay_totals, dict)
                and (params.get("return_metric") or _DEFAULT_RETURN_METRIC)
                in replay_totals
            ):
                # The shipping shape: the replay flattens its return block
                # into its own totals. Read it there, but do NOT re-render
                # it as a "capital block" — the stage section below already
                # prints totals in full, and one table twice trains a
                # reader to skim both.
                capital = replay_totals
            else:
                capital = None
        has_performance = (
            bool(replay_totals) or trades is not None or isinstance(capital, dict)
        )
        summary_notes = []
        if "summary" in sections and has_performance:
            headline, metrics, summary_notes, summary_flags = self._summary(
                stages.get("replay"), trades, rows, capital
            )
            flags.extend(summary_flags)
            payload["summary_metrics"] = metrics
            payload["headline"] = headline
            # Everything the capital stage published, verbatim and
            # unfiltered. The declared return is promoted into the table
            # above; this keeps mwr, trading_pnl, total_return_naive and
            # anything that stage adds LATER from being dropped by a
            # reader that only knows today's key names.
            if dedicated and capital:
                payload["capital_block"] = {
                    k: v for k, v in capital.items() if not isinstance(v, (list, dict))
                }

        # -- requirement 11: the family delta ----------------------------
        banked = inputs.get("banked")
        family = inputs.get("family")
        delta = None
        if "family" in sections and (banked is not None or family is not None):
            delta, family_flags = self._family_delta(banked or {}, family or [])
            flags.extend(family_flags)
            payload["family"] = delta

        # -- requirement 12: the optimizer's q vs price ------------------
        # Same seam as the trades: the ``decisions`` PORT wins, and a
        # ``candidates`` key on the sizing stage's evidence is the
        # fallback — the sizer builds exactly that table on its way to the
        # solver (it is the only place the belief and the price it was
        # formed against exist together) and writes it to its own
        # artifact. Reading it here copies it; it does not re-derive it.
        decisions = inputs.get("decisions")
        if decisions is None:
            carried = (stages.get("sizing") or {}).get("candidates")
            if isinstance(carried, dict):
                decisions = carried
        decision_rows = None
        if decisions is not None:
            decision_rows = _decision_rows(decisions)
            payload["decisions"] = {
                "n": len(decision_rows),
                "n_priced": sum(
                    1 for r in decision_rows if _num(r.get("q")) is not None
                ),
                "artifact": decisions_artifact,
                "rows": decision_rows,
            }
            self.write_artifact_text(
                ctx,
                decisions_artifact,
                _csv_text(
                    ("contract", "decided_at", *DECISION_COLUMNS[1:]), decision_rows
                ),
            )

        path = self.write_artifact(ctx, "evidence.json", payload)

        # -- the human read ----------------------------------------------
        title = payload["title"]
        lines = [f"# {title}", ""]
        loud = [f for f in flags if f["level"] == "LOUD"]
        if loud:
            lines += ["## LOUD", ""]
            lines += [f"- **{f['code']}** — {f['message']}" for f in loud]
            lines.append("")
        quiet = [f for f in flags if f["level"] != "LOUD"]
        if quiet:
            lines += ["## Notes", ""]
            lines += [f"- {f['code']} — {f['message']}" for f in quiet]
            lines.append("")
        if "headline" in payload:
            lines += self._render_summary(
                payload["headline"],
                payload["summary_metrics"],
                summary_notes,
                payload.get("capital_block"),
            )
        lines += [
            "## Deployment",
            "",
            f"- survivors: {'—' if survivors is None else len(survivors)}"
            + (f" ({sorted(survivors)})" if survivors else ""),
            f"- lots deployed: {'—' if lots is None else lots}",
            "",
        ]
        if "trades" in sections and rows is not None:
            lines += self._render_trades(
                rows, missing_columns, trades_artifact, max_rows
            )
        if "edge_test" in sections and "edge" in stages:
            lines += self._render_edge(stages["edge"], max_rows)
        if delta is not None:
            lines += self._render_family(delta, max_rows)
        if "decisions" in sections and decision_rows is not None:
            lines += self._render_decisions(decision_rows, decisions_artifact, max_rows)
        if "stages" in sections:
            if stages:
                # Keys a dedicated section above already rendered in full.
                promoted = {
                    "replay": tuple(
                        key
                        for key, used in (
                            ("fills", rows is not None),
                            ("returns", "capital_block" in payload),
                        )
                        if used
                    ),
                    "sizing": ("candidates",) if decision_rows is not None else (),
                }
                lines += ["## Stages", ""]
                for port in EVIDENCE_PORTS:
                    if port in stages:
                        lines += _render_stage(
                            port, stages[port], max_rows, promoted.get(port, ())
                        )
            else:
                lines += ["_(no stage evidence was wired into this report)_", ""]
        self.write_artifact_text(ctx, "evidence.md", "\n".join(lines) + "\n")

        for flag in flags:
            log = self.log.warning if flag["level"] == "LOUD" else self.log.info
            log("%s: %s", flag["code"], flag["message"])
        self.log.info(
            "run report: %d stage(s), %d flag(s) (%d loud) -> %s",
            len(stages),
            len(flags),
            len(loud),
            path,
        )
        return {
            "path": path,
            "summary": {
                "stages": len(stages),
                "flags": len(flags),
                "loud": len(loud),
            },
            "flags": flags,
        }


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

#: The kinds this module ships, in registration order.
_KINDS = (("run-report", RunReport),)


def register(registry=None) -> None:
    """Claim ``run-report`` in ``registry`` (default
    :data:`~dskit.pipeline.node.DEFAULT_NODE_KINDS`) as ``owned=True``.
    Idempotent: a name already present is SKIPPED, never shadowed.
    Called by the orchestrator, never at import time.
    """
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in _KINDS:
        if name not in registry:
            registry.register(name, cls, owned=True)
