"""Packet 4 fixed capabilities and signed closure under approved Matrix v8.

Real synthetic Ed25519 action/replay graphs exercise local recursive validation
and opaque external anchors. Every successful verification still ends at the
explicit atomic-issuance refusal; transaction/session/concurrency RED is later.
"""

import copy
import hashlib
import inspect
import json
import pickle

import pytest

from dskit.pipeline import trust
from tests.pipeline import test_trust as f4


def _terminal_type(name):
    """Fail at an executable capability assertion, never during collection."""
    value = getattr(trust, name, None)
    assert value is not None, f"Matrix v8 {name} capability is missing"
    return value


def _terminal_fact(terminal_class):
    """Return hostile external bytes, not a caller-issued proof or schema."""
    raw = json.dumps(f"nondeployment {terminal_class} fixture").encode("ascii")
    return {
        "ref": {
            "kind": "external-authorization",
            "role": terminal_class,
            "schema": "urn:dskit:synthetic-external:" + terminal_class,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "bytes": raw,
    }


def _local_signed(payload, self_field, role, usage):
    """Sign exact test bytes independently with the fixed nondeployment key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    value = dict(payload)
    value.update(
        issuer_role=role, key_usage=usage, signature_alg="Ed25519",
        issued_at_ms=100, not_before_ms=0, expires_at_ms=1000,
        revocation_snapshot_sha256=hashlib.sha256(b"p4-fixed-revocations").hexdigest(),
        key={"key_id": role + "/" + usage, "key_version": 1},
    )
    preimage = f4._json_bytes(value)
    value[self_field] = hashlib.sha256(preimage).hexdigest()
    seed = hashlib.sha256(("p4-fixed-test-key/" + role + "/" + usage).encode()).digest()
    value["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(preimage).hex()
    return value


def _terminal_parent(terminal_class):
    """Build a real signed local basis around exact external fixture refs."""
    if terminal_class == "fixed-owner-policy":
        kind, usage = "scope-intent", "historical-study-scope-intent"
        refs = [_terminal_fact(terminal_class)["ref"], {
            "kind": "root-pis", "role": "data-publisher",
            "schema": "dskit.published-input-set/v2", "sha256": "b" * 64,
        }]
        role = "study-lifecycle"
    else:
        kind, usage, role = "root-pis", "published-input-set-g1-g2", "data-publisher"
        refs = [_terminal_fact(name)["ref"] for name in (
            "G1-dataset-authorization", "G2-dataset-authorization",
        )]
        refs.append({
            "kind": "root-publication", "role": "data-publisher",
            "schema": "dskit.root-publication-receipt/v1", "sha256": "c" * 64,
        })
    refs.sort(key=lambda ref: tuple(ref[name] for name in ("kind", "role", "schema", "sha256")))
    basis = _local_signed({
        "schema": "dskit.issuance-basis/v1", "kind": kind,
        "study_id": "synthetic-study", "refs": refs,
    }, "issuance_basis_sha256", role, usage)
    return basis


def _terminal_setup(terminal_class, *, change_parent=False):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact(terminal_class)
    graph, _ = _complete_signed_graph()
    name = "scope-intent" if terminal_class == "fixed-owner-policy" else "root-pis"
    parent = graph.values[name + "/basis"]
    if change_parent:
        record = next(item for item in graph.facts["artifacts"] if item["ref"] == graph.refs[name])
        value = json.loads(record["bytes"])
        self_field = "historical_study_scope_intent_sha256" if name == "scope-intent" else "published_input_set_sha256"
        value.pop(self_field)
        value.pop("signature")
        if name == "scope-intent":
            value["candidate_inventory_sha256"] = "f" * 64
        else:
            value["entries"][0]["root_id"] = "f" * 64
        value = _local_signed(value, self_field, value["issuer_role"], value["key_usage"])
        record["ref"] = dict(record["ref"], sha256=value[self_field])
        record["bytes"] = f4._json_bytes(value)
    broker = _factory()(fixture_facts=graph.facts)
    resolver = broker._p4_resolver
    args = (
        resolver.snapshot(), f4._json_bytes(parent), terminal_class,
        f4._json_bytes(fact["ref"]), fact["bytes"],
    )
    return broker, resolver, args


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_terminal_proof_rejects_resigned_parent_outside_exact_external_policy(terminal_class):
    broker, resolver, args = _terminal_setup(terminal_class, change_parent=True)
    with pytest.raises((TypeError, ValueError), match="policy|projection|parent"):
        resolver._terminal.verify_terminal(*args)
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_exact_terminal_proof_is_opaque_registered_parent_and_snapshot_bound(terminal_class):
    broker, resolver, args = _terminal_setup(terminal_class)
    anchor = resolver._terminal.verify_terminal(*args)
    assert type(anchor) is trust.VerifiedExternalArtifactAnchor
    assert resolver._terminal.require_current(args[0], (anchor,)) is None
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict):
        with pytest.raises(TypeError):
            operation(anchor)
    with pytest.raises(TypeError):
        type(anchor)(anchor)
    with pytest.raises(TypeError):
        type("AnchorSubclass", (type(anchor),), {})
    foreign = _factory()()._p4_resolver
    with pytest.raises((TypeError, ValueError)):
        foreign._terminal.require_current(foreign.snapshot(), (anchor,))
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
@pytest.mark.parametrize("change", [
    "bytes", "ref-digest", "ref-kind", "ref-role", "ref-schema", "class",
    "parent-kind", "parent-ref", "parent-signature", "foreign-snapshot",
    "expiry", "issued", "key-version", "key-use", "owner", "revocations",
])
def test_terminal_proof_refuses_each_independent_authentication_mutation(terminal_class, change):
    broker, resolver, original = _terminal_setup(terminal_class)
    args = list(original)
    if change == "bytes":
        args[4] = b'"substituted external object"'
    elif change.startswith("ref-"):
        ref = json.loads(args[3])
        name = change[4:]
        ref["sha256" if name == "digest" else name] = "f" * 64
        args[3] = f4._json_bytes(ref)
    elif change == "class":
        args[2] = "arbitrary-schema-terminal"
    elif change == "foreign-snapshot":
        args[0] = _factory()()._p4_resolver.snapshot()
    else:
        parent = json.loads(args[1])
        if change == "parent-kind":
            parent["kind"] = "cas"
        elif change == "parent-ref":
            parent["refs"] = []
        elif change == "parent-signature":
            parent["signature"] = "ab" * 64
        elif change == "expiry":
            parent["expires_at_ms"] = 1
        elif change == "issued":
            parent["issued_at_ms"] = 1001
        elif change == "key-version":
            parent["key"]["key_version"] = 2
        elif change == "key-use":
            parent["key_usage"] = "captured-authorization"
        elif change == "owner":
            parent["issuer_role"] = "untrusted-owner"
        elif change == "revocations":
            parent["revocation_snapshot_sha256"] = "e" * 64
        args[1] = f4._json_bytes(parent)
    with pytest.raises((TypeError, ValueError)):
        resolver._terminal.verify_terminal(*args)
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("name", [
    "TerminalArtifactVerifier", "VerifiedExternalArtifactAnchor",
])
def test_terminal_capability_types_are_public_nonconstructible_and_nonserializable(name):
    value = _terminal_type(name)
    assert name in trust.__all__
    with pytest.raises(TypeError):
        value()
    with pytest.raises(TypeError):
        value({"verified": True})


def test_terminal_verifier_has_only_the_reviewed_verification_methods():
    value = _terminal_type("TerminalArtifactVerifier")
    assert value.__abstractmethods__ == {"verify_terminal", "require_current"}
    assert tuple(inspect.signature(value.verify_terminal).parameters) == (
        "self", "snapshot", "parent_basis_bytes", "terminal_class",
        "terminal_ref_bytes", "canonical_artifact_bytes",
    )
    assert tuple(inspect.signature(value.require_current).parameters) == (
        "self", "snapshot", "anchors",
    )
    assert not issubclass(value, trust.LifecycleAuthority)


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_external_facts_remain_opaque_data_in_one_frozen_snapshot(terminal_class):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact(terminal_class)
    broker = _factory()(fixture_facts={"artifacts": [fact]})
    resolver = broker._p4_resolver
    snapshot = resolver.snapshot()
    assert resolver.snapshot() is snapshot
    assert resolver.resolve(snapshot, f4._json_bytes(fact["ref"])) == fact["bytes"]
    fact["ref"]["sha256"] = "f" * 64
    fact["bytes"] = b'"changed after construction"'
    original = _terminal_fact(terminal_class)
    assert resolver.resolve(snapshot, f4._json_bytes(original["ref"])) == original["bytes"]
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict):
        with pytest.raises(TypeError):
            operation(snapshot)
        with pytest.raises(TypeError):
            operation(resolver._terminal)
    assert isinstance(resolver._terminal, trust.TerminalArtifactVerifier)
    assert not isinstance(resolver._terminal, trust.LifecycleAuthority)
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("injection", [
    "terminal_verifier", "terminal_policy", "anchor", "anchors", "snapshot",
    "generation", "verified", "trusted", "url", "path", "import_path",
])
def test_terminal_trust_cannot_be_selected_by_fixture_factory_or_request(injection):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    with pytest.raises((TypeError, ValueError)):
        _factory()(fixture_facts={"artifacts": [fact], injection: "caller"})
    with pytest.raises(TypeError):
        _factory()(**{injection: object()})
    broker = _factory()()
    with pytest.raises(TypeError):
        broker.authorize_capture_set((), _request(), **_runtime(), **{injection: object()})
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("mutation", [
    "foreign-snapshot", "rebuilt-snapshot", "substituted-records",
    "substituted-terminal", "substituted-generation", "substituted-resolve",
])
def test_terminal_snapshot_refuses_independent_identity_and_dispatch_substitution(
    mutation, monkeypatch,
):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    broker = _factory()(fixture_facts={"artifacts": [fact]})
    resolver = broker._p4_resolver
    snapshot = resolver.snapshot()
    if mutation == "foreign-snapshot":
        snapshot = _factory()(fixture_facts={"artifacts": [fact]})._p4_resolver.snapshot()
    elif mutation == "rebuilt-snapshot":
        snapshot = object.__new__(type(snapshot))
    elif mutation == "substituted-records":
        object.__setattr__(resolver, "_records", ())
    elif mutation == "substituted-terminal":
        object.__setattr__(resolver, "_terminal", object())
    elif mutation == "substituted-generation":
        object.__setattr__(snapshot, "_generation", 2)
    elif mutation == "substituted-resolve":
        monkeypatch.setattr(type(resolver), "resolve", lambda *args: fact["bytes"])
    with pytest.raises((TypeError, ValueError), match="snapshot|capability|integrity"):
        # The class-owned gate must detect a public-method replacement too.
        resolver._checked_resolve(snapshot, f4._json_bytes(fact["ref"]))
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("ref_mutation", ["kind", "role", "schema", "sha256", "extra"])
def test_external_fixture_reference_cannot_register_an_unknown_terminal(ref_mutation):
    _terminal_type("TerminalArtifactVerifier")
    fact = _terminal_fact("G1-dataset-authorization")
    fact["ref"][ref_mutation] = "f" * 64 if ref_mutation == "sha256" else "unknown"
    with pytest.raises((TypeError, ValueError)):
        _factory()(fixture_facts={"artifacts": [fact]})


def _graph_hash(value):
    return hashlib.sha256(value if type(value) is bytes else f4._json_bytes(value)).hexdigest()


class _SignedGraph:
    """Independent exact-byte fixture builder; never a runtime resolver or signer."""

    def __init__(self):
        self.facts = {"artifacts": []}
        self.refs = {}
        self.values = {}
        for terminal_class in ("G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy"):
            fact = _terminal_fact(terminal_class)
            self.facts["artifacts"].append(fact)
            self.refs[terminal_class] = fact["ref"]

    def add(self, name, kind, value, self_field, role):
        ref = {"kind": kind, "role": role, "schema": value.get("schema", "dskit.final-replay-entry/v1"),
               "sha256": value[self_field]}
        self.refs[name] = ref
        self.values[name] = value
        record = {"ref": ref, "bytes": f4._json_bytes(value)}
        previous = [item for item in self.facts["artifacts"] if item["ref"] == ref]
        assert not previous or previous == [record], "fixture WORM identity conflict"
        if not previous:
            self.facts["artifacts"].append(record)
        return value

    def unsigned(self, name, kind, payload, self_field, role="study-lifecycle"):
        return self.add(name, kind, dict(payload, **{self_field: _graph_hash(payload)}), self_field, role)

    def signed(self, name, kind, payload, self_field, role, usage, dependencies):
        refs = [dict(self.refs[child]) for child in dependencies]
        refs.sort(key=lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
        basis = _local_signed({"schema": "dskit.issuance-basis/v1", "kind": kind,
                               "study_id": "synthetic-study", "refs": refs},
                              "issuance_basis_sha256", role, usage)
        self.add(name + "/basis", "issuance-basis", basis, "issuance_basis_sha256", role)
        value = _local_signed(dict(payload, issuance_basis_sha256=basis["issuance_basis_sha256"]),
                              self_field, role, usage)
        return self.add(name, kind, value, self_field, role)


def _complete_signed_graph(*, replay=False, count=1):
    """Construct exact action and replay closure including every signed ancestor."""
    from tests.production import test_adr0125_preflight as p3

    graph = _SignedGraph()
    probe = trust._development_broker()
    producer, published, _ = f4._publish(probe)
    probe.end_session(producer)
    publications = [published]
    if count == 2:
        publications.append(f4._foreign_publish(probe)[0])
    descriptor = probe.descriptor(published, purpose="synthetic")
    document = f4._consumer_document(descriptor)
    if count == 2:
        document["pipeline"]["consume"]["inputs"]["second"] = {
            "$captured_artifact": probe.descriptor(publications[1], purpose="synthetic"),
        }
    document_sha = _graph_hash(document)
    root_entries = []
    root_names = []
    for index, publication in enumerate(publications):
        audit = probe._receipt_audit(publication)[-1]
        facts = {key: audit[key] for key in (
            "root_ref", "root_id", "snapshot_version", "member_manifest_sha256",
            "producer_run_identity", "producer_document_sha256", "producer_node", "producer_output",
        )}
        name = f"root-receipt-{index}"
        receipt = graph.signed(name, "root-publication", {
            "schema": "dskit.root-publication-receipt/v1", "kind": "dataset-capture",
            "capture_kind": "raw-event-dataset", **facts,
            "publication_authorization_ref": {
                "kind": "dataset-capture",
                "dataset_capture_authorization_sha256": graph.refs["G1-dataset-authorization"]["sha256"],
            },
        }, "root_publication_receipt_sha256", "data-publisher", "root-publication-g1-g2",
            ["G1-dataset-authorization", "G2-dataset-authorization"])
        root_names.append(name)
        root_entries.append({
            "input_id": f"root-{index}", "kind": "synthetic", **facts,
            "publication_receipt_schema": receipt["schema"],
            "publication_receipt_sha256": receipt["root_publication_receipt_sha256"],
            "contract_sha256": _graph_hash(f"input-contract-{index}".encode()),
        })
    root_pis = graph.signed("root-pis", "root-pis", {
        "schema": "dskit.published-input-set/v2", "study_id": "synthetic-study",
        "phase": "root-g1-g2", "purpose": "synthetic", "entries": root_entries,
    }, "published_input_set_sha256", "data-publisher", "published-input-set-g1-g2",
        ["G1-dataset-authorization", "G2-dataset-authorization", *root_names])
    actions, _ = p3._action_set()
    replays = p3._replay_set()
    contracts = [{
        "binding_id": f"input-{index}", "consumer_node": "consume",
        "consumer_input": "bundle" if index == 0 else "second",
        "source_kind": "published-input", "source_ref": f"root-{index}",
        "output_schema": "dskit.synthetic-output/v1", "output_version": "1", "purpose": "synthetic",
    } for index in range(count)]
    contract_digest = _graph_hash(b"F1-owned-fixed-document-contract")
    for action in actions:
        action.pop("action_intent_sha256")
        action.update(study_id="synthetic-study", predecessor_action_ids=[], predecessor_output_refs=[],
                      root_published_input_ids=[item["input_id"] for item in root_entries],
                      required_input_contracts=contracts, consumer_document_contract_sha256=contract_digest)
        action["output_contract"].update(producer_node="publisher", producer_output="bundle", purpose="synthetic")
        graph.unsigned("intent/" + action["action_id"], "action-intent", action, "action_intent_sha256")
    actions = [graph.values["intent/" + action["action_id"]] for action in actions]
    for replay_intent in replays:
        replay_intent.pop("replay_intent_sha256")
        replay_intent.update(study_id="synthetic-study", required_input_contracts=contracts,
                             consumer_document_contract_sha256=contract_digest)
        graph.unsigned("intent/" + replay_intent["replay_id"], "replay-intent", replay_intent, "replay_intent_sha256")
    replays = [graph.values["intent/" + item["replay_id"]] for item in replays]
    action_set = {"schema": "dskit.action-intent-set/v1", "entries": actions}
    replay_set = {"schema": "dskit.replay-intent-set/v1", "entries": replays}
    action_set_sha, replay_set_sha = _graph_hash(action_set), _graph_hash(replay_set)
    scope_payload = {
        "schema": "dskit.historical-study-scope-intent/v1", "study_id": "synthetic-study",
        "purpose": "synthetic", "published_input_set_sha256": root_pis["published_input_set_sha256"],
        "action_intents": actions, "action_intent_set_sha256": action_set_sha,
        "edge_set_sha256": _graph_hash([]),
        "action_dag_sha256": _graph_hash({"action_intents": actions, "action_intent_set_sha256": action_set_sha,
                                           "edge_set_sha256": _graph_hash([])}),
        "replay_intents": replays, "replay_intent_set_sha256": replay_set_sha,
        "environment_identity_sha256": p3._h("environment"),
        "execution_profile_sha256": p3._h("execution-profile"), "component_manifest_sha256": p3._h("component"),
        "candidate_inventory_sha256": p3._h("inventory"),
        "policy_set_sha256": graph.refs["fixed-owner-policy"]["sha256"],
    }
    scope_intent = graph.signed("scope-intent", "scope-intent", scope_payload,
                               "historical_study_scope_intent_sha256", "study-lifecycle",
                               "historical-study-scope-intent", ["root-pis", "fixed-owner-policy"])
    gates = []
    for gate, owner in enumerate(("architecture", "security", "data", "model", "risk", "execution", "operations", "research")):
        gates.append(graph.signed(f"gate-{gate}", "gate-evidence", {
            "schema": "dskit.gate-evidence-ref/v1", "gate": f"G{gate}",
            "owner_role": owner + "-owner", "key_purpose": owner + "-gate",
            "scope_intent_sha256": scope_intent["historical_study_scope_intent_sha256"],
            "evidence_sha256": p3._h("gate-evidence-" + owner), "approved_identity_sha256": p3._h("gate-identity-" + owner),
        }, "gate_evidence_ref_sha256", owner + "-owner", owner + "-gate", ["scope-intent"]))
    gate_names = [f"gate-{index}" for index in range(8)]
    gate_set_sha = _graph_hash(gates)

    def plan_tuple(intent, pis, pis_name, phase):
        subject_kind = "action" if "action_id" in intent else "replay"
        identity = intent[subject_kind + "_id"]
        plan_document_sha = (document_sha if subject_kind == "replay" or
                             (not replay and identity == actions[0]["action_id"]) else "1" * 64)
        subject = {"kind": subject_kind, subject_kind + "_id": identity,
                   subject_kind + "_intent_sha256": intent[subject_kind + "_intent_sha256"]}
        prefix = "tuple/" + identity + "/"
        entries = [{
            "binding_id": contract["binding_id"], "consumer_document_sha256": plan_document_sha,
            "consumer_node": "consume", "consumer_input": contract["consumer_input"], "purpose": "synthetic",
            "descriptor_root_ref": entry["root_ref"], "descriptor_snapshot_version": entry["snapshot_version"],
            "descriptor_document_sha256": entry["producer_document_sha256"], "descriptor_node": entry["producer_node"],
            "descriptor_output": entry["producer_output"], "descriptor_purpose": "synthetic", "published_input": entry,
        } for contract, entry in zip(contracts, pis["entries"][:count])]
        entries.sort(key=f4._json_bytes)
        common = {"study_id": "synthetic-study", "subject_ref": subject,
                  "consumer_document_contract_sha256": contract_digest, "consumer_document_sha256": plan_document_sha,
                  "purpose": "synthetic", "component_manifest_sha256": p3._h("component")}
        ces = graph.signed(prefix + "ces", "ces", {
            "schema": "dskit.capture-expectation-set/v1", **common, "phase": phase,
            "published_input_set_sha256": pis["published_input_set_sha256"], "entries": entries,
        }, "capture_expectation_set_sha256", "study-lifecycle", "capture-expectation", [pis_name, "intent/" + identity])
        authority_ref = ({"kind": "scope-intent-gate-set", "scope_intent_sha256": scope_intent["historical_study_scope_intent_sha256"],
                          "gate_set_sha256": gate_set_sha} if phase == "bootstrap" else
                         {"kind": "scope-authorization-replay", "scope_authorization_sha256": graph.refs["scope"]["sha256"],
                          "replay_intent_sha256": intent["replay_intent_sha256"]})
        pea = graph.signed(prefix + "pea", "pea", {
            "schema": "dskit.plan-evaluation-authorization/v1", **common, "phase": phase,
            "published_input_set_sha256": pis["published_input_set_sha256"],
            "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"], "authority_ref": authority_ref,
        }, "plan_evaluation_authorization_sha256", "security-broker", "plan-evaluation",
            [pis_name, prefix + "ces", "intent/" + identity, *(gate_names if phase == "bootstrap" else ["scope"])])
        pces = [dict(entry, schema="dskit.planned-capture-entry/v1", subject_ref=subject) for entry in entries]
        pces = [dict(entry, planned_entry_sha256=_graph_hash(entry)) for entry in pces]
        pces.sort(key=lambda entry: (entry["planned_entry_sha256"], f4._json_bytes(entry)))
        planned = graph.unsigned(prefix + "planned", "planned-capture-set", {
            "schema": "dskit.planned-capture-set/v1", "study_id": "synthetic-study", "subject_ref": subject, "entries": pces,
        }, "planned_capture_set_sha256", "security-broker")
        plan_body = {**common, "plan_evaluation_authorization_sha256": pea["plan_evaluation_authorization_sha256"],
                     "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
                     "planning_rules_sha256": p3._h("planning-rules"),
                     "planned_capture_set_sha256": planned["planned_capture_set_sha256"], "planned_capture_set": planned}
        bvp = graph.signed(prefix + "bvp", "bvp", {
            "schema": "dskit.broker-verified-plan/v1", **{key: value for key, value in plan_body.items() if key != "planned_capture_set"},
            "plan_sha256": _graph_hash(plan_body), "planned_capture_set_sha256": planned["planned_capture_set_sha256"],
        }, "broker_verified_plan_sha256", "security-broker", "plan-verifier", [prefix + "pea", prefix + "ces", pis_name, "intent/" + identity])
        cas = graph.signed(prefix + "cas", "cas", {
            "schema": "dskit.capture-admission-set/v1", **{key: value for key, value in common.items() if key != "component_manifest_sha256"},
            "plan_evaluation_authorization_sha256": pea["plan_evaluation_authorization_sha256"],
            "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
            "broker_verified_plan_sha256": bvp["broker_verified_plan_sha256"],
            "planned_capture_set_sha256": planned["planned_capture_set_sha256"], "entries": pces,
        }, "capture_admission_set_sha256", "study-lifecycle", "capture-admission", [prefix + "pea", prefix + "ces", prefix + "bvp", "intent/" + identity])
        return {"action_id": identity, "action_intent_sha256": intent.get("action_intent_sha256"),
                "consumer_document_contract_sha256": contract_digest, "authority_ref": authority_ref,
                "plan_evaluation_authorization_sha256": pea["plan_evaluation_authorization_sha256"],
                "capture_expectation_set_sha256": ces["capture_expectation_set_sha256"],
                "broker_verified_plan_sha256": bvp["broker_verified_plan_sha256"], "plan_sha256": bvp["plan_sha256"],
                "planned_capture_set_sha256": planned["planned_capture_set_sha256"], "capture_admission_set_sha256": cas["capture_admission_set_sha256"]}

    bootstrap = [plan_tuple(action, root_pis, "root-pis", "bootstrap") for action in actions]
    bootstrap.sort(key=lambda item: item["action_id"])
    scope_body = {key: value for key, value in scope_payload.items() if key != "schema"}
    scope_body.update(schema="dskit.historical-study-scope-authorization/v2",
                      scope_intent_sha256=scope_intent["historical_study_scope_intent_sha256"],
                      bootstrap_artifacts=bootstrap, gates=gates, gate_set_sha256=gate_set_sha)
    scope = graph.signed("scope", "scope-authorization", scope_body, "historical_study_scope_authorization_sha256",
                         "study-lifecycle", "historical-study-scope", ["scope-intent", *gate_names,
                         *("tuple/" + action["action_id"] + "/" + slot for action in actions for slot in ("pea", "ces", "bvp", "cas"))])
    stages, outputs = [], []
    for index, action in enumerate(actions):
        identity = action["action_id"]
        prefix = "tuple/" + identity + "/"
        selected_tuple = next(item for item in bootstrap if item["action_id"] == identity)
        tuple_fields = {key: value for key, value in selected_tuple.items() if key not in ("action_id", "action_intent_sha256", "authority_ref")}
        stage = graph.signed("stage/" + identity, "stage-admission", {
            "schema": "dskit.historical-study-stage-admission/v1", "study_id": "synthetic-study",
            "scope_authorization_sha256": scope["historical_study_scope_authorization_sha256"],
            "scope_intent_sha256": scope_intent["historical_study_scope_intent_sha256"], "action_id": identity,
            "action_intent_sha256": action["action_intent_sha256"], "topological_position": index,
            "predecessor_publications": [], "required_inputs": graph.values[prefix + "cas"]["entries"],
            "consumer_document_sha256": graph.values[prefix + "cas"]["consumer_document_sha256"], "closed_parameters_sha256": action["closed_parameters_sha256"],
            "component_manifest_sha256": action["component_manifest_sha256"], "candidate_selection_sha256": action["candidate_selection_sha256"],
            "output_contract": action["output_contract"], **tuple_fields,
        }, "historical_study_stage_admission_sha256", "study-lifecycle", "stage-admission",
            ["scope", "intent/" + identity, *(prefix + slot for slot in ("pea", "ces", "bvp", "cas"))])
        stages.append(stage)
        common_admission = {key: value for key, value in tuple_fields.items() if key != "consumer_document_contract_sha256"}
        common_admission.update(study_id="synthetic-study", logical_execution_id="logical/" + identity,
                                run_id="consumer-run" if not replay and index == 0 else root_entries[index % count]["producer_run_identity"],
                                recovery_journal_sha256=p3._h("journal"), recovery_fence_sha256=p3._h("fence"),
                                recovery_attempt_rules_sha256=p3._h("attempt-rules"))
        admission = graph.signed("admission/" + identity, "action-execution-admission", {
            "schema": "dskit.action-execution-admission/v1", **common_admission,
            "scope_authorization_sha256": scope["historical_study_scope_authorization_sha256"],
            "action_id": identity, "action_intent_sha256": action["action_intent_sha256"],
            "historical_study_stage_admission_sha256": stage["historical_study_stage_admission_sha256"],
        }, "action_execution_admission_sha256", "study-lifecycle", "action-execution-admission",
            ["stage/" + identity, *(prefix + slot for slot in ("pea", "ces", "bvp", "cas"))])
        entry = root_entries[index % count]
        output_facts = {key: entry[key] for key in ("root_ref", "root_id", "snapshot_version", "member_manifest_sha256",
                        "producer_run_identity", "producer_document_sha256", "producer_node", "producer_output")}
        receipt = graph.signed("output/" + identity, "stage-publication", {
            "schema": "dskit.lifecycle-publication-receipt/v2", "kind": "study-stage", "study_id": "synthetic-study",
            "action_id": identity, "execution_authority_ref": {"kind": "action",
            "action_execution_admission_sha256": admission["action_execution_admission_sha256"]},
            "logical_execution_id": admission["logical_execution_id"], "run_id": admission["run_id"],
            "publication_authorization_ref": {"kind": "study-stage", "historical_study_stage_admission_sha256": stage["historical_study_stage_admission_sha256"]},
            **output_facts,
        }, "lifecycle_publication_receipt_sha256", "study-lifecycle", "study-stage-publication",
            ["stage/" + identity, "admission/" + identity])
        published_entry = dict(entry, input_id="output/" + identity, publication_receipt_schema=receipt["schema"],
                               publication_receipt_sha256=receipt["lifecycle_publication_receipt_sha256"])
        outputs.append({"producer_action_id": identity, "producer_output_id": "bundle", "output_schema": action["output_contract"]["output_schema"],
                        "publication_identity_sha256": _graph_hash(("stage-publication-identity/" + identity).encode()),
                        "historical_study_stage_admission_sha256": stage["historical_study_stage_admission_sha256"], "published_input": published_entry})
    if not replay:
        graph.selected = graph.refs["admission/" + actions[0]["action_id"]]
        return graph, document
    outputs.sort(key=lambda value: (value["producer_action_id"], value["producer_output_id"], value["output_schema"], value["publication_identity_sha256"], f4._json_bytes(value)))
    replay_pis = graph.signed("replay-pis", "replay-pis", {
        "schema": "dskit.published-input-set/v2", "study_id": "synthetic-study", "phase": "replay-consumer", "purpose": "synthetic",
        "entries": sorted([item["published_input"] for item in outputs], key=lambda entry: entry["input_id"]),
    }, "published_input_set_sha256", "study-lifecycle", "published-input-set-study", ["scope", *("output/" + action["action_id"] for action in actions)])
    final_entries = []
    for replay_intent in replays:
        identity = replay_intent["replay_id"]
        tuple_value = plan_tuple(replay_intent, replay_pis, "replay-pis", "replay-pre-final")
        fields = {key: value for key, value in tuple_value.items() if key not in ("action_id", "action_intent_sha256", "authority_ref", "consumer_document_contract_sha256")}
        entry = graph.unsigned("final-entry/" + identity, "final-replay-entry", {
            "replay_id": identity, "replay_intent_sha256": replay_intent["replay_intent_sha256"],
            "consumer_document_sha256": document_sha, **fields, "required_inputs": graph.values["tuple/" + identity + "/cas"]["entries"],
            **{key: replay_intent[key] for key in ("environment_identity_sha256", "execution_profile_sha256", "component_manifest_sha256", "crash_schedule_sha256")},
        }, "final_replay_entry_sha256")
        final_entries.append(entry)
    manifest = graph.signed("final", "final-manifest", {
        "schema": "dskit.historical-study-manifest/v2", "study_id": "synthetic-study",
        "scope_authorization_sha256": scope["historical_study_scope_authorization_sha256"],
        "scope_intent_sha256": scope_intent["historical_study_scope_intent_sha256"],
        "published_input_set_sha256": root_pis["published_input_set_sha256"], "gate_set_sha256": gate_set_sha,
        "gates": gates, "stage_admissions": stages, "stage_outputs": outputs,
        "release_output": next(item for item in outputs if item["producer_action_id"] == "A4"), "replay_entries": final_entries,
        **{key: scope[key] for key in ("environment_identity_sha256", "execution_profile_sha256", "component_manifest_sha256")},
    }, "historical_study_manifest_sha256", "study-lifecycle", "historical-study-manifest",
        ["scope", "scope-intent", "root-pis", *gate_names, *("stage/" + action["action_id"] for action in actions),
         *("output/" + action["action_id"] for action in actions), *("final-entry/" + item["replay_id"] for item in replays)])
    entry = final_entries[0]
    graph.signed("replay-admission", "final-replay-admission", {
        "schema": "dskit.final-replay-admission/v1", "study_id": "synthetic-study", "final_manifest_sha256": manifest["historical_study_manifest_sha256"],
        "final_replay_entry_sha256": entry["final_replay_entry_sha256"],
        **{key: entry[key] for key in ("replay_id", "replay_intent_sha256", "plan_evaluation_authorization_sha256", "capture_expectation_set_sha256",
             "broker_verified_plan_sha256", "plan_sha256", "planned_capture_set_sha256", "capture_admission_set_sha256")},
        "logical_execution_id": "logical/replay", "run_id": "consumer-run", "recovery_journal_sha256": p3._h("journal"),
        "recovery_fence_sha256": p3._h("fence"), "recovery_attempt_rules_sha256": p3._h("attempt-rules"),
    }, "final_replay_admission_sha256", "study-lifecycle", "final-replay-admission",
        ["final", "final-entry/control", "intent/control", *("tuple/control/" + slot for slot in ("pea", "ces", "bvp", "cas"))])
    graph.selected = graph.refs["replay-admission"]
    return graph, document


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_complete_closure_fixture_has_real_ed25519_signatures_and_available_bases(replay, count):
    """Check the RED fixture itself without depending on the missing resolver."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    graph, _ = _complete_signed_graph(replay=replay, count=count)
    digests = {item["ref"]["sha256"] for item in graph.facts["artifacts"]}
    for value in graph.values.values():
        if "signature" not in value:
            continue
        self_field = next(item["ref"]["sha256"] for item in graph.facts["artifacts"] if item["bytes"] == f4._json_bytes(value))
        preimage = {key: item for key, item in value.items() if key != "signature" and not (key.endswith("sha256") and item == self_field)}
        assert _graph_hash(preimage) == self_field
        key_id = value["key"]["key_id"]
        public = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("p4-fixed-test-key/" + key_id).encode()).digest()).public_key()
        public.verify(bytes.fromhex(value["signature"]), f4._json_bytes(preimage))
        assert value["issuance_basis_sha256"] in digests


