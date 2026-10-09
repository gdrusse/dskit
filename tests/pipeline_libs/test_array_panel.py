"""ArrayPanel pack: one memory-mapped panel, bands cut in one place, gather-by-index windows.

Everything here is a few hundred synthetic rows: no real data, no network, seconds-long.
Expected values are restated in plain Python/numpy, independently of the pack.
"""

import itertools
import math
from datetime import datetime, timedelta, timezone

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

import dskit.onboarding.acquire as acquire_module  # noqa: E402
from dskit.onboarding import OnboardingRoot, run_acquisition  # noqa: E402
from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.libs import array_panel as ap  # noqa: E402
from dskit.pipeline.libs.array_panel import (  # noqa: E402
    DAY_MS,
    NODE_KINDS,
    ArrayPanel,
    ArrayPanelWrite,
    ColumnSelector,
    DevicePanel,
    PanelBands,
    PanelScaler,
)
from dskit.pipeline.node import NodeContext  # noqa: E402

STEP = 60_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % STEP)
HORIZONS = [1, 3]
SPAN = max(HORIZONS) * STEP
GAP_AT = 50          # group 10 skips 5 minutes before this row
GROUP_SIZES = {10: 120, 20: 100}


def make_columns(seed=0):
    """Two sorted groups, one injected gap in group 10, labels NaN at each group's tail."""
    rng = np.random.default_rng(seed)
    time, group = [], []
    for g, n in GROUP_SIZES.items():
        t = T0 + np.arange(n, dtype=np.int64) * STEP
        if g == 10:
            t[GAP_AT:] += 5 * STEP
        time.append(t)
        group.append(np.full(n, g, dtype=np.int64))
    time, group = np.concatenate(time), np.concatenate(group)
    n = len(time)
    cols = {"t": time, "g": group, "ref": rng.uniform(0.5, 2.0, n)}
    for h in HORIZONS:
        y = rng.normal(size=n)
        for g in GROUP_SIZES:
            idx = np.flatnonzero(group == g)
            y[idx[-h:]] = np.nan
        cols[f"y_{h}"] = y
    for k in range(4):
        cols[f"f{k}"] = rng.normal(size=n)
    cols["seq__a"] = rng.normal(size=n)
    cols["seq__b"] = rng.normal(size=n)
    cols["junk"] = rng.normal(size=n)
    return cols


def params(path, **over):
    base = {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
            "horizons": list(HORIZONS), "step_ms": STEP, "drop": ["junk"], "path": str(path)}
    base.update(over)
    return base


def ctx(tmp_path):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path))


def write(tmp_path, cols=None, name="panel", **over):
    node = ArrayPanelWrite("w", params(tmp_path / name, **over))
    out = node.run(ctx(tmp_path), {"columns": make_columns() if cols is None else cols})
    return out, tmp_path / name


@pytest.fixture
def panel(tmp_path):
    _, path = write(tmp_path)
    return ArrayPanel.open(str(path))


def run_lengths(cols):
    """Independent restatement of run.npy: consecutive STEP bars ending at each row, per group."""
    out, prev_t, prev_g = [], None, None
    for t, g in zip(cols["t"].tolist(), cols["g"].tolist()):
        out.append(out[-1] + 1 if (g == prev_g and t - prev_t == STEP) else 1)
        prev_t, prev_g = t, g
    return np.array(out)


# --- write / open -------------------------------------------------------------------------


def test_no_registered_kinds():
    assert NODE_KINDS == ()


def test_write_open_round_trip(tmp_path):
    cols = make_columns()
    out, path = write(tmp_path, cols)
    panel = ArrayPanel.open(str(path))
    n = len(cols["t"])
    assert panel.columns == ["f0", "f1", "f2", "f3", "seq__a", "seq__b"]
    assert panel.features.shape == (n, 6) and panel.features.dtype == np.float32
    assert panel.features.flags["F_CONTIGUOUS"]
    assert panel.labels.shape == (n, 2)
    np.testing.assert_array_equal(panel.labels, np.stack([cols["y_1"], cols["y_3"]], axis=1))
    np.testing.assert_array_equal(panel.time, cols["t"])
    np.testing.assert_array_equal(panel.reference, cols["ref"])
    assert panel.groups == [10, 20]
    assert panel.step_ms == STEP and panel.horizons == HORIZONS
    assert panel.label_span_ms == SPAN
    assert out["manifest"]["unit_contract"]
    assert set(out["manifest"]["files"]) == {
        "features.npy", "labels.npy", "time.npy", "group.npy", "reference.npy", "run.npy"}


