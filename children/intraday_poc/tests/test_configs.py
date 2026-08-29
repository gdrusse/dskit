"""Every ``configs/*.json`` parses AND validates against its engine.

Configs are the child's interface, so drift between a config and the
code it drives must fail HERE — loudly, naming the file — never at the
moment someone finally runs it. Documents must also PLAN without torch,
pyomo, or alpaca-py installed — the toolkit's core rule.
"""

import json
import os

from dskit.assets import load_model
from dskit.onboarding import check_config, load_suite
from dskit.pipeline.document import load_document

from intraday_poc.connectors import AlpacaBarsConnector

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")

#: run-train.json's DECLARED map: the shared nodes plus the one
#: ``foreach`` template every symbol is built from (ADR-0039). The
#: template lives in ``foreach.pipeline``, so it is spelled apart from
#: the shared half — see :data:`TRAIN_TEMPLATE`.
TRAIN_SHARED_NODES = {"bars", "window", "forecasts", "labeled", "select",
                      "search"}
TRAIN_TEMPLATE = {"rows", "val_rows", "qhat", "fc"}
#: What run-train.json actually RUNS: the shared nodes plus one instance
#: of every template node per symbol.
TRAIN_NODES = TRAIN_SHARED_NODES | {
    f"{key}__{symbol}" for key in TRAIN_TEMPLATE for symbol in ("aapl", "msft")
}
BACKTEST_NODES = {"bars", "window", "aapl_train", "aapl_val", "msft_train",
                  "msft_val", "qhat_aapl", "qhat_msft", "fc_aapl", "fc_msft",
                  "forecasts", "labeled", "select"}
#: The trainer node keys PER DOCUMENT, symbol-ordered (AAPL, MSFT). The
#: two documents spell them differently on purpose: run-train.json fans
#: its trainer out of a ``foreach`` template, and a fanned instance is
#: ``<template>__<key slug>`` (``dskit.pipeline.document.FOREACH_SEP``),
#: so the keys carry a DOUBLE underscore that the hand-written backtest
#: does not. That spelling is what ``live.py`` needs told — its default
#: artifact convention is ``artifacts/qhat_<symbol>``, so this document
#: is served with ``--artifact SYMBOL=artifacts/qhat__<symbol>``, the
#: hatch the loop already documents (README "Serve it").
TRAINERS = {
    "run-train.json": ("qhat__aapl", "qhat__msft"),
    "run-backtest.json": ("qhat_aapl", "qhat_msft"),
}
DOCUMENTS = ("run-train.json", "run-backtest.json")

#: The search node run-train.json declares, and the ONE space key it
#: writes. The key names the TEMPLATE (``qhat``), not an instance: the
#: expansion re-aims it at every instance, which is what pins the two
#: symbols to one architecture (:func:`test_one_space_key_tunes_both_symbols`).
SEARCH_NODE = "search"
SEARCH_SPACE_KEY = "qhat.module_params.hidden_size"
SEARCH_GRID = [16, 32, 64]

#: ONE registered source, ONE connector config: both acquisition modes
#: run through it, so live-acquired bars land in the tree the documents
#: read (ADR-0014 keys the cursors, not the source name).
SOURCE_NAME = "alpaca"
SOURCE_CONFIGS = ("source-backfill.json",)

#: Every knob each trainer node must DECLARE, in both documents. The
#: engine has a working default for most of these; declaring them is
#: the point — an undeclared knob is one nobody can see or tune. Note
#: what is NOT here: ``monitor``, which run-train.json must not declare
#: (it wires no val_rows), pinned by name instead. Add a knob here when
#: you add a knob to the documents.
DECLARED_TRAINER_KNOBS = frozenset({
    "module", "module_params", "features", "label", "optimizer",
    "optimizer_params", "epochs", "lr", "loader", "device",
})


def _path(name):
    return os.path.join(CONFIGS, name)


def _raw(name):
    """The config file's JSON, exactly as it is written."""
    with open(_path(name), encoding="utf-8") as fh:
        return json.load(fh)


def _expanded(name):
    """The document's DERIVED node map — foreach instances included."""
    return load_document(_path(name)).expanded