def _closure_ready():
    """Require executable recursive verification, not merely an always-refuser."""
    method = getattr(type(_factory()()._p4_resolver), "close_admission", None)
    assert callable(method), "Matrix v8 complete recursive closure is missing"


def _graph_live(graph, document, count):
    broker = _factory()(fixture_facts=graph.facts)
    producer, published, _ = f4._publish(broker)
    broker.end_session(producer)
    publications = [published]
    if count == 2:
        publications.append(f4._foreign_publish(broker)[0])
    captures = []
    for index, publication in enumerate(publications):
        frozen = broker.freeze_consumer_document(document, "consume", "bundle" if index == 0 else "second", "synthetic")
        captures.append((publication, frozen, broker.derive_consumer_port(frozen)))
    selected = next(value for value in graph.values.values() if value.get("schema") == graph.selected["schema"] and
                    value.get("action_execution_admission_sha256", value.get("final_replay_admission_sha256")) == graph.selected["sha256"])
    cas = next(value for value in graph.values.values() if value.get("schema") == "dskit.capture-admission-set/v1" and
               value["capture_admission_set_sha256"] == selected["capture_admission_set_sha256"])
    by_input = {capture[2]["consumer_input"]: capture for capture in captures}
    captures = tuple(by_input[entry["consumer_input"]] for entry in cas["entries"])
    before = (tuple(broker._receipt_audit(publication) for publication in publications),
              tuple(broker._session_events), tuple(broker._member_events), frozenset(broker._nonces))
    runtime = dict(_runtime(), transition_nonces=tuple(f"p4-{index}" for index in range(count)))
    return broker, captures, runtime, before