def test_shas_are_stable_and_fingerprint_moves_with_data(tmp_path):
    a, _ = write(tmp_path, name="a")
    b, _ = write(tmp_path, name="b")
    assert a["manifest"]["files"] == b["manifest"]["files"]
    cols = make_columns()
    cols["f0"] = cols["f0"] + 1.0
    c, _ = write(tmp_path, cols, name="c")
    assert c["manifest"]["files"]["features.npy"] != a["manifest"]["files"]["features.npy"]
    assert c["manifest"]["files"]["labels.npy"] == a["manifest"]["files"]["labels.npy"]
    pa_, pc = (ArrayPanel.open(str(tmp_path / n)) for n in ("a", "c"))
    assert pa_.fingerprint() != pc.fingerprint()


def test_memmaps_are_read_only(panel):
    for arr in (panel.features, panel.labels, panel.time, panel.group, panel.reference, panel.run):
        assert isinstance(arr, np.memmap)
        with pytest.raises(ValueError):
            arr[0] = arr[0]


def test_run_is_correct_across_a_gap_and_a_group_boundary(tmp_path):
    cols = make_columns()
    _, path = write(tmp_path, cols)
    panel = ArrayPanel.open(str(path))
    expected = run_lengths(cols)
    np.testing.assert_array_equal(panel.run, expected)
    assert expected[GAP_AT] == 1 and expected[GAP_AT - 1] == GAP_AT
    assert expected[GROUP_SIZES[10]] == 1          # first row of group 20


def test_verify_detects_a_drifted_file(tmp_path):
    _, path = write(tmp_path)
    ArrayPanel.open(str(path), verify=True)
    raw = bytearray((path / "reference.npy").read_bytes())
    raw[-1] ^= 0xFF
    (path / "reference.npy").write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="sha256"):
        ArrayPanel.open(str(path), verify=True)


def _unsorted(c):
    for k in c:
        c[k] = c[k][::-1].copy()
    return c


def _dup(c):
    c["t"][1] = c["t"][0]
    return c


def _nan_time(c):
    c["t"] = c["t"].astype(float)
    c["t"][3] = np.nan
    return c


def _inf_group(c):
    c["g"] = c["g"].astype(float)
    c["g"][3] = np.inf
    return c


def _ref_zero(c):
    c["ref"][4] = 0.0
    return c


def _ref_nan(c):
    c["ref"][4] = np.nan
    return c


def _feature_nan(c):
    c["f1"][7] = np.nan
    return c


def _feature_inf(c):
    c["f1"][7] = np.inf
    return c


def _ragged(c):
    c["f2"] = c["f2"][:-1]
    return c


def _no_label(c):
    del c["y_3"]
    return c


@pytest.mark.parametrize("mutate", [
    _unsorted, _dup, _nan_time, _inf_group, _ref_zero, _ref_nan, _feature_nan, _feature_inf,
    _ragged, _no_label])
def test_writer_refuses_bad_columns(tmp_path, mutate):
    cols = mutate(make_columns())
    node = ArrayPanelWrite("w", params(tmp_path / "bad"))
    with pytest.raises(ValueError):
        node.run(ctx(tmp_path), {"columns": cols})
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob("bad*"))


def test_writer_allows_nan_in_a_nullable_column(tmp_path):
    cols = make_columns()
    cols["f1"][7] = np.nan
    _, path = write(tmp_path, cols, nullable=["f1"])
    assert np.isnan(ArrayPanel.open(str(path)).features[7, 1])


def test_writer_refuses_an_existing_path(tmp_path):
    write(tmp_path)
    with pytest.raises(ValueError, match="exists"):
        write(tmp_path)


