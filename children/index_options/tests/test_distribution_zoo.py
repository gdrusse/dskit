"""ADR-0181 zoo: rung configs share one pipeline; the staged zoo plans and runs."""

import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import run_walk_forward

RUNGS = ("run-synthetic-distribution.json", "run-synthetic-har.json",
         "run-synthetic-lightgbm.json")


def _load(child_root, name):
    return json.loads((child_root / "configs" / name).read_text())


def test_rungs_differ_only_in_name_notes_and_model(child_root):
    base = _load(child_root, RUNGS[0])
    for name in RUNGS[1:]:
        rung = _load(child_root, name)
        stripped = [copy.deepcopy(d) for d in (base, rung)]
        for d in stripped:
            d.pop("name")
            d.pop("notes")
            d["pipeline"].pop("model")
        assert stripped[0] == stripped[1], name
        shared = ("fit_split", "label", "scale_field", "scale_multiplier", "n_samples")
        assert {k: rung["pipeline"]["model"]["params"][k] for k in shared} == \
            {k: base["pipeline"]["model"]["params"][k] for k in shared}


@pytest.mark.parametrize("name", RUNGS)
def test_forward_vol_and_label_share_one_horizon(child_root, name):
    pipe = _load(child_root, name)["pipeline"]
    assert pipe["fwd"]["params"]["horizon"] == pipe["labels"]["params"]["horizon"]


def test_zoo_lists_every_rung_and_pins_the_shared_pipeline(child_root):
    zoo = _load(child_root, "run-distribution-zoo.json")
    plan = zoo["stages"]["plan"]["params"]
    assert sorted(c["path"] for c in plan["candidates"]) == sorted(RUNGS)
    assert {"pipeline.score", "pipeline.condor", "pipeline.labels", "pipeline.fwd",
            "walkforward"} <= set(plan["contract_paths"])
    assert zoo["stages"]["approval"]["params"]["approved_inventory_sha256"] == \
        "PENDING-PLAN-REVIEW"


@pytest.mark.parametrize("name", RUNGS[1:])
def test_each_scale_rung_runs_its_walk_forward(child_root, tmp_path, name):
    if "lightgbm" in name:
        pytest.importorskip("lightgbm")
    obj = _load(child_root, name)
    obj["outputs"]["run_root"] = str(tmp_path)
    result = run_walk_forward(PipelineDocument.from_obj(obj), asof="1978-06-01")
    assert len(result.folds) == 4 and all(f["state"] == "ran" for f in result.folds)
    carry = json.loads((tmp_path / result.folds[-1]["run_dir"] / "carry.json").read_text())
    assert carry["score"]["metrics"]["n"] > 0 and carry["model"]["metrics"]["n_fit_rows"] > 0


def _staged(child_root, workdir, approved=None):
    shutil.copytree(child_root / "configs", workdir / "configs")
    if approved:
        path = workdir / "configs" / "run-distribution-zoo.json"
        doc = json.loads(path.read_text())
        doc["stages"]["approval"]["params"].update(
            approved_inventory_sha256=approved, approved_by="test")
        path.write_text(json.dumps(doc))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(child_root), *filter(None, [os.environ.get("PYTHONPATH")])]))
    for argv in (["dskit.journal", "init", "--root", "."],
                 ["dskit.pipeline", "staged", "configs/run-distribution-zoo.json",
                  "--asof", "1978-06-01"]):
        done = subprocess.run([sys.executable, "-m", *argv], cwd=workdir, env=env,
                              capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, (done.stdout, done.stderr)
    (stages,) = (workdir / "pipeline_runs").glob("*staged*/stages")
    return {p.stem: json.loads(p.read_text()) for p in stages.glob("*.json")}


def _outputs(stage):
    return stage.get("outputs", stage)


def test_zoo_is_plan_only_until_approved(child_root, tmp_path):
    planned = _staged(child_root, tmp_path / "plan")
    approval = _outputs(planned["approval"])["approval"]
    assert approval["approved"] is False
    assert {r["state"] for r in _outputs(planned["run"])["runs"]} == {"awaiting_approval"}


def test_approved_zoo_compares_every_rung(child_root, tmp_path):
    pytest.importorskip("lightgbm")
    planned = _staged(child_root, tmp_path / "plan")
    digest = _outputs(planned["approval"])["approval"]["inventory_sha256"]
    ran = _staged(child_root, tmp_path / "ran", approved=digest)
    ranking = _outputs(ran["compare"])["ranking"]
    assert sorted(r["id"] for r in ranking) == ["empirical", "har", "lightgbm"]
    assert all(r["n_scored"] == 4 for r in ranking)
