"""Temporary smoke test for the conftest fixtures."""
import os
import pytest
import dskit.production.release as rel
from dskit.production.base import _check_dict
from dskit.production.release import verify_release, parse_iso_duration


rel._FEED_SPEC["digest_recipe"] = _check_dict


def test_run_and_release(run_dir, release_manifest, serve_document, serving_contract,
                         source_config_hash, clock, fresh_root, training_document):
    assert os.path.isfile(os.path.join(run_dir, "config.json"))
    assert os.path.isdir(os.path.join(run_dir, "artifacts", "scaled"))
    assert sorted(os.listdir(os.path.join(run_dir, "nodes"))) == [
        "01-bars.json", "02-weights.json", "03-usable.json", "04-grid.json",
        "05-scaled.json", "06-scored.json", "07-picks.json"]
    verify_release(release_manifest, run_dir, clock.now_ms(),
                   parse_iso_duration(serve_document.serving.max_artifact_age),
                   source_config_hash)
    assert serving_contract.entity_key_fields == ("instrument",)
    assert serving_contract.event_time_field == "asof_ms"
    assert list(serve_document.serving.heads) == ["picks"]
    assert fresh_root.root != run_dir
    assert training_document.expanded["bars"].params["since_ms"] is None
    import json
    carry = json.load(open(os.path.join(run_dir, "carry.json")))
    print("CARRY KEYS", sorted(carry))
    rec = json.load(open(os.path.join(run_dir, "nodes", "07-picks.json")))
    print("PICKS RECORD", rec["outputs"], rec["uses"], rec["role"])
