"""Generate the pooled per-ticker-heads model-zoo study over both stock universes (ADR-0236/0237).

``configs/run-pooled-zoo-417.json`` is ``configs/run-pooled-heads-top5.json`` (the template:
its data block, diagnostic, study and experiment conventions) widened to every ticker of the
two stock universes, each ticker read from its own universe's onboarded sources
(``price_source.relpath`` objects and ``keyed_tables`` parts, ADR-0237), with the ADR-0236 zoo
as candidates: every candidate trains on crps + tail_crps and the study selects and reports
on ``weighted_crps``. ``configs/run-pooled-zoo-417-folds.json`` is the template's folds
document under this study's run directory.

Values measured on data are INPUTS, never computed here: the refused tickers (a universe
ticker with no bars), the fold-table pin (path, sha256, holdout start) and the expected
cells. :func:`measured` reads them back from a generated document, so a pin test regenerates
both shipped files from themselves and the template. Never hand-edit the generated files:
change this module or the template and regenerate.
"""

import copy
import json
from pathlib import Path

__all__ = ["NAME", "STUDY_FILE", "FOLDS_FILE", "TEMPLATE_FILE", "TEMPLATE_FOLDS_FILE",
           "UNIVERSES", "ZOO", "zoo_document", "folds_document", "measured", "write"]

NAME = "pooled-zoo-417"
STUDY_FILE = f"run-{NAME}.json"
FOLDS_FILE = f"run-{NAME}-folds.json"
TEMPLATE_FILE = "run-pooled-heads-top5.json"
TEMPLATE_FOLDS_FILE = "run-pooled-heads-top5-folds.json"
_TEMPLATE_NAME = "pooled-heads-top5"

#: The two stock universes, in order: the ticker file (and its key) and, per data role, the
#: onboarded source holding that universe's rows. The first universe's bars are the
#: ``price_source`` default; the template names the first universe's feature sources.
UNIVERSES = (
    {"file": "stock_universe.json", "key": "tickers", "bars": "stock-daily-bars",
     "features": {"stock-daily-features-r2": "stock-daily-features-r2",
                  "stock-option-trade-features-r2": "stock-option-trade-features-r2"}},
    {"file": "option_universe_300.json", "key": "tickers", "bars": "stock-daily-bars-300",
     "features": {"stock-daily-features-r2": "stock-daily-features-300",
                  "stock-option-trade-features-r2": "stock-option-trade-features-300"}},
)

#: The overlay whose ``corporate_windows`` (a ``"*"`` pattern plus explicit tickers) the
#: study's corporate-action windows come from; never restated here.
WINDOWS_OVERLAY = "stocks-opt.json"

#: The zoo (owner decisions 2026-10-04). ``losses`` is every candidate's objective and
#: ``tail_weight`` the study's weighted-metric weight; the study refuses the two disagreeing.
#: ``batch_size`` 4096 (the template used 512 on ~12k rows): ~1M pooled rows make 512 a
#: 2,000-step epoch, and 4096 keeps ~250 AdamW steps per epoch, enough for patience 6 to
#: see a trend, at a fraction of the wall time. The booster's loss chunk is memory only.
ZOO = {
    "losses": [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}],
    "tail_weight": 1.0,
    "learning_rates": [0.0003, 0.001, 0.003],
    "mlp_hidden": [[16], [32, 16], [64, 32]],
    "torch": {"components": 3, "epochs": 2000, "patience": 6, "batch_size": 4096,
              "seeds": [11], "device": "cuda", "deterministic": True},
    "recurrent": {"hidden_size": 16, "num_layers": 1},
    "sequence_head": [32, 16],
    "cnn": {"channels": 16, "kernel_size": 3, "dilations": [1, 2, 4, 8], "pooling": "last"},
    "boosted": {"components": 3, "hessian": "constant", "n_estimators": 2000, "patience": 20,
                "min_data_in_leaf": 100, "batch_size": 32768, "num_threads": 8, "seed": 11,
                "device": "cuda", "deterministic": True},
    "boosted_grid": {"num_leaves": [15, 31], "learning_rate": [0.05, 0.1]},
}

_CLASS = "dskit.pipeline.libs.predictive_cdf:"


def _load(configs_dir, name):
    """Return one JSON file of the configs directory."""
    return json.loads((Path(configs_dir)/name).read_text())