def _trainers(name):
    """The document's trainer node specs, symbol-ordered (AAPL, MSFT)."""
    expanded = _expanded(name)
    return tuple(expanded[key] for key in TRAINERS[name])


def test_run_train_is_a_valid_pipeline_document():
    document = load_document(_path("run-train.json"))
    assert set(document.pipeline) == TRAIN_SHARED_NODES, (
        "run-train.json's shared nodes drifted from the documented DAG"
    )
    assert set(document.foreach.pipeline) == TRAIN_TEMPLATE, (
        "run-train.json's foreach template drifted from the documented DAG"
    )
    assert set(document.expanded) == TRAIN_NODES, (
        "run-train.json's fan-out drifted from the documented DAG"
    )
    assert document.hash, "a valid document always has an identity hash"


def test_one_space_key_tunes_both_symbols():
    """One declaration, one grid, one key per symbol — and no copies.

    Written longhand, a search over both trainers needs one space key
    per symbol: two copies of a grid with nothing pinning them, so
    editing one and forgetting the other searches the symbols over
    DIFFERENT grids and calls the difference a result.
    ``test_the_symbol_twins_share_a_regime`` would not catch it — the
    declared params still agree, only the searched ones do not.
    ``foreach`` deletes the second copy: the space key names the
    TEMPLATE and the expansion re-aims it at every instance, so this
    pins both halves (ONE key written, one key per symbol run, the same
    grid on each) and the fan-out keys themselves, because a third
    symbol must be one line in ``foreach.keys`` and nothing else.

    What the fan-out does NOT do, said plainly because the opposite is
    easy to assume: it pins the DECLARATION, not the tuned VALUE. Two
    instance keys are two independent overrides, so ``hpo-grid``
    CROSSES them — three widths over two symbols is nine trials — and
    a winner may pair 16 with 64. No grammar here can tie two nodes'
    params to one value; what keeps the shipped regime symmetric is
    that the winner is reported, never written back (promoting an
    asymmetric one has to defeat ``test_the_symbol_twins_share_a_regime``
    on the way in, which is exactly the argument that should be had).
    """
    raw = _raw("run-train.json")
    assert raw["foreach"]["keys"] == ["AAPL", "MSFT"], (
        "the fan-out keys ARE the symbol universe run-train.json fits"
    )
    declared = raw["pipeline"][SEARCH_NODE]["params"]["space"]
    assert declared == {SEARCH_SPACE_KEY: SEARCH_GRID}, (
        "the search must write ONE space key, naming the template — a "
        "per-instance key is the duplication foreach exists to delete"
    )
    space = _expanded("run-train.json")[SEARCH_NODE].params["space"]
    assert space == {
        f"{key}.module_params.hidden_size": SEARCH_GRID
        for key in TRAINERS["run-train.json"]
    }, "the one key must expand to one key per symbol, on the same grid"


def test_the_search_maximizes_the_objective_it_declares():
    """A return objective minimized picks the WORST model, silently.

    ``select`` reports ``total_realized`` — the summed realized log
    return of the picks — so the winner is its MAXIMUM. ``hpo-grid``
    defaults ``select`` to ``min`` (right for a loss, wrong here), and
    nothing downstream would look odd: the search would report a
    winner, the driver would re-apply it, and the shipped architecture
    would be the one that lost the most money.
    """
    search = _raw("run-train.json")["pipeline"][SEARCH_NODE]
    assert search["uses"] == "hpo-grid"
    assert search["params"]["objective"] == "$select.metrics.total_realized"
    assert search["params"]["select"] == "max", (
        "total_realized is a return, not a loss — minimizing it selects "
        "the worst architecture in the grid"
    )


