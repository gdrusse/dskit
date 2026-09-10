"""The child's half of the Phase 6 event/metric contract (ADR-0117).

ADR-0114's plan §5 splits this contract exactly where the tiering rule
splits everything else. What an event IS — its version, its categories,
its field names, what may become a telemetry label — is generic and lives
in ``dskit.production``. What a *bar* is, what a *lead* is, and what it
means for a signal to *decay* is this project's own knowledge and lives
here.

So this module holds two things and no third:

* :class:`EquityEventAdapter` — a MAPPING from the child's own field names
  onto the catalogue's, and nothing more. It stamps no version, validates
  no body and hashes nothing: ``EventAdapter.event`` already does all
  three, and a second copy of any of them here would be the copy that
  drifts the moment the catalogue moves. The mapping is the whole of the
  domain knowledge — that the catalogue's domain-neutral ``inputs`` are
  this project's one-minute ``bars``.
* :class:`SignalDecay` — the domain metric plan §6 names by example,
  "signal decay over realized latency" / "signal decay by lead". It
  returns a mapping keyed by lead, which is DATA for the ledger and
  report artifacts. Per-lead and per-symbol detail never becomes a
  telemetry label: the metric registry refuses both by name, and full
  fidelity belongs in an artifact rather than in a series an exporter has
  to keep for ever.

Nothing here compares a value to a bound. Whether a decay profile is
acceptable is a threshold, and thresholds wait on the plan's §11 item 7.
"""

from __future__ import annotations

import math

from dskit.production.base import ProductionError
from dskit.production.metrics import EventAdapter

from .final_model import HEADS

__all__ = [
    "BASELINE_LEAD",
    "EquityEventAdapter",
    "SignalDecay",
]

#: The lead every other lead's signal is measured against — the first, and
#: the one a decay profile is a fraction OF. Named once because both the
#: refusal and the ratio read it.
BASELINE_LEAD = HEADS[0]


class EquityEventAdapter(EventAdapter):
    """Map this project's record fields onto the generic event catalogue.

    The catalogue is domain-neutral by the tiering rule, so it counts
    ``inputs`` where this project counts one-minute ``bars``; this class is
    where the two names meet, and it is deliberately the only place they
    do. :attr:`FIELD_MAP` is that meeting written down, so a name that
    drifted out of the catalogue is one table to read rather than a
    scattered set of string literals.

    A record carries more than the catalogue asks for — its own ids, its
    kind, its cache keys. Selecting the readings out of it is this class's
    job; what it does not map is not an event field and never reaches the
    body.

    Parameters
    ----------
    params : dict, optional
        No knob of its own; ``notes`` is allowed, as at every seam site.

    Attributes
    ----------
    FIELD_MAP : dict
        ``{this project's field: (category, catalogue field)}``.

    Examples
    --------
    ::

        adapter = EquityEventAdapter({})
        adapter.event({"symbol": "AAA", "expected_bars": 390, "received_bars": 388})
        # -> {'schema_version': 1,
        #     'identity': {'symbol': 'AAA'},
        #     'data': {'expected_inputs': 390, 'received_inputs': 388}}
    """

    #: The one mapping. Left side is this project's spelling, right side is
    #: the catalogue's (category, field).
    FIELD_MAP = {
        "symbol": ("identity", "symbol"),
        "lead": ("identity", "lead"),
        "release_digest": ("identity", "release_digest"),
        "model_bundle_digest": ("identity", "model_bundle_digest"),
        "cap_digest": ("identity", "cap_digest"),
        "capital_policy_digest": ("identity", "capital_policy_digest"),
        "deployment_eligible": ("identity", "deployment_eligible"),
        "expected_bars": ("data", "expected_inputs"),
        "received_bars": ("data", "received_inputs"),
        "missing_bars": ("data", "missing_inputs"),
        "late_bars": ("data", "late_inputs"),
        "stale_age": ("data", "stale_age"),
        "corporate_action_gaps": ("data", "corporate_action_gaps"),
        "signal_decay": ("execution", "signal_decay"),
    }

    def fields(self, record):
        """Return the catalogue fields one of this project's records carries.

        Parameters
        ----------
        record : dict
            A record in this project's own field names.

        Returns
        -------
        dict
            ``{category: {field: value}}`` over the mapped names the record
            actually carries; a category the record says nothing about is
            left out entirely, because a phase that did not happen is not a
            reading of zero.
        """
        fields = {}
        for source, (category, field) in self.FIELD_MAP.items():
            if source in record:
                fields.setdefault(category, {})[field] = record[source]
        return fields


class SignalDecay:
    """How much of the first lead's signal each later lead still carries.

    Plan §6 asks for "signal decay over realized latency" among the
    execution readings and "signal decay by lead" among the model ones.
    Both are the same question — a forecast made for a further-out lead is
    acted on later and is worth less by the time it is — and the answer is
    a profile keyed by lead, returned as DATA for the ledger and report
    artifacts rather than as a metric series, because a lead may never
    enter a telemetry label set.

    A missing or unusable baseline REFUSES rather than defaulting: a
    profile computed without its baseline is a row of ``1.0``s, which
    reads as "no decay at all" — the one answer a missing measurement must
    never give.

    Parameters
    ----------
    None
        The baseline lead is :data:`BASELINE_LEAD` and the permitted leads
        are ``final_model.HEADS``; neither is a knob.

    Examples
    --------
    ::

        decay = SignalDecay()
        decay.profile({"h01": 0.4, "h02": 0.2})
        # -> {'h01': 1.0, 'h02': 0.5}
    """

    def profile(self, scores):
        """Return each lead's score as a fraction of the baseline lead's.

        Parameters
        ----------
        scores : dict
            ``{lead: score}``; every key a ``final_model.HEADS`` member,
            and :data:`BASELINE_LEAD` required among them.

        Returns
        -------
        dict
            ``{lead: retained fraction}`` over exactly the leads given —
            ``1.0`` at the baseline by construction.

        Raises
        ------
        ProductionError
            If a lead is not a declared head, a score is not a finite
            number, or the baseline is absent or zero.
        """
        problems = self._problems(scores)
        if problems:
            raise ProductionError(problems)
        baseline = float(scores[BASELINE_LEAD])
        return {lead: float(score) / baseline for lead, score in scores.items()}

    def _problems(self, scores):
        """Return every reason this mapping cannot be a decay profile."""
        problems = []
        if not isinstance(scores, dict):
            problems.append(f"scores must be a mapping of lead -> score, got {scores!r}")
            return problems
        for lead in sorted(set(scores) - set(HEADS)):
            problems.append(f"{lead!r} is not a declared lead — {list(HEADS)}")
        for lead, score in sorted(scores.items()):
            if not _finite(score):
                problems.append(f"{lead}: a score must be a finite number, got {score!r}")
        problems += self._baseline_problems(scores)
        return problems

    def _baseline_problems(self, scores):
        """Return why the baseline cannot divide, if it cannot."""
        if BASELINE_LEAD not in scores:
            return [f"a decay profile needs its baseline lead {BASELINE_LEAD!r}"]
        baseline = scores[BASELINE_LEAD]
        if _finite(baseline) and float(baseline) == 0.0:
            return [f"{BASELINE_LEAD}: a zero baseline has no decay to measure"]
        return []


def _finite(value):
    """Return whether value is a finite int or float and not a bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