def _universe_tickers(configs_dir, refused):
    """Return ``{ticker: universe index}`` for every ticker not refused, in universe order."""
    owner, seen = {}, set()
    for index, universe in enumerate(UNIVERSES):
        for ticker in _load(configs_dir, universe["file"])[universe["key"]]:
            if ticker in seen:
                raise ValueError(f"{ticker} is in two universes")
            seen.add(ticker)
            if ticker not in refused:
                owner[ticker] = index
    if set(refused) - seen:
        raise ValueError(f"refused tickers {sorted(set(refused) - seen)} are in no universe")
    return owner


def _relpath(template, ticker, index):
    """Return a ticker's price location: a file under the default source, else an object."""
    source = template["data"]["price_source"]
    file = f"{ticker.lower()}/underlying_prices.parquet"
    if index == 0:
        return file
    return {"source": UNIVERSES[index]["bars"], "stream": source["stream"], "relpath": file}


def _keyed_tables(template):
    """Return the template's keyed tables, each reading every universe's part."""
    keyed = copy.deepcopy(template["data"]["keyed_tables"])
    for spec in keyed["tables"].values():
        name = spec.pop("source")
        stream = spec.pop("stream")
        spec["parts"] = [{"source": u["features"][name], "stream": stream} for u in UNIVERSES]
    return keyed


def _windows(configs_dir, tickers):
    """Return each ticker's corporate-action windows from the overlay's pattern and entries."""
    windows = _load(configs_dir, WINDOWS_OVERLAY)["corporate_windows"]
    return {t: copy.deepcopy(windows.get(t, windows["*"])) for t in tickers}


def _data(configs_dir, template, owner):
    """Return the template's data block over every ticker."""
    data = copy.deepcopy(template["data"])
    vol = set(data["symbols"].values())
    if len(vol) != 1:
        raise ValueError(f"the template maps tickers to several volatility indices {vol}")
    vol = vol.pop()
    data["symbols"] = {t: vol for t in owner}
    data["price_source"]["source"] = UNIVERSES[0]["bars"]
    data["price_source"]["relpath"] = {t: _relpath(template, t, i) for t, i in owner.items()}
    data["keyed_tables"] = _keyed_tables(template)
    data["corporate_actions"]["windows"] = _windows(configs_dir, owner)
    data["notes"] = (
        f"{len(owner)} stocks pooled in one study (the two stock universes, ADR-0236/0237). "
        "Each ticker's bars and keyed features are read from its own universe's onboarded "
        "sources: price_source.relpath objects name the second universe's bars, and every "
        "keyed table lists one part per universe. Window, reader, columns, exact_dte 31 and "
        f"the feature recipe as {TEMPLATE_FILE}; corporate-action windows from "
        f"{WINDOWS_OVERLAY}'s corporate_windows. Generated by index_options.pooled.")
    return data


def _features(template, tickers):
    """Return the template's feature list with its task one-hots replaced by every ticker's."""
    features = list(template["study"]["features"])
    tasks = list(template["experiment"]["task_features"].values())
    first = features.index(tasks[0])
    if features[first:first+len(tasks)] != tasks:
        raise ValueError("the template's task one-hots are not one contiguous block")
    return features[:first] + [f"is_{t}" for t in tickers] + features[first+len(tasks):]


def _template_names(template, prefix):
    """Return the feature names of the template candidates whose names start with ``prefix``."""
    features = template["study"]["features"]
    picks = {tuple(spec["params"]["feature_indices"])
             for name, spec in template["experiment"]["candidates"].items()
             if name.startswith(prefix)}
    if len(picks) != 1:
        raise ValueError(f"template candidates {prefix}* do not share one feature pick")
    return [features[i] for i in picks.pop()]


def _sequence_names(template):
    """Return the template GRU's sequence feature names, oldest step first."""
    features = template["study"]["features"]
    spec = next(s for n, s in template["experiment"]["candidates"].items()
                if s["params"].get("encoder", {}).get("kind") == "gru")
    picked = [features[i] for i in spec["params"]["feature_indices"]]
    return [picked[row[0]] for row in spec["params"]["encoder"]["sequence_indices"]]


def _lr_name(rate):
    return f"lr{rate:g}"


