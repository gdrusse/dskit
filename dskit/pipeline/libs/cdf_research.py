"""Compose existing CDF owners into bounded, explicit research stages.

Readers verify immutable sources and exclude sealed dates before decoding. These
nodes additionally refuse malformed identities and chronology; they cannot undo
an unsafe upstream read. Planning and descriptive slicing never launch training
or qualify a strategy. Real fit authorization belongs to a separately registered
inventory and an exclusive claim, not a successful pipeline validation.
"""

import copy
import datetime
import itertools
import math
from numbers import Real
from pathlib import Path

from dskit.pipeline.base import value_hash
from dskit.pipeline.node import Node, reject_unknown_params

__all__ = [
    "OptionPriceProxy",
    "CDFExperimentGrid",
    "ConditionalScorePockets",
    "CDFReferenceScores",
    "PairedForecastScores",
]


def _positive(value):
    """Accept finite positive real numbers excluding booleans."""
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _date(value):
    """Require a canonical date, avoiding lexicographic timestamp ambiguity."""
    if (
        not isinstance(value, str)
        or datetime.date.fromisoformat(value).isoformat() != value
    ):
        raise ValueError("expected canonical ISO date")
    return value


def _json(value):
    """Encode finite canonical JSON through the existing experiment owner."""
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment

    return CDFExperiment.json_bytes(value)


def _frame(records, identity):
    """Require nonempty, unique, complete forecast identities."""
    import pandas as pd
    from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError

    frame = pd.DataFrame(list(records))
    try:
        CDFExperiment.check_pairing(frame, frame, identity)
    except IntegrityError as error:
        raise ValueError(str(error)) from error
    if frame.empty:
        raise ValueError("empty forecast cohort")
    return frame


class _ResearchNode(Node):
    role = "transform"
    outputs = ("records", "summary")

    @classmethod
    def validate_params(cls, params):
        """Validate explicit schema and common temporal declarations.

        Parameters
        ----------
        params : dict
            Complete node configuration.

        Returns
        -------
        list
            Configuration problems, empty when valid.
        """
        errors = []
        reject_unknown_params(errors, params, cls._PARAMS)
        errors.extend("missing " + key for key in cls._PARAMS if key not in params)
        for key in ("cutoff", "end_before"):
            if key in params:
                try:
                    _date(params[key])
                except (TypeError, ValueError):
                    errors.append(key + " must be an ISO date")
        if "cutoff" in params and "end_before" in params and not errors:
            if params["cutoff"] >= params["end_before"]:
                errors.append("cutoff must precede end_before")
        return errors

    def _cohorts(self, inputs):
        """Validate settled fit rows before cutoff and score rows after it."""
        p = self.params
        fit, score = [_frame(inputs[name], p["identity"]) for name in ("fit", "score")]
        for frame in (fit, score):
            for key in (p["date"], p["end"]):
                frame[key].map(_date)
                if not (frame[key] < p["end_before"]).all():
                    raise ValueError("record reaches end_before")
            if not (frame[p["date"]] < frame[p["end"]]).all():
                raise ValueError("outcome must follow forecast origin")
        if (
            not (fit[p["end"]] < p["cutoff"]).all()
            or not (score[p["date"]] >= p["cutoff"]).all()
        ):
            raise ValueError("fit/score chronology violation")
        return fit, score


