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


#: The tiny BERT both text estates instantiate — one name, so the encoder,
#: the classifier heads and the bfloat16 copy are the SAME model shape.
BERT_CONFIG = dict(vocab_size=len(VOCAB), hidden_size=WIDTH, num_hidden_layers=1,
                   num_attention_heads=2, intermediate_size=2 * WIDTH,
                   max_position_embeddings=16)

#: The tiny PatchTST every forecaster variant starts from; the variants pass
#: their one changed knob as an override, never a second full spelling.
PATCHTST_CONFIG = dict(num_input_channels=1, context_length=CONTEXT,
                       prediction_length=PREDICTION, patch_length=2, patch_stride=2,
                       d_model=8, num_attention_heads=2, num_hidden_layers=1,
                       ffn_dim=16)


def _tokenizer(into):
    """The tiny word-piece tokenizer every text estate saves beside its model."""
    from transformers import BertTokenizer

    os.makedirs(into, exist_ok=True)
    vocab_path = os.path.join(into, "vocab.txt")
    with open(vocab_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(VOCAB) + "\n")
    return BertTokenizer(vocab_path)


def _build_models(into):
    """Three tiny models, saved with ``save_pretrained``: an encoder + its
    tokenizer, a two-label classifier, and a PatchTST forecaster."""
    import torch
    from transformers import (
        BertConfig,
        BertForSequenceClassification,
        BertModel,
        PatchTSTConfig,
        PatchTSTForPrediction,
    )

    tokenizer = _tokenizer(into)
    torch.manual_seed(7)
    enc = os.path.join(into, "encoder")
    BertModel(BertConfig(**BERT_CONFIG)).save_pretrained(enc)
    tokenizer.save_pretrained(enc)
    cls = os.path.join(into, "classifier")
    BertForSequenceClassification(BertConfig(
        **BERT_CONFIG, num_labels=2, id2label={0: "negative", 1: "positive"},
        label2id={"negative": 0, "positive": 1},
    )).save_pretrained(cls)
    tokenizer.save_pretrained(cls)
    fc = os.path.join(into, "forecaster")
    PatchTSTForPrediction(PatchTSTConfig(**PATCHTST_CONFIG)).save_pretrained(fc)
    return {"enc": enc, "cls": cls, "fc": fc}


