"""The ``device`` knob and the adapter-state half of the artifact protocol.

Two independent additions to the torch pack, tested together because they
share one property: BOTH must be invisible to a document that does not use
them. A run with no ``device`` must be bit-for-bit what it always was, and
an adapter with no fitted state must write the sidecar it always wrote.
"""

import hashlib
import json
import pathlib

import pytest

torch = pytest.importorskip("torch")

from dskit.pipeline.libs.torch import (  # noqa: E402
    DeclaredPredict,
    DeclaredTrain,
    LinearRegressor,
    RowVectorAdapter,
    TorchAdapter,
)
from dskit.pipeline.node import NodeContext  # noqa: E402


def ctx(tmp_path, key="k"):
    run = pathlib.Path(tmp_path) / "run"
    (run / "artifacts" / key).mkdir(parents=True, exist_ok=True)
    return NodeContext(name="t", asof="2026-08-16", run_dir=str(run))


def flat_rows(n=24):
    return [{"x1": i * 0.1, "x2": (i % 3) * 0.5, "y": i * 0.02} for i in range(n)]


FLAT = {"features": ["x1", "x2"], "label": "y", "epochs": 2, "lr": 0.05}


# ---------------------------------------------------------------------------
# device — the default must change nothing
# ---------------------------------------------------------------------------


def test_no_device_is_the_old_behaviour_bit_for_bit(tmp_path):
    """The knob's whole safety claim: omitting it reproduces the previous
    fit exactly, weight for weight."""
    a = LinearRegressor("k", FLAT, mode="train").run(
        ctx(tmp_path), {"rows": flat_rows()}
    )
    b = LinearRegressor("k", {**FLAT, "device": "cpu"}, mode="train").run(
        ctx(tmp_path / "b"), {"rows": flat_rows()}
    )
    sa = torch.load(a["artifact_path"], map_location="cpu", weights_only=True)
    sb = torch.load(b["artifact_path"], map_location="cpu", weights_only=True)
    assert set(sa) == set(sb)
    for key in sa:
        assert torch.equal(sa[key], sb[key]), key
    assert a["metrics"]["device"] == "cpu"


def test_the_device_is_recorded_in_metrics(tmp_path):
    out = LinearRegressor("k", FLAT, mode="train").run(
        ctx(tmp_path), {"rows": flat_rows()}
    )
    assert out["metrics"]["device"] == "cpu"


@pytest.mark.parametrize("bad", [17, "", True])
def test_a_non_string_device_is_refused_by_name_at_plan(bad):
    problems = LinearRegressor.validate_params({**FLAT, "device": bad})
    assert any("device must be null or a torch device string" in p for p in problems)


def test_an_absent_device_fails_at_the_move_not_silently(tmp_path):
    """A plan machine may have no GPU, so availability is not a plan-time
    check — but the fit must refuse rather than quietly running on CPU."""
    with pytest.raises(Exception) as caught:
        LinearRegressor(
            "k", {**FLAT, "device": "definitely-not-a-device"}, mode="train"
        ).run(ctx(tmp_path), {"rows": flat_rows()})
    assert (
        "definitely-not-a-device" in str(caught.value).lower()
        or "device" in str(caught.value).lower()
    )


def test_the_row_vector_adapter_moves_its_own_two_tuple():
    x, y = RowVectorAdapter({}).to_device((torch.zeros(2, 2), torch.zeros(2)), "cpu")
    assert x.device.type == "cpu" and y.device.type == "cpu"


class _NoMoveAdapter(TorchAdapter):
    """Complete enough to construct, and deliberately silent about
    ``to_device`` — the seam's inherited default, under test below."""

    def prepare(self, rows, params, *, where):
        raise AssertionError("not exercised")

    def select(self, batches, index):
        raise AssertionError("not exercised")

    def loss(self, module, batch):
        raise AssertionError("not exercised")

    def predict(self, module, record):
        raise AssertionError("not exercised")


def test_the_base_adapter_declines_rather_than_guessing():
    """An adapter that has not implemented the move gets its batch back —
    which meets a moved module as a loud device mismatch, not a wrong fit."""
    sentinel = object()
    assert _NoMoveAdapter({}).to_device(sentinel, "cuda") is sentinel


# ---------------------------------------------------------------------------
# adapter state — the save/reload gap
# ---------------------------------------------------------------------------


def test_a_stateless_adapter_writes_no_adapter_state_key(tmp_path):
    """Backward compatibility, stated as a test: an artifact from an adapter
    with no fitted state is exactly the sidecar this pack always wrote."""
    out = LinearRegressor("k", FLAT, mode="train").run(
        ctx(tmp_path), {"rows": flat_rows()}
    )
    sidecar = json.loads(
        pathlib.Path(out["artifact_path"]).with_suffix(".json").read_text()
    )
    assert "adapter_state" not in sidecar