def test_writer_default_deny_and_required_params(tmp_path):
    with pytest.raises(ConfigError):
        ArrayPanelWrite("w", params(tmp_path / "p", typo=1))
    for knob in ("time", "group", "reference", "label_prefix", "horizons", "step_ms", "path"):
        bad = params(tmp_path / "p")
        del bad[knob]
        with pytest.raises(ConfigError):
            ArrayPanelWrite("w", bad)
    for over in ({"horizons": [3, 1]}, {"horizons": []}, {"step_ms": 0}, {"drop": "junk"}):
        with pytest.raises(ConfigError):
            ArrayPanelWrite("w", params(tmp_path / "p", **over))


def test_unit_contract_labels_are_stored_as_given_and_pit_is_uniform(tmp_path):
    """y = ret / ref is stored unchanged; a Gaussian with sigma = vol / ref gives uniform PIT."""
    rng = np.random.default_rng(3)
    n = 4000
    ref = rng.uniform(0.002, 0.01, n)
    vol = ref * rng.uniform(0.5, 1.5, n)
    ret = rng.normal(size=n) * vol
    y = ret / ref
    cols = {"t": T0 + np.arange(n, dtype=np.int64) * STEP, "g": np.zeros(n, dtype=np.int64),
            "ref": ref, "y_1": y, "f0": rng.normal(size=n)}
    node = ArrayPanelWrite("w", params(tmp_path / "u", horizons=[1], drop=[]))
    node.run(ctx(tmp_path), {"columns": cols})
    panel = ArrayPanel.open(str(tmp_path / "u"))
    np.testing.assert_array_equal(panel.labels[:, 0], y)
    np.testing.assert_array_equal(panel.reference, ref)
    z = panel.labels[:, 0] / (vol / panel.reference)
    pit = np.array([0.5 * (1 + math.erf(v / math.sqrt(2))) for v in z])
    counts, _ = np.histogram(pit, bins=10, range=(0, 1))
    assert counts.min() > 0.8 * n / 10 and counts.max() < 1.2 * n / 10


def test_from_source_resolves_through_the_store(tmp_path, monkeypatch):
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(acquire_module, "utc_now",
                        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"))
    archive = tmp_path / "archive"
    archive.mkdir()
    ArrayPanelWrite("w", params(archive / "px")).run(ctx(tmp_path), {"columns": make_columns()})
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "blobs", "catalog_source": "x", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "blobs", "files", "backfill")
    spec = {"root": root.root, "source": "blobs", "stream": "files", "relpath": "px"}
    panel = ArrayPanel.from_source(dict(spec, verify=True))
    assert panel.horizons == HORIZONS and panel.features.shape[1] == 6
    direct = ArrayPanel.open(str(archive / "px"))
    assert panel.fingerprint() == direct.fingerprint()
    with pytest.raises(ValueError):
        ArrayPanel.from_source(dict(spec, relpath="nope"))
    with pytest.raises(ValueError, match="unknown"):
        ArrayPanel.from_source(dict(spec, typo=1))


# --- ColumnSelector -----------------------------------------------------------------------


def test_selector_all_names_prefixes_and_exclusions(panel):
    cols = panel.columns
    assert ColumnSelector({}).select(cols) == [0, 1, 2, 3, 4, 5]
    assert ColumnSelector({"prefixes": ["seq__"]}).select(cols) == [4, 5]
    assert ColumnSelector({"names": ["f3", "f0"]}).select(cols) == [0, 3]       # panel order
    assert ColumnSelector({"except_prefixes": ["seq__"]}).select(cols) == [0, 1, 2, 3]
    assert ColumnSelector({"prefixes": ["f"], "except_names": ["f1"]}).select(cols) == [0, 2, 3]
    assert ColumnSelector({"names": ["f0"], "prefixes": ["seq__"]}).select(cols) == [0, 4, 5]


@pytest.mark.parametrize("spec", [
    {"typo": ["f0"]},
    {"names": ["nope"]},
    {"except_names": ["nope"]},
    {"prefixes": ["zzz"]},
    {"except_prefixes": ["zzz"]},
    {"names": "f0"},
    {"except_prefixes": ["f", "seq__"]},       # empty result
])
def test_selector_refuses(panel, spec):
    with pytest.raises(ValueError):
        ColumnSelector(spec).select(panel.columns)


