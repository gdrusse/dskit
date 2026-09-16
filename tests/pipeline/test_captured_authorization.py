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


def _complete_signed_graph(*, replay=False, count=1, nonroot=False, document_name="consumer"):
    """Construct exact action and replay closure including every signed ancestor.

    ``document_name`` names the consumer document so two closures over the same
    publication can carry distinct ``consumer_document_sha256`` identities.
    """
    from tests.production import test_adr0125_preflight as p3

    graph = _SignedGraph()
    probe = trust._development_broker()
    producer, published, _ = f4._publish(probe)
    probe.end_session(producer)
    publications = [published]
    if count == 2:
        publications.append(f4._foreign_publish(probe)[0])
    descriptor = probe.descriptor(published, purpose="synthetic")
    document = f4._consumer_document(descriptor, name=document_name)
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
    edges = []
    for action in actions:
        action.pop("action_intent_sha256")
        action.update(study_id="synthetic-study", predecessor_action_ids=[], predecessor_output_refs=[],
                      root_published_input_ids=[item["input_id"] for item in root_entries],
                      required_input_contracts=contracts, consumer_document_contract_sha256=contract_digest)
        action["output_contract"].update(producer_node="publisher", producer_output="bundle", purpose="synthetic")
        if nonroot and action["topological_position"] < count:
            action["output_contract"]["output_schema"] = f"dskit.synthetic-predecessor-{action['topological_position']}/v1"
        if nonroot and action["action_id"] == "A4":
            predecessors = actions[:count]
            refs = [{"predecessor_action_id": item["action_id"], **{key: item["output_contract"][key]
                     for key in ("output_name", "output_schema", "output_version", "purpose")}} for item in predecessors]
            action.update(predecessor_action_ids=[item["action_id"] for item in predecessors],
                predecessor_output_refs=refs, root_published_input_ids=[],
                required_input_contracts=[dict(contract, source_kind="predecessor-output", source_ref=ref["predecessor_action_id"],
                    output_schema=ref["output_schema"]) for contract, ref in zip(contracts, refs, strict=True)])
            edges = [dict(ref, consumer_action_id="A4", binding_id=contract["binding_id"])
                     for ref, contract in zip(refs, action["required_input_contracts"], strict=True)]
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
        "edge_set_sha256": _graph_hash(edges),
        "action_dag_sha256": _graph_hash({"action_intents": actions, "action_intent_set_sha256": action_set_sha,
                                           "edge_set_sha256": _graph_hash(edges)}),
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
                             (not replay and identity == ("A4" if nonroot else actions[0]["action_id"])) else "1" * 64)
        subject = {"kind": subject_kind, subject_kind + "_id": identity,
                   subject_kind + "_intent_sha256": intent[subject_kind + "_intent_sha256"]}
        prefix = "tuple/" + identity + "/"
        entries = [{
            "binding_id": contract["binding_id"], "consumer_document_sha256": plan_document_sha,
            "consumer_node": "consume", "consumer_input": contract["consumer_input"], "purpose": "synthetic",
            "descriptor_root_ref": entry["root_ref"], "descriptor_snapshot_version": entry["snapshot_version"],
            "descriptor_document_sha256": entry["producer_document_sha256"], "descriptor_node": entry["producer_node"],
            "descriptor_output": entry["producer_output"], "descriptor_purpose": "synthetic", "published_input": entry,
        } for contract, entry in zip(intent["required_input_contracts"], pis["entries"][:count])]
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
                         {"kind": "scope-authorization-" + subject_kind, "scope_authorization_sha256": graph.refs["scope"]["sha256"],
                          subject_kind + "_intent_sha256": intent[subject_kind + "_intent_sha256"]})
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

    bootstrap = [plan_tuple(action, root_pis, "root-pis", "bootstrap") for action in actions if not action["predecessor_output_refs"]]
    bootstrap.sort(key=lambda item: item["action_id"])
    scope_body = {key: value for key, value in scope_payload.items() if key != "schema"}
    scope_body.update(schema="dskit.historical-study-scope-authorization/v2",
                      scope_intent_sha256=scope_intent["historical_study_scope_intent_sha256"],
                      bootstrap_artifacts=bootstrap, gates=gates, gate_set_sha256=gate_set_sha)
    scope = graph.signed("scope", "scope-authorization", scope_body, "historical_study_scope_authorization_sha256",
                         "study-lifecycle", "historical-study-scope", ["scope-intent", *gate_names,
                         *("tuple/" + action["action_id"] + "/" + slot for action in actions if not action["predecessor_output_refs"]
                           for slot in ("pea", "ces", "bvp", "cas"))])
    stages, outputs = [], []
    for index, action in enumerate(actions):
        identity = action["action_id"]
        prefix = "tuple/" + identity + "/"
        predecessor_publications = []
        predecessor_names = []
        if action["predecessor_output_refs"]:
            for ref in action["predecessor_output_refs"]:
                predecessor = ref["predecessor_action_id"]
                output = next(item for item in outputs if item["producer_action_id"] == predecessor)
                predecessor_publications.append(dict(ref, published_input=output["published_input"]))
                predecessor_names.append("output/" + predecessor)
            stage_pis = graph.signed("stage-pis/" + identity, "stage-pis", {
                "schema": "dskit.published-input-set/v2", "study_id": "synthetic-study", "phase": "stage-consumer", "purpose": "synthetic",
                "entries": [item["published_input"] for item in predecessor_publications],
            }, "published_input_set_sha256", "study-lifecycle", "published-input-set-study",
                ["scope", *("stage/" + ref["predecessor_action_id"] for ref in action["predecessor_output_refs"]), *predecessor_names])
            selected_tuple = plan_tuple(action, stage_pis, "stage-pis/" + identity, "scope-action")
        else:
            selected_tuple = next(item for item in bootstrap if item["action_id"] == identity)
        tuple_fields = {key: value for key, value in selected_tuple.items() if key not in ("action_id", "action_intent_sha256", "authority_ref")}
        stage = graph.signed("stage/" + identity, "stage-admission", {
            "schema": "dskit.historical-study-stage-admission/v1", "study_id": "synthetic-study",
            "scope_authorization_sha256": scope["historical_study_scope_authorization_sha256"],
            "scope_intent_sha256": scope_intent["historical_study_scope_intent_sha256"], "action_id": identity,
            "action_intent_sha256": action["action_intent_sha256"], "topological_position": index,
            "predecessor_publications": predecessor_publications, "required_inputs": graph.values[prefix + "cas"]["entries"],
            "consumer_document_sha256": graph.values[prefix + "cas"]["consumer_document_sha256"], "closed_parameters_sha256": action["closed_parameters_sha256"],
            "component_manifest_sha256": action["component_manifest_sha256"], "candidate_selection_sha256": action["candidate_selection_sha256"],
            "output_contract": action["output_contract"], **tuple_fields,
        }, "historical_study_stage_admission_sha256", "study-lifecycle", "stage-admission",
            ["scope", "intent/" + identity, *(prefix + slot for slot in ("pea", "ces", "bvp", "cas")), *predecessor_names])
        stages.append(stage)
        common_admission = {key: value for key, value in tuple_fields.items() if key != "consumer_document_contract_sha256"}
        common_admission.update(study_id="synthetic-study", logical_execution_id="logical/" + identity,
                                run_id="consumer-run" if not replay and identity == ("A4" if nonroot else actions[0]["action_id"]) else root_entries[index % count]["producer_run_identity"],
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
        graph.selected = graph.refs["admission/" + ("A4" if nonroot else actions[0]["action_id"])]
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
def test_complete_signed_closure_itself_remains_nonauthorizing(replay, count):
    _closure_ready()
    graph, document = _complete_signed_graph(replay=replay, count=count)
    broker, captures, runtime, before = _graph_live(graph, document, count)
    for _attempt in range(2):
        resolver = broker._p4_resolver
        assert resolver.close_admission(resolver.snapshot(), graph.selected,
            (broker, captures, {key: value for key, value in runtime.items() if key != "transition_nonces"})) is None
        assert not broker._p4_ledger._committed()
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


def _issue_complete(*, replay=False, count=1):
    """Exercise the real public doorway; incomplete issuance is an assertion RED."""
    graph, document = _complete_signed_graph(replay=replay, count=count)
    broker, captures, runtime, before = _graph_live(graph, document, count)
    try:
        result = broker.authorize_capture_set(captures, graph.selected, **runtime)
    except ValueError as exc:
        pytest.fail(f"complete Packet 4 issuance did not succeed: {exc}")
    assert type(result) is tuple and len(result) == 2
    record, session = result
    assert type(record) is getattr(trust, "CapturedAuthorizationRecord", None)
    assert type(session) is trust.LaunchSession
    assert session._kind == "captured-authorization-v2"
    return graph, broker, captures, runtime, before, record, session


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_p4_complete_atomic_record_has_exact_signed_batch_and_one_session(replay, count):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    graph, broker, captures, runtime, before, record, session = _issue_complete(replay=replay, count=count)
    audit = broker._p4_ledger._audit(record)
    assert set(audit) == {"execution_key", "admission_ref", "consumed", "ports", "receipts", "set", "replay",
                          "bases", "session", "session_start", "streams", "nonces", "batch_sha256"}
    assert audit["admission_ref"] == graph.selected and audit["consumed"] is True
    assert len(audit["ports"]) == len(audit["receipts"]) == count
    assert audit["nonces"] == runtime["transition_nonces"]
    parsed = {slot: [json.loads(raw) for raw in audit[slot]] for slot in ("ports", "receipts", "bases")}
    captured_set = json.loads(audit["set"])
    assert captured_set["schema"] == "dskit.captured-authorization-set/v2"
    admission = next(value for value in graph.values.values() if value.get("schema") == graph.selected["schema"]
                     and value.get("action_execution_admission_sha256", value.get("final_replay_admission_sha256")) == graph.selected["sha256"])
    cas = next(value for value in graph.values.values() if value.get("schema") == "dskit.capture-admission-set/v1"
               and value["capture_admission_set_sha256"] == admission["capture_admission_set_sha256"])
    authority_ref = {"kind": "replay" if replay else "action",
                     "final_replay_admission_sha256" if replay else "action_execution_admission_sha256": graph.selected["sha256"]}
    identities = {"execution_authority_ref": authority_ref, "logical_execution_id": admission["logical_execution_id"], "run_id": admission["run_id"]}
    expected_entries = []
    signed = []
    for index, (port, receipt, pce) in enumerate(zip(parsed["ports"], parsed["receipts"], cas["entries"], strict=True)):
        assert port["schema"] == "dskit.captured-port-authorization/v2"
        assert receipt["schema"] == "dskit.lifecycle-captured-receipt/v2"
        for value in (port, receipt, captured_set):
            assert all(value[key] == expected for key, expected in identities.items())
            assert value["study_id"] == admission["study_id"] and value["subject_ref"] == cas["subject_ref"]
            for key in ("plan_evaluation_authorization_sha256", "capture_expectation_set_sha256", "broker_verified_plan_sha256", "capture_admission_set_sha256"):
                assert value[key] == admission[key]
        assert port["planned_entry_sha256"] == receipt["planned_entry_sha256"] == pce["planned_entry_sha256"]
        assert receipt["captured_port_authorization_sha256"] == port["captured_port_authorization_sha256"]
        assert receipt["predecessor_publication_receipt_schema"] == pce["published_input"]["publication_receipt_schema"]
        assert receipt["predecessor_publication_receipt_sha256"] == pce["published_input"]["publication_receipt_sha256"]
        assert receipt["actor_runtime_sha256"] == runtime["runtime_sha256"]
        assert receipt["transition_nonce"] == runtime["transition_nonces"][index]
        genesis = f4._json_bytes({"schema": "dskit.lifecycle-captured-receipt-genesis/v1",
            "stream_id": audit["streams"][index],
            "predecessor_publication_receipt_schema": pce["published_input"]["publication_receipt_schema"],
            "predecessor_publication_receipt_sha256": pce["published_input"]["publication_receipt_sha256"]})
        assert type(receipt["sequence"]) is int and receipt["sequence"] == 1
        assert receipt["stream_id"] == audit["streams"][index]
        assert receipt["previous_lifecycle_captured_receipt_sha256"] == hashlib.sha256(genesis).hexdigest()
        assert receipt["previous_lifecycle_captured_receipt_sha256"] != receipt["predecessor_publication_receipt_sha256"]
        assert receipt["consumer_kind"] == cas["subject_ref"]["kind"]
        assert receipt["consumer_id"] == cas["subject_ref"]["replay_id" if replay else "action_id"]
        expected_entries.append({**identities, "planned_entry_sha256": pce["planned_entry_sha256"],
            "captured_port_authorization_sha256": port["captured_port_authorization_sha256"],
            "lifecycle_captured_receipt_sha256": receipt["lifecycle_captured_receipt_sha256"]})
        signed.extend(((audit["ports"][index], "captured_port_authorization_sha256", "captured-port-authorization"),
                       (audit["receipts"][index], "lifecycle_captured_receipt_sha256", "lifecycle-capture")))
    assert captured_set["entries"] == expected_entries
    signed.append((audit["set"], "captured_authorization_set_sha256", "captured-authorization"))
    if replay:
        evidence = json.loads(audit["replay"])
        assert evidence["schema"] == "dskit.replay-capture-admission-evidence/v1"
        assert evidence["captured_authorization_set_sha256"] == captured_set["captured_authorization_set_sha256"]
        assert evidence["issued_ports"] == [{key: entry[key] for key in (
            "planned_entry_sha256", "captured_port_authorization_sha256", "lifecycle_captured_receipt_sha256",
        )} for entry in expected_entries]
        for key in ("final_manifest_sha256", "final_replay_entry_sha256", "replay_id", "replay_intent_sha256"):
            assert evidence[key] == admission[key]
        signed.append((audit["replay"], "replay_capture_admission_evidence_sha256", "replay-capture-admission"))
    else:
        assert audit["replay"] is None
    bases = {value["issuance_basis_sha256"]: value for value in parsed["bases"]}
    for raw, self_field, usage in signed:
        value = json.loads(raw)
        assert f4._json_bytes(value) == raw
        assert value["issuer_role"] == "security-broker" and value["key_usage"] == usage
        assert value["signature_alg"] == "Ed25519" and value["key"] == {"key_id": "security-broker/" + usage, "key_version": 1}
        assert value["not_before_ms"] <= value["issued_at_ms"] <= 500 <= value["expires_at_ms"]
        assert value["revocation_snapshot_sha256"] == hashlib.sha256(b"p4-fixed-revocations").hexdigest()
        preimage = f4._json_bytes({key: item for key, item in value.items() if key not in (self_field, "signature")})
        assert hashlib.sha256(preimage).hexdigest() == value[self_field]
        seed = hashlib.sha256(("p4-fixed-test-key/security-broker/" + usage).encode()).digest()
        Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(bytes.fromhex(value["signature"]), preimage)
        basis = bases[value["issuance_basis_sha256"]]
        assert basis["issuer_role"] == "security-broker" and basis["key_usage"] == usage
        assert graph.selected in basis["refs"]
        assert len({f4._json_bytes(ref) for ref in basis["refs"]}) == len(basis["refs"])
        assert all(set(ref) == {"kind", "role", "schema", "sha256"} for ref in basis["refs"])
    decision = json.loads(audit["session"])
    assert set(decision) == {"study_id", "execution_authority_ref", "logical_execution_id", "run_id", "consumer_document_sha256",
        "broker_verified_plan_sha256", "captured_authorization_set_sha256", "process_measurement_sha256", "purpose",
        "issued_at_ms", "expires_at_ms", "session_nonce"}
    assert json.loads(audit["session_start"]) == {"event": "SessionStartRecord", "session": decision}
    assert decision["purpose"] == cas["purpose"] and decision["issued_at_ms"] == 500 and decision["expires_at_ms"] == 1000
    assert len(bytes.fromhex(decision["session_nonce"])) == 32
    assert all(decision[key] == expected for key, expected in identities.items())
    assert decision["consumer_document_sha256"] == cas["consumer_document_sha256"]
    assert decision["captured_authorization_set_sha256"] == captured_set["captured_authorization_set_sha256"]
    assert decision["process_measurement_sha256"] == runtime["process_measurement_sha256"]
    assert broker._p4_ledger.resolve_p4(audit["execution_key"], graph.selected) == (record, session)
    assert broker.authorize_capture_set(captures, graph.selected, **runtime) == (record, session)
    assert broker._p4_ledger._audit(record) == audit
    _assert_graph_no_effect(broker, captures, before)  # No v1 receipt/session/member side channel.


@pytest.mark.parametrize("replay", [False, True])
def test_p4_record_and_session_cannot_be_copied_serialized_or_used_by_legacy(replay):
    _graph, broker, captures, _runtime_value, before, record, session = _issue_complete(replay=replay)
    for value in (record, session):
        for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict, bytes):
            with pytest.raises(TypeError):
                operation(value)
    for operation in (
        lambda: broker.open_capture(session, record),
        lambda: broker.captured_bindings(session, captures[0][1], record, "consume", "forbidden"),
        lambda: broker.release(session),
        lambda: broker._consume_binding(record, "bundle"),
    ):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            operation()
    with pytest.raises(TypeError):
        trust.CapturedAuthorizationRecord()
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("point", ["preflight", "prepared", "reserved", "verified", "intern-before", "intern-after",
                                  "commit-before", "commit-after", "return"])
