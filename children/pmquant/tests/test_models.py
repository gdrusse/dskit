"""The ladder-q̂ model on dskit's declared-model seam.

Small synthetic panel items (hand-built numpy arrays, no store, no
records), ``d_model`` 16, one or two epochs. Every expected value is an
independent restatement — the monotone law by inspection of the logits,
the event log-loss by a plain Python loop over the same cells.
"""

import hashlib
import json
import math
import os

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from dskit.pipeline.libs.torch import DeclaredPredict, DeclaredTrain
from dskit.pipeline.node import NodeContext

from pmquant.ladder.panels import PANEL_KEYS, TokenFeaturizer, collate_items
from pmquant.ladder.protocols import LEAD_ROUND_DP, STRIKE_CODES, lead_key
from pmquant.models import (
    SERVING_SUFFIX,
    TIME_ENCODERS,
    LadderPanelAdapter,
    LadderQhatModule,
    LawHead,
    TokenEncoder,
    head_loss,
    q_from_logits,
    touches,
)

F = TokenFeaturizer().n_features
NAMES = TokenFeaturizer().feature_names()
FRACS = (0.9, 0.5, 0.1)
ADAPTER_REF = "pmquant.models:LadderPanelAdapter"
MODULE_REF = "pmquant.models:LadderQhatModule"
MODULE_PARAMS = {"d_model": 16, "n_time_layers": 1, "k_lvl": 5}
#: The frozen recipe's layout identity and the fixture's vocab — RESTATED.
IDENTITY = (5, ())
VOCAB = {"KXA": 0, "KXB": 1}


def make_item(rng, *, C, series, event, market_id=0, partition=False, eligible=True,
              y=None, unseen=(), vocab=VOCAB, identity=IDENTITY):
    """One hand-built panel item over T=3 leads: random tokens, full visibility
    except the ``(step, rung)`` cells named in ``unseen``."""
    T = len(FRACS)
    feats = rng.normal(size=(T, C, F)).astype(np.float32) * 0.1
    feats[..., NAMES.index("yes_touch")] = rng.uniform(0.05, 0.95, size=(T, C))
    feats[..., NAMES.index("no_touch")] = rng.uniform(0.05, 0.95, size=(T, C))
    seen = np.ones((T, C), dtype=bool)
    for k, r in unseen:
        seen[k, r] = False
    if y is None:
        y = np.zeros(C, dtype=np.float32)
        y[0] = 1.0
    st = np.full(C, 1 if partition else 2, dtype=np.int64)
    return {
        "feats": feats,
        "seen": seen,
        "visible": np.logical_or.accumulate(seen, axis=0),
        "y": np.asarray(y, dtype=np.float32),
        "market_id": market_id,
        "is_partition": partition,
        "st_code": st,
        "eligible": eligible,
        "contracts": [f"{event}-R{r}" for r in range(C)],
        "lead_fracs": FRACS,
        "featurizer": identity,
        "vocab": vocab,
        "series": series,
        "event": event,
        "close_ts_ms": 1_000,
        "asks": feats[..., NAMES.index("yes_touch")],
        "asks_no": feats[..., NAMES.index("no_touch")],
        "ask_sz": np.ones((T, C), dtype=np.float32),
        "bid_sz": np.ones((T, C), dtype=np.float32),
    }


def make_items(seed=0):
    rng = np.random.default_rng(seed)
    return [
        make_item(rng, C=2, series="KXA", event="KXA-1", market_id=0),
        make_item(rng, C=3, series="KXB", event="KXB-1", market_id=1, partition=True,
                  y=[0.0, 1.0, 0.0], unseen=[(0, 2)]),
        make_item(rng, C=2, series="KXA", event="KXA-2", market_id=0, eligible=False),
    ]


def params(**over):
    base = {
        "adapter": ADAPTER_REF,
        "module": MODULE_REF,
        "module_params": dict(MODULE_PARAMS),
        "epochs": 1,
        "lr": 0.01,
        "loader": {"batch_size": 4, "shuffle": False, "seed": 3},
    }
    base.update(over)
    return base


