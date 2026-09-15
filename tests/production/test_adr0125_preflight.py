"""Focused synthetic contract for the non-authorizing ADR-0125 preflight."""

import copy
import hashlib
import json
import pickle

import pytest

from dskit.pipeline.trust import ReleaseKeyring, TrustedClock
from dskit.production import verifier as verifier_module


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _digest(value):
    raw = value if type(value) is bytes else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _h(label):
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _unsigned(payload, self_name):
    value = dict(payload)
    value[self_name] = _digest(payload)
    return value


def _signed(payload, self_name, role, usage, issued_at_ms):
    value = dict(payload)
    value.update(
        {
            "issuance_basis_sha256": _h("basis-" + self_name),
            "issuer_role": role,
            "key_usage": usage,
            "signature_alg": "Ed25519",
            "issued_at_ms": issued_at_ms,
            "not_before_ms": 0,
            "expires_at_ms": 1_000,
            "revocation_snapshot_sha256": _h("revocations"),
            "key": {"key_id": "release-key", "key_version": 3},
        }
    )
    value[self_name] = _digest(value)
    value["signature"] = "synthetic-signature-" + self_name
    return value


def _pis_entry(input_id, run_identity):
    return {
        "input_id": input_id,
        "kind": "historical-bars",
        "root_ref": "memory://root",
        "root_id": "root-1",
        "snapshot_version": "1",
        "member_manifest_sha256": _h("member-" + input_id),
        "producer_run_identity": run_identity,
        "producer_document_sha256": _h("producer-document"),
        "producer_node": "producer",
        "producer_output": "bars",
        "publication_receipt_schema": "dskit.root-publication-receipt/v1",
        "publication_receipt_sha256": _h("receipt-" + input_id),
        "contract_sha256": _h("contract-" + input_id),
    }


_ACTION_IDS = (
    "tape-data-materialization",
    "tape-manifest-materialization",
    "A1",
    "A2",
    "A3",
    "A4",
)


def _output_contract(action_id):
    return {
        "output_name": "output-" + action_id,
        "output_schema": "dskit.synthetic-output/v1",
        "output_version": "1",
        "purpose": "historical-study",
        "producer_node": action_id,
        "producer_output": "output-" + action_id,
        "media_type": "application/json",
    }


def _input_contract(binding_id, consumer_node, source_kind):
    return {
        "binding_id": binding_id,
        "consumer_node": consumer_node,
        "consumer_input": "bars",
        "source_kind": source_kind,
        "source_ref": "opaque-source-ref",
        "output_schema": "dskit.synthetic-output/v1",
        "output_version": "1",
        "purpose": "historical-study",
    }


