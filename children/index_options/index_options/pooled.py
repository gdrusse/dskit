"""Generate the pooled per-ticker-heads model-zoo study over both stock universes (ADR-0236/0237).

``configs/run-pooled-zoo-417.json`` is ``configs/run-pooled-heads-top5.json`` (the template:
its data block, diagnostic, study and experiment conventions) widened to every ticker of the
two stock universes, each ticker read from its own universe's onboarded sources
(``price_source.relpath`` objects and ``keyed_tables`` parts, ADR-0237), with the ADR-0236 zoo
as candidates: every candidate trains on crps + tail_crps and the study selects and reports
on ``weighted_crps``. ``configs/run-pooled-zoo-417-folds.json`` is the template's folds
document under this study's run directory. ``configs/workflow-pooled-zoo-417.json`` runs the
study's stages and step 7 through the workflow runner (its ledger is what the verified report
checks) and ``configs/templates/report-spec-pooled-zoo-417.json`` is that report's spec.

Values measured on data are INPUTS, never computed here: the refused tickers (a universe
ticker with no bars), the fold-table pin (path, sha256, holdout start) and the expected
cells. :func:`measured` reads them back from a generated document, so a pin test regenerates
every shipped file from itself and the template. Never hand-edit the generated files:
change this module or the template and regenerate.
"""

import copy
import json
from pathlib import Path

__all__ = ["NAME", "STUDY_FILE", "FOLDS_FILE", "WORKFLOW_FILE", "REPORT_SPEC_FILE",
           "TEMPLATE_FILE", "TEMPLATE_FOLDS_FILE", "WORKFLOW_SOURCE", "UNIVERSES", "ZOO",
           "zoo_document", "folds_document", "workflow_document", "report_spec_document",
           "measured", "write"]

