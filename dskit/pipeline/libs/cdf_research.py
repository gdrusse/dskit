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
    "ResidualCDFStudy",
    "SettledErrorFeatures",
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
        for name, frame in frames.items():
            required = {
                p[key]
                for key in (
                    "date",
                    "end",
                    "reference",
                    "decision_strike",
                    "trained_through",
                )
            }
            if required - set(frame):
                raise ValueError(name + " lacks required comparison metadata")
        for name, frame in frames.items():
            try:
                CDFExperiment.check_pairing(model, frame, [*p["identity"], p["end"]])
            except IntegrityError as error:
                raise ValueError(str(error)) from error
            for field in (p["date"], p["end"], p["trained_through"]):
                frame[field].map(_date)
                if not (frame[field] < p["end_before"]).all():
                    raise ValueError("paired record reaches end_before")
            if not (
                (frame[p["trained_through"]] < frame[p["date"]])
                & (frame[p["date"]] < frame[p["end"]])
            ).all():
                raise ValueError(
                    "paired training/origin/settlement chronology violation"
                )
            if not all(_positive(value) for value in frame[p["reference"]]):
                raise ValueError("invalid comparison scale")
            if not np.isfinite(frame[p["decision_strike"]].to_numpy(float)).all():
                raise ValueError("invalid comparison strike")
            for field in (p["reference"], p["decision_strike"]):
                if not np.array_equal(
                    frame[field].to_numpy(), context[field].to_numpy()
                ):
                    raise ValueError(name + " comparison scale or strike mismatch")
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
        result[p["trained_through"]] = [
            max(values)
            for values in zip(
                *(frame[p["trained_through"]] for frame in frames.values())
            )
        ]
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


