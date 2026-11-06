"""The panel extension's own logic on synthetic frames (ADR-0298, proposed); the real builder is not run here."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError

from index_options import panel_extension as pe
from index_options.quarterly_forecasts import FOREVER_SEAL

NAMES = ["AAA", "BBB", "CCC"]


def _frame(names, start="2023-12-01", periods=50, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for name in names:
        for day in pd.date_range(start, periods=periods, freq="D"):
            expiry = (day + pd.Timedelta(days=7)).date().isoformat()
            rows.append({"symbol": name, "quote_date": day.date().isoformat(), "expiry": expiry,
                         "settlement_date": expiry, "terminal_return": float(rng.normal()), "gap_age": 3,
                         "maybe": float("nan") if rng.random() < .3 else 1.0, "kind": "x"})
    return pd.DataFrame(rows)


def _onehot(frame, names, everyone):
    frame = frame.copy()
    for name in names:
        frame["is_"+name] = (frame["symbol"] == name).astype("int8")
    return frame


def _old_frame(tmp, frame):
    part = frame[(frame.quote_date < "2024-01-01") & (frame.settlement_date < "2024-01-01")]
    table = pa.Table.from_pandas(_onehot(part, NAMES, NAMES), preserve_index=False)
    table = table.set_column(table.schema.get_field_index("gap_age"), "gap_age", table["gap_age"].cast(pa.float64()))
    path = tmp/"old.parquet"
    pq.write_table(table, path)
    return path, table.schema


def _built(tmp, frame, schema, names=NAMES, cuts=((0, 1), (1, 3))):
    paths = []
    for i, (low, high) in enumerate(cuts):
        part = _onehot(frame[frame.symbol.isin(names[low:high])], names[low:high], names)
        path = tmp/f"batch{i}.parquet"
        pq.write_table(pa.Table.from_pandas(part, preserve_index=False), path)
        paths.append(str(path))
    dest = tmp/"new.parquet"
    assert pe.stitch(paths, names, str(dest), schema) == len(frame)
    return dest


def test_stitched_frame_has_the_old_columns_order_types_and_zeroed_one_hots(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    new = _built(tmp_path, frame, schema)
    assert pq.read_schema(new).remove_metadata().equals(schema.remove_metadata())
    table = pq.read_table(new).to_pandas()
    assert table.loc[table.symbol == "AAA", "is_BBB"].eq(0).all() and table.loc[table.symbol == "BBB", "is_BBB"].eq(1).all()


def test_overlap_passes_and_shows_no_value(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    new = _built(tmp_path, frame, schema)
    report = pe.verify_overlap(old, new, NAMES)
    assert report["passed"] and report["rows_added"] > 0 and report["rows_overlap"] == report["rows_old"]
    assert report["last_date"]["quote_date"] >= "2024-01-01"
    assert "terminal_return" in report["column_sha256"]
    wire = json.dumps({k: v for k, v in report.items() if k != "column_sha256"})
    assert not re.search(r"\d\.\d", wire)


def test_overlap_finds_one_changed_value_and_names_only_its_column(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    changed = frame.copy()
    changed.loc[(changed.symbol == "BBB") & (changed.quote_date == "2023-12-05"), "terminal_return"] += 1e-12
    new = _built(tmp_path, changed, schema)
    report = pe.verify_overlap(old, new, NAMES)
    assert not report["passed"] and report["differing_columns"] == ["terminal_return"]


def test_a_change_only_past_the_overlap_passes(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    changed = frame.copy()
    changed.loc[changed.quote_date >= "2024-01-01", "terminal_return"] = 5.0
    assert pe.verify_overlap(old, _built(tmp_path, changed, schema), NAMES)["passed"]


def test_overlap_refuses_a_missing_or_extra_key_and_a_date_past_the_seal(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    gone = frame[~((frame.symbol == "AAA") & (frame.quote_date == "2023-12-03"))]
    assert not pe.verify_overlap(old, _built(tmp_path, gone, schema), NAMES)["keys_equal"]
    late = frame.copy()
    late.loc[late.index[-1], ["settlement_date", "expiry"]] = FOREVER_SEAL
    report = pe.verify_overlap(old, _built(tmp_path, late, schema), NAMES)
    assert not report["passed"] and not report["inside_forever_seal"]


def test_overlap_refuses_a_name_outside_the_lock_and_a_nan_swap_is_seen(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    assert not pe.verify_overlap(old, _built(tmp_path, frame, schema), NAMES[:2])["passed"]
    swapped = frame.copy()
    day = swapped[swapped.maybe.isna() & (swapped.quote_date < "2023-12-20")].index[0]
    swapped.loc[day, "maybe"] = 1.0
    assert "maybe" in pe.verify_overlap(old, _built(tmp_path, swapped, schema), NAMES)["differing_columns"]


def test_a_schema_change_is_found_before_any_value_is_read(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    table = pq.read_table(_built(tmp_path, frame, schema)).drop_columns(["kind"])
    pq.write_table(table, tmp_path/"short.parquet")
    report = pe.verify_overlap(old, tmp_path/"short.parquet", NAMES)
    assert not report["passed"] and report["differing_columns"] == ["kind"] and not report["column_sha256"]


def test_rebound_moves_the_bound_and_root_and_refuses_an_unowned_boundary():
    doc = {"pipeline": {"chain": {"params": {"end_before": "2024-01-01", "symbols": ["AAA"]}},
                        "bars": {"read_bounds": {"date": {"start": "2016-01-04", "end_before": "2024-01-01"}}},
                        "keep": {"end_before": "2019-01-01"}},
           "study": {"fold_table": {"holdout_start": "2024-01-01"}, "output": "/dev/shm/x/study"},
           "notes": "the bound 2024-01-01 in words stays as written"}
    moved = pe.rebound(doc, "/dev/shm/x", "/mnt/d/y")
    assert moved["pipeline"]["chain"]["params"]["end_before"] == FOREVER_SEAL
    assert moved["pipeline"]["bars"]["read_bounds"]["date"]["end_before"] == FOREVER_SEAL
    assert moved["pipeline"]["keep"]["end_before"] == "2019-01-01"
    assert moved["study"]["fold_table"]["holdout_start"] == FOREVER_SEAL and moved["study"]["output"] == "/mnt/d/y/study"
    with pytest.raises(ValueError, match="old boundary"):
        pe.rebound({"other": "through 2024-01-01"}, "/dev/shm/x", "/mnt/d/y")


def _config(tmp, docs):
    wire = json.dumps(NAMES, separators=(",", ":")).encode()
    lock = tmp/"lock.json"
    lock.write_text(json.dumps({"symbols": NAMES, "count": 3, "sha256": hashlib.sha256(wire).hexdigest()}))
    return {"docs_from": str(docs), "old_root": "/dev/shm/x", "work_root": str(tmp/"work"), "final_wsl": str(tmp/"wsl"),
            "old_frame": str(tmp/"old.parquet"), "old_frame_sha256": "0"*64, "python": "python",
            "repo": str(tmp), "batches": 2, "ticker_lock": str(lock), "synthetic": True}


def test_plan_writes_moved_documents_guarded_by_the_lock_and_the_gate_jobs(tmp_path):
    docs = tmp_path/"docs"
    docs.mkdir()
    conv = {"pipeline": {"chain": {"params": {"symbols": NAMES, "end_before": "2024-01-01"}}}}
    study = {"data": {"symbols": {s: s for s in NAMES}}, "study": {"fold_table": {"holdout_start": "2024-01-01"}}}
    for name, doc in (("conv", conv), ("prepare", study), ("panel0", study)):
        (docs/f"{name}.json").write_text(json.dumps(doc))
    extension = pe.PanelExtension(_config(tmp_path, docs))
    extension.plan()
    assert json.loads((extension.docs/"conv.json").read_text())["pipeline"]["chain"]["params"]["end_before"] == FOREVER_SEAL
    jobs = json.loads((extension.work/"jobs-panel.json").read_text())["jobs"]
    assert [j["id"] for j in jobs] == ["tables", "panel", "verify"] and all(j["ram_gb"] and j["ram_basis"] for j in jobs)
    assert all(j["serial_reason"] for j in jobs[1:])
    (docs/"conv.json").write_text(json.dumps({"pipeline": {"chain": {"params": {"symbols": NAMES[:2]}}}}))
    with pytest.raises(ValueError):
        pe.PanelExtension(_config(tmp_path, docs)).plan()


def test_the_final_panel_stays_in_wsl_and_refuses_if_c_cannot_hold_it(tmp_path, monkeypatch):
    docs = tmp_path/"docs"
    docs.mkdir()
    vhdx = tmp_path/"ext4.vhdx"
    vhdx.write_text("x")
    extension = pe.PanelExtension({**_config(tmp_path, docs), "c_floor_gb": 3, "c_vhdx": str(vhdx)})
    gib = 1024**3

    class Usage:
        free = 5*gib
    monkeypatch.setattr(pe.shutil, "disk_usage", lambda _: Usage)
    assert extension.final_directory(gib) == tmp_path/"wsl"
    with pytest.raises(IntegrityError, match="C:"):
        extension.final_directory(3*gib)
    vhdx.unlink()                   # the WSL disk moved to D: writes no longer land on C:
    assert extension.final_directory(3*gib) == tmp_path/"wsl"


@pytest.mark.parametrize("key", ["final_wsl", "work_root"])
def test_a_panel_with_2024_outcomes_never_sits_on_a_windows_drive_without_the_owner(tmp_path, key):
    docs = tmp_path/"docs"
    docs.mkdir()
    with pytest.raises(ValueError, match="outcome"):
        pe.PanelExtension({**_config(tmp_path, docs), key: "/mnt/d/anywhere"})


def test_overlap_counts_added_rows_per_name_and_refuses_a_vanished_name(tmp_path):
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp_path, frame)
    ok = pe.verify_overlap(old, _built(tmp_path, frame, schema), NAMES)
    assert ok["passed"] and ok["names_cover_added_rows"] and sum(ok["added_rows_by_name"].values()) == ok["rows_added"]
    gone = frame[~((frame.symbol == "CCC") & (frame.settlement_date >= "2024-01-01"))]
    report = pe.verify_overlap(old, _built(tmp_path, gone, schema), NAMES)
    assert not report["passed"] and not report["names_cover_added_rows"] and report["added_quarters_missing"]["CCC"]
    assert report["added_rows_by_name"]["CCC"] == 0 and report["keys_equal"] and report["symbols_equal_lock"]
    assert not re.search(r"\d\.\d", json.dumps({k: v for k, v in report.items() if k != "column_sha256"}))


def test_overlap_refuses_a_name_missing_one_added_quarter_unless_excepted(tmp_path):
    frame = _frame(NAMES, periods=130)
    old, schema = _old_frame(tmp_path, frame)
    gone = frame[~((frame.symbol == "BBB") & (frame.quote_date >= "2024-04-01"))]
    new = _built(tmp_path, gone, schema)
    report = pe.verify_overlap(old, new, NAMES)
    assert not report["passed"] and report["added_quarters_missing"] == {"BBB": ["2024Q2"]}
    assert pe.verify_overlap(old, new, NAMES, exceptions={"BBB": "owner ruling, test only"})["passed"]


def test_no_added_rows_at_all_refuses(tmp_path):
    frame = _frame(NAMES, periods=20)
    old, schema = _old_frame(tmp_path, frame)
    report = pe.verify_overlap(old, _built(tmp_path, frame, schema), NAMES)
    assert report["rows_added"] == 0 and not report["passed"]


def _resumable(tmp, monkeypatch):
    docs = tmp/"docs"
    docs.mkdir()
    frame = _frame(NAMES)
    old, schema = _old_frame(tmp, frame)
    extension = pe.PanelExtension({**_config(tmp, docs), "old_frame_sha256": AtomicFitStore.file_hash(old)})
    extension.work.mkdir()
    batches = [_built(tmp, frame, schema)]
    monkeypatch.setattr(extension, "_batch", lambda part, tag: str(batches[0]))
    monkeypatch.setattr(extension, "_names_cuts", lambda: [NAMES])
    return extension


def test_panel_resumes_after_a_kill_before_the_record_and_never_overwrites_another_build(tmp_path, monkeypatch):
    extension = _resumable(tmp_path, monkeypatch)
    first = extension.panel()
    target = tmp_path/"wsl"/"frame.parquet"
    assert first["sha256"] == AtomicFitStore.file_hash(target) and first["panel"] == str(target)
    (extension.work/"final.json").unlink()                # killed after the rename, before the record
    (tmp_path/"wsl"/"frame.partial").write_bytes(b"torn")
    assert extension.panel() == first
    stamp = target.stat().st_mtime_ns
    assert extension.panel() == first and target.stat().st_mtime_ns == stamp
    (extension.work/"final.json").unlink()
    target.write_bytes(b"another build")
    with pytest.raises(IntegrityError, match="overwrite"):
        extension.panel()
    assert target.read_bytes() == b"another build"


def test_verify_json_binds_the_panel_hashes_for_the_forecast_plan(tmp_path, monkeypatch):
    extension = _resumable(tmp_path, monkeypatch)
    final = extension.panel()
    extension.verify()
    report = json.loads((extension.work/"verify.json").read_text())
    assert report["passed"] is True and report["panel_sha256"] == final["sha256"]
    assert report["old_frame_sha256"] == extension.config["old_frame_sha256"]
    assert report["synthetic"] is True and len(report["ticker_lock_sha256"]) == 64


def test_tables_resume_after_a_killed_convert_and_an_existing_store(tmp_path, monkeypatch):
    docs = tmp_path/"docs"
    docs.mkdir()
    extension = pe.PanelExtension(_config(tmp_path, docs))
    extension.work.mkdir()
    partial = extension.work/".exact-expiry-tables.partial"
    partial.mkdir()
    calls = []

    monkeypatch.setattr(extension, "_run", lambda *args: calls.append(args))
    extension._convert()
    assert len(calls) == 1 and not partial.exists()
    (extension.work/"exact-expiry-tables").mkdir()
    partial.mkdir()
    extension._convert()                    # already published: nothing to run, the stale partial stays visible
    assert len(calls) == 1 and partial.exists()
    root = extension.work/"ob"
    (root/"store").mkdir(parents=True)
    (root/"store"/"store.json").write_text("{}")
    extension._init_onboarding(str(root))
    assert len(calls) == 1
    (root/"raw"/"source"/"snapshot").mkdir(parents=True)
    extension._register(str(root), "source", "localblobs", {}, "files")
    assert len(calls) == 1
    extension._register(str(root), "other", "localblobs", {}, "files")
    assert len(calls) == 3


def test_plan_refuses_when_chmod_did_not_take_and_when_the_projection_passes_the_floor(tmp_path, monkeypatch):
    docs = tmp_path/"docs"
    docs.mkdir()
    conv = {"pipeline": {"chain": {"params": {"symbols": NAMES, "end_before": "2024-01-01"}}}}
    study = {"data": {"symbols": {s: s for s in NAMES}}, "study": {"fold_table": {"holdout_start": "2024-01-01"}}}
    for name, doc in (("conv", conv), ("prepare", study), ("panel0", study)):
        (docs/f"{name}.json").write_text(json.dumps(doc))
    vhdx = tmp_path/"ext4.vhdx"
    vhdx.write_text("x")
    extension = pe.PanelExtension({**_config(tmp_path, docs), "c_floor_gb": 3, "c_vhdx": str(vhdx)})

    class Usage:
        free = 5*1024**3
    monkeypatch.setattr(pe.shutil, "disk_usage", lambda _: Usage)
    with pytest.raises(IntegrityError, match="C:"):
        extension.plan()
    vhdx.unlink()
    (tmp_path/"wsl").mkdir(mode=0o755)
    monkeypatch.setattr(pe.os, "chmod", lambda *a: None)
    with pytest.raises(IntegrityError, match="private"):
        extension.plan()


def test_the_plan_verb_refuses_the_c_floor_before_any_folder_or_file_exists(tmp_path, monkeypatch):
    docs = tmp_path/"docs"
    docs.mkdir()
    vhdx = tmp_path/"ext4.vhdx"
    vhdx.write_text("x")
    config = {**_config(tmp_path, docs), "c_floor_gb": 3, "c_vhdx": str(vhdx)}
    path = tmp_path/"run.json"
    path.write_text(json.dumps(config))

    class Usage:
        free = 5*1024**3
    monkeypatch.setattr(pe.shutil, "disk_usage", lambda _: Usage)
    assert pe.TABLES_GB == 11
    with pytest.raises(IntegrityError, match="C:"):
        pe.main(["plan", str(path)])
    assert not Path(config["work_root"]).exists() and not Path(config["final_wsl"]).exists()


def test_a_real_run_refuses_a_private_lock(tmp_path):
    docs = tmp_path/"docs"
    docs.mkdir()
    config = _config(tmp_path, docs)
    del config["synthetic"]
    with pytest.raises(ValueError, match="lock"):
        pe.PanelExtension(config)


def test_verify_refuses_a_changed_old_frame(tmp_path):
    docs = tmp_path/"docs"
    docs.mkdir()
    frame = _frame(NAMES)
    _old_frame(tmp_path, frame)
    extension = pe.PanelExtension(_config(tmp_path, docs))
    (extension.work).mkdir(parents=True)
    with pytest.raises(IntegrityError, match="pin"):
        extension.verify()


def test_the_real_build_documents_rebound_under_the_real_lock(tmp_path):
    """Only build documents are read here: nothing is built."""
    config = json.loads((Path(pe.__file__).parents[1]/"configs"/"quarterly-panel-extension.json").read_text())
    if not (Path(config["docs_from"])/"panel0.json").exists():
        pytest.skip("the panel build's documents are not on this machine")
    extension = pe.PanelExtension({**config, "work_root": str(tmp_path/"work"), "final_wsl": str(tmp_path/"final"),
                                   "c_vhdx": str(tmp_path/"moved")})
    extension.plan()
    panel = json.loads((extension.docs/"panel0.json").read_text())
    assert panel["study"]["fold_table"]["holdout_start"] == FOREVER_SEAL
    assert str(tmp_path/"work") in panel["study"]["output"]
    conv = (extension.docs/"conv.json").read_text()
    assert str(tmp_path/"work/exact-expiry-tables") in conv and config["old_root"] not in conv


def _commit(repo):
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                                 "commit", "-qm", "c"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE, text=True)
    return done.stdout.strip()


def _repo(tmp, extension):
    repo = tmp/"repo"                                      # apart from the build's work and final folders
    repo.mkdir()
    (repo/"code.py").write_text("x = 1\n")
    head = _commit(repo)
    extension.code = pe.CodeTree(repo)
    return repo, head


def test_verify_json_records_the_code_commit_of_a_clean_tree(tmp_path, monkeypatch):
    extension = _resumable(tmp_path, monkeypatch)
    _, head = _repo(tmp_path, extension)
    extension.panel()
    extension.verify()
    assert json.loads((extension.work/"verify.json").read_text())["code_commit"] == head


def test_a_real_extension_refuses_every_stage_from_a_dirty_tree(tmp_path, monkeypatch):
    extension = _resumable(tmp_path, monkeypatch)
    repo, _ = _repo(tmp_path, extension)
    (repo/"code.py").write_text("x = 2\n")
    extension.config["synthetic"] = False                 # the synthetic exemption is gone
    for stage in (extension.tables, extension.panel, extension.verify):
        with pytest.raises(IntegrityError, match="committed tree"):
            stage()
    assert not (tmp_path/"wsl"/"frame.parquet").exists()