def _group_candidates(group, names, sequence, features, heads):
    """Return one feature group's candidates, in kind then grid order."""
    indices = [features.index(n) for n in names]
    base = {"feature_indices": indices, "head_features": heads}
    torch = {**ZOO["torch"], "losses": copy.deepcopy(ZOO["losses"]), **base}
    order = [names.index(n) for n in sequence]
    context = [i for i in range(len(names)) if i not in order]
    candidates = {}
    for hidden in ZOO["mlp_hidden"]:
        for rate in ZOO["learning_rates"]:
            label = "x".join(map(str, hidden))
            candidates[f"mlp_{group}_h{label}_{_lr_name(rate)}"] = {
                "encoder": {"kind": "mlp"}, **torch, "hidden": hidden, "learning_rate": rate}
    encoders = {
        "gru": {"kind": "gru", **ZOO["recurrent"]},
        "lstm": {"kind": "lstm", **ZOO["recurrent"]},
        "cnn": {"kind": "cnn", **ZOO["cnn"]},
    }
    for kind, encoder in encoders.items():
        for rate in ZOO["learning_rates"]:
            candidates[f"{kind}_{group}_{_lr_name(rate)}"] = {
                "encoder": {**encoder, "sequence_indices": [[i] for i in order],
                            "context_indices": context},
                **torch, "hidden": ZOO["sequence_head"], "learning_rate": rate}
    grid = ZOO["boosted_grid"]
    for leaves in grid["num_leaves"]:
        for rate in grid["learning_rate"]:
            candidates[f"lgbm_{group}_l{leaves}_{_lr_name(rate)}"] = {
                **ZOO["boosted"], "losses": copy.deepcopy(ZOO["losses"]), **base,
                "num_leaves": leaves, "learning_rate": rate}
    return candidates


def _kind(name):
    return name.split("_", 1)[0]


def _experiment(template, features, tickers, cells):
    """Return the zoo experiment block."""
    experiment = copy.deepcopy(template["experiment"])
    heads = [features.index(f"is_{t}") for t in tickers]
    sequence = _sequence_names(template)
    candidates, groups = {}, {}
    for group, prefix in (("base", "mlp_heads_base"), ("options", "mlp_heads_options")):
        for name, params in _group_candidates(
                group, _template_names(template, prefix), sequence, features, heads).items():
            cls = "BoostedTorchCDF" if _kind(name) == "lgbm" else "TorchCDF"
            candidates[name] = {"class": _CLASS+cls, "calibrate": False, "pooled": True,
                                "params": params}
            groups.setdefault(f"{_kind(name)}_{group}", []).append(name)
    experiment.update({
        "output": f"pipeline_runs/{NAME}/study", "max_candidates": len(candidates),
        "candidates": candidates, "candidate_groups": groups,
        "search_partitions": copy.deepcopy(groups),
        "task_features": {t: f"is_{t}" for t in tickers},
        "selection_metric": "weighted_crps", "expected_cells": copy.deepcopy(cells),
        "notes": (
            "ADR-0236 pooled zoo, owner design 2026-10-04: one model per kind over every "
            "ticker, per-ticker heads (TorchCDF: one mixture head per is_<T>; BoostedTorchCDF: "
            "the one-hots collapsed to one native categorical column), every candidate "
            "trained on crps + tail_crps (weight 1, = study tail_weight) and selected, "
            "evaluated and accepted on weighted_crps = crps + tail_crps. Per feature group "
            "(base = the template's 38 core + 4 volume_liquidity names; options = base + the "
            "7 option-trade features and their _missing flags): MLP hidden {[16],[32,16],"
            "[64,32]} x lr {3e-4,1e-3,3e-3}; GRU, LSTM, CNN x lr (hidden_size 16, head [32,16], "
            "the template GRU's ret_lag sequence; CNN kernel 3, dilations 1-2-4-8, receptive "
            "field 31 of 22 steps, newest-step pooling); BoostedTorchCDF constant Hessian, "
            "CUDA loss, patience 20 rounds, num_leaves {15,31} x lr {0.05,0.1}. One search "
            "partition per kind and group. Generated by index_options.pooled.")})
    return experiment