class OptionPriceProxy(_ResearchNode):
    """Prepare exact-request option proxies from price quotes alone.

    Parameters
    ----------
    params : dict
        Identity/clock/spot/scale/growth mappings, immutable source SHA256,
        end_before and existing OptionPriceCDF parameters. Clocks are canonical
        dates; callers must conservatively advance intraday availability to the
        first decision date on which it is usable.

    Examples
    --------
    Construct from a validated pipeline declaration::

        node = OptionPriceProxy("proxy", document["pipeline"]["proxy"]["params"])
    """

    _PARAMS = (
        "identity",
        "date",
        "end",
        "end_before",
        "spot",
        "scale",
        "growth",
        "available",
        "decision",
        "source_sha256",
        "parameters",
    )

    @classmethod
    def validate_params(cls, params):
        """Validate mappings and the existing proxy parameter vocabulary.

        Parameters
        ----------
        params : dict
            Node configuration.

        Returns
        -------
        list
            Configuration problems.
        """
        errors = super().validate_params(params)
        if not errors:
            from dskit.pipeline.libs.predictive_cdf import OptionPriceCDF

            try:
                OptionPriceCDF(**params["parameters"])
                if (
                    not params["identity"]
                    or len(set(params["identity"])) != len(params["identity"])
                    or params["date"] not in params["identity"]
                ):
                    raise ValueError("identity must include forecast date")
                pin = params["source_sha256"]
                if (
                    not isinstance(pin, str)
                    or len(pin) != 64
                    or any(c not in "0123456789abcdef" for c in pin)
                ):
                    raise ValueError("invalid source SHA256")
            except (TypeError, ValueError) as error:
                errors.append(str(error))
        return errors

    def run(self, ctx, inputs):
        """Return one proxy or refusal per request without fabricating fields.

        Parameters
        ----------
        ctx : object
            Pipeline context, unused.
        inputs : dict
            requests and quotes, already read through bounded pinned readers.

        Returns
        -------
        dict
            Records with eligibility, normalized quantiles and source identity.
        """
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.predictive_cdf import OptionPriceCDF

        p = self.params
        if p["source_sha256"] == "0" * 64:
            raise ValueError("unbound source pin")
        requests = _frame(inputs["requests"], p["identity"])
        quotes = pd.DataFrame(list(inputs["quotes"]))
        required = [*p["identity"], "strike", "type", "bid", "ask", p["available"]]
        if set(required) - set(quotes):
            raise ValueError("missing price quote columns")
        if quotes[required].isna().any().any():
            raise ValueError("missing quote identity or prices")
        if quotes.duplicated([*p["identity"], "strike", "type"]).any():
            raise ValueError("duplicate contract quotes")
        quotes[p["date"]].map(_date)
        quotes[p["available"]].map(_date)
        if not (quotes[p["date"]] < p["end_before"]).all():
            raise ValueError("quote reaches end_before")
        requested = set(requests[p["identity"]].itertuples(index=False, name=None))
        quoted = set(quotes[p["identity"]].itertuples(index=False, name=None))
        if quoted - requested:
            raise ValueError("unrequested quote identity")
        owner = OptionPriceCDF(**p["parameters"])
        grouped = {
            key if isinstance(key, tuple) else (key,): group
            for key, group in quotes.groupby(p["identity"], sort=False)
        }
        records = []
        for req in requests.to_dict("records"):
            for name in (p["date"], p["end"], p["decision"]):
                _date(req[name])
            if (
                not req[p["date"]]
                <= req[p["decision"]]
                < req[p["end"]]
                < p["end_before"]
            ):
                raise ValueError("request chronology violation")
            if not all(
                _positive(req[name]) for name in (p["spot"], p["scale"], p["growth"])
            ):
                raise ValueError("invalid spot, reference scale or growth")
            key = tuple(req[name] for name in p["identity"])
            group = grouped.get(key)
            result = {"eligible": False, "reasons": ["missing_quote_group"]}
            if group is not None:
                if not (
                    (group[p["available"]] >= req[p["date"]])
                    & (group[p["available"]] <= req[p["decision"]])
                ).all():
                    raise ValueError("future quote availability")
                numeric = group[["strike", "bid", "ask"]].to_numpy(dtype=float)
                valid = (
                    np.isfinite(numeric).all()
                    and (numeric > 0).all()
                    and (group.ask >= group.bid).all()
                    and group.type.isin(["call", "put"]).all()
                )
                if valid:
                    priced = group[["strike", "type"]].copy()
                    priced["mark"] = group.bid / 2 + group.ask / 2
                    result = owner.estimate(
                        priced, req[p["spot"]], growth=req[p["growth"]]
                    )
                else:
                    result = {"eligible": False, "reasons": ["invalid_quotes"]}
            row = {
                **req,
                "market_eligible": int(result["eligible"]),
                "market_reasons": result["reasons"],
                "source_sha256": p["source_sha256"],
            }
            for i in range(len(p["parameters"]["probabilities"])):
                row["proxy_q_" + str(i)] = (
                    result["quantiles"][i] / req[p["scale"]]
                    if result["eligible"]
                    else None
                )
            row["forward_ratio"] = result.get("forward_ratio")
            records.append(row)
        return {
            "records": records,
            "summary": {
                "requests": len(records),
                "eligible": sum(row["market_eligible"] for row in records),
                "source_sha256": p["source_sha256"],
                "end_before": p["end_before"],
            },
        }