def ctx(tmp_path, sub="run"):
    return NodeContext(name="t", asof="2026-01-01", run_dir=str(tmp_path / sub))


# --- the settlement head ------------------------------------------------------


def head_inputs(C, code, partition=False, B=1, T=2, d=8, seed=0):
    g = torch.Generator().manual_seed(seed)
    state = torch.randn(B, T, C, d, generator=g)
    ask = torch.rand(B, T, C, generator=g)
    market = torch.zeros(B, dtype=torch.long)
    st = torch.full((B, C), code, dtype=torch.long)
    part = torch.tensor([partition] * B)
    return state, ask, ask, market, st, part


def test_law_head_is_monotone_per_tail_and_leaves_partitions_raw():
    torch.manual_seed(0)
    head = LawHead(n_markets=1, d=8)
    # perturb the trunk so raw logits are not already ordered
    with torch.no_grad():
        for p in head.trunk.parameters():
            p.add_(torch.randn_like(p))
    state, ay, an, m, st, part = head_inputs(C=5, code=2)
    upper = head(state, ay, an, m, st, part)
    assert upper.shape == (1, 2, 5)
    assert (upper[..., 1:] <= upper[..., :-1] + 1e-6).all(), upper
    state, ay, an, m, st, part = head_inputs(C=5, code=0)
    lower = head(state, ay, an, m, st, part)
    assert (lower[..., 1:] >= lower[..., :-1] - 1e-6).all(), lower
    state, ay, an, m, st, part = head_inputs(C=5, code=1, partition=True)
    raw = head.trunk(torch.cat([state, ay[..., None], an[..., None]], -1))[..., 0]
    assert torch.allclose(head(state, ay, an, m, st, part), raw)  # zero-init s/b embeddings


def test_two_tailed_ladders_run_each_tail_on_its_own_contiguous_run():
    torch.manual_seed(1)
    head = LawHead(n_markets=1, d=8, wide_head=True)
    with torch.no_grad():
        for p in head.trunk.parameters():
            p.add_(torch.randn_like(p))
    state, ay, an, m, _, part = head_inputs(C=6, code=0)
    st = torch.tensor([[0, 0, 0, 2, 2, 2]])
    out = head(state, ay, an, m, st, part)
    less, greater = out[..., :3], out[..., 3:]
    assert (less[..., 1:] >= less[..., :-1] - 1e-6).all()
    assert (greater[..., 1:] <= greater[..., :-1] + 1e-6).all()


def test_q_from_logits_sums_to_one_over_visible_rungs_for_partitions():
    logit = torch.tensor([[[2.0, -1.0, 0.5, 3.0]]])
    visible = torch.tensor([[[True, True, True, False]]])
    q = q_from_logits(logit, visible, torch.tensor([True]))
    assert q[0, 0, 3].item() == pytest.approx(0.0, abs=1e-6)
    assert q[0, 0, :3].sum().item() == pytest.approx(1.0)
    q_thr = q_from_logits(logit, visible, torch.tensor([False]))
    assert torch.allclose(q_thr, torch.sigmoid(logit))


def batch_for_loss(partition):
    return {
        "visible": torch.tensor([[[True, True, True]] * 2]),
        "contract_mask": torch.tensor([[True, True, True]]),
        "y": torch.tensor([[0.0, 1.0, 0.0]]),
        "is_partition": torch.tensor([partition]),
    }


@pytest.mark.parametrize("partition", [True, False])
def test_head_loss_prefers_the_winner(partition):
    b = batch_for_loss(partition)
    good = torch.tensor([[[-2.0, 3.0, -2.0]] * 2])
    bad = torch.tensor([[[3.0, -2.0, 3.0]] * 2])
    lg, lb = head_loss(good, b), head_loss(bad, b)
    assert math.isfinite(lg.item()) and math.isfinite(lb.item())
    assert lg.item() < lb.item()


def test_head_loss_is_event_equal_and_restated_for_a_threshold_batch():
    b = batch_for_loss(False)
    logit = torch.tensor([[[0.5, -0.5, 1.5], [0.0, 0.0, 0.0]]])
    expected = torch.nn.functional.binary_cross_entropy_with_logits(
        logit[0], b["y"][0][None, :].expand(2, 3), reduction="mean"
    )
    assert head_loss(logit, b).item() == pytest.approx(expected.item())


