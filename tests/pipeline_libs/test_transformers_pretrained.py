"""libs/transformers.py, the pretrained doorway (ADR-0082/0083).

Weights enter as a verified WORM snapshot pinned by manifest hash; the
``encode`` / ``classify`` kinds turn text into feature rows, ``forecast``
answers zero-shot from a windowed row. Everything runs offline: every model
is built fresh from a tiny config and ``save_pretrained``'d, then ACQUIRED
through the huggingface connector with both hub seams scripted (the
download copies the saved directory), so the nodes read exactly what a
real pull would have laid down — including the manifest they are pinned by.
"""

import atexit
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.libs.huggingface import SNAPSHOT_STREAM, HuggingFaceHubConnector
from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs.transformers import (
    NODE_KINDS,
    POOLINGS,
    ForecastSignal,
    PretrainedClassify,
    PretrainedEncode,
    PretrainedForecast,
)
from dskit.pipeline.node import NodeContext

pytest.importorskip("torch")
pytest.importorskip("transformers")

SHA = "0123456789abcdef0123456789abcdef01234567"
COMMITTED = datetime(2026, 2, 1, tzinfo=timezone.utc)
VOCAB = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "up", "down", "flat",
         "rates", "cut", "hike", "earnings", "beat", "miss"]
WIDTH = 8
CONTEXT, PREDICTION = 8, 2
CONTEXT_FEATURES = [f"x{i}" for i in range(CONTEXT)]
TEXT_RECORDS = [
    {"asof_ms": 1, "symbol": "AAA", "text": "rates cut", "label": 0.2},
    {"asof_ms": 2, "symbol": "BBB", "text": "earnings miss badly", "label": 0.7},
    {"asof_ms": 3, "symbol": "CCC", "text": "hike", "label": 0.4},
]
WINDOW_ROW = {f"x{i}": round(0.1 * i - 0.3, 3) for i in range(CONTEXT)}
WINDOW_ROW.update({"asof_ms": 9, "contract": "AAA", "label": 0.05})
PRETRAINED_ROLES = {
    "transformers-encode": "tensor",
    "transformers-classify": "tensor",
    "transformers-forecast": "signal",
}


class LocalDirHub(HuggingFaceHubConnector):
    """The pack with both hub seams scripted: a fixed commit, a local copy."""

    sources = {}

    def resolve(self, repo_id, revision, repo_type, token, timeout_s):
        return {"sha": SHA, "last_modified": COMMITTED}

    def download(self, repo_id, revision, repo_type, allow_patterns, ignore_patterns,
                 token, local_dir):
        src = type(self).sources[repo_id]
        for name in os.listdir(src):
            shutil.copy2(os.path.join(src, name), os.path.join(local_dir, name))


def _build_models(into):
    """Three tiny models, saved with ``save_pretrained``: an encoder + its
    tokenizer, a two-label classifier, and a PatchTST forecaster."""
    import torch
    from transformers import (
        BertConfig,
        BertForSequenceClassification,
        BertModel,
        BertTokenizer,
        PatchTSTConfig,
        PatchTSTForPrediction,
    )

    os.makedirs(into)
    vocab_path = os.path.join(into, "vocab.txt")
    with open(vocab_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(VOCAB) + "\n")
    tokenizer = BertTokenizer(vocab_path)
    bert = dict(vocab_size=len(VOCAB), hidden_size=WIDTH, num_hidden_layers=1,
                num_attention_heads=2, intermediate_size=2 * WIDTH,
                max_position_embeddings=16)
    torch.manual_seed(7)
    enc = os.path.join(into, "encoder")
    BertModel(BertConfig(**bert)).save_pretrained(enc)
    tokenizer.save_pretrained(enc)
    cls = os.path.join(into, "classifier")
    BertForSequenceClassification(BertConfig(
        **bert, num_labels=2, id2label={0: "negative", 1: "positive"},
        label2id={"negative": 0, "positive": 1},
    )).save_pretrained(cls)
    tokenizer.save_pretrained(cls)
    fc = os.path.join(into, "forecaster")
    PatchTSTForPrediction(PatchTSTConfig(
        num_input_channels=1, context_length=CONTEXT, prediction_length=PREDICTION,
        patch_length=2, patch_stride=2, d_model=8, num_attention_heads=2,
        num_hidden_layers=1, ffn_dim=16,
    )).save_pretrained(fc)
    return {"enc": enc, "cls": cls, "fc": fc}