def _build_edge_models(into):
    """The review round's estate: every snapshot the doorway must refuse — and
    the two (multi-label, bfloat16) it must read RIGHT rather than refuse."""
    import torch
    from transformers import (
        BertConfig,
        BertForSequenceClassification,
        BertModel,
        PatchTSTConfig,
        PatchTSTForPrediction,
        TimeSeriesTransformerConfig,
        TimeSeriesTransformerForPrediction,
    )

    tokenizer = _tokenizer(into)
    torch.manual_seed(11)
    out = {}

    def _text_model(name, model, with_tokenizer=True):
        out[name] = os.path.join(into, name)
        model.save_pretrained(out[name])
        if with_tokenizer:
            tokenizer.save_pretrained(out[name])
        return out[name]

    def _head(**over):
        return BertForSequenceClassification(BertConfig(**BERT_CONFIG, **over))

    # Config + weights and NOT ONE tokenizer file: AutoTokenizer answers a
    # specials-only vocabulary here rather than raising, so every text would
    # embed as [UNK] and the run would look fine.
    _text_model("naked", BertModel(BertConfig(**BERT_CONFIG)), with_tokenizer=False)
    _text_model("onelabel", _head(num_labels=1, id2label={0: "score"},
                                  label2id={"score": 0}))
    _text_model("mlab", _head(num_labels=2, problem_type="multi_label_classification",
                              id2label={0: "rates", 1: "earnings"},
                              label2id={"rates": 0, "earnings": 1}))
    _text_model("regr", _head(num_labels=2, problem_type="regression",
                              id2label={0: "low", 1: "high"},
                              label2id={"low": 0, "high": 1}))
    _text_model("bf16enc", BertModel(BertConfig(**BERT_CONFIG)).to(torch.bfloat16))
    _text_model("bf16fc", PatchTSTForPrediction(PatchTSTConfig(**PATCHTST_CONFIG)
                                                ).to(torch.bfloat16), with_tokenizer=False)

    # Pickle-only weights, the shape an old hub repository still has.
    # ``save_pretrained(safe_serialization=False)`` no longer writes one in
    # transformers 5, so the .bin is laid down by hand.
    binonly = _text_model("binonly", BertModel(BertConfig(**BERT_CONFIG)))
    torch.save(BertModel(BertConfig(**BERT_CONFIG)).state_dict(),
               os.path.join(binonly, "pytorch_model.bin"))
    os.unlink(os.path.join(binonly, "model.safetensors"))

    # Forecasters the default ``forecast`` hook cannot drive: one whose
    # forward wants more than ``past_values``, one whose head answers a
    # distribution tuple, one that reads three channels.
    _text_model("tst", TimeSeriesTransformerForPrediction(TimeSeriesTransformerConfig(
        prediction_length=PREDICTION, context_length=CONTEXT, lags_sequence=[1],
        num_time_features=1, d_model=8, encoder_layers=1, decoder_layers=1,
        encoder_attention_heads=2, decoder_attention_heads=2,
        encoder_ffn_dim=16, decoder_ffn_dim=16,
    )), with_tokenizer=False)
    _text_model("nll", PatchTSTForPrediction(
        PatchTSTConfig(**{**PATCHTST_CONFIG, "loss": "nll"})), with_tokenizer=False)
    _text_model("chan3", PatchTSTForPrediction(
        PatchTSTConfig(**{**PATCHTST_CONFIG, "num_input_channels": 3})),
        with_tokenizer=False)

    def _config_only(name, config):
        out[name] = os.path.join(into, name)
        config.save_pretrained(out[name])
        return out[name]

    # A snapshot that is nothing but a config: the library's own OSError.
    _config_only("configonly", BertConfig(**BERT_CONFIG))
    _config_only("fcconfigonly", PatchTSTConfig(
        **PATCHTST_CONFIG, architectures=["PatchTSTForPrediction"]))
    # ``architectures`` naming something that is not a model class at all.
    _config_only("notamodel", PatchTSTConfig(**PATCHTST_CONFIG,
                                             architectures=["BertConfig"]))
    _config_only("notaclass", PatchTSTConfig(**PATCHTST_CONFIG,
                                             architectures=["pipeline"]))
    return out


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
        models.update(_build_edge_models(os.path.join(base, "edge")))
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
    def test_non_contiguous_label_ids_are_refused(self):
        node = PretrainedClassify("sent", _pin("cls", text_field="text"))
        model = SimpleNamespace(
            config=SimpleNamespace(id2label={1: "negative", 2: "positive"})
        )
        with pytest.raises(ValueError, match="does not name 2 distinct"):
            node.column_names(model, 2)

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
# the review round: what the doorway must refuse, and what it must read right
# ---------------------------------------------------------------------------