def _action_set(
    *,
    selected_study=None,
    selected_component=None,
    contract_kind=None,
    output_graft=False,
):
    actions = []
    edges = []
    for position, action_id in enumerate(_ACTION_IDS):
        if position == 0:
            predecessor_ids = []
            predecessor_refs = []
            root_ids = ["root-input"]
            contracts = [
                _input_contract(
                    "published-binding",
                    action_id,
                    contract_kind or "published-input",
                )
            ]
        else:
            predecessor_id = _ACTION_IDS[position - 1]
            predecessor_output = _output_contract(predecessor_id)
            output_name = predecessor_output["output_name"]
            output_schema = predecessor_output["output_schema"]
            output_version = predecessor_output["output_version"]
            purpose = predecessor_output["purpose"]
            if output_graft and action_id == "A1":
                output_name = "forged-output"
                output_schema = "dskit.forged/v9"
                output_version = "9"
                purpose = "forged-purpose"
            predecessor_ids = [predecessor_id]
            predecessor_refs = [
                {
                    "predecessor_action_id": predecessor_id,
                    "output_name": output_name,
                    "output_schema": output_schema,
                    "output_version": output_version,
                    "purpose": purpose,
                }
            ]
            root_ids = []
            contracts = [
                {
                    "binding_id": "edge-" + action_id,
                    "consumer_node": action_id,
                    "consumer_input": "bars",
                    "source_kind": "predecessor-output",
                    "source_ref": "opaque-source-ref",
                    "output_schema": output_schema,
                    "output_version": output_version,
                    "purpose": purpose,
                }
            ]
            edges.append(
                {
                    "consumer_action_id": action_id,
                    "binding_id": "edge-" + action_id,
                    "predecessor_action_id": predecessor_id,
                    "output_name": output_name,
                    "output_schema": output_schema,
                    "output_version": output_version,
                    "purpose": purpose,
                }
            )
        study_id = selected_study if position == 0 and selected_study else "study-1"
        component = (
            selected_component
            if position == 0 and selected_component
            else _h("component")
        )
        action = _unsigned(
            {
                "schema": "dskit.action-intent/v1",
                "study_id": study_id,
                "action_id": action_id,
                "consumer_document_contract_sha256": _h("consumer-contract"),
                "kind": "synthetic-action",
                "topological_position": position,
                "predecessor_action_ids": predecessor_ids,
                "predecessor_output_refs": predecessor_refs,
                "root_published_input_ids": root_ids,
                "required_input_contracts": contracts,
                "output_contract": _output_contract(action_id),
                "closed_parameters_sha256": _h("parameters-" + action_id),
                "component_manifest_sha256": component,
                "candidate_selection_sha256": _h("candidate-" + action_id),
                "policy_sha256": _h("policy-" + action_id),
            },
            "action_intent_sha256",
        )
        actions.append(action)
    edges.sort(
        key=lambda item: _canonical(
            [
                item["consumer_action_id"],
                item["binding_id"],
                item["predecessor_action_id"],
                item["output_name"],
                item["output_schema"],
                item["output_version"],
                item["purpose"],
            ]
        )
    )
    return actions, edges


def _replay_set(*, selected_study=None, selected_component=None, contract_kind=None):
    replays = []
    for replay_id in ("control", "crash-restart"):
        contracts = []
        if replay_id == "control":
            contracts = [
                _input_contract(
                    "published-binding",
                    "replay-control",
                    contract_kind or "published-input",
                )
            ]
        replay = _unsigned(
            {
                "schema": "dskit.replay-intent/v1",
                "study_id": selected_study
                if replay_id == "control" and selected_study
                else "study-1",
                "replay_id": replay_id,
                "consumer_document_contract_sha256": _h("consumer-contract"),
                "required_input_contracts": contracts,
                "environment_identity_sha256": _h("environment"),
                "execution_profile_sha256": _h("execution-profile"),
                "component_manifest_sha256": (
                    selected_component
                    if replay_id == "control" and selected_component
                    else _h("component")
                ),
                "crash_schedule_sha256": _h("crash-" + replay_id),
                "recovery_policy_sha256": _h("recovery-" + replay_id),
                "policy_sha256": _h("replay-policy-" + replay_id),
            },
            "replay_intent_sha256",
        )
        replays.append(replay)
    return replays