def _acquire(root_dir, model_dirs):
    """Acquire each saved model through the connector; return the pins."""
    root = OnboardingRoot.create(root_dir)
    registry = root.registry()
    pins = {"root": root.root}
    for name, path in model_dirs.items():
        repo = f"acme/{name}"
        LocalDirHub.sources[repo] = path
        vid = registry.register("source_config", {
            "name": name,
            "catalog_source": "huggingface.co",
            "connector": "tests.pipeline_libs.test_transformers_pretrained:LocalDirHub",
            "config": {"repo_id": repo, "revision": "main"},
        }, origin="test")
        registry.transition(vid, "active", origin="test")
        summary = run_acquisition(root, registry, name, SNAPSHOT_STREAM, "backfill")
        pins[name] = registry.get(summary["snapshot"]).payload["manifest_hash"]
    return pins


_SHARED = {}


def shared():
    """One read-only estate for the module: models built and acquired once."""
    if not _SHARED:
        base = tempfile.mkdtemp(prefix="dskit-pretrained-")
        atexit.register(shutil.rmtree, base, True)
        models = _build_models(os.path.join(base, "models"))
        _SHARED.update(_acquire(os.path.join(base, "ob"), models))
        _SHARED["models"] = models
    return _SHARED


def _ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


def _pin(name, **over):
    est = shared()
    params = {"root": est["root"], "snapshot": est[name]}
    params.update(over)
    return params


def _expected_mean_embedding(model_dir, text):
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModel.from_pretrained(model_dir, local_files_only=True).eval()
    with torch.no_grad():
        hidden = model(**tokenizer([text], return_tensors="pt")).last_hidden_state
    return hidden[0].mean(0).tolist()


def _expected_forecast(model_dir, values, step):
    import torch
    import transformers
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
    model = getattr(transformers, config.architectures[0]).from_pretrained(
        model_dir, local_files_only=True
    ).eval()
    with torch.no_grad():
        out = model(past_values=torch.tensor(values, dtype=torch.float32).reshape(1, -1, 1))
    return float(out.prediction_outputs[0, step - 1, 0])


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------