# --- PanelBands ---------------------------------------------------------------------------


def long_panel(tmp_path, days=6):
    """One group, one bar every 6 hours so day-sized bands hold a few rows each."""
    step = 6 * 3_600_000
    n = days * 4
    t0 = 1_700_006_400_000                       # a UTC midnight
    cols = {"t": t0 + np.arange(n, dtype=np.int64) * step, "g": np.zeros(n, dtype=np.int64),
            "ref": np.ones(n), "y_1": np.ones(n), "y_2": np.ones(n), "f0": np.arange(n, dtype=float)}
    node = ArrayPanelWrite("w", {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
                                 "horizons": [1, 2], "step_ms": step, "path": str(tmp_path / "long")})
    node.run(ctx(tmp_path), {"columns": cols})
    return ArrayPanel.open(str(tmp_path / "long")), t0, step


def bands_for(panel, t0, step, **over):
    base = {"assets": [0], "train_end_ms": t0 + 3 * DAY_MS - 1,
            "val_start_ms": t0 + 3 * DAY_MS - 1 + panel.label_span_ms + 1,
            "val_end_ms": t0 + 5 * DAY_MS - 1, "cal_days": 1, "min_run": 1}
    base.update(over)
    return PanelBands(panel, **base)


def test_bands_cut_cal_inside_train_and_purge_by_label_span(tmp_path):
    panel, t0, step = long_panel(tmp_path)
    span = panel.label_span_ms
    b = bands_for(panel, t0, step)
    cal_start = b.train_end_ms - DAY_MS + 1
    assert b.cal_start_ms == cal_start
    t = panel.time
    assert set(panel.time[b.cal_idx]) <= set(t[(t >= cal_start) & (t <= b.train_end_ms)])
    assert (t[b.cal_idx] >= cal_start).all() and (t[b.cal_idx] <= b.train_end_ms).all()
    assert (t[b.fit_idx] + span < cal_start).all()
    # the row whose label ends EXACTLY at cal_start is excluded (end >= start reaches)
    boundary = np.flatnonzero(t + span == cal_start)
    assert len(boundary) == 1 and boundary[0] not in set(b.fit_idx.tolist())
    assert (t[b.val_idx] >= b.val_start_ms).all() and (t[b.val_idx] <= b.val_end_ms).all()
    # no band's label reaches the next band
    assert (t[b.cal_idx] + span < b.val_start_ms).all()
    assert (t[b.fit_idx] + span < t[b.cal_idx].min()).all()
    assert not set(b.fit_idx) & set(b.cal_idx) and not set(b.cal_idx) & set(b.val_idx)


def test_bands_refuse_embargo_not_longer_than_the_span_and_bad_cal_days(tmp_path):
    panel, t0, step = long_panel(tmp_path)
    span = panel.label_span_ms
    train_end = t0 + 3 * DAY_MS - 1
    with pytest.raises(ValueError, match="embargo"):
        bands_for(panel, t0, step, val_start_ms=train_end + span)
    bands_for(panel, t0, step, val_start_ms=train_end + span + 1)
    for over in ({"cal_days": 0}, {"min_run": 0}, {"val_end_ms": t0}, {"assets": [99]}, {"assets": []}):
        with pytest.raises(ValueError):
            bands_for(panel, t0, step, **over)


def test_bands_drop_rows_without_labels_or_run(tmp_path):
    hour, n, gap = 3_600_000, 200, 140
    t = T0 + np.arange(n, dtype=np.int64) * hour
    t[gap:] += 5 * hour                                  # an injected gap before row 140
    y1, y2 = np.ones(n), np.ones(n)
    y1[-1:], y2[-2:] = np.nan, np.nan                    # no label past the group's end
    cols = {"t": t, "g": np.full(n, 10, dtype=np.int64), "ref": np.ones(n),
            "y_1": y1, "y_2": y2, "f0": np.arange(n, dtype=float)}
    ArrayPanelWrite("w", {"time": "t", "group": "g", "reference": "ref", "label_prefix": "y_",
                          "horizons": [1, 2], "step_ms": hour, "path": str(tmp_path / "h")}
                    ).run(ctx(tmp_path), {"columns": cols})
    panel = ArrayPanel.open(str(tmp_path / "h"))
    b = PanelBands(panel, [10], int(t[150]), int(t[150]) + panel.label_span_ms + 1,
                   int(t[-1]), cal_days=1, min_run=5)
    rows = set(np.concatenate([b.fit_idx, b.cal_idx, b.val_idx]).tolist())
    assert {0, 1, 2, 3, gap, gap + 1, gap + 2, gap + 3, n - 2, n - 1}.isdisjoint(rows)
    assert {4, gap + 4, n - 3} <= rows
    assert np.isfinite(panel.labels[sorted(rows)]).all() and (panel.run[sorted(rows)] >= 5).all()
    assert (panel.group[sorted(rows)] == 10).all()


