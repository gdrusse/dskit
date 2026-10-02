"""Step 1 neutral template: placeholders only, expands with the fixture args, plans.

The expander is the production one, ``dskit.pipeline.workflow.expand``.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from dskit.pipeline.workflow import expand, placeholder_names

CHILD = Path(__file__).resolve().parents[1]
TEMPLATE = CHILD / "configs" / "templates" / "step1-target-dates.json"
ARGS = CHILD / "tests" / "fixtures" / "args-step1.json"
HOLE = re.compile(r"\$(\$|\{([A-Za-z_]\w*(?:\.\w+)*)\})")


#: Keys that hold prose, not values; the literal scan skips them like notes.
PROSE = ("notes", "provenance_waiver", "source")


def _strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in PROSE:
                yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


@pytest.fixture(scope="module")
def args():
    return json.loads(ARGS.read_text())


@pytest.fixture(scope="module")
def template():
    return json.loads(TEMPLATE.read_text())


def test_no_project_literal_survives_in_the_template(template, args):
    """No ticker, horizon or schema name is typed in the template outside notes."""
    forbidden = {args["T"], str(args["H"])} | set(args["S"].values()) | set(
        args["U"]["columns"].values()) | {args["U"]["source"]}
    for text in _strings(template):
        bare = HOLE.sub("", text).replace("$each", "")
        assert not (set(re.findall(r"[A-Za-z0-9_-]+", bare)) & forbidden), text


def test_every_arg_is_used_and_every_hole_filled(template, args):
    assert placeholder_names(template) == set(args)
    assert not [s for s in _strings(expand(template, args)) if HOLE.search(s)]


def test_expansion_keeps_types_and_pipeline_references(template, args):
    out = expand(template, args)
    inner = out["foreach"]["pipeline"]
    assert out["foreach"]["keys"] == ["QQQ"]
    assert inner["pairs"]["params"]["horizon_days"] == 7
    assert inner["prices"]["params"]["relpath_by_key"] == {"QQQ": "qqq/underlying_prices.parquet"}
    assert inner["settled"]["inputs"]["records"] == "$pairs.records"
    assert out["pipeline"]["target_dates"]["params"]["path"] == args["L"]["step1"]["target_dates"]
    assert inner["pairs"]["params"]["fields"] == {
        "symbol": "symbol", "date": "quote_date", "settlement_date": "settlement_date",
        "entry_close": "spot", "settle_close": "terminal_price",
        "terminal_return": "terminal_return", "period": "period"}
    assert inner["date_figure"]["params"]["filename"] == args["labels"]["filename"]


def test_reader_columns_and_pair_fields_agree(args):
    """The fields the reader emits are the fields the pairing node reads (pinned)."""
    assert set(args["U"]["columns"].values()) == set(args["U"]["fields"].values())


def test_template_plans_clean(template, args, tmp_path):
    """plan resolves the whole document: every kind is built (ADR-0228)."""
    doc = tmp_path / "step1.json"
    doc.write_text(json.dumps(expand(template, args)))
    run = subprocess.run([sys.executable, "-m", "dskit.pipeline", "plan", str(doc)],
                         capture_output=True, text=True, cwd=CHILD)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["order"]