class TestEncode:
    def test_rows_carry_the_declared_fields_and_one_column_per_dimension(self, tmp_path):
        node = PretrainedEncode("emb", _pin(
            "enc", text_field="text", carry_fields=["asof_ms", "symbol", "label"]))
        out = node.run(_ctx(tmp_path), {"records": [dict(r) for r in TEXT_RECORDS]})
        rows = out["rows"]
        assert len(rows) == 3
        columns = [f"emb_{i}" for i in range(WIDTH)]
        assert all(sorted(row) == sorted(["asof_ms", "symbol", "label"] + columns)
                   for row in rows)
        assert [row["symbol"] for row in rows] == ["AAA", "BBB", "CCC"]
        assert all(math.isfinite(row[c]) for row in rows for c in columns)
        assert out["metrics"] == {"n_rows": 3, "n_records": 3, "n_dropped": 0,
                                  "n_columns": WIDTH}

    def test_the_numbers_are_the_models_pooled_hidden_state(self, tmp_path):
        node = PretrainedEncode("emb", _pin("enc", text_field="text"))
        out = node.run(_ctx(tmp_path), {"records": [dict(r) for r in TEXT_RECORDS]})
        expected = _expected_mean_embedding(shared()["models"]["enc"], "hike")
        got = [out["rows"][2][f"emb_{i}"] for i in range(WIDTH)]
        assert got == pytest.approx(expected, abs=1e-4)

    def test_records_without_text_yield_no_row_and_are_counted(self, tmp_path):
        records = [dict(TEXT_RECORDS[0]), {"asof_ms": 4, "text": None},
                   {"asof_ms": 5}, {"asof_ms": 6, "text": ""}]
        node = PretrainedEncode("emb", _pin("enc", text_field="text",
                                             carry_fields=["asof_ms"]))
        out = node.run(_ctx(tmp_path), {"records": records})
        assert [row["asof_ms"] for row in out["rows"]] == [1]
        assert out["metrics"]["n_dropped"] == 3 and out["metrics"]["n_records"] == 4

    def test_batching_does_not_change_the_numbers(self, tmp_path):
        records = [dict(r) for r in TEXT_RECORDS]
        one = PretrainedEncode("a", _pin("enc", text_field="text", batch_size=1))
        many = PretrainedEncode("b", _pin("enc", text_field="text", batch_size=3))
        rows_one = one.run(_ctx(tmp_path, "a"), {"records": records})["rows"]
        rows_many = many.run(_ctx(tmp_path, "b"), {"records": records})["rows"]
        for x, y in zip(rows_one, rows_many):
            assert [x[f"emb_{i}"] for i in range(WIDTH)] == pytest.approx(
                [y[f"emb_{i}"] for i in range(WIDTH)], abs=1e-4)

    def test_pooling_is_a_closed_table_and_cls_differs_from_mean(self, tmp_path):
        assert POOLINGS == ("mean", "cls", "max")
        records = [dict(TEXT_RECORDS[1])]
        mean = PretrainedEncode("m", _pin("enc", text_field="text")).run(
            _ctx(tmp_path, "m"), {"records": records})["rows"][0]
        cls = PretrainedEncode("c", _pin("enc", text_field="text", pooling="cls")).run(
            _ctx(tmp_path, "c"), {"records": records})["rows"][0]
        assert any(abs(mean[f"emb_{i}"] - cls[f"emb_{i}"]) > 1e-6 for i in range(WIDTH))
        assert PretrainedEncode.validate_params(
            _pin("enc", text_field="text", pooling="median"))

    def test_a_column_may_not_take_a_carried_fields_name(self, tmp_path):
        node = PretrainedEncode("emb", _pin("enc", text_field="text",
                                             carry_fields=["emb_0", "asof_ms"]))
        with pytest.raises(ValueError, match="emb_0"):
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})

    def test_the_prefix_is_a_knob(self, tmp_path):
        node = PretrainedEncode("emb", _pin("enc", text_field="text", prefix="news_"))
        out = node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert sorted(out["rows"][0]) == [f"news_{i}" for i in range(WIDTH)]

    def test_attribute_rows_are_read_like_dicts(self, tmp_path):
        record = SimpleNamespace(asof_ms=1, text="rates cut")
        node = PretrainedEncode("emb", _pin("enc", text_field="text",
                                             carry_fields=["asof_ms"]))
        out = node.run(_ctx(tmp_path), {"records": [record]})
        assert out["rows"][0]["asof_ms"] == 1

    @pytest.mark.parametrize(
        "over, needle",
        [
            ({"conformance_unknown_knob": 1}, "conformance_unknown_knob"),
            ({"snapshot": "abc"}, "snapshot"),
            ({"snapshot": "F" * 64}, "snapshot"),
            ({"root": ""}, "root"),
            ({"stream": "Bad Name"}, "stream"),
            ({"text_field": ""}, "text_field"),
            ({"carry_fields": "asof_ms"}, "carry_fields"),
            ({"carry_fields": ["a", "a"]}, "carry_fields"),
            ({"prefix": 3}, "prefix"),
            ({"max_length": 0}, "max_length"),
            ({"batch_size": "8"}, "batch_size"),
        ],
    )
    def test_validators_refuse_junk_by_name(self, over, needle):
        params = {**_pin("enc", text_field="text"), **over}
        problems = PretrainedEncode.validate_params(params)
        assert problems and any(needle in p for p in problems)

    def test_text_field_is_required(self):
        problems = PretrainedEncode.validate_params(_pin("enc"))
        assert any("text_field" in p for p in problems)

    def test_an_unknown_snapshot_refuses_naming_the_hash(self, tmp_path):
        ghost = "f" * 64
        node = PretrainedEncode("emb", _pin("enc", text_field="text", snapshot=ghost))
        with pytest.raises(ValueError, match=ghost):
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})

    def test_a_one_shot_records_iterable_is_refused_unwalked(self):
        node = PretrainedEncode("emb", _pin("enc", text_field="text"))
        problems = node.validate_inputs({"records": iter([dict(TEXT_RECORDS[0])])})
        assert problems and "records" in problems[0]

    def test_the_snapshot_is_resolved_once_per_instance(self, tmp_path, monkeypatch):
        import dskit.onboarding.observations as seam

        calls = []
        real = seam.verified_payload_dir

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(seam, "verified_payload_dir", counting)
        node = PretrainedEncode("emb", _pin("enc", text_field="text"))
        node.run(_ctx(tmp_path, "a"), {"records": [dict(TEXT_RECORDS[0])]})
        node.run(_ctx(tmp_path, "b"), {"records": [dict(TEXT_RECORDS[1])]})
        assert len(calls) == 1


