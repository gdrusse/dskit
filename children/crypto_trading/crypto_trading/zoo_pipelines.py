"""The two orchestration manifests: the HPO pipeline and the production pipeline.

Each is ONE ``dskit.pipeline workflow`` manifest whose steps are commands: a ``zoo_lanes`` step writes the lane
manifest the next step needs (from the zoo pick, which is not known when the manifest is generated), a
``workflow-batch`` step runs those lanes, and a closing step reads their results. So the shipped manifests are static
and pinned to a fresh generation, while the lanes they create are written beside the run into its work directory.
``pick`` (and ``frozen`` for production) are unfilled arguments that ``--args`` fills, like the W2 skeleton's finalist.

* HPO (``workflow-hpo.json``): ``search`` (candidate lanes over the warmup folds) -> ``freeze`` (``hpo-frozen.json``)
  -> ``evaluate`` (default vs frozen winner over the remaining folds, ``hpo-evaluation.json``). Reads no W2.
* Production (``workflow-production.json``): ``blocks`` (one lane per retrain block) -> ``train`` (the lanes) ->
  ``index`` (every retrain's model under a stable path, ``models.json``, the forward report).

Every value comes from the recipe's ``hpo.pipeline`` / ``production.pipeline`` sections. Import cost: stdlib only.
"""

__all__ = ["HpoPipeline", "ProductionPipeline"]

_NOTES = "notes"
_BATCH = "batch.json"


class _Pipeline:
    """Shared shape of an orchestration manifest: args, layout, one registry entry per step, ordered steps."""

    #: The recipe section holding this pipeline's settings.
    SECTION = None

    def __init__(self, recipe):
        self.recipe = recipe
        self.section = recipe[self.SECTION]
        self.cfg = self.section["pipeline"]
        self.cli = f"{{python}} -m {self.cfg['cli']}"
        self.batch = "{python} -m dskit.pipeline workflow-batch"

    def _entry(self, command, notes, before=None, after=None):
        """Return one registry entry on the stage template."""
        entry = {"notes": notes, "template": self.cfg["stage_template"], "extends": None, "command": command}
        if before:
            entry["before"] = [f"{self.cli} {before}"]
        if after:
            entry["after"] = [f"{self.cli} {after}"]
        return entry

    def _assemble(self, args, layout, registry, steps):
        """Return the manifest from its four blocks."""
        return {"notes": self.cfg["notes"], "args": {"notes": self.cfg["args_notes"], **args}, "layout": layout,
                "registry": registry, "steps": steps}


class HpoPipeline(_Pipeline):
    """The HPO workflow: search -> freeze -> evaluate.

    Parameters
    ----------
    recipe : dict
        The recipe (``hpo`` section and its ``pipeline`` block).

    Examples
    --------
    Generate the shipped manifest::

        manifest = HpoPipeline(recipe).manifest()
        # -> list(manifest["steps"]) == ["search", "freeze", "evaluate"]
    """

    SECTION = "hpo"

    def manifest(self):
        """Return the HPO workflow manifest."""
        c = self.cfg
        args = {"work_dir": self.section["work_dir"], "recipe": c["recipe"], "pick": c["pick_unfilled"], "parallel": c["parallel"]}
        layout = {
            "search": {"dir": "{W}/search", "lanes": "{W}/search/hpo-lanes.json", "batch": "{W}/search/lanes/" + _BATCH},
            "freeze": {"dir": "{W}", "frozen": "{W}/hpo-frozen.json"},
            "evaluate": {"dir": "{W}/evaluate", "lanes": "{W}/evaluate/frozen-lanes.json", "batch": "{W}/evaluate/lanes/" + _BATCH,
                         "evaluation": "{W}/hpo-evaluation.json"}}
        registry = {
            "search": self._entry(f"{self.batch} {{out.lanes}} --root {{dir}}/lanes --parallel {{in.parallel}}",
                                  "Write the candidate lanes of the picked cell, then run them over the warmup folds.",
                                  before="hpo-manifest --pick {in.pick} --recipe {in.recipe} --out {out.lanes}"),
            "freeze": self._entry(f"{self.cli} hpo-pick --batch {{in.search_batch}} --pick {{in.pick}} --recipe {{in.recipe}} --out {{out.frozen}}",
                                  "Take the lowest warmup mean (the default wins ties) and write its full params."),
            "evaluate": self._entry(f"{self.batch} {{out.lanes}} --root {{dir}}/lanes --parallel {{in.parallel}}",
                                    "Default vs the frozen winner over the folds after the warmup, then the paired comparison.",
                                    before="frozen-manifest --frozen {in.frozen} --pick {in.pick} --recipe {in.recipe} --out {out.lanes}",
                                    after="hpo-evaluate --batch {out.batch} --frozen {in.frozen} --pick {in.pick} --recipe {in.recipe} --out {out.evaluation}")}
        steps = {
            "search": {"notes": "Needs the zoo pick (`--args` fills `pick`).", "registry": "search", "out": ["lanes", "batch"],
                       "in": {"W": "$args.work_dir", "pick": "$args.pick", "recipe": "$args.recipe", "parallel": "$args.parallel"}},
            "freeze": {"notes": "Refuses unless every search lane is ok.", "registry": "freeze", "out": ["frozen"],
                       "in": {"W": "$args.work_dir", "pick": "$args.pick", "recipe": "$args.recipe", "search_batch": "$search.out.batch"}},
            "evaluate": {"notes": "No search node: both cells are fixed, the folds are the schedule's remaining ones.", "registry": "evaluate",
                         "out": ["lanes", "batch", "evaluation"],
                         "in": {"W": "$args.work_dir", "pick": "$args.pick", "recipe": "$args.recipe", "parallel": "$args.parallel",
                                "frozen": "$freeze.out.frozen"}}}
        return self._assemble(args, layout, registry, steps)