def test_head_loss_partition_skips_steps_where_the_winner_is_unlisted():
    b = batch_for_loss(True)
    b["visible"] = torch.tensor([[[True, False, True], [True, True, True]]])
    logit = torch.tensor([[[5.0, 5.0, 5.0], [0.0, 2.0, 0.0]]])
    # only step 1 counts: -log_softmax([0,2,0])[1]
    expected = -torch.log_softmax(torch.tensor([0.0, 2.0, 0.0]), -1)[1]
    assert head_loss(logit, b).item() == pytest.approx(expected.item())


@pytest.mark.parametrize("y", [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]])
def test_head_loss_partition_without_exactly_one_winner_is_no_target(y):
    """All-NO (a bracket ladder the outcome fell outside) or two-YES labels
    name no winner: argmax would train rung 0 / the first YES as the law's
    choice. Such an event contributes nothing; as a threshold it still does."""
    b = batch_for_loss(True)
    b["y"] = torch.tensor([y])
    logit = torch.tensor([[[3.0, -1.0, -1.0]] * 2])
    assert head_loss(logit, b).item() == 0.0
    b["is_partition"] = torch.tensor([False])
    assert head_loss(logit, b).item() > 0.0
    # the head reads the tail codes from the ONE table the panels write
    assert (STRIKE_CODES["less"], STRIKE_CODES["between"], STRIKE_CODES["greater"]) == (0, 1, 2)


# --- the encoder --------------------------------------------------------------


@pytest.mark.parametrize("time_enc", TIME_ENCODERS)
def test_encoder_shape_and_causality(time_enc):
    torch.manual_seed(0)
    enc = TokenEncoder(n_markets=2, tok_f=F, n_leads=3, time_enc=time_enc, d=16, n_layers=1)
    enc.eval()
    batch = collate_items(make_items())
    with torch.no_grad():
        out = enc(batch["feats"], batch["seen"], batch["visible"], batch["market_id"])
        assert out.shape == (3, 3, 3, 16)
        bumped = dict(batch)
        bumped["feats"] = batch["feats"].clone()
        bumped["feats"][:, 2] += 5.0  # perturb the LAST lead only
        again = enc(bumped["feats"], bumped["seen"], bumped["visible"], bumped["market_id"])
    assert torch.allclose(out[:, :2], again[:, :2], atol=1e-5)
    assert not torch.allclose(out[:, 2], again[:, 2])


def test_encoder_refuses_an_unknown_time_encoder():
    with pytest.raises(ValueError, match="time_enc"):
        TokenEncoder(n_markets=1, tok_f=F, n_leads=3, time_enc="lstm")


def test_module_forward_gives_one_logit_per_cell():
    torch.manual_seed(0)
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS)
    batch = collate_items(make_items())
    assert module(batch).shape == (3, 3, 3)
    ay, an = touches(batch, NAMES)
    assert torch.equal(ay, batch["feats"][..., NAMES.index("yes_touch")])
    assert torch.equal(an, batch["feats"][..., NAMES.index("no_touch")])
    with pytest.raises(ValueError, match="drop"):
        LadderQhatModule(n_markets=2, n_leads=3, drop="nope")


def test_module_refuses_a_batch_featurized_under_another_layout():
    """The panels node's ``drop`` and the module's are two spellings of one
    ablation; the batch carries its identity and the module refuses a
    mismatch instead of naming an ablation its tokens never got."""
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    dropped = [make_item(rng, C=2, series="KXA", event="KXA-1", identity=(5, ("context",)))]
    plain = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS)
    with pytest.raises(ValueError, match="drop"):
        plain(collate_items(dropped))
    ablated = LadderQhatModule(n_markets=2, n_leads=3, drop=["context"], **MODULE_PARAMS)
    assert ablated(collate_items(dropped)).shape == (1, 3, 2)
    assert ablated.featurizer.identity == (5, ("context",))
    with pytest.raises(ValueError, match="layout"):
        collate_items(dropped + make_items()[:1])