class CDFExperimentGrid(_ResearchNode):
    """Publish a finite immutable grid of existing experiment documents.

    Parameters
    ----------
    params : dict
        base_config, training space, seeds, declared fold group counts,
        max_recipes, isolated output and end_before. Counts are planning
        estimates requiring actual source/admission preflight before launch.

    Examples
    --------
    Plan only; no estimator or worker is instantiated::

        node = CDFExperimentGrid("grid", document["pipeline"]["grid"]["params"])
    """

    outputs = ("documents", "summary")
    _PARAMS = (
        "base_config",
        "space",
        "seeds",
        "folds",
        "max_recipes",
        "output",
        "end_before",
    )

    def run(self, ctx, inputs):
        """Expand and atomically publish plans with zero execution authority.

        Parameters
        ----------
        ctx : object
            Pipeline context, unused.
        inputs : dict
            No data inputs are consumed.

        Returns
        -------
        dict
            Candidate documents and finite planning counts.
        """
        from dskit.pipeline.kinds_search import CandidateInventory
        from dskit.pipeline.libs.cdf_experiment import AtomicFitStore

        p = self.params
        base = copy.deepcopy(p["base_config"])
        space = p["space"]
        if not space or set(space) - set(base["training"]):
            raise ValueError("search may override declared training parameters only")
        if (
            not p["seeds"]
            or len(set(p["seeds"])) != len(p["seeds"])
            or any(type(seed) is not int for seed in p["seeds"])
        ):
            raise ValueError("invalid fixed seed inventory")
        if (
            not p["folds"]
            or len({f["fold"] for f in p["folds"]}) != len(p["folds"])
            or any(
                set(f) != {"fold", "groups"}
                or type(f["groups"]) is not int
                or f["groups"] < 1
                for f in p["folds"]
            )
        ):
            raise ValueError("invalid declared fold inventory")
        if not base["arms"] or not base["candidates"]:
            raise ValueError("empty base experiment inventory")
        inventory = CandidateInventory(space, max_candidates=p["max_recipes"])
        # CandidateInventory owns the Cartesian product and budget semantics.
        documents = {}
        for combination in inventory.combinations:
            overrides = dict(combination)
            recipe = value_hash(overrides)[:16]
            for seed in p["seeds"]:
                name = recipe + "-seed" + str(seed)
                if name in documents:
                    raise ValueError("duplicate recipe identity")
                doc = copy.deepcopy(base)
                doc["training"].update(overrides, seeds=[seed])
                doc["output"] = str(
                    Path(p["output"]).with_name(Path(p["output"]).name + "-runs") / name
                )
                doc["date_upper_exclusive"] = p["end_before"]
                doc["end_upper_exclusive"] = p["end_before"]
                documents[name] = doc
        multiplicity = len(documents) * len(base["arms"]) * len(base["candidates"])
        summary = {
            "recipes": len(inventory.combinations),
            "documents": len(documents),
            "neural_fit_slots": multiplicity * len(p["folds"]),
            "empirical_fit_slots": multiplicity * sum(f["groups"] for f in p["folds"]),
            "authorized_fit_attempts": 0,
            "end_before": p["end_before"],
            "status": "plan_only_requires_source_and_budget_registration",
            "declared_folds": p["folds"],
        }
        files = {
            "documents/" + name + ".json": _json(doc) for name, doc in documents.items()
        }
        files["plan.json"] = _json(summary)
        AtomicFitStore(
            p["output"], {"params": p, "implementation": type(self).__name__}
        ).publish(files)
        return {"documents": documents, "summary": summary}