def _build_tuple(profile="bootstrap", **mutations):
    root_item = _pis_entry("root-input", "root-run")
    phase_item = _pis_entry(
        "root-input", "phase-run" if profile != "bootstrap" else "root-run"
    )
    if mutations.get("descriptor_root"):
        descriptor_root = "memory://forged-root"
    else:
        descriptor_root = phase_item["root_ref"]

    root_pis = _signed(
        {
            "schema": "dskit.published-input-set/v2",
            "study_id": "study-1",
            "phase": "root-g1-g2",
            "purpose": "root-publication",
            "entries": [root_item],
        },
        "published_input_set_sha256",
        "data-publisher",
        "published-input-set-g1-g2",
        10,
    )
    if profile == "bootstrap":
        phase_pis = root_pis
        phase_name = "bootstrap"
    else:
        phase_pis = _signed(
            {
                "schema": "dskit.published-input-set/v2",
                "study_id": "study-1",
                "phase": "stage-consumer"
                if profile == "scope-action"
                else "replay-consumer",
                "purpose": "phase-publication",
                "entries": [phase_item],
            },
            "published_input_set_sha256",
            "study-lifecycle",
            "published-input-set-study",
            20,
        )
        phase_name = profile

    selected_study = "other-study" if mutations.get("child_study") else None
    selected_component = (
        _h("other-component") if mutations.get("child_component") else None
    )
    contract_kind = mutations.get("contract_kind")
    actions, edges = _action_set(
        selected_study=selected_study if profile != "replay-pre-final" else None,
        selected_component=selected_component
        if profile != "replay-pre-final"
        else None,
        contract_kind=contract_kind if profile != "replay-pre-final" else None,
        output_graft=mutations.get("output_graft", False),
    )
    replays = _replay_set(
        selected_study=selected_study if profile == "replay-pre-final" else None,
        selected_component=selected_component
        if profile == "replay-pre-final"
        else None,
        contract_kind=contract_kind if profile == "replay-pre-final" else None,
    )
    action_set = _unsigned(
        {"schema": "dskit.action-intent-set/v1", "entries": actions},
        "action_intent_set_sha256",
    )
    replay_set = _unsigned(
        {"schema": "dskit.replay-intent-set/v1", "entries": replays},
        "replay_intent_set_sha256",
    )
    edge_bytes = _canonical(edges)
    scope_root = (
        _h("wrong-root-pis")
        if mutations.get("scope_root")
        else root_pis["published_input_set_sha256"]
    )
    scope = _signed(
        {
            "schema": "dskit.historical-study-scope-intent/v1",
            "study_id": "study-1",
            "purpose": "historical-study",
            "published_input_set_sha256": scope_root,
            "action_intents": actions,
            "action_intent_set_sha256": action_set["action_intent_set_sha256"],
            "action_dag_sha256": _h("action-dag"),
            "edge_set_sha256": _digest(edge_bytes),
            "replay_intents": replays,
            "replay_intent_set_sha256": replay_set["replay_intent_set_sha256"],
            "environment_identity_sha256": _h("environment"),
            "execution_profile_sha256": _h("execution-profile"),
            "component_manifest_sha256": _h("component"),
            "candidate_inventory_sha256": _h("inventory"),
            "policy_set_sha256": _h("policy-set"),
        },
        "historical_study_scope_intent_sha256",
        "study-lifecycle",
        "historical-study-scope-intent",
        30,
    )

    if profile == "replay-pre-final":
        selected = replays[0]
        subject = {
            "kind": "replay",
            "replay_id": selected["replay_id"],
            "replay_intent_sha256": selected["replay_intent_sha256"],
        }
        selected_contract = selected["required_input_contracts"][0]
    else:
        selected = actions[0]
        subject = {
            "kind": "action",
            "action_id": selected["action_id"],
            "action_intent_sha256": selected["action_intent_sha256"],
        }
        selected_contract = selected["required_input_contracts"][0]

    ces_entry = {
        "binding_id": selected_contract["binding_id"],
        "consumer_document_sha256": _h("consumer-document"),
        "consumer_node": selected_contract["consumer_node"],
        "consumer_input": selected_contract["consumer_input"],
        "purpose": selected_contract["purpose"],
        "descriptor_root_ref": descriptor_root,
        "descriptor_snapshot_version": phase_item["snapshot_version"],
        "descriptor_document_sha256": phase_item["producer_document_sha256"],
        "descriptor_node": phase_item["producer_node"],
        "descriptor_output": phase_item["producer_output"],
        "descriptor_purpose": selected_contract["purpose"],
        "published_input": phase_item,
    }
    ces_entries = [] if mutations.get("omit_ces") else [ces_entry]
    if mutations.get("extra_ces"):
        extra_item = _pis_entry("second-input", "phase-run")
        phase_payload = dict(phase_pis)
        for key in ("published_input_set_sha256", "signature"):
            phase_payload.pop(key)
        phase_payload["entries"] = sorted(
            [phase_item, extra_item], key=lambda item: _canonical(item["input_id"])
        )
        phase_pis = _signed(
            {
                key: phase_payload[key]
                for key in ("schema", "study_id", "phase", "purpose", "entries")
            },
            "published_input_set_sha256",
            phase_payload["issuer_role"],
            phase_payload["key_usage"],
            phase_payload["issued_at_ms"],
        )
        extra = dict(ces_entry)
        extra["binding_id"] = "extra-binding"
        extra["published_input"] = extra_item
        extra["descriptor_root_ref"] = extra_item["root_ref"]
        extra["descriptor_snapshot_version"] = extra_item["snapshot_version"]
        extra["descriptor_document_sha256"] = extra_item["producer_document_sha256"]
        extra["descriptor_node"] = extra_item["producer_node"]
        extra["descriptor_output"] = extra_item["producer_output"]
        ces_entries.append(extra)
        ces_entries.sort(key=_canonical)

    phase_pis_digest = (
        _h("wrong-phase-pis")
        if mutations.get("phase_link")
        else phase_pis["published_input_set_sha256"]
    )
    ces = _signed(
        {
            "schema": "dskit.capture-expectation-set/v1",
            "study_id": "study-1",
            "phase": phase_name,
            "subject_ref": subject,
            "consumer_document_contract_sha256": _h("consumer-contract"),
            "consumer_document_sha256": _h("consumer-document"),
            "purpose": "historical-study",
            "component_manifest_sha256": _h("component"),
            "published_input_set_sha256": phase_pis_digest,
            "entries": ces_entries,
        },
        "capture_expectation_set_sha256",
        "study-lifecycle",
        "capture-expectation",
        40,
    )
    if profile == "bootstrap":
        authority_ref = {
            "kind": "scope-intent-gate-set",
            "scope_intent_sha256": scope["historical_study_scope_intent_sha256"],
            "gate_set_sha256": _h("gate-set"),
        }
    elif profile == "scope-action":
        authority_ref = {
            "kind": "scope-authorization-action",
            "scope_authorization_sha256": _h("scope-authorization"),
            "action_intent_sha256": subject["action_intent_sha256"],
        }
    else:
        authority_ref = {
            "kind": "scope-authorization-replay",
            "scope_authorization_sha256": _h("scope-authorization"),
            "replay_intent_sha256": subject["replay_intent_sha256"],
        }
    pea = _signed(
        {
            "schema": "dskit.plan-evaluation-authorization/v1",
            "study_id": "study-1",
            "phase": phase_name,
            "subject_ref": subject,
            "consumer_document_contract_sha256": _h("consumer-contract"),
            "consumer_document_sha256": _h("consumer-document"),
            "purpose": "historical-study",
            "component_manifest_sha256": _h("component"),
            "published_input_set_sha256": phase_pis_digest,
            "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
            "authority_ref": authority_ref,
        },
        "plan_evaluation_authorization_sha256",
        "security-broker",
        "plan-evaluation",
        50,
    )
    bvp = _signed(
        {
            "schema": "dskit.broker-verified-plan/v1",
            "plan_evaluation_authorization_sha256": pea[
                "plan_evaluation_authorization_sha256"
            ],
            "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
            "subject_ref": subject,
            "study_id": "study-1",
            "consumer_document_contract_sha256": _h("consumer-contract"),
            "consumer_document_sha256": _h("consumer-document"),
            "purpose": "historical-study",
            "component_manifest_sha256": _h("component"),
            "planning_rules_sha256": _h("planning-rules"),
            "plan_sha256": _h("plan"),
            "planned_capture_set_sha256": _h("planned-set"),
        },
        "broker_verified_plan_sha256",
        "security-broker",
        "plan-verifier",
        60,
    )
    pces = []
    for entry in ces_entries:
        pce = dict(entry)
        pce["schema"] = "dskit.planned-capture-entry/v1"
        pce["subject_ref"] = subject
        pce = _unsigned(pce, "planned_entry_sha256")
        pces.append(pce)
    pces.sort(
        key=lambda item: (_canonical(item["planned_entry_sha256"]), _canonical(item))
    )
    cas = _signed(
        {
            "schema": "dskit.capture-admission-set/v1",
            "study_id": "study-1",
            "subject_ref": subject,
            "consumer_document_contract_sha256": _h("consumer-contract"),
            "consumer_document_sha256": _h("consumer-document"),
            "purpose": "historical-study",
            "plan_evaluation_authorization_sha256": pea[
                "plan_evaluation_authorization_sha256"
            ],
            "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
            "broker_verified_plan_sha256": bvp["broker_verified_plan_sha256"],
            "planned_capture_set_sha256": bvp["planned_capture_set_sha256"],
            "entries": pces,
        },
        "capture_admission_set_sha256",
        "study-lifecycle",
        "capture-admission",
        70,
    )
    admission_common = {
        "study_id": "study-1",
        "plan_evaluation_authorization_sha256": pea[
            "plan_evaluation_authorization_sha256"
        ],
        "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
        "broker_verified_plan_sha256": bvp["broker_verified_plan_sha256"],
        "plan_sha256": bvp["plan_sha256"],
        "planned_capture_set_sha256": bvp["planned_capture_set_sha256"],
        "capture_admission_set_sha256": cas["capture_admission_set_sha256"],
        "logical_execution_id": "logical-1",
        "run_id": "run-1",
        "recovery_journal_sha256": _h("journal"),
        "recovery_fence_sha256": _h("fence"),
        "recovery_attempt_rules_sha256": _h("attempt-rules"),
    }
    if profile == "replay-pre-final":
        admission = _signed(
            {
                "schema": "dskit.final-replay-admission/v1",
                **admission_common,
                "final_manifest_sha256": _h("final-manifest"),
                "final_replay_entry_sha256": _h("final-replay-entry"),
                "replay_id": subject["replay_id"],
                "replay_intent_sha256": subject["replay_intent_sha256"],
            },
            "final_replay_admission_sha256",
            "study-lifecycle",
            "final-replay-admission",
            80,
        )
    else:
        admission = _signed(
            {
                "schema": "dskit.action-execution-admission/v1",
                **admission_common,
                "scope_authorization_sha256": _h("scope-authorization"),
                "action_id": subject["action_id"],
                "action_intent_sha256": subject["action_intent_sha256"],
                "historical_study_stage_admission_sha256": _h("stage-admission"),
            },
            "action_execution_admission_sha256",
            "study-lifecycle",
            "action-execution-admission",
            80,
        )
    return tuple(
        _canonical(value)
        for value in (
            root_pis,
            phase_pis,
            scope,
            action_set,
            edges,
            replay_set,
            ces,
            pea,
            bvp,
            cas,
            admission,
        )
    )