# --- the adapter --------------------------------------------------------------


def test_prepare_keeps_panel_items_counts_the_rest_and_refuses_none_usable():
    adapter = LadderPanelAdapter({})
    items = make_items()
    prepared = adapter.prepare(items + [{"x1": 1.0}, "junk"], {}, where="rows")
    assert len(prepared) == 3 and prepared.n_skipped == 2
    assert prepared.payload[0] is items[0]
    with pytest.raises(ValueError, match="rows"):
        adapter.prepare([{"x1": 1.0}], {}, where="rows")
    assert len(adapter.prepare([], {}, where="rows")) == 0
    assert adapter.requires_features is False and adapter.applies_loss is False
    assert adapter._PARAMS == ()
    assert set(PANEL_KEYS) <= set(items[0])


def test_module_params_are_implied_by_the_data():
    adapter = LadderPanelAdapter({})
    prepared = adapter.prepare(make_items(), {}, where="rows")
    assert adapter.module_params(prepared, {}) == {"n_markets": 2, "n_leads": 3}
    # the WHOLE vocab sizes the embedding, not the markets train happens to hold
    rng = np.random.default_rng(1)
    only_a = [make_item(rng, C=2, series="KXA", event="KXA-9", vocab={"KXA": 0, "KXB": 1, "KXC": 2})]
    assert adapter.module_params(adapter.prepare(only_a, {}, where="rows"), {})["n_markets"] == 3


def test_prepare_holds_items_to_one_vocab_and_the_fitted_one():
    torch.manual_seed(0)
    rng = np.random.default_rng(2)
    adapter = LadderPanelAdapter({})
    items = make_items()
    # an item indexed by a vocab other than the one it carries
    inconsistent = [make_item(rng, C=2, series="KXB", event="KXB-7", market_id=0)]
    with pytest.raises(ValueError, match="market_id 0"):
        adapter.prepare(inconsistent, {}, where="rows")
    # two builds' vocabs in one port
    other = [make_item(rng, C=2, series="KXA", event="KXA-7", vocab={"KXA": 0})]
    with pytest.raises(ValueError, match="different vocab"):
        adapter.prepare(items + other, {}, where="rows")
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS).eval()
    adapter.fitted(module, adapter.prepare(items, {}, where="rows"), None)
    # after the fit: an unseen series, and a series whose index shifted, refuse BY NAME
    grown = {"KXA": 0, "KXAA": 1, "KXB": 2}
    unseen = [make_item(rng, C=2, series="KXAA", event="KXAA-1", market_id=1, vocab=grown)]
    with pytest.raises(ValueError, match="'KXAA'.*unseen market"):
        adapter.prepare(unseen, {}, where="panel_rows")
    shifted = [make_item(rng, C=2, series="KXB", event="KXB-9", market_id=2, vocab=grown)]
    with pytest.raises(ValueError, match="'KXB'.*shifting"):
        adapter.prepare(shifted, {}, where="panel_rows")
    same = [make_item(rng, C=2, series="KXA", event="KXA-9", market_id=0, vocab=grown)]
    assert len(adapter.prepare(same, {}, where="panel_rows")) == 1


def test_select_loss_beliefs_and_device_move():
    torch.manual_seed(0)
    adapter = LadderPanelAdapter({})
    prepared = adapter.prepare(make_items(), {}, where="rows")
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS)
    whole = adapter.select(prepared, None)
    assert whole["feats"].shape == (3, 3, 3, F)
    sub = adapter.select(prepared, torch.tensor([1]))
    assert sub["feats"].shape == (1, 3, 3, F) and sub["is_partition"].tolist() == [True]
    loss = adapter.loss(module, whole)
    assert loss.ndim == 0 and math.isfinite(loss.item())
    q, y = adapter.beliefs(module, whole)
    n_visible = int((whole["visible"] & whole["contract_mask"][:, None, :]).sum())
    assert len(q) == len(y) == n_visible == 3 * 2 + (3 * 3 - 1) + 3 * 2
    assert all(0.0 <= v <= 1.0 for v in q) and set(y) <= {0.0, 1.0}
    moved = adapter.to_device(whole, "cpu")
    assert all(torch.is_tensor(v) for k, v in moved.items() if k != "featurizer")
    assert moved["featurizer"] == IDENTITY  # the identity rides along, unmoved