def _assert_graph_no_effect(broker, captures, before):
    assert (tuple(broker._receipt_audit(capture[0]) for capture in sorted(captures, key=lambda item: item[2]["consumer_input"])),
            tuple(broker._session_events), tuple(broker._member_events), frozenset(broker._nonces)) == before


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_complete_signed_closure_reaches_only_the_later_atomic_issuance_boundary(replay, count):
    _closure_ready()
    graph, document = _complete_signed_graph(replay=replay, count=count)
    broker, captures, runtime, before = _graph_live(graph, document, count)
    for _attempt in range(2):
        with pytest.raises(ValueError, match="^P4 atomic issuance is unavailable$"):
            broker.authorize_capture_set(captures, graph.selected, **runtime)
        _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("target", [
    "root-receipt-0", "root-pis", "scope-intent", "gate-1", "scope",
    "tuple/tape-data-materialization/ces", "tuple/tape-data-materialization/pea",
    "tuple/tape-data-materialization/bvp", "tuple/tape-data-materialization/cas",
    "stage/tape-data-materialization", "admission/tape-data-materialization",
])
@pytest.mark.parametrize("change", ["missing", "extra-field", "signature", "self", "basis-ref", "basis-kind"])
def test_recursive_local_artifact_and_basis_mutations_never_reach_issuance(replay, target, change):
    _closure_ready()
    graph, document = _complete_signed_graph(replay=replay)
    ref = graph.refs[target + "/basis" if change.startswith("basis-") else target]
    item = next(item for item in graph.facts["artifacts"] if item["ref"] == ref)
    if change == "missing":
        graph.facts["artifacts"].remove(item)
    else:
        value = json.loads(item["bytes"])
        if change == "extra-field":
            value["unexpected"] = "untrusted"
        elif change == "signature":
            value["signature"] = "ab" * 64
        elif change == "self":
            self_name = next(key for key, val in value.items() if key.endswith("sha256") and val == ref["sha256"])
            value[self_name] = "d" * 64
        elif change == "basis-ref":
            value["refs"][0]["sha256"] = "e" * 64
        elif change == "basis-kind":
            value["kind"] = "captured-set"
        item["bytes"] = f4._json_bytes(value)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("target", ["output/A4", "replay-pis", "final-entry/control", "final", "replay-admission"])