def test_the_search_scores_on_rows_it_never_trained_on():
    """Selection reads validation, and validation starts after an embargo.

    A search whose objective scores the rows its trials trained on
    grades memorization: every trial improves with capacity and the
    winner is always the biggest grid value. The engine enforces the
    declaration (an objective must come from a ``split: "val"`` score
    node) but not the WIRING, so the cut itself is pinned here: the
    trainer reads rows up to ``train_end_ms``, the scored rows start at
    ``val_start_ms``, and the band between them belongs to no split —
    the same embargo discipline run-backtest.json's folds carry.
    """
    raw = _raw("run-train.json")
    splits = raw["splits"]
    assert splits["kind"] == "time", (
        "a random split of minute bars puts the scored future inside the "
        "fitted past"
    )
    assert splits["train_end_ms"] < splits["val_start_ms"] <= \
        splits["val_end_ms"] < splits["test_end_ms"], (
        "the cuts must be strictly ascending with an embargo band open "
        "between the fit and the rows that grade it"
    )
    template = raw["foreach"]["pipeline"]
    assert template["rows"]["params"]["where"][-1] == {
        "field": "asof_ms", "op": "<=", "value": "$splits.train_end_ms",
    }, "the fitted rows must stop at the train cut"
    val_bounds = template["val_rows"]["params"]["where"][1:]
    assert val_bounds == [
        {"field": "asof_ms", "op": ">=", "value": "$splits.val_start_ms"},
        {"field": "asof_ms", "op": "<=", "value": "$splits.val_end_ms"},
    ], "the scored rows must open after the embargo and close at the cut"
    for node in ("fc", ):
        assert template[node]["params"]["split"] == "val"
    assert raw["pipeline"]["select"]["params"]["split"] == "val"


def test_the_trials_reach_a_tracking_sink_that_needs_no_server():
    """Trials nobody can compare are scores with no way back to a config.

    ``tracking`` is excluded from the identity hash (WHERE metrics land
    says nothing about what a run computes), so no other test would
    notice the section disappearing — and without it the run's params
    and metrics live only in its own run dir, which is exactly the "no
    sink, nowhere to SEE the comparison" gap this document closes. The
    URI must stay a LOCAL store: a child config that assumed a running
    server would fail the plan on every machine that has none.
    """
    sinks = _raw("run-train.json")["tracking"]["sinks"]
    assert len(sinks) == 1, "one sink, or the comparison has two homes"
    sink = sinks[0]
    assert sink["kind"] == "dskit.pipeline.libs.mlflow:MlflowTracker", (
        "spelled as a class reference, so the run needs no registration "
        "call the child would otherwise have to make in code"
    )
    uri = sink["params"]["tracking_uri"]
    assert uri.startswith("sqlite:///") or uri.startswith("./"), (
        f"the sink must be a local store, got {uri!r} — a server URI "
        "fails the plan wherever no server is running"
    )
    assert sink["params"]["experiment"], "an unnamed experiment is unfindable"


def test_run_backtest_is_a_valid_walkforward_document():
    document = load_document(_path("run-backtest.json"))
    assert set(document.pipeline) == BACKTEST_NODES, (
        "run-backtest.json drifted from the documented DAG"
    )
    assert document.walkforward is not None, (
        "the backtest IS the walk-forward — the section is load-bearing"
    )
    assert document.hash


def test_the_two_documents_share_their_modelling_core():
    """Backtest and production fit must consume identical features or
    the backtest proves nothing — pinned by comparing the shared nodes'
    params verbatim, modulo the one knob the two documents are allowed
    to diverge on.

    ``monitor`` is that knob: run-backtest.json wires val_rows and
    selects each fold's checkpoint by validation loss (ADR-0035);
    run-train.json wires no val_rows and leaves monitor undeclared. A
    whitelist of hand-picked knob names would miss any OTHER knob
    declared on only one document (an unpinned ``adapter``, a stray
    ``max_log_lines``) and would miss ``monitor`` itself drifting off
    its expected value — so this compares the full params dicts minus
    the declared divergence, and pins the divergence's values too.

    The exemption is a real gap, not a formality, and both documents'
    notes carry it: monitoring makes each backtest fold ship its
    best-scoring epoch, while the production fit ships its last, so
    total_realized grades a checkpoint-selection procedure the live
    artifact never gets. This test pins the asymmetry as DELIBERATE —
    it does not make it harmless. Presence of the pinned knobs is
    ``test_both_documents_declare_every_trainer_knob``; agreement
    between the two symbols is ``test_the_symbol_twins_share_a_regime``.
    """
    train, backtest = _raw("run-train.json"), _raw("run-backtest.json")
    assert train["pipeline"]["window"]["params"] == \
        backtest["pipeline"]["window"]["params"]
    divergent = {"monitor"}
    pairs = zip(_trainers("run-train.json"), _trainers("run-backtest.json"),
                TRAINERS["run-train.json"])
    for train_spec, backtest_spec, key in pairs:
        t, b = train_spec.params, backtest_spec.params
        t_core = {k: v for k, v in t.items() if k not in divergent}
        b_core = {k: v for k, v in b.items() if k not in divergent}
        assert t_core == b_core, key
        assert "monitor" not in t, (
            key, "run-train.json's fit is graded by the search, not by a "
            "checkpoint monitor: monitoring on the rows the objective "
            "scores would select the epoch AND the architecture on one "
            "set (run-backtest.json's own caveat)"
        )
        assert b["monitor"] == "val_loss", (
            key, "run-backtest.json must select each fold's checkpoint on val_loss"
        )


