"""Quarterly forecasts of a tuned ``fixed_loc`` recipe, 2019Q4 and 2020Q1 through 2025-11-28 (ADR-0298, proposed).

Each quarter trains on all history whose label settled before the quarter starts and holds its weights for
the quarter (Path A0003, A0004, A0008). Fitting, forecasting and scoring belong to ``CDFExperiment``,
``GridCDFMethod`` and ``StrikeRegionScores``; this module only turns one recipe file into their documents,
averages the seeds, publishes each quarter atomically and refuses what the owner forbids:

* 2019Q4 (open, tuned; the only 2019 quarter, owner 2026-11-05 ~17:55): the HPO tuned on these rows, so this forecast
  is favored, not independent evidence. Its receipt says so, and the score table reports it apart from the A0005 window.
* Through 2023 (``open``): one document per quarter whose validation band is the quarter, so the fit also
  leaves the mixture of every row. Forecasts rebuild from it exactly. The ``quarter`` job reads no score; the
  ``score`` job, after it, writes twCRPS and full CRPS.
* From 2024 (``sealed``): a fit-only document that never reads a row of its own quarter, then
  ``CDFExperiment.forecast``, which reads no outcome. No score, metric or table exists for these dates:
  :class:`ScoreGate` refuses every entry point. Reports show counts, keys, dates and hashes only.
* Nothing may be dated at or past 2025-11-29 (2026 stays sealed forever).
* A fit failure publishes nothing and retries (``raise_fit_errors``); every locked name with panel rows in a
  quarter has forecasts there, or an owner exception written in the run file.

Launch order: ``docs/plans/quarterly-forecasts-runbook.md``.

Usage: ``python -m index_options.quarterly_forecasts plan|rerun-plan|quarter|score|table|check|pin RUN.json ...``.
"""
import argparse
import copy
import datetime as dt
import glob
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dskit.evaluation.provenance import git_revision
from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.node import atomic_write

from .strike_region_scores import METRIC_SEAL, zlabel
from .ticker_lock import DEFAULT_LOCK, TickerLock

__all__ = ["CFloor", "CodeTree", "FOREVER_SEAL", "KEYS", "MONOTONE_TOLERANCE", "Pipeline", "Quarter", "Recipe", "ScoreGate", "SeedAverage", "main", "name_exceptions", "plan_rerun_check", "require_private"]

#: Owner's ruling of 2026-11-05 (PLAN.md): inference stops at 2025-11-28; nothing is dated at or past this.
FOREVER_SEAL = "2025-11-29"
FIRST_YEAR, LAST_YEAR = 2019, 2025
#: 2020Q1 is fold 2 and 2025Q4 fold 25. MIO's bridge takes whole folds from 1, so 2019 follows them. Only 2019Q4 runs
#: (owner, 2026-11-05 ~17:55) and it keeps fold 29; folds 26 through 28 are never used.
FOLD_2020Q1, FOLD_2019Q1, FIRST_2019_QUARTER = 2, 26, 4
#: The years the HPO objective read (PLAN.md, 2026-11-05 ~12:40): their forecasts are not independent evidence.
TUNED_YEARS = ("2019",)
TUNING_DISCLOSURE = ("The HPO tuned this recipe on all of 2019, so this quarter's forecasts are favored, "
                     "not independent evidence.")
#: The largest CDF decrease between grid points taken as float noise, in probability units. Both forecast files are
#: published as computed; a larger decrease refuses. MIO raises a noise point at read (owner, 2026-11-05 ~16:10).
MONOTONE_TOLERANCE = 1e-9
KEYS = ["symbol", "quote_date", "expiry"]
IDENTITY = [*KEYS, "fold", "band"]
CONTEXT_COLUMNS = ["spot", "reference_scale", "settlement_date"]
REGIME = "pooled"
#: The values at which CUBLAS is deterministic; a GPU fit without one fails, and it must fail before launch.
CUBLAS_VARIABLE, CUBLAS_VALUES = "CUBLAS_WORKSPACE_CONFIG", (":4096:8", ":16:8")
WINDOWS_DRIVES = "/mnt/"
WSL_NVIDIA_SMI = "/usr/lib/wsl/lib/nvidia-smi"


def require_private(path):
    """Create a directory and refuse unless owner-only took: chmod does nothing on a drvfs drive."""
    Path(path).mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    if Path(path).stat().st_mode & 0o077:
        raise IntegrityError(f"{path} could not be made private (0700): it may not hold sealed data")


def name_exceptions(config, symbols):
    """Return the owner's written exceptions to full coverage: ``{locked name: ruling}``; there are none today."""
    exceptions = config.get("owner_name_exceptions", {})
    if not isinstance(exceptions, dict) or set(exceptions)-set(symbols) or not all(
            isinstance(text, str) and text.strip() for text in exceptions.values()):
        raise ValueError("owner_name_exceptions must map locked names to the owner's written ruling")
    return exceptions


class CFloor:
    """The owner's C: floor, projected: WSL writes land on C: only while the WSL disk file is still there."""

    def __init__(self, config):
        self.floor, self.vhdx = config.get("c_floor_gb"), config.get("c_vhdx")

    def on_c(self, root):
        """Say whether writes under this root cost C: space."""
        if str(root).startswith(WINDOWS_DRIVES):
            return str(root).startswith(WINDOWS_DRIVES+"c/")
        return self.vhdx is not None and Path(self.vhdx).exists()

    def refuse(self, writes):
        """Refuse if ``(root, gb)`` writes would pass the floor; a run with no floor is a synthetic one."""
        if self.floor is None:
            return
        projected = sum(gb for root, gb in writes if self.on_c(root))
        free = shutil.disk_usage(WINDOWS_DRIVES+"c").free/1024**3
        if free-projected < self.floor:
            raise IntegrityError(f"C: has {free:.1f} GB free; {projected:.1f} GB more would pass the "
                                 f"owner's {self.floor} GB floor")