@pytest.mark.parametrize("change", ["missing", "extra-field", "noncanonical", "self", "linked-digest"])
def test_replay_only_ancestors_cannot_be_missing_or_substituted(target, change):
    _closure_ready()
    graph, document = _complete_signed_graph(replay=True)
    item = next(item for item in graph.facts["artifacts"] if item["ref"] == graph.refs[target])
    if change == "missing":
        graph.facts["artifacts"].remove(item)
    elif change == "noncanonical":
        item["bytes"] += b" "
        with pytest.raises((TypeError, ValueError)):
            _factory()(fixture_facts=graph.facts)
        return
    else:
        value = json.loads(item["bytes"])
        if change == "extra-field":
            value["future_authority"] = "untrusted"
        elif change == "self":
            self_field = next(key for key, val in value.items() if key.endswith("sha256") and val == item["ref"]["sha256"])
            value[self_field] = "e" * 64
        else:
            linked = next(key for key, val in value.items() if key.endswith("sha256") and val != item["ref"]["sha256"])
            value[linked] = "f" * 64
        item["bytes"] = f4._json_bytes(value)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("surface", [
    "closure-dispatch", "closure-function", "keyring", "clock", "revocations",
    "external-corpus", "local-keys", "local-shapes", "signature-method",
])
def test_complete_closure_refuses_each_single_trust_dependency_replacement(replay, surface, monkeypatch):
    _closure_ready()
    graph, document = _complete_signed_graph(replay=replay)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    if surface == "closure-dispatch":
        monkeypatch.setattr(trust, "_P4_CLOSE_ADMISSION", lambda *args: None)
    elif surface == "closure-function":
        monkeypatch.setattr(trust, "_p4_close_admission", lambda *args: None)
    elif surface == "external-corpus":
        monkeypatch.setattr(trust, "_P4_EXTERNAL_BY_REF", dict(trust._P4_EXTERNAL_BY_REF))
    elif surface == "local-keys":
        monkeypatch.setattr(trust, "_P4_LOCAL_PUBLIC_KEYS", dict(trust._P4_LOCAL_PUBLIC_KEYS))
    elif surface == "local-shapes":
        monkeypatch.setattr(trust, "_P4_ARTIFACT_SPECS", dict(trust._P4_ARTIFACT_SPECS))
    elif surface == "signature-method":
        monkeypatch.setattr(trust.HistoricalStudyEnvelopePreflight, "_verify_one_signed", lambda *args: None)
    else:
        field = "_" + surface
        original = getattr(trust._P4_VERIFICATION, field)
        replacement = type(original)(500) if surface == "clock" else type(original)()
        monkeypatch.setattr(trust._P4_VERIFICATION, field, replacement)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