def zoo_document(configs_dir, measured_values):
    """Return the pooled zoo study document.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory (template, universes, overlay).
    measured_values : dict
        ``refused`` (list of tickers with no bars), ``fold_table`` (``path``, ``sha256``,
        ``holdout_start``) and ``expected_cells`` (the study's ``development`` and
        ``evaluation`` cell maps), all measured on data.

    Returns
    -------
    dict
        The study document ``write`` stores as :data:`STUDY_FILE`.

    Raises
    ------
    ValueError
        A ticker in two universes, a refused ticker in none, or a template that breaks an
        assumption this generator states (one volatility index, contiguous one-hots, one
        feature pick per template group).
    """
    template = _load(configs_dir, TEMPLATE_FILE)
    owner = _universe_tickers(configs_dir, measured_values["refused"])
    tickers = list(owner)
    features = _features(template, tickers)
    study = copy.deepcopy(template["study"])
    study.update({
        "features": features, "output": f"pipeline_runs/{NAME}/study/unused",
        "tail_weight": ZOO["tail_weight"]})
    study["fold_table"].update(copy.deepcopy(measured_values["fold_table"]))
    study["fold_table"]["notes"] = (
        f"From {FOLDS_FILE}: one expanding warm-up fold before the options window, nine "
        "expanding scored folds inside it, fold dates global across tickers; the holdout "
        f"from {measured_values['fold_table']['holdout_start']} is never evaluated. cal_n = "
        "40 dates, the inner slice of each training window used only for patience early "
        "stopping.")
    return {"notes": (
                f"Pooled ADR-0236 model zoo over {len(tickers)} stocks at DTE 31 (refused: "
                f"{', '.join(measured_values['refused']) or 'none'}). Run from "
                f"children/index_options: python -m index_options.cdf_study configs/{STUDY_FILE} "
                "--stage search --partition <each of experiment.search_partitions>, then "
                "--stage select, --stage evaluate --partition development, --stage evaluate "
                "--partition later, --stage report (env CUBLAS_WORKSPACE_CONFIG=:4096:8, "
                f"OMP/OPENBLAS threads 1). Folds: configs/{FOLDS_FILE}. Generated by "
                "index_options.pooled from the template; never hand-edit."),
            "data": _data(configs_dir, template, owner),
            "diagnostic": copy.deepcopy(template["diagnostic"]),
            "study": study,
            "experiment": _experiment(template, features, tickers,
                                      measured_values["expected_cells"])}


def _renamed(value):
    """Return ``value`` with every template run name in its strings replaced by this study's."""
    if isinstance(value, dict):
        return {k: _renamed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_renamed(v) for v in value]
    if isinstance(value, str):
        return value.replace(_TEMPLATE_NAME, NAME)
    return value


def folds_document(configs_dir):
    """Return the folds document: the template's, under this study's run directory.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory.

    Returns
    -------
    dict
        The document ``write`` stores as :data:`FOLDS_FILE`.
    """
    document = _renamed(_load(configs_dir, TEMPLATE_FOLDS_FILE))
    document["notes"] = (
        f"Fold table for the pooled zoo ({STUDY_FILE}): the template's graph "
        f"({TEMPLATE_FOLDS_FILE}) unchanged except for this run directory. Its inputs are the "
        "workflow's steps 1-3 for the NVDA lane at horizon 31 over full history, layered "
        "configs/stocks-opt.json + configs/stocks-opt-features.json + {tickers: [NVDA], "
        "horizon: 31, price_source.window: {field: date, end: 2026-08-25}, split: "
        "{holdout_fraction: 0.045, tau: 0.9, folds: {train_n: 120, val_n: 30, step_n: 30, "
        f"warmup_folds: 2}}}}, work_dir: ./pipeline_runs/{NAME}/plan}}. Fold dates are global "
        "across tickers, so one table serves every ticker. Generated by index_options.pooled.")
    return document


def measured(configs_dir, document):
    """Return the measured inputs a generated study document carries.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory (the universes).
    document : dict
        A document :func:`zoo_document` made.

    Returns
    -------
    dict
        ``refused`` (universe tickers the document does not read, in universe order),
        ``fold_table`` and ``expected_cells``, as :func:`zoo_document` takes them.
    """
    table = document["study"]["fold_table"]
    every = [t for u in UNIVERSES for t in _load(configs_dir, u["file"])[u["key"]]]
    return {"refused": [t for t in every if t not in document["data"]["symbols"]],
            "fold_table": {k: table[k] for k in ("path", "sha256", "holdout_start")},
            "expected_cells": document["experiment"]["expected_cells"]}


def write(configs_dir, measured_values):
    """Write both generated documents into ``configs_dir``.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory.
    measured_values : dict
        As :func:`zoo_document` takes it.

    Returns
    -------
    list of Path
        The two files written.
    """
    out = []
    for name, document in ((STUDY_FILE, zoo_document(configs_dir, measured_values)),
                           (FOLDS_FILE, folds_document(configs_dir))):
        path = Path(configs_dir)/name
        path.write_text(json.dumps(document, indent=2)+"\n")
        out.append(path)
    return out