class CodeTree:
    """The git tree the run's code comes from: its commit, and a refusal to run real work from a dirty one.

    A real run's receipts must name the code that made them, so a real run starts only from a committed tree.
    The journal's own two files are the one exception: every real score job, panel stage and ``pin`` appends a
    row to them in this tree, by design (runbook), so they cannot count as a change. A synthetic run is exempt.
    """

    JOURNAL_FILES = ("children/index_options/docs/decisioning/actions.csv",
                     "children/index_options/docs/decisioning/README.md")

    def __init__(self, repo):
        self.repo = str(repo)

    def _git(self, *args):
        try:
            done = subprocess.run(["git", "-C", self.repo, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True)
        except OSError as error:
            raise IntegrityError(f"git cannot run in {self.repo}: {error}") from error
        if done.returncode:
            raise IntegrityError(f"{self.repo} is not a git work tree (git {args[0]} failed)")
        return done.stdout

    def _head(self):
        revision = git_revision(self.repo)      # dskit's owner of the commit; its dirty flag ignores untracked files
        if revision is None:
            raise IntegrityError(f"{self.repo} is not a git work tree (no commit)")
        return revision["commit"]

    def changes(self):
        """Return the changed or untracked paths, journal files aside (untracked code counts: the commit must hold it)."""
        entries = self._git("status", "--porcelain", "-z", "--untracked-files=all").split("\0")
        paths, skip_next = [], False
        for entry in entries:
            if skip_next:
                skip_next = False       # the old name of a rename or copy
            elif entry:
                paths.append(entry[3:])
                skip_next = entry[0] in "RC" or entry[1] in "RC"
        return [path for path in paths if path not in self.JOURNAL_FILES]

    def commit(self):
        """Return the HEAD commit when the tree is clean, else None."""
        return None if self.changes() else self._head()

    def require_clean(self, config):
        """Return the commit a run's receipt names; a real run from a dirty tree refuses.

        A synthetic run is exempt: a dirty tree, or no git at all, gives None.
        """
        if config.get("synthetic"):
            try:
                return self.commit()
            except IntegrityError:
                return None
        changed = self.changes()
        if changed:
            raise IntegrityError(f"a real run needs a committed tree: {len(changed)} changed or untracked "
                                 f"path(s) in {self.repo}, e.g. {changed[:3]}; commit first (runbook)")
        return self._head()


@dataclass(frozen=True)
class Quarter:
    """One retraining quarter: its forecast window and fold number."""

    name: str
    fold: int
    start: str
    end_before: str

    @property
    def sealed(self):
        """Say whether this quarter is forecast only, with no metric of any kind."""
        return self.start >= METRIC_SEAL

    @property
    def tuned(self):
        """Say whether the HPO objective read this quarter."""
        return self.start[:4] in TUNED_YEARS

    @property
    def train_end(self):
        """Return the last calendar day before the quarter."""
        return (dt.date.fromisoformat(self.start)-dt.timedelta(days=1)).isoformat()

    @property
    def val_end(self):
        """Return the last calendar day of the forecast window."""
        return (dt.date.fromisoformat(self.end_before)-dt.timedelta(days=1)).isoformat()

    @classmethod
    def all(cls):
        """Return every quarter, in order: 2019Q4 first, 2019Q1-Q3 never; the last stops at the forever seal."""
        out = []
        for year in range(FIRST_YEAR, LAST_YEAR+1):
            for number, month in enumerate((1, 4, 7, 10), start=1):
                if year == FIRST_YEAR and number < FIRST_2019_QUARTER:
                    continue
                after = f"{year+1}-01-01" if month == 10 else f"{year}-{month+3:02d}-01"
                fold = (FOLD_2019Q1 if year == FIRST_YEAR else FOLD_2020Q1+4*(year-FIRST_YEAR-1))+number-1
                out.append(cls(f"{year}Q{number}", fold, f"{year}-{month:02d}-01", min(after, FOREVER_SEAL)))
        return out

    @classmethod
    def parse(cls, name):
        """Return the named quarter; any other name refuses."""
        for quarter in cls.all():
            if quarter.name == name:
                return quarter
        raise ValueError(f"{name!r} is not a quarter: {FIRST_YEAR}Q{FIRST_2019_QUARTER}, then {FIRST_YEAR+1}Q1 through {LAST_YEAR}Q4")


class ScoreGate:
    """The structural refusals: no metric from 2024, no date at or past the forever seal."""

    @staticmethod
    def refuse_sealed(dates):
        """Refuse any date from 2024 on (owner, 2026-11-05); a row whose label reaches 2024 counts too."""
        if len(dates) and max(dates) >= METRIC_SEAL:
            raise IntegrityError(f"no metric is computed on a date from {METRIC_SEAL}")

    @staticmethod
    def refuse_quarter(quarter):
        """Refuse a sealed quarter before anything is read."""
        if quarter.sealed:
            raise IntegrityError(f"{quarter.name} is forecast only: no metric from {METRIC_SEAL}")

    @staticmethod
    def refuse_past_forever(dates):
        """Refuse any date at or past the forever seal."""
        if len(dates) and max(dates) >= FOREVER_SEAL:
            raise IntegrityError(f"a date is at or past {FOREVER_SEAL}: 2026 stays sealed forever")


class Recipe:
    """The tuned recipe file: HPO picks the values, the owner the seeds and the average (A0008 pending)."""

    KEYS = {"kind", "learning_rate", "dropout", "weight_decay", "hidden", "width", "epochs", "seeds", "average"}
    AVERAGES = ("linear_pool", "quantile_average")

    def __init__(self, values):
        if set(values) != self.KEYS:
            raise ValueError(f"recipe fields must be exactly {sorted(self.KEYS)}")
        if values["kind"] != "fixed_loc" or values["average"] not in self.AVERAGES:
            raise ValueError("recipe needs kind fixed_loc and an average in "+str(self.AVERAGES))
        seeds = values["seeds"]
        if (not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds)
                or any(type(s) is not int for s in seeds)
                or any(type(values[k]) is not int or values[k] < 1 for k in ("hidden", "width", "epochs"))
                or any(type(values[k]) is not float or not values[k] > 0
                       for k in ("learning_rate", "dropout", "weight_decay"))):
            raise ValueError("invalid recipe value type")
        self.values = copy.deepcopy(values)
        self.seeds, self.average = list(seeds), values["average"]
        self.identity = value_hash(values)
        self.training_identity = value_hash({k: v for k, v in values.items() if k != "average"})

    def apply(self, document):
        """Write the recipe into a fit document, as the HPO ``bo.py docs`` step does."""
        v = self.values
        training = document["training"]
        if training.get("fixed_location") is not True or training.get("patience") is not None:
            raise ValueError("the template must be fixed_location with no patience")
        training.update(learning_rate=v["learning_rate"], dropout=v["dropout"], weight_decay=v["weight_decay"],
                        epochs=v["epochs"], seeds=list(v["seeds"]))
        for regime in ("pooled", "unpooled"):
            params = document["candidates"][0]["adapters"][regime][0]["params"]
            params["width"] = v["width"]
            params["arch_params"]["hidden_size"] = v["hidden"]


class ForecastGrid:
    """The published CDF grid and the parameters of its score."""

    KEYS = {"z_grid", "score_weights", "tail", "levels", "batch_rows"}

    def __init__(self, spec):
        if set(spec) != self.KEYS or len(spec["z_grid"]) != len(spec["score_weights"]):
            raise ValueError("grid needs "+str(sorted(self.KEYS))+" with one score weight per grid point")
        self.z = list(spec["z_grid"])
        self.weights = list(spec["score_weights"])
        self.tail, self.levels, self.batch_rows = spec["tail"], spec["levels"], spec["batch_rows"]
        self.labels = ["z"+zlabel(z) for z in self.z]
        self.columns = ["method_cdf_"+label for label in self.labels]

    @property
    def forecast_identity(self):
        """Return the settings that change a published forecast."""
        return {"z_grid": self.z, "tail": self.tail, "levels": self.levels}

    @property
    def score_identity(self):
        """Return the settings that change a score."""
        return {**self.forecast_identity, "score_weights": self.weights}

    def finer(self, z_grid):
        """Return the output-only second grid over the same span, holding every point of this one; never scored."""
        z = list(z_grid)
        if (any(a >= b for a, b in zip(z, z[1:])) or set(self.z)-set(z) or z[0] != self.z[0] or z[-1] != self.z[-1]):
            raise ValueError("fine_z_grid must increase and hold every point of the scored grid, same span")
        return ForecastGrid({"z_grid": z, "score_weights": [None]*len(z), "tail": self.tail, "levels": self.levels,
                             "batch_rows": self.batch_rows})


class SeedAverage:
    """Average the seeds of a saved joint mixture, by the recipe's method.

    A joint fit trains each seed from its own reset generator and concatenates the mixtures in seed order,
    so every seed's components can be taken back out: the method may change with no new training.

    On any grid, ``linear_pool`` is the mixture itself, so a finer grid's shared points equal the coarser file
    exactly. ``quantile_average`` inverts each seed's CDF on the grid it is given and averages those quantiles, so
    a finer grid is its own average, more faithful between points; at shared points it is close but not bit-equal.
    """

    def __init__(self, method, seeds, grid):
        if method not in Recipe.AVERAGES:
            raise ValueError("unknown seed average "+str(method))
        self.method, self.seeds, self.grid = method, seeds, grid

    def _members(self, w, m, s):
        """Yield one seed's own mixture curve at a time."""
        from dskit.pipeline.libs.predictive_cdf import MixtureCurve

        width = w.shape[1]//self.seeds
        for j in range(self.seeds):
            part = slice(j*width, (j+1)*width)
            yield MixtureCurve(w[:, part]*self.seeds, m[:, part], s[:, part])

    def _quantile(self, w, m, s):
        import numpy as np
        from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod

        stack = np.stack([curve.cdf(self.grid.z) for curve in self._members(w, m, s)], axis=1)
        return GridCDFMethod.quantile_average_rows(np.clip(stack, 0., 1.), self.grid.z, self.grid.tail,
                                                   self.grid.levels)

    def curve(self, w, m, s):
        """Return the averaged forecast as a curve, for a scorer."""
        from dskit.pipeline.libs.predictive_cdf import GridCurve, MixtureCurve

        if self.method == "linear_pool":
            return MixtureCurve(w, m, s)
        knots, levels, _ = self._quantile(w, m, s)
        return GridCurve(knots, levels)

    def cdf(self, w, m, s):
        """Return the ``(rows, points)`` averaged CDF on the grid, in bounded batches."""
        import numpy as np

        parts = []
        for start in range(0, len(w), self.grid.batch_rows):
            rows = slice(start, start+self.grid.batch_rows)
            if self.method == "linear_pool":
                parts.append(np.clip(self.curve(w[rows], m[rows], s[rows]).cdf(self.grid.z), 0., 1.))
            else:
                parts.append(np.clip(self._quantile(w[rows], m[rows], s[rows])[2], 0., 1.))
        return np.concatenate(parts, axis=0)