class ProductionPipeline(_Pipeline):
    """The production workflow: blocks -> train -> index.

    Parameters
    ----------
    recipe : dict
        The recipe (``production`` section and its ``pipeline`` block).

    Examples
    --------
    Generate the shipped manifest::

        manifest = ProductionPipeline(recipe).manifest()
        # -> manifest["args"]["retrain_days"] == 60
    """

    SECTION = "production"

    def manifest(self):
        """Return the production workflow manifest."""
        c, p = self.cfg, self.section
        args = {"work_dir": p["work_dir"], "recipe": c["recipe"], "pick": c["pick_unfilled"], "frozen": c["frozen_unfilled"],
                "forward_start": p["forward_start"], "evaluate_through": p["evaluate_through"], "retrain_days": p["retrain_days"],
                "allow_past_w2": p["allow_past_w2"], "panel_source": c["panel_source"], "panel_relpath": c["panel_relpath"],
                "parallel": c["parallel"]}
        layout = {
            "blocks": {"dir": "{W}/blocks", "lanes": "{W}/blocks/production-lanes.json"},
            "train": {"dir": "{W}/train", "batch": "{W}/train/lanes/" + _BATCH},
            "index": {"dir": "{W}", "models": "{W}/" + p["files"]["index"], "report": "{W}/" + p["files"]["report"]}}
        registry = {
            "blocks": self._entry(f"{self.cli} production-manifest --pick {{in.pick}} --frozen {{in.frozen}} --recipe {{in.recipe}} "
                                  "--forward-start {in.forward_start} --evaluate-through {in.evaluate_through} "
                                  "--retrain-days {in.retrain_days} --allow-past-w2 {in.allow_past_w2} --panel-source={in.panel_source} "
                                  "--panel-relpath={in.panel_relpath} --out {out.lanes}",
                                  "Write one lane per retrain block (refuses a window past W2 unless allow_past_w2)."),
            "train": self._entry(f"{self.batch} {{in.lanes}} --root {{dir}}/lanes --parallel {{in.parallel}}",
                                 "Train and score every block; each lane is an independent expanding-window retrain."),
            "index": self._entry(f"{self.cli} production-index --batch {{in.batch}} --lanes {{in.lanes}} --pick {{in.pick}} "
                                 "--frozen {in.frozen} --recipe {in.recipe} --out-dir {dir}",
                                 "Save every retrain's model under a stable path, index them and write the forward report.")}
        steps = {
            "blocks": {"notes": "Needs the zoo pick and the HPO freeze (`--args` fills `pick` and `frozen`).", "registry": "blocks", "out": ["lanes"],
                       "in": {"W": "$args.work_dir", "pick": "$args.pick", "frozen": "$args.frozen", "recipe": "$args.recipe",
                              "forward_start": "$args.forward_start", "evaluate_through": "$args.evaluate_through",
                              "retrain_days": "$args.retrain_days", "allow_past_w2": "$args.allow_past_w2",
                              "panel_source": "$args.panel_source", "panel_relpath": "$args.panel_relpath"}},
            "train": {"notes": "Runs the generated lanes in parallel.", "registry": "train", "out": ["batch"],
                      "in": {"W": "$args.work_dir", "parallel": "$args.parallel", "lanes": "$blocks.out.lanes"}},
            "index": {"notes": "Refuses unless every lane is ok.", "registry": "index", "out": ["models", "report"],
                      "in": {"W": "$args.work_dir", "pick": "$args.pick", "frozen": "$args.frozen", "recipe": "$args.recipe", "batch": "$train.out.batch",
                             "lanes": "$blocks.out.lanes"}}}
        return self._assemble(args, layout, registry, steps)