def test_p4_failure_is_empty_or_resolves_the_one_complete_committed_identity(replay, point):
    graph, document = _complete_signed_graph(replay=replay)
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    ledger = getattr(broker, "_p4_ledger", None)
    assert ledger is not None, "same-domain P4 ledger is missing"
    ledger._test_fault = point
    with pytest.raises(RuntimeError, match="injected P4"):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert len(ledger._committed()) == (1 if point in ("commit-after", "return") else 0)
    _assert_graph_no_effect(broker, captures, before)
    result = broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert len(ledger._committed()) == 1
    assert broker.authorize_capture_set(captures, graph.selected, **runtime) == result


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("different_request", [False, True])
def test_p4_concurrent_same_admission_never_remints_or_mixes_requests(replay, different_request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    graph, document = _complete_signed_graph(replay=replay, count=2)
    broker, captures, runtime, before = _graph_live(graph, document, 2)
    barrier = Barrier(4)

    def contender(index):
        request = dict(runtime)
        if different_request:
            request["transition_nonces"] = (f"race-{index}-0", f"race-{index}-1")
        barrier.wait(timeout=5)
        try:
            return broker.authorize_capture_set(captures, graph.selected, **request)
        except (TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(contender, range(4)))
    winners = [result for result in results if result is not None]
    assert len(winners) == (1 if different_request else 4), "one immutable transaction must win"
    assert all(result[0] is winners[0][0] and result[1] is winners[0][1] for result in winners)
    assert len(broker._p4_ledger._committed()) == 1
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("winner", ["legacy", "p4"])
def test_p4_and_legacy_are_mutually_exclusive_for_the_same_live_publication(winner):
    graph, document = _complete_signed_graph()
    broker, captures, runtime, _before = _graph_live(graph, document, 1)

    def legacy():
        return broker.capture(*captures[0], **{key: value for key, value in runtime.items() if key != "transition_nonces"},
                              transition_nonce="legacy-winner")

    def p4():
        return broker.authorize_capture_set(captures, graph.selected, **runtime)

    first, second = (legacy, p4) if winner == "legacy" else (p4, legacy)
    try:
        assert first() is not None
    except ValueError as exc:
        pytest.fail(f"intended arbitration winner did not complete: {exc}")
    with pytest.raises((TypeError, ValueError)):
        second()
    assert not broker._member_events


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_nonroot_predecessor_action_and_its_replay_form_a_complete_positive_dag(replay, count):
    graph, document = _complete_signed_graph(replay=replay, count=count, nonroot=True)
    scope = graph.values["scope"]
    assert len(scope["bootstrap_artifacts"]) == 5
    stage = graph.values["stage/A4"]
    assert len(stage["predecessor_publications"]) == count
    assert graph.values["tuple/A4/pea"]["phase"] == "scope-action"
    broker, captures, runtime, before = _graph_live(graph, document, count)
    try:
        record, session = broker.authorize_capture_set(captures, graph.selected, **runtime)
    except ValueError as exc:
        pytest.fail(f"approved nonroot graph must issue its complete transaction: {exc}")
    assert type(record) is trust.CapturedAuthorizationRecord
    assert session._kind == "captured-authorization-v2"
    _assert_graph_no_effect(broker, captures, before)


def test_private_prepared_batch_is_opaque_nonserializable_and_nonauthorizing():
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    streams = tuple(broker._stream_for_published(item[0], "test") for item in captures)
    prepared = trust._P4_PREPARE(trust._P4_CONTRACT, broker._p4_resolver, graph.selected, streams,
        {key: value for key, value in runtime.items() if key != "transition_nonces"}, runtime["transition_nonces"])
    assert not isinstance(prepared, (bytes, str, dict, tuple, list)), "private prepared bytes must not escape as data"
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict, bytes):
        with pytest.raises(TypeError):
            operation(prepared)
    assert not broker._p4_ledger._committed()
    with pytest.raises((TypeError, ValueError)):
        broker._p4_ledger._audit(prepared)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("surface", ["commit-capsule", "ledger-root", "ledger-lock", "ledger-pin", "contract", "signer"])