class Pipeline:
    """One run: its recipe, the documents of every quarter and the stores they publish to."""

    KEYS = {"recipe", "fit_template", "score_template", "panel_open", "panel_sealed", "panel_verify", "open_root", "sealed_root",
            "scores_root", "plan_root", "device", "train_start", "grid", "proxy", "calibration", "ram_model",
            "score_ram_gb", "python", "repo"}
    OPTIONAL = {"ticker_lock", "synthetic", "quarters", "halt_file", "c_floor_gb", "c_vhdx", "gpu_slots", "notes",
                "owner_name_exceptions", "fine_z_grid", "rerun_check"}

    def __init__(self, config):
        if not self.KEYS <= set(config) or set(config)-self.KEYS-self.OPTIONAL:
            raise ValueError(f"run keys differ: missing {sorted(self.KEYS-set(config))}, "
                             f"unknown {sorted(set(config)-self.KEYS-self.OPTIONAL)}")
        if not config.get("synthetic"):
            if config["device"] != "cuda":
                raise ValueError("final forecasts train on the WSL GPU: device must be cuda (owner, 2026-11-05)")
            if Path(config.get("ticker_lock", DEFAULT_LOCK)).resolve() != DEFAULT_LOCK.resolve():
                raise ValueError("a real run uses the one locked ticker list (A0009)")
            if config.get("c_floor_gb") is None or not config.get("c_vhdx"):
                raise ValueError("a real run names c_floor_gb and c_vhdx, the WSL disk file while it is on C:")
        self.config = config
        self.recipe = Recipe(json.loads(Path(config["recipe"]).read_text()))
        self.grid = ForecastGrid(config["grid"])
        self.fine = self.grid.finer(config["fine_z_grid"]) if "fine_z_grid" in config else None
        self.plan_root = Path(config["plan_root"])
        self.quarters = [Quarter.parse(q) for q in config.get("quarters", [q.name for q in Quarter.all()])]
        self.exceptions = name_exceptions(config, self._lock().symbols)
        self.rerun = config.get("rerun_check")
        if self.rerun and (len(self.quarters) != 1 or self.quarters[0].sealed):
            raise ValueError("a rerun check repeats one open quarter")
        self._study_settings = None

    @classmethod
    def load(cls, path):
        """Read a run file."""
        return cls(json.loads(Path(path).read_text()))

    # -- paths ------------------------------------------------------------------------------------------

    def _root(self, quarter):
        return Path(self.config["sealed_root" if quarter.sealed else "open_root"])

    def _fit_output(self, quarter):
        return self._root(quarter)/"fits"/self.recipe.training_identity[:12]/quarter.name

    def fit_root(self, quarter):
        """Return the directory of the quarter's one pooled cell."""
        document = self.fit_document(quarter)
        arm, candidate = next(iter(document["arms"])), document["candidates"][0]["name"]
        return self._fit_output(quarter)/REGIME/arm/candidate/str(quarter.fold)/REGIME

    def forecast_dir(self, quarter):
        """Return the quarter's published folder."""
        return self._root(quarter)/f"forecasts-{self.recipe.identity[:12]}"/quarter.name

    def forecast_store(self, quarter):
        """Return the immutable store of the quarter's forecasts."""
        identity = {"recipe": self.recipe.identity, "quarter": quarter.name, "grid": self.grid.forecast_identity,
                    "fit_document": self._planned(quarter, "fit")}
        if self.fine and not quarter.sealed:
            identity["fine_z_grid"] = self.fine.z
        return AtomicFitStore(self.forecast_dir(quarter), identity)

    def _fit_tag(self, quarter):
        """Name a score cache by its fit document, so a changed panel or fit can never reuse it."""
        return AtomicFitStore.file_hash(self._doc_path(quarter, "fit"))[:12]

    def _score_tag(self):
        """Name score files by the rest of what changes a score: the grid, its weights and the score template."""
        return value_hash({"grid": self.grid.score_identity,
                           "template": AtomicFitStore.file_hash(self.config["score_template"])})[:12]

    def method_score_path(self, quarter):
        """Return the per-row scores of the published forecast (through 2023 only)."""
        ScoreGate.refuse_quarter(quarter)
        name = f"{quarter.name}-method-{self.recipe.identity[:12]}-{self._fit_tag(quarter)}-{self._score_tag()}.parquet"
        return Path(self.config["scores_root"])/name

    def full_scorer_path(self, quarter):
        """Return the full scorer's output folder (through 2023 only)."""
        ScoreGate.refuse_quarter(quarter)
        return Path(self.config["scores_root"])/f"{self._fit_tag(quarter)}-{self._score_tag()}"/quarter.name

    def score_table_path(self):
        """Return the through-2023 score table."""
        return Path(self.config["scores_root"])/f"score-table-{self.recipe.identity[:12]}-{self._score_tag()}.csv"

    def score_gate_path(self):
        """Return the through-2023 gate summary."""
        return Path(self.config["scores_root"])/f"score-gate-{self.recipe.identity[:12]}-{self._score_tag()}.json"

    def refuse_sealed_path(self, path):
        """Refuse a metric or a MIO input read from the sealed folder."""
        root = Path(self.config["sealed_root"]).resolve()
        if root == Path(path).resolve() or root in Path(path).resolve().parents:
            raise IntegrityError(f"nothing under the sealed folder is scored or given to MIO: {path}")

    # -- plan -------------------------------------------------------------------------------------------

    def _lock(self):
        return TickerLock.load(self.config.get("ticker_lock", DEFAULT_LOCK))

    def _doc_path(self, quarter, kind):
        return self.plan_root/"docs"/f"{quarter.name}-{kind}.json"

    def _planned(self, quarter, kind):
        plan = json.loads((self.plan_root/"plan.json").read_text())
        if plan["recipe_sha256"] != self.recipe.identity:
            raise IntegrityError("the recipe file changed after the plan: plan again")
        try:
            digest = plan["documents"][quarter.name][kind]
        except KeyError as error:
            raise IntegrityError(f"{quarter.name} {kind} is not in the plan") from error
        if AtomicFitStore.file_hash(self._doc_path(quarter, kind)) != digest:
            raise IntegrityError(f"{quarter.name} {kind} document changed after the plan")
        return digest

    def fit_document(self, quarter):
        """Return the planned fit document, checked against the plan."""
        self._planned(quarter, "fit")
        return json.loads(self._doc_path(quarter, "fit").read_text())

    def full_scorer_document(self, quarter):
        """Return the planned full-scorer document; a sealed quarter has none and refuses."""
        ScoreGate.refuse_quarter(quarter)
        self._planned(quarter, "score")
        document = json.loads(self._doc_path(quarter, "score").read_text())
        if document["pipeline"]["scores"]["params"]["output"] != str(self.full_scorer_path(quarter)):
            raise IntegrityError(f"{quarter.name}: the grid or score template changed after the plan: plan again")
        return document

    def _relocated_pins(self, pins):
        out = {}
        for path in pins:
            found = re.match(r"^.*?/(dskit/.*)$", path)
            if not found:
                raise ValueError("a source pin is not under dskit/: "+path)
            target = Path(self.config["repo"])/found.group(1)
            out[str(target)] = AtomicFitStore.file_hash(target)
        return out

    def _make_fit(self, quarter, template, panel, panel_sha, folds):
        document = copy.deepcopy(template)
        document["raise_fit_errors"] = True     # no terminal "skipped": a CUDA error is retried, never final
        if len(document["candidates"]) != 1 or len(document["arms"]) != 1 or not document.get("refit_full_train"):
            raise ValueError("the template must be one refit_full_train arm and candidate")
        self.recipe.apply(document)
        document["capacity"]["pooled"]["device"] = self.config["device"]
        document["output"] = str(self._fit_output(quarter))
        document["panel"], document["panel_sha256"] = panel, panel_sha
        document["source_hashes"] = self._relocated_pins(document["source_hashes"])
        table = document["study"]["fold_table"]
        table.update(path=str(folds), sha256=AtomicFitStore.file_hash(folds), roles=["scored"])
        if quarter.sealed:
            # Fit only: this quarter's rows are never read, its fit stage scores nothing, no monitor loss.
            table["holdout_start"] = FOREVER_SEAL
            document["date_upper_exclusive"] = document["end_upper_exclusive"] = quarter.start
            document["training_telemetry"] = False
        else:
            # The quarter is the validation band, so the fit stage leaves each row's mixture.
            table["holdout_start"] = METRIC_SEAL
            document["date_upper_exclusive"], document["end_upper_exclusive"] = quarter.end_before, METRIC_SEAL
        return document

    def _make_forecast(self, quarter, document):
        arm, candidate = next(iter(document["arms"])), document["candidates"][0]["name"]
        return {"fold": quarter.fold, "arm": arm, "candidate": candidate, "start": quarter.start,
                "end_before": quarter.end_before, "z_grid": self.grid.z, "grid_labels": self.grid.labels,
                "output": str(self._root(quarter)/"components"/self.recipe.training_identity[:12]/quarter.name),
                "batch_rows": self.grid.batch_rows, "proxy": self.config["proxy"],
                "calibration": self.config["calibration"]}

    def _make_score(self, quarter, template, document_path, panel):
        document = copy.deepcopy(template)
        document["name"] = "quarterly-scores-"+quarter.name.lower()
        document["outputs"]["run_root"] = str(Path(self.config["scores_root"])/"runs"/quarter.name)
        params = document["pipeline"]["scores"]["params"]
        params.update(experiment_document=str(document_path), panel=panel, end_before=METRIC_SEAL,
                      output=str(self.full_scorer_path(quarter)))
        return document

    def _panel_dates(self, path):
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["symbol", "quote_date", "settlement_date", "expiry"]).to_pandas()
        for column in ("quote_date", "settlement_date", "expiry"):
            ScoreGate.refuse_past_forever(table[column])
        names = set(table["symbol"].unique())
        if names-set(self._lock().symbols):
            raise IntegrityError("the panel holds a name outside the locked list")
        return table

    def _ram(self, rows):
        model = self.config["ram_model"]
        return round(model["base_gb"]+model["gb_per_million_rows"]*rows/1e6, 1)

    def _job(self, job_id, verb, quarter, ram, basis, disk, gpu, **ordering):
        repo = self.config["repo"]
        child = repo+"/children/index_options"
        return {"id": job_id, "cwd": child, "cores": 3, "gpu": gpu, "ram_gb": ram, "ram_basis": basis, "disk_gb": disk,
               "retries": 1, "allowed_hosts": ["wsl"],
               "command": f"{self.config['python']} -B -m index_options.quarterly_forecasts {verb} "
                          f"{self.plan_root/'run.json'} {quarter.name}",
               "env": {"PYTHONPATH": f"{repo}:{child}", "PYTHONDONTWRITEBYTECODE": "1",
                       CUBLAS_VARIABLE: CUBLAS_VALUES[0], "OMP_NUM_THREADS": "3"}, **ordering}

    def _refuse_unverified_extension(self, shas):
        """Refuse sealed planning unless ``verify`` passed on exactly these two panels."""
        path = self.config.get("panel_verify")
        record = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
        if record.get("passed") is not True:
            raise IntegrityError("the extended panel has no passed verify.json: run panel_extension verify")
        if record.get("panel_sha256") != shas.get(True) or record.get("old_frame_sha256") != shas.get(False):
            raise IntegrityError("verify.json proves other panels than this plan's: run panel_extension verify")
        if record.get("synthetic") and not self.config.get("synthetic"):
            raise IntegrityError("verify.json came from a synthetic extension")

    def plan(self):
        """Write every document, the plan and the compute-gate job lists; start nothing."""
        lock = self._lock()
        c = self.config
        self.plan_root.mkdir(parents=True, exist_ok=True)
        (self.plan_root/"docs").mkdir(exist_ok=True)
        (self.plan_root/"folds").mkdir(exist_ok=True)
        fit_template = json.loads(Path(c["fit_template"]).read_text())
        score_template = json.loads(Path(c["score_template"]).read_text())
        require_private(c["sealed_root"])
        panels = {False: c["panel_open"], True: c["panel_sealed"]}
        shas = {sealed: AtomicFitStore.file_hash(path) for sealed, path in panels.items() if path}
        if True in shas:
            self._refuse_unverified_extension(shas)
        dates = {sealed: self._panel_dates(panels[sealed]) for sealed in shas}
        if False in dates:
            ScoreGate.refuse_sealed(dates[False]["settlement_date"])
            ScoreGate.refuse_sealed(dates[False]["quote_date"])
        documents, jobs, scores, writes = {}, {"open": [], "sealed": []}, [], []
        forecast_gb, score_gb = .2, .6
        for quarter in self.quarters:
            if quarter.sealed not in dates:
                continue            # the extended panel does not exist yet: plan again once it does
            folds = self.plan_root/"folds"/f"{quarter.name}.jsonl"
            atomic_write(str(folds), (json.dumps({
                "fold": quarter.fold, "role": "scored", "train_start": c["train_start"],
                "train_end": quarter.train_end, "val_start": quarter.start, "val_end": quarter.val_end})+"\n").encode())
            fit = self._make_fit(quarter, fit_template, panels[quarter.sealed], shas[quarter.sealed], folds)
            lock.check(fit)
            fit_path = self._doc_path(quarter, "fit")
            atomic_write(str(fit_path), json.dumps(fit, indent=1).encode())
            entry = {"fit": AtomicFitStore.file_hash(fit_path)}
            frame = dates[quarter.sealed]
            read = frame[(frame.quote_date < fit["date_upper_exclusive"])
                         & (frame.settlement_date < fit["end_upper_exclusive"])]
            ram = self._ram(len(read))
            basis = c["ram_model"]["basis"]+f"; {len(read)} rows loaded"
            if quarter.sealed:
                spec_path = self._doc_path(quarter, "forecast")
                atomic_write(str(spec_path), json.dumps(self._make_forecast(quarter, fit), indent=1).encode())
                entry["forecast"] = AtomicFitStore.file_hash(spec_path)
                jobs["sealed"].append(self._job("forecast-"+quarter.name, "quarter", quarter, ram, basis, forecast_gb, 1))
                writes.append((c["sealed_root"], forecast_gb))
            elif self.rerun:
                jobs["open"].append(self._job("forecast-"+quarter.name, "quarter", quarter, ram, basis, forecast_gb, 1))
                writes.append((c["open_root"], forecast_gb))
            else:
                score = self._make_score(quarter, score_template, fit_path, panels[False])
                score_path = self._doc_path(quarter, "score")
                atomic_write(str(score_path), json.dumps(score, indent=1).encode())
                entry["score"] = AtomicFitStore.file_hash(score_path)
                jobs["open"].append(self._job("forecast-"+quarter.name, "quarter", quarter, ram, basis, forecast_gb, 1))
                # One job file: the gate resolves depends_on only inside a file; one scorer at a time for its RAM.
                scores.append(self._job("score-"+quarter.name, "score", quarter, c["score_ram_gb"],
                                        "zoo score-fl21/f90 receipts peak RSS 11.3-14.1 GB", score_gb, 1,
                                        depends_on=["forecast-"+quarter.name], serial_group="scores",
                                        serial_reason="scores the quarter's published forecast; one 14 GB scorer at a time"))
                writes += [(c["open_root"], forecast_gb), (c["scores_root"], score_gb)]
            documents[quarter.name] = entry
        jobs["open"] += scores
        CFloor(c).refuse(writes)
        atomic_write(str(self.plan_root/"plan.json"), json.dumps({
            "recipe": self.recipe.values, "recipe_sha256": self.recipe.identity,
            "panels": {("sealed" if k else "open"): v for k, v in shas.items()},
            "documents": documents}, indent=1).encode())
        for name, listed in jobs.items():
            if self.rerun and not listed:
                continue
            run_id = f"quarterly-{name}-{self.recipe.identity[:8]}"+(f"-rerun-{self.rerun}" if self.rerun else "")
            atomic_write(str(self.plan_root/f"jobs-{name}.json"), json.dumps(
                {"run_id": run_id, "limits": {"gpu_slots": c.get("gpu_slots", 3)}, "jobs": listed}, indent=1).encode())
        return documents

    # -- one quarter -------------------------------------------------------------------------------------

    def _gates(self):
        CodeTree(self.config["repo"]).require_clean(self.config)
        if self.config["device"] == "cuda" and os.environ.get(CUBLAS_VARIABLE) not in CUBLAS_VALUES:
            raise RuntimeError(f"{CUBLAS_VARIABLE} must be one of {CUBLAS_VALUES} before a GPU fit starts")
        halt = self.config.get("halt_file")
        if halt and Path(halt).exists():
            raise RuntimeError(f"HALT file present: {halt}")
        floor = self.config.get("c_floor_gb")
        if floor is not None and shutil.disk_usage(WINDOWS_DRIVES+"c").free < floor*1024**3:
            raise RuntimeError(f"C: is below the owner's {floor} GB floor")

    def _prove_fit(self, quarter):
        """Refuse a skipped fit or settings other than the recipe's, from the fit's own record.

        ``raise_fit_errors`` means these documents never publish ``skipped``; one found here is kept for a person.
        """
        root = self.fit_root(quarter)
        if (root/"skipped").exists() or not (root/"completed").exists():
            raise IntegrityError(f"{quarter.name}: the fit was skipped or did not finish")
        summary = json.loads((root/"completed"/"summary.json").read_text())
        settings = summary["settings"]
        v = self.recipe.values
        adapter = settings["encoder"]["adapter"]["params"]
        if (any(settings[k] != v[k] for k in ("learning_rate", "dropout", "weight_decay", "epochs", "seeds"))
                or adapter["arch_params"]["hidden_size"] != v["hidden"] or adapter["width"] != v["width"]
                or summary["total_epochs"] != [v["epochs"]]*len(v["seeds"])):
            raise IntegrityError(f"{quarter.name}: the fit's settings differ from the recipe")
        if quarter.sealed and (root/"scores").exists():
            raise IntegrityError(f"{quarter.name}: a sealed fit may hold no scores")
        return summary

    def _bounded(self, quarter, columns, panel=None):
        from dskit.pipeline.libs.parquet import DateBoundedParquet

        document = self.fit_document(quarter)
        bounds = {"quote_date": {"start": quarter.start, "end_before": quarter.end_before}}
        if not quarter.sealed:
            bounds["settlement_date"] = {"end_before": METRIC_SEAL}
        return DateBoundedParquet(panel or document["panel"], columns, bounds).read().to_pandas()

    def mixture_rows(self, quarter):
        """Return ``(frame, [weights, means, scales])`` of the quarter's rows, sorted by identity."""
        import pandas as pd

        if quarter.sealed:
            frame = pd.read_parquet(Path(self._forecast_spec(quarter)["output"])/"forecasts.parquet")
        else:
            paths = sorted(p for p in (self.fit_root(quarter)/"scores").glob("*/scores.parquet")
                           if ".pending-" not in p.parent.name)
            frame = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
        frame = frame.sort_values(KEYS).reset_index(drop=True)
        count = sum(c.startswith("weights") for c in frame)
        parts = [frame[[f"{name}{i}" for i in range(count)]].to_numpy(float) for name in ("weights", "means", "scales")]
        kept = [*KEYS, "settlement_date", *[c for c in ("model_crps",) if c in frame]]
        return frame[kept], parts

    def _forecast_spec(self, quarter):
        self._planned(quarter, "forecast")
        return json.loads(self._doc_path(quarter, "forecast").read_text())

    def _averager(self):
        return SeedAverage(self.recipe.average, len(self.recipe.seeds), self.grid)

    def _refuse_failed(self, quarter, report):
        failed = sorted(k for k, v in report.items() if k not in self.REPORT_INFO and v is not True)
        if failed:
            raise IntegrityError(f"{quarter.name}: forecast structure failed: {failed}")

    def _hardware(self):
        """Return the training hardware for the receipt: GPU model, driver and CUDA version."""
        if self.config["device"] != "cuda":
            return {"device": self.config["device"]}
        import torch

        tool = shutil.which("nvidia-smi") or WSL_NVIDIA_SMI
        try:
            driver = subprocess.run([tool, "--query-gpu=driver_version", "--format=csv,noheader"], check=True,
                                    stdout=subprocess.PIPE, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            driver = "unavailable"              # a finished fit is not lost over its receipt's driver line
        return {"device": "cuda", "gpu_model": torch.cuda.get_device_name(0), "driver": driver,
                "cuda_version": torch.version.cuda}

    FINE_FILE = "forecasts-fine.parquet"

    def _fine_forecasts(self, quarter, forecasts, mixture):
        """Return the second grid's rows as computed; ``structure`` holds them to the first grid's tolerance."""
        import pandas as pd

        cdf = SeedAverage(self.recipe.average, len(self.recipe.seeds), self.fine).cdf(*mixture)
        return pd.concat([forecasts[[*IDENTITY, "fitted"]], pd.DataFrame(cdf, columns=self.fine.columns)], axis=1)

    def _forecast_files(self, quarter, frame, mixture, document):
        """Return the published payloads: forecasts, contexts and the receipt."""
        import hashlib
        import io
        import pandas as pd

        forecasts = frame[KEYS].copy()
        forecasts["fold"], forecasts["band"], forecasts["fitted"] = quarter.fold, "val", True
        forecasts = pd.concat([forecasts, pd.DataFrame(self._averager().cdf(*mixture), columns=self.grid.columns)], axis=1)
        contexts = self._bounded(quarter, [*KEYS, *CONTEXT_COLUMNS])
        contexts = contexts.merge(frame[KEYS], on=KEYS, validate="one_to_one").sort_values(KEYS).reset_index(drop=True)
        contexts.insert(3, "fold", quarter.fold)
        contexts.insert(4, "band", "val")
        CDFExperiment.check_pairing(forecasts, contexts, IDENTITY)
        self._refuse_failed(quarter, self.structure(quarter, forecasts))
        tables = [("forecasts.parquet", forecasts), ("contexts.parquet", contexts)]
        if self.fine and not quarter.sealed:
            fine = self._fine_forecasts(quarter, forecasts, mixture)
            self._refuse_failed(quarter, self.structure(quarter, fine, self.fine))
            tables.append((self.FINE_FILE, fine))
        files = {}
        for name, table in tables:
            buffer = io.BytesIO()
            table.to_parquet(buffer, index=False, compression="zstd")
            files[name] = buffer.getvalue()
        named = set(forecasts["symbol"])
        limit = METRIC_SEAL if not quarter.sealed else FOREVER_SEAL
        receipt = {"schema": "quarterly-forecast/v1", "quarter": quarter.name, "bridge_fold": quarter.fold,
                   "forecast_window": {"start": quarter.start, "end_before": quarter.end_before},
                   "label_cutoff": quarter.start, "recipe": self.recipe.values, "recipe_sha256": self.recipe.identity,
                   "fit_document_sha256": self._planned(quarter, "fit"), "panel_sha256": document["panel_sha256"],
                   "fold_table_sha256": document["study"]["fold_table"]["sha256"],
                   "fit_manifest_sha256": AtomicFitStore.file_hash(self.fit_root(quarter)/"fit"/"complete.json"),
                   "forecasts_sha256": hashlib.sha256(files["forecasts.parquet"]).hexdigest(),
                   "contexts_sha256": hashlib.sha256(files["contexts.parquet"]).hexdigest(),
                   "device": self.config["device"], "hardware": self._hardware(),
                   "code_commit": CodeTree(self.config["repo"]).require_clean(self.config),
                   "synthetic": bool(self.config.get("synthetic")),
                   "ticker_lock_sha256": AtomicFitStore.file_hash(self.config.get("ticker_lock", DEFAULT_LOCK)),
                   "rows": len(forecasts), "expected_names": sorted(named),
                   "missing_names": [t for t in document["tickers"] if t not in named],
                   "owner_name_exceptions": self.exceptions,
                   "coverage_note": f"rows expiring or settling at or after {limit} are absent, so the last weeks "
                                    "before it hold short-dated rows only",
                   "seed_average_status": "open: the seeds and the average await the owner's A0008 decision",
                   "evaluation_computed": False, "z_grid": self.grid.z, "grid_labels": self.grid.labels}
        if self.FINE_FILE in files:
            receipt["fine_grid"] = {"file": self.FINE_FILE, "sha256": hashlib.sha256(files[self.FINE_FILE]).hexdigest(),
                                    "z_grid": self.fine.z, "grid_labels": self.fine.labels,
                                    "seed_average": self.recipe.average, "scored": False}
        if quarter.tuned:
            receipt["tuning_disclosure"] = TUNING_DISCLOSURE
        files["receipt.json"] = json.dumps(receipt, sort_keys=True, indent=1, allow_nan=False).encode()
        return files

    def _fit_recording_failure(self, quarter, experiment):
        """Run the fit; a failure stays on record under the plan, publishes nothing and is free to retry.

        A sealed quarter's message may quote a value of 2024 on: it keeps the exception's words, without numbers.
        """
        try:
            experiment.run(REGIME)
        except Exception as error:
            reason = re.sub(r"\d[\d.,:eE+-]*", "#", str(error))[:200] if quarter.sealed else str(error)[:300]
            folder = self.plan_root/"fit-failures"
            folder.mkdir(exist_ok=True)
            with (folder/f"{quarter.name}.jsonl").open("a") as stream:
                stream.write(json.dumps({"quarter": quarter.name, "error": type(error).__name__,
                                         "reason": reason, "at": dt.datetime.now(dt.timezone.utc).isoformat()})+"\n")
            if quarter.sealed:
                raise RuntimeError(f"{quarter.name}: the fit failed: {type(error).__name__}: {reason}") from None
            raise

    def run_quarter(self, quarter):
        """Fit, forecast and publish one quarter; a finished one is verified, never repeated."""
        started = time.monotonic()
        self._gates()
        document = self.fit_document(quarter)
        store = self.forecast_store(quarter)
        if store.path.exists():
            store.verify()
        else:
            self._lock().check(document)
            experiment = CDFExperiment(document)        # one instance: one fewer whole-panel hash
            self._fit_recording_failure(quarter, experiment)
            self._prove_fit(quarter)
            self._gates()
            if quarter.sealed:
                experiment.forecast(self._forecast_spec(quarter))
            frame, mixture = self.mixture_rows(quarter)
            store.publish(self._forecast_files(quarter, frame, mixture, document))
        receipts = self.plan_root/"receipts"
        receipts.mkdir(exist_ok=True)
        atomic_write(str(receipts/f"{quarter.name}.json"), json.dumps({
            "quarter": quarter.name, "seconds": round(time.monotonic()-started),
            "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, 2)}).encode())

    # -- structural checks: counts, keys, dates, finite, hashes; never a value --------------------------------

    REPORT_INFO = {"rows", "symbols", "first_quote", "last_quote", "names_missing"}

    def structure(self, quarter, frame, grid=None):
        """Return the structural facts of one quarter's forecasts; no value is returned or shown."""
        import numpy as np

        panel = self._bounded(quarter, KEYS)
        panel = panel[~panel["symbol"].isin(self.exceptions)].sort_values(KEYS).reset_index(drop=True)
        missing = sorted(set(panel["symbol"])-set(frame["symbol"]))
        grid = grid or self.grid
        cdf = frame[grid.columns].to_numpy(float)
        ordered = frame[KEYS].sort_values(KEYS).reset_index(drop=True)
        finite = bool(np.isfinite(cdf).all())
        return {"rows": len(frame), "symbols": int(frame["symbol"].nunique()),
                "first_quote": str(frame["quote_date"].min()), "last_quote": str(frame["quote_date"].max()),
                "names_missing": missing, "names_covered": not missing,
                "keys_unique": not frame[KEYS].duplicated().any(),
                "rows_match_panel": ordered.equals(panel[KEYS]),
                "dates_in_window": bool(frame["quote_date"].between(quarter.start, quarter.end_before, inclusive="left").all()),
                "before_forever_seal": str(frame["expiry"].max()) < FOREVER_SEAL,
                "before_metric_seal": quarter.sealed or str(frame["expiry"].max()) < METRIC_SEAL,
                "fold_band_fitted": bool(frame["fold"].eq(quarter.fold).all() and frame["band"].eq("val").all()
                                         and frame["fitted"].all()),
                "finite": finite, "probabilities": finite and bool((cdf >= 0).all() and (cdf <= 1).all()),
                "monotone": finite and bool((np.diff(cdf, axis=1) >= -MONOTONE_TOLERANCE).all()),
                "columns": set(frame) == {*IDENTITY, "fitted", *grid.columns}}

    def required_quarters(self):
        """Return the quarters a finished run must hold: a synthetic run's own; otherwise every one, or only the open ones while no sealed panel is set."""
        if self.config.get("synthetic"):
            return self.quarters
        return [q for q in Quarter.all() if self.config.get("panel_sealed") or not q.sealed]

    def check(self):
        """Verify every published quarter, write the two hash manifests, return the reports."""
        import pandas as pd

        out = {"open": {}, "sealed": {}, "missing": []}
        for quarter in self.required_quarters():
            if not self.forecast_dir(quarter).exists():
                out["missing"].append(quarter.name)
                continue
            store = self.forecast_store(quarter)
            record = store.verify()
            frame = pd.read_parquet(store.path/"forecasts.parquet")
            report = self.structure(quarter, frame)
            if self.fine and not quarter.sealed:
                fine = self.structure(quarter, pd.read_parquet(store.path/self.FINE_FILE), self.fine)
                report.update({"fine_"+k: v for k, v in fine.items() if k not in self.REPORT_INFO})
            bad = sorted(k for k, v in report.items() if k not in self.REPORT_INFO and v is not True)
            out["sealed" if quarter.sealed else "open"][quarter.name] = {**report, "failed": bad, "files": record["files"]}
        for name, root in (("open", self.config["open_root"]), ("sealed", self.config["sealed_root"])):
            manifest = {"schema": "quarterly-forecast-manifest/v1", "recipe_sha256": self.recipe.identity,
                        "quarters": out[name]}
            Path(root).mkdir(parents=True, exist_ok=True)
            atomic_write(str(Path(root)/"MANIFEST.json"), json.dumps(manifest, indent=1, sort_keys=True).encode())
        return out

    # -- through 2023 only ----------------------------------------------------------------------------------

    def score_rows(self, frame, cdf, curve, y, stored_crps=None):
        """Return each row's twCRPS and full CRPS; refuses any row dated or settling from 2024."""
        from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod
        from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

        ScoreGate.refuse_sealed(frame["quote_date"])
        ScoreGate.refuse_sealed(frame["settlement_date"])
        twcrps = GridCDFMethod.grid_score_rows(cdf, y, self.grid.z, self.grid.weights, self.grid.tail)
        s = self._study_settings
        crps = stored_crps if stored_crps is not None else ChronologicalCDFStudy.scores(
            curve, y, s["samples"], s["tail_intervals"], s["tail_points"])[0]["crps"]
        return twcrps, crps

    def method_scores(self, quarter):
        """Score the published forecast by row; the averaged forecast itself, which the full scorer never sees."""
        import numpy as np

        ScoreGate.refuse_quarter(quarter)
        target = self.method_score_path(quarter)
        if target.exists():
            return target
        self.refuse_sealed_path(self.forecast_dir(quarter))
        self.forecast_store(quarter).verify()
        document = self.fit_document(quarter)
        self._study_settings = document["study"]
        frame, (w, m, s) = self.mixture_rows(quarter)
        labels = self._bounded(quarter, [*KEYS, "terminal_return", "reference_scale"], self.config["panel_open"])
        ScoreGate.refuse_sealed(labels["quote_date"])
        labels = frame[KEYS].merge(labels, on=KEYS, how="left", validate="one_to_one")
        y = (labels["terminal_return"]/labels["reference_scale"]).to_numpy(float)
        average, twcrps, crps = self._averager(), [], []
        for start in range(0, len(frame), self.grid.batch_rows):
            rows = slice(start, start+self.grid.batch_rows)
            mixture = (w[rows], m[rows], s[rows])
            # A linear pool IS the fit stage's own mixture, whose CRPS the shards already hold; no second bisection.
            stored = frame["model_crps"].to_numpy(float)[rows] if self.recipe.average == "linear_pool" else None
            a, b = self.score_rows(frame.iloc[rows], average.cdf(*mixture), None if stored is not None else average.curve(*mixture),
                                   y[rows], stored)
            twcrps.append(a)
            crps.append(b)
        out = frame[[*KEYS, "settlement_date"]].copy()
        out["fold"], out["twcrps"], out["crps"] = quarter.fold, np.concatenate(twcrps), np.concatenate(crps)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name+".partial")
        out.to_parquet(temp, index=False)
        os.replace(temp, target)
        return target

    def score_quarter(self, quarter):
        """The ``score`` job: the published forecast's own scores, then the comparators' from the full scorer."""
        ScoreGate.refuse_quarter(quarter)
        self._gates()
        if self.rerun:
            raise IntegrityError("a rerun check compares forecast hashes and scores nothing")
        self.full_scorer_document(quarter)
        self.method_scores(quarter)
        return self.run_full_scorer(quarter)

    @staticmethod
    def _live_scorers(document_path):
        """Return the pids of live ``dskit.pipeline run`` processes for this document (this process aside)."""
        listing = subprocess.run(["ps", "-axww", "-o", "pid=,args="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, check=False).stdout
        needle = f"dskit.pipeline run {document_path}"
        found = []
        for line in listing.splitlines():
            pid, _, args = line.strip().partition(" ")
            if pid.isdigit() and int(pid) != os.getpid() and needle in args:
                found.append(int(pid))
        return found

    def _clear_interrupted_runs(self, quarter):
        """Remove this plan's unfinished scorer run dirs for the quarter, so a relaunch can claim its own.

        The pipeline refuses a run dir that exists, so an interrupted scorer would fail every retry. Refuses while a
        ``dskit.pipeline run`` of this document is alive. A run belongs to this plan when its ``config.json`` holds the
        current document's scorer params (its output path and experiment document); an older plan's run is left alone,
        neither removed nor blocking. Only a dir with no ``result.json`` saying ``ran`` goes; a finished run is evidence
        and is never removed.
        """
        path = self._doc_path(quarter, "score")
        live = self._live_scorers(path)
        if live:
            raise IntegrityError(f"{quarter.name}: a scorer run of {path.name} is still alive (pid {live[0]}); "
                                 "nothing was cleared: wait for it or stop it first")
        document = json.loads(path.read_text())
        params = document["pipeline"]["scores"]["params"]
        root, prefix = Path(document["outputs"]["run_root"]), document["name"]+"-"
        for run in sorted(root.iterdir()) if root.is_dir() else ():
            if not run.is_dir() or not run.name.startswith(prefix):
                continue
            try:
                ours = json.loads((run/"config.json").read_text())["pipeline"]["scores"]["params"] == params
            except (OSError, ValueError, KeyError, TypeError):
                ours = True         # no readable record: a run killed at birth, nothing in it to keep
            if not ours:
                continue
            try:
                state = json.loads((run/"result.json").read_text()).get("state")
            except (OSError, ValueError, AttributeError):
                state = None
            if state == "ran":
                raise IntegrityError(f"{quarter.name}: the finished scorer run {run} of this plan has no published "
                                     f"output {self.full_scorer_path(quarter)}; the run is kept: ask the owner")
            shutil.rmtree(run)

    def run_full_scorer(self, quarter):
        """Run ``StrikeRegionScores`` for one through-2023 quarter; skipped once published."""
        ScoreGate.refuse_quarter(quarter)
        self._gates()
        self.full_scorer_document(quarter)
        output = self.full_scorer_path(quarter)
        if output.exists():
            return output
        self._clear_interrupted_runs(quarter)       # first: it refuses while a scorer is alive, before anything goes
        partial = output.parent/f".{output.name}.partial"
        if partial.exists():
            shutil.rmtree(partial)        # this quarter's interrupted build is ours to remove
        self._prove_fit(quarter)
        child = Path(self.config["repo"])/"children"/"index_options"
        output.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([sys.executable, "-B", "-m", "dskit.pipeline", "run", str(self._doc_path(quarter, "score")),
                               "--asof", "2023-12-31"], cwd=child, check=False, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
        if done.returncode:
            raise RuntimeError(f"{quarter.name}: the full scorer failed: {done.stdout[-1500:]}")
        return output

    @staticmethod
    def _gain(diff, expiry, symbol, reference):
        """Return ``(gain %, low, high)`` from the owner of the paired two-way interval (A0005)."""
        from dskit.pipeline.libs.cdf_research import ResidualCDFStudy

        scale = 100/reference
        point = scale*float(diff.mean())
        if len(set(expiry)) < 2 or len(set(symbol)) < 2:
            return point, float("nan"), float("nan")
        band = ResidualCDFStudy.paired(diff, expiry, symbol)["ci95"]
        return (point, float("nan"), float("nan")) if band is None else (point, scale*band[0], scale*band[1])

    def _score_frame(self, quarters):
        import pandas as pd
        import pyarrow.parquet as pq
        from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod

        parts = []
        for quarter in quarters:
            ScoreGate.refuse_quarter(quarter)
            forecasters = ("market", "baseline")
            columns = [*KEYS, "settlement_date", "actual_calendar_dte", "band", "y", "market_eligible",
                       *[f"{who}_crps" for who in forecasters],
                       *[f"{who}_cdf_{label}" for who in forecasters for label in self.grid.labels]]
            path = self.full_scorer_path(quarter)/"rows.parquet"
            rows = pq.read_table(path, columns=columns, filters=[("band", "==", "val")]).to_pandas()
            ScoreGate.refuse_sealed(rows["quote_date"])
            ScoreGate.refuse_sealed(rows["settlement_date"])
            rows = rows[rows["market_eligible"]].reset_index(drop=True)
            method = pd.read_parquet(self.method_score_path(quarter))[[*KEYS, "twcrps", "crps"]]
            rows = rows.merge(method, on=KEYS, how="left", validate="one_to_one")
            if rows["crps"].isna().any():
                raise IntegrityError(f"{quarter.name}: scored rows and forecast rows differ")
            y = rows["y"].to_numpy(float)
            for who in forecasters:
                cdf = rows[[f"{who}_cdf_{label}" for label in self.grid.labels]].to_numpy(float)
                rows[f"{who}_twcrps"] = GridCDFMethod.grid_score_rows(cdf, y, self.grid.z, self.grid.weights, self.grid.tail)
            parts.append(rows[[*KEYS, "actual_calendar_dte", "twcrps", "crps", *[f"{who}_{m}" for who in forecasters
                                                                                 for m in ("twcrps", "crps")]]])
        return pd.concat(parts, ignore_index=True)

    def score_table(self, quarters=None):
        """Write twCRPS and full CRPS, with paired gains, by year, regime, DTE and (symbol, DTE) cell."""
        import pandas as pd

        chosen = list(quarters) if quarters is not None else [q for q in self.quarters if not q.sealed]
        for quarter in chosen:
            ScoreGate.refuse_quarter(quarter)
        params = self.full_scorer_document(chosen[0])["pipeline"]["scores"]["params"]
        frame = self._score_frame(chosen)
        frame["year"] = frame["quote_date"].str[:4]
        frame["regime"] = None
        for name, (low, high) in params["sub_periods"].items():
            frame.loc[frame["quote_date"].between(low, high), "regime"] = name
        frame["dte"] = None
        for low, high in params["dte_slices"]:
            frame.loc[frame["actual_calendar_dte"].between(low, high), "dte"] = f"{low}-{high}"
        frame["cell"] = frame["symbol"]+"|"+frame["dte"].astype(str)
        # A0005's window is 2020-2023: a year the HPO tuned on is shown apart and never counts towards the gate.
        tuned = frame["year"].isin(TUNED_YEARS)
        favored, frame = frame[tuned], frame[~tuned]
        records, references = [], ("market", "baseline")

        def add(level, period, rows, interval):
            for who in references:
                record = {"level": level, "period": period, "versus": who, "rows": len(rows)}
                for metric in ("twcrps", "crps"):
                    diff = (rows[f"{who}_{metric}"]-rows[metric]).to_numpy(float)
                    reference = float(rows[f"{who}_{metric}"].mean())
                    record[metric], record[f"versus_{metric}"] = float(rows[metric].mean()), reference
                    if interval:
                        record[f"{metric}_gain_pct"], record[f"{metric}_ci_low"], record[f"{metric}_ci_high"] = self._gain(
                            diff, rows["expiry"].to_numpy(), rows["symbol"].to_numpy(), reference)
                    else:
                        record[f"{metric}_gain_pct"] = 100*diff.mean()/reference
                        record[f"{metric}_ci_low"] = record[f"{metric}_ci_high"] = float("nan")
                records.append(record)

        if len(frame):
            add("all", "all", frame, True)
        for period, rows in favored.groupby("year"):
            add("tuned_year", period, rows, True)
        for level in ("year", "regime", "dte"):
            for period, rows in frame.dropna(subset=[level]).groupby(level):
                add(level, period, rows, True)
        for cell, rows in frame.dropna(subset=["dte"]).groupby("cell"):
            add("cell", cell, rows, False)
        table = pd.DataFrame(records)
        a0005 = {}
        for who in references if len(frame) else ():
            for metric in ("twcrps", "crps"):
                years = table[(table.level == "year") & (table.versus == who)][f"{metric}_gain_pct"]
                whole = table[(table.level == "all") & (table.versus == who)][f"{metric}_gain_pct"].iloc[0]
                a0005[f"{who}_{metric}"] = bool(whole > 0 and (years > 0).sum() >= 3)
        target = self.score_table_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(target), table.to_csv(index=False).encode())
        atomic_write(str(self.score_gate_path()), json.dumps({
            "rows": len(frame), "tuned_rows_excluded": len(favored), "quarters": [q.name for q in chosen],
            "a0005": a0005}, indent=1).encode())
        return target

    # -- the MIO input, through 2023 only ---------------------------------------------------------------------

    def pin(self, onboarding_root, source):
        """Onboard the through-2023 forecast folder and write one MIO binding per quarter."""
        from dskit.onboarding.snapshot import read_manifest, snapshot_hash

        folder = Path(self.config["open_root"])/f"forecasts-{self.recipe.identity[:12]}"
        self.refuse_sealed_path(folder)
        if self.rerun:
            raise IntegrityError("a rerun check gives MIO nothing")
        names = sorted(q.name for q in self.required_quarters() if not q.sealed)
        found = {p.name for p in folder.iterdir() if p.is_dir() and ".pending-" not in p.name} if folder.exists() else set()
        if found != set(names):
            raise IntegrityError(f"pin needs exactly the {len(names)} planned open quarters: "
                                 f"missing {sorted(set(names)-found)}, unplanned {sorted(found-set(names))}")
        for name in names:
            ScoreGate.refuse_quarter(Quarter.parse(name))
            self.forecast_store(Quarter.parse(name)).verify()
        CodeTree(self.config["repo"]).require_clean(self.config)       # before onboarding writes its journal rows
        # Fixed by the published files: a rerun re-registers the same source instead of a second active one.
        stamp = max((folder/name/"complete.json").stat().st_mtime for name in names)
        as_of = dt.datetime.fromtimestamp(int(stamp), dt.timezone.utc).isoformat()
        # Named quarters only: a stale .pending- folder also holds a forecasts.parquet.
        leaves = ("forecasts.parquet", "contexts.parquet", "receipt.json", *([self.FINE_FILE] if self.fine else []))
        config = {"path": str(folder), "as_of": as_of, "include": [f"{name}/{leaf}" for name in names for leaf in leaves]}
        cli = [sys.executable, "-B", "-m", "dskit.onboarding"]
        def run(*args):
            subprocess.run([*cli, *args], check=True, stdout=subprocess.DEVNULL)
        if not (Path(onboarding_root)/"store"/"store.json").exists():      # a rerun of this verb resumes
            run("init", "--root", str(onboarding_root))
        run("register-source", source, "--catalog-source", source, "--connector", "localblobs", "--config",
            json.dumps(config), "--activate", "--root", str(onboarding_root))
        run("acquire", "--source", source, "--stream", "files", "--mode", "backfill", "--root", str(onboarding_root))
        snapshot = sorted(glob.glob(str(Path(onboarding_root)/"raw"/source/"*/")))[-1].rstrip("/")
        manifest = read_manifest(snapshot)
        prefix = "files/"
        files = {e["relpath"][len(prefix):]: e["sha256"] for e in manifest["files"] if e["relpath"].startswith(prefix)}
        pin = {"root": str(onboarding_root), "source": source, "snapshot": Path(snapshot).name, "stream": "files",
               "manifest_sha256": snapshot_hash(manifest), "files": files}
        out = Path(self.config["open_root"])/f"mio-bindings-{self.recipe.identity[:12]}"
        out.mkdir(exist_ok=True)
        fine_out = out.with_name(out.name+"-fine")
        if self.fine:
            fine_out.mkdir(exist_ok=True)
        for name in names:
            forecast, context, receipt = (f"{name}/{leaf}" for leaf in ("forecasts.parquet", "contexts.parquet", "receipt.json"))
            binding = {"forecasts": {"pin": pin, "files": {"shared": forecast}}, "contexts": {"pin": pin, "files": {"shared": context}},
                       "receipt": {"source": pin, "file": receipt, "sha256": files[receipt]}}
            atomic_write(str(out/f"{name}.json"), json.dumps(binding, indent=1).encode())
            if self.fine:
                fine = f"{name}/{self.FINE_FILE}"
                atomic_write(str(fine_out/f"{name}.json"), json.dumps({
                    **binding, "forecasts": {"pin": pin, "files": {"shared": fine}},
                    "grid": {"z_grid": self.fine.z, "grid_labels": self.fine.labels, "forecasts_sha256": files[fine]}},
                    indent=1).encode())
        return out


def plan_rerun_check(path, quarter="2020Q1", recipe=None):
    """Plan one open quarter again beside the run, with its own roots and job id and no score job (runbook).

    ``recipe`` names another recipe file (the runbook's parity check, zoo ``fl21``'s settings): its folder is
    ``parity-check``, so it never meets the rerun check's.
    """
    config = json.loads(Path(path).read_text())
    root = Path(config["plan_root"]).parent/("parity-check" if recipe else "rerun-check")
    if root.exists():
        raise IntegrityError(f"{root} exists: delete the finished check first")
    if recipe:
        config["recipe"] = str(Path(recipe).resolve())
    config.update(quarters=[quarter], rerun_check=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                  **{key: str(root/name) for key, name in (("open_root", "open"), ("sealed_root", "sealed"),
                                                           ("scores_root", "scores"), ("plan_root", "plan"))})
    pipe = Pipeline(config)
    pipe.plan_root.mkdir(parents=True)
    atomic_write(str(pipe.plan_root/"run.json"), json.dumps(config, indent=1).encode())
    pipe.plan()
    return pipe.plan_root/"jobs-open.json"


def _copy_run_file(path, pipe):
    pipe.plan_root.mkdir(parents=True, exist_ok=True)
    atomic_write(str(pipe.plan_root/"run.json"), Path(path).read_bytes())


def _print(payload):
    print(json.dumps(payload, indent=1, sort_keys=True))


def main(argv=None):
    """Run one verb; every report is structure only."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("plan", "table", "check"):
        sub.add_parser(verb).add_argument("run")
    for verb in ("quarter", "score"):
        one = sub.add_parser(verb)
        one.add_argument("run")
        one.add_argument("name")
    rerun = sub.add_parser("rerun-plan")
    rerun.add_argument("run")
    rerun.add_argument("--quarter", default="2020Q1")
    rerun.add_argument("--recipe", help="a recipe file for the parity check; its folder is parity-check")
    pin = sub.add_parser("pin")
    pin.add_argument("run")
    pin.add_argument("--onboarding-root", required=True)
    pin.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    if args.verb == "rerun-plan":
        _print({"jobs": str(plan_rerun_check(args.run, args.quarter, args.recipe))})
        return 0
    pipe = Pipeline.load(args.run)
    if args.verb == "plan":
        _copy_run_file(args.run, pipe)
        documents = pipe.plan()
        _print({"planned": sorted(documents), "recipe_sha256": pipe.recipe.identity})
    elif args.verb == "quarter":
        pipe.run_quarter(Quarter.parse(args.name))
        _print({"quarter": args.name, "published": True})
    elif args.verb == "score":
        quarter = Quarter.parse(args.name)
        ScoreGate.refuse_quarter(quarter)
        pipe.score_quarter(quarter)
        _print({"quarter": args.name, "scored": True})
    elif args.verb == "table":
        _print({"table": str(pipe.score_table())})
    elif args.verb == "check":
        report = pipe.check()
        _print({"missing": report["missing"],
                **{side: {name: {k: v for k, v in item.items() if k != "files"} for name, item in report[side].items()}
                   for side in ("open", "sealed")}})
        if report["missing"] or any(item["failed"] for side in ("open", "sealed") for item in report[side].values()):
            return 3
    else:
        _print({"bindings": str(pipe.pin(args.onboarding_root, args.source))})
    return 0


if __name__ == "__main__":
    sys.exit(main())
