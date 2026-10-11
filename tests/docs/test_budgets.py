"""Markdown stays short enough to focus on (owner rule, 2026-10-10).

Three pins. Every governed instruction file and re-entry note has a line
budget, so growth is a red test rather than a slow loss of focus (the single
``docs/RE-ENTRY.md`` reached 469,597 lines, 4,431 unique, before this file
existed). No re-entry note may carry a keep-both or git conflict marker: the
ADR-0109 driver is deliberately loud, and a committed marker means a session
merged and skipped the prune. And every ``CLAUDE.md`` / ``AGENTS.md`` pair is
one document under two names; the comparison rule is imported from the tool
that writes them, never restated here.
"""

import glob
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, relpath))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mirror = _load("tools/docs/mirror_pairs.py", "mirror_pairs")
keep_both = _load("tools/merge/keep_both.py", "keep_both")

#: Line budgets. A glob maps to the budget every matching file must respect.
BUDGETS = {
    "CLAUDE.md": 160,
    "AGENTS.md": 160,
    "docs/RE-ENTRY.md": 80,
    "children/*/docs/RE-ENTRY.md": 80,
    "dskit/*/CLAUDE.md": 260,
    "dskit/*/AGENTS.md": 260,
    "children/*/CLAUDE.md": 260,
    "children/*/AGENTS.md": 260,
}

CONFLICT_MARKERS = ("<<<<<<< ", "=======\n", ">>>>>>> ")


def _governed():
    for pattern, budget in BUDGETS.items():
        for path in sorted(glob.glob(os.path.join(REPO, pattern))):
            yield os.path.relpath(path, REPO), budget


def _lines(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as fh:
        return fh.read()


def _children():
    return sorted(
        d for d in os.listdir(os.path.join(REPO, "children"))
        if os.path.isdir(os.path.join(REPO, "children", d))
    )


def test_every_governed_markdown_file_is_within_its_line_budget():
    over = {}
    for relpath, budget in _governed():
        count = _lines(relpath).count("\n")
        if count > budget:
            over[relpath] = (count, budget)
    assert not over, (
        "over budget (lines, budget): "
        f"{over}. Move detail into docs/standards/, a package EXTENDING.md, a memo "
        "or docs/archive/; the rule is the root file keeps the rule, not the essay."
    )


def test_re_entry_notes_carry_no_merge_markers():
    offenders = {}
    for relpath, _budget in _governed():
        if not relpath.endswith("RE-ENTRY.md"):
            continue
        text = _lines(relpath)
        found = [m for m in (keep_both.MARKER, *CONFLICT_MARKERS) if m in text]
        if found:
            offenders[relpath] = found
    assert not offenders, (
        f"merge markers committed in a re-entry note: {offenders}. Two sessions "
        "wrapped in parallel; rewrite the note as ONE current note before committing."
    )


def test_every_claude_md_and_agents_md_pair_is_one_document():
    differing = []
    for d in mirror.pair_dirs(REPO):
        paths = [os.path.join(d, name) for name in mirror.NAMES]
        if not all(os.path.isfile(p) for p in paths):
            differing.append(f"{os.path.relpath(d, REPO)}: one half of the pair is missing")
            continue
        texts = [open(p, encoding="utf-8").read() for p in paths]
        if mirror.normalized(texts[0]) != mirror.normalized(texts[1]):
            differing.append(os.path.relpath(d, REPO))
    assert not differing, (
        f"CLAUDE.md and AGENTS.md differ in {differing}; run "
        "`python tools/docs/mirror_pairs.py` (or `--source AGENTS.md`)."
    )


def test_every_child_has_a_re_entry_note_and_the_index_names_it():
    index = _lines("docs/RE-ENTRY.md")
    missing_note, missing_row = [], []
    for child in _children():
        if not os.path.isfile(os.path.join(REPO, "children", child, "docs", "RE-ENTRY.md")):
            missing_note.append(child)
        if child != "_skeleton" and f"children/{child}/docs/RE-ENTRY.md" not in index:
            missing_row.append(child)
    assert not missing_note, f"children without docs/RE-ENTRY.md: {missing_note}"
    assert not missing_row, f"docs/RE-ENTRY.md has no row for: {missing_row}"