def test_event_logloss_equals_an_independent_restatement():
    torch.manual_seed(0)
    adapter = LadderPanelAdapter({})
    items = make_items()
    prepared = adapter.prepare(items, {}, where="rows")
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS).eval()
    batch = collate_items(items)
    with torch.no_grad():
        vis = batch["visible"] & batch["contract_mask"][:, None, :]
        q = q_from_logits(module(batch), vis, batch["is_partition"]).numpy()
    per_event = []
    for i, item in enumerate(items):
        cells = []
        for k in range(3):
            for r in range(len(item["y"])):
                if vis[i, k, r]:
                    qq = min(max(float(q[i, k, r]), 1e-4), 1 - 1e-4)
                    yy = float(item["y"][r])
                    cells.append(-(yy * math.log(qq) + (1 - yy) * math.log(1 - qq)))
        per_event.append((item["eligible"], sum(cells) / len(cells)))
    eligible = [v for e, v in per_event if e]
    assert adapter.event_logloss(module, prepared) == pytest.approx(
        sum(eligible) / len(eligible)
    )
    assert adapter.event_logloss(module, prepared, eligible_only=False) == pytest.approx(
        sum(v for _, v in per_event) / len(per_event)
    )
    chunked = adapter.event_logloss(module, prepared, batch_size=1)
    assert chunked == pytest.approx(sum(eligible) / len(eligible), abs=1e-5)
    none_eligible = adapter.prepare([items[2]], {}, where="rows")
    assert math.isnan(adapter.event_logloss(module, none_eligible))


def test_predict_raises_without_a_table_and_fitted_builds_one():
    torch.manual_seed(0)
    adapter = LadderPanelAdapter({})
    items = make_items()
    train = adapter.prepare(items[:2], {}, where="rows")
    val = adapter.prepare(items[2:], {}, where="val_rows")
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS).eval()
    with pytest.raises(ValueError, match="table"):
        adapter.predict(module, {"contract": "KXA-1-R0", "lead_frac": 0.9})
    table = adapter.fitted(module, train, val)
    assert set(table) == {
        (c, lead_key(f)) for item in items for c in item["contracts"] for f in FRACS
    } - {("KXB-1-R2", lead_key(0.9))}  # the one never-visible cell
    assert adapter.predict(module, {"contract": "KXA-1-R0", "lead_frac": 0.9}) == pytest.approx(
        table[("KXA-1-R0", lead_key(0.9))]
    )
    assert adapter.predict(module, {"contract": "KXB-1-R2", "lead_frac": 0.9}) is None
    assert adapter.predict(module, {"contract": "NOPE", "lead_frac": 0.5}) is None
    assert adapter.predict(module, {"contract": "KXA-1-R0"}) is None
    record = type("R", (), {"contract": "KXA-1-R0", "lead_frac": 0.5000001})()
    assert adapter.predict(module, record) == pytest.approx(table[("KXA-1-R0", 0.5)])
    # a fit over panels with nothing visible yields nothing to serve
    blind = make_items()[0]
    blind["seen"][:] = False
    blind["visible"][:] = False
    with pytest.raises(ValueError, match="EMPTY"):
        adapter.fitted(module, adapter.prepare([blind], {}, where="rows"), None)


