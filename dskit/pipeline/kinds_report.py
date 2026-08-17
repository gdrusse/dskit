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
exit-code contract (0 ran / 3 NO-GO / 1 error).

Import cost: stdlib only.
"""

from __future__ import annotations

from dskit.pipeline.node import DEFAULT_NODE_KINDS, Node

__all__ = ["EVIDENCE_PORTS", "RunReport", "register"]

#: The stage ports, in the order a run executes them — which is also the
#: order the report renders. Every one is OPTIONAL: a document that has no
#: sizing stage yet still gets a report of the stages it does have.
EVIDENCE_PORTS = ("training", "validation", "edge", "sizing", "replay")


def _reject_unknown(problems, params, allowed):
    """Default-deny on this class's own knobs (the sibling kind modules
    carry the same helper; a typo'd knob silently ignored is a config
    lie)."""
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        problems.append(f"unknown param(s) {unknown} — allowed: {sorted(allowed)}")


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


#: Rows of one table rendered into the markdown before it is truncated with
#: a pointer to the JSON. The read has to stay a read; the RECORD is never
#: truncated.
_MAX_TABLE_ROWS = 25


def _is_row_table(value):
    """``True`` for a non-empty ``{key: {field: ...}}`` — a table of rows,
    not a scalar and not a bag of values."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(row, dict) for row in value.values())
    )


def _render_stage(port, evidence):
    """One stage's markdown section."""
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
        if len(rows) > _MAX_TABLE_ROWS:
            # Truncating the RENDER, never the record: evidence.json holds
            # every row, and the reader is told where to find them.
            head = dict(
                sorted(rows.items(), key=lambda kv: str(kv[0]))[:_MAX_TABLE_ROWS]
            )
            lines += _table(head, key)
            lines.append("")
            lines.append(
                f"_… {len(rows) - _MAX_TABLE_ROWS} more row(s) — the full table "
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

    Inputs (ALL optional, so a partial pipeline still reports on itself):
    ``training``, ``validation``, ``edge``, ``sizing``, ``replay`` — each
    one stage's evidence dict (see the module docstring for the
    convention); plus ``survivors`` (the edge test's surviving
    instruments) and ``lots`` (the lots the sizing stage actually
    deployed).

    Those last two are the LOUD flag's operands and are wired DIRECTLY
    from the producing nodes' real outputs rather than read out of an
    evidence dict — so the flag cannot be silenced by an evidence payload
    going missing. The failure mode being fixed is exactly "the number
    was computed and then nobody looked at it".

    Params: ``title`` — optional heading for the rendered report.

    Writes ``evidence.json`` (the whole structured record) and
    ``evidence.md`` (the human read) into this node's artifact dir.

    Outputs: ``path`` — the JSON artifact; ``summary`` — ``{"stages": k,
    "flags": n, "loud": m}``; ``flags`` — the findings list, which the
    driver lifts to the top of ``report.md``.
    """

    role = "report"
    outputs = ("path", "summary", "flags")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("title",)

    @classmethod
    def validate_params(cls, params):
        problems = []
        _reject_unknown(problems, params, cls._PARAMS)
        title = params.get("title", "")
        if not isinstance(title, str):
            problems.append(f"title must be a string, got {title!r}")
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
        return problems

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

    def run(self, ctx, inputs):
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
            "title": self.params.get("title", "") or self.key,
            "deployment": {
                "survivors": survivors,
                "n_survivors": None if survivors is None else len(survivors),
                "lots": lots,
            },
            "flags": flags,
            "stages": stages,
        }
        path = self.write_artifact(ctx, "evidence.json", payload)

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
        lines += [
            "## Deployment",
            "",
            f"- survivors: {'—' if survivors is None else len(survivors)}"
            + (f" ({sorted(survivors)})" if survivors else ""),
            f"- lots deployed: {'—' if lots is None else lots}",
            "",
        ]
        if stages:
            lines += ["## Stages", ""]
            for port in EVIDENCE_PORTS:
                if port in stages:
                    lines += _render_stage(port, stages[port])
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