def _resign_exact(value, self_field):
    """Re-sign deliberately changed metadata, independently of runtime helpers."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    value = copy.deepcopy(value)
    value.pop(self_field)
    value.pop("signature")
    raw = f4._json_bytes(value)
    seed = hashlib.sha256(("p4-fixed-test-key/" + value["issuer_role"] + "/" + value["key_usage"]).encode()).digest()
    return dict(value, **{self_field: hashlib.sha256(raw).hexdigest(),
                         "signature": Ed25519PrivateKey.from_private_bytes(seed).sign(raw).hex()})


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("change", [
    "future", "expired", "not-yet-valid", "revocation", "key-version", "owner", "use",
    "basis-duplicate", "basis-missing", "basis-extra", "basis-kind", "basis-unknown",
])
def test_resigned_complete_graph_cannot_bypass_trust_or_closed_basis_checks(replay, change, monkeypatch):
    original = _local_signed

    def changed(payload, self_field, role, usage):
        value = original(payload, self_field, role, usage)
        if change == "future":
            value["issued_at_ms"] = 700
        elif change == "expired":
            value["expires_at_ms"] = 499
        elif change == "not-yet-valid":
            value["not_before_ms"], value["issued_at_ms"] = 501, 501
        elif change == "revocation":
            value["revocation_snapshot_sha256"] = "e" * 64
        elif change == "key-version":
            value["key"]["key_version"] = 2
        elif change == "owner":
            value["issuer_role"] = "untrusted-owner"
        elif change == "use":
            value["key_usage"] = "captured-authorization"
        elif value["schema"] == "dskit.issuance-basis/v1":
            if change == "basis-duplicate":
                value["refs"].append(copy.deepcopy(value["refs"][0]))
            elif change == "basis-missing":
                value["refs"].pop()
            elif change == "basis-extra":
                value["refs"].append(_terminal_fact("fixed-owner-policy")["ref"])
            elif change == "basis-kind":
                value["kind"] = "captured-set"
            elif change == "basis-unknown":
                value["refs"][0]["schema"] = "dskit.unknown/v1"
            value["refs"].sort(key=lambda ref: tuple(ref[key] for key in ("kind", "role", "schema", "sha256")))
        return _resign_exact(value, self_field)

    monkeypatch.setattr(__import__(__name__, fromlist=["_local_signed"]), "_local_signed", changed)
    graph, document = _complete_signed_graph(replay=replay)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


def test_resigned_replay_pea_authority_cannot_substitute_its_subject(monkeypatch):
    original = _local_signed

    def changed(payload, self_field, role, usage):
        value = original(payload, self_field, role, usage)
        if value["schema"] == "dskit.plan-evaluation-authorization/v1" and value["phase"] == "replay-pre-final":
            value["authority_ref"]["replay_intent_sha256"] = "f" * 64
            return _resign_exact(value, self_field)
        return value

    monkeypatch.setattr(__import__(__name__, fromlist=["_local_signed"]), "_local_signed", changed)
    graph, document = _complete_signed_graph(replay=True)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("change", ["future-admission", "basis-before-dependency"])
def test_resigned_selected_admission_cannot_be_issued_after_trusted_now(replay, change, monkeypatch):
    original = _local_signed
    schema = "dskit.final-replay-admission/v1" if replay else "dskit.action-execution-admission/v1"

    def changed(payload, self_field, role, usage):
        value = original(payload, self_field, role, usage)
        if change == "future-admission" and value["schema"] == schema:
            value["issued_at_ms"] = 700
            return _resign_exact(value, self_field)
        if change == "basis-before-dependency" and value["schema"] == "dskit.issuance-basis/v1" and value["kind"] == schema[6:-3]:
            value["issued_at_ms"] = 50
            return _resign_exact(value, self_field)
        return value

    monkeypatch.setattr(__import__(__name__, fromlist=["_local_signed"]), "_local_signed", changed)
    graph, document = _complete_signed_graph(replay=replay)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert "atomic issuance is unavailable" not in str(failure.value)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
@pytest.mark.parametrize("slot", range(18))
def test_opaque_anchor_rejects_each_independent_binding_substitution(terminal_class, slot):
    broker, resolver, args = _terminal_setup(terminal_class)
    anchor = resolver._terminal.verify_terminal(*args)
    binding = list(anchor._binding)
    binding[slot] = object()
    object.__setattr__(anchor, "_binding", tuple(binding))
    with pytest.raises((TypeError, ValueError)):
        resolver._terminal.require_current(args[0], (anchor,))
    assert not broker._session_events and not broker._member_events


@pytest.mark.parametrize("terminal_class", [
    "G1-dataset-authorization", "G2-dataset-authorization", "fixed-owner-policy",
])
def test_opaque_anchor_rejects_duplicate_and_unregistered_proofs(terminal_class):
    broker, resolver, args = _terminal_setup(terminal_class)
    anchor = resolver._terminal.verify_terminal(*args)
    forged = object.__new__(trust.VerifiedExternalArtifactAnchor)
    object.__setattr__(forged, "_binding", anchor._binding)
    for anchors in ((anchor, anchor), (forged,)):
        with pytest.raises((TypeError, ValueError)):
            resolver._terminal.require_current(args[0], anchors)
    assert not broker._session_events and not broker._member_events


def _factory():
    """Locate the approved factory with an explicit executable RED assertion."""
    factory = getattr(trust, "_development_p4_broker", None)
    assert callable(factory), "Matrix v7 fixed P4 broker factory is missing"
    return factory


def _request():
    """Return a syntactically valid reference to an unavailable admission."""
    return {
        "kind": "action-execution-admission",
        "role": "study-lifecycle",
        "schema": "dskit.action-execution-admission/v1",
        "sha256": "a" * 64,
    }


def _runtime():
    """Return distinct synthetic runtime identifiers with one unused nonce."""
    return {
        "consumer_run_identity": "consumer-run",
        "process_measurement_sha256": f4._SHA["consumer_process"],
        "runtime_sha256": f4._SHA["consumer_runtime"],
        "transition_nonces": ("p4-captured",),
    }


def test_p4_factory_has_no_dependency_selection_and_preserves_v1_abc():
    assert trust.LifecycleAuthority.__abstractmethods__ == {
        "produce", "seal", "publish", "capture", "open_capture"
    }
    factory = _factory()
    parameters = inspect.signature(factory).parameters
    assert tuple(parameters) == ("fixture_facts",)
    assert parameters["fixture_facts"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["fixture_facts"].default is None
    authority_type = getattr(trust, "CapturedAuthorizationAuthority", None)
    assert authority_type is not None, "P4 capability ABC is missing"
    assert issubclass(authority_type, trust.LifecycleAuthority)
    assert authority_type.__abstractmethods__ == (
        trust.LifecycleAuthority.__abstractmethods__ | {"authorize_capture_set"}
    )
    with pytest.raises(TypeError):
        authority_type()
    authority = factory()
    assert isinstance(authority, authority_type)
    assert authority.deployment_eligible is False
    assert not isinstance(trust._development_broker(), authority_type)


@pytest.mark.parametrize("name", [
    "signer", "keyring", "resolver", "clock", "revocations", "contract",
    "ledger", "store", "lock", "callback", "factory", "provider",
])
def test_p4_factory_refuses_every_dependency_keyword(name):
    factory = _factory()
    with pytest.raises(TypeError):
        factory(**{name: object()})
    with pytest.raises(TypeError):
        factory(object())


@pytest.mark.parametrize("facts", [
    object(), lambda: None, "/tmp/authority.json", "https://example.invalid/",
    {"signer": object()}, {"keyring": {}}, {"clock": 0}, {"ledger": {}},
])
def test_p4_factory_rejects_hostile_fixture_authority_selection(facts):
    factory = _factory()
    with pytest.raises((TypeError, ValueError)):
        factory(fixture_facts=facts)


def test_issued_p4_authority_is_not_copyable_or_publicly_constructible():
    authority = _factory()()
    with pytest.raises(TypeError):
        type(authority)()
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):
        dict(authority)


@pytest.mark.parametrize("kind,schema", [
    ("action-execution-admission", "dskit.action-execution-admission/v1"),
    ("final-replay-admission", "dskit.final-replay-admission/v1"),
])
def test_missing_resolved_admission_cannot_spend_live_capture(kind, schema):
    broker = _factory()()
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    before = broker._receipt_audit(published)
    session_events = tuple(broker._session_events)
    member_events = tuple(broker._member_events)
    admission = dict(_request(), kind=kind, schema=schema)

    # The same refusal is retryable and must not create a session or receipt.
    for _attempt in range(2):
        with pytest.raises(ValueError):
            broker.authorize_capture_set(
                ((published, frozen, port),), admission, **_runtime()
            )
        assert broker._receipt_audit(published) == before
        assert tuple(broker._session_events) == session_events
        assert tuple(broker._member_events) == member_events

    captured, session = broker.capture(
        published, frozen, port,
        consumer_run_identity="legacy-consumer",
        process_measurement_sha256=f4._SHA["consumer_process"],
        runtime_sha256=f4._SHA["consumer_runtime"],
        transition_nonce="legacy-captured",
    )
    assert captured is not None and session is not None
    assert broker._receipt_audit(published)[-1]["event"] == "CAPTURED"
    assert tuple(broker._member_events) == member_events


def test_loaded_fixture_is_data_and_cannot_substitute_for_verified_closure():
    admission = _request()
    facts = {"artifacts": [{
        "ref": admission,
        "bytes": f4._json_bytes({"schema": admission["schema"]}),
    }]}
    broker = _factory()(fixture_facts=facts)
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    before = (broker._receipt_audit(published), tuple(broker._session_events))
    facts["artifacts"].clear()
    with pytest.raises(ValueError, match="closure|verification"):
        broker.authorize_capture_set(
            ((published, frozen, port),), admission, **_runtime()
        )
    assert (broker._receipt_audit(published), tuple(broker._session_events)) == before
    assert not broker._member_events


@pytest.mark.parametrize("change", [
    "captures-list", "entry-list", "empty", "duplicate", "port-extra",
    "port-wrong", "reference-extra", "reference-role", "reference-schema",
    "reference-null", "run-empty", "run-producer", "runtime-placeholder",
    "measurement-uppercase", "nonce-list", "nonce-empty", "nonce-used",
    "nonce-cardinality", "frozen-mutated",
])
def test_invalid_request_refuses_before_fixture_lookup_and_any_effect(change):
    broker = _factory()()
    producer, published, _values = f4._publish(broker)
    broker.end_session(producer)
    document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    captures = ((published, frozen, port),)
    admission = _request()
    runtime = _runtime()
    if change == "captures-list":
        captures = list(captures)
    elif change == "entry-list":
        captures = (list(captures[0]),)
    elif change == "empty":
        captures = ()
    elif change == "duplicate":
        captures = captures * 2
        runtime["transition_nonces"] = ("one", "two")
    elif change == "port-extra":
        port["extra"] = "unplanned"
    elif change == "port-wrong":
        port["consumer_input"] = "other"
    elif change == "reference-extra":
        admission["resolver"] = "other"
    elif change == "reference-role":
        admission["role"] = "security-broker"
    elif change == "reference-schema":
        admission["schema"] = "dskit.final-replay-admission/v1"
    elif change == "reference-null":
        admission = None
    elif change == "run-empty":
        runtime["consumer_run_identity"] = ""
    elif change == "run-producer":
        runtime["consumer_run_identity"] = "producer-run"
    elif change == "runtime-placeholder":
        runtime["runtime_sha256"] = "0" * 64
    elif change == "measurement-uppercase":
        runtime["process_measurement_sha256"] = "A" * 64
    elif change == "nonce-list":
        runtime["transition_nonces"] = ["one"]
    elif change == "nonce-empty":
        runtime["transition_nonces"] = ("",)
    elif change == "nonce-used":
        runtime["transition_nonces"] = ("nonce-produced",)
    elif change == "nonce-cardinality":
        runtime["transition_nonces"] = ("one", "two")
    elif change == "frozen-mutated":
        document["name"] = "changed-after-freeze"
    before = (broker._receipt_audit(published), tuple(broker._session_events))
    with pytest.raises((TypeError, ValueError)) as failure:
        broker.authorize_capture_set(captures, admission, **runtime)
    assert "missing admission" not in str(failure.value)
    assert (broker._receipt_audit(published), tuple(broker._session_events)) == before
    assert not broker._member_events