class _Keyring(ReleaseKeyring):
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def verify(self, key_id, key_version, issued_at_ms, message, signature):
        self.calls.append((key_id, key_version, issued_at_ms, message, signature))
        return self.result


class _Clock(TrustedClock):
    def __init__(self, value=100):
        self.value = value
        self.calls = 0

    def now_ms(self):
        self.calls += 1
        return self.value


def _revocations(result=True):
    class Revocations(verifier_module.HistoricalStudyRevocations):
        def __init__(self):
            self.calls = []

        def is_unrevoked(self, snapshot_sha256, key_id, key_version, now_ms):
            self.calls.append((snapshot_sha256, key_id, key_version, now_ms))
            if isinstance(result, BaseException):
                raise result
            return result

    return Revocations()


def _preflight(*, keyring_result=True, clock_value=100, revocation_result=True):
    keyring = _Keyring(keyring_result)
    clock = _Clock(clock_value)
    revocations = _revocations(revocation_result)
    preflight = verifier_module.HistoricalStudyEnvelopePreflight(
        keyring, clock, revocations
    )
    return preflight, keyring, clock, revocations


@pytest.mark.parametrize("profile", ["bootstrap", "scope-action", "replay-pre-final"])
def test_preflight_accepts_exact_profiles_and_returns_opaque_no_effect(profile):
    preflight, keyring, clock, revocations = _preflight()

    result = preflight.verify(*_build_tuple(profile))

    assert (
        type(result)
        is verifier_module.NonAuthorizingAdr0125StructuralSignaturePreflight
    )
    assert result.deployment_eligible is False
    assert not callable(result)
    assert clock.calls == 1
    assert len(keyring.calls) == 8
    assert len(revocations.calls) == 8
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.loads(pickle.dumps(result)),
    ):
        with pytest.raises(TypeError, match="opaque"):
            operation()


