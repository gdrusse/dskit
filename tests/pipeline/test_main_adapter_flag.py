"""``--adapter MODULE`` on ``run`` and ``plan``, not just ``validate``.

Adapters ship components, never CLIs — there is ONE universal command
line. A document may name its components two ways, and only one of them
was reachable from that command line before this seam: a class reference
(``pkg.module:Class``) imports itself when it resolves, but a REGISTERED
kind name (``synth-source``) exists only after its owning package has been
imported. ``--adapter`` is that import, and it happens BEFORE the document
is read.

The properties pinned here:

* class-ref documents still need no flag at all (run and plan);
* a registered adapter kind is unreachable without the flag and resolves
  with it — asserted in a FRESH interpreter, because in-process a sibling
  suite may already have imported the adapter and the flag would then
  prove nothing;
* the flag repeats, and EVERY entry is imported;
* adapters import before the document is read (a bad module beats a
  missing file to the error message);
* a bad module exits 1 with the ImportError printed — no traceback;
* ``validate --adapter`` is unchanged.

The adapter here is a neutral in-repo fixture (:mod:`tests.pipeline.synth_adapter`);
importing it registers a node kind, exactly as a real adapter package does.
"""

import json
import os
import subprocess
import sys

import pytest

from dskit.pipeline.__main__ import main

ASOF = "2026-01-01"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXAMPLES = os.path.join(REPO_ROOT, "examples", "pipeline")
#: A node-map document wired entirely by class reference — the "no flag
#: needed" side of the contract.
NODEMAP = os.path.join(EXAMPLES, "nodemap-minimal.json")
#: A stage-list document — where ``validate --adapter`` has always applied.
LEGACY = os.path.join(EXAMPLES, "synthetic.json")

ADAPTER = "tests.pipeline.synth_adapter"
ADAPTER_KIND = "synth-source"
MISSING = "no_such_adapter_xyz"