class ConditionalScorePockets(_ResearchNode):
    """Summarize fixed quantile pockets from earlier chronological forecasts.

    Parameters
    ----------
    params : dict
        Identity/date/end/trained_through columns, cutoff, end_before, numeric
        feature names, quantiles, max_order, paired loss columns and descriptive
        support requirements. No result is a promotion or significance flag.

    Examples
    --------
    Keep the complete pocket family, including empty intersections::

        node = ConditionalScorePockets("pockets", document["pipeline"]["pockets"]["params"])
    """

    _PARAMS = (
        "identity",
        "date",
        "end",
        "trained_through",
        "cutoff",
        "end_before",
        "features",
        "quantiles",
        "max_order",
        "losses",
        "model",
        "min_rows",
        "min_dates",
        "eligible",
    )

    def run(self, ctx, inputs):
        """Freeze cutoffs on prior rows and summarize every paired later slice.

        Parameters
        ----------
        ctx : object
            Pipeline context, unused.
        inputs : dict
            fit and score record sets containing earlier-issued predictions.

        Returns
        -------
        dict
            All pocket summaries, identities and frozen cutoffs.
        """
        import numpy as np

        p = self.params
        fit, score = self._cohorts(inputs)
        original_counts = {"fit": len(fit), "score": len(score)}
        for frame in (fit, score):
            if not frame[p["eligible"]].isin([0, 1]).all():
                raise ValueError("invalid shared market eligibility")
        fit, score = [
            frame.loc[frame[p["eligible"]] == 1].copy() for frame in (fit, score)
        ]
        if fit.empty:
            raise ValueError("no eligible earlier market forecasts for thresholds")
        names, qs = p["features"], p["quantiles"]
        if (
            not names
            or len(set(names)) != len(names)
            or not qs
            or any(not _positive(q) or q >= 1 for q in qs)
            or any(a >= b for a, b in zip(qs, qs[1:]))
            or type(p["max_order"]) is not int
            or not 1 <= p["max_order"] <= len(names)
            or p["model"] not in p["losses"]
            or any(type(p[k]) is not int or p[k] < 1 for k in ("min_rows", "min_dates"))
        ):
            raise ValueError("invalid pocket family or support contract")
        for frame in (fit, score):
            frame[p["trained_through"]].map(_date)
            if not (frame[p["trained_through"]] < frame[p["date"]]).all():
                raise ValueError("in-sample forecast cannot define or score pockets")
            if not np.isfinite(frame[names].to_numpy(dtype=float)).all():
                raise ValueError("nonfinite pocket covariate")
        loss_columns = list(p["losses"].values())
        if len(set(loss_columns)) != len(loss_columns):
            raise ValueError("loss labels alias the same column")
        losses = score[loss_columns].to_numpy(dtype=float)
        if not np.isfinite(losses).all() or (losses < 0).any():
            raise ValueError("nonfinite or negative paired losses")
        thresholds = {
            name: np.quantile(fit[name].to_numpy(float), qs).tolist() for name in names
        }
        bins = {
            name: np.searchsorted(
                thresholds[name], score[name].to_numpy(float), side="right"
            )
            for name in names
        }
        pockets = [()]
        for order in range(1, p["max_order"] + 1):
            for fields in itertools.combinations(names, order):
                pockets.extend(
                    tuple(zip(fields, cells))
                    for cells in itertools.product(range(len(qs) + 1), repeat=order)
                )
        records = []
        for pocket in pockets:
            mask = np.ones(len(score), dtype=bool)
            for name, cell in pocket:
                mask &= bins[name] == cell
            rows = score.loc[mask]
            means = {
                name: float(rows[column].mean()) if len(rows) else None
                for name, column in p["losses"].items()
            }
            skill = {
                name: (
                    100 * (1 - means[p["model"]] / value)
                    if value is not None and value > 0
                    else None
                )
                for name, value in means.items()
                if name != p["model"]
            }
            dates = rows[p["date"]].nunique()
            records.append(
                {
                    "pocket": dict(pocket),
                    "rows": len(rows),
                    "dates": int(dates),
                    "mean_loss": means,
                    "improvement_pct": skill,
                    "identity_sha256": value_hash(
                        rows[p["identity"]].to_dict("records")
                    ),
                    "status": (
                        "descriptive"
                        if len(rows) >= p["min_rows"] and dates >= p["min_dates"]
                        else "insufficient_support"
                    ),
                }
            )
        return {
            "records": records,
            "summary": {
                "thresholds": thresholds,
                "fit_rows": len(fit),
                "score_rows": len(score),
                "excluded_nonmarket": {
                    "fit": original_counts["fit"] - len(fit),
                    "score": original_counts["score"] - len(score),
                },
                "pockets": len(records),
                "cutoff": p["cutoff"],
                "end_before": p["end_before"],
                "cutoff_identity": value_hash(
                    {"params": p, "fit": fit.to_dict("records")}
                ),
                "qualification": "descriptive_only_no_selection_or_significance",
            },
        }