def test_new_transaction_dependencies_refuse_one_surface_substitution(surface, monkeypatch):
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    ledger = broker._p4_ledger
    if surface == "commit-capsule":
        monkeypatch.setattr(trust, "_P4_COMMIT", lambda *args: ("forged-record", "forged-session"))
    elif surface == "ledger-root":
        ledger._root = ("forged",)
    elif surface == "ledger-lock":
        from threading import RLock
        ledger._lock = RLock()
    elif surface == "ledger-pin":
        trust._P4_LEDGER_PINS[ledger] = tuple(list(ledger._pin))
    elif surface == "contract":
        monkeypatch.setattr(trust, "_P4_CONTRACT", type(trust._P4_CONTRACT)())
    else:
        monkeypatch.setattr(trust, "_P4_SIGNER", type(trust._P4_SIGNER)())
    with pytest.raises((TypeError, ValueError)):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    # A deliberately corrupted ledger is not queried as an oracle afterward.
    assert tuple(broker._session_events) == before[1] and tuple(broker._member_events) == before[2]
    assert frozenset(broker._nonces) == before[3]


_SIGNED_FIELDS = "issuance_basis_sha256 issuer_role key_usage signature_alg issued_at_ms not_before_ms expires_at_ms revocation_snapshot_sha256 key signature".split()
_BATCH_FIELDS = {
    "ports": "schema study_id execution_authority_ref logical_execution_id run_id subject_ref plan_evaluation_authorization_sha256 capture_expectation_set_sha256 broker_verified_plan_sha256 capture_admission_set_sha256 planned_entry_sha256 captured_port_authorization_sha256",
    "receipts": "schema stream_id sequence previous_lifecycle_captured_receipt_sha256 study_id consumer_kind consumer_id execution_authority_ref logical_execution_id run_id subject_ref predecessor_publication_receipt_schema predecessor_publication_receipt_sha256 plan_evaluation_authorization_sha256 capture_expectation_set_sha256 broker_verified_plan_sha256 planned_capture_set_sha256 capture_admission_set_sha256 planned_entry_sha256 captured_port_authorization_sha256 actor_runtime_sha256 transition_nonce lifecycle_captured_receipt_sha256",
    "set": "schema study_id execution_authority_ref logical_execution_id run_id subject_ref plan_evaluation_authorization_sha256 capture_expectation_set_sha256 broker_verified_plan_sha256 planned_capture_set_sha256 capture_admission_set_sha256 entries captured_authorization_set_sha256",
    "replay": "schema study_id final_manifest_sha256 final_replay_admission_sha256 final_replay_entry_sha256 replay_id replay_intent_sha256 plan_evaluation_authorization_sha256 capture_expectation_set_sha256 broker_verified_plan_sha256 planned_capture_set_sha256 capture_admission_set_sha256 issued_ports captured_authorization_set_sha256 replay_capture_admission_evidence_sha256",
}


@pytest.fixture(scope="module")
def committed_replay_batch():
    return _issue_complete(replay=True, count=2)


@pytest.mark.parametrize("slot,field", [(slot, field) for slot, names in _BATCH_FIELDS.items()
                                       for field in (*names.split(), *_SIGNED_FIELDS)])
@pytest.mark.parametrize("change", ["missing", "substituted", "resigned"])
def test_every_emitted_field_is_closed_and_bound_independently(committed_replay_batch, slot, field, change):
    graph, broker, captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = {name: [json.loads(raw) for raw in audit[name]] for name in ("ports", "receipts", "bases")}
    batch.update(set=json.loads(audit["set"]), replay=json.loads(audit["replay"]))
    value = batch[slot][0] if slot in ("ports", "receipts") else batch[slot]
    assert set(value) == set(_BATCH_FIELDS[slot].split()) | set(_SIGNED_FIELDS)
    if change == "missing":
        value.pop(field)
    else:
        original = value[field]
        value[field] = (original + 1 if type(original) is int else
                        {"substituted": "identity"} if type(original) is dict else
                        [] if type(original) is list else "e" * 64)
        if change == "resigned" and field not in ("issuer_role", "key_usage", "signature", _BATCH_FIELDS[slot].split()[-1]):
            self_field = _BATCH_FIELDS[slot].split()[-1]
            value.update(_resign_exact(value, self_field))
    with pytest.raises((TypeError, ValueError)):
        trust._P4_VERIFY_BYTES(trust._P4_CONTRACT, f4._json_bytes(batch), broker._p4_resolver, graph.selected,
            audit["streams"], {key: val for key, val in runtime.items() if key != "transition_nonces"}, runtime["transition_nonces"])
    assert broker._p4_ledger._audit(record) == audit
    assert not broker._member_events