def test_bands_only_include_the_named_assets(tmp_path):
    panel, _, _ = long_panel(tmp_path)
    t = int(panel.time.min())
    with pytest.raises(ValueError, match="assets"):
        PanelBands(panel, [7], t, t + 2 * DAY_MS, t + 3 * DAY_MS, 1, 1)


def test_bands_for_train_and_score_are_identical_from_the_same_values(tmp_path):
    panel, t0, step = long_panel(tmp_path)
    a, b = bands_for(panel, t0, step), bands_for(panel, t0, step)
    assert a.rows_sha256() == b.rows_sha256()
    assert a.rows_sha256() != bands_for(panel, t0, step, cal_days=2).rows_sha256()


# --- PanelScaler --------------------------------------------------------------------------


def test_scaler_chunked_equals_one_shot_numpy(panel):
    rows = np.arange(5, 190, 2)
    cols = [0, 2, 4]
    s = PanelScaler.fit(panel, cols, rows, chunk_rows=7)
    x = np.asarray(panel.features)[rows][:, cols].astype(np.float64)
    np.testing.assert_allclose(s.mean, x.mean(axis=0), rtol=1e-9)
    np.testing.assert_allclose(s.std, x.std(axis=0), rtol=1e-9)
    out = s.transform(np.asarray(panel.features)[:10][:, cols], cols)
    np.testing.assert_allclose(out, (np.asarray(panel.features)[:10][:, cols] - s.mean) / s.std, atol=1e-5)


def test_scaler_missing_strategies(tmp_path):
    cols = make_columns()
    cols["f1"][3] = np.nan
    _, path = write(tmp_path, cols, nullable=["f1"])
    panel = ArrayPanel.open(str(path))
    rows = np.arange(0, 100)
    with pytest.raises(ValueError, match="missing"):
        PanelScaler.fit(panel, [1], rows)
    s = PanelScaler.fit(panel, [1], rows, missing="mean")
    ref = np.asarray(panel.features)[rows, 1].astype(np.float64)
    np.testing.assert_allclose(s.mean, [np.nanmean(ref)], rtol=1e-9)
    out = s.transform(np.asarray(panel.features)[:5][:, [1]], [1])
    assert out[3, 0] == 0.0 and np.isfinite(out).all()
    refuse = PanelScaler.fit(panel, [0], rows)
    with pytest.raises(ValueError, match="missing"):
        refuse.transform(np.array([[np.nan]], dtype=np.float32), [0])
    with pytest.raises(ValueError):
        PanelScaler.fit(panel, [0], rows, missing="zero")


def test_scaler_refuses_a_column_it_was_not_fit_on(panel):
    s = PanelScaler.fit(panel, [0, 1], np.arange(50))
    with pytest.raises(ValueError, match="column"):
        s.transform(np.zeros((2, 1), dtype=np.float32), [3])


# --- DevicePanel --------------------------------------------------------------------------

L, STRIDE = 4, 2
NEED = (L - 1) * STRIDE + 1


def device_panel(panel, spans, min_run=NEED, features=(0, 1), channels=(4, 5), window=L, stride=STRIDE):
    cols = sorted(set(features) | set(channels))
    scaler = PanelScaler.fit(panel, cols, np.arange(len(panel.time)))
    return DevicePanel.load(panel, list(features), list(channels), spans, scaler, "cpu",
                            window=window, stride=stride, min_run=min_run), scaler