NAME = "pooled-zoo-417"
STUDY_FILE = f"run-{NAME}.json"
FOLDS_FILE = f"run-{NAME}-folds.json"
#: The workflow manifest (ADR-0227) that runs the study's stages and the verified report, and
#: the report spec template it expands; both generated here beside the study.
WORKFLOW_FILE = f"workflow-{NAME}.json"
REPORT_SPEC_FILE = f"templates/report-spec-{NAME}.json"
#: The per-ticker workflow whose stage lists, CLI flags and run environment the pooled
#: manifest reuses, so step 7 runs exactly ``args.evaluate.stages``.
WORKFLOW_SOURCE = "workflow.json"
#: The report table's row cap: the per-ticker rollup holds about 4,400 rows.
REPORT_MAX_ROWS = 5000
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
    # Owner option B, 2026-10-04 (enter once seen): a ticker is scored in a fold, and its
    # monitor rows used, once the fold's fit band holds 40 of its rows (about two months),
    # so a head is never scored off a handful of rows; every model waits alike.
    "min_task_fit_rows": 40,
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
        "tail_weight": ZOO["tail_weight"], "min_task_fit_rows": ZOO["min_task_fit_rows"]})
    study["fold_table"].update(copy.deepcopy(measured_values["fold_table"]))
    study["fold_table"]["notes"] = (
        f"From {FOLDS_FILE}: one expanding warm-up fold before the options window, nine "
        "expanding scored folds inside it, fold dates global across tickers; the holdout "
        f"from {measured_values['fold_table']['holdout_start']} is never evaluated. cal_n = "
        "40 dates, the inner slice of each training window used only for patience early "
        "stopping.")
    return {"notes": (
                f"Pooled ADR-0236 model zoo over {len(tickers)} stocks at DTE 31 (refused: "
                f"{', '.join(measured_values['refused']) or 'none'}). A ticker enters a fold "
                f"once its fit band holds {ZOO['min_task_fit_rows']} rows (owner option B): "
                "until then its calibration and scored rows are dropped for every model and "
                "counted in each stage's admission.json and the report's. Run from "
                f"children/index_options: python -m index_options.cdf_study configs/{STUDY_FILE} "
                "--stage search --partition <each of experiment.search_partitions>, then "
                "--stage select, --stage evaluate --partition development, --stage evaluate "
                "--partition later, --stage report (env CUBLAS_WORKSPACE_CONFIG=:4096:8, "
                "OMP/OPENBLAS threads 1). The first stage builds the panel into "
                "experiment.output/panel-cache and every later stage reuses it while the data "
                "config, source snapshots, holdout and code are unchanged. Folds: "
                f"configs/{FOLDS_FILE}. Generated by "
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


def _stages(records):
    """Return a step's ``stages`` hook: the declared records, expanded into CLI flags."""
    cli = "$args.study_io.cli"
    return {"hook": "declared_sequence", "records": records, "flags": f"{cli}.flags",
            "expand": f"{cli}.expand", "wildcard": f"{cli}.wildcard",
            "partitions_at": f"{cli}.partitions_at"}


def workflow_document(configs_dir):
    """Return the manifest running the zoo, step 7 and the verified report (ADR-0227).

    Step ``zoo`` builds the panel cache in its own process, then searches every
    partition and selects; ``step7`` runs the per-ticker workflow's
    ``args.evaluate.stages``; ``report`` hands :func:`report_spec_document`'s
    template to ``dskit.pipeline.workflow_report``, which verifies the ledger
    the runner keeps. Stage lists, CLI flags, the run environment, the study and
    report commands and the report's file names are copied from
    :data:`WORKFLOW_SOURCE`.

    Parameters
    ----------
    configs_dir : str or Path
        The child's ``configs`` directory.

    Returns
    -------
    dict
        The document ``write`` stores as :data:`WORKFLOW_FILE`.
    """
    workflow = _load(configs_dir, WORKFLOW_SOURCE)
    source, registry = workflow["args"], workflow["registry"]
    report_files = {k: workflow["layout"]["report"][k] for k in ("markdown", "html", "sections")}
    study = "{W}/study"
    return {
        "notes": (
            f"Pooled zoo run ({STUDY_FILE}), generated by index_options.pooled; never "
            "hand-edit. Run from children/index_options: python -m dskit.pipeline workflow "
            f"configs/{WORKFLOW_FILE} (--only zoo, step7 or report to run one step). The "
            "study writes under experiment.output, which is the layout's {W}/study."),
        "args": {
            "work_dir": f"./pipeline_runs/{NAME}",
            "run_env": copy.deepcopy(source["run_env"]),
            "zoo": {"stages": {"sequence": [{"stage": "panel"},
                                            *copy.deepcopy(source["hpo"]["run"]["sequence"])]},
                    "notes": "The panel stage builds the panel cache in a process that fits "
                             "nothing; then search every partition, then select."},
            "evaluate": {"stages": copy.deepcopy(source["evaluate"]["stages"]),
                         "notes": "The per-ticker workflow's step-7 stages: step zoo already "
                                  "searched and selected (a second select would refuse to "
                                  "overwrite the frozen selection)."},
            "study_io": {"cli": copy.deepcopy(source["study_io"]["cli"])},
        },
        "env": "$args.run_env",
        "layout": {
            "zoo": {"dir": "{W}/workflow/zoo", "search": f"{study}/search",
                    "selection": f"{study}/selection"},
            "step7": {"dir": "{W}/workflow/step7",
                      "development": f"{study}/evaluate/development",
                      "scored": f"{study}/evaluate/later", "report": f"{study}/report"},
            "report": {"dir": "{W}/workflow/report", "output": "{W}/report", **report_files},
        },
        "registry": {
            "zoo": {"template": STUDY_FILE, "extends": None,
                    "command": registry["step4"]["command"]},
            "step7": {"extends": "zoo"},
            "report": {"template": REPORT_SPEC_FILE, "extends": None,
                       "command": registry["report"]["command"]},
        },
        "steps": {
            "zoo": {"registry": "zoo", "in": {"W": "$args.work_dir"},
                    "out": ["search", "selection"],
                    "stages": _stages("$args.zoo.stages.sequence")},
            "step7": {"registry": "step7", "in": {"W": "$args.work_dir"},
                      "out": ["development", "scored", "report"],
                      "stages": _stages("$args.evaluate.stages.sequence")},
            "report": {"registry": "report",
                       "in": {"W": "$args.work_dir", "selection": "$zoo.out.selection",
                              "development": "$step7.out.development",
                              "scored": "$step7.out.scored",
                              "evaluation": "$step7.out.report"},
                       "out": ["markdown", "html", "sections"]},
        },
    }


def _section(name, title, source, fmt, rows="", step=None, notes=None, **extra):
    """Return one report section; a ``step`` binds it to that step output's ledger hash."""
    section = {"name": name, "title": title, "source": source, "format": fmt, "rows": rows}
    if step is not None:
        output = step[1]
        section.update(step=step[0], output=output, bound=f"${{{_REPORT_INPUT[output]}}}")
    if notes:
        section["notes"] = notes
    return {**section, **extra}


#: Report template input (the report step's ``in`` key) for each bound step output.
_REPORT_INPUT = {"selection": "selection", "development": "development", "scored": "scored",
                 "report": "evaluation"}


def report_spec_document(document):
    """Return the pooled report spec template ``dskit.pipeline.workflow_report`` reads.

    Sections: the fold table; selection across every candidate and the frozen
    variants; development and scored admission (the rows a waiting ticker had
    dropped); the development relative score; the scored metrics; the paired
    block-bootstrap intervals and the per-ticker skill against the reference on
    the selection metric. Every section but the fold table (pinned by sha256 in
    the study) is bound to the ledger hash of the step output holding it.

    Parameters
    ----------
    document : dict
        The study document :func:`zoo_document` made.

    Returns
    -------
    dict
        The template ``write`` stores as :data:`REPORT_SPEC_FILE`.
    """
    c, e = document["study"], document["experiment"]
    metric, reference = e["selection_metric"], c["reference_model"]
    zoo, step7 = ("zoo", "selection"), ("step7", "development")
    scored, report = ("step7", "scored"), ("step7", "report")
    comparison = "${evaluation}/comparison.json"
    return {
        "notes": f"Report spec for {WORKFLOW_FILE}, generated by index_options.pooled; never "
                 "hand-edit. ${...} values are the report step's inputs and layout paths.",
        "ledger": "${W}",
        "output_dir": "${L.report.output}",
        "title": f"Pooled model zoo {NAME}: selection, development and scored evaluation",
        "required_steps": ["zoo", "step7"],
        "max_rows": REPORT_MAX_ROWS,
        "statements": [
            {"title": "Scope.", "text": (
                "Descriptive evidence: the warm-up fold selects one candidate and variant per "
                "search partition; the scored folds evaluate those frozen choices; the holdout "
                f"from {c['fold_table']['holdout_start']} is never evaluated.")},
            {"title": "Admission.", "text": (
                f"A ticker enters a fold once its fit band holds {c['min_task_fit_rows']} "
                "rows; until then its calibration and scored rows are dropped for every model "
                "and listed in the admission tables.")},
            {"title": "Intervals.", "text": (
                f"Skill is 100 x (1 - model / {reference}) on {metric}. The paired date-block "
                "bootstrap intervals pool every scored ticker with equal cell weight; the "
                "per-ticker table is point skill without an interval.")},
            {"title": "Gaps.", "text": "n/a marks a value a model or stage did not produce, "
                                       "never a zero."},
        ],
        "sections": [
            _section("folds", "Fold table", c["fold_table"]["path"], "jsonl",
                     notes="The pinned rolling-origin table: one warm-up fold, then the "
                           "scored folds."),
            _section("selection_candidates", "Selection: every candidate and variant",
                     "${selection}/candidates.jsonl", "jsonl", step=zoo,
                     notes=f"Warm-up skill against {reference} on {metric}; one winner per "
                           "partition.", flatten={"separator": ", ", "max_items": 64}),
            _section("selection_variants", "Selection: frozen variants",
                     "${selection}/selected.json", "json", "variants", step=zoo),
            _section("admission_development_totals", "Development admission: rows dropped",
                     "${development}/admission.json", "json", "dropped_rows", step=step7),
            _section("admission_development", "Development admission: tickers waiting",
                     "${development}/admission.json", "json", "not_evaluated", step=step7,
                     required=False),
            _section("development_relative", f"Development: {metric} relative to {reference}",
                     comparison, "json", "development_relative_primary", step=report,
                     notes="Equal-cell mean ratio on the warm-up fold (below 1 beats it)."),
            _section("admission_scored_totals", "Scored admission: rows dropped",
                     "${scored}/admission.json", "json", "dropped_rows", step=scored),
            _section("admission_scored", "Scored admission: tickers waiting",
                     "${scored}/admission.json", "json", "not_evaluated", step=scored,
                     required=False),
            _section("scored_metrics", "Scored folds: metrics of the frozen choices",
                     comparison, "json", "metrics", step=report),
            _section("scored_intervals", f"Scored folds: paired block bootstrap on {metric}",
                     comparison, "json", "paired_block_intervals", step=report,
                     where={"metric": metric}),
            _section("per_ticker_skill", f"Scored folds: per-ticker skill on {metric}",
                     "${evaluation}/skill_by_exact_day.csv", "csv", step=report,
                     where={"metric": metric}),
            _section("identity", "Scored stage identity hashes", "${scored}/complete.json",
                     "json", "identity", step=scored),
        ],
    }


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
        The four files written: study, folds, workflow manifest, report spec.
    """
    out, study = [], zoo_document(configs_dir, measured_values)
    for name, document in ((STUDY_FILE, study), (FOLDS_FILE, folds_document(configs_dir)),
                           (WORKFLOW_FILE, workflow_document(configs_dir)),
                           (REPORT_SPEC_FILE, report_spec_document(study))):
        path = Path(configs_dir)/name
        path.write_text(json.dumps(document, indent=2)+"\n")
        out.append(path)
    return out