@pytest.mark.parametrize("value", [None, "", "0" * 64, "A" * 64, "f" * 64])
def test_first_v2_genesis_has_no_default_or_alternate_sentinel(committed_replay_batch, value):
    graph, broker, _captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = {name: [json.loads(raw) for raw in audit[name]] for name in ("ports", "receipts", "bases")}
    batch.update(set=json.loads(audit["set"]), replay=json.loads(audit["replay"]))
    receipt = batch["receipts"][0]
    receipt["previous_lifecycle_captured_receipt_sha256"] = value
    receipt.update(_resign_exact(receipt, "lifecycle_captured_receipt_sha256"))
    with pytest.raises((TypeError, ValueError)):
        trust._P4_VERIFY_BYTES(trust._P4_CONTRACT, f4._json_bytes(batch), broker._p4_resolver, graph.selected,
            audit["streams"], {key: val for key, val in runtime.items() if key != "transition_nonces"}, runtime["transition_nonces"])


@pytest.mark.parametrize("facade", ["direct", "verifier", "driver"])
def test_legacy_capture_waits_for_the_same_lock_then_observes_p4_commit(facade):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from dskit.production.verifier import HistoricalStudyCaptureDriver, HistoricalStudyVerifier

    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    published, frozen, port = captures[0]
    verifier = HistoricalStudyVerifier(broker)
    from tests.production.test_capture_lifecycle import _PLAN
    verifier.bind(**_PLAN)
    doorway = broker if facade == "direct" else verifier if facade == "verifier" else HistoricalStudyCaptureDriver(verifier)
    started, done = Event(), Event()

    def loser():
        started.set()
        try:
            doorway.capture(published, frozen, port, **{key: val for key, val in runtime.items() if key != "transition_nonces"},
                            transition_nonce="legacy-loser")
        except (TypeError, ValueError):
            return "refused"
        finally:
            done.set()
        return "incorrectly captured"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with broker._p4_ledger._lock:
            future = pool.submit(loser)
            assert started.wait(timeout=5)
            assert not done.is_set()
            broker.authorize_capture_set(captures, graph.selected, **runtime)
        assert future.result(timeout=5) == "refused"
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("point", ["preflight", "prepared", "commit-before", "commit-after", "return"])
def test_legacy_capture_commits_receipt_session_and_claim_in_the_same_ledger(point):
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    ledger = broker._p4_ledger
    ledger._test_fault = "legacy-" + point

    def capture():
        return broker.capture(*captures[0], **{key: val for key, val in runtime.items() if key != "transition_nonces"},
                              transition_nonce="legacy-atomic")

    with pytest.raises(RuntimeError, match="injected P4 legacy-"):
        capture()
    committed = point in ("commit-after", "return")
    assert len(ledger._legacy_captures()) == int(committed)
    assert not ledger._committed()
    if not committed:
        _assert_graph_no_effect(broker, captures, before)
        assert "legacy-atomic" not in broker._nonces
        capture()
    assert len(ledger._legacy_captures()) == 1
    with pytest.raises((TypeError, ValueError)):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    with pytest.raises((TypeError, ValueError)):
        capture()
    assert len([event for event in broker._session_events if event[0] == "start" and event[1] == "consumer-run"]) == 1
    assert [row["event"] for row in broker._receipt_audit(captures[0][0])].count("CAPTURED") == 1
    assert not broker._member_events


def _check_committed_batch(graph, broker, runtime, audit, batch):
    return trust._P4_VERIFY_BYTES(trust._P4_CONTRACT, f4._json_bytes(batch), broker._p4_resolver, graph.selected,
        audit["streams"], {key: val for key, val in runtime.items() if key != "transition_nonces"}, runtime["transition_nonces"])


def _batch_copy(audit):
    value = {name: [json.loads(raw) for raw in audit[name]] for name in ("ports", "receipts", "bases")}
    return dict(value, set=json.loads(audit["set"]), replay=None if audit["replay"] is None else json.loads(audit["replay"]))