def test_save_and_load_state_round_trip_and_refusals(tmp_path):
    torch.manual_seed(0)
    adapter = LadderPanelAdapter({})
    items = make_items()
    module = LadderQhatModule(n_markets=2, n_leads=3, **MODULE_PARAMS).eval()
    table = adapter.fitted(module, adapter.prepare(items, {}, where="rows"), None)
    prefix = str(tmp_path / "model")
    with pytest.raises(ValueError, match="serving table"):
        LadderPanelAdapter({}).save_state(prefix)
    manifest = adapter.save_state(prefix)
    path = prefix + SERVING_SUFFIX
    assert manifest["serving_table"]["file"] == os.path.basename(path)
    assert manifest["serving_table"]["cells"] == len(table)
    text = open(path, encoding="utf-8").read()
    assert manifest["serving_table"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    payload = json.loads(text)
    assert payload["lead_key_dp"] == LEAD_ROUND_DP
    assert payload["vocab"] == VOCAB  # the vocab travels with the artifact
    assert payload["cells"] == sorted(payload["cells"])
    assert {(c, lead) for c, lead, _ in payload["cells"]} == set(table)

    fresh = LadderPanelAdapter({})
    restored = fresh.load_state(prefix, manifest)
    assert restored == table
    assert fresh.predict(module, {"contract": "KXA-1-R0", "lead_frac": 0.9}) == pytest.approx(
        table[("KXA-1-R0", 0.9)]
    )
    # ...and a restored adapter holds predict-time items to it
    with pytest.raises(ValueError, match="'KXC'"):
        fresh.prepare(
            [make_item(np.random.default_rng(3), C=2, series="KXC", event="KXC-1", market_id=2,
                       vocab={**VOCAB, "KXC": 2})],
            {}, where="panel_rows",
        )
    with pytest.raises(ValueError, match="records no serving table"):
        LadderPanelAdapter({}).load_state(prefix, {})
    with pytest.raises(ValueError, match="not on disk"):
        LadderPanelAdapter({}).load_state(str(tmp_path / "elsewhere"), manifest)
    tampered = dict(payload)
    tampered["cells"] = [[c, lead, 0.5] for c, lead, _ in payload["cells"]]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(tampered, separators=(",", ":")))
    with pytest.raises(ValueError, match="sha256"):
        LadderPanelAdapter({}).load_state(prefix, manifest)

    def forge(body, needle):
        text = json.dumps(body, separators=(",", ":"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        forged = {"serving_table": {**manifest["serving_table"],
                                    "sha256": hashlib.sha256(text.encode()).hexdigest()}}
        with pytest.raises(ValueError, match=needle):
            LadderPanelAdapter({}).load_state(prefix, forged)

    forge({"lead_key_dp": LEAD_ROUND_DP, "vocab": VOCAB, "cells": []}, "empty")
    forge({"lead_key_dp": LEAD_ROUND_DP, "cells": payload["cells"]}, "no market vocab")


# --- through the pack ---------------------------------------------------------


def test_the_declared_seam_fits_persists_and_restores(tmp_path):
    items = make_items()
    node = DeclaredTrain("fit", params())
    out = node.run(ctx(tmp_path), {"rows": items[:2], "val_rows": items[2:]})
    assert out["metrics"]["n_rows"] == 2 and out["metrics"]["n_val_rows"] == 1
    assert out["metrics"]["epochs_run"] == 1
    assert math.isfinite(out["metrics"]["final_logloss"])  # beliefs fed the pack's metrics
    sidecar = json.load(open(os.path.splitext(out["artifact_path"])[0] + ".json"))
    assert sidecar["data_params"] == {"n_markets": 2, "n_leads": 3}
    assert sidecar["adapter_state"]["serving_table"]["cells"] > 0
    probe = {"contract": "KXA-1-R0", "lead_frac": 0.9}
    expected = out["signal"].predict(probe)
    assert expected is not None
    loaded = DeclaredPredict(
        "serve",
        {k: v for k, v in params().items() if k in ("adapter", "module", "module_params")},
        mode="load",
        artifact=out["artifact_path"],
    ).run(ctx(tmp_path, "serve"), {})["signal"]
    assert loaded.loaded and loaded.predict(probe) == pytest.approx(expected)
    # a declared loss is refused BY NAME: the adapter computes its own objective
    problems = DeclaredTrain.validate_params(params(loss="torch.nn.functional:mse_loss"))
    assert any("applies_loss" in p and "LadderPanelAdapter" in p for p in problems)
    # adapter_params are default-deny: the vocab is data, never a knob
    problems = DeclaredTrain.validate_params(params(adapter_params={"n_markets": 5}))
    assert any("n_markets" in p for p in problems)