def test_a_tampered_snapshot_refuses_before_anything_loads(tmp_path):
    models = _build_models(str(tmp_path / "models"))
    pins = _acquire(str(tmp_path / "ob"), {"enc": models["enc"]})
    raw = os.path.join(pins["root"], "raw", "enc")
    [acq] = os.listdir(raw)
    target = os.path.join(raw, acq, "payload", SNAPSHOT_STREAM, "model.safetensors")
    with open(target, "r+b") as fh:
        fh.seek(64)
        fh.write(b"\xff\xff")
    node = PretrainedEncode("emb", {"root": pins["root"], "snapshot": pins["enc"],
                                    "text_field": "text"})
    with pytest.raises(ValueError, match="content drift"):
        node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_columns_come_from_id2label_and_the_probabilities_sum_to_one(self, tmp_path):
        node = PretrainedClassify("sent", _pin("cls", text_field="text",
                                                carry_fields=["asof_ms"]))
        out = node.run(_ctx(tmp_path), {"records": [dict(r) for r in TEXT_RECORDS]})
        for row in out["rows"]:
            assert sorted(row) == ["asof_ms", "p_negative", "p_positive"]
            assert 0.0 <= row["p_negative"] <= 1.0
            assert row["p_negative"] + row["p_positive"] == pytest.approx(1.0, abs=1e-6)
        assert out["metrics"]["n_columns"] == 2

    def test_the_prefix_has_its_own_default_and_is_a_knob(self, tmp_path):
        node = PretrainedClassify("sent", _pin("cls", text_field="text", prefix=""))
        out = node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert sorted(out["rows"][0]) == ["negative", "positive"]

    def test_pooling_is_narrowed_away(self):
        assert "pooling" not in PretrainedClassify._PARAMS
        problems = PretrainedClassify.validate_params(
            _pin("cls", text_field="text", pooling="mean"))
        assert problems and "pooling" in problems[0]

    def test_a_label_colliding_with_a_carried_field_refuses(self, tmp_path):
        node = PretrainedClassify("sent", _pin("cls", text_field="text",
                                                carry_fields=["p_positive"]))
        with pytest.raises(ValueError, match="p_positive"):
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------


class TestForecast:
    def test_the_signal_answers_the_horizon_step_of_the_models_forecast(self, tmp_path):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES, horizon=2))
        out = node.run(_ctx(tmp_path), {})
        signal = out["signal"]
        assert isinstance(signal, ForecastSignal)
        values = [WINDOW_ROW[f] for f in CONTEXT_FEATURES]
        expected = _expected_forecast(shared()["models"]["fc"], values, 2)
        assert signal.predict(dict(WINDOW_ROW)) == pytest.approx(expected, abs=1e-6)
        assert signal.predict(SimpleNamespace(**WINDOW_ROW)) == pytest.approx(expected, abs=1e-6)

    def test_the_default_horizon_is_the_first_step(self, tmp_path):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES))
        signal = node.run(_ctx(tmp_path), {})["signal"]
        values = [WINDOW_ROW[f] for f in CONTEXT_FEATURES]
        assert signal.predict(dict(WINDOW_ROW)) == pytest.approx(
            _expected_forecast(shared()["models"]["fc"], values, 1), abs=1e-6)

    def test_incomplete_or_non_numeric_rows_decline(self, tmp_path):
        signal = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES)).run(
            _ctx(tmp_path), {})["signal"]
        short = {k: v for k, v in WINDOW_ROW.items() if k != "x3"}
        assert signal.predict(short) is None
        assert signal.predict({**WINDOW_ROW, "x0": "abc"}) is None
        assert signal.predict({**WINDOW_ROW, "x0": float("nan")}) is None
        assert signal.predict({**WINDOW_ROW, "x0": True}) is None

    def test_metrics_and_provenance_name_the_pin(self, tmp_path):
        params = _pin("fc", features=CONTEXT_FEATURES, horizon=2)
        out = PretrainedForecast("fc", params).run(_ctx(tmp_path), {})
        assert out["metrics"] == {"restored": 1.0, "snapshot_digest": params["snapshot"],
                                  "context_length": CONTEXT, "horizon": 2}
        signal = out["signal"]
        assert signal.restored and signal.digest == params["snapshot"]
        assert signal.artifact_path.endswith(os.path.join("payload", SNAPSHOT_STREAM))

    def test_a_context_length_mismatch_refuses_at_load(self, tmp_path):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES[:6]))
        with pytest.raises(ValueError, match="context_length") as exc:
            node.run(_ctx(tmp_path), {})
        assert "6" in str(exc.value) and str(CONTEXT) in str(exc.value)

    def test_a_horizon_beyond_the_prediction_length_refuses_at_load(self, tmp_path):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES, horizon=3))
        with pytest.raises(ValueError, match="horizon") as exc:
            node.run(_ctx(tmp_path), {})
        assert str(PREDICTION) in str(exc.value)

    def test_mode_train_refuses_by_name(self, tmp_path):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES), mode="train")
        with pytest.raises(ValueError, match="train"):
            node.run(_ctx(tmp_path), {})

    def test_a_node_level_artifact_may_restate_the_hash_but_never_replace_it(self, tmp_path):
        params = _pin("fc", features=CONTEXT_FEATURES)
        restated = PretrainedForecast("fc", params, mode="load", artifact=params["snapshot"])
        assert restated.run(_ctx(tmp_path, "a"), {})["metrics"]["restored"] == 1.0
        other = PretrainedForecast("fc", params, mode="load", artifact="e" * 64)
        with pytest.raises(ValueError, match="one pin"):
            other.run(_ctx(tmp_path, "b"), {})
        path_pin = PretrainedForecast("fc", params, mode="load",
                                      artifact="/some/where/checkpoint")
        with pytest.raises(ValueError, match="one pin"):
            path_pin.run(_ctx(tmp_path, "c"), {})

    @pytest.mark.parametrize(
        "over, needle",
        [
            ({"features": []}, "features"),
            ({"features": ["x0", "x0"]}, "features"),
            ({"features": "x0"}, "features"),
            ({"horizon": 0}, "horizon"),
            ({"horizon": 1.5}, "horizon"),
            ({"snapshot": "zz"}, "snapshot"),
            ({"conformance_unknown_knob": 1}, "conformance_unknown_knob"),
        ],
    )
    def test_validators_refuse_junk_by_name(self, over, needle):
        params = {**_pin("fc", features=CONTEXT_FEATURES), **over}
        problems = PretrainedForecast.validate_params(params)
        assert problems and any(needle in p for p in problems)

    def test_features_are_required(self):
        with pytest.raises(ConfigError, match="features"):
            PretrainedForecast("fc", _pin("fc"))


