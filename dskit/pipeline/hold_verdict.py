"""A hold-or-exit verdict per holding, written as one markdown file (ADR-0279 amendment, 2026-10-28).

:class:`~dskit.pipeline.position_summary.PositionSummary` reduces a decision run to a few
numbers per holding and for the portfolio. An owner who has just pulled a snapshot wants
those numbers as one page: which holdings cost money to keep, by how much a year, what
leaving would cost today, and what return an alternative must clear. :class:`HoldVerdict`
writes that page and nothing else. Every number is read from its inputs; the one product it
forms is the portfolio's tax to sell in currency (its share of value times the total value),
and the rest is display (percentages to one decimal, a beta to two, nulls as an em dash).
The page says what its total covers: the required ``universe`` param is the label's words
("Total value (listed equity longs)"), because a total over a filtered snapshot reads as the
account's value unless it says otherwise.

The page: a title line with the decisions' as-of day, read from the rows themselves (every
row carries ``as_of_field``; several days refuse, and the run's own ``asof`` is never used);
one table, one row per holding, sorted by opportunity cost descending (a null opportunity
cost sorts last, ties keep input order); a portfolio block; a key. A holding whose
opportunity cost is null (the active hurdle was withheld, or the decision run declared no
alternative at all, so it had no admissible alternative) reads ``n/a — no admissible alternative`` instead of HOLD or EXIT,
and so do the portfolio's sell-all comparison and its verdict counts. A row's decision must
agree with the sign of its opportunity cost (exit needs more than zero; hold needs zero, less
or null, so a tie holds): a disagreement refuses by index, because the page prints both.

A text cell (the holding, the top symbol) goes through :func:`dskit.pipeline.runs.render_cell`,
the pipeline's one owner of cell text: a ``|`` is escaped, a line break reads as ``⏎`` and a
cell past its length limit is cut with an ellipsis, so a value cannot open a phantom column or
row. (Numbers are not its: it prints a float to six figures, the page wants a percent.)

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
from dskit.pipeline.document import date_problem, is_node_ref
from dskit.pipeline.kinds_run_write import RUN_PLACEHOLDER, RecordsWriteRun
from dskit.pipeline.kinds_table import RecordsWrite
from dskit.pipeline.liquidation import check_name_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.runs import render_cell

__all__ = ["DISPLAY_FIELDS", "NO_ALTERNATIVE", "NULLABLE_FIELDS", "PORTFOLIO_FIELDS", "VERDICTS",
           "HoldVerdict"]

#: The per-row field-name params, in column order after the holding.
DISPLAY_FIELDS = (
    "weight_field", "expected_return_field", "volatility_field", "beta_field",
    "embedded_gain_field", "tax_cost_field", "break_even_field", "opportunity_cost_field",
)
#: The display fields whose value may be null: no table wired, or no admissible hurdle.
NULLABLE_FIELDS = ("volatility_field", "beta_field", "opportunity_cost_field")
_HOLD, _EXIT = "hold", "exit"
#: The decision vocabulary and how each reads (a test pins it to PositionSummary's).
VERDICTS = {_HOLD: "HOLD", _EXIT: "EXIT"}
#: What a holding with no admissible alternative (withheld or none declared) reads instead of a verdict.
NO_ALTERNATIVE = "n/a — no admissible alternative"

#: The PositionSummary ``metrics`` keys the portfolio block reads, and those that may be null.
PORTFOLIO_FIELDS = (
    "total_value", "expected_return", "weighted_volatility", "beta", "hhi",
    "effective_holdings", "top_symbol", "top_weight", "tax_cost", "opportunity_cost",
    "exit_positions", "hold_positions",
)
_PORTFOLIO_NULLABLE = ("weighted_volatility", "beta", "opportunity_cost")
_PORTFOLIO_TEXT = ("top_symbol",)

#: The params that are free text on the page: the title line and the total's label.
_TEXT_PARAMS = ("title", "universe")

_NULL = "—"
_COLUMNS = ("Holding", "Weight", "Expected return", "Vol", "Beta", "Embedded gain",
            "Tax to sell", "Break-even", "Opportunity cost/yr", "Verdict")
#: The one display column that is not a rate: a beta prints as a plain number.
_BETA_PARAM = "beta_field"
#: The param naming the opportunity cost field: the sort key, the n/a test and the sign check.
_COST_PARAM = "opportunity_cost_field"
_KEY_SEPARATOR = " / "
#: The key under the table. Break-even is PositionSummary's ``redeploy_return`` that ties the two
#: paths: the same pre-cost, pre-tax basis as the hurdle (the declared cost and tax come off it).
_KEY_LINES = (
    "Opportunity cost > 0: the after-tax alternative beats holding by that much per year.",
    "Break-even: the annual return an alternative must earn, before its own cost and tax "
    "(same basis as the hurdle), for exiting to tie holding.",
    "Expected return is before tax.",
    "Embedded gain is a share of value, not of cost.",
    "Tax to sell is a share of value; 'tax to sell all' ignores loss offsets unless "
    "loss_offset is true.",
)
_NOT_WIRED = ("Not wired: wire the summary's portfolio metrics to `portfolio` for the "
              "totals, concentration and the cost of selling everything.")


def _plain(value, places):
    """``value`` to ``places`` decimals; null as the em dash; a value that rounds to zero is unsigned."""
    if value is None:
        return _NULL
    text = f"{value:.{places}f}"
    return text[1:] if text.startswith("-") and float(text) == 0.0 else text


def _percent(value):
    """``value`` (a fraction) as a percent to one decimal; null as the em dash; never ``-0.0%``."""
    return _NULL if value is None else _plain(value * 100.0, 1) + "%"


class _Frame(NamedTuple):
    """What a render needs besides the rows: the checked portfolio metrics, or None when unwired."""

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
        none defaulted: ``title`` and ``universe`` (non-empty strings; the universe is what the
        total covers, shown as ``Total value (<universe>)``); ``key_fields`` (non-empty list of
        distinct field names, joined with `` / `` as the Holding cell); ``as_of_field`` (the
        field every row carries the decision day in, an ISO date; the title shows it);
        and one field name per :data:`DISPLAY_FIELDS` entry plus ``decision_field``.

    Inputs
    ------
    ``records`` (REQUIRED)
        Summary rows, one per holding, each carrying every declared field. The summary's own
        ``key_fields`` may name the as-of field (``["account", "symbol", "as_of"]``): a key
        field rides along onto its rows, so the day needs no second port.
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
            "title": "Hold verdict", "universe": "listed equity longs",
            "key_fields": ["account", "symbol"], "as_of_field": "as_of",
            "weight_field": "weight", ..., "decision_field": "decision"})
        out = node.run(ctx, {"records": summary_rows, "portfolio": summary_metrics})
    """

    _PARAMS = RecordsWrite._PARAMS + ("title", "universe", "key_fields", "as_of_field",
                                      *DISPLAY_FIELDS, "decision_field")
    _REQUIRED = ("title", "universe", "key_fields", "as_of_field", *DISPLAY_FIELDS,
                 "decision_field")
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
        for name in _TEXT_PARAMS:
            text = params.get(name)
            if name in params and not is_node_ref(text) and not (
                    isinstance(text, str) and text.strip()):
                problems.append(f"{name} must be a non-empty string, got {text!r}")
        check_key_fields_param(problems, params)
        check_name_params(problems, params, ("as_of_field", *DISPLAY_FIELDS, "decision_field"))
        path = params.get("path")
        if isinstance(path, str) and path and not is_node_ref(path):
            problems += RecordsWriteRun.path_problems(path)
        return problems

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
        framed = copy.copy(self)
        framed.params = {**self.params, "path": self.params["path"].replace(RUN_PLACEHOLDER, name)}
        framed._frame = _Frame(self._portfolio(inputs.get(self._PORTFOLIO)))
        return super(HoldVerdict, framed).run(ctx, inputs)

    def render(self, records):
        """Return the page as UTF-8 bytes, plus the holding count."""
        if self._frame is None:
            raise ValueError(f"{self.key}: render needs the run frame; call run()")
        rows = self._rows(records)
        lines = [f"# {self.params['title']} — {self._day_of(rows)}", ""]
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
        day = self._day(where, row)
        numbers = {name: self._value(where, row, p[name], name in NULLABLE_FIELDS)
                   for name in DISPLAY_FIELDS}
        decision = self._decision(where, row, numbers[_COST_PARAM])
        holding = _KEY_SEPARATOR.join(str(row[k]) for k in p["key_fields"])
        return {"holding": holding, "day": day, "numbers": numbers, "decision": decision}

    def _day(self, where, row):
        """Return the row's decision day; refuse a missing field or anything but a real ISO date."""
        field = self.params["as_of_field"]
        if field not in row:
            raise ValueError(f"{self.key}: {where} has no field {field!r}")
        if date_problem(row[field]):
            raise ValueError(f"{self.key}: {where} field {field!r} must be an ISO date "
                             f"(YYYY-MM-DD), got {row[field]!r}")
        return row[field]

    def _decision(self, where, row, cost):
        """Return the row's decision; refuse one outside the vocabulary or against ``cost``'s sign.

        The page prints the opportunity cost and the verdict side by side, so they must agree:
        exit needs a cost above zero; hold needs zero (a tie holds), a negative cost or None.
        """
        p = self.params
        field = p["decision_field"]
        if field not in row:
            raise ValueError(f"{self.key}: {where} has no field {field!r}")
        decision = row[field]
        if not isinstance(decision, str) or decision not in VERDICTS:
            raise ValueError(f"{self.key}: {where} field {field!r} must be one of "
                             f"{sorted(VERDICTS)}, got {decision!r}")
        if (cost is not None and cost > 0.0) != (decision == _EXIT):
            raise ValueError(
                f"{self.key}: {where} field {field!r} is {decision!r} but field "
                f"{p[_COST_PARAM]!r} is {cost!r}: exit needs a positive opportunity cost; hold "
                "needs zero, a negative one or null")
        return decision

    def _day_of(self, rows):
        """Return the one decision day the rows carry; refuse several."""
        days = sorted({row["day"] for row in rows})
        if len(days) != 1:
            raise ValueError(f"{self.key}: rows carry several {self.params['as_of_field']} days "
                             f"{days}; a page is one decision day")
        return days[0]

    def _rows(self, records):
        """Read every row and sort by opportunity cost descending, nulls last, ties stable."""
        if not records:
            raise ValueError(f"{self.key}: records carry no rows: there is no holding to report")
        rows = [self._row(index, row) for index, row in enumerate(records)]
        return sorted(rows, key=lambda r: (r["numbers"][_COST_PARAM] is None,
                                           -(r["numbers"][_COST_PARAM] or 0.0)))

    def _table(self, rows):
        """Return the table's lines: header, alignment, one line per holding."""
        lines = ["| " + " | ".join(_COLUMNS) + " |",
                 "|---|" + "---:|" * (len(_COLUMNS) - 2) + "---|"]
        for row in rows:
            numbers = row["numbers"]
            cells = [render_cell(row["holding"])]
            cells += [_plain(numbers[name], 2) if name == _BETA_PARAM else _percent(numbers[name])
                      for name in DISPLAY_FIELDS]
            admissible = numbers[_COST_PARAM] is not None
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
        lines.append(f"- Total value ({self.params['universe']}): {total:,.2f}")
        lines.append(f"- Weighted expected return: {_percent(portfolio['expected_return'])} a year")
        if portfolio["weighted_volatility"] is not None:
            lines.append("- Weighted volatility (an upper bound): "
                         f"{_percent(portfolio['weighted_volatility'])}")
        if portfolio["beta"] is not None:
            lines.append(f"- Weighted beta: {_plain(portfolio['beta'], 2)}")
        lines.append(f"- Concentration: HHI {portfolio['hhi']:.3f}, effective holdings "
                     f"{portfolio['effective_holdings']:.1f}, top holding "
                     f"{render_cell(portfolio['top_symbol'])} at {_percent(portfolio['top_weight'])} of value")
        tax = portfolio["tax_cost"]
        lines.append(f"- Tax to sell all: {_percent(tax)} of value ({tax * total:,.2f})")
        cost = portfolio["opportunity_cost"]
        if cost is None:
            lines.append(f"- Sell all vs hold all: {NO_ALTERNATIVE}")
        else:
            wins = "selling all wins" if cost > 0.0 else "holding all wins"
            lines.append(f"- Sell all vs hold all: opportunity cost {_percent(cost)} a year ({wins})")
        if cost is None:
            lines.append(f"- Verdicts: {NO_ALTERNATIVE}")
        else:
            lines.append(f"- Verdicts: {int(portfolio['exit_positions'])} {VERDICTS[_EXIT]}, "
                         f"{int(portfolio['hold_positions'])} {VERDICTS[_HOLD]}")
        return lines