class CDFReferenceScores(_ResearchNode):
    """Score paired raw/transported option proxies with explicit fit accounting.

    Parameters
    ----------
    params : dict
        Feature mappings, settled fit/score cutoff, existing estimator/score
        parameters, named transport knot counts, exact authorized_fit_attempts
        and exclusive claim path. Each estimator fit and its empirical fit count
        separately; nonzero transport additionally counts one PIT-map fit.

    Examples
    --------
    A zero-budget template refuses before consuming records::

        node = CDFReferenceScores("refs", document["pipeline"]["refs"]["params"])
    """

    role = "train"
    _PARAMS = (
        "identity",
        "group",
        "date",
        "end",
        "cutoff",
        "end_before",
        "features",
        "target",
        "reference",
        "eligible",
        "decision_strike",
        "parameters",
        "transports",
        "samples",
        "tail_intervals",
        "tail_points",
        "tail_weight",
        "authorized_fit_attempts",
        "claim",
    )

    def run(self, ctx, inputs):
        """Precharge immutable fit inventory before fitting paired references.

        Parameters
        ----------
        ctx : object
            Pipeline context, unused.
        inputs : dict
            fit and score rows from pinned bounded sources.

        Returns
        -------
        dict
            All paired reference scores, active/fallback masks and fit counts.
        """
        import numpy as np

        p = self.params
        if (
            type(p["authorized_fit_attempts"]) is not int
            or p["authorized_fit_attempts"] <= 0
        ):
            raise ValueError("reference fit budget is zero or invalid")
        features = p["features"]
        indices = p["parameters"]
        if (
            not features
            or len(set(features)) != len(features)
            or features[indices["eligible_index"]] != p["eligible"]
            or features[indices["reference_index"]] != p["reference"]
        ):
            raise ValueError("reference feature/eligibility/scale mapping mismatch")
        if (
            not _positive(p["tail_weight"])
            or type(p["samples"]) is not int
            or p["samples"] < 2
            or type(p["tail_points"]) is not int
            or p["tail_points"] < 2
        ):
            raise ValueError("invalid reference scoring parameters")
        fit, score = self._cohorts(inputs)
        groups = sorted(score[p["group"]].unique())
        if set(groups) - set(fit[p["group"]]):
            raise ValueError("scored group lacks fitting history")
        if not p["transports"] or any(
            type(k) is not int or (k != 0 and k < 3) for k in p["transports"].values()
        ):
            raise ValueError("invalid market transport inventory")
        inventory = [
            {"group": group, "reference": name, "operation": operation}
            for group in groups
            for name, knots in sorted(p["transports"].items())
            for operation in (
                ["estimator", "empirical_tail"] + (["pit_map"] if knots else [])
            )
        ]
        if len(inventory) != p["authorized_fit_attempts"]:
            raise ValueError("reference fit budget does not equal inventory")
        from dskit.pipeline.libs.predictive_cdf import OptionImpliedTransportCDF

        for frame in (fit, score):
            if not frame[p["eligible"]].isin([0, 1]).all():
                raise ValueError("invalid proxy eligibility")
            numeric = frame[[p["target"], p["reference"]]].to_numpy(dtype=float)
            if not np.isfinite(numeric).all() or not (numeric[:, 1] > 0).all():
                raise ValueError("invalid target/reference scale")
        # Constructor validation has no fit effects. Canonical inventory is fixed
        # before claim publication, and the exclusive claim consumes failures.
        models = {
            name: OptionImpliedTransportCDF(**p["parameters"], transport_knots=knots)
            for name, knots in sorted(p["transports"].items())
        }
        claim = Path(p["claim"])
        claim.parent.mkdir(parents=True, exist_ok=True)
        import os

        payload = _json(
            {
                "params": p,
                "inventory": inventory,
                "fit_identity": value_hash(
                    fit.astype(object).where(fit.notna(), None).to_dict("records")
                ),
                "score_identity": value_hash(
                    score.astype(object).where(score.notna(), None).to_dict("records")
                ),
            }
        )
        stream = claim.open("xb")
        receipt = {
            "status": "failed",
            "charged_fit_attempts": len(inventory),
            "end_before": p["end_before"],
        }
        try:
            with stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_directory(claim.parent)
            result = self._score_references(fit, score, groups, models, len(inventory))
            _json(result)  # Never publish success with nonfinite output.
            receipt["status"] = "complete"
            receipt["result_sha256"] = value_hash(result)
        except BaseException as error:
            receipt["error"] = type(error).__name__ + ": " + str(error)
            try:
                self._terminal(claim, receipt)
            except OSError:
                pass  # Exclusive claim still consumes attempts if storage fails.
            raise
        self._terminal(claim, receipt)
        return result

    @staticmethod
    def _sync_directory(path):
        """Persist a published directory entry before any fit starts."""
        import os

        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _terminal(cls, claim, receipt):
        """Publish a non-overwritable terminal observation when storage works."""
        import os

        with Path(str(claim) + ".terminal.json").open("xb") as stream:
            stream.write(_json(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        cls._sync_directory(claim.parent)

    def _score_references(self, fit, score, groups, models, charged):
        """Fit existing owners and preserve shared active/fallback identities."""
        import numpy as np
        from dskit.pipeline.libs.predictive_cdf import (
            OptionImpliedTransportCDF,
            ChronologicalCDFStudy,
        )

        p = self.params
        records = []
        for group in groups:
            f, s = [frame.loc[frame[p["group"]] == group] for frame in (fit, score)]
            arrays = [frame[p["features"]].to_numpy(dtype=float) for frame in (f, s)]
            outcomes = [
                (frame[p["target"]] / frame[p["reference"]]).to_numpy()
                for frame in (f, s)
            ]
            rows = s[
                list(
                    dict.fromkeys(
                        [
                            *p["identity"],
                            p["date"],
                            p["end"],
                            p["reference"],
                            p["decision_strike"],
                        ]
                    )
                )
            ].to_dict("records")
            for row, active in zip(rows, s[p["eligible"]]):
                row["market_eligible"] = int(active)
            for name in models:
                estimator = OptionImpliedTransportCDF(
                    **p["parameters"], transport_knots=p["transports"][name]
                )
                estimator.fit(arrays[0], outcomes[0], None, None)
                curve = estimator.curve(arrays[1])
                scores, _ = ChronologicalCDFStudy.scores(
                    curve,
                    outcomes[1],
                    p["samples"],
                    p["tail_intervals"],
                    p["tail_points"],
                )
                mean, sd = curve.moments()
                strike = s[p["decision_strike"]].to_numpy(dtype=float)
                if not np.isfinite(strike).all():
                    raise ValueError("nonfinite standardized decision strike")
                tail = curve.cdf(strike[:, None])[:, 0]
                for i, row in enumerate(rows):
                    row[name + "_mean_z"] = float(mean[i])
                    row[name + "_sd_z"] = float(sd[i])
                    row[name + "_lower_probability"] = float(tail[i])
                    row[name + "_crps"] = float(scores["crps"][i])
                    row[name + "_tail_crps"] = float(scores["tail_crps"][i])
                    row[name + "_weighted_crps"] = float(
                        scores["crps"][i] + p["tail_weight"] * scores["tail_crps"][i]
                    )
            records.extend(rows)
        return {
            "records": records,
            "summary": {
                "fit_attempts": charged,
                "market_rows": int(score[p["eligible"]].sum()),
                "fallback_rows": int((score[p["eligible"]] == 0).sum()),
                "end_before": p["end_before"],
                "claim": p["claim"],
            },
        }


class PairedForecastScores(_ResearchNode):
    """Align complete saved forecast cohorts before computing market gaps.

    Parameters
    ----------
    params : dict
        identity, end_before, components, market reference prefix and context
        column mappings. The decision strike is a standardized log-return.

    Examples
    --------
    Join exact cohorts, refusing missing or duplicate identities::

        node = PairedForecastScores("paired", document["pipeline"]["paired"]["params"])
    """

    _PARAMS = (
        "identity",
        "date",
        "end",
        "end_before",
        "components",
        "market",
        "decision_strike",
        "iv",
        "spread",
        "trained_through",
        "reference",
    )

    def run(self, ctx, inputs):
        """Compare only exactly paired saved outputs in the same return units.

        Parameters
        ----------
        ctx : object
            Pipeline context, unused.
        inputs : dict
            model, control, market and context record sets.

        Returns
        -------
        dict
            Wide score records with forecast/market gaps and eligibility masks.
        """
        import numpy as np
        from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError
        from dskit.pipeline.libs.predictive_cdf import MixtureCurve

        p = self.params
        if type(p["components"]) is not int or p["components"] < 1:
            raise ValueError("invalid mixture component count")
        frames = {
            name: _frame(inputs[name], p["identity"])
            .sort_values(p["identity"])
            .reset_index(drop=True)
            for name in ("model", "control", "market", "context")
        }
        model, control, market, context = [
            frames[name] for name in ("model", "control", "market", "context")
        ]
        for frame in frames.values():
            try:
                CDFExperiment.check_pairing(model, frame, [*p["identity"], p["end"]])
            except IntegrityError as error:
                raise ValueError(str(error)) from error
            for field in (p["date"], p["end"]):
                frame[field].map(_date)
                if not (frame[field] < p["end_before"]).all():
                    raise ValueError("paired record reaches end_before")
        for field in (p["reference"], p["decision_strike"]):
            if not np.array_equal(market[field].to_numpy(), context[field].to_numpy()):
                raise ValueError("market/context scale or strike mismatch")
        if not all(_positive(value) for value in context[p["reference"]]):
            raise ValueError("invalid comparison scale")
        arrays = [
            model[[name + str(i) for i in range(p["components"])]].to_numpy(float)
            for name in ("weights", "means", "scales")
        ]
        curve = MixtureCurve(*arrays)
        _, sd = curve.moments()
        strike = context[p["decision_strike"]].to_numpy(float)
        market_sd = market[p["market"] + "_sd_z"].to_numpy(float)
        market_tail = market[p["market"] + "_lower_probability"].to_numpy(float)
        if (
            not np.isfinite(strike).all()
            or not all(_positive(v) for v in market_sd)
            or not np.isfinite(market_tail).all()
            or ((market_tail < 0) | (market_tail > 1)).any()
        ):
            raise ValueError("invalid market distribution moments or probabilities")
        result = context.copy()
        result["ratio"] = sd / market_sd
        result["tail_gap"] = curve.cdf(strike[:, None])[:, 0] - market_tail
        result["iv"] = context[p["iv"]]
        result["spread"] = context[p["spread"]]
        result["model_weighted_crps"] = model["model_weighted_crps"]
        result["baseline_weighted_crps"] = model["baseline_weighted_crps"]
        if not np.array_equal(
            model["baseline_weighted_crps"], control["baseline_weighted_crps"]
        ):
            raise ValueError("neural/control historical references differ")
        result["price_only_weighted_crps"] = control["model_weighted_crps"]
        for field in market:
            if field.endswith("_weighted_crps") or field == "market_eligible":
                result[field] = market[field]
        if not result["market_eligible"].isin([0, 1]).all():
            raise ValueError("invalid common market mask")
        return {
            "records": result.to_dict("records"),
            "summary": {
                "rows": len(result),
                "market_eligible": int(result.market_eligible.sum()),
                "end_before": p["end_before"],
                "pairing": "exact_no_dropped_identities",
            },
        }