def test_gather_equals_an_explicit_slice(panel):
    dev, scaler = device_panel(panel, [(60, 110)])
    idx = np.array([70, 80, 99])
    batch = dev.gather(torch.as_tensor(idx))
    feats = np.asarray(panel.features, dtype=np.float64)
    for b, g in enumerate(idx):
        rows = [g - STRIDE * k for k in reversed(range(L))]    # chronological, target last
        expect = scaler.transform(feats[rows][:, [4, 5]].astype(np.float32), [4, 5])
        np.testing.assert_allclose(batch.seq[b].numpy(), expect, atol=1e-5)
        np.testing.assert_allclose(
            batch.x[b].numpy(), scaler.transform(feats[[g]][:, [0, 1]].astype(np.float32), [0, 1])[0], atol=1e-5)
        np.testing.assert_allclose(batch.y[b].numpy(), panel.labels[g], atol=1e-6)
        assert batch.ref[b].item() == pytest.approx(panel.reference[g], rel=1e-6)
        assert batch.group[b].item() == panel.group[g]
    assert batch.seq.shape == (3, L, 2) and batch.x.shape == (3, 2) and batch.y.shape == (3, 2)


def test_window_never_reads_rows_after_t(tmp_path):
    cols = make_columns()
    _, a = write(tmp_path, cols, name="a")
    later = dict(cols)
    later["seq__a"] = cols["seq__a"].copy()
    later["seq__a"][81:] += 100.0                    # perturb only rows after t = 80
    _, b = write(tmp_path, later, name="b")
    pa_, pb = ArrayPanel.open(str(a)), ArrayPanel.open(str(b))
    scaler_cols = [4, 5]
    scaler = PanelScaler.fit(pa_, [0] + scaler_cols, np.arange(60))        # fit rows precede the change
    ga = DevicePanel.load(pa_, [0], scaler_cols, [(60, 110)], scaler, "cpu", window=L, stride=STRIDE, min_run=NEED)
    gb = DevicePanel.load(pb, [0], scaler_cols, [(60, 110)], scaler, "cpu", window=L, stride=STRIDE, min_run=NEED)
    idx = torch.as_tensor([80])
    np.testing.assert_array_equal(ga.gather(idx).seq.numpy(), gb.gather(idx).seq.numpy())
    assert not np.array_equal(ga.gather(torch.as_tensor([90])).seq.numpy(),
                              gb.gather(torch.as_tensor([90])).seq.numpy())


def test_window_longer_than_min_run_refuses(panel):
    with pytest.raises(ValueError, match="min_run"):
        device_panel(panel, [(60, 110)], min_run=NEED - 1)


def test_span_extended_load_equals_the_training_time_gather(panel):
    wide, _ = device_panel(panel, [(10, 190)])
    narrow, _ = device_panel(panel, [(80, 81)])           # forecast path: one row, extended back
    idx = torch.as_tensor([80])
    a, b = wide.gather(idx), narrow.gather(idx)
    np.testing.assert_array_equal(a.seq.numpy(), b.seq.numpy())
    np.testing.assert_array_equal(a.x.numpy(), b.x.numpy())
    with pytest.raises(ValueError, match="not loaded"):
        narrow.gather(torch.as_tensor([120]))


def test_device_panel_holds_only_selected_columns_and_no_window_array(panel):
    dev, _ = device_panel(panel, [(60, 110)])
    tensors = {k: v for k, v in vars(dev).items() if isinstance(v, torch.Tensor)}
    assert tensors
    assert sum(v.shape[1] for k, v in tensors.items() if k in ("x_data", "seq_data")) == 4
    assert all(L not in v.shape[1:] for v in tensors.values())
    dev.release()
    assert not [v for v in vars(dev).values() if isinstance(v, torch.Tensor)]


def test_device_panel_without_channels_has_no_seq(panel):
    dev, _ = device_panel(panel, [(60, 110)], channels=(), window=1, stride=1, min_run=1)
    assert dev.gather(torch.as_tensor([70])).seq is None


def test_module_names_are_public_api():
    assert set(ap.__all__) >= {"ArrayPanelWrite", "ArrayPanel", "ColumnSelector", "PanelBands",
                               "PanelScaler", "DevicePanel", "DAY_MS", "NODE_KINDS"}