class TableAdapter(RowVectorAdapter):
    """An adapter whose SERVING needs fitted state beyond the weights — the
    generic stand-in for any panel/lookup family. Module-level so a
    document can NAME it."""

    def __init__(self, params=None):
        super().__init__(params)
        self._table = None

    def fitted(self, module, train_batches, val_batches):
        _x, y = train_batches.payload
        self._table = {"mean_label": float(y.mean())}

    def save_state(self, prefix):
        path = prefix + ".table.json"
        body = json.dumps(self._table, sort_keys=True, separators=(",", ":"))
        pathlib.Path(path).write_text(body, encoding="utf-8")
        return {
            "table": {
                "file": pathlib.Path(path).name,
                "digest": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        }

    def load_state(self, prefix, recorded):
        if not recorded:
            raise ValueError("this adapter needs its fitted table; none recorded")
        path = pathlib.Path(prefix + ".table.json")
        if not path.is_file():
            raise ValueError(f"fitted table {str(path)!r} is not on disk")
        body = path.read_text(encoding="utf-8")
        if (
            hashlib.sha256(body.encode("utf-8")).hexdigest()
            != (recorded["table"]["digest"])
        ):
            raise ValueError("the table on disk is not the one this artifact wrote")
        self._table = json.loads(body)

    def predict(self, module, record):
        if self._table is None:
            raise ValueError("neither fitted nor restored — no table to serve")
        return self._table["mean_label"]


STATEFUL = {
    "module": "torch.nn.Linear",
    "module_params": {"in_features": 2, "out_features": 1},
    "adapter": f"{TableAdapter.__module__}:{TableAdapter.__qualname__}",
    **FLAT,
    "epochs": 1,
}


def fit_stateful(tmp_path):
    return DeclaredTrain("k", STATEFUL, mode="train").run(
        ctx(tmp_path), {"rows": flat_rows()}
    )


def test_the_fitted_state_is_persisted_and_hashed(tmp_path):
    out = fit_stateful(tmp_path)
    stem = pathlib.Path(out["artifact_path"]).with_suffix("")
    sidecar = json.loads(stem.with_suffix(".json").read_text())
    entry = sidecar["adapter_state"]["table"]
    assert entry["file"] == "model.table.json"
    assert pathlib.Path(str(stem) + ".table.json").is_file()


def test_a_restored_model_serves_the_same_beliefs(tmp_path):
    """The gap this closes: before, mode='load' rebuilt an adapter with no
    table and serving answered from nothing."""
    out = fit_stateful(tmp_path)
    record = {"x1": 0.1, "x2": 0.5}
    expected = out["signal"].predict(record)

    # torch-predict owns only the model knobs, not the training ones.
    predict_params = {
        k: v
        for k, v in STATEFUL.items()
        if k in ("module", "module_params", "adapter", "adapter_params")
    }
    for node in (
        DeclaredTrain("k", STATEFUL, mode="load", artifact=out["artifact_path"]),
        DeclaredPredict(
            "p", predict_params, mode="load", artifact=out["artifact_path"]
        ),
    ):
        restored = node.run(ctx(tmp_path, node.key), {})["signal"]
        assert restored.loaded is True
        assert restored.predict(record) == expected
        assert restored.adapter._table == out["signal"].adapter._table


def test_a_missing_state_file_raises_it_never_falls_back(tmp_path):
    out = fit_stateful(tmp_path)
    stem = str(pathlib.Path(out["artifact_path"]).with_suffix(""))
    pathlib.Path(stem + ".table.json").unlink()
    with pytest.raises(ValueError, match="could not restore the fitted state"):
        DeclaredTrain("k", STATEFUL, mode="load", artifact=out["artifact_path"]).run(
            ctx(tmp_path, "k2"), {}
        )


def test_a_tampered_state_file_is_refused_by_digest(tmp_path):
    out = fit_stateful(tmp_path)
    stem = str(pathlib.Path(out["artifact_path"]).with_suffix(""))
    payload = json.loads(pathlib.Path(stem + ".table.json").read_text())
    payload["mean_label"] = 0.999999
    pathlib.Path(stem + ".table.json").write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(ValueError, match="could not restore the fitted state"):
        DeclaredTrain("k", STATEFUL, mode="load", artifact=out["artifact_path"]).run(
            ctx(tmp_path, "k3"), {}
        )