def test_both_documents_declare_every_trainer_knob():
    """Comparing the two documents cannot see a knob DELETED from both.

    ``test_the_two_documents_share_their_modelling_core`` compares the
    params dicts whole, which catches divergence but is blind to
    removal: two dicts missing the same key compare equal. Drop ``lr``
    and ``loader`` from both documents and every other config test
    stays green while both fits fall back to the engine's DEFAULT_LR
    and LOADER_DEFAULTS — including the shuffle seed, which is these
    documents' only reproducibility knob. Presence is therefore pinned
    by name, so leaning on an engine default is a deliberate edit to
    ``DECLARED_TRAINER_KNOBS`` rather than a silent one.
    """
    for doc_name in DOCUMENTS:
        for key, spec in zip(TRAINERS[doc_name], _trainers(doc_name)):
            declared = spec.params.keys()
            assert DECLARED_TRAINER_KNOBS <= declared, (
                doc_name, key,
                "stopped declaring "
                f"{sorted(DECLARED_TRAINER_KNOBS - declared)} — the engine's "
                "default takes over invisibly; re-declare it or drop it from "
                "DECLARED_TRAINER_KNOBS on purpose",
            )


def test_the_symbol_twins_share_a_regime():
    """AAPL and MSFT differ by their rows, never by their regime.

    The select-one program compares the two symbols' predictions head
    to head every minute, so a knob set on one twin and not the other
    grades two fitting regimes against each other and calls the winner
    a signal. Every trainer knob is duplicated across the twins and
    nothing else pins the copies to each other — the cross-document
    test compares train-vs-backtest per key, and
    ``test_lookback_agrees_everywhere`` covers only lookback and
    features. Per CLAUDE.md, unpinned duplication is a scheduled bug.
    Deliberate per-symbol tuning is a real future need; when it comes,
    exempt the tuned knob here by name and say why.

    run-train.json now satisfies this BY CONSTRUCTION — both symbols
    are instances of one ``foreach`` template, and the search's one
    space key moves them together — which is the point of the fan-out,
    not a reason to stop checking: the assertion is what proves the
    template stayed the only source (and it still bites on the
    hand-written backtest).
    """
    for doc_name in DOCUMENTS:
        aapl, msft = (spec.params for spec in _trainers(doc_name))
        assert aapl == msft, (
            doc_name,
            "the symbol twins' params diverged; the selector would then "
            "compare models fit under different regimes",
        )


def test_monitor_agrees_with_the_val_rows_wiring():
    """A declared monitor must have the telemetry it selects on.

    ADR-0035: every monitor except ``train_loss`` reads validation
    telemetry, and the engine refuses to fit when one is declared with
    no ``val_rows`` input. That refusal fires at fit time — mid-fold,
    after a walk-forward has already spent its train windows — so the
    coupling is pinned here too, where rewiring a document's splits is
    what breaks it. The value side is pinned by
    ``test_the_two_documents_share_their_modelling_core``; this pins
    that the value is runnable.
    """
    for doc_name in DOCUMENTS:
        for key, spec in zip(TRAINERS[doc_name], _trainers(doc_name)):
            monitor = spec.params.get("monitor")
            if monitor is None or monitor == "train_loss":
                continue
            assert "val_rows" in spec.inputs, (
                doc_name, key,
                f"monitor {monitor!r} selects on validation telemetry; "
                "wire val_rows or drop monitor",
            )