# ---------------------------------------------------------------------------
# the conformance suite over the trio
# ---------------------------------------------------------------------------


def probes(tmp_path):
    est = shared()
    values = [WINDOW_ROW[f] for f in CONTEXT_FEATURES]
    expected = _expected_forecast(est["models"]["fc"], values, 1)

    def verify_loaded(out):
        signal = out["signal"]
        prediction = signal.predict(dict(WINDOW_ROW))
        return (
            out["metrics"].get("snapshot_digest") == est["fc"]
            and bool(signal.restored)
            and prediction is not None
            and abs(prediction - expected) < 1e-9
        )

    text_probe = dict(
        inputs={"records": [dict(r) for r in TEXT_RECORDS]},
        stream_ports=("records",),
        runnable=True,
    )
    return {
        "transformers-encode": NodeProbe(
            params=_pin("enc", text_field="text", carry_fields=["asof_ms"]),
            required=("root", "snapshot", "text_field"),
            **text_probe,
        ),
        "transformers-classify": NodeProbe(
            params=_pin("cls", text_field="text", carry_fields=["asof_ms"]),
            required=("root", "snapshot", "text_field"),
            **text_probe,
        ),
        "transformers-forecast": NodeProbe(
            params=_pin("fc", features=CONTEXT_FEATURES),
            required=("root", "snapshot", "features"),
            inputs={},
            stream_ports=(),
            runnable=True,
            load_artifact=est["fc"],
            verify_loaded=verify_loaded,
        ),
    }


PRETRAINED_KINDS = tuple(pair for pair in NODE_KINDS if pair[0] in PRETRAINED_ROLES)


def test_the_default_stream_is_the_connectors_stream_name():
    """The pack may not import the connector module (purity), so the two
    spellings of the FILE stream are pinned equal here — a drift would send
    every default-pinned document to a payload/ directory that is not there."""
    from dskit.pipeline.libs.transformers import DEFAULT_SNAPSHOT_STREAM

    assert DEFAULT_SNAPSHOT_STREAM == SNAPSHOT_STREAM


def test_the_trio_is_registered_with_its_roles():
    assert dict(PRETRAINED_KINDS) == {
        "transformers-encode": PretrainedEncode,
        "transformers-classify": PretrainedClassify,
        "transformers-forecast": PretrainedForecast,
    }
    assert {name: cls.role for name, cls in PRETRAINED_KINDS} == PRETRAINED_ROLES


TestPretrainedConformance = conformance_suite(
    registry=PRETRAINED_KINDS,
    module="dskit.pipeline.libs.transformers",
    probes=probes,
    expected_roles=PRETRAINED_ROLES,
    name="TestPretrainedConformance",
)