class ResidualCDFStudy:
    """Select fixed-baseline residual heads on earlier blocks, then freeze.

    Parameters
    ----------
    params : dict
        Explicit identity/clock/column mappings, ordered features and families,
        penalties, chronological folds, immutable provenance pins and output.
        Input frames must come from bounded, pinned onboarding readers. This
        owner never acquires data or treats settlement as proven availability.

    Examples
    --------
    Separate learning and outcome loading::

        study = ResidualCDFStudy(params)
        study.freeze(history, forecasts_without_outcomes)
        report = study.evaluate(forecasts_without_outcomes, load_paired_outcomes)
    """

    _PARAMS = ("identity", "date", "end", "baseline_asof", "outcome", "baseline",
               "features", "families", "alphas", "z_grid", "score_weights",
               "tail_width", "end_before", "output", "folds", "provenance",
               "batch_rows", "clusters", "comparators", "groups", "periods", "pit_series",
               "family_order", "continuation", "feature_specs")
    _DEFAULTS = {"batch_rows": 4096, "clusters": None, "comparators": {},
                 "groups": [], "periods": {}, "pit_series": [],
                 "family_order": None, "continuation": None}

    def __init__(self, params):
        from dskit.pipeline.libs.cdf_grid_methods import ResidualRidgeCDF

        self.params = copy.deepcopy({**self._DEFAULTS, **params})
        p = self.params
        if (set(p) - set(self._PARAMS)
                or (set(self._PARAMS) - {"feature_specs"}) - set(p)):
            raise ValueError("unknown or missing residual study parameters")
        self.estimator = ResidualRidgeCDF(p["z_grid"], p["score_weights"], None)
        for key in ("identity", "baseline", "features"):
            values = p[key]
            if (not isinstance(values, list) or len(set(values)) != len(values)
                    or any(not isinstance(v, str) or not v for v in values)):
                raise ValueError("invalid ordered column inventory: " + key)
        if (not p["identity"] or p["date"] not in p["identity"]
                or len(p["baseline"]) != len(p["z_grid"])
                or set(p["baseline"]) & set(p["features"])
                or p["outcome"] in set(p["baseline"] + p["features"] + p["identity"])):
            raise ValueError("invalid baseline, outcome or identity mappings")
        if (not p["families"] or any(not isinstance(k, str) or not k for k in p["families"])
                or any(not isinstance(v, list) or not v for v in p["families"].values())):
            raise ValueError("nonempty named feature families required")
        members = [f for group in p["families"].values() for f in group]
        if len(set(members)) != len(members) or set(members) != set(p["features"]):
            raise ValueError("families must partition the feature inventory")
        if p["family_order"] is None:
            p["family_order"] = list(p["families"])
        if (not isinstance(p["family_order"], list)
                or len(p["family_order"]) != len(p["families"])
                or set(p["family_order"]) != set(p["families"])):
            raise ValueError("family_order must list every family exactly once")
        if (not isinstance(p["alphas"], list) or not p["alphas"]
                or not all(_positive(v) for v in p["alphas"])
                or len(set(p["alphas"])) != len(p["alphas"])):
            raise ValueError("distinct positive ordered penalties required")
        if not _positive(p["tail_width"]) or type(p["batch_rows"]) is not int or p["batch_rows"] < 1:
            raise ValueError("invalid tail width or batch rows")
        _date(p["end_before"])
        if (not isinstance(p["provenance"], dict) or not p["provenance"]
                or any(not isinstance(v, str) or len(v) != 64
                       or set(v) - set("0123456789abcdef") or v == "0" * 64
                       for v in p["provenance"].values())):
            raise ValueError("nonzero immutable provenance SHA256 pins required")
        if p["clusters"] is None:
            p["clusters"] = [p["end"], p["identity"][0]]
        if (not isinstance(p["clusters"], list) or len(p["clusters"]) != 2
                or len(set(p["clusters"])) != 2):
            raise ValueError("two distinct cluster columns required")
        self._validate_specs()
        for name, columns in p["comparators"].items():
            if name in {"null", "selected", *self.specifications()} or len(columns) != len(p["baseline"]):
                raise ValueError("invalid comparator name or grid")
        for bounds in p["periods"].values():
            if len(bounds) != 2 or _date(bounds[0]) >= _date(bounds[1]):
                raise ValueError("invalid period bounds")
        self._validate_folds()
        rule = p["continuation"]
        if rule is not None:
            ranked = {f["name"] for f in p["folds"] if f.get("ranked", True)}
            if (not isinstance(rule, dict) or set(rule) != {"controls", "folds"}
                    or any(not isinstance(rule[k], list) or not rule[k]
                           or len(set(rule[k])) != len(rule[k]) for k in ("controls", "folds"))
                    or set(rule["controls"]) - {"null", "f0_only"}
                    or set(rule["folds"]) - ranked):
                raise ValueError("invalid continuation controls or ranked folds")
        self.output = Path(p["output"])

    def _validate_specs(self):
        """Validate explicit ordered recipes without changing legacy identities."""
        p = self.params
        if "feature_specs" not in p:
            return
        specs = p["feature_specs"]
        if not isinstance(specs, list) or not specs:
            raise ValueError("nonempty ordered feature_specs required")
        names = set()
        for spec in specs:
            if not isinstance(spec, dict) or set(spec) != {"name", "features"}:
                raise ValueError("invalid explicit feature specification")
            name, columns = spec["name"], spec["features"]
            if (not isinstance(name, str) or not name.strip() or name in names
                    or name in {"null", "selected", "f0_only"}
                    or not isinstance(columns, list) or not columns
                    or any(not isinstance(c, str) for c in columns)
                    or len(set(columns)) != len(columns) or set(columns) - set(p["features"])):
                raise ValueError("invalid explicit specification name or features")
            names.add(name)
        if any(not f.get("ranked", True) for f in p["folds"]):
            raise ValueError("explicit specifications do not support unranked fits")

    def _validate_folds(self):
        """Require disjoint outer intervals and strictly earlier inner blocks."""
        p = self.params
        if not p["folds"] or len({f["name"] for f in p["folds"]}) != len(p["folds"]):
            raise ValueError("distinct nonempty fold inventory required")
        prior_stop = None
        for fold in p["folds"]:
            required = {"name", "fit_start", "start", "stop", "inner", "exclusions"}
            if set(fold) - required - {"ranked"} or required - set(fold):
                raise ValueError("invalid fold fields")
            if (not isinstance(fold["name"], str) or not fold["name"]
                    or not _date(fold["fit_start"]) < _date(fold["start"]) < _date(fold["stop"]) <= p["end_before"]
                    or (prior_stop is not None and prior_stop > fold["start"])
                    or type(fold.get("ranked", True)) is not bool):
                raise ValueError("invalid or overlapping chronological folds")
            prior_stop = fold["stop"]
            last = fold["fit_start"]
            if fold.get("ranked", True) and not fold["inner"]:
                raise ValueError("ranked folds require inner validation")
            for start, stop in fold["inner"]:
                if not last <= _date(start) < _date(stop) <= fold["start"]:
                    raise ValueError("inner blocks must be disjoint and earlier than outer forecasts")
                last = stop
            for start, stop in fold["exclusions"]:
                if not fold["fit_start"] <= _date(start) < _date(stop) <= fold["start"]:
                    raise ValueError("invalid monitoring exclusion")

    def issue_baselines(self, forecasts, learning, blocks, *, raw_columns, pit_column, knots):
        """Apply each block's own strictly earlier PIT map and retain its lineage.

        ``learning`` maps block names to exact calibration identity/clock/PIT
        frames. A block may bind ``expected`` knot values from a saved recipe;
        any difference refuses. No new outcome or score is read here.
        """
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod
        from dskit.pipeline.libs.cdf_experiment import CDFExperiment

        p, result, receipts = self.params, forecasts.copy(), {}
        if (result.empty or result[p["identity"]].isna().any().any()
                or result.duplicated(p["identity"]).any() or len(raw_columns) != len(p["baseline"])):
            raise ValueError("invalid baseline ledger identities or raw grid")
        for column in (p["date"], p["end"]):
            result[column] = pd.to_datetime(result[column], errors="raise").dt.strftime("%Y-%m-%d")
        if result[[p["date"], p["end"]]].isna().any().any() or not (
                (result[p["date"]] < result[p["end"]]) & (result[p["end"]] < p["end_before"])).all():
            raise ValueError("baseline ledger reaches temporal boundary")
        CDFExperiment.check_pairing(result, result, p["identity"])
        self.estimator.fit(None, result[raw_columns].to_numpy(), None)
        assigned = np.zeros(len(result), int)
        if set(learning) != {block["name"] for block in blocks} or len(learning) != len(blocks):
            raise ValueError("one calibration batch required per distinct baseline block")
        raw = result[raw_columns].to_numpy(float)
        for block in blocks:
            if set(block) != {"name", "start", "stop", "expected"}:
                raise ValueError("invalid baseline block declaration")
            if not _date(block["start"]) < _date(block["stop"]) <= p["end_before"]:
                raise ValueError("invalid baseline block bounds")
            batch = learning[block["name"]].copy()
            if (batch.empty or batch[p["identity"]].isna().any().any()
                    or batch.duplicated(p["identity"]).any()):
                raise ValueError("invalid calibration identities")
            for column in (p["date"], p["end"]):
                batch[column] = pd.to_datetime(batch[column], errors="raise").dt.strftime("%Y-%m-%d")
            if not ((batch[p["date"]] < batch[p["end"]]) & (batch[p["end"]] < block["start"])).all():
                raise ValueError("calibration availability must precede issued baseline")
            CDFExperiment.check_pairing(batch, batch, p["identity"])
            g = GridCDFMethod.calibration_map(batch[pit_column], knots)
            if block["expected"] is not None and not np.array_equal(g, np.asarray(block["expected"], float)):
                raise ValueError("saved calibration map does not reproduce exactly")
            mask = result[p["date"]].between(block["start"], block["stop"], inclusive="left").to_numpy()
            assigned += mask
            result.loc[mask, p["baseline"]] = np.interp(raw[mask], np.linspace(0, 1, knots), g)
            result.loc[mask, p["baseline_asof"]] = batch[p["end"]].max()
            receipts[block["name"]] = {"start": block["start"], "stop": block["stop"],
                "learning_rows": len(batch), "max_available_assumed": batch[p["end"]].max(),
                "map": g.tolist(), "knots": knots,
                "learning_sha256": self._frame_hash(batch.sort_values(p["identity"]),
                                                     list(dict.fromkeys(p["identity"] + [p["end"], pit_column])))}
        if not (assigned == 1).all():
            raise ValueError("every baseline row must belong to exactly one issue-time map")
        return result, receipts

    def specifications(self):
        """Return F0-only, ordered singles, then complete-family bitmasks."""
        p = self.params
        specs = {"f0_only": []}
        if "feature_specs" in p:
            specs.update((v["name"], list(v["features"])) for v in p["feature_specs"])
            return specs
        specs.update({f"feature_{i:03d}": [f] for i, f in enumerate(p["features"])})
        families = [p["families"][name] for name in p["family_order"]]
        for mask in range(1, 2 ** len(families)):
            chosen = {f for i, family in enumerate(families) if mask & (1 << i) for f in family}
            specs[f"families_{mask:02d}"] = [f for f in p["features"] if f in chosen]
        return specs

    def candidates(self):
        """Return the exact tie order, including the zero-correction control."""
        return [{"spec": "null", "alpha": None, "features": []}] + [
            {"spec": name, "alpha": alpha, "features": features}
            for name, features in self.specifications().items() for alpha in self.params["alphas"]]

    @staticmethod
    def choose(losses):
        """Choose the strict finite minimum, breaking exact ties by inventory order."""
        import numpy as np

        values = np.asarray(losses, float)
        if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
            raise ValueError("selection losses must all be finite")
        return int(np.argmin(values))

    @staticmethod
    def _digest(values):
        """Hash normalized numerical predictions independently of array strides."""
        import hashlib
        import numpy as np

        return hashlib.sha256(np.asarray(values, dtype="<f8").tobytes(order="C")).hexdigest()

    @staticmethod
    def _frame_hash(frame, columns):
        """Bind ordered identities, features and clocks with explicit column names."""
        import hashlib
        import pandas as pd

        h = hashlib.sha256(_json(columns))
        h.update(pd.util.hash_pandas_object(frame[columns], index=False).values.tobytes())
        return h.hexdigest()

    def _frame(self, frame, *, outcomes):
        """Validate complete causal rows; predictions must not carry outcomes."""
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.cdf_experiment import CDFExperiment

        p = self.params
        data = frame.copy()
        required = list(dict.fromkeys(p["identity"] + [p["date"], p["end"], p["baseline_asof"]]
                                      + p["baseline"] + p["features"]))
        if outcomes:
            required += [p["outcome"]]
        elif p["outcome"] in data:
            raise ValueError("prediction-only frame must omit the outcome column")
        if set(required) - set(data) or data.empty:
            raise ValueError("missing or empty study columns")
        if data[p["identity"]].isna().any().any() or data.duplicated(p["identity"]).any():
            raise ValueError("duplicate or missing forecast identity")
        for name in (p["date"], p["end"], p["baseline_asof"]):
            if data[name].isna().any():
                raise ValueError("missing study clock")
            data[name] = pd.to_datetime(data[name], errors="raise").dt.strftime("%Y-%m-%d")
        if (not (data[p["baseline_asof"]] < data[p["date"]]).all()
                or not (data[p["date"]] < data[p["end"]]).all()
                or not (data[p["end"]] < p["end_before"]).all()):
            raise ValueError("baseline or outcome chronology violation")
        CDFExperiment.check_pairing(data, data, p["identity"])
        self.estimator.fit(None, data[p["baseline"]].to_numpy(), None)
        for name in p["features"]:
            data[name] = pd.to_numeric(data[name], errors="raise")
        if not outcomes:
            metadata = p["clusters"] + p["pit_series"] + [c for group in p["groups"] for c in group]
            if set(metadata) - set(data) or data[metadata].isna().any().any():
                raise ValueError("missing comparison metadata")
            for columns in p["comparators"].values():
                self.estimator.fit(None, data[columns].to_numpy(), None)
        if outcomes and not np.isfinite(data[p["outcome"]].to_numpy(float)).all():
            raise ValueError("nonfinite historical outcome")
        return data.sort_values(p["identity"]).reset_index(drop=True)

    def _history(self, history, fold, cutoff):
        """Admit only earlier settled rows outside the original monitor bands."""
        p = self.params
        mask = ((history[p["date"]] >= fold["fit_start"])
                & (history[p["date"]] < cutoff) & (history[p["end"]] < cutoff))
        for start, stop in fold["exclusions"]:
            mask &= ~((history[p["date"]] < stop) & (history[p["end"]] >= start))
        result = history.loc[mask]
        if result.empty:
            raise ValueError("empty admitted history")
        return result

    def _fit(self, rows, candidate):
        """Fit one declared setting on its already checked training subset."""
        from dskit.pipeline.libs.cdf_grid_methods import ResidualRidgeCDF

        p = self.params
        model = ResidualRidgeCDF(p["z_grid"], p["score_weights"], candidate["alpha"])
        x = rows[candidate["features"]].to_numpy(float)
        return model.fit(x, rows[p["baseline"]].to_numpy(), rows[p["outcome"]].to_numpy(float))

    def _predict(self, frame, model, features, *, details=False):
        """Use a fixed batch shape for both freeze and verified regeneration."""
        import numpy as np

        p = self.params
        chunks, totals = [], {"rows": 0, "correction_squared": 0., "projection_squared": 0., "repaired_rows": 0}
        for start in range(0, len(frame), p["batch_rows"]):
            rows = frame.iloc[start:start + p["batch_rows"]]
            prediction = model.predict(rows[features].to_numpy(float), rows[p["baseline"]].to_numpy(), details=details)
            if details:
                prediction, stats = prediction
                for key, value in stats.items():
                    totals[key] += value
            chunks.append(prediction)
        result = np.concatenate(chunks)
        return (result, totals) if details else result

    def _inner(self, history, fold, progress):
        """Score every declared setting on expanding earlier validation blocks."""
        import numpy as np

        p, candidates = self.params, self.candidates()
        totals, count, blocks = np.zeros(len(candidates)), 0, []
        for start, stop in fold["inner"]:
            train = self._history(history, fold, start)
            valid = self._history(history, fold, stop)
            valid = valid[valid[p["date"]] >= start]
            if valid.empty:
                raise ValueError("empty inner validation after settlement and monitor filters")
            losses = []
            for candidate in candidates:
                model = self._fit(train, candidate)
                pred = self._predict(valid, model, candidate["features"])
                losses.append(float(model.grid.score(pred, valid[p["outcome"]].to_numpy()).sum()))
            totals += losses
            count += len(valid)
            blocks.append({"start": start, "stop": stop, "training_rows": len(train),
                           "validation_rows": len(valid), "loss_sums": losses,
                           "training_identity": self._frame_hash(train, p["identity"]),
                           "validation_identity": self._frame_hash(valid, p["identity"])})
            if progress:
                progress({"fold": fold["name"], "inner": start, "candidates": len(candidates)})
        return (totals / count).tolist(), blocks

    def freeze(self, history, forecasts, progress=None):
        """Fit all outer procedures and atomically freeze outcome-blind predictions.

        Inputs must be bounded upstream; history may include earlier outer-year
        labels needed by later expanding fits, but never a label at or after the
        latest outer training cutoff. Forecasts contain no outcome column.
        Repeated calls refuse rather than silently reuse partial computations.
        """
        from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError

        p = self.params
        store = AtomicFitStore(self.output / "freeze", p)
        if store.path.exists() or (self.output / "fit-claim.json").exists():
            raise IntegrityError("residual study already claimed or frozen")
        history, forecasts = self._frame(history, outcomes=True), self._frame(forecasts, outcomes=False)
        if not (history[p["end"]] < max(f["start"] for f in p["folds"])).all():
            raise ValueError("history includes outcomes beyond the last training boundary")
        assigned = sum(((forecasts[p["date"]] >= f["start"]) & (forecasts[p["date"]] < f["stop"])).astype(int)
                       for f in p["folds"])
        if not (assigned == 1).all():
            raise ValueError("every forecast must belong to exactly one outer fold")
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / "fit-claim.json").open("x") as claim:
            claim.write(_json({"configuration": value_hash(p)}).decode())
        candidates, book = self.candidates(), {"config_hash": value_hash(p), "folds": {}}
        book["history_hash"] = self._frame_hash(history, list(history.columns))
        book["forecast_hash"] = self._frame_hash(forecasts, list(forecasts.columns))
        book["specifications"], book["candidates"] = self.specifications(), candidates
        for fold in p["folds"]:
            train = self._history(history, fold, fold["start"])
            future = forecasts[(forecasts[p["date"]] >= fold["start"])
                               & (forecasts[p["date"]] < fold["stop"])]
            if future.empty:
                raise ValueError("empty outer fold")
            ranked = fold.get("ranked", True)
            if ranked:
                losses, blocks = self._inner(history, fold, progress)
                selected = candidates[self.choose(losses)]["spec"]
                choices = {"null": candidates[0]}
                for name in self.specifications():
                    indices = [0] + [i for i, c in enumerate(candidates) if c["spec"] == name]
                    chosen = indices[self.choose([losses[i] for i in indices])]
                    choices[name] = candidates[chosen]
            else:
                losses, blocks, selected = [], [], "null"
                full = list(self.specifications())[-1]
                choices = {"null": candidates[0], full: {"spec": full, "features": p["features"],
                                                         "alpha": p["alphas"][0]}}
            receipt = {"ranked": ranked, "training_rows": len(train), "forecast_rows": len(future),
                       "training_identity": self._frame_hash(train, p["identity"]),
                       "max_training_end": train[p["end"]].max(), "inner_losses": losses,
                       "inner_blocks": blocks, "selected": selected, "models": {}}
            for name, candidate in choices.items():
                model = self._fit(train, candidate)
                pred = self._predict(future, model, candidate["features"])
                receipt["models"][name] = {"candidate": candidate, "state": model.state,
                                           "prediction_sha256": self._digest(pred)}
            book["folds"][fold["name"]] = receipt
            if progress:
                progress({"fold": fold["name"], "frozen_models": len(choices), "selected": selected})
        store.publish({"models.json": _json(book)})
        return book

    @staticmethod
    def paired(improvement, first, second):
        """Two-way cluster interval for paired mean improvement, positive is better."""
        import numpy as np
        import pandas as pd
        from scipy.stats import t

        d = np.asarray(improvement, float)
        groups = pd.DataFrame({"first": first, "second": second})
        if d.ndim != 1 or not len(d) or len(groups) != len(d) or not np.isfinite(d).all() or groups.isna().any().any():
            raise ValueError("invalid paired rows or clusters")
        centered, variances, counts = d - d.mean(), [], []
        for key in (groups["first"], groups["second"], pd.MultiIndex.from_frame(groups)):
            codes, unique = pd.factorize(key)
            count = len(unique)
            if count < 2:
                raise ValueError("paired intervals require at least two clusters per dimension")
            sums = np.bincount(codes, weights=centered)
            variances.append(float(count / (count - 1) * (sums @ sums) / len(d) ** 2))
            counts.append(count)
        variance = variances[0] + variances[1] - variances[2]
        radius = float(t.ppf(.975, min(counts[:2]) - 1) * np.sqrt(variance)) if variance > 0 else None
        mean = float(d.mean())
        return {"rows": len(d), "mean_improvement": mean, "variance": variance, "clusters": counts,
                "ci95": [mean - radius, mean + radius] if radius is not None else None,
                "status": "defined" if radius is not None else "nonpositive_variance",
                "rule": "first + second - intersection; G/(G-1); Student-t(min(G)-1); pointwise"}

    def distribution_scores(self, probabilities, outcomes):
        """Score the declared finite-tail GridCurve through public curve/scorer APIs."""
        import numpy as np
        from dskit.pipeline.libs.predictive_cdf import GridCurve, DecisionRegionScores

        p = self.params
        cdf, y = np.asarray(probabilities, float), np.asarray(outcomes, float)
        self.estimator.fit(None, cdf, None)
        if y.shape != (len(cdf),) or not np.isfinite(y).all():
            raise ValueError("invalid score outcomes")
        z = np.asarray(p["z_grid"])
        left, right = z[0] - p["tail_width"], z[-1] + p["tail_width"]
        x = np.broadcast_to(np.r_[left, z, right], (len(y), len(z) + 2))
        curve = GridCurve(x, np.column_stack([np.zeros(len(y)), cdf, np.ones(len(y))]))
        full = np.maximum(left - y, 0.) + np.maximum(y - right, 0.)
        # The public exact segment scorer separates wings at zero; each call
        # has one uniform interval, so multiplying its length removes density.
        cuts = [left, *([0.] if left < 0 < right else []), right]
        for lo, hi in zip(cuts, cuts[1:]):
            context = [{"identity": [str(i)], "thresholds": [lo, hi], "weights": [.5, .5],
                        "intervals": [[lo, hi]]} for i in range(len(y))]
            full += DecisionRegionScores(context).wing_score(curve, y)["decision_wing_twcrps"] * (hi - lo)
        return {"twcrps": self.estimator.grid.score(cdf, y),
                "grid_crps": self.estimator.grid.score(cdf, y, np.ones(len(z))),
                "full_crps": full, "pit": curve.cdf(y[:, None])[:, 0]}

    def _diagnostic(self, rows, prediction):
        """Collect exact distribution scores and threshold reliability totals."""
        import numpy as np

        p = self.params
        y = rows[p["outcome"]].to_numpy(float)
        pieces = [self.distribution_scores(prediction[a:a + p["batch_rows"]], y[a:a + p["batch_rows"]])
                  for a in range(0, len(rows), p["batch_rows"])]
        stats = {k: np.concatenate([part[k] for part in pieces]) for k in pieces[0]}
        truth = self.estimator.grid.indicator(y)
        stats.update(rows=len(y), cdf_sum=prediction.sum(axis=0), frequency_sum=truth.sum(axis=0),
                     brier_sum=((prediction - truth) ** 2).sum(axis=0))
        return stats

    def _summarize_diagnostics(self, diagnostics, combined):
        """Keep warmup separate and report overlapping-series tests descriptively."""
        import numpy as np
        from dskit.pipeline.distribution_scores import BerkowitzTest

        result, p = {}, self.params
        for name, parts in diagnostics.items():
            count = sum(part["rows"] for part in parts)
            entry = {k: float(np.concatenate([part[k] for part in parts]).mean())
                     for k in ("twcrps", "grid_crps", "full_crps")}
            for key in ("cdf_sum", "frequency_sum", "brier_sum"):
                entry[key.replace("_sum", "_mean")] = (sum(part[key] for part in parts) / count).tolist()
            pit = np.concatenate([part["pit"] for part in parts])
            entry["pit_histogram"] = np.histogram(pit, bins=np.linspace(0, 1, 11))[0].tolist()
            entry["pit_zero_or_one"] = int(((pit == 0) | (pit == 1)).sum())
            entry["pit_below_005"] = float((pit < .05).mean())
            entry["pit_above_095"] = float((pit > .95).mean())
            series = combined.copy(deep=False).assign(pit=pit)
            grouped = series.groupby(p["pit_series"], sort=True) if p["pit_series"] else [("all", series)]
            tests = []
            for key, group in grouped:
                ordered = group.sort_values([p["date"], *[c for c in p["identity"] if c != p["date"]]])
                # Endpoint clipping is explicit and affects only the normal test.
                values = np.clip(ordered.pit.to_numpy(), np.finfo(float).eps, 1 - np.finfo(float).eps)
                tests.append({"series": str(key), "rows": len(values), **BerkowitzTest().evaluate(values.tolist())})
            entry["berkowitz"] = {"endpoint_clip": float(np.finfo(float).eps), "series": tests,
                "interpretation": "descriptive AR(1) diagnostic; overlapping horizons invalidate iid confirmation"}
            result[name] = entry
        return result

    def _verified_forecasts(self, forecasts):
        """Verify every fitted state and prediction before allowing outcome access."""
        import json
        from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, IntegrityError
        from dskit.pipeline.libs.cdf_grid_methods import ResidualRidgeCDF

        store = AtomicFitStore(self.output / "freeze", self.params)
        store.verify()
        book = json.loads((store.path / "models.json").read_text())
        if book["specifications"] != self.specifications() or book["candidates"] != self.candidates():
            raise IntegrityError("frozen residual inventory differs from current configuration")
        frame = self._frame(forecasts, outcomes=False)
        if self._frame_hash(frame, list(frame.columns)) != book["forecast_hash"]:
            raise IntegrityError("forecast input hash mismatch")
        for fold in self.params["folds"]:
            rows = frame[(frame[self.params["date"]] >= fold["start"])
                         & (frame[self.params["date"]] < fold["stop"])]
            for saved in book["folds"][fold["name"]]["models"].values():
                model = ResidualRidgeCDF.from_state(saved["state"])
                prediction = self._predict(rows, model, saved["candidate"]["features"])
                if self._digest(prediction) != saved["prediction_sha256"]:
                    raise IntegrityError("regenerated prediction hash mismatch")
        return book, frame

    def _continuation(self, means, by_fold, pairs):
        """Record every frozen research continuation clause; no promotion grant."""
        rule = self.params["continuation"]
        if rule is None:
            return None
        clauses = {}
        for control in rule["controls"]:
            clauses["pooled_vs_" + control] = means[control] > means["selected"]
            interval = pairs["selected_vs_" + control]["ci95"]
            clauses["pooled_ci95_vs_" + control] = (
                interval is not None and math.isfinite(interval[0]) and interval[0] > 0)
            for fold in rule["folds"]:
                clauses[fold + "_vs_" + control] = by_fold[fold][control] > by_fold[fold]["selected"]
        failed = [name for name, passed in clauses.items() if not passed]
        return {"passed": not failed, "clauses": clauses, "failed_clauses": failed,
                "status": "further_research_eligible" if not failed else "stop_feature_operator_search",
                "promotion_assessed": False}

    def evaluate(self, forecasts, load_outcomes, progress=None):
        """Verify the complete freeze, then call the outcome loader exactly once.

        Publish paired row losses and descriptive summaries. Warmup folds remain
        separate and cannot enter pooled ranks or continuation comparisons.
        This method does not confer promotion or trade-execution authority.
        """
        import io
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
        from dskit.pipeline.libs.cdf_grid_methods import ResidualRidgeCDF

        p = self.params
        store = AtomicFitStore(self.output / "evaluation", p)
        if store.path.exists() or (self.output / "evaluation-claim.json").exists():
            raise IntegrityError("evaluation already claimed or published")
        book, frame = self._verified_forecasts(forecasts)
        with (self.output / "evaluation-claim.json").open("x") as claim:
            claim.write(_json({"freeze": AtomicFitStore.file_hash(self.output / "freeze" / "complete.json")}).decode())
        outcomes = load_outcomes().copy()
        for name in (p["date"], p["end"]):
            outcomes[name] = pd.to_datetime(outcomes[name], errors="raise").dt.strftime("%Y-%m-%d")
        CDFExperiment.check_pairing(frame, outcomes, p["identity"])
        outcomes = outcomes.sort_values(p["identity"]).reset_index(drop=True)
        if not np.isfinite(outcomes[p["outcome"]].to_numpy(float)).all():
            raise ValueError("nonfinite evaluation outcome")
        if p["end"] not in p["identity"] and not outcomes[p["end"]].equals(frame[p["end"]]):
            raise ValueError("evaluation outcome clock mismatch")
        frame[p["outcome"]] = outcomes[p["outcome"]].to_numpy(float)
        loss_frames, saved_forecasts, diagnostics, warmup, corrections = [], [], {}, {}, {}
        for fold in p["folds"]:
            rows = frame[(frame[p["date"]] >= fold["start"]) & (frame[p["date"]] < fold["stop"])]
            receipt = book["folds"][fold["name"]]
            meta = list(dict.fromkeys(p["identity"] + p["clusters"] + p["pit_series"] + [c for g in p["groups"] for c in g]))
            losses = rows[meta].copy()
            losses["fold"] = fold["name"]
            predictions = rows[p["identity"]].copy()
            chosen = receipt["selected"]
            for name, saved in receipt["models"].items():
                model = ResidualRidgeCDF.from_state(saved["state"])
                pred, correction = self._predict(rows, model, saved["candidate"]["features"], details=True)
                if receipt["ranked"]:
                    corrections.setdefault(name, []).append(correction)
                    if name == chosen:
                        corrections.setdefault("selected", []).append(correction)
                losses[name] = model.grid.score(pred, rows[p["outcome"]].to_numpy(float))
                if name in {"null", "f0_only", chosen}:
                    stats = self._diagnostic(rows, pred)
                    if receipt["ranked"]:
                        diagnostics.setdefault(name, []).append(stats)
                    if name in {"null", "f0_only"}:
                        for j, col in enumerate(p["baseline"]):
                            predictions[name + "_" + col] = pred[:, j]
                if name == chosen:
                    losses["selected"] = losses[name]
                    if receipt["ranked"]:
                        diagnostics.setdefault("selected", []).append(stats)
                    for j, col in enumerate(p["baseline"]):
                        predictions["selected_" + col] = pred[:, j]
            for name, columns in p["comparators"].items():
                base = rows[columns].to_numpy(float)
                self.estimator.fit(None, base, None)
                losses[name] = self.estimator.grid.score(base, rows[p["outcome"]].to_numpy(float))
                if receipt["ranked"]:
                    diagnostics.setdefault(name, []).append(self._diagnostic(rows, base))
            if receipt["ranked"]:
                loss_frames.append(losses)
                saved_forecasts.append(predictions)
            else:
                warmup[fold["name"]] = {name: float(losses[name].mean()) for name in receipt["models"]}
            if progress:
                progress({"fold": fold["name"], "scored": len(losses)})
        combined = pd.concat(loss_frames, ignore_index=True)
        names = ["null", *self.specifications(), "selected", *p["comparators"]]
        means = {name: float(combined[name].mean()) for name in names}
        pairs = {}
        for name in names:
            for control in ("null", "f0_only"):
                if name != control:
                    pairs[name + "_vs_" + control] = self.paired(
                        combined[control] - combined[name], *(combined[c] for c in p["clusters"]))
        full_name = list(self.specifications())[-1]
        full_mask = 2 ** len(p["families"]) - 1
        for i, family in enumerate([] if "feature_specs" in p else p["family_order"]):
            less = full_mask ^ (1 << i)
            reduced = f"families_{less:02d}" if less else "f0_only"
            pairs["ablation_" + family] = self.paired(
                combined[reduced] - combined[full_name], *(combined[c] for c in p["clusters"]))
        by_fold = {key: {name: float(group[name].mean()) for name in names}
                   for key, group in combined.groupby("fold", sort=False)}
        # Only controls and the selected procedure have every ranked fold.
        reported = {k: v for k, v in diagnostics.items() if sum(t["rows"] for t in v) == len(combined)}
        diagnostic_means = self._summarize_diagnostics(reported, combined)
        correction_summary = {}
        for name, parts in corrections.items():
            rows = sum(part["rows"] for part in parts)
            correction_summary[name] = {
                "weighted_rms_correction": math.sqrt(sum(t["correction_squared"] for t in parts) / rows),
                "weighted_rms_projection": math.sqrt(sum(t["projection_squared"] for t in parts) / rows),
                "fraction_repaired": sum(t["repaired_rows"] for t in parts) / rows}
        slices = []
        for name, (start, stop) in p["periods"].items():
            cell = combined[combined[p["date"]].between(start, stop, inclusive="left")]
            if not cell.empty:
                slices.append({"by": ["period"], "cell": [name], "rows": len(cell),
                               "mean_losses": {n: float(cell[n].mean()) for n in names}})
        for group in p["groups"]:
            for key, cell in combined.groupby(group, sort=True, dropna=False):
                values = key if isinstance(key, tuple) else (key,)
                slices.append({"by": group, "cell": [str(v) for v in values], "rows": len(cell),
                               "mean_losses": {name: float(cell[name].mean()) for name in names}})
        report = {"rows": len(combined), "mean_losses": means, "by_fold": by_fold,
                  "paired": pairs, "diagnostics": diagnostic_means, "corrections": correction_summary,
                  "continuation": self._continuation(means, by_fold, pairs),
                  "warmup_unranked": warmup,
                  "slices": slices, "candidate_count": len(self.candidates()),
                  "config_hash": value_hash(p), "interpretation": "retrospective predictive comparison"}
        files = {"report.json": _json(report)}
        for name, data in (("losses.parquet", combined), ("selected-forecasts.parquet", pd.concat(saved_forecasts))):
            buffer = io.BytesIO()
            data.to_parquet(buffer, index=False)
            files[name] = buffer.getvalue()
        store.publish(files)
        return report