@pytest.mark.parametrize("change", ["domain", "stream", "publication-schema", "publication-digest", "missing", "extra", "noncanonical"])
def test_genesis_preimage_is_exact_nonartifact_domain_separated_identity(committed_replay_batch, change):
    graph, broker, _captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = _batch_copy(audit)
    receipt = batch["receipts"][0]
    genesis = {"schema": "dskit.lifecycle-captured-receipt-genesis/v1", "stream_id": receipt["stream_id"],
        "predecessor_publication_receipt_schema": receipt["predecessor_publication_receipt_schema"],
        "predecessor_publication_receipt_sha256": receipt["predecessor_publication_receipt_sha256"]}
    if change == "domain":
        genesis["schema"] = "dskit.lifecycle-captured-receipt/v2"
    elif change == "stream":
        genesis["stream_id"] = batch["receipts"][1]["stream_id"]
    elif change == "publication-schema":
        genesis["predecessor_publication_receipt_schema"] = "dskit.lifecycle-receipt/v1"
    elif change == "publication-digest":
        genesis["predecessor_publication_receipt_sha256"] = batch["receipts"][1]["predecessor_publication_receipt_sha256"]
    elif change == "missing":
        genesis.pop("stream_id")
    elif change == "extra":
        genesis["sequence"] = 1
    raw = json.dumps(genesis, indent=2).encode() if change == "noncanonical" else f4._json_bytes(genesis)
    receipt["previous_lifecycle_captured_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    receipt.update(_resign_exact(receipt, "lifecycle_captured_receipt_sha256"))
    with pytest.raises((TypeError, ValueError)):
        _check_committed_batch(graph, broker, runtime, audit, batch)
    with pytest.raises((TypeError, ValueError)):
        _factory()(fixture_facts={"artifacts": [{"ref": {"kind": "captured-receipt-genesis", "role": "security-broker",
            "schema": "dskit.lifecycle-captured-receipt-genesis/v1", "sha256": hashlib.sha256(raw).hexdigest()}, "bytes": raw}]})


@pytest.mark.parametrize("sequence", [True, False, 0, 2, -1, "1", 1.0, None])
def test_genesis_first_sequence_is_exact_integer_one(committed_replay_batch, sequence):
    graph, broker, _captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = _batch_copy(audit)
    batch["receipts"][0]["sequence"] = sequence
    if type(sequence) is not float:
        batch["receipts"][0].update(_resign_exact(batch["receipts"][0], "lifecycle_captured_receipt_sha256"))
    with pytest.raises((TypeError, ValueError)):
        _check_committed_batch(graph, broker, runtime, audit, batch)


@pytest.mark.parametrize("kind", ["captured-port", "captured-receipt", "captured-set", "replay-capture-evidence"])
@pytest.mark.parametrize("change", ["missing", "extra", "kind", "study", "ref-kind", "ref-role", "ref-schema", "ref-digest",
                                  "duplicate", "order", "missing-ref", "self", "signature", "revocation", "time", "use"])
def test_emitted_basis_exact_fields_and_refs_are_independently_bound(committed_replay_batch, kind, change):
    graph, broker, _captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = _batch_copy(audit)
    basis = next(item for item in batch["bases"] if item["kind"] == kind)
    assert set(basis) == {"schema", "kind", "study_id", "refs", "issuance_basis_sha256", *_SIGNED_FIELDS}
    if change == "missing":
        basis.pop("kind")
    elif change == "extra":
        basis["unknown"] = "field"
    elif change in ("kind", "study"):
        basis["kind" if change == "kind" else "study_id"] = "substituted"
    elif change.startswith("ref-"):
        field = change.removeprefix("ref-")
        basis["refs"][0]["sha256" if field == "digest" else field] = "d" * 64 if field == "digest" else "unknown"
    elif change == "duplicate":
        basis["refs"].append(dict(basis["refs"][0]))
    elif change == "order":
        basis["refs"].reverse()
    elif change == "missing-ref":
        basis["refs"].pop()
    elif change == "self":
        basis["issuance_basis_sha256"] = "d" * 64
    elif change == "signature":
        basis["signature"] = "ab" * 64
    elif change == "revocation":
        basis["revocation_snapshot_sha256"] = "d" * 64
    elif change == "time":
        basis["issued_at_ms"] = 501
    else:
        basis["key_usage"] = "historical-study-plan"
    if change not in ("self", "signature", "use"):
        basis.update(_resign_exact(basis, "issuance_basis_sha256"))
    with pytest.raises((TypeError, ValueError)):
        _check_committed_batch(graph, broker, runtime, audit, batch)
    assert broker._p4_ledger._audit(record) == audit


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("different_run", [False, True])
def test_two_valid_admissions_with_different_keys_racing_one_live_identity_have_one_winner(replay, different_run):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    graph, document = _complete_signed_graph(replay=replay)
    original = next(value for value in graph.values.values() if value.get("schema") == graph.selected["schema"] and
        value.get("action_execution_admission_sha256", value.get("final_replay_admission_sha256")) == graph.selected["sha256"])
    self_field = "final_replay_admission_sha256" if replay else "action_execution_admission_sha256"
    altered = dict(original, issued_at_ms=101)
    if different_run:
        altered["run_id"] = "another-consumer-run"
        altered["logical_execution_id"] = "another-logical-id"
    altered = _resign_exact(altered, self_field)
    alternate = dict(graph.selected, sha256=altered[self_field])
    graph.facts["artifacts"].append({"ref": alternate, "bytes": f4._json_bytes(altered)})
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    requests = [runtime, dict(runtime, consumer_run_identity=altered["run_id"], transition_nonces=("alternate-nonce",))]
    refs = [graph.selected, alternate]
    # Both contenders independently close before racing; neither is a merely
    # malformed loser masquerading as a concurrency test.
    for ref, request in zip(refs, requests):
        assert broker._p4_resolver.close_admission(broker._p4_resolver.snapshot(), ref,
            (broker, captures, {key: value for key, value in request.items() if key != "transition_nonces"})) is None
    barrier = Barrier(2)

    def contender(index):
        barrier.wait(timeout=5)
        try:
            return broker.authorize_capture_set(captures, refs[index], **requests[index])
        except (TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(broker._p4_ledger._committed()) == 1
    _assert_graph_no_effect(broker, captures, before)


def test_legacy_receipt_writer_cannot_append_a_capture_behind_a_committed_p4_stream():
    _graph, broker, captures, _runtime_value, before, _record, _session = _issue_complete()
    stream = broker._stream_for_published(captures[0][0], "test")
    producer_session = next(iter(broker._sessions.values()))
    with pytest.raises((TypeError, ValueError)):
        broker._append_receipt(broker._stream_pin[stream], "CAPTURED", producer_session, "private-v1-bypass",
                               {"consumer_captured_port": captures[0][2]})
    _assert_graph_no_effect(broker, captures, before)


def _merge_facts(*graphs):
    """Union artifact lists by ref identity so two closures share one resolver."""
    seen = {}
    for graph in graphs:
        for item in graph.facts["artifacts"]:
            ref_bytes = f4._json_bytes(item["ref"])
            previous = seen.get(ref_bytes)
            assert previous is None or previous == item, "fixture WORM identity conflict"
            seen[ref_bytes] = item
    return {"artifacts": list(seen.values())}


def _two_consumer_setup(*, same_document=False):
    """Build one resolver holding two admissions over one shared publication."""
    self_field = "action_execution_admission_sha256"
    document_name_b = "consumer-a" if same_document else "consumer-b"
    graph_a, document_a = _complete_signed_graph(document_name="consumer-a")
    graph_b, document_b = _complete_signed_graph(document_name=document_name_b)
    admission_b = next(value for value in graph_b.values.values()
                       if value.get("schema") == graph_b.selected["schema"]
                       and value.get(self_field) == graph_b.selected["sha256"])
    altered_b = _resign_exact(dict(admission_b, run_id="consumer-b-run",
                                   logical_execution_id="logical/consumer-b"), self_field)
    alternate_b = dict(graph_b.selected, sha256=altered_b[self_field])
    graph_b.facts["artifacts"].append({"ref": alternate_b, "bytes": f4._json_bytes(altered_b)})
    broker = _factory()(fixture_facts=_merge_facts(graph_a, graph_b))
    producer, published, _ = f4._publish(broker)
    broker.end_session(producer)
    return broker, published, graph_a, document_a, document_b, alternate_b


def test_second_distinct_document_captures_the_same_publication():
    broker, published, graph_a, document_a, document_b, alternate_b = _two_consumer_setup()

    frozen_a = broker.freeze_consumer_document(document_a, "consume", "bundle", "synthetic")
    captures_a = ((published, frozen_a, broker.derive_consumer_port(frozen_a)),)
    record_a, session_a = broker.authorize_capture_set(captures_a, graph_a.selected, **_runtime())

    frozen_b = broker.freeze_consumer_document(document_b, "consume", "bundle", "synthetic")
    captures_b = ((published, frozen_b, broker.derive_consumer_port(frozen_b)),)
    runtime_b = dict(_runtime(), consumer_run_identity="consumer-b-run", transition_nonces=("p4-b",))
    record_b, session_b = broker.authorize_capture_set(captures_b, alternate_b, **runtime_b)

    assert record_a is not record_b
    assert session_a is not session_b
    assert session_a._plan_sha256 != session_b._plan_sha256
    assert len(broker._p4_ledger._committed()) == 2


def test_same_document_cannot_capture_the_same_stream_twice():
    broker, published, graph_a, document_a, document_b, alternate_b = _two_consumer_setup(same_document=True)

    frozen_a = broker.freeze_consumer_document(document_a, "consume", "bundle", "synthetic")
    captures_a = ((published, frozen_a, broker.derive_consumer_port(frozen_a)),)
    broker.authorize_capture_set(captures_a, graph_a.selected, **_runtime())

    frozen_b = broker.freeze_consumer_document(document_b, "consume", "bundle", "synthetic")
    captures_b = ((published, frozen_b, broker.derive_consumer_port(frozen_b)),)
    runtime_b = dict(_runtime(), consumer_run_identity="consumer-b-run", transition_nonces=("p4-b",))
    with pytest.raises(ValueError, match="already captured"):
        broker.authorize_capture_set(captures_b, alternate_b, **runtime_b)
    assert len(broker._p4_ledger._committed()) == 1


def test_stream_documents_derivation_returns_the_document_sha():
    broker, published, graph_a, document_a, _document_b, _alternate_b = _two_consumer_setup()
    frozen_a = broker.freeze_consumer_document(document_a, "consume", "bundle", "synthetic")
    captures_a = ((published, frozen_a, broker.derive_consumer_port(frozen_a)),)
    broker.authorize_capture_set(captures_a, graph_a.selected, **_runtime())
    stream = broker._stream_for_published(published, "test")
    documents = broker._p4_ledger._p4_stream_documents(stream)
    assert documents == {frozen_a.document_sha256}
    for value in documents:
        assert len(value) == 64 and all(char in "0123456789abcdef" for char in value)


@pytest.mark.parametrize("slot", ["_kind", "_run_identity", "_ended", "_plan_sha256", "_runtime", "_stream_id", "_locked"])
def test_resolve_refuses_each_single_private_session_slot_substitution(slot):
    graph, broker, captures, runtime, before, record, session = _issue_complete()
    audit = broker._p4_ledger._audit(record)
    object.__setattr__(session, slot, {"forged": "session"} if slot == "_runtime" else "forged")
    with pytest.raises((TypeError, ValueError)):
        broker._p4_ledger.resolve_p4(audit["execution_key"], graph.selected)
    with pytest.raises((TypeError, ValueError)):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert len(broker._p4_ledger._committed()) == 1
    _assert_graph_no_effect(broker, captures, before)


def test_post_return_audit_and_input_aliases_cannot_change_or_remint_the_committed_batch():
    graph, broker, captures, runtime, before, record, session = _issue_complete(count=2)
    ledger = broker._p4_ledger
    audit = ledger._audit(record)
    original = copy.deepcopy(audit)
    audit["admission_ref"]["sha256"] = "f" * 64
    audit["ports"] = ()
    audit["session"] = b"{}"
    assert ledger._audit(record) == original
    alias = broker
    assert alias.authorize_capture_set(captures, dict(graph.selected), **dict(runtime)) == (record, session)
    captures[0][2]["consumer_input"] = "mutated"
    with pytest.raises((TypeError, ValueError)):
        alias.authorize_capture_set(captures, graph.selected, **runtime)
    assert ledger.resolve_p4(original["execution_key"], graph.selected) == (record, session)
    assert ledger._audit(record) == original
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("change", ["missing", "duplicate", "swapped"])
@pytest.mark.parametrize("slot", ["ports", "receipts", "entries", "issued_ports"])
def test_complete_batch_arrays_reject_omission_duplication_and_cross_pce_order(committed_replay_batch, slot, change):
    graph, broker, _captures, runtime, _before, record, _session = committed_replay_batch
    audit = broker._p4_ledger._audit(record)
    batch = _batch_copy(audit)
    values = batch["set"][slot] if slot == "entries" else batch["replay"][slot] if slot == "issued_ports" else batch[slot]
    if change == "missing":
        values.pop()
    elif change == "duplicate":
        values[1] = copy.deepcopy(values[0])
    else:
        values.reverse()
    with pytest.raises((TypeError, ValueError)):
        _check_committed_batch(graph, broker, runtime, audit, batch)


@pytest.mark.parametrize("route", ["member", "read_bytes", "read_text", "require"])
def test_legacy_member_and_require_entire_operation_uses_authority_lock(route):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    broker, _published, _document, frozen, _captured, session, verified = f4._captured_flow()
    member = verified.member("artifacts/bundle.json")
    bindings = broker.captured_bindings(session, frozen, verified, "consume", "locked-consume")
    operation = (lambda: verified.member("artifacts/bundle.json")) if route == "member" else (
        lambda: bindings.require("bundle")) if route == "require" else getattr(member, route)
    started, done = Event(), Event()

    def contender():
        started.set()
        try:
            return operation()
        finally:
            done.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with broker._p4_ledger._lock:
            future = pool.submit(contender)
            assert started.wait(timeout=5)
            # Event wait yields the GIL and establishes a controlled blocked
            # interval; require must not burn _used before acquiring the lock.
            assert not done.wait(timeout=0.05)
            assert not bindings._used and not member._read
        assert future.result(timeout=5) is not None


def test_private_fault_schedule_never_executes_a_caller_object():
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    invoked = []

    class Impostor:
        def __eq__(self, _other):
            invoked.append(True)
            return False

    broker._p4_ledger._test_fault = Impostor()
    with pytest.raises((TypeError, ValueError)):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert not invoked
    _assert_graph_no_effect(broker, captures, before)


def test_unrelated_v1_capture_stays_positive_but_cannot_reuse_p4_session_run():
    _graph, broker, _captures, _runtime_value, _before, record, p4_session = _issue_complete()
    published = f4._foreign_publish(broker)[0]
    _document, frozen = f4._freeze(broker, published)
    port = broker.derive_consumer_port(frozen)
    before = (tuple(broker._session_events), tuple(broker._receipt_audit(published)), frozenset(broker._nonces))
    runtime = {key: val for key, val in _runtime().items() if key != "transition_nonces"}
    with pytest.raises((TypeError, ValueError)):
        broker.capture(published, frozen, port, **runtime, transition_nonce="unrelated-nonce")
    assert (tuple(broker._session_events), tuple(broker._receipt_audit(published)), frozenset(broker._nonces)) == before
    captured, session = broker.capture(published, frozen, port, **dict(runtime, consumer_run_identity="unrelated-v1-consumer"),
                                      transition_nonce="unrelated-nonce")
    assert session is not p4_session
    verified = broker.open_capture(session, captured)
    assert verified.member("artifacts/bundle.json").read_bytes()
    assert broker._p4_ledger._committed() == (record,)
    assert len(broker._p4_ledger._legacy_captures()) == 1


@pytest.mark.parametrize("key", [None, [], "execution", ("study", b"{}", "logical", "run"), ("study", "not-bytes", "logical", "run")])
def test_unknown_outcome_resolution_accepts_only_an_exact_canonical_execution_key(key):
    graph, broker, captures, _runtime_value, before, record, _session = _issue_complete()
    with pytest.raises((TypeError, ValueError)):
        broker._p4_ledger.resolve_p4(key, graph.selected)
    assert broker._p4_ledger._committed() == (record,)
    _assert_graph_no_effect(broker, captures, before)


def test_failed_missing_admission_request_can_retry_the_same_held_valid_snapshot():
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    with pytest.raises((TypeError, ValueError)):
        broker.authorize_capture_set(captures, dict(graph.selected, sha256="e" * 64), **runtime)
    assert not broker._p4_ledger._committed()
    _assert_graph_no_effect(broker, captures, before)
    record, session = broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert broker.authorize_capture_set(captures, graph.selected, **runtime) == (record, session)


@pytest.mark.parametrize("phase", ["reserved", "verified", "commit-before"])
@pytest.mark.parametrize("surface", ["commit-capsule", "authority-dispatch", "attestation", "signer", "contract",
                                    "ledger-lock", "ledger-root", "keyring", "terminal", "generation", "live-port", "frozen-document"])
def test_single_surface_substitution_during_reservation_or_precommit_is_empty(phase, surface, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, RLock
    import sys

    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    ledger = broker._p4_ledger
    paused, resume = Event(), Event()
    fault_code = type(ledger)._fault.__code__

    def trace(frame, event, _arg):
        if event == "call" and frame.f_code is fault_code and frame.f_locals.get("point") == phase:
            paused.set()
            assert resume.wait(timeout=10)
        return None

    def contender():
        # Test-only interpreter scheduling: production receives no callback,
        # alternate lock, trust dependency or callable fault hook.
        sys.settrace(trace)
        try:
            return broker.authorize_capture_set(captures, graph.selected, **runtime)
        finally:
            sys.settrace(None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contender)
        assert paused.wait(timeout=10)
        frozen_restore = None
        try:
            with monkeypatch.context() as patch:
                if surface == "commit-capsule":
                    patch.setattr(trust, "_P4_COMMIT", lambda *args: (None, None))
                elif surface == "authority-dispatch":
                    patch.setattr(type(broker), "authorize_capture_set", lambda *args, **kwargs: (None, None))
                elif surface == "attestation":
                    patch.delitem(trust._P4_ISSUED, broker)
                elif surface in ("signer", "contract"):
                    patch.setattr(trust, "_P4_" + surface.upper(), object())
                elif surface == "ledger-lock":
                    patch.setattr(ledger, "_lock", RLock())
                elif surface == "ledger-root":
                    patch.setattr(ledger, "_root", (("forged",),))
                elif surface == "keyring":
                    patch.setattr(trust._P4_VERIFICATION, "_keyring", object())
                elif surface == "terminal":
                    frozen_restore = (broker._p4_resolver, "_terminal", broker._p4_resolver._terminal)
                    object.__setattr__(broker._p4_resolver, "_terminal", object())
                elif surface == "live-port":
                    patch.setitem(captures[0][2], "consumer_input", "substituted")
                elif surface == "frozen-document":
                    patch.setitem(captures[0][1].source, "substituted", "document")
                else:
                    frozen_restore = (broker._p4_resolver._snapshot, "_generation", broker._p4_resolver._snapshot._generation)
                    object.__setattr__(broker._p4_resolver._snapshot, "_generation", 2)
                resume.set()
                with pytest.raises((TypeError, ValueError)):
                    future.result(timeout=10)
        finally:
            resume.set()
            if frozen_restore is not None:
                object.__setattr__(*frozen_restore)
    assert not ledger._committed() and not ledger._reserving
    _assert_graph_no_effect(broker, captures, before)
    assert broker.authorize_capture_set(captures, graph.selected, **runtime)


@pytest.mark.parametrize("facade", ["direct", "verifier", "driver"])
def test_p4_waits_for_legacy_full_commit_then_refuses_without_its_own_effect(facade):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from dskit.production.verifier import HistoricalStudyCaptureDriver, HistoricalStudyVerifier
    from tests.production.test_capture_lifecycle import _PLAN

    graph, document = _complete_signed_graph()
    broker, captures, runtime, _before = _graph_live(graph, document, 1)
    verifier = HistoricalStudyVerifier(broker)
    verifier.bind(**_PLAN)
    doorway = broker if facade == "direct" else verifier if facade == "verifier" else HistoricalStudyCaptureDriver(verifier)
    started, done = Event(), Event()

    def contender():
        started.set()
        try:
            return broker.authorize_capture_set(captures, graph.selected, **runtime)
        finally:
            done.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with broker._p4_ledger._lock:
            future = pool.submit(contender)
            assert started.wait(timeout=5) and not done.wait(timeout=0.05)
            doorway.capture(*captures[0], **{key: val for key, val in runtime.items() if key != "transition_nonces"},
                            transition_nonce="legacy-lock-winner")
        with pytest.raises((TypeError, ValueError)):
            future.result(timeout=5)
    assert not broker._p4_ledger._committed()
    assert len(broker._p4_ledger._legacy_captures()) == 1
    assert not broker._member_events


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


@pytest.mark.parametrize("field", ["node", "output", "purpose"])
@pytest.mark.parametrize("sibling_first", [False, True])
def test_p6_p4_authorizes_exact_publication_with_same_prefix_sibling(monkeypatch, field, sibling_first):
    graph, document = _complete_signed_graph()
    original_publish = f4._publish

    def publish_selected(broker):
        def sibling():
            session, _, _, _ = f4._p6_identity_siblings(broker, field, ("b",))[0]
            broker.end_session(session)

        if sibling_first:
            sibling()
        result = original_publish(broker)
        if not sibling_first:
            sibling()
        return result

    monkeypatch.setattr(f4, "_publish", publish_selected)
    broker, captures, runtime, _ = _graph_live(graph, document, 1)
    record, session = broker.authorize_capture_set(captures, graph.selected, **runtime)
    audit = broker._p4_ledger._audit(record)
    assert session is not None
    assert len(audit["streams"]) == 1
    publication = broker._receipt_audit(captures[0][0])[-1]
    assert audit["streams"][0] == publication["stream_id"]
    assert publication["producer_run_identity"] == "producer-run"


def test_p6_p4_mid_operation_descriptor_poke_refuses_before_effects(monkeypatch):
    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    original = broker._publication_snapshot
    observed = []

    def snapshot(*args):
        result = original(*args)
        if not observed:
            captures[0][0].descriptor["purpose"] = "changed-after-validation"
            observed.append(True)
        return result

    monkeypatch.setattr(broker, "_publication_snapshot", snapshot)
    with pytest.raises(ValueError):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    assert observed
    assert not broker._p4_ledger._committed()
    _assert_graph_no_effect(broker, captures, before)


def test_p4_record_reads_each_verified_member_once_for_its_committed_session():
    graph, broker, captures, runtime, before, record, session = _issue_complete()
    published, frozen, port = captures[0]
    stream = broker._stream_for_published(published, "test")
    document_sha = port["consumer_document_sha256"]
    audit = broker._p4_ledger._audit(record)
    receipt = json.loads(audit["receipts"][0])
    assert callable(getattr(record, "lifecycle_captured_receipt_sha256", None))
    assert record.lifecycle_captured_receipt_sha256(stream, document_sha) == receipt["lifecycle_captured_receipt_sha256"]
    assert tuple(broker._member_events) == before[2]
    assert callable(getattr(record, "read_member_bytes", None))
    expected = f4._json_bytes({"rows": [{"id": "one", "value": 7}]})
    assert record.read_member_bytes(session, published, document_sha, "artifacts/bundle.json") == expected
    with pytest.raises((TypeError, ValueError)):
        record.read_member_bytes(session, published, document_sha, "artifacts/bundle.json")
    assert len(broker._member_events) == len(before[2]) + 1
    assert record.read_member_bytes(session, published, document_sha, "config.json") == f4._json_bytes({"name": "producer"})
    assert broker.authorize_capture_set(captures, graph.selected, **runtime) == (record, session)


def test_p4_record_read_refuses_foreign_session_document_stream_and_member_before_open():
    _graph, broker, captures, _runtime, before, record, session = _issue_complete()
    published, _frozen, port = captures[0]
    stream = broker._stream_for_published(published, "test")
    document_sha = port["consumer_document_sha256"]
    foreign = object.__new__(trust.CapturedAuthorizationRecord)
    cases = (
        lambda: foreign.lifecycle_captured_receipt_sha256(stream, document_sha),
        lambda: record.lifecycle_captured_receipt_sha256(stream, "f" * 64),
        lambda: record.lifecycle_captured_receipt_sha256("foreign-stream", document_sha),
        lambda: record.read_member_bytes(object(), published, document_sha, "config.json"),
        lambda: record.read_member_bytes(session, published, "f" * 64, "config.json"),
        lambda: record.read_member_bytes(session, published, document_sha, "missing.json"),
    )
    for read in cases:
        with pytest.raises((TypeError, ValueError)):
            read()
    assert tuple(broker._member_events) == before[2]
    assert record.read_member_bytes(session, published, document_sha, "config.json") == f4._json_bytes({"name": "producer"})


def test_p4_record_rechecks_retained_member_digest_before_returning_bytes():
    _graph, broker, captures, _runtime, before, record, session = _issue_complete()
    published, _frozen, port = captures[0]
    descriptor = broker.descriptor(published, purpose="synthetic")
    key = (descriptor["root_ref"], descriptor["snapshot_version"], "config.json")
    broker._provider._storage[key] = b"{}"
    with pytest.raises((TypeError, ValueError)):
        record.read_member_bytes(session, published, port["consumer_document_sha256"], "config.json")
    assert len(broker._member_events) == len(before[2]) + 1
    with pytest.raises((TypeError, ValueError)):
        record.read_member_bytes(session, published, port["consumer_document_sha256"], "config.json")
    assert len(broker._member_events) == len(before[2]) + 1


def test_p4_record_member_read_is_atomic_across_threads_and_aliases():
    from concurrent.futures import ThreadPoolExecutor

    _graph, broker, captures, _runtime, before, record, session = _issue_complete()
    published, _frozen, port = captures[0]
    document_sha = port["consumer_document_sha256"]

    def read():
        try:
            return record.read_member_bytes(session, published, document_sha, "config.json")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: read(), range(2)))
    assert results.count(f4._json_bytes({"name": "producer"})) == 1
    assert results.count(None) == 1
    assert len(broker._member_events) == len(before[2]) + 1


def test_p4_record_read_keys_same_member_name_by_committed_stream():
    _graph, broker, captures, _runtime, before, record, session = _issue_complete(count=2)
    for published, _frozen, port in captures:
        assert record.read_member_bytes(session, published, port["consumer_document_sha256"],
                                        "config.json") == f4._json_bytes({"name": "producer"})
    assert len(broker._member_events) == len(before[2]) + 2


def _synthetic_dataset_grant_fixture():
    """Offline signed fixture bytes for the read-only ADR-0133 verifier."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    authorization = {
        "schema_version": "dskit.dataset-capture-authorization/v1",
        "authorization_id": "fixture-authorization-1",
        "source_ids": ["src:A", "src:B"],
        "scope": {
            "availability_start_ms": 0,
            "availability_end_ms": 1000,
            "source_provenance_sha256": "1" * 64,
        },
        "license_digests": ["2" * 64],
        "event_schema": "dskit.raw-event/v1",
        "media_type": "application/x-ndjson",
        "source_roster_root_sha256": "3" * 64,
        "source_roster_publication_receipt_sha256": "4" * 64,
        "source_roster_policy_sha256": "5" * 64,
        "correction_bust_metadata_sha256": "6" * 64,
        "allow_empty_capture": True,
        "issued_at_ms": 100,
        "not_before_ms": 100,
        "expires_at_ms": 900,
    }
    raw_authorization = f4._json_bytes(authorization)
    authorization_sha256 = hashlib.sha256(raw_authorization).hexdigest()
    snapshot = hashlib.sha256(f4._json_bytes({
        "schema_version": "dskit.synthetic-dataset-revocations/v1",
        "revoked": [],
    })).hexdigest()
    grants = []
    for role in ("G1", "G2"):
        grant = {
            "schema_version": "dskit.dataset-capture-grant/v1",
            "role": role,
            "issuer_key_id": "synthetic-" + role.lower() + "/dataset-capture/v1",
            "authorization_sha256": authorization_sha256,
            "issued_at_ms": 100,
            "not_before_ms": 100,
            "expires_at_ms": 900,
            "revocation_snapshot_sha256": snapshot,
        }
        preimage = f4._json_bytes(grant)
        seed = hashlib.sha256(("dskit.synthetic-dataset-grant/" + role + "/v1").encode()).digest()
        grant["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(preimage).hex()
        grants.append(f4._json_bytes(grant))
    return raw_authorization, *grants


def test_adr133_read_only_dual_grant_verifier_accepts_exact_signed_bytes():
    cls = getattr(trust, "NonAuthorizingSyntheticGrantVerifier", None)
    assert cls is not None, "ADR-0133 read-only verifier is missing"
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    result = cls().verify(authorization, g1, g2)
    assert result["authorization_sha256"] == hashlib.sha256(authorization).hexdigest()
    assert result["checked_at_ms"] == 500
    assert result["deployment_eligible"] is False
    assert result["authorizing"] is False
    with pytest.raises(TypeError):
        result["authorizing"] = True


def _resign_synthetic_grant(value):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    body = {key: item for key, item in value.items() if key != "signature"}
    role = body["role"]
    seed = hashlib.sha256(("dskit.synthetic-dataset-grant/" + role + "/v1").encode()).digest()
    body["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(
        f4._json_bytes(body)
    ).hex()
    return f4._json_bytes(body)


@pytest.mark.parametrize("change", [
    "auth-whitespace", "auth-duplicate-key", "auth-extra-key", "auth-id-space",
    "source-id-space", "source-id-duplicate", "source-id-type",
    "scope-bool", "license-uppercase", "window-bool", "grant-swapped",
    "grant-role", "grant-key", "grant-extra-key", "grant-signature",
    "grant-stale-snapshot", "grant-digest",
])
def test_adr133_refuses_substituted_or_noncanonical_grants(change):
    cls = getattr(trust, "NonAuthorizingSyntheticGrantVerifier")
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    changed_authorization = False
    if change == "auth-whitespace":
        authorization += b" "
        changed_authorization = True
    elif change == "auth-duplicate-key":
        authorization = authorization[:-1] + b',"schema_version":"dskit.dataset-capture-authorization/v1"}'
        changed_authorization = True
    elif change.startswith(("auth-", "source-")) or change in (
        "scope-bool", "license-uppercase", "window-bool",
    ):
        value = json.loads(authorization)
        if change == "auth-extra-key":
            value["unexpected"] = True
        elif change == "auth-id-space":
            value["authorization_id"] = " bad id "
        elif change == "source-id-space":
            value["source_ids"] = ["bad id"]
        elif change == "source-id-duplicate":
            value["source_ids"] = ["src:A", "src:A"]
        elif change == "source-id-type":
            value["source_ids"] = ["src:A", 7]
        elif change == "scope-bool":
            value["scope"]["availability_start_ms"] = True
        elif change == "license-uppercase":
            value["license_digests"] = ["A" * 64]
        elif change == "window-bool":
            value["expires_at_ms"] = True
        authorization = f4._json_bytes(value)
        changed_authorization = True
    elif change == "grant-swapped":
        g1, g2 = g2, g1
    else:
        value = json.loads(g1)
        if change == "grant-role":
            value["role"] = "G2"
        elif change == "grant-key":
            value["issuer_key_id"] = "synthetic-g2/dataset-capture/v1"
        elif change == "grant-extra-key":
            value["unexpected"] = True
        elif change == "grant-signature":
            value["signature"] = ("0" if value["signature"][0] != "0" else "1") + value["signature"][1:]
        elif change == "grant-stale-snapshot":
            value["revocation_snapshot_sha256"] = "a" * 64
        elif change == "grant-digest":
            value["authorization_sha256"] = "b" * 64
        g1 = f4._json_bytes(value) if change == "grant-signature" else _resign_synthetic_grant(value)
    if changed_authorization:
        auth_digest = hashlib.sha256(authorization).hexdigest()
        left, right = json.loads(g1), json.loads(g2)
        left["authorization_sha256"] = auth_digest
        right["authorization_sha256"] = auth_digest
        g1, g2 = _resign_synthetic_grant(left), _resign_synthetic_grant(right)
    with pytest.raises((TypeError, ValueError)):
        cls().verify(authorization, g1, g2)


def test_adr133_rechecks_revocation_and_never_spends_or_mints(monkeypatch):
    verifier = trust.NonAuthorizingSyntheticGrantVerifier()
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    first = verifier.verify(authorization, g1, g2)
    second = verifier.verify(authorization, g1, g2)
    assert first == second and first is not second
    assert not any(hasattr(first, name) for name in (
        "consume", "mint", "read_member", "publish", "authorize",
    ))
    assert not hasattr(verifier, "sign")
    monkeypatch.setattr(trust, "_SYNTHETIC_GRANT_REVOKED",
                        frozenset({"fixture-authorization-1"}))
    with pytest.raises(ValueError):
        verifier.verify(authorization, g1, g2)


def test_adr133_production_facade_reexports_same_read_only_verifier():
    from dskit.production import verifier as production_verifier

    assert production_verifier.NonAuthorizingSyntheticGrantVerifier is (
        trust.NonAuthorizingSyntheticGrantVerifier
    )


@pytest.mark.parametrize("window", ("at-not-before", "expired", "future"))
def test_adr133_enforces_trusted_now_boundaries_with_valid_signatures(window):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    value = json.loads(authorization)
    if window == "at-not-before":
        value["not_before_ms"] = 500
    elif window == "expired":
        value["expires_at_ms"] = 500
    else:
        value["not_before_ms"] = 501
    authorization = f4._json_bytes(value)
    digest = hashlib.sha256(authorization).hexdigest()
    grants = []
    for raw in (g1, g2):
        grant = json.loads(raw)
        grant["authorization_sha256"] = digest
        for name in ("issued_at_ms", "not_before_ms", "expires_at_ms"):
            grant[name] = value[name]
        grants.append(_resign_synthetic_grant(grant))
    verifier = trust.NonAuthorizingSyntheticGrantVerifier()
    if window == "at-not-before":
        assert verifier.verify(authorization, *grants)["checked_at_ms"] == 500
    else:
        with pytest.raises(ValueError):
            verifier.verify(authorization, *grants)


def test_adr133_refuses_current_revocation_even_with_matching_signed_snapshot(monkeypatch):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    monkeypatch.setattr(trust, "_SYNTHETIC_GRANT_REVOKED",
                        frozenset({"fixture-authorization-1"}))
    snapshot = hashlib.sha256(f4._json_bytes({
        "schema_version": "dskit.synthetic-dataset-revocations/v1",
        "revoked": ["fixture-authorization-1"],
    })).hexdigest()
    grants = []
    for raw in (g1, g2):
        grant = json.loads(raw)
        grant["revocation_snapshot_sha256"] = snapshot
        grants.append(_resign_synthetic_grant(grant))
    with pytest.raises(ValueError, match="revocation|revoked"):
        trust.NonAuthorizingSyntheticGrantVerifier().verify(authorization, *grants)


def _synthetic_fixture_attestation(authorization, g2):
    """Offline G2 signature over exact synthetic fixture member commitments."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    auth = json.loads(authorization)
    grant = json.loads(g2)
    members = [
        {
            "member_name": "fixture_A.ndjson",
            "source_id": "src:A",
            "byte_length": 12,
            "sha256": hashlib.sha256(b"fixture-a\n").hexdigest(),
        },
        {
            "member_name": "fixture_B.ndjson",
            "source_id": "src:B",
            "byte_length": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        },
    ]
    body = {
        "schema_version": "dskit.synthetic-dataset-fixture-attestation/v1",
        "issuer_key_id": "synthetic-g2/dataset-fixture-attestation/v1",
        "authorization_sha256": hashlib.sha256(authorization).hexdigest(),
        "issued_at_ms": auth["issued_at_ms"],
        "not_before_ms": auth["not_before_ms"],
        "expires_at_ms": auth["expires_at_ms"],
        "revocation_snapshot_sha256": grant["revocation_snapshot_sha256"],
        "ordered_members": members,
    }
    seed = hashlib.sha256(
        b"dskit.synthetic-dataset-fixture-attestation/G2/v1"
    ).digest()
    body["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(
        f4._json_bytes(body)
    ).hex()
    return f4._json_bytes(body)


def test_adr134_signed_fixture_commitments_are_read_only_and_exact():
    cls = getattr(trust, "NonAuthorizingSyntheticFixtureVerifier", None)
    assert cls is not None, "ADR-0134 fixture verifier is missing"
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    result = cls().verify(authorization, g1, g2, attestation)
    assert result["authorization_sha256"] == hashlib.sha256(authorization).hexdigest()
    assert result["attestation_sha256"] == hashlib.sha256(attestation).hexdigest()
    assert result["checked_at_ms"] == 500
    assert result["authorizing"] is False
    assert result["deployment_eligible"] is False
    assert tuple(row["source_id"] for row in result["ordered_members"]) == (
        "src:A", "src:B",
    )
    with pytest.raises(TypeError):
        result["authorizing"] = True
    with pytest.raises(TypeError):
        result["ordered_members"][0]["source_id"] = "src:evil"
    assert not any(hasattr(result, name) for name in (
        "read_member", "consume", "publish", "mint", "authorize",
    ))