@pytest.mark.parametrize("position", range(11))
def test_preflight_refuses_non_bytes_and_noncanonical_bytes(position):
    preflight, _keyring, _clock, _revocations = _preflight()
    values = list(_build_tuple())
    values[position] = values[position].decode("ascii")
    with pytest.raises((TypeError, ValueError)):
        preflight.verify(*values)

    values = list(_build_tuple())
    values[position] += b" "
    with pytest.raises(ValueError):
        preflight.verify(*values)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keyring_result": False},
        {"keyring_result": 1},
        {"clock_value": True},
        {"clock_value": 1_001},
        {"revocation_result": False},
        {"revocation_result": 1},
        {"revocation_result": RuntimeError("unavailable")},
    ],
)
def test_preflight_fails_closed_at_public_trust_seams(kwargs):
    preflight, _keyring, _clock, _revocations = _preflight(**kwargs)
    with pytest.raises(ValueError):
        preflight.verify(*_build_tuple())


@pytest.mark.parametrize(
    "mutation",
    [
        {"scope_root": True},
        {"phase_link": True},
        {"child_study": True},
        {"child_component": True},
        {"contract_kind": "predecessor-output"},
        {"omit_ces": True},
        {"extra_ces": True},
        {"descriptor_root": True},
        {"output_graft": True},
    ],
)
def test_preflight_refuses_locally_decidable_substitutions(mutation):
    preflight, _keyring, _clock, _revocations = _preflight()
    with pytest.raises(ValueError):
        preflight.verify(*_build_tuple(**mutation))


def test_preflight_refuses_extra_or_missing_position_and_untrusted_dependencies():
    values = _build_tuple()
    preflight, _keyring, _clock, _revocation_spy = _preflight()
    with pytest.raises(TypeError):
        preflight.verify(*values[:-1])
    with pytest.raises(TypeError):
        preflight.verify(*values, b"{}")

    for args in (
        (object(), _Clock(), _revocations()),
        (_Keyring(), object(), _revocations()),
        (_Keyring(), _Clock(), object()),
    ):
        with pytest.raises(TypeError):
            verifier_module.HistoricalStudyEnvelopePreflight(*args)


def test_preflight_refuses_closed_schema_extra_and_bad_self_digest():
    preflight, _keyring, _clock, _revocations = _preflight()
    values = list(_build_tuple())
    ces = json.loads(values[6])
    ces["unexpected"] = "field"
    values[6] = _canonical(ces)
    with pytest.raises(ValueError):
        preflight.verify(*values)

    values = list(_build_tuple())
    ces = json.loads(values[6])
    ces["capture_expectation_set_sha256"] = _h("wrong-self")
    values[6] = _canonical(ces)
    with pytest.raises(ValueError):
        preflight.verify(*values)
