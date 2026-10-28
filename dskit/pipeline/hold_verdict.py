"""A hold-or-exit verdict per holding, written as one markdown file (ADR-0279 amendment, 2026-10-28).

:class:`~dskit.pipeline.position_summary.PositionSummary` reduces a decision run to a few
numbers per holding and for the portfolio. An owner who has just pulled a snapshot wants
those numbers as one page: which holdings cost money to keep, by how much a year, what
leaving would cost today, and what return an alternative must clear. :class:`HoldVerdict`
writes that page and nothing else; every number is read from its inputs, none is computed
except the display (percentages to one decimal, a beta to two, nulls as an em dash).

The page: a title line with the run's as-of day; one table, one row per holding, sorted by
opportunity cost descending (a null opportunity cost sorts last, ties keep input order);
a portfolio block; a three-line key. A holding whose opportunity cost is null (the active
hurdle was withheld, so the decision run had no admissible alternative) reads
``n/a — no admissible alternative`` instead of HOLD or EXIT.

The portfolio block reads PositionSummary's ``metrics`` dict when it is wired to the
``portfolio`` port (:data:`PORTFOLIO_FIELDS` are the keys read; a test pins them to what
PositionSummary emits). The port is optional: when it is absent the block says so and
nothing is recomputed. Restating PositionSummary's concentration rule (by symbol, across
accounts) from the rows here would be a second owner of it, so a document that wants the
block wires the metrics.

The write is :class:`~dskit.pipeline.kinds_table.RecordsWrite`'s, unchanged: no clobber
without ``overwrite``, no created directories, an atomic write, ``provenance`` and
``metrics`` carrying the digest of the exact bytes. The path carries exactly one ``{run}``
in its file stem, expanded to the run directory's name by
:class:`~dskit.pipeline.kinds_run_write.RecordsWriteRun`'s own rule, so each run writes its
own page.

Import cost: stdlib + the pipeline core.
"""

from __future__ import annotations

import copy
from typing import NamedTuple

from dskit.pipeline.decision_outcomes import check_key_fields_param
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.kinds_run_write import RUN_PLACEHOLDER, RecordsWriteRun
from dskit.pipeline.kinds_table import RecordsWrite
from dskit.pipeline.liquidation import check_name_params
from dskit.pipeline.records import number_ok

__all__ = ["DISPLAY_FIELDS", "NULLABLE_FIELDS", "PORTFOLIO_FIELDS", "VERDICTS", "HoldVerdict"]

#: The per-row field-name params, in column order after the holding.
DISPLAY_FIELDS = (
    "weight_field", "expected_return_field", "volatility_field", "beta_field",
    "embedded_gain_field", "tax_cost_field", "break_even_field", "opportunity_cost_field",
)
#: The display fields whose value may be null: no table wired, or no admissible hurdle.
NULLABLE_FIELDS = ("volatility_field", "beta_field", "opportunity_cost_field")
#: The decision vocabulary and how each reads (a test pins it to PositionSummary's).
VERDICTS = {"hold": "HOLD", "exit": "EXIT"}
#: What a holding with no admissible alternative reads instead of a verdict.
NO_ALTERNATIVE = "n/a — no admissible alternative"

#: The PositionSummary ``metrics`` keys the portfolio block reads, and those that may be null.
PORTFOLIO_FIELDS = (
    "total_value", "expected_return", "weighted_volatility", "beta", "hhi",
    "effective_holdings", "top_symbol", "top_weight", "tax_cost", "opportunity_cost",
    "exit_positions", "hold_positions",
)
_PORTFOLIO_NULLABLE = ("weighted_volatility", "beta", "opportunity_cost")
_PORTFOLIO_TEXT = ("top_symbol",)

_NULL = "—"
_COLUMNS = ("Holding", "Weight", "Expected return", "Vol", "Beta", "Embedded gain",
            "Tax to sell", "Break-even", "Opportunity cost/yr", "Verdict")
#: The one display column that is not a rate: a beta prints as a plain number.
_BETA_PARAM = "beta_field"
_KEY_SEPARATOR = " / "
_KEY_LINES = (
    "Opportunity cost > 0: the after-tax alternative beats holding by that much per year.",
    "Break-even: the after-tax return an alternative must clear to tie.",
    "Tax to sell: what leaving costs today.",
)
_NOT_WIRED = ("Not wired: wire the summary's portfolio metrics to `portfolio` for the "
              "totals, concentration and the cost of selling everything.")


def _percent(value):
    """``value`` (a fraction) as a percent to one decimal; null as the em dash; never ``-0.0%``."""
    if value is None:
        return _NULL
    text = f"{value * 100.0:.1f}"
    return ("0.0" if text == "-0.0" else text) + "%"


def _plain(value, places):
    """``value`` to ``places`` decimals; null as the em dash."""
    return _NULL if value is None else f"{value:.{places}f}"