def adapter_document(tmp_path):
    """A document naming a REGISTERED adapter kind, written to disk.

    One data node: it plans anywhere and runs to a one-node completion, so
    one file proves both commands with no data on disk.
    """
    path = tmp_path / "adapter-kinds.json"
    path.write_text(
        json.dumps(
            {
                "name": "adapter-kinds",
                "pipeline": {
                    "source": {
                        "uses": ADAPTER_KIND,
                        "params": {"n_events": 4, "n_instruments": 1, "seed": 1},
                    }
                },
                "outputs": {"run_root": str(tmp_path / "runs")},
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def cli(argv):
    """``python -m dskit.pipeline ...`` in a FRESH interpreter — the only
    place the "unregistered without the flag" claim can be made honestly."""
    return subprocess.run(
        [sys.executable, "-m", "dskit.pipeline", *argv],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class TestNoFlagStillNeeded:
    """Class-ref documents import themselves; the flag is not a new tax."""

    def test_plan_works_with_no_flag(self, capsys):
        assert main(["plan", NODEMAP]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["order"][0] == "dataset"

    def test_run_works_with_no_flag(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)  # outputs.run_root "" -> ./pipeline_runs
        assert main(["run", NODEMAP, "--asof", ASOF]) == 0
        assert capsys.readouterr().out.startswith("**RAN")


class TestAdapterKindsBecomeReachable:
    """The point of the change: the second reference form reaches the one
    command line."""

    def test_plan_resolves_a_registered_adapter_kind(self, tmp_path, capsys):
        assert main(["plan", adapter_document(tmp_path), "--adapter", ADAPTER]) == 0
        payload = json.loads(capsys.readouterr().out)
        node = payload["nodes"]["source"]
        assert node["uses"] == ADAPTER_KIND
        assert node["class"] == "dskit.pipeline.synthetic_nodes:SynthEvents"
        assert node["role"] == "data"
        # An adapter kind is never OWNED — ownership is toolkit doctrine.
        assert node["owned"] is False

    def test_run_executes_a_document_naming_a_registered_adapter_kind(
        self, tmp_path, capsys
    ):
        path = adapter_document(tmp_path)
        assert main(["run", path, "--asof", ASOF, "--adapter", ADAPTER]) == 0
        out = capsys.readouterr().out
        assert out.startswith("**RAN — all 1 node(s) completed**")
        assert "| source | data | ok |" in out

    @pytest.mark.parametrize("command", ["plan", "run"])
    def test_the_flag_is_what_makes_the_kind_reachable(self, tmp_path, command):
        # Fresh interpreters both ways: in-process, a sibling suite may
        # already have imported the adapter, and then the flag proves
        # nothing about ORDER.
        argv = [command, adapter_document(tmp_path)]
        if command == "run":
            argv += ["--asof", ASOF]

        without = cli(argv)
        assert without.returncode == 1
        assert f"no node kind registered as {ADAPTER_KIND!r}" in without.stdout
        assert "adapter kinds at adapter import" in without.stdout

        with_flag = cli([*argv, "--adapter", ADAPTER])
        assert with_flag.returncode == 0, with_flag.stdout + with_flag.stderr


class TestImportOrderAndRepetition:
    @pytest.mark.parametrize("command", ["plan", "run"])
    def test_adapters_import_before_the_document_is_read(self, command, capsys):
        # Both a bad module AND a missing file: whichever error surfaces
        # names which step ran first.
        assert main([command, "no-such-document.json", "--adapter", MISSING]) == 1
        out = capsys.readouterr().out
        assert f"No module named '{MISSING}'" in out
        assert "no-such-document.json" not in out

    def test_the_flag_repeats(self, capsys):
        assert main(["plan", NODEMAP, "--adapter", ADAPTER, "--adapter", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["order"][0] == "dataset"

    def test_every_repeat_is_imported_not_just_the_first(self, capsys):
        # A good module first, a bad one second: the second must still be
        # attempted, or the repetition is decorative.
        assert main(["plan", NODEMAP, "--adapter", ADAPTER, "--adapter", MISSING]) == 1
        assert f"No module named '{MISSING}'" in capsys.readouterr().out


class TestBadAdapterModule:
    @pytest.mark.parametrize("command", ["plan", "run"])
    def test_bad_adapter_exits_one_without_a_traceback(self, command, capsys):
        argv = [command, NODEMAP, "--adapter", MISSING]
        if command == "run":
            argv += ["--asof", ASOF]
        assert main(argv) == 1  # returned, not raised
        out = capsys.readouterr().out
        assert f"No module named '{MISSING}'" in out
        assert "Traceback" not in out

    def test_a_bad_adapter_stops_the_run_before_anything_is_written(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        assert main(["run", NODEMAP, "--asof", ASOF, "--adapter", MISSING]) == 1
        assert os.listdir(tmp_path) == []


class TestValidateUnchanged:
    """The command the flag already served — regression only."""

    def test_without_the_flag_optimizer_params_are_shape_only(self, capsys):
        assert main(["validate", LEGACY]) == 0
        out = capsys.readouterr().out
        assert "(shape-only*)" in out
        # The hint names a CHILD package, never a venue or a dskit
        # subpackage (ADR-0032; tier-1 stays venue-neutral, test_purity).
        assert "--adapter yourproject, your child package" in out

    def test_with_the_flag_validation_is_strict(self, capsys):
        assert main(["validate", LEGACY, "--adapter", ADAPTER]) == 0
        out = capsys.readouterr().out
        assert "(strict)" in out
        assert "shape-only" not in out

    def test_node_map_documents_still_validate_shape_and_hash(self, capsys):
        assert main(["validate", NODEMAP, "--adapter", ADAPTER]) == 0
        assert "nodes: 5" in capsys.readouterr().out

    def test_a_bad_adapter_on_validate_reads_like_run_and_plan(self, capsys):
        # One flag, one behaviour on every command: printed, exit 1. This
        # used to escape `main` as a traceback on validate alone.
        assert main(["validate", LEGACY, "--adapter", MISSING]) == 1
        out = capsys.readouterr().out
        assert MISSING in out
        assert "Traceback" not in out

    def test_a_bad_adapter_on_a_node_map_document_also_exits_one(self, capsys):
        # Node-map validation resolves no `uses`, so the import changes no
        # outcome — but the flag must not silently do nothing on exactly
        # the grammar it was widened for.
        assert main(["validate", NODEMAP, "--adapter", MISSING]) == 1
        assert MISSING in capsys.readouterr().out


class TestHelp:
    @pytest.mark.parametrize("command", ["run", "plan", "validate"])
    def test_the_flag_is_advertised(self, command, capsys):
        with pytest.raises(SystemExit) as exc:
            main([command, "--help"])
        assert exc.value.code == 0
        # argparse re-wraps help text, so compare on collapsed whitespace
        out = " ".join(capsys.readouterr().out.split())
        assert "--adapter MODULE" in out
        assert (
            "adapter module(s) to import first (import = registration), "
            "e.g. yourproject — a child package, never a dskit subpackage"
            in out
        )