def test_asset_model_validates_and_keeps_its_shape():
    model = load_model(_path("asset-model.json"))
    assert set(model.kinds) == {"artifact", "dataset"}, (
        "asset-model.json drifted from the documented dataset/artifact pair"
    )
    governed = sorted(k for k, spec in model.kinds.items() if spec.states)
    assert governed == ["dataset"], (
        f"only 'dataset' is governed by design, got lifecycles on {governed}"
    )


def test_suite_bars_validates_and_names_its_rules():
    suite = load_suite(_path("suite-bars.json"))
    assert [r.id for r in suite.rules] == [
        "bars-arrived", "close-present", "close-positive",
        "symbol-vocabulary", "dates-parse-bitemporally",
    ], "suite-bars.json drifted from its documented rule set"


def test_source_configs_validate_against_the_connectors_spec():
    connector = AlpacaBarsConnector()
    for name in SOURCE_CONFIGS:
        with open(_path(name), encoding="utf-8") as fh:
            config = json.load(fh)
        check_config(connector, config)  # default-deny against spec()
        connector.resolve_knobs(config)  # and the knob gate accepts them


def test_one_source_name_carries_both_pulls():
    """The registered source name, the documents, and the README tell
    ONE story — because a second source name silently breaks modelling.

    The child used to ship a second connector config registering
    ``alpaca-live`` while both run documents read ``source: "alpaca"``.
    Observations live at ``observations/<source>/`` and a document reads
    one source, so every live-acquired bar landed in a tree nothing read
    — no error, just an unused store. ADR-0014 already keys checkpoints
    per (source, stream, MODE), which is precisely what makes the second
    SOURCE unnecessary: one registration, two modes, two independent
    cursors, one tree the documents can see.
    """
    shipped = sorted(name for name in os.listdir(CONFIGS)
                     if name.startswith("source-"))
    assert shipped == list(SOURCE_CONFIGS), (
        "one connector config, or the modes can disagree on vendor knobs "
        "again"
    )
    for doc_name in DOCUMENTS:
        with open(_path(doc_name), encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc["pipeline"]["bars"]["params"]["source"] == SOURCE_NAME

    with open(os.path.join(CHILD_ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert f"register-source {SOURCE_NAME} " in readme
    assert f"--config @configs/{SOURCE_CONFIGS[0]}" in readme
    for mode in ("backfill", "live"):
        assert f"--source {SOURCE_NAME} --stream bars --mode {mode}" in readme, (
            f"the README must show the {mode} pull against the one "
            "registered source"
        )
    assert "AssetError" in readme, (
        "the README once claimed a mistyped source yields an empty scan; "
        "it raises — see test_a_mistyped_source_refuses_loudly"
    )


def test_the_forward_top_up_is_declared_and_documented():
    """The live mode's window is CONFIG, and the README quotes it.

    Deleting the second source config removed the only place a
    live-mode start could be declared; ``live_lookback_minutes``
    replaced it, and it is the knob that keeps the first live pull from
    re-committing the entire backfill as a duplicate acquisition. A
    number written in the config and a different one in the prose is
    the same defect this card exists to kill, so the two are pinned to
    each other here — and declaring the knob at all is what makes it
    visible and tunable rather than an invisible engine default.
    """
    with open(_path(SOURCE_CONFIGS[0]), encoding="utf-8") as fh:
        config = json.load(fh)
    lookback = config.get("live_lookback_minutes")
    assert isinstance(lookback, int) and lookback > 0, (
        "the source config must DECLARE how far back a first live pull "
        "reaches; undeclared, nobody can see or tune it"
    )
    with open(os.path.join(CHILD_ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert f"`live_lookback_minutes` ({lookback}" in readme, (
        "the README must quote the declared window, or the two stories "
        "drift"
    )


def test_lookback_agrees_everywhere():
    """The window width, the module's lookback, and the feature list
    must be the SAME number — the live loop and the LSTM both refuse a
    mismatch, so pin it at config level too."""
    for doc_name in DOCUMENTS:
        lookback = _raw(doc_name)["pipeline"]["window"]["params"]["lookback"]
        for key, spec in zip(TRAINERS[doc_name], _trainers(doc_name)):
            params = spec.params
            assert params["module_params"]["lookback"] == lookback, doc_name
            features = params["features"]
            assert features == [f"ret_lag_{i}" for i in range(lookback)], (
                doc_name, key,
            )
