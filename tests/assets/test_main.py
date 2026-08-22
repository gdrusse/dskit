"""__main__.py: every command through main(argv), exit codes included."""

import json

import pytest

from dskit.assets.__main__ import main


@pytest.fixture
def store_args(tmp_path, capsys):
    args = ["--store", str(tmp_path / "s")]
    assert main(["init", *args]) == 0
    capsys.readouterr()  # drain init's output so tests parse only their own
    return args


def _register(capsys, store_args, kind, payload, *extra):
    assert main(["register", kind, "--payload", json.dumps(payload),
                 *extra, *store_args]) == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


# -- normal ----------------------------------------------------------------


def test_validate_model_prints_the_pin(capsys):
    assert main(["validate-model"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "default" and len(out["kinds"]) == 12
    assert len(out["model_hash"]) == 64


def test_register_get_list_state_transition(capsys, store_args):
    e = _register(capsys, store_args, "entity", {"name": "AAPL"})
    f = _register(capsys, store_args, "feature", {"name": "mom"},
                  "--ref", f"entity={e}")
    assert main(["state", f, *store_args]) == 0
    assert capsys.readouterr().out.strip() == "draft"
    assert main(["transition", f, "validated", *store_args]) == 0
    assert capsys.readouterr().out.strip() == "validated"
    assert main(["get", f, *store_args]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["state"] == "validated" and got["refs"]["entity"] == e
    assert main(["list", "feature", *store_args]) == 0
    assert "mom" in capsys.readouterr().out


def test_ingest_run_and_lineage(capsys, store_args, run_dir):
    assert main(["ingest-run", run_dir, *store_args]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["artifacts"] and summary["outputs"]
    assert main(["lineage", summary["run"], "--show", "children", *store_args]) == 0
    children = json.loads(capsys.readouterr().out)
    assert children == sorted(summary["artifacts"] + summary["outputs"])


def test_state_of_record_only_kind(capsys, store_args):
    run = _register(capsys, store_args, "run_observation", {"name": "run-1"})
    assert main(["state", run, *store_args]) == 0
    assert capsys.readouterr().out.strip() == "record-only"


def test_payload_from_file(capsys, store_args, tmp_path):
    path = tmp_path / "payload.json"
    path.write_text('{"name": "AAPL"}')
    assert main(["register", "entity", "--payload", f"@{path}", *store_args]) == 0


# -- failure: errors print cleanly and exit 1 ------------------------------


def test_reinit_fails_with_exit_1(capsys, store_args):
    assert main(["init", *store_args]) == 1
    assert "exactly once" in capsys.readouterr().err


def test_bad_payload_json_fails(capsys, store_args):
    assert main(["register", "entity", "--payload", "{not json", *store_args]) == 1
    assert "error:" in capsys.readouterr().err


def test_governance_violation_fails(capsys, store_args):
    assert main(["register", "feature", "--payload", '{"name": "f"}',
                 *store_args]) == 1
    assert "required ref" in capsys.readouterr().err


def test_malformed_ref_flag_fails(capsys, store_args):
    assert main(["register", "entity", "--payload", '{"name": "x"}',
                 "--ref", "nonsense", *store_args]) == 1
    assert "name=version_id" in capsys.readouterr().err