def _expected_logits(model_dir, text):
    """The saved classifier's own logits for one text — the independent reference."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir, local_files_only=True
    ).eval()
    with torch.no_grad():
        return model(**tokenizer([text], return_tensors="pt")).logits[0]


class TestLoading:
    """What ``from_pretrained`` must never do quietly (B1, B2, m11, m12)."""

    def test_a_snapshot_without_tokenizer_files_refuses_naming_node_and_pin(self, tmp_path):
        # AutoTokenizer answers a specials-only vocabulary for a config+weights
        # directory instead of raising: every text would embed as [UNK] and the
        # run would look perfectly healthy.
        params = _pin("naked", text_field="text")
        node = PretrainedEncode("emb", params)
        with pytest.raises(ValueError, match="tokenizer") as exc:
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert "emb" in str(exc.value) and params["snapshot"] in str(exc.value)

    def test_the_trap_this_refusal_exists_for_is_real(self):
        # The pin behind the refusal above: the library really does synthesize a
        # vocabulary of nothing but special tokens. If this ever starts raising,
        # the guard's presence check is what still holds.
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            shared()["models"]["naked"], local_files_only=True)
        assert len(tokenizer) <= len(tokenizer.all_special_ids)

    def test_an_encoder_snapshot_in_the_classify_kind_refuses_naming_the_head(self, tmp_path):
        node = PretrainedClassify("sent", _pin("enc", text_field="text"))
        with pytest.raises(ValueError, match="classifier") as exc:
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert "sent" in str(exc.value) and "randomly initialized" in str(exc.value)

    def test_weights_this_kind_does_not_use_are_lawful(self, tmp_path):
        # The other half of the missing-keys rule: a classifier snapshot read
        # by the ENCODE kind leaves the head unused. Unused is not missing —
        # every weight this class needs came from the snapshot, so it runs.
        node = PretrainedEncode("emb", _pin("cls", text_field="text"))
        out = node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert out["metrics"]["n_columns"] == WIDTH and out["metrics"]["n_rows"] == 1

    def test_a_pickle_only_snapshot_refuses_naming_node_and_pin(self, tmp_path):
        params = _pin("binonly", text_field="text")
        node = PretrainedEncode("emb", params)
        with pytest.raises(ValueError, match="safetensors") as exc:
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert "emb" in str(exc.value) and params["snapshot"] in str(exc.value)

    @pytest.mark.parametrize(
        "make",
        [
            lambda: (PretrainedEncode("emb", _pin("configonly", text_field="text")),
                     {"records": [dict(TEXT_RECORDS[0])]}),
            lambda: (PretrainedClassify("sent", _pin("configonly", text_field="text")),
                     {"records": [dict(TEXT_RECORDS[0])]}),
            lambda: (PretrainedForecast("fc", _pin("fcconfigonly",
                                                   features=CONTEXT_FEATURES)), {}),
        ],
        ids=["encode", "classify", "forecast"],
    )
    def test_a_config_only_snapshot_refuses_naming_node_and_pin(self, tmp_path, make):
        node, inputs = make()
        with pytest.raises(ValueError, match="cannot be loaded") as exc:
            node.run(_ctx(tmp_path), inputs)
        assert node.key in str(exc.value) and node.params["snapshot"] in str(exc.value)


class TestClassifyHeads:
    """The head decides the activation — a table, and a refusal for the rest (B2, M6)."""

    def test_the_probabilities_are_the_softmax_of_the_saved_models_logits(self, tmp_path):
        import torch

        node = PretrainedClassify("sent", _pin("cls", text_field="text"))
        out = node.run(_ctx(tmp_path), {"records": [dict(r) for r in TEXT_RECORDS]})
        logits = _expected_logits(shared()["models"]["cls"], TEXT_RECORDS[1]["text"])
        expected = torch.softmax(logits, dim=-1).tolist()
        got = [out["rows"][1][c] for c in ("p_negative", "p_positive")]
        assert got == pytest.approx(expected, abs=1e-6)

    def test_a_multi_label_head_reports_sigmoid_not_softmax(self, tmp_path):
        import torch

        node = PretrainedClassify("sent", _pin("mlab", text_field="text"))
        out = node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        logits = _expected_logits(shared()["models"]["mlab"], TEXT_RECORDS[0]["text"])
        got = [out["rows"][0][c] for c in ("p_rates", "p_earnings")]
        assert got == pytest.approx(torch.sigmoid(logits).tolist(), abs=1e-6)
        assert got != pytest.approx(torch.softmax(logits, dim=-1).tolist(), abs=1e-6)

    def test_a_single_label_head_refuses_by_name(self, tmp_path):
        node = PretrainedClassify("sent", _pin("onelabel", text_field="text"))
        with pytest.raises(ValueError, match="num_labels") as exc:
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert "sent" in str(exc.value)

    def test_a_regression_head_refuses_by_name(self, tmp_path):
        node = PretrainedClassify("sent", _pin("regr", text_field="text"))
        with pytest.raises(ValueError, match="regression") as exc:
            node.run(_ctx(tmp_path), {"records": [dict(TEXT_RECORDS[0])]})
        assert "sent" in str(exc.value)


class TestForecastFits:
    """The default hook claims ``past_values`` in and ``prediction_outputs`` out;
    a model that does not answer that shape refuses at LOAD, not per row (M5)."""

    @pytest.mark.parametrize("name", ["tst", "nll"])
    def test_a_model_the_default_hook_cannot_drive_refuses_at_load(self, tmp_path, name):
        node = PretrainedForecast("fc", _pin(name, features=CONTEXT_FEATURES))
        with pytest.raises(ValueError, match="forecast") as exc:
            node.run(_ctx(tmp_path), {})
        assert "fc" in str(exc.value) and "subclass" in str(exc.value)

    def test_a_multi_channel_model_refuses_at_load(self, tmp_path):
        node = PretrainedForecast("fc", _pin("chan3", features=CONTEXT_FEATURES))
        with pytest.raises(ValueError, match="num_input_channels") as exc:
            node.run(_ctx(tmp_path), {})
        assert "fc" in str(exc.value) and "3" in str(exc.value)

    @pytest.mark.parametrize("name", ["notamodel", "notaclass"])
    def test_an_architecture_that_is_not_a_model_class_refuses_by_name(self, tmp_path, name):
        node = PretrainedForecast("fc", _pin(name, features=CONTEXT_FEATURES))
        with pytest.raises(ValueError, match="architectures") as exc:
            node.run(_ctx(tmp_path), {})
        assert "fc" in str(exc.value)

    def test_a_hook_that_answers_the_wrong_shape_refuses_at_load(self, tmp_path):
        # The probe judges the ANSWER, not only whether the call survived: a
        # list, an unselected channel axis, or too few steps are all a hook
        # that does not fit — caught once at load, not per scored row.
        class ListForecast(PretrainedForecast):
            """A hook answering a plain list instead of a tensor of steps."""

            def forecast(self, model, context):
                """Answer a list — the shape the probe must refuse."""
                return [0.0, 0.0]

        class RankThreeForecast(PretrainedForecast):
            """A hook that forgets to select the channel."""

            def forecast(self, model, context):
                """Answer the raw ``[batch, steps, channels]`` tensor."""
                return model(past_values=context).prediction_outputs

        class ShortForecast(PretrainedForecast):
            """A hook answering one step where the horizon wants two."""

            def forecast(self, model, context):
                """Answer only the first step."""
                return model(past_values=context).prediction_outputs[..., 0][:, :1]

        for cls, needle in ((ListForecast, "not a tensor"),
                            (RankThreeForecast, "rank-3"),
                            (ShortForecast, "fewer than horizon")):
            node = cls("fc", _pin("fc", features=CONTEXT_FEATURES, horizon=2))
            with pytest.raises(ValueError, match="does not fit this model") as exc:
                node.run(_ctx(tmp_path, cls.__name__), {})
            assert needle in str(exc.value) and "fc" in str(exc.value)

    def test_context_length_of_reads_the_config_and_declines_when_it_is_absent(self):
        node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES))
        assert node.context_length_of(SimpleNamespace(
            config=SimpleNamespace(context_length=CONTEXT))) == CONTEXT
        assert node.context_length_of(SimpleNamespace(config=SimpleNamespace())) is None


def test_integral_float_knobs_mean_the_int_spelling(tmp_path):
    # ``check_int_param`` accepts 2.0 by design (a metric wired into a knob is a
    # float), so the ACCESSORS must hand the library an int, never a float.
    records = [dict(r) for r in TEXT_RECORDS]
    ints = PretrainedEncode("a", _pin("enc", text_field="text", batch_size=1,
                                      max_length=8))
    floats = PretrainedEncode("b", _pin("enc", text_field="text", batch_size=1.0,
                                        max_length=8.0))
    assert floats.batch_size() == 1 and floats.max_length() == 8
    assert ints.run(_ctx(tmp_path, "a"), {"records": records})["rows"] == \
        floats.run(_ctx(tmp_path, "b"), {"records": records})["rows"]
    node = PretrainedForecast("fc", _pin("fc", features=CONTEXT_FEATURES, horizon=2.0))
    assert node.horizon() == 2
    signal = node.run(_ctx(tmp_path, "c"), {})["signal"]
    values = [WINDOW_ROW[f] for f in CONTEXT_FEATURES]
    assert signal.predict(dict(WINDOW_ROW)) == pytest.approx(
        _expected_forecast(shared()["models"]["fc"], values, 2), abs=1e-6)


def test_a_bfloat16_snapshot_encodes_and_forecasts(tmp_path):
    # The context and the pooled vectors must meet the model in ITS dtype: a
    # float32 tensor into a bfloat16 forward is a crash, not a forecast.
    out = PretrainedEncode("emb", _pin("bf16enc", text_field="text")).run(
        _ctx(tmp_path, "e"), {"records": [dict(TEXT_RECORDS[0])]})
    row = out["rows"][0]
    assert all(isinstance(row[f"emb_{i}"], float) and math.isfinite(row[f"emb_{i}"])
               for i in range(WIDTH))
    signal = PretrainedForecast("fc", _pin("bf16fc", features=CONTEXT_FEATURES)).run(
        _ctx(tmp_path, "f"), {})["signal"]
    value = signal.predict(dict(WINDOW_ROW))
    assert value is not None and math.isfinite(value)


def test_the_snapshot_dir_memo_is_keyed_on_the_pin(tmp_path):
    # One instance, two pins: a memo that ignored its argument would hand the
    # second caller the FIRST snapshot's directory.
    est = shared()
    node = PretrainedEncode("emb", _pin("enc", text_field="text"))
    enc_dir = node.snapshot_dir()
    assert node.snapshot_dir(est["cls"]) != enc_dir
    assert node.snapshot_dir() == enc_dir


def test_the_stream_name_rule_is_the_onboarding_segment_rule():
    # A stream name becomes a directory segment on the onboarding side; two
    # spellings of one rule drift the moment either is tightened (ADR-0020).
    from dskit.onboarding.base import _SEGMENT
    from dskit.pipeline.libs.transformers import _STREAM_NAME

    for value in ["snapshot", "_snap", "-snap", "snapshot\n", "Snap", "a-b_c"]:
        assert bool(_STREAM_NAME.match(value)) == bool(_SEGMENT.match(value)), value


class TestEncodeCoverage:
    """The paths a first round left unmeasured (m14, m16)."""

    def test_max_pooling_honours_the_attention_mask(self, tmp_path):
        # Padded positions are -inf before the max, so a short text batched with
        # a long one must pool exactly as it does alone.
        short, long = TEXT_RECORDS[2], TEXT_RECORDS[1]
        params = _pin("enc", text_field="text", pooling="max", batch_size=8)
        alone = PretrainedEncode("a", params).run(
            _ctx(tmp_path, "a"), {"records": [dict(short)]})["rows"][0]
        batched = PretrainedEncode("b", params).run(
            _ctx(tmp_path, "b"), {"records": [dict(long), dict(short)]})["rows"][1]
        assert [alone[f"emb_{i}"] for i in range(WIDTH)] == pytest.approx(
            [batched[f"emb_{i}"] for i in range(WIDTH)], abs=1e-5)

    def test_a_max_length_shorter_than_the_text_truncates_and_still_runs(self, tmp_path):
        params = _pin("enc", text_field="text")
        records = [dict(TEXT_RECORDS[1])]
        cut = PretrainedEncode("c", {**params, "max_length": 3}).run(
            _ctx(tmp_path, "c"), {"records": records})["rows"][0]
        whole = PretrainedEncode("w", params).run(
            _ctx(tmp_path, "w"), {"records": records})["rows"][0]
        assert all(math.isfinite(cut[f"emb_{i}"]) for i in range(WIDTH))
        assert any(abs(cut[f"emb_{i}"] - whole[f"emb_{i}"]) > 1e-6 for i in range(WIDTH))

    def test_no_records_still_names_the_models_columns(self, tmp_path):
        out = PretrainedEncode("emb", _pin("enc", text_field="text")).run(
            _ctx(tmp_path), {"records": []})
        assert out["rows"] == []
        assert out["metrics"] == {"n_rows": 0, "n_records": 0, "n_dropped": 0,
                                  "n_columns": WIDTH}

    def test_a_model_whose_config_names_no_width_refuses_by_name(self, tmp_path):
        class WidthlessEncode(PretrainedEncode):
            """A model family whose config declares neither hidden_size nor num_labels."""

            def width_of(self, model):
                """Give nothing — the config named no width."""
                return 0

        node = WidthlessEncode("emb", _pin("enc", text_field="text"))
        with pytest.raises(ValueError, match="hidden_size") as exc:
            node.run(_ctx(tmp_path), {"records": []})
        assert "emb" in str(exc.value)

    def test_a_record_missing_a_required_field_yields_no_row_and_is_counted(self, tmp_path):
        records = [dict(TEXT_RECORDS[0]), {"asof_ms": 4, "text": "hike"},
                   {"asof_ms": 5, "text": "hike", "symbol": None}]
        node = PretrainedEncode("emb", _pin("enc", text_field="text",
                                            carry_fields=["asof_ms"],
                                            require_fields=["symbol"]))
        out = node.run(_ctx(tmp_path), {"records": records})
        assert [row["asof_ms"] for row in out["rows"]] == [1]
        assert out["metrics"] == {"n_rows": 1, "n_records": 3, "n_dropped": 2,
                                  "n_columns": WIDTH}

    def test_require_fields_defaults_to_requiring_nothing(self, tmp_path):
        node = PretrainedEncode("emb", _pin("enc", text_field="text"))
        assert node.require_fields() == ()
        out = node.run(_ctx(tmp_path), {"records": [{"text": "hike"}]})
        assert out["metrics"]["n_rows"] == 1

    @pytest.mark.parametrize(
        "over, needle",
        [
            ({"require_fields": "symbol"}, "require_fields"),
            ({"require_fields": ["a", "a"]}, "require_fields"),
            ({"require_fields": [""]}, "require_fields"),
            ({"snapshot": "a" * 64 + "\n"}, "snapshot"),
            ({"stream": "-snap"}, "stream"),
        ],
    )
    def test_validators_refuse_junk_by_name(self, over, needle):
        problems = PretrainedEncode.validate_params(
            {**_pin("enc", text_field="text"), **over})
        assert problems and any(needle in p for p in problems)


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