def _cell(text):
    """``text`` safe inside a markdown table cell (a ``|`` would split it)."""
    return str(text).replace("|", "\\|")


class _Frame(NamedTuple):
    """What a render needs besides the rows: the run's as-of day and the portfolio (or None)."""

    day: str
    portfolio: dict | None


class HoldVerdict(RecordsWrite):
    """Write one markdown verdict page from PositionSummary rows and metrics.

    Role ``report``. See the module docstring for the page.

    Parameters
    ----------
    params : dict
        :class:`~dskit.pipeline.kinds_table.RecordsWrite`'s ``path`` / ``source`` /
        ``overwrite`` / ``expect``, ``path`` carrying exactly one ``{run}`` in its file stem
        (:meth:`~dskit.pipeline.kinds_run_write.RecordsWriteRun.path_problems`). REQUIRED,
        none defaulted: ``title`` (non-empty string); ``key_fields`` (non-empty list of
        distinct field names, joined with `` / `` as the Holding cell); and one field name
        per :data:`DISPLAY_FIELDS` entry plus ``decision_field``.

    Inputs
    ------
    ``records`` (REQUIRED)
        Summary rows, one per holding, each carrying every declared field.
    ``portfolio`` (optional)
        PositionSummary's ``metrics`` dict; absent, the block says it is not wired.

    Outputs
    -------
    ``path``, ``provenance``, ``metrics``
        As :class:`~dskit.pipeline.kinds_table.RecordsWrite`; ``rows`` counts holdings.

    Examples
    --------
    One page per run::

        node = HoldVerdict("verdict", {"path": "report/{run}.md", "source": "hold verdict",
            "title": "Hold verdict", "key_fields": ["account", "symbol"],
            "weight_field": "weight", ..., "decision_field": "decision"})
        out = node.run(ctx, {"records": summary_rows, "portfolio": summary_metrics})
    """

    _PARAMS = RecordsWrite._PARAMS + ("title", "key_fields", *DISPLAY_FIELDS, "decision_field")
    _REQUIRED = ("title", "key_fields", *DISPLAY_FIELDS, "decision_field")
    _PORTFOLIO = "portfolio"

    #: Set only on the per-run copy :meth:`run` renders from.
    _frame = None

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none."""
        problems = super().validate_params(params)
        missing = sorted(set(cls._REQUIRED) - set(params))
        if missing:
            problems.append(f"missing required params {missing}")
        title = params.get("title")
        if "title" in params and not is_node_ref(title) and not (
                isinstance(title, str) and title.strip()):
            problems.append(f"title must be a non-empty string, got {title!r}")
        check_key_fields_param(problems, params)
        check_name_params(problems, params, (*DISPLAY_FIELDS, "decision_field"))
        path = params.get("path")
        if isinstance(path, str) and path and not is_node_ref(path):
            problems += RecordsWriteRun.path_problems(path)
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Answer ``pure``: the node reads nothing but its inputs and params."""
        return "pure"

    def validate_inputs(self, inputs):
        """List problems with the wired ports: the rows' container and the portfolio's shape."""
        problems = super().validate_inputs(inputs)
        inputs = inputs or {}
        if self._PORTFOLIO in inputs and not isinstance(inputs[self._PORTFOLIO], dict):
            problems.append(f"portfolio must be a mapping (PositionSummary's metrics), "
                            f"got {inputs[self._PORTFOLIO]!r}")
        return problems

    def run(self, ctx, inputs):
        """Check the ports, expand ``{run}``, and write the page through RecordsWrite."""
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"{self.key}: {'; '.join(problems)}")
        name = RecordsWriteRun.run_name(ctx)
        day = str(getattr(ctx, "asof", "") or "")[:10]
        if not day:
            raise ValueError(f"{self.key}: the title line needs the run's as-of day (ctx.asof)")
        framed = copy.copy(self)
        framed.params = {**self.params, "path": self.params["path"].replace(RUN_PLACEHOLDER, name)}
        framed._frame = _Frame(day, self._portfolio(inputs.get(self._PORTFOLIO)))
        return super(HoldVerdict, framed).run(ctx, inputs)

    def render(self, records):
        """Return the page as UTF-8 bytes, plus the holding count."""
        if self._frame is None:
            raise ValueError(f"{self.key}: render needs the run frame; call run()")
        rows = self._rows(records)
        lines = [f"# {self.params['title']} — {self._frame.day}", ""]
        lines += self._table(rows) + [""]
        lines += self._portfolio_lines(self._frame.portfolio) + [""]
        lines += ["## Key", ""] + [f"- {line}" for line in _KEY_LINES]
        return ("\n".join(lines) + "\n").encode("utf-8"), len(rows)

    def _value(self, where, row, field, nullable):
        """Return ``row[field]``: a number, or None when ``nullable``; refuse anything else."""
        if field not in row:
            raise ValueError(f"{self.key}: {where} has no field {field!r}")
        value = row[field]
        if value is None and nullable:
            return None
        if not number_ok(value):
            what = "a number or null" if nullable else "a number"
            raise ValueError(f"{self.key}: {where} field {field!r} must be {what}, got {value!r}")
        return float(value)

    def _row(self, index, row):
        """Return one row's cells: the holding, the display numbers by param, the decision."""
        p, where = self.params, f"row {index}"
        if not isinstance(row, dict):
            raise ValueError(f"{self.key}: {where} must be a mapping, got {row!r}")
        for field in p["key_fields"]:
            if field not in row:
                raise ValueError(f"{self.key}: {where} has no field {field!r}")
        numbers = {name: self._value(where, row, p[name], name in NULLABLE_FIELDS)
                   for name in DISPLAY_FIELDS}
        field = p["decision_field"]
        if field not in row:
            raise ValueError(f"{self.key}: {where} has no field {field!r}")
        if row[field] not in VERDICTS:
            raise ValueError(f"{self.key}: {where} field {field!r} must be one of "
                             f"{sorted(VERDICTS)}, got {row[field]!r}")
        holding = _KEY_SEPARATOR.join(str(row[k]) for k in p["key_fields"])
        return {"holding": holding, "numbers": numbers, "decision": row[field]}

    def _rows(self, records):
        """Read every row and sort by opportunity cost descending, nulls last, ties stable."""
        if not records:
            raise ValueError(f"{self.key}: records carry no rows: there is no holding to report")
        rows = [self._row(index, row) for index, row in enumerate(records)]
        return sorted(rows, key=lambda r: (r["numbers"]["opportunity_cost_field"] is None,
                                           -(r["numbers"]["opportunity_cost_field"] or 0.0)))

    def _table(self, rows):
        """Return the table's lines: header, alignment, one line per holding."""
        lines = ["| " + " | ".join(_COLUMNS) + " |",
                 "|---|" + "---:|" * (len(_COLUMNS) - 2) + "---|"]
        for row in rows:
            numbers = row["numbers"]
            cells = [_cell(row["holding"])]
            cells += [_plain(numbers[name], 2) if name == _BETA_PARAM else _percent(numbers[name])
                      for name in DISPLAY_FIELDS]
            admissible = numbers["opportunity_cost_field"] is not None
            cells.append(VERDICTS[row["decision"]] if admissible else NO_ALTERNATIVE)
            lines.append("| " + " | ".join(cells) + " |")
        return lines

    def _portfolio(self, portfolio):
        """Return the portfolio's fields read and checked, or None when the port is absent."""
        if portfolio is None:
            return None
        out = {}
        for field in PORTFOLIO_FIELDS:
            if field in _PORTFOLIO_TEXT:
                if field not in portfolio:
                    raise ValueError(f"{self.key}: portfolio has no field {field!r}")
                out[field] = portfolio[field]
            else:
                out[field] = self._value("portfolio", portfolio, field,
                                         field in _PORTFOLIO_NULLABLE)
        return out

    def _portfolio_lines(self, portfolio):
        """Return the portfolio block's lines; one line saying so when it is not wired."""
        lines = ["## Portfolio", ""]
        if portfolio is None:
            return lines + [_NOT_WIRED]
        total = portfolio["total_value"]
        lines.append(f"- Total value: {total:,.2f}")
        lines.append(f"- Weighted expected return: {_percent(portfolio['expected_return'])} a year")
        if portfolio["weighted_volatility"] is not None:
            lines.append("- Weighted volatility (an upper bound): "
                         f"{_percent(portfolio['weighted_volatility'])}")
        if portfolio["beta"] is not None:
            lines.append(f"- Weighted beta: {_plain(portfolio['beta'], 2)}")
        lines.append(f"- Concentration: HHI {portfolio['hhi']:.3f}, effective holdings "
                     f"{portfolio['effective_holdings']:.1f}, top holding "
                     f"{_cell(portfolio['top_symbol'])} at {_percent(portfolio['top_weight'])} of value")
        tax = portfolio["tax_cost"]
        lines.append(f"- Tax to sell all: {_percent(tax)} of value ({tax * total:,.2f})")
        cost = portfolio["opportunity_cost"]
        if cost is None:
            lines.append(f"- Sell all vs hold all: {NO_ALTERNATIVE}")
        else:
            wins = "selling all wins" if cost > 0.0 else "holding all wins"
            lines.append(f"- Sell all vs hold all: opportunity cost {_percent(cost)} a year ({wins})")
        lines.append(f"- Verdicts: {int(portfolio['exit_positions'])} {VERDICTS['exit']}, "
                     f"{int(portfolio['hold_positions'])} {VERDICTS['hold']}")
        return lines