class SettledErrorFeatures:
    """Aggregate issued probability errors using strictly earlier settled labels.

    Parameters
    ----------
    params : dict
        Explicit identity/date/end/outcome/baseline mappings, grid and weights,
        global windows, named grouping columns, group/probability windows,
        probability edges, positive prior_rows, exclusions and end_before.
        Windows include settlement on date minus window and exclude date itself.
        Probability bins use right insertion at each edge.

    Examples
    --------
    Supply a fully declared contract and outcome-free queries::

        transform = SettledErrorFeatures(params)
        features = transform.transform(historical_issued_forecasts, queries)
    """

    _PARAMS = ("identity", "date", "end", "outcome", "baseline", "z_grid",
               "score_weights", "end_before", "windows", "groups", "group_window",
               "probability_edges", "probability_window", "prior_rows", "exclusions")

    def __init__(self, params):
        from dskit.pipeline.libs.cdf_grid_methods import ResidualRidgeCDF

        self.params = copy.deepcopy(params)
        p = self.params
        if set(p) != set(self._PARAMS):
            raise ValueError("unknown or missing settled-error parameters")
        self.grid = ResidualRidgeCDF(p["z_grid"], p["score_weights"], None)
        for key in ("identity", "baseline"):
            values = p[key]
            if (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError("invalid settled-error column inventory")
        if (p["date"] not in p["identity"] or p["outcome"] in p["identity"] + p["baseline"]
                or len(p["baseline"]) != len(p["z_grid"])
                or len({p["date"], p["end"], p["outcome"]}) != 3):
            raise ValueError("invalid settled-error clocks or baseline")
        _date(p["end_before"])
        if (not isinstance(p["windows"], list) or not p["windows"]
                or any(type(v) is not int or v < 1 for v in p["windows"])
                or len(set(p["windows"])) != len(p["windows"])
                or any(type(p[k]) is not int or p[k] not in p["windows"]
                       for k in ("group_window", "probability_window"))
                or not _positive(p["prior_rows"])):
            raise ValueError("invalid settled-error windows or shrinkage")
        edges = p["probability_edges"]
        if (not isinstance(edges, list) or not edges or any(not _positive(v) or v >= 1 for v in edges)
                or any(a >= b for a, b in zip(edges, edges[1:]))):
            raise ValueError("probability edges must increase strictly inside zero and one")
        if (not isinstance(p["groups"], dict) or any(
                not isinstance(name, str) or not name.strip() or not isinstance(cols, list) or not cols
                or any(not isinstance(c, str) or not c.strip() or c == p["outcome"] for c in cols)
                or len(set(cols)) != len(cols) for name, cols in p["groups"].items())):
            raise ValueError("invalid settled-error group mapping")
        for bounds in p["exclusions"]:
            if not isinstance(bounds, list) or len(bounds) != 2 or _date(bounds[0]) >= _date(bounds[1]):
                raise ValueError("invalid settled-error exclusion")
        self.audit = None

    def _frame(self, frame, outcomes):
        """Normalize clocks before checking identity and bounded causal rows."""
        import numpy as np
        import pandas as pd
        from dskit.pipeline.libs.cdf_experiment import CDFExperiment

        p, data = self.params, frame.copy()
        required = set(p["identity"] + p["baseline"] + [p["date"], p["end"]])
        required.update(c for cols in p["groups"].values() for c in cols)
        if outcomes:
            required.add(p["outcome"])
        elif p["outcome"] in data:
            raise ValueError("memory queries must not contain outcomes")
        if required - set(data) or data[list(required)].isna().any().any():
            raise ValueError("missing settled-error fields")
        for col in (p["date"], p["end"]):
            data[col] = pd.to_datetime(data[col], errors="raise").dt.strftime("%Y-%m-%d")
        CDFExperiment.check_pairing(data, data, p["identity"])
        if not ((data[p["date"]] < data[p["end"]]) & (data[p["end"]] < p["end_before"])).all():
            raise ValueError("settled-error row reaches temporal boundary")
        self.grid.fit(None, data[p["baseline"]].to_numpy(), None)
        if outcomes and not np.isfinite(data[p["outcome"]].to_numpy(float)).all():
            raise ValueError("nonfinite memory outcome")
        return data.reset_index(drop=True)

    @staticmethod
    def _window(ends, residual, dates, window, history_codes, query_codes):
        """Return group/window residual sums and counts through prefix sums."""
        import numpy as np

        sums = np.zeros((len(dates), residual.shape[1]))
        counts = np.zeros(len(dates), dtype=int)
        for code in np.unique(query_codes):
            query = np.flatnonzero(query_codes == code)
            past = np.flatnonzero(history_codes == code)
            if not len(past):
                continue
            past = past[np.argsort(ends[past], kind="stable")]
            ordered = ends[past]
            prefix = np.vstack([np.zeros(residual.shape[1]), np.cumsum(residual[past], axis=0)])
            upper = np.searchsorted(ordered, dates[query], side="left")
            lower = np.searchsorted(ordered, dates[query] - np.timedelta64(window, "D"), side="left")
            sums[query] = prefix[upper] - prefix[lower]
            counts[query] = upper - lower
        return sums, counts

    def transform(self, history, queries):
        """Return identity columns and causal pooled/group/conditional errors.

        Parameters
        ----------
        history : pandas.DataFrame
            Issued baseline forecasts and finite outcomes, globally bounded.
        queries : pandas.DataFrame
            Outcome-free baseline forecasts and known grouping metadata.

        Returns
        -------
        pandas.DataFrame
            Same query order, normalized identities and named numerical features.
            ``audit`` records retained/excluded history counts and window rules.

        Raises
        ------
        ValueError
            Schema, identities, probabilities or chronology violate the contract.
        """
        import numpy as np
        import pandas as pd

        p = self.params
        h, q = self._frame(history, True), self._frame(queries, False)
        keep = np.ones(len(h), bool)
        for start, stop in p["exclusions"]:
            keep &= ~((h[p["date"]] < stop) & (h[p["end"]] >= start)).to_numpy()
        original_count = len(h)
        h = h.loc[keep].reset_index(drop=True)
        base = h[p["baseline"]].to_numpy(float)
        residual = self.grid.grid.indicator(h[p["outcome"]].to_numpy(float)) - base
        ends = h[p["end"]].to_numpy(dtype="datetime64[D]")
        dates = q[p["date"]].to_numpy(dtype="datetime64[D]")
        features, pooled = {}, {}
        for window in p["windows"]:
            sums, counts = self._window(ends, residual, dates, window, np.zeros(len(h), int), np.zeros(len(q), int))
            pooled[window] = np.divide(sums, counts[:, None], out=np.zeros_like(sums), where=counts[:, None] > 0)
            features.update((f"pool_{window}_{i}", pooled[window][:, i]) for i in range(base.shape[1]))
        window, prior = p["group_window"], p["prior_rows"]
        for name, columns in sorted(p["groups"].items()):
            categories = pd.MultiIndex.from_frame(pd.concat([h[columns], q[columns]], ignore_index=True))
            codes = pd.factorize(categories)[0]
            sums, counts = self._window(ends, residual, dates, window, codes[:len(h)], codes[len(h):])
            mean = (sums + prior * pooled[window]) / (counts[:, None] + prior)
            features.update((f"group_{name}_{window}_{i}", mean[:, i]) for i in range(base.shape[1]))
        window = p["probability_window"]
        query_base = q[p["baseline"]].to_numpy(float)
        for i in range(base.shape[1]):
            hc = np.searchsorted(p["probability_edges"], base[:, i], side="right")
            qc = np.searchsorted(p["probability_edges"], query_base[:, i], side="right")
            sums, counts = self._window(ends, residual[:, i:i+1], dates, window, hc, qc)
            features[f"bin_{window}_{i}"] = (sums[:, 0] + prior * pooled[window][:, i]) / (counts + prior)
        if set(features) & set(p["identity"]):
            raise ValueError("feature names collide with identity")
        self.audit = {"input_rows": original_count, "retained_rows": len(h),
                      "excluded_rows": original_count - len(h), "query_rows": len(q),
                      "window": "date-window <= settlement < date", "bin_edge": "right insertion"}
        return pd.concat([q[p["identity"]], pd.DataFrame(features)], axis=1)
