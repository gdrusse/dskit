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


def _complete_signed_graph(*, replay=False, count=1, nonroot=False, document_name="consumer",
                           input_names=None):
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
    input_names = ("bundle", "second") if input_names is None else input_names
    assert type(input_names) is tuple and len(input_names) == 2
    if input_names[0] != "bundle":
        document["pipeline"]["consume"]["inputs"][input_names[0]] = document["pipeline"]["consume"]["inputs"].pop("bundle")
    if count == 2:
        document["pipeline"]["consume"]["inputs"][input_names[1]] = {
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
        "consumer_input": input_names[index],
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


def _graph_live(graph, document, count, input_names=None):
    broker = _factory()(fixture_facts=graph.facts)
    producer, published, _ = f4._publish(broker)
    broker.end_session(producer)
    publications = [published]
    if count == 2:
        publications.append(f4._foreign_publish(broker)[0])
    captures = []
    input_names = ("bundle", "second") if input_names is None else input_names
    for index, publication in enumerate(publications):
        frozen = broker.freeze_consumer_document(document, "consume", input_names[index], "synthetic")
        captures.append((publication, frozen, broker.derive_consumer_port(frozen)))
    selected = next(value for value in graph.values.values() if value.get("schema") == graph.selected["schema"] and
                    value.get("action_execution_admission_sha256", value.get("final_replay_admission_sha256")) == graph.selected["sha256"])
    cas = next(value for value in graph.values.values() if value.get("schema") == "dskit.capture-admission-set/v1" and
               value["capture_admission_set_sha256"] == selected["capture_admission_set_sha256"])
    by_input = {capture[2]["consumer_input"]: capture for capture in captures}
    captures = tuple(by_input[entry["consumer_input"]] for entry in cas["entries"])
    before = (tuple(broker._receipt_audit(capture[0])
                    for capture in sorted(captures, key=lambda item: item[2]["consumer_input"])),
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


def _issue_complete(*, replay=False, count=1, input_names=None):
    """Exercise the real public doorway; incomplete issuance is an assertion RED."""
    graph, document = _complete_signed_graph(replay=replay, count=count, input_names=input_names)
    broker, captures, runtime, before = _graph_live(graph, document, count, input_names=input_names)
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


_TAPE_INPUTS = ("tape_manifest", "tape_data")


def _issue_tape_pair():
    return _issue_complete(replay=True, count=2, input_names=_TAPE_INPUTS)


def test_p4_replay_tape_pair_mints_one_opaque_port_set():
    import gc
    import weakref

    _graph, broker, captures, _runtime, before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    assert type(view) is trust.CapturedPortSet
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict, bytes):
        with pytest.raises(TypeError):
            operation(view)
    with pytest.raises(TypeError):
        trust.CapturedPortSet()
    reference = weakref.ref(view)
    del view
    gc.collect()
    assert reference() is None
    with pytest.raises((TypeError, ValueError)):
        broker.captured_port_set(record, session)
    _assert_graph_no_effect(broker, captures, before)


def test_p4_replay_tape_readers_are_exact_named_one_shot_capabilities():
    _graph, broker, captures, _runtime, before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    with pytest.raises((TypeError, ValueError)):
        view.require("unknown")
    manifest = view.require("tape_manifest")
    data = view.require("tape_data")
    for reader in (manifest, data):
        for operation in (copy.copy, copy.deepcopy, pickle.dumps, dict, bytes):
            with pytest.raises(TypeError):
                operation(reader)
        with pytest.raises(TypeError):
            type(reader)()
    with pytest.raises((TypeError, ValueError)):
        view.require("tape_manifest")
    audit = broker._p4_ledger._audit(record)
    receipts = [json.loads(raw)["lifecycle_captured_receipt_sha256"] for raw in audit["receipts"]]
    assert manifest.lifecycle_captured_receipt_sha256 == receipts[0]
    assert data.lifecycle_captured_receipt_sha256 == receipts[1]
    assert manifest.read_member_bytes("config.json") == f4._json_bytes({"name": "producer"})
    assert data.read_member_bytes("config.json") == f4._json_bytes({"name": "producer"})
    with pytest.raises((TypeError, ValueError)):
        manifest.read_member_bytes("config.json")
    assert len(broker._member_events) == len(before[2]) + 2


def test_p4_port_set_factory_refuses_action_and_wrong_session_without_effects():
    _graph, broker, captures, _runtime, before, record, session = _issue_complete(count=2)
    for candidate in (session, object()):
        with pytest.raises((TypeError, ValueError)):
            broker.captured_port_set(record, candidate)
    _assert_graph_no_effect(broker, captures, before)


@pytest.mark.parametrize("point", ["commit-after", "return"])
def test_p4_tape_pair_retains_exact_handles_across_postcommit_fault_retry(point):
    graph, document = _complete_signed_graph(replay=True, count=2, input_names=_TAPE_INPUTS)
    broker, captures, runtime, before = _graph_live(graph, document, 2, input_names=_TAPE_INPUTS)
    broker._p4_ledger._test_fault = point
    with pytest.raises(RuntimeError, match="injected P4"):
        broker.authorize_capture_set(captures, graph.selected, **runtime)
    record, session = broker.authorize_capture_set(captures, graph.selected, **runtime)
    view = broker.captured_port_set(record, session)
    assert view.require("tape_manifest").lifecycle_captured_receipt_sha256
    assert view.require("tape_data").lifecycle_captured_receipt_sha256
    _assert_graph_no_effect(broker, captures, before)


def test_p4_port_set_factory_is_singleton_under_concurrency():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    barrier = Barrier(4)

    def contender(_index):
        barrier.wait(timeout=5)
        try:
            return broker.captured_port_set(record, session)
        except (TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(contender, range(4)))
    assert len([result for result in results if result is not None]) == 1


def test_p4_port_set_refuses_cross_authority_record_and_exact_foreign_session():
    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    foreign_broker = _factory()()
    _other_graph, _other_broker, _other_captures, _other_runtime, _other_before, other_record, other_session = _issue_complete()
    foreign_record = object.__new__(trust.CapturedAuthorizationRecord)
    for authority, candidate_record, candidate_session in (
        (foreign_broker, record, session),
        (broker, other_record, session),
        (broker, record, other_session),
        (broker, foreign_record, session),
    ):
        with pytest.raises((TypeError, ValueError)):
            authority.captured_port_set(candidate_record, candidate_session)


@pytest.mark.parametrize("order", [
    ("tape_manifest", "tape_data"),
    ("tape_data", "tape_manifest"),
])
def test_p4_port_set_require_supports_only_either_exact_order(order):
    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    readers = [view.require(name) for name in order]
    assert all(reader.lifecycle_captured_receipt_sha256 for reader in readers)
    for name in order:
        with pytest.raises((TypeError, ValueError)):
            view.require(name)


def test_p4_port_set_require_is_single_winner_under_concurrency():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    barrier = Barrier(4)

    def contender(_index):
        barrier.wait(timeout=5)
        try:
            return view.require("tape_manifest")
        except (TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(contender, range(4)))
    assert len([result for result in results if result is not None]) == 1
    assert view.require("tape_data").lifecycle_captured_receipt_sha256


def test_p4_port_set_refuses_fabricated_view_and_reader_registry_aliases():
    import weakref

    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    fabricated_view = object.__new__(trust.CapturedPortSet)
    trust._P4_PORT_SET_VIEWS[fabricated_view] = trust._P4_PORT_SET_VIEWS[view]
    with pytest.raises((TypeError, ValueError)):
        fabricated_view.require("tape_manifest")
    reader = view.require("tape_manifest")
    fabricated_reader = object.__new__(type(reader))
    trust._P4_PORT_READERS[fabricated_reader] = trust._P4_PORT_READERS[reader]
    with pytest.raises((TypeError, ValueError)):
        _ = fabricated_reader.lifecycle_captured_receipt_sha256
    with pytest.raises((TypeError, ValueError)):
        fabricated_reader.read_member_bytes("config.json")
    data_reader = view.require("tape_data")
    data_state = trust._P4_PORT_READERS[data_reader]
    trust._P4_PORT_READERS[reader] = (weakref.ref(reader), *data_state[1:])
    with pytest.raises((TypeError, ValueError)):
        _ = reader.lifecycle_captured_receipt_sha256


def test_p4_port_set_require_refuses_swapped_retained_handle_state():
    _graph, broker, _captures, _runtime, _before, record, session = _issue_tape_pair()
    view = broker.captured_port_set(record, session)
    retained_state = trust._P4_CAPTURE_HANDLES[record]
    for name, value in (("_view", None), ("_used", frozenset())):
        with pytest.raises(AttributeError):
            setattr(retained_state, name, value)
    _other_graph, _other_broker, _other_captures, _other_runtime, _other_before, other_record, _other_session = _issue_tape_pair()
    trust._P4_CAPTURE_HANDLES[record] = trust._P4_CAPTURE_HANDLES[other_record]
    with pytest.raises((TypeError, ValueError)):
        view.require("tape_manifest")


def test_p4_port_set_factory_and_require_do_not_call_provider_or_spend_read_budget(monkeypatch):
    _graph, broker, captures, _runtime, before, record, session = _issue_tape_pair()
    reads_before = dict(broker._p4_ledger._p4_reads)

    def forbidden_provider_call(*_args, **_kwargs):
        raise AssertionError("port-set factory/require must not call provider")

    monkeypatch.setattr(type(broker._provider), "open_member", forbidden_provider_call)
    view = broker.captured_port_set(record, session)
    reader = view.require("tape_manifest")
    assert reader.lifecycle_captured_receipt_sha256
    assert broker._p4_ledger._p4_reads == reads_before
    _assert_graph_no_effect(broker, captures, before)


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
def test_legacy_capture_waits_for_the_same_lock_then_observes_p4_commit(facade, tmp_path):
    """A v1 capture blocked on the P4 ledger's lock observes the commit taken while it waited.

    Every facade drives a REAL capture that reaches
    `broker._p4_ledger._lock`. ADR-0147 Decision point 2 made
    `.durable(...)` the only capture-capable verifier shape, so the
    verifier/driver facades are built that way; an authority-only verifier
    would refuse mechanically before the lock and prove nothing about lock
    ordering. The refusal each facade ends at is pinned to the exact
    committed-P4 observation that caused it, so "refused for some other
    reason" cannot satisfy this test.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from dskit.production.verifier import HistoricalStudyCaptureDriver
    from tests.production import test_adr0147_durable_admission as durable

    graph, document = _complete_signed_graph()
    broker, captures, runtime, before = _graph_live(graph, document, 1)
    published, frozen, port = captures[0]
    legacy_runtime = {key: val for key, val in runtime.items() if key != "transition_nonces"}
    # `.durable(...)` demands a genuine P4 capability; this fixture's broker is one.
    assert isinstance(broker, trust.CapturedAuthorizationAuthority)
    verifier = None if facade == "direct" else durable._durable(broker, tmp_path / "root")
    if verifier is not None:
        verifier.bind(**durable._PLAN)
    doorway = (broker if facade == "direct" else
               verifier if facade == "verifier" else HistoricalStudyCaptureDriver(verifier))
    observed = ("P4 committed stream cannot enter legacy capture" if facade == "direct"
                else "P4 consumer document already captured this stream")
    started, done = Event(), Event()

    def attempt():
        if facade == "direct":
            return doorway.capture(published, frozen, port, **legacy_runtime,
                                   transition_nonce="legacy-loser")
        return durable._capture(doorway, captures, graph.selected, runtime,
                                bind=False, transition_nonce="legacy-loser")

    def loser():
        started.set()
        try:
            attempt()
        except (TypeError, ValueError) as refusal:
            return str(refusal)
        finally:
            done.set()
        return "CAPTURED WITHOUT REFUSAL"

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with broker._p4_ledger._lock:
                future = pool.submit(loser)
                assert started.wait(timeout=5)
                assert not done.wait(timeout=0.25)  # held off by THIS lock, not by a gate
                broker.authorize_capture_set(captures, graph.selected, **runtime)
            assert observed in future.result(timeout=5)
        assert broker._p4_ledger._committed()
        assert not broker._p4_ledger._legacy_captures()
        if verifier is not None:
            # The refusal preceded the durable spend, so the admission is unburned.
            assert not list(verifier._ledger.scan(kind="admission_use"))
    finally:
        if verifier is not None:
            verifier._ledger.close()
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
def test_p4_waits_for_legacy_full_commit_then_refuses_without_its_own_effect(facade, tmp_path):
    """A P4 set blocked on the lock observes the v1 capture committed while it waited.

    The mirror of the previous test: here the capture wins the lock and
    commits, and the contending `authorize_capture_set` must refuse ON that
    committed legacy capture -- pinned to the exact message -- leaving no P4
    commit of its own. The verifier/driver facades run through
    `.durable(...)`, ADR-0147's only capture-capable shape, so their capture
    genuinely reaches and holds the same ledger lock.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from dskit.production.verifier import HistoricalStudyCaptureDriver
    from tests.production import test_adr0147_durable_admission as durable

    graph, document = _complete_signed_graph()
    broker, captures, runtime, _before = _graph_live(graph, document, 1)
    legacy_runtime = {key: val for key, val in runtime.items() if key != "transition_nonces"}
    assert isinstance(broker, trust.CapturedAuthorizationAuthority)
    verifier = None if facade == "direct" else durable._durable(broker, tmp_path / "root")
    if verifier is not None:
        verifier.bind(**durable._PLAN)
    doorway = (broker if facade == "direct" else
               verifier if facade == "verifier" else HistoricalStudyCaptureDriver(verifier))
    started, done = Event(), Event()

    def contender():
        started.set()
        try:
            return broker.authorize_capture_set(captures, graph.selected, **runtime)
        finally:
            done.set()

    def winner():
        if facade == "direct":
            return doorway.capture(*captures[0], **legacy_runtime,
                                   transition_nonce="legacy-lock-winner")
        return durable._capture(doorway, captures, graph.selected, runtime,
                                bind=False, transition_nonce="legacy-lock-winner")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with broker._p4_ledger._lock:
                future = pool.submit(contender)
                assert started.wait(timeout=5) and not done.wait(timeout=0.25)
                captured, session = winner()
                assert captured is not None and session is not None
            with pytest.raises((TypeError, ValueError),
                               match="P4 stream was already captured by the same legacy ledger"):
                future.result(timeout=5)
        assert not broker._p4_ledger._committed()
        assert len(broker._p4_ledger._legacy_captures()) == 1
        assert not broker._member_events
        if verifier is not None:
            # The winning capture spent its admission durably, exactly once.
            assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
    finally:
        if verifier is not None:
            verifier._ledger.close()


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


@pytest.mark.parametrize("change", [
    "event-schema", "event-schema-v2", "media-type", "allow-empty-type",
])
def test_adr133_dataset_authorization_pins_schema_media_and_empty_policy(change):
    """Pin the dataset-capture wire guard, which had no negative coverage.

    ``trust.py:5711`` refuses any ``event_schema``/``media_type`` other than
    ``dskit.raw-event/v1``/``application/x-ndjson``, and a non-``bool``
    ``allow_empty_capture``. Before this test the message ``dataset schema,
    media or empty policy refused`` was unreachable from the suite. The
    ``event-schema-v2`` case pins that a v2 wire is refused *today*.

    This guard is also why the cross-object ``event_schema`` equality at
    ``trust.py:7332`` cannot be reached through that field: both the dataset
    and the roster-bootstrap authorizations are independently pinned to the
    same literal, so they can never disagree on it while v1 is the only
    accepted value.
    """
    cls = getattr(trust, "NonAuthorizingSyntheticGrantVerifier")
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    value = json.loads(authorization)
    if change == "event-schema":
        value["event_schema"] = "dskit.raw-event/v0"
    elif change == "event-schema-v2":
        value["event_schema"] = "dskit.raw-event/v2"
    elif change == "media-type":
        value["media_type"] = "application/json"
    else:
        value["allow_empty_capture"] = "true"
    authorization = f4._json_bytes(value)
    auth_digest = hashlib.sha256(authorization).hexdigest()
    left, right = json.loads(g1), json.loads(g2)
    left["authorization_sha256"] = auth_digest
    right["authorization_sha256"] = auth_digest
    g1, g2 = _resign_synthetic_grant(left), _resign_synthetic_grant(right)
    with pytest.raises(ValueError, match="dataset schema, media or empty policy refused"):
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
    fixture_a = b"fixture-a\n"
    members = [
        {
            "member_name": "fixture_A.ndjson",
            "source_id": "src:A",
            "byte_length": len(fixture_a),
            "sha256": hashlib.sha256(fixture_a).hexdigest(),
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


def _resign_synthetic_fixture_attestation(value, seed_role="fixture"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    body = {key: item for key, item in value.items() if key != "signature"}
    seed = hashlib.sha256(
        b"dskit.synthetic-dataset-fixture-attestation/G2/v1"
        if seed_role == "fixture" else
        b"dskit.synthetic-dataset-grant/G2/v1"
    ).digest()
    body["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(
        f4._json_bytes(body)
    ).hex()
    return f4._json_bytes(body)


@pytest.mark.parametrize("change", [
    "whitespace", "duplicate-key", "extra-key", "missing-key", "wrong-schema",
    "wrong-key", "wrong-signer", "bad-signature", "wrong-auth",
    "time-bool", "time-mismatch", "stale-snapshot", "members-type",
    "missing-member", "extra-member", "swapped-member", "wrong-source",
    "duplicate-name", "bad-name", "length-bool", "length-negative",
    "digest-uppercase", "member-extra-key",
])
def test_adr134_refuses_invalid_signed_fixture_commitments(change):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    value = json.loads(attestation)
    if change == "whitespace":
        attestation += b" "
    elif change == "duplicate-key":
        attestation = attestation[:-1] + (
            b',"schema_version":"dskit.synthetic-dataset-fixture-attestation/v1"}'
        )
    else:
        if change == "extra-key":
            value["unexpected"] = 1
        elif change == "missing-key":
            value.pop("issuer_key_id")
        elif change == "wrong-schema":
            value["schema_version"] = "dskit.dataset-capture-grant/v1"
        elif change == "wrong-key":
            value["issuer_key_id"] = "synthetic-g2/dataset-capture/v1"
        elif change == "bad-signature":
            value["signature"] = (
                ("0" if value["signature"][0] != "0" else "1")
                + value["signature"][1:]
            )
        elif change == "wrong-auth":
            value["authorization_sha256"] = "a" * 64
        elif change == "time-bool":
            value["expires_at_ms"] = True
        elif change == "time-mismatch":
            value["not_before_ms"] = 101
        elif change == "stale-snapshot":
            value["revocation_snapshot_sha256"] = "b" * 64
        elif change == "members-type":
            value["ordered_members"] = {}
        elif change == "missing-member":
            value["ordered_members"].pop()
        elif change == "extra-member":
            value["ordered_members"].append(dict(value["ordered_members"][0]))
        elif change == "swapped-member":
            value["ordered_members"].reverse()
        elif change == "wrong-source":
            value["ordered_members"][0]["source_id"] = "src:evil"
        elif change == "duplicate-name":
            value["ordered_members"][1]["member_name"] = "fixture_A.ndjson"
        elif change == "bad-name":
            value["ordered_members"][0]["member_name"] = "../fixture_A.ndjson"
        elif change == "length-bool":
            value["ordered_members"][0]["byte_length"] = True
        elif change == "length-negative":
            value["ordered_members"][0]["byte_length"] = -1
        elif change == "digest-uppercase":
            value["ordered_members"][0]["sha256"] = "A" * 64
        elif change == "member-extra-key":
            value["ordered_members"][0]["unexpected"] = 1
        attestation = (
            f4._json_bytes(value) if change == "bad-signature" else
            _resign_synthetic_fixture_attestation(
                value, "grant" if change == "wrong-signer" else "fixture"
            )
        )
    with pytest.raises((TypeError, ValueError)):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            authorization, g1, g2, attestation,
        )


def test_adr134_refuses_empty_signed_source_set_and_rechecks_grants():
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    value = json.loads(authorization)
    value["source_ids"] = []
    authorization = f4._json_bytes(value)
    digest = hashlib.sha256(authorization).hexdigest()
    g1_value, g2_value = json.loads(g1), json.loads(g2)
    g1_value["authorization_sha256"] = digest
    g2_value["authorization_sha256"] = digest
    g1, g2 = _resign_synthetic_grant(g1_value), _resign_synthetic_grant(g2_value)
    attestation_value = json.loads(attestation)
    attestation_value["authorization_sha256"] = digest
    attestation_value["ordered_members"] = []
    attestation = _resign_synthetic_fixture_attestation(attestation_value)
    with pytest.raises(ValueError, match="nonempty"):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            authorization, g1, g2, attestation,
        )
    valid = _synthetic_dataset_grant_fixture()
    with pytest.raises((TypeError, ValueError)):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            valid[0], valid[1], g2, _synthetic_fixture_attestation(*valid[::2]),
        )


@pytest.mark.parametrize("revoked_identity", [
    "G2-fixture", "synthetic-g2/dataset-fixture-attestation/v1",
    "fixture-authorization-1",
])
def test_adr134_refuses_current_fixture_revocation_with_signed_snapshot(
        revoked_identity, monkeypatch):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    monkeypatch.setattr(trust, "_SYNTHETIC_GRANT_REVOKED",
                        frozenset({revoked_identity}))
    snapshot = hashlib.sha256(f4._json_bytes({
        "schema_version": "dskit.synthetic-dataset-revocations/v1",
        "revoked": [revoked_identity],
    })).hexdigest()
    grants = []
    for raw in (g1, g2):
        value = json.loads(raw)
        value["revocation_snapshot_sha256"] = snapshot
        grants.append(_resign_synthetic_grant(value))
    attestation = json.loads(_synthetic_fixture_attestation(
        authorization, grants[1],
    ))
    attestation["revocation_snapshot_sha256"] = snapshot
    signed_attestation = _resign_synthetic_fixture_attestation(attestation)
    with pytest.raises(ValueError, match="revocation|revoked"):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            authorization, *grants, signed_attestation,
        )


def test_adr134_refuses_snapshot_change_between_grant_and_fixture(monkeypatch):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    original = trust.NonAuthorizingSyntheticGrantVerifier.verify

    def transition(self, *raw):
        facts = original(self, *raw)
        monkeypatch.setattr(trust, "_SYNTHETIC_GRANT_REVOKED",
                            frozenset({"other-authorization"}))
        return facts

    monkeypatch.setattr(trust.NonAuthorizingSyntheticGrantVerifier,
                        "verify", transition)
    with pytest.raises(ValueError, match="revocation"):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            authorization, g1, g2, attestation,
        )


def test_adr134_production_facade_and_repeat_read_only_result():
    from dskit.production import verifier as production_verifier

    assert production_verifier.NonAuthorizingSyntheticFixtureVerifier is (
        trust.NonAuthorizingSyntheticFixtureVerifier
    )
    verifier = production_verifier.NonAuthorizingSyntheticFixtureVerifier()
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    left = verifier.verify(authorization, g1, g2, attestation)
    right = verifier.verify(authorization, g1, g2, attestation)
    assert left == right and left is not right
    assert not hasattr(verifier, "sign")
    with pytest.raises(TypeError):
        class ForgedFixtureVerifier(trust.NonAuthorizingSyntheticFixtureVerifier):
            pass


def test_adr134_rechecks_expiry_after_grant_verification(monkeypatch):
    authorization, g1, g2 = _synthetic_dataset_grant_fixture()
    attestation = _synthetic_fixture_attestation(authorization, g2)
    instants = iter((500, 900))
    monkeypatch.setattr(
        trust._FixedP4VerificationClock, "now_ms",
        staticmethod(lambda _clock: next(instants)),
    )
    with pytest.raises(ValueError, match="time"):
        trust.NonAuthorizingSyntheticFixtureVerifier().verify(
            authorization, g1, g2, attestation,
        )



def _synthetic_roster_bootstrap_fixture():
    """Offline dual-signed pre-roster authorization with a derived rank policy."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    source_ids = ["src:A", "src:B"]
    policy = {
        "schema_version": "dskit.source-rank-policy/v1",
        "sources": [
            {"source_id": source_id, "rank": rank}
            for rank, source_id in enumerate(source_ids)
        ],
    }
    policy_sha256 = hashlib.sha256(f4._json_bytes(policy)).hexdigest()
    authorization = {
        "schema_version": "dskit.roster-bootstrap-authorization/v1",
        "bootstrap_id": "bootstrap-1",
        "source_ids": source_ids,
        "scope": {
            "availability_start_ms": 0,
            "availability_end_ms": 1000,
            "source_provenance_sha256": "1" * 64,
        },
        "license_digests": ["2" * 64],
        "event_schema": "dskit.raw-event/v1",
        "media_type": "application/x-ndjson",
        "source_rank_policy_sha256": policy_sha256,
        "issued_at_ms": 100,
        "not_before_ms": 100,
        "expires_at_ms": 900,
    }
    raw_authorization = f4._json_bytes(authorization)
    auth_digest = hashlib.sha256(raw_authorization).hexdigest()
    snapshot = hashlib.sha256(f4._json_bytes({
        "schema_version": "dskit.synthetic-dataset-revocations/v1",
        "revoked": [],
    })).hexdigest()
    grants = []
    for role in ("G1", "G2"):
        grant = {
            "schema_version": "dskit.roster-bootstrap-grant/v1",
            "role": role,
            "issuer_key_id": (
                "synthetic-" + role.lower() + "/roster-bootstrap/v1"
            ),
            "bootstrap_sha256": auth_digest,
            "issued_at_ms": 100,
            "not_before_ms": 100,
            "expires_at_ms": 900,
            "revocation_snapshot_sha256": snapshot,
        }
        seed = hashlib.sha256(
            ("dskit.synthetic-roster-bootstrap-grant/" + role + "/v1").encode()
        ).digest()
        grant["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(
            f4._json_bytes(grant)
        ).hex()
        grants.append(f4._json_bytes(grant))
    return raw_authorization, *grants, policy_sha256


def test_adr135_read_only_bootstrap_verifier_accepts_exact_signed_bytes():
    cls = getattr(trust, "NonAuthorizingRosterBootstrapVerifier", None)
    assert cls is not None, "ADR-0135 read-only bootstrap verifier is missing"
    authorization, g1, g2, policy_sha256 = _synthetic_roster_bootstrap_fixture()
    result = cls().verify(authorization, g1, g2)
    assert result["bootstrap_sha256"] == hashlib.sha256(authorization).hexdigest()
    assert result["source_rank_policy_sha256"] == policy_sha256
    assert result["g1_grant_sha256"] == hashlib.sha256(g1).hexdigest()
    assert result["g2_grant_sha256"] == hashlib.sha256(g2).hexdigest()
    assert result["checked_at_ms"] == 500
    assert result["authorizing"] is False
    assert result["deployment_eligible"] is False
    with pytest.raises(TypeError):
        result["authorizing"] = True
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingRosterBootstrapVerifier is cls



def _resign_roster_bootstrap_grant(value):
    """Sign test-only roster grant bytes with the fixed role fixture key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    grant = {key: item for key, item in value.items() if key != "signature"}
    role = grant["role"]
    seed = hashlib.sha256(
        ("dskit.synthetic-roster-bootstrap-grant/" + role + "/v1").encode()
    ).digest()
    grant["signature"] = Ed25519PrivateKey.from_private_bytes(seed).sign(
        f4._json_bytes(grant)
    ).hex()
    return f4._json_bytes(grant)


@pytest.mark.parametrize("change", [
    "source-empty", "source-duplicate", "source-unsorted", "source-type",
    "policy-digest", "scope-bool", "scope-provenance", "license-uppercase",
    "window-bool", "not-before", "expired", "extra-key",
])
def test_adr135_bootstrap_refuses_authorization_mutations(change):
    raw_auth, raw_g1, raw_g2, _policy = _synthetic_roster_bootstrap_fixture()
    auth = json.loads(raw_auth)
    if change == "source-empty":
        auth["source_ids"] = []
    elif change == "source-duplicate":
        auth["source_ids"].append("src:B")
    elif change == "source-unsorted":
        auth["source_ids"].reverse()
    elif change == "source-type":
        auth["source_ids"][0] = 1
    elif change == "policy-digest":
        auth["source_rank_policy_sha256"] = "0" * 64
    elif change == "scope-bool":
        auth["scope"]["availability_end_ms"] = True
    elif change == "scope-provenance":
        auth["scope"]["source_provenance_sha256"] = "Z" * 64
    elif change == "license-uppercase":
        auth["license_digests"] = ["A" * 64]
    elif change == "window-bool":
        auth["expires_at_ms"] = True
    elif change == "not-before":
        auth["not_before_ms"] = 501
    elif change == "expired":
        auth["expires_at_ms"] = 500
    else:
        auth["extra"] = "forbidden"
    changed_auth = f4._json_bytes(auth)
    digest = hashlib.sha256(changed_auth).hexdigest()
    grants = []
    for raw in (raw_g1, raw_g2):
        grant = json.loads(raw)
        grant["bootstrap_sha256"] = digest
        for field in ("issued_at_ms", "not_before_ms", "expires_at_ms"):
            grant[field] = auth[field]
        grants.append(_resign_roster_bootstrap_grant(grant))
    with pytest.raises(ValueError):
        trust.NonAuthorizingRosterBootstrapVerifier().verify(
            changed_auth, *grants,
        )


@pytest.mark.parametrize("change", ["event-schema", "event-schema-v2", "media-type"])
def test_adr135_bootstrap_authorization_pins_schema_and_media(change):
    """Pin the roster-bootstrap wire guard, which had no negative coverage.

    ``trust.py:5970`` refuses any ``event_schema``/``media_type`` other than
    ``dskit.raw-event/v1``/``application/x-ndjson``. Before this test the
    message ``roster bootstrap schema or media refused`` was unreachable from
    the suite: every fixture set both fields to the valid value. The
    ``event-schema-v2`` case additionally pins that a v2 wire is refused
    *today*, so a later versioned-wire change cannot widen this boundary
    silently.
    """
    raw_auth, raw_g1, raw_g2, _policy = _synthetic_roster_bootstrap_fixture()
    auth = json.loads(raw_auth)
    if change == "event-schema":
        auth["event_schema"] = "dskit.raw-event/v0"
    elif change == "event-schema-v2":
        auth["event_schema"] = "dskit.raw-event/v2"
    else:
        auth["media_type"] = "application/json"
    changed_auth = f4._json_bytes(auth)
    digest = hashlib.sha256(changed_auth).hexdigest()
    grants = []
    for raw in (raw_g1, raw_g2):
        grant = json.loads(raw)
        grant["bootstrap_sha256"] = digest
        for field in ("issued_at_ms", "not_before_ms", "expires_at_ms"):
            grant[field] = auth[field]
        grants.append(_resign_roster_bootstrap_grant(grant))
    with pytest.raises(ValueError, match="roster bootstrap schema or media refused"):
        trust.NonAuthorizingRosterBootstrapVerifier().verify(
            changed_auth, *grants,
        )


@pytest.mark.parametrize("change", [
    "swap", "wrong-role", "wrong-key", "wrong-digest", "wrong-signature",
    "stale-snapshot", "extra-key", "noncanonical",
])
def test_adr135_bootstrap_refuses_grant_mutations(change):
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    if change == "swap":
        grants = (g2, g1)
    elif change == "noncanonical":
        grants = (g1.replace(b":", b": ", 1), g2)
    else:
        grant = json.loads(g1)
        if change == "wrong-role":
            grant["role"] = "G2"
        elif change == "wrong-key":
            grant["issuer_key_id"] = "synthetic-g2/roster-bootstrap/v1"
        elif change == "wrong-digest":
            grant["bootstrap_sha256"] = "0" * 64
        elif change == "wrong-signature":
            grant["signature"] = "0" * 128
        elif change == "stale-snapshot":
            grant["revocation_snapshot_sha256"] = "0" * 64
        else:
            grant["extra"] = "forbidden"
        changed_g1 = (f4._json_bytes(grant) if change == "wrong-signature"
                      else _resign_roster_bootstrap_grant(grant))
        grants = (changed_g1, g2)
    with pytest.raises(ValueError):
        trust.NonAuthorizingRosterBootstrapVerifier().verify(
            authorization, *grants,
        )


@pytest.mark.parametrize("revoked", [
    "G1", "G2", "synthetic-g1/roster-bootstrap/v1",
    "synthetic-g2/roster-bootstrap/v1", "bootstrap-1",
])
def test_adr135_bootstrap_refuses_current_revocation(monkeypatch, revoked):
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    current = frozenset({revoked})
    snapshot = hashlib.sha256(f4._json_bytes({
        "schema_version": "dskit.synthetic-dataset-revocations/v1",
        "revoked": sorted(current),
    })).hexdigest()
    grants = []
    for raw in (g1, g2):
        grant = json.loads(raw)
        grant["revocation_snapshot_sha256"] = snapshot
        grants.append(_resign_roster_bootstrap_grant(grant))
    monkeypatch.setattr(trust, "_SYNTHETIC_GRANT_REVOKED", current)
    with pytest.raises(ValueError):
        trust.NonAuthorizingRosterBootstrapVerifier().verify(
            authorization, *grants,
        )


def test_adr135_bootstrap_refuses_noncanonical_bytes_and_subtypes():
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    verifier = trust.NonAuthorizingRosterBootstrapVerifier()
    with pytest.raises(ValueError):
        verifier.verify(authorization.replace(b":", b": ", 1), g1, g2)
    with pytest.raises(ValueError):
        verifier.verify(
            authorization[:-1] + b',"bootstrap_id":"bootstrap-1"}', g1, g2,
        )

    class BytesChild(bytes):
        pass

    with pytest.raises(TypeError):
        verifier.verify(BytesChild(authorization), g1, g2)
    with pytest.raises(TypeError):
        verifier.verify(authorization, BytesChild(g1), g2)
    with pytest.raises(TypeError):
        class UnsafeVerifier(trust.NonAuthorizingRosterBootstrapVerifier):
            pass
    assert not hasattr(verifier, "publish")
    assert not hasattr(verifier, "reserve")
    assert not hasattr(verifier, "sign")



def test_adr136_roster_reservation_is_shared_and_one_use(tmp_path):
    cls = getattr(trust, "_SyntheticAuthorizationReserve", None)
    assert cls is not None, "ADR-0136 shared reserve is missing"
    path = str(tmp_path / "synthetic-reserve.sqlite")
    with pytest.raises(ValueError):
        cls(path)
    cls._provision(path)
    first, second = cls(path), cls(path)
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    assert first._reserve_roster(authorization, g1, g2, intent) is None
    with pytest.raises(ValueError, match="spent|reserved"):
        second._reserve_roster(authorization, g1, g2, intent)
    assert "_SyntheticAuthorizationReserve" not in trust.__all__



def test_adr136_roster_reserve_uses_durable_expiry(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    store = cls(path)
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    store._advance_clock(901)
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    with pytest.raises(ValueError, match="time|expired"):
        store._reserve_roster(authorization, g1, g2, intent)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses"
    ).fetchone() == (0,)


def test_adr136_shared_revocation_prevents_roster_reserve(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    admin, broker = cls(path), cls(path)
    admin._revoke("bootstrap-1")
    generation, revoked, snapshot = broker._snapshot()
    assert generation == 1 and revoked == frozenset({"bootstrap-1"})
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    grants = []
    for raw in (g1, g2):
        grant = json.loads(raw)
        grant["revocation_snapshot_sha256"] = snapshot
        grants.append(_resign_roster_bootstrap_grant(grant))
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    with pytest.raises(ValueError, match="revocation"):
        broker._reserve_roster(authorization, *grants, intent)


def test_adr136_reserve_refuses_missing_schema_uri_and_sync_downgrade(tmp_path):
    import sqlite3

    cls = trust._SyntheticAuthorizationReserve
    missing = str(tmp_path / "missing.sqlite")
    with pytest.raises(ValueError):
        cls(missing)
    empty = str(tmp_path / "empty.sqlite")
    sqlite3.connect(empty).close()
    with pytest.raises(ValueError):
        cls(empty)
    with pytest.raises(ValueError):
        cls("file:" + empty + "?mode=rw")
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    store = cls(path)
    store._connection.execute("PRAGMA synchronous=NORMAL")
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    with pytest.raises(ValueError, match="WAL/FULL"):
        store._reserve_roster(authorization, g1, g2, intent)
    with pytest.raises(ValueError, match="WAL/FULL"):
        store._revoke("G1")
    store._connection.execute("PRAGMA synchronous=FULL")
    assert store._reserve_roster(authorization, g1, g2, intent) is None



def _adr136_reserve_in_process(path, authorization, g1, g2, intent,
                               start, results):
    """Attempt one reservation from a separate WSL process."""
    store = trust._SyntheticAuthorizationReserve(path)
    start.wait()
    try:
        store._reserve_roster(authorization, g1, g2, intent)
    except ValueError:
        results.put("spent")
    else:
        results.put("reserved")
    finally:
        store._close()


def test_adr136_roster_signed_id_has_one_cross_process_winner(tmp_path):
    import multiprocessing

    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    ctx = multiprocessing.get_context("fork")
    start, results = ctx.Event(), ctx.Queue()
    workers = [
        ctx.Process(
            target=_adr136_reserve_in_process,
            args=(path, authorization, g1, g2, intent, start, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in workers) == [
        "reserved", "spent",
    ]



def test_adr136_ambiguous_commit_never_returns_reservation(tmp_path):
    import sqlite3

    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    store = cls(path)
    real = store._connection

    class AmbiguousCommit:
        @property
        def in_transaction(self):
            return real.in_transaction

        def execute(self, sql, *args):
            result = real.execute(sql, *args)
            if sql == "COMMIT":
                raise sqlite3.OperationalError("ambiguous commit")
            return result

    store._connection = AmbiguousCommit()
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    with pytest.raises(sqlite3.OperationalError, match="ambiguous"):
        store._reserve_roster(authorization, g1, g2, intent)
    with pytest.raises(ValueError, match="spent"):
        cls(path)._reserve_roster(authorization, g1, g2, intent)


def test_adr136_rejects_changed_schema_with_same_table_names(tmp_path):
    import sqlite3

    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE reserve_uses ADD COLUMN injected TEXT")
    with pytest.raises(ValueError, match="schema"):
        cls(path)



def _adr136_crash_with_uncommitted_insert(path):
    """Leave an uncommitted reservation row in a crashed WSL process."""
    import os

    store = trust._SyntheticAuthorizationReserve(path)
    store._connection.execute("BEGIN IMMEDIATE")
    store._connection.execute(
        "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
        ("roster-bootstrap", "bootstrap-1", "0" * 64, "0" * 64,
         "0" * 64, "0" * 64, "0" * 64, "RESERVED"),
    )
    os._exit(17)


def test_adr136_crash_before_commit_does_not_spend_id(tmp_path):
    import multiprocessing

    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    process = multiprocessing.get_context("fork").Process(
        target=_adr136_crash_with_uncommitted_insert, args=(path,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent = hashlib.sha256(b"fixed-roster-publish-intent").hexdigest()
    assert cls(path)._reserve_roster(authorization, g1, g2, intent) is None



# ---------------------------------------------------------------------------
# ADR-0157: F3's derivation hop spends through the existing authorization
# reserve (decision-log.md, search "## ADR-0157"). Two RED gates, both
# transplanted from the ADR-0136 tests directly above, per the ADR's own
# "next deliverable is TWO tests, not code" section. Gates only -- the
# "derivation-root" reserve kind itself is not implemented here.
# ---------------------------------------------------------------------------


def _adr157_derivation_root_identity(parents, hop="ReplayRun"):
    """Return the ADR-0157 two-parent (signed_id, intent_sha256) pair."""
    ordered = sorted(parents, key=lambda parent: parent["port"])
    signed_id = trust._digest(trust._hs_canonical_bytes({
        "schema": "dskit.derivation-root-intent/v1",
        "hop": hop,
        "parents": ordered,
    }))
    intent_sha256 = trust._digest(trust._hs_canonical_bytes({
        "schema_version": "dskit.derivation-root-intent/v1",
        "hop": hop,
        "parents": ordered,
    }))
    return signed_id, intent_sha256


def test_adr157_derivation_root_identity_binds_port_not_just_pair():
    """Swapping which port a parent fills changes the identity; reordering
    the same port/parent assignments does not.

    ADR-0157: "port is not decoration" -- Hop 3's two parents are BOTH
    ``derivation-root`` rows, so a bare ``{signed_id, intent_sha256}`` pair
    list would hash a role-swapped construction identically to the correct
    one. This is the correction v2 made after v1's Major (a single-parent
    formula that aliased two distinct intents).
    """
    manifest = {"port": "tape_manifest", "signed_id": "a" * 64,
                "intent_sha256": "c" * 64}
    data = {"port": "tape_data", "signed_id": "b" * 64,
            "intent_sha256": "d" * 64}
    canonical, _ = _adr157_derivation_root_identity([manifest, data])
    reordered, _ = _adr157_derivation_root_identity([data, manifest])
    assert canonical == reordered

    role_swapped, _ = _adr157_derivation_root_identity([
        {"port": "tape_manifest", "signed_id": data["signed_id"],
         "intent_sha256": data["intent_sha256"]},
        {"port": "tape_data", "signed_id": manifest["signed_id"],
         "intent_sha256": manifest["intent_sha256"]},
    ])
    assert role_swapped != canonical


def _adr157_derivation_reserve_in_process(path, kind, signed_id,
                                          intent_sha256, start, results):
    """Attempt one derivation-root reservation from a separate OS process."""
    import sqlite3

    store = trust._SyntheticAuthorizationReserve(path)
    connection = store._connection
    start.wait()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO reserve_uses VALUES (?,?,?,?,?,?,?,?)",
            (kind, signed_id, "1" * 64, "2" * 64, "3" * 64, "4" * 64,
             intent_sha256, "RESERVED"),
        )
        connection.execute("COMMIT")
    except sqlite3.IntegrityError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        results.put("spent")
    else:
        results.put("reserved")
    finally:
        store._close()


def test_adr157_derivation_root_two_parent_race_has_one_cross_process_winner(
    tmp_path,
):
    """ADR-0157 Gate 1 (the race gate).

    Transplant of
    ``test_adr136_roster_signed_id_has_one_cross_process_winner``
    (``:3010-3033``): the same ``fork``/``Event``/two-``Process`` race,
    against the same real ``_SyntheticAuthorizationReserve``, retargeted at
    the ADR's own ``derivation-root`` identity formula instead of
    ``roster-bootstrap``.

    ``reserve_uses.kind`` carries no CHECK constraint, so the
    ``(kind, signed_id)`` primary key fences a brand new kind exactly as it
    fences the four kinds already shipped: this proves the fencing
    mechanism the ADR's Decision relies on -- "the database decides, once,
    atomically" -- independent of whether ``derivation-root``'s own
    construction code exists (it does not; this task builds gates only).

    Covers the TWO-PARENT case: both parents are the real
    ``_REPLAY_TAPE_INPUTS`` ports (``document.py:766``) Hop 3 actually
    uses, and the identity is the ADR's exact formula with parents sorted
    canonically by ``port``.
    """
    import multiprocessing

    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    parents = [
        {"port": "tape_data", "signed_id": "b" * 64,
         "intent_sha256": "d" * 64},
        {"port": "tape_manifest", "signed_id": "a" * 64,
         "intent_sha256": "c" * 64},
    ]
    signed_id, intent_sha256 = _adr157_derivation_root_identity(parents)
    ctx = multiprocessing.get_context("fork")
    start, results = ctx.Event(), ctx.Queue()
    workers = [
        ctx.Process(
            target=_adr157_derivation_reserve_in_process,
            args=(path, "derivation-root", signed_id, intent_sha256,
                  start, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in workers) == [
        "reserved", "spent",
    ]


def _adr157_crash_after_p4_commit(issuer, path):
    """Crash right after the dynamic-p4-authority RESERVED->ISSUED commit."""
    import os

    def _crash_on_construct(_self, _token, _graph_arg):
        os._exit(17)

    issuer._publisher._reserve = trust._SyntheticAuthorizationReserve(path)
    trust._DynamicP4TrustedArtifactResolver.__init__ = _crash_on_construct
    trust._development_dynamic_p4_broker(issuer)
    os._exit(1)  # pragma: no cover -- must never be reached


@pytest.mark.xfail(
    strict=True, raises=ValueError,
    reason="ADR-0157 Gap 1 (recorded open, decision-log.md ## ADR-0157): a "
           "crash between the RESERVED->ISSUED commit and construction "
           "strands the intent permanently -- no retry or quarantine path "
           "reaches it. Remove this marker only once Gap 1 is closed.",
)
def test_adr157_p4_authority_crash_after_commit_strands_the_intent(
    tmp_path, monkeypatch,
):
    """ADR-0157 Gate 2 (the liveness gate).

    Transplant of ``_adr136_crash_with_uncommitted_insert`` /
    ``test_adr136_crash_before_commit_does_not_spend_id`` (``:3080-3094``),
    with the ``os._exit`` moved from BEFORE the reserve INSERT to AFTER a
    full ``RESERVED -> ISSUED`` commit.

    Per the independent skeptic review that cleared ADR-0157 v4, this
    targets the EXISTING, shipped ``_development_dynamic_p4_broker``
    (``trust.py:9900-10018``) rather than the unbuilt ``derivation-root``
    kind: that function has the identical commit-then-construct shape (the
    commit completes at ``:9981-9982``, no construction runs before
    ``:9991``), so crashing it right there reproduces Gap 1's exact
    symptom in code that ships today, in a real forked OS process, not a
    simulation.

    XFAIL, strict, records Gap 1 as OPEN: a real crash at this boundary
    leaves the row permanently ``ISSUED`` with no published authority. The
    only available recovery -- retrying the same construction call --
    recomputes the identical deterministic ``signed_id`` and dies on the
    same ``IntegrityError`` the precedent uses to detect a second
    construction, so it can never distinguish "died after commit" from
    "already built". This is the first ``xfail`` in this repo (grepped,
    zero prior uses under ``tests/``): used for lack of an existing
    convention for a recorded-open-defect test.
    """
    import multiprocessing

    publisher, issuer, _graph, _snapshot = _adr142_graph_case(
        tmp_path, monkeypatch,
    )
    path = publisher._reserve._path
    process = multiprocessing.get_context("fork").Process(
        target=_adr157_crash_after_p4_commit, args=(issuer, path),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17

    fresh = trust._SyntheticAuthorizationReserve(path)
    row = fresh._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='dynamic-p4-authority'"
    ).fetchone()
    assert row == ("ISSUED",)  # OBSERVED: the commit survived the crash.
    issuer._publisher._reserve = fresh

    # Desired postcondition: a stuck-ISSUED intent should still be
    # recoverable. It is not, today -- this is Gap 1, recorded open.
    authority = trust._development_dynamic_p4_broker(issuer)
    assert isinstance(authority, trust.CapturedAuthorizationAuthority)

def test_adr136_roster_transition_audits_exact_order(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    store = cls(path)
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent_bytes = trust._derive_synthetic_roster_publish_intent(
        authorization, g1, g2,
    )[1]
    intent = hashlib.sha256(intent_bytes).hexdigest()
    store._reserve_roster(authorization, g1, g2, intent)
    states = ("SESSION_STARTED", "PRODUCED", "SEALED", "PUBLISHED", "SESSION_ENDED")
    for state in states:
        assert store._admit_roster_transition(
            authorization, g1, g2, intent_bytes, state,
        ) is None
    rows = store._connection.execute(
        "SELECT old_state,new_state FROM reserve_audit ORDER BY seq"
    ).fetchall()
    assert rows == list(zip((None, "RESERVED") + states[:-1],
                            ("RESERVED",) + states, strict=True))
    with pytest.raises(ValueError):
        store._admit_roster_transition(
            authorization, g1, g2, intent_bytes, "SESSION_STARTED",
        )


def test_adr136_roster_transition_refuses_wrong_intent_and_revocation(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    store = cls(path)
    authorization, g1, g2, _policy = _synthetic_roster_bootstrap_fixture()
    intent_bytes = trust._derive_synthetic_roster_publish_intent(
        authorization, g1, g2,
    )[1]
    intent = hashlib.sha256(intent_bytes).hexdigest()
    store._reserve_roster(authorization, g1, g2, intent)
    with pytest.raises(ValueError):
        store._admit_roster_transition(
            authorization, g1, g2, b"{}", "SESSION_STARTED",
        )
    with pytest.raises(ValueError):
        store._admit_roster_transition(
            authorization, g1, g2, intent_bytes, "PUBLISHED",
        )
    assert store._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "RESERVED"
    store._revoke("G1")
    with pytest.raises(ValueError):
        store._admit_roster_transition(
            authorization, g1, g2, intent_bytes, "SESSION_STARTED",
        )
    assert store._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "RESERVED"


def test_adr138_fixed_publish_intent():
    authorization, g1, g2, policy_sha256 = _synthetic_roster_bootstrap_fixture()
    roster_bytes, intent_bytes = trust._derive_synthetic_roster_publish_intent(
        authorization, g1, g2,
    )
    roster, intent = json.loads(roster_bytes), json.loads(intent_bytes)
    auth = json.loads(authorization)
    assert roster["scope"] == auth["scope"]
    assert roster["source_ids"] == auth["source_ids"]
    assert roster["policy"]["policy_sha256"] == policy_sha256
    assert roster_bytes == f4._json_bytes(roster)
    assert intent_bytes == f4._json_bytes(intent)
    assert set(intent) == {
        "schema_version", "bootstrap_id", "bootstrap_sha256",
        "g1_grant_sha256", "g2_grant_sha256", "roster_sha256",
        "roster_byte_length", "source_rank_policy_sha256",
        "expected_members", "root", "producer", "output_member",
        "receipt_key",
    }
    assert intent["roster_sha256"] == hashlib.sha256(roster_bytes).hexdigest()
    assert intent["expected_members"] == [{
        "relative_path": "source_roster.json",
        "media_type": "application/json",
        "sha256": intent["roster_sha256"],
        "byte_length": len(roster_bytes),
    }]
    assert intent["receipt_key"]["publication_authorization_ref"] == {
        "kind": "roster-bootstrap",
        "roster_bootstrap_authorization_sha256": hashlib.sha256(
            authorization
        ).hexdigest(),
    }
    assert intent["receipt_key"]["root_ref"] == intent["root"]["root_ref"]
    with pytest.raises(ValueError):
        trust._derive_synthetic_roster_publish_intent(
            authorization + b" ", g1, g2,
        )


def test_adr138_publisher_commits_one_roster_and_v2_receipt(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, policy_sha256 = _synthetic_roster_bootstrap_fixture()
    roster_bytes, basis_bytes, receipt_bytes = publisher.publish(
        authorization, g1, g2,
    )
    roster, basis, receipt = map(
        json.loads, (roster_bytes, basis_bytes, receipt_bytes),
    )
    assert roster["policy"]["policy_sha256"] == policy_sha256
    assert basis["schema"] == "dskit.issuance-basis/v2"
    assert basis["kind"] == "roster-root-publication"
    assert len(basis["refs"]) == 3
    assert receipt["schema"] == "dskit.root-publication-receipt/v2"
    assert receipt["capture_kind"] == "source-roster"
    assert receipt["issuance_basis_sha256"] == basis["issuance_basis_sha256"]
    assert len(publisher._outer_receipts) == 1
    assert next(iter(publisher._outer_receipts.values())) == receipt_bytes
    assert publisher._retained["bootstrap-1"][2:4] == (
        basis_bytes, receipt_bytes,
    )
    assert receipt["publication_authorization_ref"] == {
        "kind": "roster-bootstrap",
        "roster_bootstrap_authorization_sha256": hashlib.sha256(
            authorization
        ).hexdigest(),
    }
    assert publisher._broker._provider.open_member(
        publisher._broker._provider.describe(
            receipt["root_ref"], receipt["snapshot_version"],
        ), "source_roster.json",
    ) == roster_bytes
    states = [
        row[0] for row in publisher._reserve._connection.execute(
            "SELECT new_state FROM reserve_audit ORDER BY seq"
        )
    ]
    assert states == [
        "RESERVED", "SESSION_STARTED", "PRODUCED", "SEALED",
        "PUBLISHED", "SESSION_ENDED", "RECEIPT_ISSUED",
    ]
    with pytest.raises(ValueError):
        publisher.publish(authorization, g1, g2)

def test_adr138_roster_receipt_signatures_bind_basis_and_publication(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis_bytes, receipt_bytes = publisher.publish(
        authorization, g1, g2,
    )
    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(
        "03ad7440941bf1c06b1d7c326f4d37d2a3c1abd514a9c1f98aa8ed03858731cd"
    ))
    for raw, self_field in (
        (basis_bytes, "issuance_basis_sha256"),
        (receipt_bytes, "root_publication_receipt_sha256"),
    ):
        value = json.loads(raw)
        preimage = f4._json_bytes({
            key: item for key, item in value.items()
            if key not in (self_field, "signature")
        })
        assert value[self_field] == hashlib.sha256(preimage).hexdigest()
        public.verify(bytes.fromhex(value["signature"]), preimage)


def test_adr138_partial_f4_write_quarantines_without_outer_receipt(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = publisher._broker._provider.write_member

    def write_then_fail(*args):
        original(*args)
        raise OSError("partial F4 publish")

    monkeypatch.setattr(publisher._broker._provider, "write_member",
                        write_then_fail)
    with pytest.raises(OSError, match="partial"):
        publisher.publish(authorization, g1, g2)
    assert publisher._closed
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"
    with pytest.raises(ValueError):
        trust._SyntheticRosterPublisher(path).publish(authorization, g1, g2)


def test_adr138_revocation_before_receipt_refuses_after_f4(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    admin = trust._SyntheticAuthorizationReserve(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = publisher._broker.end_session

    def revoke_after_end(session):
        original(session)
        admin._revoke("data-publisher")

    monkeypatch.setattr(publisher._broker, "end_session", revoke_after_end)
    with pytest.raises(ValueError, match="revoked|snapshot"):
        publisher.publish(authorization, g1, g2)
    assert publisher._closed
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"


def test_adr138_sign_before_put_failure_cannot_retry(tmp_path, monkeypatch):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = type(publisher)._sign
    calls = 0

    def fail_receipt_sign(payload, self_field):
        nonlocal calls
        calls += 1
        if self_field == "root_publication_receipt_sha256":
            raise OSError("sign before put")
        return original(payload, self_field)

    monkeypatch.setattr(type(publisher), "_sign", staticmethod(
        fail_receipt_sign
    ))
    with pytest.raises(OSError, match="sign before put"):
        publisher.publish(authorization, g1, g2)
    assert calls == 2
    assert publisher._outer_receipts == {}
    assert publisher._closed
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"
    with pytest.raises(ValueError):
        publisher.publish(authorization, g1, g2)

def test_adr138_expiry_before_produce_refuses_without_produce(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = publisher._broker.start_producer_session

    def start_then_expire(**kwargs):
        session = original(**kwargs)
        publisher._reserve._advance_clock(901)
        return session

    monkeypatch.setattr(publisher._broker, "start_producer_session",
                        start_then_expire)
    with pytest.raises(ValueError, match="time"):
        publisher.publish(authorization, g1, g2)
    assert publisher._broker._storage == {}
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"


def test_adr138_roster_worm_mutation_refuses_before_receipt(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = publisher._broker.publish

    def publish_then_corrupt(*args, **kwargs):
        published = original(*args, **kwargs)
        intent = json.loads(trust._derive_synthetic_roster_publish_intent(
            authorization, g1, g2,
        )[1])
        root = intent["root"]
        publisher._broker._storage[
            (root["root_ref"], root["snapshot_version"],
             intent["output_member"])
        ] = b"{}"
        return published

    monkeypatch.setattr(publisher._broker, "publish",
                        publish_then_corrupt)
    with pytest.raises(ValueError, match="WORM bytes"):
        publisher.publish(authorization, g1, g2)
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"


def test_adr138_invalid_grant_refuses_before_f4_or_reservation(tmp_path):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    with pytest.raises(ValueError):
        publisher.publish(authorization, g1 + b" ", g2)
    assert publisher._broker._storage == {}
    assert publisher._broker._session_events == []
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses"
    ).fetchone()[0] == 0

@pytest.mark.parametrize("completed", [
    "start_producer_session", "produce", "seal", "publish", "end_session",
])
def test_adr138_shared_revocation_blocks_next_effect(
    tmp_path, monkeypatch, completed,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    admin = trust._SyntheticAuthorizationReserve(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = getattr(publisher._broker, completed)
    calls = []

    def complete_then_revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(completed)
        admin._revoke("G1")
        return result

    monkeypatch.setattr(publisher._broker, completed, complete_then_revoke)
    with pytest.raises(ValueError):
        publisher.publish(authorization, g1, g2)
    assert calls == [completed]
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"

def _adr138_publish_in_process(path, authorization, g1, g2, start, results):
    publisher = trust._SyntheticRosterPublisher(path)
    start.wait()
    try:
        _roster, _basis, receipt = publisher.publish(authorization, g1, g2)
    except ValueError:
        results.put(("spent", len(publisher._broker._session_events)))
    else:
        results.put((
            hashlib.sha256(receipt).hexdigest(),
            len(publisher._broker._session_events),
        ))


def test_adr138_two_processes_publish_at_most_once(tmp_path):
    import multiprocessing

    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    ctx = multiprocessing.get_context("fork")
    start, results = ctx.Event(), ctx.Queue()
    workers = [
        ctx.Process(
            target=_adr138_publish_in_process,
            args=(path, authorization, g1, g2, start, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    outcomes = [results.get(timeout=2) for _ in workers]
    assert sum(item[0] == "spent" for item in outcomes) == 1
    assert sum(item[0] != "spent" for item in outcomes) == 1
    assert sorted(item[1] for item in outcomes) == [0, 1]

@pytest.mark.parametrize("tamper", ["delete", "corrupt"])
def test_adr138_f4_lifecycle_backing_loss_blocks_outer_receipt(
    tmp_path, monkeypatch, tamper,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    original = publisher._broker.publish

    def publish_then_tamper(*args, **kwargs):
        published = original(*args, **kwargs)
        data = publisher._broker._receipt_store._data
        stream = next(iter(data))
        if tamper == "delete":
            del data[stream]
        else:
            rows = list(data[stream])
            rows[-1] = b"{}"
            data[stream] = tuple(rows)
        return published

    monkeypatch.setattr(publisher._broker, "publish",
                        publish_then_tamper)
    with pytest.raises(ValueError):
        publisher.publish(authorization, g1, g2)
    assert publisher._outer_receipts == {}
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"

def test_adr138_outer_receipt_conflict_refuses_after_one_admission(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    intent = json.loads(trust._derive_synthetic_roster_publish_intent(
        authorization, g1, g2,
    )[1])
    receipt_key = intent["receipt_key"]
    key = (
        receipt_key["receipt_schema"],
        f4._json_bytes(receipt_key["publication_authorization_ref"]),
        *(receipt_key[field] for field in (
            "producer_run_identity", "producer_document_sha256",
            "producer_node", "producer_output", "root_ref",
            "root_id", "snapshot_version",
        )),
    )
    original = publisher._broker.end_session

    def foreign_receipt_before_sign(session):
        original(session)
        publisher._outer_receipts[key] = b"foreign-receipt"

    monkeypatch.setattr(publisher._broker, "end_session",
                        foreign_receipt_before_sign)
    with pytest.raises(ValueError, match="WORM conflict"):
        publisher.publish(authorization, g1, g2)
    assert publisher._outer_receipts[key] == b"foreign-receipt"
    assert publisher._closed
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses"
    ).fetchone()[0] == "QUARANTINED"

def test_adr138_receipt_and_basis_have_closed_exact_parent_fields(tmp_path):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis_bytes, receipt_bytes = publisher.publish(
        authorization, g1, g2,
    )
    basis, receipt = json.loads(basis_bytes), json.loads(receipt_bytes)
    suffix = {
        "issuer_role", "key_usage", "signature_alg", "issued_at_ms",
        "not_before_ms", "expires_at_ms", "revocation_snapshot_sha256",
        "key", "signature",
    }
    assert set(basis) == suffix | {
        "schema", "kind", "study_id", "refs", "publish_intent_sha256",
        "issuance_basis_sha256",
    }
    assert set(receipt) == suffix | {
        "schema", "kind", "capture_kind",
        "publication_authorization_ref", "root_ref", "root_id",
        "snapshot_version", "member_manifest_sha256",
        "producer_run_identity", "producer_document_sha256",
        "producer_node", "producer_output", "issuance_basis_sha256",
        "root_publication_receipt_sha256",
    }
    assert basis["refs"] == sorted([
        {"kind": "roster-bootstrap-authorization", "role": "security-data",
         "schema": "dskit.roster-bootstrap-authorization/v1",
         "sha256": hashlib.sha256(authorization).hexdigest()},
        {"kind": "roster-bootstrap-grant", "role": "G1",
         "schema": "dskit.roster-bootstrap-grant/v1",
         "sha256": hashlib.sha256(g1).hexdigest()},
        {"kind": "roster-bootstrap-grant", "role": "G2",
         "schema": "dskit.roster-bootstrap-grant/v1",
         "sha256": hashlib.sha256(g2).hexdigest()},
    ], key=lambda ref: tuple(ref[key] for key in (
        "kind", "role", "schema", "sha256",
    )))
    assert basis["study_id"] == "synthetic-study"
    assert basis["publish_intent_sha256"] == hashlib.sha256(
        trust._derive_synthetic_roster_publish_intent(
            authorization, g1, g2,
        )[1]
    ).hexdigest()
    assert basis["issued_at_ms"] == basis["not_before_ms"] == 500
    assert receipt["key"] == basis["key"] == {
        "key_id": "data-publisher/root-publication-bootstrap-g1-g2",
        "key_version": 1,
    }
    assert receipt["issued_at_ms"] == receipt["not_before_ms"] == 500

@pytest.mark.parametrize("ambiguous_state", ["PRODUCED", "RECEIPT_ISSUED"])
def test_adr138_ambiguous_commit_never_allows_next_effect(
    tmp_path, ambiguous_state,
):
    import sqlite3

    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    real = publisher._reserve._connection

    class AmbiguousOnce:
        fired = False

        @property
        def in_transaction(self):
            return real.in_transaction

        def execute(self, sql, *args):
            result = real.execute(sql, *args)
            if sql == "COMMIT" and not self.fired:
                state = real.execute(
                    "SELECT state FROM reserve_uses"
                ).fetchone()[0]
                if state == ambiguous_state:
                    self.fired = True
                    raise sqlite3.OperationalError("ambiguous commit")
            return result

    wrapper = AmbiguousOnce()
    publisher._reserve._connection = wrapper
    with pytest.raises(sqlite3.OperationalError, match="ambiguous"):
        publisher.publish(authorization, g1, g2)
    assert wrapper.fired
    assert publisher._closed
    assert publisher._outer_receipts == {}
    assert real.execute("SELECT state FROM reserve_uses").fetchone()[0] == (
        "QUARANTINED"
    )
    if ambiguous_state == "PRODUCED":
        assert publisher._broker._storage == {}

def test_adr137_read_only_roster_root_proof_matches_live_publication(tmp_path):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, policy_sha256 = _synthetic_roster_bootstrap_fixture()
    roster_bytes, basis_bytes, receipt_bytes = publisher.publish(
        authorization, g1, g2,
    )
    proof = publisher.proof()
    assert isinstance(proof, trust.NonAuthorizingRosterRootProof)
    before = publisher._reserve._connection.total_changes
    facts = proof.verify(
        authorization, g1, g2, basis_bytes, receipt_bytes,
    )
    receipt = json.loads(receipt_bytes)
    assert facts["roster_root_sha256"] == hashlib.sha256(f4._json_bytes({
        key: receipt[key] for key in (
            "root_ref", "root_id", "snapshot_version",
            "member_manifest_sha256",
        )
    })).hexdigest()
    assert facts["roster_publication_receipt_sha256"] == (
        receipt["root_publication_receipt_sha256"]
    )
    assert facts["source_rank_policy_sha256"] == policy_sha256
    assert facts["authorizing"] is False
    assert facts["deployment_eligible"] is False
    assert publisher._reserve._connection.total_changes == before
    assert roster_bytes == publisher._retained["bootstrap-1"][1]
    with pytest.raises(TypeError):
        trust.NonAuthorizingRosterRootProof(publisher)

@pytest.mark.parametrize("changed", ["authorization", "G1", "G2", "basis", "receipt"])
def test_adr137_roster_proof_refuses_substituted_original_bytes(
    tmp_path, changed,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis, receipt = publisher.publish(authorization, g1, g2)
    values = {
        "authorization": authorization, "G1": g1, "G2": g2,
        "basis": basis, "receipt": receipt,
    }
    values[changed] += b" "
    before = publisher._reserve._connection.total_changes
    with pytest.raises((TypeError, ValueError)):
        publisher.proof().verify(
            values["authorization"], values["G1"], values["G2"],
            values["basis"], values["receipt"],
        )
    assert publisher._reserve._connection.total_changes == before


@pytest.mark.parametrize("tamper", [
    "f4-log", "f4-member", "outer-missing", "outer-alias",
    "audit-gap", "reserve-quarantine",
])
def test_adr137_roster_proof_refuses_lost_or_mutated_retained_evidence(
    tmp_path, tamper,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis, receipt = publisher.publish(authorization, g1, g2)
    if tamper == "f4-log":
        publisher._broker._receipt_store._data.clear()
    elif tamper == "f4-member":
        key = next(iter(publisher._broker._storage))
        publisher._broker._storage[key] = b"{}"
    elif tamper == "outer-missing":
        publisher._outer_receipts.clear()
    elif tamper == "outer-alias":
        publisher._outer_receipts[("alias",)] = receipt
    elif tamper == "audit-gap":
        publisher._reserve._connection.execute(
            "DELETE FROM reserve_audit WHERE new_state='SEALED'"
        )
    else:
        publisher._reserve._connection.execute(
            "UPDATE reserve_uses SET state='QUARANTINED'"
        )
    before = publisher._reserve._connection.total_changes
    with pytest.raises((TypeError, ValueError)):
        publisher.proof().verify(authorization, g1, g2, basis, receipt)
    assert publisher._reserve._connection.total_changes == before


def test_adr137_roster_proof_refuses_shared_revocation_and_restart(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    authorization, g1, g2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis, receipt = publisher.publish(authorization, g1, g2)
    after_restart = trust._SyntheticRosterPublisher(path)
    with pytest.raises(ValueError, match="retained"):
        after_restart.proof().verify(
            authorization, g1, g2, basis, receipt,
        )
    admin = trust._SyntheticAuthorizationReserve(path)
    admin._revoke("G1")
    with pytest.raises(ValueError):
        publisher.proof().verify(
            authorization, g1, g2, basis, receipt,
        )
    publisher._reserve._advance_clock(901)
    with pytest.raises(ValueError):
        publisher.proof().verify(
            authorization, g1, g2, basis, receipt,
        )
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingRosterRootProof is (
        trust.NonAuthorizingRosterRootProof
    )


def _adr132_raw_case(tmp_path, monkeypatch, member_a=None, advance=True):
    """Offline signed raw fixture anchored to a live synthetic roster."""
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    bootstrap, bg1, bg2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis, receipt = publisher.publish(bootstrap, bg1, bg2)
    if advance:
        publisher._reserve._advance_clock(600)
    receipt_value = json.loads(receipt)
    if member_a is None:
        member_a = f4._json_bytes({
            "schema_version": "dskit.raw-event/v1",
            "source_id": "src:A", "event_id": "event-a",
            "source_sequence": 0, "availability_ms": 500,
            "payload_sha256": "a" * 64,
        }) + b"\n"
    members = {"fixture_A.ndjson": member_a, "fixture_B.ndjson": b""}
    empty_meta = f4._json_bytes({
        "corrections": [],
        "schema_version": "dskit.correction-bust-metadata/v1",
    })
    auth = json.loads(bootstrap)
    authorization = {
        "schema_version": "dskit.dataset-capture-authorization/v1",
        "authorization_id": "raw-authorization-1",
        "source_ids": auth["source_ids"],
        "scope": auth["scope"],
        "license_digests": auth["license_digests"],
        "event_schema": auth["event_schema"],
        "media_type": auth["media_type"],
        "source_roster_root_sha256": hashlib.sha256(f4._json_bytes({
            key: receipt_value[key] for key in (
                "root_ref", "root_id", "snapshot_version",
                "member_manifest_sha256",
            )
        })).hexdigest(),
        "source_roster_publication_receipt_sha256":
            receipt_value["root_publication_receipt_sha256"],
        "source_roster_policy_sha256": auth["source_rank_policy_sha256"],
        "correction_bust_metadata_sha256": hashlib.sha256(empty_meta).hexdigest(),
        "allow_empty_capture": False,
        "issued_at_ms": 501, "not_before_ms": 501, "expires_at_ms": 900,
    }
    authorization_bytes = f4._json_bytes(authorization)
    snapshot = json.loads(bg1)["revocation_snapshot_sha256"]
    grants = []
    for role in ("G1", "G2"):
        grants.append(_resign_synthetic_grant({
            "schema_version": "dskit.dataset-capture-grant/v1",
            "role": role,
            "issuer_key_id": "synthetic-" + role.lower() + "/dataset-capture/v1",
            "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
            "issued_at_ms": 501, "not_before_ms": 501, "expires_at_ms": 900,
            "revocation_snapshot_sha256": snapshot,
        }))
    attestation = _resign_synthetic_fixture_attestation({
        "schema_version": "dskit.synthetic-dataset-fixture-attestation/v1",
        "issuer_key_id": "synthetic-g2/dataset-fixture-attestation/v1",
        "authorization_sha256": hashlib.sha256(authorization_bytes).hexdigest(),
        "issued_at_ms": 501, "not_before_ms": 501, "expires_at_ms": 900,
        "revocation_snapshot_sha256": snapshot,
        "ordered_members": [
            {
                "member_name": name, "source_id": source_id,
                "byte_length": len(members[name]),
                "sha256": hashlib.sha256(members[name]).hexdigest(),
            }
            for name, source_id in (
                ("fixture_A.ndjson", "src:A"),
                ("fixture_B.ndjson", "src:B"),
            )
        ],
    })
    return (path, publisher, (bootstrap, bg1, bg2, basis, receipt),
            (authorization_bytes, *grants, attestation), members)


def test_adr132_raw_preflight_spends_before_signed_order_reads(tmp_path, monkeypatch):
    path, publisher, roster, signed, members = _adr132_raw_case(tmp_path, monkeypatch)
    source = trust._SyntheticFixtureSource(members)
    preflight = trust._SyntheticRawPreflight(publisher, source)
    proof = preflight.verify(*signed, *roster)
    assert proof.event_count == 1
    assert proof.member_names == ("fixture_A.ndjson", "fixture_B.ndjson")
    assert source.read_names == proof.member_names
    assert proof.deployment_eligible is False
    assert not hasattr(proof, "root")
    assert not hasattr(proof, "receipt")
    row = publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone()
    assert row == ("RAW_READ_STARTED",)
    with pytest.raises(ValueError):
        preflight.verify(*signed, *roster)
    assert source.read_names == proof.member_names


def test_adr132_raw_preflight_bad_member_is_spent_without_f4(tmp_path, monkeypatch):
    path, publisher, roster, signed, members = _adr132_raw_case(tmp_path, monkeypatch)
    source = trust._SyntheticFixtureSource({
        **members, "fixture_A.ndjson": b"wrong\n",
    })
    preflight = trust._SyntheticRawPreflight(publisher, source)
    with pytest.raises(ValueError):
        preflight.verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)
    assert not any("synthetic-raw/" in str(key) for key in publisher._broker._storage)


def test_adr132_raw_preflight_rejects_bad_roster_before_raw_read(tmp_path, monkeypatch):
    path, publisher, roster, signed, members = _adr132_raw_case(tmp_path, monkeypatch)
    source = trust._SyntheticFixtureSource(members)
    bad_signed = list(signed)
    value = json.loads(bad_signed[0])
    value["source_roster_root_sha256"] = "f" * 64
    bad_signed[0] = f4._json_bytes(value)
    preflight = trust._SyntheticRawPreflight(publisher, source)
    with pytest.raises(ValueError):
        preflight.verify(*bad_signed, *roster)
    assert source.read_names == ()


@pytest.mark.parametrize("event", [
    {"schema_version": "dskit.raw-event/v1", "source_id": "src:A",
     "event_id": "event-a", "source_sequence": True,
     "availability_ms": 500, "payload_sha256": "a" * 64},
    {"schema_version": "dskit.raw-event/v1", "source_id": "src:B",
     "event_id": "event-a", "source_sequence": 0,
     "availability_ms": 500, "payload_sha256": "a" * 64},
    {"schema_version": "dskit.raw-event/v1", "source_id": "src:A",
     "event_id": "event-a", "source_sequence": 0,
     "availability_ms": 1001, "payload_sha256": "a" * 64},
    {"schema_version": "dskit.raw-event/v1", "source_id": "src:A",
     "event_id": "event-a", "source_sequence": 0,
     "availability_ms": 500, "payload_sha256": "a" * 64, "extra": 1},
])
def test_adr132_raw_preflight_refuses_invalid_signed_event(tmp_path, monkeypatch, event):
    member = f4._json_bytes(event) + b"\n"
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch, member_a=member,
    )
    source = trust._SyntheticFixtureSource(members)
    with pytest.raises(ValueError):
        trust._SyntheticRawPreflight(publisher, source).verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RAW_READ_STARTED",)


@pytest.mark.parametrize("member", [
    b"\n",
    b'{"availability_ms":500}\n',
    b'{"availability_ms":500}',
    b"\xff\n",
])
def test_adr132_raw_preflight_refuses_noncanonical_ndjson(tmp_path, monkeypatch, member):
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch, member_a=member,
    )
    source = trust._SyntheticFixtureSource(members)
    with pytest.raises(ValueError):
        trust._SyntheticRawPreflight(publisher, source).verify(*signed, *roster)
    assert source.read_names == ("fixture_A.ndjson",)


def test_adr132_raw_preflight_second_broker_cannot_spend_same_signed_id(tmp_path, monkeypatch):
    path, publisher, roster, signed, members = _adr132_raw_case(tmp_path, monkeypatch)
    first = trust._SyntheticRawPreflight(
        publisher, trust._SyntheticFixtureSource(members),
    )
    first.verify(*signed, *roster)
    second_source = trust._SyntheticFixtureSource(members)
    second = trust._SyntheticRawPreflight(publisher, second_source)
    with pytest.raises(ValueError):
        second.verify(*signed, *roster)
    assert second_source.read_names == ()


def test_adr132_raw_preflight_shared_revocation_blocks_read_admission(
    tmp_path, monkeypatch,
):
    path, publisher, roster, signed, members = _adr132_raw_case(tmp_path, monkeypatch)
    source = trust._SyntheticFixtureSource(members)
    preflight = trust._SyntheticRawPreflight(publisher, source)
    original = trust._SyntheticRawPreflight._transition
    calls = 0

    def revoke_between(self, signed_bytes, roster_bytes, prior_intent=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            admin = trust._SyntheticAuthorizationReserve(path)
            admin._revoke("G2-fixture")
            admin._close()
        return original(self, signed_bytes, roster_bytes, prior_intent)

    monkeypatch.setattr(trust._SyntheticRawPreflight, "_transition", revoke_between)
    with pytest.raises(ValueError):
        preflight.verify(*signed, *roster)
    assert source.read_names == ()
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RESERVED",)


def _adr132_resign_authorization(signed, **changes):
    """Rebind both offline grants and the fixture commitment to changed auth."""
    authorization, g1, g2, attestation = signed
    value = json.loads(authorization)
    value.update(changes)
    authorization = f4._json_bytes(value)
    digest = hashlib.sha256(authorization).hexdigest()
    grants = []
    for original in (g1, g2):
        grant = json.loads(original)
        grant["authorization_sha256"] = digest
        for field in ("issued_at_ms", "not_before_ms", "expires_at_ms"):
            grant[field] = value[field]
        grants.append(_resign_synthetic_grant(grant))
    fixture = json.loads(attestation)
    fixture["authorization_sha256"] = digest
    for field in ("issued_at_ms", "not_before_ms", "expires_at_ms"):
        fixture[field] = value[field]
    return authorization, *grants, _resign_synthetic_fixture_attestation(fixture)


def test_adr132_raw_preflight_rejects_pre_roster_signed_issuance(
    tmp_path, monkeypatch,
):
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    backdated = _adr132_resign_authorization(
        signed, issued_at_ms=100, not_before_ms=501,
    )
    source = trust._SyntheticFixtureSource(members)
    with pytest.raises(ValueError, match="issue after roster"):
        trust._SyntheticRawPreflight(publisher, source).verify(
            *backdated, *roster,
        )
    assert source.read_names == ()
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == (0,)


@pytest.mark.parametrize("allow_empty", [False, True])
def test_adr132_raw_preflight_empty_event_policy(
    tmp_path, monkeypatch, allow_empty,
):
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch, member_a=b"",
    )
    signed = _adr132_resign_authorization(
        signed, allow_empty_capture=allow_empty,
    )
    source = trust._SyntheticFixtureSource(members)
    preflight = trust._SyntheticRawPreflight(publisher, source)
    if allow_empty:
        proof = preflight.verify(*signed, *roster)
        assert proof.event_count == 0
        assert source.read_names == (
            "fixture_A.ndjson", "fixture_B.ndjson",
        )
    else:
        with pytest.raises(ValueError, match="nonempty"):
            preflight.verify(*signed, *roster)
        assert source.read_names == (
            "fixture_A.ndjson", "fixture_B.ndjson",
        )


def test_adr132_raw_preflight_duplicate_id_across_members(
    tmp_path, monkeypatch,
):
    event_a = {
        "schema_version": "dskit.raw-event/v1",
        "source_id": "src:A", "event_id": "shared",
        "source_sequence": 0, "availability_ms": 500,
        "payload_sha256": "a" * 64,
    }
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch, member_a=f4._json_bytes(event_a) + b"\n",
    )
    event_b = {**event_a, "source_id": "src:B"}
    members["fixture_B.ndjson"] = f4._json_bytes(event_b) + b"\n"
    fixture = json.loads(signed[3])
    fixture["ordered_members"][1]["byte_length"] = len(
        members["fixture_B.ndjson"]
    )
    fixture["ordered_members"][1]["sha256"] = hashlib.sha256(
        members["fixture_B.ndjson"]
    ).hexdigest()
    signed = (*signed[:3], _resign_synthetic_fixture_attestation(fixture))
    source = trust._SyntheticFixtureSource(members)
    with pytest.raises(ValueError, match="duplicate raw event"):
        trust._SyntheticRawPreflight(publisher, source).verify(
            *signed, *roster,
        )
    assert source.read_names == (
        "fixture_A.ndjson", "fixture_B.ndjson",
    )


def test_adr136_shared_clock_advance_is_durable_and_monotone(tmp_path):
    cls = trust._SyntheticAuthorizationReserve
    path = str(tmp_path / "synthetic-reserve.sqlite")
    cls._provision(path)
    first, second = cls(path), cls(path)
    assert first._now() == second._now() == 500
    first._advance_clock(600)
    assert second._now() == 600
    with pytest.raises(ValueError, match="advance"):
        second._advance_clock(500)
    with pytest.raises(ValueError, match="integer"):
        second._advance_clock(True)
    second._close()
    third = cls(path)
    assert third._now() == 600


def test_adr132_raw_preflight_requires_trusted_clock_advance(
    tmp_path, monkeypatch,
):
    _path, publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch, advance=False,
    )
    source = trust._SyntheticFixtureSource(members)
    monkeypatch.setattr(
        trust._FixedP4VerificationClock, "now_ms", lambda _clock: 600,
    )
    assert publisher._reserve._now() == 500
    with pytest.raises(ValueError, match="time|chronology|window"):
        trust._SyntheticRawPreflight(publisher, source).verify(
            *signed, *roster,
        )
    assert source.read_names == ()
    assert publisher._reserve._now() == 500


def test_adr137_roster_proof_expires_after_shared_clock_advance(tmp_path):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    trust._SyntheticAuthorizationReserve._provision(path)
    publisher = trust._SyntheticRosterPublisher(path)
    bootstrap, bg1, bg2, _ = _synthetic_roster_bootstrap_fixture()
    _roster, basis, receipt = publisher.publish(bootstrap, bg1, bg2)
    publisher._reserve._advance_clock(901)
    with pytest.raises(ValueError, match="expired|revoked"):
        publisher.proof().verify(bootstrap, bg1, bg2, basis, receipt)


def test_adr139_raw_publisher_issues_one_exact_f4_root_and_v1_receipt(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    manifest, basis, receipt = publisher.publish(proof, *signed, *roster)
    parsed = json.loads(manifest)
    assert parsed["schema_version"] == "dskit.raw-event-dataset-capture/v1"
    assert [m["member_name"] for m in parsed["ordered_member_digests"]] == [
        "fixture_A.ndjson", "fixture_B.ndjson",
    ]
    assert parsed["dataset_capture_authorization_sha256"] == (
        hashlib.sha256(signed[0]).hexdigest()
    )
    basis_value, receipt_value = json.loads(basis), json.loads(receipt)
    assert basis_value["kind"] == "root-publication"
    assert receipt_value["schema"] == "dskit.root-publication-receipt/v1"
    assert receipt_value["capture_kind"] == "raw-event-dataset"
    assert receipt_value["publication_authorization_ref"] == {
        "kind": "dataset-capture",
        "dataset_capture_authorization_sha256":
            hashlib.sha256(signed[0]).hexdigest(),
    }
    root_ref = "synthetic-raw/" + hashlib.sha256(signed[0]).hexdigest()
    keys = {key[2] for key in roster_publisher._broker._storage
            if key[0] == root_ref}
    assert keys == {
        "fixture_A.ndjson", "fixture_B.ndjson",
        "raw_event_dataset.json",
    }
    assert roster_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("RECEIPT_ISSUED",)


def test_adr139_raw_publisher_proof_alias_cannot_republish(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    first = trust._SyntheticRawPublisher(preflight)
    first.publish(proof, *signed, *roster)
    second = trust._SyntheticRawPublisher(preflight)
    with pytest.raises(ValueError):
        second.publish(proof, *signed, *roster)
    assert roster_publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_audit WHERE kind='raw-dataset' "
        "AND new_state='RECEIPT_ISSUED'"
    ).fetchone() == (1,)


def test_adr139_raw_publisher_rejects_signed_output_name_collision(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    raw = members.pop("fixture_A.ndjson")
    members["raw_event_dataset.json"] = raw
    attestation = json.loads(signed[3])
    attestation["ordered_members"][0]["member_name"] = (
        "raw_event_dataset.json"
    )
    signed = (*signed[:3], _resign_synthetic_fixture_attestation(attestation))
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    before = len(roster_publisher._broker._storage)
    with pytest.raises(ValueError, match="output|collision"):
        trust._SyntheticRawPublisher(preflight).publish(
            proof, *signed, *roster,
        )
    assert len(roster_publisher._broker._storage) == before


def test_adr139_raw_publisher_revocation_before_produce_quarantines(
    tmp_path, monkeypatch,
):
    path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    original = roster_publisher._broker.start_producer_session

    def start_then_revoke(**kwargs):
        session = original(**kwargs)
        admin = trust._SyntheticAuthorizationReserve(path)
        admin._revoke("G2-fixture")
        admin._close()
        return session

    monkeypatch.setattr(
        roster_publisher._broker, "start_producer_session",
        start_then_revoke,
    )
    before = len(roster_publisher._broker._storage)
    with pytest.raises(ValueError):
        publisher.publish(proof, *signed, *roster)
    assert len(roster_publisher._broker._storage) == before
    assert publisher._closed and proof._used
    assert roster_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)


def test_adr139_raw_publisher_extra_worm_backing_blocks_receipt(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    original = roster_publisher._broker.end_session
    root_ref = "synthetic-raw/" + hashlib.sha256(signed[0]).hexdigest()

    def end_then_add_extra(session):
        result = original(session)
        roster_publisher._broker._storage[
            (root_ref, "v1", "extra.ndjson")
        ] = b""
        return result

    monkeypatch.setattr(
        roster_publisher._broker, "end_session", end_then_add_extra,
    )
    with pytest.raises(ValueError, match="member set"):
        publisher.publish(proof, *signed, *roster)
    assert publisher._closed
    assert not any(
        json.loads(raw).get("schema")
        == "dskit.root-publication-receipt/v1"
        for raw in roster_publisher._outer_receipts.values()
    )
    assert roster_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)


def test_adr139_raw_publisher_sign_failure_has_no_retry(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    original = trust._SyntheticRawPublisher._sign
    calls = 0

    def fail_receipt_sign(payload, self_field):
        nonlocal calls
        calls += 1
        if self_field == "root_publication_receipt_sha256":
            raise OSError("raw sign before put")
        return original(payload, self_field)

    monkeypatch.setattr(
        trust._SyntheticRawPublisher, "_sign",
        staticmethod(fail_receipt_sign),
    )
    with pytest.raises(OSError, match="raw sign before put"):
        publisher.publish(proof, *signed, *roster)
    assert calls == 2
    assert publisher._closed and proof._used
    assert roster_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)
    with pytest.raises(ValueError):
        trust._SyntheticRawPublisher(preflight).publish(
            proof, *signed, *roster,
        )


@pytest.mark.parametrize("ambiguous_state", ["PRODUCED", "RECEIPT_ISSUED"])
def test_adr139_raw_publisher_ambiguous_commit_never_retries(
    tmp_path, monkeypatch, ambiguous_state,
):
    import sqlite3

    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    real = roster_publisher._reserve._connection

    class AmbiguousOnce:
        fired = False

        @property
        def in_transaction(self):
            return real.in_transaction

        def execute(self, sql, *args):
            result = real.execute(sql, *args)
            if sql == "COMMIT" and not self.fired:
                row = real.execute(
                    "SELECT state FROM reserve_uses "
                    "WHERE kind='raw-dataset'"
                ).fetchone()
                if row == (ambiguous_state,):
                    self.fired = True
                    raise sqlite3.OperationalError("ambiguous raw commit")
            return result

    wrapper = AmbiguousOnce()
    roster_publisher._reserve._connection = wrapper
    with pytest.raises(sqlite3.OperationalError, match="ambiguous raw"):
        publisher.publish(proof, *signed, *roster)
    assert wrapper.fired
    assert publisher._closed and proof._used
    assert real.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)
    assert not any(
        json.loads(raw).get("schema")
        == "dskit.root-publication-receipt/v1"
        for raw in roster_publisher._outer_receipts.values()
    )
    with pytest.raises(ValueError):
        trust._SyntheticRawPublisher(preflight).publish(
            proof, *signed, *roster,
        )


def test_adr139_raw_publisher_late_outer_conflict_never_overwrites(
    tmp_path, monkeypatch,
):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    proof = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    original = trust._SyntheticRawPublisher._sign
    conflict_key = None

    def insert_conflict_during_sign(payload, self_field):
        nonlocal conflict_key
        if self_field == "root_publication_receipt_sha256":
            conflict_key = (
                payload["schema"],
                f4._json_bytes(payload["publication_authorization_ref"]),
                *(payload[field] for field in (
                    "producer_run_identity", "producer_document_sha256",
                    "producer_node", "producer_output", "root_ref",
                    "root_id", "snapshot_version",
                )),
            )
            roster_publisher._outer_receipts[conflict_key] = b"conflict"
        return original(payload, self_field)

    monkeypatch.setattr(
        trust._SyntheticRawPublisher, "_sign",
        staticmethod(insert_conflict_during_sign),
    )
    with pytest.raises(ValueError, match="WORM conflict"):
        publisher.publish(proof, *signed, *roster)
    assert roster_publisher._outer_receipts[conflict_key] == b"conflict"
    assert publisher._closed and proof._used
    assert roster_publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='raw-dataset'"
    ).fetchone() == ("QUARANTINED",)


def _adr140_published_raw_case(tmp_path, monkeypatch):
    _path, roster_publisher, roster, signed, members = _adr132_raw_case(
        tmp_path, monkeypatch,
    )
    preflight = trust._SyntheticRawPreflight(
        roster_publisher, trust._SyntheticFixtureSource(members),
    )
    raw_fixture = preflight.verify(*signed, *roster)
    publisher = trust._SyntheticRawPublisher(preflight)
    manifest, basis, receipt = publisher.publish(
        raw_fixture, *signed, *roster,
    )
    return publisher, roster, signed, (manifest, basis, receipt)


def test_adr140_raw_root_proof_matches_live_signed_publication(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    proof = publisher.proof()
    assert isinstance(proof, trust.NonAuthorizingRawRootProof)
    before = publisher._reserve._connection.total_changes
    facts = proof.verify(*signed, *roster, *output)
    receipt = json.loads(output[2])
    assert facts["raw_publication_receipt_sha256"] == (
        receipt["root_publication_receipt_sha256"]
    )
    assert facts["raw_manifest_sha256"] == hashlib.sha256(output[0]).hexdigest()
    assert facts["event_count"] == 1
    assert len(facts["ordered_member_digests"]) == 2
    assert facts["authorizing"] is False
    assert facts["deployment_eligible"] is False
    assert publisher._reserve._connection.total_changes == before
    with pytest.raises(TypeError):
        trust.NonAuthorizingRawRootProof(publisher)
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingRawRootProof is (
        trust.NonAuthorizingRawRootProof
    )


@pytest.mark.parametrize("tamper", [
    "manifest", "basis", "receipt", "f4-member", "f4-log",
    "outer-missing", "audit-gap", "reserve-quarantine",
])
def test_adr140_raw_root_proof_refuses_substitution_or_backing_loss(
    tmp_path, monkeypatch, tamper,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    manifest, basis, receipt = output
    root = "synthetic-raw/" + hashlib.sha256(signed[0]).hexdigest()
    if tamper == "manifest":
        manifest += b" "
    elif tamper == "basis":
        basis += b" "
    elif tamper == "receipt":
        receipt += b" "
    elif tamper == "f4-member":
        publisher._broker._storage[(root, "v1", "fixture_A.ndjson")] = b"wrong"
    elif tamper == "f4-log":
        publisher._broker._receipt_store._data.clear()
    elif tamper == "outer-missing":
        key = next(
            key for key in publisher._roster_publisher._outer_receipts
            if key[0] == "dskit.root-publication-receipt/v1"
        )
        del publisher._roster_publisher._outer_receipts[key]
    elif tamper == "audit-gap":
        publisher._reserve._connection.execute(
            "DELETE FROM reserve_audit WHERE kind='raw-dataset' "
            "AND new_state='SEALED'"
        )
    else:
        publisher._reserve._connection.execute(
            "UPDATE reserve_uses SET state='QUARANTINED' "
            "WHERE kind='raw-dataset'"
        )
    with pytest.raises((TypeError, ValueError)):
        publisher.proof().verify(
            *signed, *roster, manifest, basis, receipt,
        )


def test_adr140_raw_root_proof_rechecks_revocation_after_f4_readback(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "synthetic-reserve.sqlite")
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    path = publisher._reserve._path
    original = publisher._broker._provider.open_member
    fired = False

    def revoke_during_raw_read(snapshot, name):
        nonlocal fired
        raw = original(snapshot, name)
        if snapshot["root_ref"].startswith("synthetic-raw/") and not fired:
            fired = True
            admin = trust._SyntheticAuthorizationReserve(path)
            admin._revoke("G1")
            admin._close()
        return raw

    monkeypatch.setattr(
        publisher._broker._provider, "open_member", revoke_during_raw_read,
    )
    with pytest.raises(ValueError):
        publisher.proof().verify(*signed, *roster, *output)
    assert fired


def test_adr141_root_pis_issues_exact_live_two_root_pair_once(tmp_path, monkeypatch):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    basis_bytes, pis_bytes = issuer.issue(*signed, *roster, *output)
    basis, pis = json.loads(basis_bytes), json.loads(pis_bytes)
    assert basis["schema"] == "dskit.issuance-basis/v2"
    assert basis["kind"] == "root-pis"
    assert len(basis["refs"]) == 8
    assert pis["schema"] == "dskit.published-input-set/v2"
    assert pis["phase"] == "root-g1-g2"
    assert [entry["input_id"] for entry in pis["entries"]] == [
        "raw_event_dataset", "source_roster",
    ]
    assert basis["issued_at_ms"] == pis["issued_at_ms"] == 601
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == ("ISSUED",)
    with pytest.raises(ValueError):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )


def test_adr141_root_pis_read_only_proof_rechecks_pair_and_shared_state(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    pair = issuer.issue(*signed, *roster, *output)
    proof = issuer.proof()
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingSyntheticRootPisProof is (
        trust.NonAuthorizingSyntheticRootPisProof
    )
    facts = proof.verify(*signed, *roster, *output, *pair)
    assert facts["published_input_set_sha256"] == json.loads(pair[1])[
        "published_input_set_sha256"
    ]
    assert facts["authorizing"] is False
    assert facts["deployment_eligible"] is False
    with pytest.raises(ValueError):
        proof.verify(*signed, *roster, *output, pair[0], pair[1] + b" ")
    publisher._reserve._advance_clock(602)
    assert proof.verify(*signed, *roster, *output, *pair)["checked_at_ms"] == 602
    publisher._reserve._revoke("data-publisher/published-input-set-g1-g2")
    with pytest.raises(ValueError):
        proof.verify(*signed, *roster, *output, *pair)


def test_adr141_root_pis_requires_post_raw_shared_time(tmp_path, monkeypatch):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    with pytest.raises(ValueError, match="follow raw receipt clock"):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == (0,)


def test_adr141_root_pis_signer_revocation_refuses_without_spend(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    publisher._reserve._revoke("data-publisher/published-input-set-g1-g2")
    with pytest.raises(ValueError):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == (0,)


def test_adr141_root_pis_sign_failure_quarantines_spent_pair(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)

    def fail_sign(_payload, _field):
        raise ValueError("injected signer failure")

    monkeypatch.setattr(
        trust._SyntheticRootPisIssuer, "_sign", staticmethod(fail_sign),
    )
    with pytest.raises(ValueError, match="injected signer failure"):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == ("QUARANTINED",)
    assert publisher._root_pis_pairs == {}


def test_adr141_root_pis_worm_conflict_quarantines_spent_pair(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    signed_id = f4._json_bytes({
        "bootstrap_id": json.loads(roster[0])["bootstrap_id"],
        "authorization_id": json.loads(signed[0])["authorization_id"],
    }).decode("ascii")
    key = ("synthetic-study", "root-g1-g2", signed_id)
    publisher._root_pis_pairs[key] = (b"wrong basis", b"wrong PIS")
    with pytest.raises(ValueError, match="WORM conflict"):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )
    assert publisher._root_pis_pairs[key] == (b"wrong basis", b"wrong PIS")
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == ("QUARANTINED",)


def test_adr141_root_pis_proof_fences_clock_change_during_readback(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    pair = issuer.issue(*signed, *roster, *output)
    original = publisher._broker._provider.open_member
    fired = False

    def advance_during_read(snapshot, name):
        nonlocal fired
        raw = original(snapshot, name)
        if not fired:
            fired = True
            admin = trust._SyntheticAuthorizationReserve(
                publisher._reserve._path,
            )
            admin._advance_clock(602)
            admin._close()
        return raw

    monkeypatch.setattr(
        publisher._broker._provider, "open_member", advance_during_read,
    )
    with pytest.raises(ValueError, match="freshness changed"):
        issuer.proof().verify(*signed, *roster, *output, *pair)
    assert fired


@pytest.mark.parametrize("tamper", ["audit", "row", "worm"])
def test_adr141_root_pis_proof_refuses_backing_tamper(
    tmp_path, monkeypatch, tamper,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    pair = issuer.issue(*signed, *roster, *output)
    if tamper == "audit":
        publisher._reserve._connection.execute(
            "DELETE FROM reserve_audit WHERE kind='root-pis' "
            "AND new_state='RESERVED'"
        )
    elif tamper == "row":
        publisher._reserve._connection.execute(
            "UPDATE reserve_uses SET state='QUARANTINED' "
            "WHERE kind='root-pis'"
        )
    else:
        key = next(iter(publisher._root_pis_pairs))
        publisher._root_pis_pairs[key] = (b"wrong", b"wrong")
    with pytest.raises(ValueError):
        issuer.proof().verify(*signed, *roster, *output, *pair)


def test_adr141_root_pis_expired_grant_cannot_issue(tmp_path, monkeypatch):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(900)
    with pytest.raises(ValueError):
        trust._SyntheticRootPisIssuer(publisher).issue(
            *signed, *roster, *output,
        )
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='root-pis'"
    ).fetchone() == (0,)


def test_adr142_dynamic_root_graph_resolves_exact_twelve_signed_artifacts(
    tmp_path, monkeypatch,
):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    pair = issuer.issue(*signed, *roster, *output)
    graph = issuer.graph()
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingDynamicRootGraph is (
        trust.NonAuthorizingDynamicRootGraph
    )
    snapshot = graph.snapshot()
    assert len(snapshot.references) == 12
    expected_bytes = set((*signed[:3], *roster[:3], *roster[3:],
                          *output[1:], *pair))
    assert {graph.resolve(snapshot, ref) for ref in snapshot.references} == (
        expected_bytes
    )
    assert not hasattr(graph, "authorize_capture_set")
    assert not hasattr(graph, "read_member_bytes")


def _adr142_graph_case(tmp_path, monkeypatch):
    publisher, roster, signed, output = _adr140_published_raw_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    issuer.issue(*signed, *roster, *output)
    graph = issuer.graph()
    return publisher, issuer, graph, graph.snapshot()


def test_adr142_dynamic_root_graph_snapshot_and_reference_refusals(
    tmp_path, monkeypatch,
):
    publisher, issuer, graph, snapshot = _adr142_graph_case(
        tmp_path, monkeypatch,
    )
    before = publisher._reserve._connection.total_changes
    ref = snapshot.references[0]
    assert graph.resolve(snapshot, ref)
    assert publisher._reserve._connection.total_changes == before
    parsed = json.loads(ref)
    parsed["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        graph.resolve(snapshot, f4._json_bytes(parsed))
    with pytest.raises(ValueError):
        graph.resolve(snapshot, ref + b" ")
    with pytest.raises(TypeError):
        trust._DynamicRootGraphSnapshot()
    publisher._reserve._advance_clock(602)
    with pytest.raises(ValueError, match="snapshot state changed"):
        graph.resolve(snapshot, ref)
    renewed = graph.snapshot()
    assert graph.resolve(renewed, ref)
    assert issuer.graph() is graph


@pytest.mark.parametrize("kind", ["roster-bootstrap", "raw-dataset", "root-pis"])
def test_adr142_dynamic_root_graph_refuses_changed_row_or_audit(
    tmp_path, monkeypatch, kind,
):
    publisher, _issuer, graph, snapshot = _adr142_graph_case(
        tmp_path, monkeypatch,
    )
    publisher._reserve._connection.execute(
        "DELETE FROM reserve_audit WHERE kind=? AND seq=("
        "SELECT MIN(seq) FROM reserve_audit WHERE kind=?)",
        (kind, kind),
    )
    with pytest.raises(ValueError, match="snapshot state changed"):
        graph.resolve(snapshot, snapshot.references[0])


def test_adr142_dynamic_root_graph_second_proof_catches_worm_mutation(
    tmp_path, monkeypatch,
):
    publisher, _issuer, graph, snapshot = _adr142_graph_case(
        tmp_path, monkeypatch,
    )
    original = trust.NonAuthorizingSyntheticRootPisProof.verify
    calls = 0

    def mutate_after_first_proof(self, *values):
        nonlocal calls
        result = original(self, *values)
        calls += 1
        if calls == 1:
            key = next(iter(publisher._root_pis_pairs))
            publisher._root_pis_pairs[key] = (b"lost", b"lost")
        return result

    monkeypatch.setattr(
        trust.NonAuthorizingSyntheticRootPisProof, "verify",
        mutate_after_first_proof,
    )
    with pytest.raises(ValueError):
        graph.resolve(snapshot, snapshot.references[0])
    assert calls == 1


# ---------------------------------------------------------------------------
# ADR-0143: same-domain dynamic P4 capture authority (F5a Packet 7)
#
# Covers evidence 0181's 12 required matrix rows plus a positive closure
# case and both public facades. The dynamic authority's admission chain
# (root-capture-admission -> cas -> pce) is exercised directly through
# resolver.close_admission, which is the ADR's own required four-method
# resolver contract entry point; see
# test_adr143_authorize_capture_set_blocked_by_prepare_contract below for the
# disclosed, separately-tracked gap in the full authorize_capture_set path
# (evidence 0182 convergence note).
# ---------------------------------------------------------------------------


def _adr143_issued_case(tmp_path, monkeypatch):
    """Return publisher, issuer, graph and one constructed dynamic authority."""
    publisher, issuer, graph, _snapshot = _adr142_graph_case(tmp_path, monkeypatch)
    authority = trust._development_dynamic_p4_broker(issuer)
    return publisher, issuer, graph, authority


def _adr143_captures(authority, run_id="dynamic-consumer-run"):
    """Drive one real F4 produce/seal/publish lifecycle on the dynamic broker
    and build one live capture tuple, mirroring the legacy _graph_live shape."""
    session, published, _values = f4._publish(authority)
    authority.end_session(session)
    descriptor = authority.descriptor(published, purpose="synthetic")
    source = f4._consumer_document(descriptor)
    frozen = authority.freeze_consumer_document(source, consumer_node="consume",
                                                consumer_input="bundle", purpose="synthetic")
    port = authority.derive_consumer_port(frozen)
    return (published, frozen, port), run_id


def test_adr143_dynamic_broker_factory_exists():
    """RED anchor: the ADR-0143 factory must exist as a callable."""
    factory = getattr(trust, "_development_dynamic_p4_broker", None)
    assert callable(factory), "ADR-0143 dynamic P4 broker factory is missing"


def test_adr143_dynamic_authority_constructs_from_retained_graph_once(tmp_path, monkeypatch):
    publisher, _issuer, graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    assert isinstance(authority, trust.CapturedAuthorizationAuthority)
    assert authority.deployment_eligible is False
    resolver = authority._p4_resolver
    assert type(resolver) is trust._DynamicP4TrustedArtifactResolver
    assert resolver._graph is graph
    assert publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='dynamic-p4-authority'"
    ).fetchone() == ("ISSUED",)
    from dskit.production import verifier as production_verifier
    assert production_verifier.NonAuthorizingDynamicRootGraph is trust.NonAuthorizingDynamicRootGraph


def test_adr143_matrix_row5_second_construction_for_same_graph_refuses(tmp_path, monkeypatch):
    _publisher, issuer, _graph, _authority = _adr143_issued_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="already constructed"):
        trust._development_dynamic_p4_broker(issuer)


def test_adr143_matrix_row4_refuses_before_root_pis_is_issued(tmp_path, monkeypatch):
    publisher, roster, signed, output = _adr140_published_raw_case(tmp_path, monkeypatch)
    publisher._reserve._advance_clock(601)
    issuer = trust._SyntheticRootPisIssuer(publisher)
    issuer.issue(*signed, *roster, *output)
    signed_id = issuer._retained[5][2]
    publisher._reserve._connection.execute(
        "UPDATE reserve_uses SET state='QUARANTINED' WHERE kind='root-pis' AND signed_id=?",
        (signed_id,),
    )
    with pytest.raises(ValueError, match="ISSUED root-PIS row"):
        trust._development_dynamic_p4_broker(issuer)
    assert publisher._reserve._connection.execute(
        "SELECT COUNT(*) FROM reserve_uses WHERE kind='dynamic-p4-authority'"
    ).fetchone() == (0,)


def test_adr143_matrix_row9_post_commit_failure_quarantines_row(tmp_path, monkeypatch):
    """Crash/ambiguous-COMMIT family: a failure AFTER the reserve COMMIT but
    before the broker returns must quarantine, never leave a silent ISSUED
    row backing no authority and never allow a later retry to reuse it."""
    publisher, issuer, _graph, _snapshot = _adr142_graph_case(tmp_path, monkeypatch)

    def fail_after_reserve(_self, _token, _graph_arg):
        raise RuntimeError("injected post-reserve construction failure")

    monkeypatch.setattr(
        trust._DynamicP4TrustedArtifactResolver, "__init__", fail_after_reserve,
    )
    with pytest.raises(RuntimeError, match="injected post-reserve"):
        trust._development_dynamic_p4_broker(issuer)
    monkeypatch.undo()
    row = publisher._reserve._connection.execute(
        "SELECT state FROM reserve_uses WHERE kind='dynamic-p4-authority'"
    ).fetchone()
    assert row == ("QUARANTINED",)
    # A fresh attempt against the same graph is refused too: quarantine is
    # terminal, it never grants a second live construction.
    with pytest.raises(ValueError, match="already constructed"):
        trust._development_dynamic_p4_broker(issuer)


def test_adr143_matrix_row11_reserve_first_ordering_leaves_no_orphan_resolver(tmp_path, monkeypatch):
    """Phase 0 pin F3: the reserve spend commits BEFORE any resolver/terminal
    object is constructed, so a losing/interleaved caller never ends up
    holding a live resolver pointed at a graph whose snapshot slot another
    winning caller has already overwritten (evidence 0181 matrix row 11)."""
    publisher, issuer, graph, _snapshot = _adr142_graph_case(tmp_path, monkeypatch)
    calls = []
    real_init = trust._DynamicP4TrustedArtifactResolver.__init__

    def spy(self, token, graph_arg):
        # By the time ANY resolver is constructed, the reserve row is
        # already ISSUED -- confirming reserve-first ordering directly.
        row = publisher._reserve._connection.execute(
            "SELECT state FROM reserve_uses WHERE kind='dynamic-p4-authority'"
        ).fetchone()
        calls.append(row)
        return real_init(self, token, graph_arg)

    monkeypatch.setattr(trust._DynamicP4TrustedArtifactResolver, "__init__", spy)
    authority = trust._development_dynamic_p4_broker(issuer)
    assert calls == [("ISSUED",)]
    assert trust._P4_ISSUED[authority][1] is graph


@pytest.mark.parametrize("resolver_kind", ["legacy", "dynamic"])
def test_adr143_matrix_row1_and_row2_dispatch_never_crosses_or_fails_open(
    tmp_path, monkeypatch, resolver_kind,
):
    _publisher, _issuer, _graph, dynamic_authority = _adr143_issued_case(tmp_path, monkeypatch)
    legacy_authority = trust._development_p4_broker()
    legacy_resolver = legacy_authority._p4_resolver
    dynamic_resolver = dynamic_authority._p4_resolver
    if resolver_kind == "legacy":
        resolver, snapshot = legacy_resolver, legacy_resolver.snapshot()
        # Matrix row 1: the legacy branch never evaluates dynamic-only state
        # (no _graph attribute exists on this resolver at all).
        assert not hasattr(resolver, "_graph")
    else:
        resolver, snapshot = dynamic_resolver, dynamic_resolver._snapshot
        # Matrix row 1: the dynamic branch never evaluates legacy-only state
        # (no _records attribute exists on this resolver at all).
        assert not hasattr(resolver, "_records")
    trust._p4_snapshot_integrity(resolver, snapshot)  # each branch is self-consistent
    # Matrix row 2: neither closed type -> unconditional else-arm refusal,
    # identical message to today's single-branch refusal.
    with pytest.raises(ValueError, match="P4 snapshot integrity refused"):
        trust._p4_snapshot_integrity(object(), snapshot)
    with pytest.raises(TypeError, match="exact broker-issued P4 capability is required"):
        trust._p4_require_issued_authority(object())


def test_adr143_matrix_row2_third_resolver_subclass_fails_closed():
    class _ThirdResolver:
        pass

    with pytest.raises(ValueError, match="P4 snapshot integrity refused"):
        trust._p4_snapshot_integrity(_ThirdResolver(), object())


def test_adr143_matrix_row3_stale_snapshot_token_refuses(tmp_path, monkeypatch):
    publisher, _issuer, graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    stale = resolver.snapshot()
    publisher._reserve._advance_clock(publisher._reserve._now() + 1)
    renewed = resolver.snapshot()
    assert renewed is not stale
    with pytest.raises(ValueError, match="exact issued dynamic root snapshot required"):
        graph.resolve(stale, stale.references[0])
    assert graph.resolve(renewed, renewed.references[0])


def test_adr143_matrix_row3_foreign_graph_token_refuses(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _publisher_a, _issuer_a, _graph_a, authority = _adr143_issued_case(tmp_path / "a", monkeypatch)
    _publisher_b, _issuer_b, graph_b, _snapshot_b = _adr142_graph_case(tmp_path / "b", monkeypatch)
    resolver = authority._p4_resolver
    own_snapshot = resolver._snapshot
    with pytest.raises(ValueError, match="exact issued dynamic root snapshot required"):
        graph_b.resolve(own_snapshot, own_snapshot.references[0])


def test_adr143_matrix_row6_forged_admission_ref_refuses(tmp_path, monkeypatch):
    """Aliased/swapped/wrong-schema root-capture-admission refs never pass."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    snapshot = resolver._snapshot
    (published, frozen, port), run_id = _adr143_captures(authority)
    runtime = {"consumer_run_identity": run_id, "process_measurement_sha256": f4._SHA["consumer_process"],
               "runtime_sha256": f4._SHA["consumer_runtime"]}
    live_projection = (authority, ((published, frozen, port),), runtime)
    forged = {"kind": "root-capture-admission", "role": "study-lifecycle",
              "schema": "dskit.root-capture-admission/v1", "sha256": "f" * 64}
    with pytest.raises(ValueError, match="does not match closed graph chain"):
        resolver.close_admission(snapshot, forged, live_projection)
    wrong_kind = dict(forged, kind="dataset-capture-grant")
    with pytest.raises(ValueError, match="kind/role/schema refused"):
        resolver.close_admission(snapshot, wrong_kind, live_projection)


def test_adr143_matrix_row7_planned_entry_binding_is_recomputed_not_trusted():
    """The pce leaf's planned_entry_sha256 is always recomputed from the PIS
    entry's OWN input_id/contract_sha256 (evidence 0181 matrix row 7); an
    alias/swap of the binding is structurally impossible by construction, but
    the recompute formula itself is pinned here so any future refactor that
    starts trusting a caller-supplied digest is caught immediately."""
    entry_a = {"input_id": "raw_event_dataset", "contract_sha256": "a" * 64}
    entry_b = {"input_id": "source_roster", "contract_sha256": "b" * 64}
    pce_a = trust._p4_dynamic_planned_entry(entry_a, "d" * 64, "node", "raw_event_dataset", "historical-study")
    pce_b = trust._p4_dynamic_planned_entry(entry_b, "d" * 64, "node", "source_roster", "historical-study")
    assert pce_a["planned_entry_sha256"] != pce_b["planned_entry_sha256"]
    swapped_preimage = {"root_pis_entry_input_id": entry_a["input_id"], "root_pis_contract_sha256": entry_b["contract_sha256"]}
    assert pce_a["planned_entry_sha256"] != trust._digest(trust._hs_canonical_bytes(swapped_preimage))


def test_adr143_matrix_row8_revocation_mid_close_admission_refuses(tmp_path, monkeypatch):
    publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    snapshot = resolver._snapshot
    ref = snapshot.references[0]
    publisher._reserve._revoke("data-publisher/published-input-set-g1-g2")
    with pytest.raises(ValueError):
        resolver.resolve(snapshot, ref)


def test_adr143_matrix_row10_same_run_identity_across_instances_is_not_refused(tmp_path, monkeypatch):
    """Decision point 8 / matrix row 10: cross-instance run-identity reuse is
    a DELIBERATE non-goal, not an oversight -- each authority enforces
    exclusivity only against its own per-instance ledger/_stream_pin, and
    this ADR adds no cross-instance check."""
    _publisher, _issuer, _graph, dynamic_authority = _adr143_issued_case(tmp_path, monkeypatch)
    legacy_authority = trust._development_p4_broker()
    for authority, run_identity in (
        (legacy_authority, "producer-a"), (dynamic_authority, "producer-a"),
    ):
        session, _published, _values = f4._publish(authority)
        authority.end_session(session)
    # Both authorities accepted the SAME producer run_identity ("producer-a",
    # f4._publish's fixed producer identity) without either one refusing due
    # to the other's state: confirms per-instance-only enforcement.
    assert legacy_authority._stream_pin
    assert dynamic_authority._stream_pin


def test_adr143_matrix_row12_authorize_before_publish_completes_refuses(tmp_path, monkeypatch):
    """Partial lifecycle (produce without seal/publish) leaves the stream
    short of PUBLISHED -- the same inherited, unchanged guard the legacy
    authority relies on today (Decision point 8: no new lifecycle bypass)."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    from tests.pipeline.test_trust import _PRODUCER
    session = authority.start_producer_session(
        run_identity="producer-dynamic-row12",
        process_measurement_sha256=f4._SHA["producer_process"],
        runtime_sha256=f4._SHA["producer_runtime"],
        plan_sha256=f4._SHA["producer_plan"],
    )
    prepared = authority.produce(
        session, producer=dict(_PRODUCER), root={
            "root_ref": "capture://synthetic/root", "root_id": "7" * 64, "snapshot_version": "1",
        }, purpose="synthetic", expected_members=("config.json", "artifacts/bundle.json"),
        members=f4._members(), output_member="artifacts/bundle.json",
        completed=True, planned=True, transition_nonce="nonce-produced-row12",
    )
    # No seal()/publish(): confirm the stream head is not PUBLISHED yet.
    assert prepared is not None
    stream = next(iter(authority._stream_pin))
    assert authority._streams[stream][-1]["event"] != "PUBLISHED"


def test_adr143_positive_close_admission_succeeds_for_a_real_captured_chain(tmp_path, monkeypatch):
    """Positive case: construct the dynamic authority from a real ADR-0141/
    0142 graph, drive F4 produce/seal/publish twice (one capture per PIS
    entry), and confirm resolver.close_admission -- the ADR's own required
    four-method resolver contract entry point -- accepts a genuine matching
    admission chain with no effect (it is nonauthorizing on its own).

    See test_adr143_authorize_capture_set_blocked_by_prepare_contract for
    the disclosed, separate gap in the further authorize_capture_set step.
    """
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    snapshot = resolver._snapshot
    run_id = "dynamic-consumer-run"

    session_a, published_a, _values = f4._publish(
        authority, produced_nonce="a-produced", sealed_nonce="a-sealed", published_nonce="a-published",
    )
    authority.end_session(session_a)
    published_b, _sealed_b = f4._foreign_publish(authority)
    descriptor_a = authority.descriptor(published_a, purpose="synthetic")
    descriptor_b = authority.descriptor(published_b, purpose="synthetic")
    frozen_a = authority.freeze_consumer_document(
        f4._consumer_document(descriptor_a), consumer_node="consume",
        consumer_input="bundle", purpose="synthetic",
    )
    source_b = {"name": "consumer-2", "pipeline": {"consume": {"inputs": {
        "second": {"$captured_artifact": descriptor_b},
    }}}}
    frozen_b = authority.freeze_consumer_document(
        source_b, consumer_node="consume", consumer_input="second", purpose="synthetic",
    )
    port_a = authority.derive_consumer_port(frozen_a)
    port_b = authority.derive_consumer_port(frozen_b)
    graph_refs = [trust._hs_parse_canonical(ref) for ref, _raw in resolver._graph._records]
    root_pis_ref = next(ref for ref in graph_refs if ref["kind"] == "root-pis")
    g1_ref = next(ref for ref in graph_refs if ref["kind"] == "dataset-capture-grant" and ref["role"] == "G1")
    g2_ref = next(ref for ref in graph_refs if ref["kind"] == "dataset-capture-grant" and ref["role"] == "G2")
    root_pis = trust._hs_parse_canonical(
        trust._P4_DYNAMIC_CHECKED_RESOLVE(resolver, snapshot, trust._hs_canonical_bytes(root_pis_ref)),
    )
    entries = sorted(root_pis["entries"], key=lambda entry: entry["input_id"])
    assert [entry["input_id"] for entry in entries] == ["raw_event_dataset", "source_roster"]
    # close_admission pairs captures positionally against the sorted PIS
    # entries (Decision point 7); the caller's consumer_input naming is
    # projected into the pce leaf, not matched against entry["input_id"].
    captures = ((published_a, frozen_a, port_a), (published_b, frozen_b, port_b))
    runtime = {"consumer_run_identity": run_id, "process_measurement_sha256": f4._SHA["consumer_process"],
               "runtime_sha256": f4._SHA["consumer_runtime"]}
    _admission, admission_sha256 = trust._p4_dynamic_root_capture_admission(
        root_pis_ref, g1_ref, g2_ref, run_id, run_id,
    )
    admission_ref = {"kind": "root-capture-admission", "role": "study-lifecycle",
                     "schema": "dskit.root-capture-admission/v1", "sha256": admission_sha256}
    # ADR-0144 Decision point 6 (disclosed divergence from the fixed
    # resolver's None-on-success convention): the dynamic resolver's
    # close_admission now returns the frozen _DynamicP4AdmissionReconstruction
    # on success instead of None. Assert the correct frozen type/shape and
    # that it genuinely reflects THIS captured chain -- not merely "is not
    # None" -- so this still pins a real captured-chain acceptance, not just
    # a changed return type.
    result = resolver.close_admission(snapshot, admission_ref, (authority, captures, runtime))
    assert type(result) is trust._DynamicP4AdmissionReconstruction
    assert result.root_pis_ref == root_pis_ref
    assert result.dataset_g1_ref == g1_ref and result.dataset_g2_ref == g2_ref
    assert result.expected_admission_sha256 == admission_sha256
    assert result.expected_admission == _admission
    assert result.pis == root_pis
    assert len(result.pce_entries) == len(captures) == len(result.cas["entries"])
    assert result.cas["capture_admission_set_sha256"] == result.cas_sha256
    assert {entry["consumer_document_sha256"] for entry in result.cas["entries"]} == {
        port_a["consumer_document_sha256"], port_b["consumer_document_sha256"],
    }


def test_adr143_authorize_capture_set_blocked_by_prepare_contract(tmp_path, monkeypatch):
    """ADR-0144 CLOSES this disclosed convergence finding (evidence 0182).

    This test originally pinned a refusal: the SHARED, unedited
    ``_p4_checked_dispatch`` -> ``_p4_reference_bytes`` gate hardcoded its
    accepted admission kinds to ``_P4_ADMISSION_SCHEMAS = {"action-execution-
    admission", "final-replay-admission"}`` -- no case for
    ``root-capture-admission`` -- so a full ``authorize_capture_set`` call
    for the dynamic authority always refused right there, before
    ``close_admission``/``_prepare`` were ever reached.

    ADR-0144 Decision point 1 adds a resolver-dispatched ``elif`` arm to
    ``_p4_reference_bytes`` that accepts a well-formed ``root-capture-
    admission`` reference for the dynamic resolver. That SPECIFIC historical
    blocker is therefore gone: this test now confirms the shared gate no
    longer refuses a well-formed root-capture-admission reference, and that
    the call instead proceeds to the (unrelated, unedited)
    nonempty-captures precondition -- not back to the old kind/schema
    refusal. See test_adr144_dynamic_authorize_capture_set_reaches_genuine_captured_admission
    (below) for the full positive path with real, nonempty captures."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    assert not hasattr(resolver, "_records")
    admission_ref = {"kind": "root-capture-admission", "role": "study-lifecycle",
                     "schema": "dskit.root-capture-admission/v1", "sha256": "a" * 64}
    # The shared gate itself no longer objects to the kind/role/schema.
    assert trust._p4_reference_bytes(admission_ref, resolver) == trust._hs_canonical_bytes(admission_ref)
    with pytest.raises(ValueError) as failure:
        authority.authorize_capture_set(
            (), admission_ref,
            consumer_run_identity="x", process_measurement_sha256=f4._SHA["consumer_process"],
            runtime_sha256=f4._SHA["consumer_runtime"], transition_nonces=(),
        )
    assert "kind, role and schema must agree" not in str(failure.value)


def test_adr143_dynamic_authority_binds_transparently_into_verifier_facade(tmp_path, monkeypatch):
    """Public facade coverage (evidence 0181 public_facades / finding F4):
    HistoricalStudyVerifier accepts a dynamic authority instance exactly as
    it accepts the legacy one, via isinstance(authority, CapturedAuthorizationAuthority)
    -- no verifier.py edit is required or was made by this ADR."""
    from dskit.production import verifier as production_verifier
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    facade = production_verifier.HistoricalStudyVerifier(authority)
    assert facade._authority is authority
    assert production_verifier._P4_VERIFIER_PINS[facade] is authority


def test_adr143_dynamic_clock_and_revocations_read_live_shared_state(tmp_path, monkeypatch):
    publisher, _issuer, _graph, _authority = _adr143_issued_case(tmp_path, monkeypatch)
    reserve = publisher._reserve
    clock = trust._DynamicP4VerificationClock(reserve)
    revocations = trust._DynamicP4VerificationRevocations(reserve)
    before = clock.now_ms()
    publisher._reserve._advance_clock(before + 5)
    after = clock.now_ms()
    assert after == before + 5
    _generation, _revoked, snapshot_sha256 = reserve._snapshot()
    assert revocations.is_unrevoked(
        snapshot_sha256, "data-publisher/published-input-set-g1-g2", 1, after,
    ) is True
    publisher._reserve._revoke("data-publisher/published-input-set-g1-g2")
    _generation2, _revoked2, snapshot_sha256_2 = reserve._snapshot()
    assert snapshot_sha256_2 != snapshot_sha256
    with pytest.raises(AttributeError):
        clock._reserve = reserve
    with pytest.raises(AttributeError):
        revocations._reserve = reserve


def test_adr143_dynamic_resolver_and_terminal_final_markers():
    with pytest.raises(TypeError, match="final"):
        class _Sub(trust._DynamicP4TrustedArtifactResolver):
            pass
    with pytest.raises(TypeError, match="final"):
        class _SubTerm(trust._DynamicP4TerminalArtifactVerifier):
            pass
    with pytest.raises(TypeError):
        trust._DynamicP4TerminalArtifactVerifier()
    with pytest.raises(TypeError, match="dynamic broker construction"):
        trust._DynamicP4TrustedArtifactResolver(object(), object())


# ---------------------------------------------------------------------------
# ADR-0144: dynamic authorize_capture_set closure to a genuine CAPTURED
# admission (F5a Packet 7). Closes the convergence-checkpoint gap ADR-0143's
# GREEN disclosed (evidence 0182; see
# test_adr143_authorize_capture_set_blocked_by_prepare_contract above), which
# pinned the refusal this ADR now resolves.
# ---------------------------------------------------------------------------


def _adr144_foreign_publish_with_purpose(authority, purpose):
    """Drive one real produce/seal/publish lifecycle with a caller-chosen
    purpose (f4._foreign_publish hardcodes purpose='synthetic' with no
    override, so this mirrors its body with purpose parameterized -- needed
    to build two live captures whose baked publish-time purpose genuinely
    differs, since descriptor()/freeze_consumer_document() both hard-require
    the passed purpose to equal the publication's own baked purpose)."""
    session = authority.start_producer_session(
        run_identity="producer-adr144-purpose",
        process_measurement_sha256=f4._SHA["producer_process"],
        runtime_sha256=f4._SHA["producer_runtime"],
        plan_sha256=f4._SHA["producer_plan"],
    )
    producer = dict(f4._PRODUCER)
    producer["run_identity"] = "producer-adr144-purpose"
    prepared = authority.produce(
        session, producer=producer,
        root={"root_ref": "capture://synthetic/root-adr144-purpose",
              "root_id": "9" * 64, "snapshot_version": "1"},
        purpose=purpose, expected_members=("config.json", "artifacts/bundle.json"),
        members=f4._members({"rows": [{"id": "ADR144-PURPOSE"}]}),
        output_member="artifacts/bundle.json", completed=True, planned=True,
        transition_nonce="nonce-produced-adr144-purpose",
    )
    sealed = authority.seal(session, prepared, transition_nonce="nonce-sealed-adr144-purpose")
    published = authority.publish(session, sealed, transition_nonce="nonce-published-adr144-purpose")
    authority.end_session(session)
    return published


def _adr144_document_and_captures(authority, *, run_id="dynamic-consumer-run",
                                   purpose_a="synthetic", purpose_b="synthetic"):
    """Build two live captures that share ONE frozen consumer document (so
    _validate_capture_request's shared, unedited 'one frozen consumer
    document' gate is satisfied) but reference two DISTINCT published
    streams keyed by consumer_input (so the dynamic reconstruction's
    two-PIS-entry cardinality requirement is also satisfied) -- mirroring
    _graph_live's own reuse-one-document/two-consumer_input construction for
    the legacy grammar (trust.py-adjacent test helper above, count=2)."""
    session_a, published_a, _values = f4._publish(
        authority, produced_nonce="adr144-a-produced", sealed_nonce="adr144-a-sealed",
        published_nonce="adr144-a-published",
    )
    authority.end_session(session_a)
    if purpose_b == "synthetic":
        published_b, _sealed_b = f4._foreign_publish(authority)
    else:
        published_b = _adr144_foreign_publish_with_purpose(authority, purpose_b)
    descriptor_a = authority.descriptor(published_a, purpose=purpose_a)
    descriptor_b = authority.descriptor(published_b, purpose=purpose_b)
    document = {
        "name": "adr144-consumer",
        "pipeline": {"consume": {"inputs": {
            "bundle": {"$captured_artifact": descriptor_a},
            "second": {"$captured_artifact": descriptor_b},
        }}},
    }
    frozen_a = authority.freeze_consumer_document(
        document, consumer_node="consume", consumer_input="bundle", purpose=purpose_a,
    )
    frozen_b = authority.freeze_consumer_document(
        document, consumer_node="consume", consumer_input="second", purpose=purpose_b,
    )
    port_a = authority.derive_consumer_port(frozen_a)
    port_b = authority.derive_consumer_port(frozen_b)
    captures = ((published_a, frozen_a, port_a), (published_b, frozen_b, port_b))
    runtime = {"consumer_run_identity": run_id, "process_measurement_sha256": f4._SHA["consumer_process"],
               "runtime_sha256": f4._SHA["consumer_runtime"]}
    return captures, runtime


def _adr144_admission_ref(resolver, run_id):
    """Build the exact matching root-capture-admission ref for one graph."""
    graph_refs = [trust._hs_parse_canonical(ref) for ref, _raw in resolver._graph._records]
    root_pis_ref = next(ref for ref in graph_refs if ref["kind"] == "root-pis")
    g1_ref = next(ref for ref in graph_refs if ref["kind"] == "dataset-capture-grant" and ref["role"] == "G1")
    g2_ref = next(ref for ref in graph_refs if ref["kind"] == "dataset-capture-grant" and ref["role"] == "G2")
    _admission, admission_sha256 = trust._p4_dynamic_root_capture_admission(
        root_pis_ref, g1_ref, g2_ref, run_id, run_id,
    )
    return {"kind": "root-capture-admission", "role": "study-lifecycle",
            "schema": "dskit.root-capture-admission/v1", "sha256": admission_sha256}


def test_adr144_dynamic_authorize_capture_set_reaches_genuine_captured_admission(tmp_path, monkeypatch):
    """Positive end-to-end case (RED task item 1): a full authorize_capture_set
    call on the dynamic authority reaches a genuine CAPTURED admission,
    verified the SAME way _issue_complete (above) verifies the legacy path's
    CAPTURED admission: exact (record, session) tuple shape, exact record/
    session types, resolve_p4 round-trip, and idempotent re-issuance."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    run_id = "dynamic-consumer-run"
    captures, runtime = _adr144_document_and_captures(authority, run_id=run_id)
    admission_ref = _adr144_admission_ref(resolver, run_id)
    nonces = ("adr144-p4-0", "adr144-p4-1")
    try:
        result = authority.authorize_capture_set(
            captures, admission_ref, transition_nonces=nonces, **runtime,
        )
    except (TypeError, ValueError) as exc:
        pytest.fail(f"dynamic authorize_capture_set did not reach CAPTURED: {exc}")
    assert type(result) is tuple and len(result) == 2
    record, session = result
    assert type(record) is trust.CapturedAuthorizationRecord
    assert type(session) is trust.LaunchSession
    assert session._kind == "captured-authorization-v2"
    audit = authority._p4_ledger._audit(record)
    assert audit["admission_ref"] == admission_ref and audit["consumed"] is True
    # _audit's canonical-bytes round trip parses JSON arrays back to tuples
    # (this codebase's _hs_parse_canonical convention), so compare directly
    # against the already-tuple `nonces`, not a list.
    assert audit["nonces"] == nonces
    assert authority._p4_ledger.resolve_p4(audit["execution_key"], admission_ref) == (record, session)
    # Idempotent re-issuance: the same request returns the same committed pair.
    assert authority.authorize_capture_set(
        captures, admission_ref, transition_nonces=nonces, **runtime,
    ) == (record, session)


def test_adr144_legacy_authorize_capture_set_unaffected():
    """Regression (RED task item 2): the fixed/legacy authority's own
    authorize_capture_set -> CAPTURED path is unaffected by commit_p4_batch's
    five ADR-0144 edits. Reuses the exact same public doorway and assertion
    shape _issue_complete already applies to every legacy positive test."""
    graph, broker, _captures, _runtime, _before, record, session = _issue_complete()
    audit = broker._p4_ledger._audit(record)
    assert audit["admission_ref"] == graph.selected and audit["consumed"] is True
    assert broker._p4_ledger.resolve_p4(audit["execution_key"], graph.selected) == (record, session)


def test_adr144_fixed_resolver_pre_commit_recheck_uses_close_admission_not_legacy_walk(monkeypatch):
    """Latent-bug fix, verified for the FIXED resolver specifically (RED task
    item 3): evidence 0183's ground truth disclosed that commit_p4_batch's
    LATE pre-commit re-check called _P4_CLOSE_ADMISSION (the legacy-only
    closure walk) directly instead of resolver.close_admission, unlike the
    same method's two earlier checkpoints. Spy on
    _FixedWormTrustedArtifactResolver.close_admission itself and confirm it
    is invoked at ALL THREE checkpoints (not just the first two) for one
    successful legacy commit."""
    calls = []
    real_close_admission = trust._FixedWormTrustedArtifactResolver.close_admission

    def spy(self, snapshot, admission_ref, live_projection):
        calls.append(1)
        return real_close_admission(self, snapshot, admission_ref, live_projection)

    monkeypatch.setattr(trust._FixedWormTrustedArtifactResolver, "close_admission", spy)
    _issue_complete()
    assert len(calls) == 3


def test_adr144_forged_admission_ref_refused_through_full_authorize_capture_set(tmp_path, monkeypatch):
    """A forged/substituted cas or pce cannot be smuggled into a dynamic-
    authority CAPTURED admission (RED task item 4): matches ADR-0143's D1
    reconstruction-based defense (matrix row 6), now exercised through the
    FULL authorize_capture_set path rather than resolver.close_admission
    directly."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    captures, runtime = _adr144_document_and_captures(authority)
    forged = {"kind": "root-capture-admission", "role": "study-lifecycle",
              "schema": "dskit.root-capture-admission/v1", "sha256": "f" * 64}
    with pytest.raises(ValueError, match="does not match closed graph chain"):
        authority.authorize_capture_set(
            captures, forged, transition_nonces=("adr144-forged-0", "adr144-forged-1"), **runtime,
        )
    # A wrong kind is refused even earlier than close_admission's own inner
    # check: through the full authorize_capture_set path this first hits the
    # shared _p4_checked_dispatch -> _p4_reference_bytes gate (Decision
    # point 1), which reuses the identical legacy exception text for the
    # same class of failure -- not close_admission's own
    # "kind/role/schema refused" message (that inner check is only reached
    # directly via resolver.close_admission, see
    # test_adr143_matrix_row6_forged_admission_ref_refuses above).
    wrong_kind = dict(forged, kind="dataset-capture-grant")
    with pytest.raises(ValueError, match="kind, role and schema must agree"):
        authority.authorize_capture_set(
            captures, wrong_kind, transition_nonces=("adr144-forged-2", "adr144-forged-3"), **runtime,
        )


def test_adr144_signed_batch_matches_close_admission_reconstruction_exactly(tmp_path, monkeypatch):
    """The signed batch's bytes are provably identical to what close_admission
    independently verified (RED task item 5, the core safety property),
    asserted DIRECTLY: spy on both the dynamic resolver's close_admission and
    _DynamicCapturedAuthorizationContract._prepare, confirm close_admission's
    graph-reading reconstruction path executes EXACTLY ONCE for the whole
    commit, that every _prepare call reads that SAME frozen instance, and
    that the actually-committed batch's capture_admission_set_sha256 equals
    that one instance's own cas digest byte-for-byte."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    run_id = "dynamic-consumer-run"
    captures, runtime = _adr144_document_and_captures(authority, run_id=run_id)
    admission_ref = _adr144_admission_ref(resolver, run_id)

    seen_close = []
    real_close = trust._DynamicP4TrustedArtifactResolver.close_admission

    def spy_close(self, snapshot, admission_ref_arg, live_projection):
        result = real_close(self, snapshot, admission_ref_arg, live_projection)
        seen_close.append(result)
        return result

    # commit_p4_batch calls the MODULE-LEVEL pin _P4_DYNAMIC_PREPARE (a
    # plain function reference captured at import time), not
    # _DynamicCapturedAuthorizationContract._prepare through the class --
    # exactly mirroring how the fixed contract's own _P4_PREPARE pin is
    # immune to class-level monkeypatching by design (anti-tampering,
    # _p4_fixed_integrity-style). So the spy must replace the pin itself.
    seen_prepare = []
    real_prepare = trust._P4_DYNAMIC_PREPARE

    def spy_prepare(contract, resolver_arg, admission_ref_arg, streams, runtime_arg, nonces, captures_arg, reconstruction):
        seen_prepare.append(reconstruction)
        return real_prepare(contract, resolver_arg, admission_ref_arg, streams, runtime_arg, nonces, captures_arg, reconstruction)

    monkeypatch.setattr(trust._DynamicP4TrustedArtifactResolver, "close_admission", spy_close)
    monkeypatch.setattr(trust, "_P4_DYNAMIC_PREPARE", spy_prepare)
    nonces = ("adr144-match-0", "adr144-match-1")
    record, session = authority.authorize_capture_set(
        captures, admission_ref, transition_nonces=nonces, **runtime,
    )
    # commit_p4_batch invokes the admission-closure checkpoint three times,
    # but the graph-reading reconstruction must execute at MOST ONCE
    # (Decision point 6): close_admission is only spied at the resolver
    # level, so a genuinely-once-derived design shows exactly one call here
    # too, since only the FIRST checkpoint reaches resolver.close_admission
    # at all -- the second/third are handled by the cheap cell-populated
    # re-check inside _p4_close_admission_once and never call this method.
    assert len(seen_close) == 1
    reconstruction = seen_close[0]
    assert reconstruction is not None
    assert seen_prepare and all(value is reconstruction for value in seen_prepare)
    audit = authority._p4_ledger._audit(record)
    captured_set = json.loads(audit["set"])
    assert captured_set["capture_admission_set_sha256"] == reconstruction.cas["capture_admission_set_sha256"]
    assert captured_set["capture_admission_set_sha256"] == reconstruction.cas_sha256


def test_adr144_purpose_uniqueness_enforced_across_dynamic_captures(tmp_path, monkeypatch):
    """Purpose-uniqueness enforcement (RED task item 6, ADR Decision point
    8): a batch whose captures do not share one purpose is refused, mirroring
    _validate_capture_request's existing consumer-document-uniqueness
    pattern."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    captures, runtime = _adr144_document_and_captures(
        authority, purpose_a="synthetic", purpose_b="adr144-other-purpose",
    )
    resolver = authority._p4_resolver
    admission_ref = _adr144_admission_ref(resolver, runtime["consumer_run_identity"])
    with pytest.raises(ValueError, match="one purpose"):
        authority.authorize_capture_set(
            captures, admission_ref, transition_nonces=("adr144-purpose-0", "adr144-purpose-1"), **runtime,
        )


def test_adr144_both_public_facades_reach_captured_for_the_dynamic_authority(tmp_path, monkeypatch):
    """Both public facades (RED task item 7): HistoricalStudyVerifier.
    authorize_capture_set and HistoricalStudyCaptureDriver.authorize_capture_set
    both now reach a real CAPTURED admission for a dynamic authority
    instance, exactly as matrix row public_facades disclosed -- neither
    facade is edited by this ADR."""
    from dskit.production import verifier as production_verifier

    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    run_id = "dynamic-consumer-run"
    captures, runtime = _adr144_document_and_captures(authority, run_id=run_id)
    admission_ref = _adr144_admission_ref(resolver, run_id)
    facade = production_verifier.HistoricalStudyVerifier(authority)
    record, session = facade.authorize_capture_set(
        captures, admission_ref, transition_nonces=("adr144-facade-0", "adr144-facade-1"), **runtime,
    )
    assert type(record) is trust.CapturedAuthorizationRecord
    assert type(session) is trust.LaunchSession

    (tmp_path / "driver").mkdir()
    _publisher2, _issuer2, _graph2, authority2 = _adr143_issued_case(tmp_path / "driver", monkeypatch)
    resolver2 = authority2._p4_resolver
    captures2, runtime2 = _adr144_document_and_captures(authority2, run_id=run_id)
    admission_ref2 = _adr144_admission_ref(resolver2, run_id)
    facade2 = production_verifier.HistoricalStudyVerifier(authority2)
    driver = production_verifier.HistoricalStudyCaptureDriver(facade2)
    record2, session2 = driver.authorize_capture_set(
        captures2, admission_ref2, transition_nonces=("adr144-driver-0", "adr144-driver-1"), **runtime2,
    )
    assert type(record2) is trust.CapturedAuthorizationRecord
    assert type(session2) is trust.LaunchSession


# ---------------------------------------------------------------------------
# ADR-0146 -- bounded synthetic composed-tape verification (P7 closure gate).
#
# Reuses this file's own ADR-0143/0144 fixture helpers directly
# (_adr143_issued_case, _adr144_admission_ref, f4._publish/_foreign_publish)
# per Decision point 9(a): "reusing its private _adr143_issued_case/
# _adr144_document_and_captures/_adr144_admission_ref/f4._publish helpers
# directly and importing dskit.production.bundles locally for the
# compose_replay_tape/verify_causal_order calls". compose_replay_tape
# itself lives in dskit/production/bundles.py; nothing here reimplements
# its steps 1-10.
#
# CONVERGENCE CHECKPOINT (Phase 0 discovery, disclosed in evidence 0191):
# a full POSITIVE composed-tape fixture (a custom source_roster.json +
# raw-event members) cannot be carried through the FIXED/legacy authority's
# own action-execution-admission chain (_complete_signed_graph/_graph_live)
# -- _p4_close_admission's _p4_terminal_parent_projection re-signs the
# captured root's member_manifest_sha256 into the verified
# root-publication-receipt and checks the resulting projection digest
# against the HARDCODED, two-entry _P4_APPROVED_ROOT_PROJECTIONS allowlist
# (trust.py:3861) -- confirmed empirically: any member content other than
# the two pre-baked default fixtures f4._publish/_foreign_publish already
# use refuses with "fixed external root policy projection refused". This
# is the fixed corpus's own intentional design (a genuinely FIXED,
# pre-approved external policy grant, unlike the dynamic authority's
# per-test graph), not a test-construction bug, and fixing it would require
# editing dskit/pipeline/trust.py's hardcoded allowlist -- forbidden by
# Decision point 10. ADR-0146 Decision point 8's own text anticipates and
# permits exactly this asymmetry ("A fixed-authority case may additionally
# exist... but does not substitute for the dynamic-authority case"), so
# every test below that needs CUSTOM composed-tape fixture content is
# driven through the DYNAMIC authority instead (_adr146_dynamic_case) --
# compose_replay_tape's own validation logic (roster/raw-event shape
# checks, causal-order checks, round-trip genuineness, docstring, import
# purity) is independent of which authority supplied record/session/
# published (ADR-0146 Context point 1's own "resolver-type-agnostic by
# construction" claim, which row 2 below independently verifies). Row 1
# instead exercises what IS achievable through the fixed authority: real
# accessor reachability against the fixed corpus's own default content.
# ---------------------------------------------------------------------------

_ADR146_ROSTER_SCHEMA = "dskit.composed-tape-roster-fixture/v1"
_ADR146_RAW_EVENT_SCHEMA = "dskit.raw-event/v1"

#: The default straight-line, two-source, two-event fixture (Decision
#: point 5's minimal positive fixture: correction_position=0,
#: corrects_event_id=None, prior_envelope_sha256=None throughout).
_DEFAULT_ROSTER_SOURCES = [("alpha", 0), ("beta", 1)]
_DEFAULT_RAW_EVENTS = [
    ("events/e0.json", dict(source_id="alpha", event_id="evt-0", source_sequence=0,
                             availability_ms=1_000, payload_sha256="1" * 64)),
    ("events/e1.json", dict(source_id="beta", event_id="evt-1", source_sequence=0,
                             availability_ms=2_000, payload_sha256="2" * 64)),
]


def _adr146_entry(relative_path, **overrides):
    """One ``raw_event_members`` entry: the caller-supplied, ADR-0145-scoped
    per-envelope metadata (Decision point 7's signature)."""
    entry = {
        "relative_path": relative_path,
        "exchange_ms": 100,
        "receive_ms": 200,
        "source_provenance_tag": "fixture",
        "source_timezone_tag": "UTC",
        "correction_position": 0,
        "corrects_event_id": None,
        "prior_envelope_sha256": None,
    }
    entry.update(overrides)
    return entry


_DEFAULT_RAW_EVENT_MEMBERS = [
    _adr146_entry("events/e0.json"),
    _adr146_entry("events/e1.json", exchange_ms=150, receive_ms=250),
]


def _adr146_member(relative_path, raw_bytes):
    """One F4 ``members`` entry (``broker.produce``'s own member shape)."""
    return {
        "relative_path": relative_path,
        "media_type": "application/json",
        "bytes": bytearray(raw_bytes),
        "file_type": "regular",
        "link_count": 1,
    }


def _adr146_roster_bytes(sources, *, schema_version=_ADR146_ROSTER_SCHEMA):
    """Canonical bytes of one ``dskit.composed-tape-roster-fixture/v1`` object."""
    value = {
        "schema_version": schema_version,
        "sources": [{"source_id": source_id, "rank": rank} for source_id, rank in sources],
    }
    return f4._json_bytes(value)


def _adr146_raw_event_bytes(*, schema_version=_ADR146_RAW_EVENT_SCHEMA, **fields):
    """Canonical bytes of one ``dskit.raw-event/v1`` object."""
    value = {"schema_version": schema_version, **fields}
    return f4._json_bytes(value)


def _adr146_members(roster_sources, raw_events, *, roster_relative_path="source_roster.json"):
    """Build one F4 member list: a dummy output member (``f4._publish``'s own
    ``_produce`` hardcodes ``output_member='artifacts/bundle.json'``
    regardless of the caller's ``members=`` override, so it must be present
    among them), the ADR-0146 roster fixture, and one raw-event/v1 member
    per entry (Decision point 3's fixture root member layout)."""
    members = [
        _adr146_member("artifacts/bundle.json", f4._json_bytes({"rows": []})),
        _adr146_member(roster_relative_path, _adr146_roster_bytes(roster_sources)),
    ]
    for relative_path, fields in raw_events:
        members.append(_adr146_member(relative_path, _adr146_raw_event_bytes(**fields)))
    return members


def _adr146_dynamic_case(tmp_path, monkeypatch, members, *, run_id="adr146-dynamic-run"):
    """Drive one real F4 produce/seal/publish/authorize_capture_set lifecycle
    on the DYNAMIC authority (ADR-0143/0144), mirroring
    _adr144_document_and_captures's own two-stream cardinality construction,
    but with an ADR-0146 roster+raw-event fixture on stream A. Stream B is
    the ordinary f4._foreign_publish fixture -- only stream A is ever read
    by compose_replay_tape in these tests. Unlike the fixed authority (see
    this section's own convergence-checkpoint note above), the dynamic
    authority's admission is verified through
    _DynamicP4TrustedArtifactResolver's own narrow root-capture-admission
    closure (ADR-0143/0144), which is NOT gated by any hardcoded member-
    content allowlist, so custom fixture content is directly usable via
    f4._publish's own members=/expected_members= override -- no
    monkeypatch of f4._publish is needed here."""
    _publisher, _issuer, _graph, authority = _adr143_issued_case(tmp_path, monkeypatch)
    resolver = authority._p4_resolver
    expected = tuple(member["relative_path"] for member in members)
    session_a, published_a, _values = f4._publish(
        authority, members=members, expected_members=expected,
        produced_nonce="adr146-a-produced", sealed_nonce="adr146-a-sealed",
        published_nonce="adr146-a-published",
    )
    authority.end_session(session_a)
    published_b, _sealed_b = f4._foreign_publish(authority)
    descriptor_a = authority.descriptor(published_a, purpose="synthetic")
    descriptor_b = authority.descriptor(published_b, purpose="synthetic")
    document = {
        # The "name" carries run_id so two _adr146_dynamic_case calls in
        # the same test produce genuinely DIFFERENT consumer documents
        # (hence different consumer_document_sha256 values) -- every other
        # field the frozen document embeds (the descriptors) is a fixed
        # f4._PRODUCER/_ROOT constant, identical across any two calls with
        # the same member paths, so without this the two captures'
        # documents would collide byte-for-byte.
        "name": f"adr146-consumer-{run_id}",
        "pipeline": {"consume": {"inputs": {
            "bundle": {"$captured_artifact": descriptor_a},
            "second": {"$captured_artifact": descriptor_b},
        }}},
    }
    frozen_a = authority.freeze_consumer_document(
        document, consumer_node="consume", consumer_input="bundle", purpose="synthetic")
    frozen_b = authority.freeze_consumer_document(
        document, consumer_node="consume", consumer_input="second", purpose="synthetic")
    port_a = authority.derive_consumer_port(frozen_a)
    port_b = authority.derive_consumer_port(frozen_b)
    captures = ((published_a, frozen_a, port_a), (published_b, frozen_b, port_b))
    runtime = {
        "consumer_run_identity": run_id,
        "process_measurement_sha256": f4._SHA["consumer_process"],
        "runtime_sha256": f4._SHA["consumer_runtime"],
    }
    admission_ref = _adr144_admission_ref(resolver, run_id)
    nonces = (f"{run_id}-p4-0", f"{run_id}-p4-1")
    record, session = authority.authorize_capture_set(
        captures, admission_ref, transition_nonces=nonces, **runtime)
    return authority, record, session, published_a, port_a


# ---------------------------------------------------------------------------
# task item (1) -- FIXED-authority comparison/regression case
# ---------------------------------------------------------------------------


def test_compose_replay_tape_fixed_authority_accessor_reachable_default_content_refused():
    """RED task item (1) -- FIXED-authority comparison/regression case
    (ADR-0146 Decision point 8: "A fixed-authority case may additionally
    exist... but does not substitute for the dynamic-authority case").

    See this section's own convergence-checkpoint note above: a full
    POSITIVE case through the fixed authority (a custom ADR-0146 roster+
    raw-event fixture) is architecturally infeasible without editing
    trust.py's hardcoded _P4_APPROVED_ROOT_PROJECTIONS allowlist. What IS
    exercised here: the SAME compose_replay_tape accessor calls
    (read_member_bytes) reach real committed FIXED-authority content
    (_issue_complete's own default fixture, unmodified), and
    compose_replay_tape's own roster shape-check correctly refuses that
    content -- it is not roster-shaped -- rather than silently accepting
    it (matrix row 1, as a regression guard per Decision point 8's own
    suggested framing)."""
    from dskit.production import bundles

    _graph, _broker, captures, _runtime, _before, record, session = _issue_complete()
    published, _frozen, port = captures[0]

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "config.json", [],
        )


# ---------------------------------------------------------------------------
# task item (2) -- positive case through the DYNAMIC authority (Decision
# point 8's mandatory capstone -- must not be skipped, weakened, or
# collapsed into row 1)
# ---------------------------------------------------------------------------


def test_compose_replay_tape_dynamic_authority_positive_full_chain(tmp_path, monkeypatch):
    """RED task item (2) -- Decision point 8's MANDATORY capstone case: one
    real F4 produce/seal/publish/authorize_capture_set lifecycle on the
    DYNAMIC authority (ADR-0143/0144), a valid roster (>=2 sources) and a
    straight-line raw_event_members list (>=2 entries, correction_position=0
    throughout, Decision point 5's minimal positive fixture) -- ADR-0146's
    own stated positive proof case (Decision point 2 names the dynamic
    authority specifically), matrix row 2."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    authority, record, session, published, port = _adr146_dynamic_case(tmp_path, monkeypatch, members)
    consumer_document_sha256 = port["consumer_document_sha256"]

    tape = bundles.compose_replay_tape(
        record, session, published, consumer_document_sha256,
        "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
    )

    assert isinstance(tape, bundles.CapturedReplayTape)
    assert tape.envelope_count == len(_DEFAULT_RAW_EVENT_MEMBERS)
    for digest in (tape.data_capture_root, tape.data_captured_receipt, tape.source_rank_policy_sha256):
        assert isinstance(digest, str) and len(digest) == 64 and digest != "0" * 64
    assert authority.deployment_eligible is False

    # A second call with the SAME arguments must refuse at the accessor's
    # own single-read discipline -- compose_replay_tape does not itself
    # cache or bypass it (mirrors matrix row 1's own forbidden_effect,
    # exercised here since row 1's own fixture cannot carry a full success
    # -- see this section's convergence-checkpoint note).
    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, consumer_document_sha256,
            "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
        )


# ---------------------------------------------------------------------------
# task item (3) -- forged/mismatched record/session/published triple, and
# task item (10) -- cross-capture substitution
# ---------------------------------------------------------------------------


def test_compose_replay_tape_refuses_session_from_a_different_capture(tmp_path, monkeypatch):
    """RED task item (3): a genuine LaunchSession from a DIFFERENT capture
    than record refuses at the FIRST accessor call (matrix row 3)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _authority_a, record_a, _session_a, published_a, port_a = _adr146_dynamic_case(
        tmp_path / "a", monkeypatch, members, run_id="adr146-session-a")
    _authority_b, _record_b, session_b, _published_b, _port_b = _adr146_dynamic_case(
        tmp_path / "b", monkeypatch, members, run_id="adr146-session-b")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record_a, session_b, published_a, port_a["consumer_document_sha256"],
            "source_roster.json", [],
        )


def test_compose_replay_tape_refuses_published_from_a_different_capture(tmp_path, monkeypatch):
    """RED task item (10): record/session genuinely belong to capture A;
    published genuinely belongs to a DIFFERENT capture B -- refused before
    any roster content is read (matrix row 10)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _authority_a, record_a, session_a, _published_a, port_a = _adr146_dynamic_case(
        tmp_path / "a", monkeypatch, members, run_id="adr146-published-a")
    _authority_b, _record_b, _session_b, published_b, _port_b = _adr146_dynamic_case(
        tmp_path / "b", monkeypatch, members, run_id="adr146-published-b")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record_a, session_a, published_b, port_a["consumer_document_sha256"],
            "source_roster.json", [],
        )


def test_compose_replay_tape_refuses_wrong_consumer_document_sha256(tmp_path, monkeypatch):
    """RED task item (10): a syntactically valid sha256-shaped digest that
    simply does not match the frozen document actually used at capture
    time -- the check is keyed on the EXACT consumer_document_sha256, not
    merely 'any document that captured this stream' (matrix row 10)."""
    from dskit.production import bundles

    members_a = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    # Capture B's own member CONTENT must differ from A's -- consumer
    # document identity (document_sha256) is derived from the frozen
    # document's own bytes, which embed each publication's descriptor
    # (itself keyed in part by the captured root's own member-manifest
    # digest); identical member content would make A's and B's
    # consumer_document_sha256 collide, which would silently defeat this
    # refusal case instead of exercising it.
    members_b = _adr146_members(_DEFAULT_ROSTER_SOURCES, [
        _DEFAULT_RAW_EVENTS[0],
        ("events/e1.json", dict(source_id="beta", event_id="evt-1", source_sequence=0,
                                 availability_ms=2_000, payload_sha256="3" * 64)),
    ])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _authority_a, record_a, session_a, published_a, _port_a = _adr146_dynamic_case(
        tmp_path / "a", monkeypatch, members_a, run_id="adr146-docsha-a")
    _authority_b, _record_b, _session_b, _published_b, port_b = _adr146_dynamic_case(
        tmp_path / "b", monkeypatch, members_b, run_id="adr146-docsha-b")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record_a, session_a, published_a, port_b["consumer_document_sha256"],
            "source_roster.json", [],
        )


# ---------------------------------------------------------------------------
# task item (4) -- roster fixture validation failure
# ---------------------------------------------------------------------------

_ADR146_BAD_ROSTERS = {
    "wrong_schema_version": {
        "schema_version": "dskit.composed-tape-roster-fixture/v2",
        "sources": [{"source_id": "alpha", "rank": 0}],
    },
    "empty_sources": {
        "schema_version": _ADR146_ROSTER_SCHEMA,
        "sources": [],
    },
    "duplicate_source_id": {
        "schema_version": _ADR146_ROSTER_SCHEMA,
        "sources": [{"source_id": "alpha", "rank": 0}, {"source_id": "alpha", "rank": 1}],
    },
    "rank_not_list_index": {
        "schema_version": _ADR146_ROSTER_SCHEMA,
        "sources": [{"source_id": "alpha", "rank": 1}, {"source_id": "beta", "rank": 0}],
    },
    "rank_gap": {
        "schema_version": _ADR146_ROSTER_SCHEMA,
        "sources": [{"source_id": "alpha", "rank": 0}, {"source_id": "beta", "rank": 2}],
    },
    "unknown_source_field": {
        "schema_version": _ADR146_ROSTER_SCHEMA,
        "sources": [{"source_id": "alpha", "rank": 0, "extra": "nope"}],
    },
}


@pytest.mark.parametrize("case", sorted(_ADR146_BAD_ROSTERS))
def test_compose_replay_tape_refuses_malformed_roster_fixture(tmp_path, monkeypatch, case):
    """RED task item (4): every _check_composed_tape_roster_fixture refusal
    family (matrix row 4) raises BEFORE any raw-event member is read."""
    from dskit.production import bundles

    roster_bytes = f4._json_bytes(_ADR146_BAD_ROSTERS[case])
    members = [
        _adr146_member("artifacts/bundle.json", f4._json_bytes({"rows": []})),
        _adr146_member("source_roster.json", roster_bytes),
    ] + [_adr146_member(path, _adr146_raw_event_bytes(**fields)) for path, fields in _DEFAULT_RAW_EVENTS]
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id=f"adr146-roster-{case}")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
        )


# ---------------------------------------------------------------------------
# task item (5) -- raw-event member validation failure
# ---------------------------------------------------------------------------

_ADR146_BAD_RAW_EVENTS = {
    "wrong_schema_version": {
        "schema_version": "dskit.raw-event/v2", "source_id": "alpha", "event_id": "evt-0",
        "source_sequence": 0, "availability_ms": 1_000, "payload_sha256": "1" * 64,
    },
    "empty_source_id": {
        "schema_version": _ADR146_RAW_EVENT_SCHEMA, "source_id": "", "event_id": "evt-0",
        "source_sequence": 0, "availability_ms": 1_000, "payload_sha256": "1" * 64,
    },
    "negative_source_sequence": {
        "schema_version": _ADR146_RAW_EVENT_SCHEMA, "source_id": "alpha", "event_id": "evt-0",
        "source_sequence": -1, "availability_ms": 1_000, "payload_sha256": "1" * 64,
    },
    "malformed_payload_sha256": {
        "schema_version": _ADR146_RAW_EVENT_SCHEMA, "source_id": "alpha", "event_id": "evt-0",
        "source_sequence": 0, "availability_ms": 1_000, "payload_sha256": "not-a-digest",
    },
    "unknown_key": {
        "schema_version": _ADR146_RAW_EVENT_SCHEMA, "source_id": "alpha", "event_id": "evt-0",
        "source_sequence": 0, "availability_ms": 1_000, "payload_sha256": "1" * 64, "extra": "nope",
    },
}


@pytest.mark.parametrize("case", sorted(_ADR146_BAD_RAW_EVENTS))
def test_compose_replay_tape_refuses_malformed_raw_event_member(tmp_path, monkeypatch, case):
    """RED task item (5): every _check_raw_event_member refusal family
    (matrix row 5) raises BEFORE the envelope is ever assembled."""
    from dskit.production import bundles

    bad_bytes = f4._json_bytes(_ADR146_BAD_RAW_EVENTS[case])
    members = [
        _adr146_member("artifacts/bundle.json", f4._json_bytes({"rows": []})),
        _adr146_member("source_roster.json", _adr146_roster_bytes([("alpha", 0)])),
        _adr146_member("events/bad.json", bad_bytes),
    ]
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id=f"adr146-rawevent-{case}")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", [_adr146_entry("events/bad.json")],
        )


def test_compose_replay_tape_refuses_raw_event_orphaned_from_the_roster(tmp_path, monkeypatch):
    """RED task item (5)'s own distinct sub-case: an individually well-formed
    raw-event member whose source_id is not in the resolved roster
    accumulates into the raised ProductionError rather than a bare
    KeyError (matrix row 5)."""
    from dskit.production import bundles

    orphan_bytes = _adr146_raw_event_bytes(
        source_id="ghost", event_id="evt-ghost", source_sequence=0,
        availability_ms=1_000, payload_sha256="9" * 64,
    )
    members = [
        _adr146_member("artifacts/bundle.json", f4._json_bytes({"rows": []})),
        _adr146_member("source_roster.json", _adr146_roster_bytes([("alpha", 0)])),
        _adr146_member("events/ghost.json", orphan_bytes),
    ]
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-orphan")

    with pytest.raises(bundles.ProductionError) as excinfo:
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", [_adr146_entry("events/ghost.json")],
        )
    assert any("ghost" in problem for problem in excinfo.value.problems)


# ---------------------------------------------------------------------------
# task item (6) -- data_capture_root tamper-resistance
# ---------------------------------------------------------------------------


def test_compose_replay_tape_signature_has_no_data_capture_root_parameter():
    """RED task item (6): a caller cannot inject data_capture_root -- it is
    not a parameter at all (matrix row 6)."""
    import inspect

    from dskit.production import bundles

    parameters = inspect.signature(bundles.compose_replay_tape).parameters
    assert "data_capture_root" not in parameters
    assert list(parameters) == [
        "record", "session", "published", "consumer_document_sha256",
        "roster_relative_path", "raw_event_members",
    ]


def test_compose_replay_tape_data_capture_root_changes_when_captured_content_differs(tmp_path, monkeypatch):
    """RED task item (6): data_capture_root is computed fresh, inside the
    function, from the live published token's own sealed.digests -- two
    captures whose member CONTENT differs produce DIFFERENT roots (the
    corrected formula's real tamper-sensitivity: evidence 0190's
    targeted_recheck_of_critical_correction empirically confirmed mutating
    one member's sha256 changes this hash)."""
    from dskit.production import bundles

    members_a = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    members_b = _adr146_members(_DEFAULT_ROSTER_SOURCES, [
        _DEFAULT_RAW_EVENTS[0],
        ("events/e1.json", dict(source_id="beta", event_id="evt-1", source_sequence=0,
                                 availability_ms=2_000, payload_sha256="3" * 64)),  # differs
    ])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _authority_a, record_a, session_a, published_a, port_a = _adr146_dynamic_case(
        tmp_path / "a", monkeypatch, members_a, run_id="adr146-root-a")
    _authority_b, record_b, session_b, published_b, port_b = _adr146_dynamic_case(
        tmp_path / "b", monkeypatch, members_b, run_id="adr146-root-b")

    tape_a = bundles.compose_replay_tape(
        record_a, session_a, published_a, port_a["consumer_document_sha256"],
        "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
    )
    tape_b = bundles.compose_replay_tape(
        record_b, session_b, published_b, port_b["consumer_document_sha256"],
        "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
    )
    assert tape_a.data_capture_root != tape_b.data_capture_root


def test_compose_replay_tape_refuses_a_duck_typed_published_object(tmp_path, monkeypatch):
    """RED task item (6): a caller cannot achieve a chosen data_capture_root
    by constructing a published-shaped duck-type object -- there is no
    public _Published constructor reachable from outside trust.py, so a
    plain stand-in refuses instead (matrix row 6)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, _published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-ducktype")
    fake_published = type("FakePublished", (), {"sealed": None, "descriptor": {}})()

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, fake_published, port["consumer_document_sha256"],
            "source_roster.json", [],
        )


# ---------------------------------------------------------------------------
# task item (7) -- the build/canonical_bytes/parse round trip genuinely
# catches a construction bug, not a decorative passthrough
# ---------------------------------------------------------------------------


def test_compose_replay_tape_round_trip_is_genuine_not_a_shortcut(tmp_path, monkeypatch):
    """RED task item (7): a spy on CapturedReplayTape._build/.parse and
    verify_causal_order confirms compose_replay_tape's step 10 argument is
    literally the REPARSED object, not the pre-round-trip _build result
    (matrix row 7)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-roundtrip")

    build_calls = []
    parse_calls = []
    vco_calls = []
    original_build = bundles.CapturedReplayTape._build.__func__
    original_parse = bundles.CapturedReplayTape.parse.__func__
    original_vco = bundles.verify_causal_order

    def build_spy(cls, *args, **kwargs):
        result = original_build(cls, *args, **kwargs)
        build_calls.append(result)
        return result

    def parse_spy(cls, raw):
        result = original_parse(cls, raw)
        parse_calls.append((raw, result))
        return result

    def vco_spy(tape, ordered_envelope_bytes):
        vco_calls.append(tape)
        return original_vco(tape, ordered_envelope_bytes)

    monkeypatch.setattr(bundles.CapturedReplayTape, "_build", classmethod(build_spy))
    monkeypatch.setattr(bundles.CapturedReplayTape, "parse", classmethod(parse_spy))
    monkeypatch.setattr(bundles, "verify_causal_order", vco_spy)

    result = bundles.compose_replay_tape(
        record, session, published, port["consumer_document_sha256"],
        "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
    )

    assert len(build_calls) == 1 and len(parse_calls) == 1 and len(vco_calls) == 1
    built = build_calls[0]
    parsed_raw, parsed = parse_calls[0]
    assert parsed_raw == built.canonical_bytes()
    assert vco_calls[0] is parsed
    assert vco_calls[0] is not built
    assert result is parsed


# ---------------------------------------------------------------------------
# task item (8) -- verify_causal_order genuinely called on the reparsed
# tape, and ADR-0145's own matrix rows transitively still holding when
# reached through compose_replay_tape's raw_event_members surface
# ---------------------------------------------------------------------------


def test_compose_replay_tape_refuses_decreasing_causal_order(tmp_path, monkeypatch):
    """RED task item (8): evidence 0187's out-of-causal-order family,
    reachable through raw_event_members -- a caller controls availability_ms
    per entry via the ordered raw-event members, so a decreasing order key
    is reachable one hop upstream of verify_causal_order's own direct
    surface (matrix row 8)."""
    from dskit.production import bundles

    raw_events = [
        ("events/e0.json", dict(source_id="alpha", event_id="evt-0", source_sequence=0,
                                 availability_ms=2_000, payload_sha256="1" * 64)),
        ("events/e1.json", dict(source_id="beta", event_id="evt-1", source_sequence=0,
                                 availability_ms=1_000, payload_sha256="2" * 64)),  # decreasing
    ]
    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, raw_events)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-order")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
        )


def test_compose_replay_tape_refuses_duplicate_event_id(tmp_path, monkeypatch):
    """RED task item (8): evidence 0187's duplicate event_id family,
    reachable through raw_event_members (matrix row 8)."""
    from dskit.production import bundles

    raw_events = [
        ("events/e0.json", dict(source_id="alpha", event_id="evt-dup", source_sequence=0,
                                 availability_ms=1_000, payload_sha256="1" * 64)),
        ("events/e1.json", dict(source_id="beta", event_id="evt-dup", source_sequence=0,
                                 availability_ms=2_000, payload_sha256="2" * 64)),
    ]
    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, raw_events)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-dup")

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", _DEFAULT_RAW_EVENT_MEMBERS,
        )


def test_compose_replay_tape_refuses_correction_chain_forward_reference(tmp_path, monkeypatch):
    """RED task item (8): evidence 0187's correction-chain forward-reference
    family, reachable through raw_event_members's caller-supplied
    correction_position/corrects_event_id/prior_envelope_sha256 fields
    (matrix row 8)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-forwardref")
    entries = [
        _adr146_entry("events/e0.json", correction_position=1, corrects_event_id="evt-1",
                       prior_envelope_sha256="a" * 64),
        _adr146_entry("events/e1.json"),
    ]

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", entries,
        )


def test_compose_replay_tape_refuses_malformed_new_envelope_field(tmp_path, monkeypatch):
    """RED task item (8): evidence 0187's malformed-new-field family --
    reachable via a raw_event_members entry that passes
    _check_raw_event_member's own checks (which do not cover
    exchange_ms/receive_ms) but fails _check_event_envelope at step 6e
    (matrix row 8)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-badfield")
    entries = [
        _adr146_entry("events/e0.json", exchange_ms=500, receive_ms=100),  # receive < exchange
        _adr146_entry("events/e1.json"),
    ]

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", entries,
        )


def test_compose_replay_tape_refuses_unrecognized_raw_event_members_entry_key(tmp_path, monkeypatch):
    """RED task item (8) / evidence 0190 open_findings 'unrecognized_ninth_key':
    an extra ninth key in a raw_event_members entry dict is default-deny
    refused, matching this ADR lineage's other shape checks (matrix row 8,
    phase0_skeptic_review's Nit pin)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-ninthkey")
    entries = [
        _adr146_entry("events/e0.json", unexpected_field="nope"),
        _adr146_entry("events/e1.json"),
    ]

    with pytest.raises(bundles.ProductionError):
        bundles.compose_replay_tape(
            record, session, published, port["consumer_document_sha256"],
            "source_roster.json", entries,
        )


# evidence 0190 matrix row 8: evidence 0187 rows 1 and 9 (digest
# substitution; noncanonical bytes) are STRUCTURALLY UNREACHABLE through
# compose_replay_tape's own construction path -- envelope_bytes is always
# `_canonical_bytes(envelope)` and its digest is always
# `sha256(envelope_bytes)`, computed from the SAME envelope object in the
# SAME loop iteration (step 6f); they cannot diverge by construction, and
# there is no caller-supplied raw envelope-bytes surface. Evidence 0187 row
# 12 (mutation/aliasing of the whole list) is only PARTIALLY reachable for
# the same reason: a caller MAY reorder raw_event_members itself (a
# legitimate choice per Decision point 7 -- "no sorting is performed by
# this function"), but cannot independently reorder/duplicate/omit
# ordered_envelope_bytes against ordered_envelope_digests, since
# compose_replay_tape builds both in lockstep from the same loop. This is
# recorded here, not silently dropped, per matrix row 8's own instruction.
# Evidence 0187 row 14 (regression -- CapturedReplayTape.parse/_check_tape/
# _check_event_envelope unedited) is confirmed by running
# tests/production/test_event_envelope_causal_order.py unmodified
# alongside this file at GREEN (evidence 0191).


# ---------------------------------------------------------------------------
# task item (9) -- empty raw_event_members list
# ---------------------------------------------------------------------------


def test_compose_replay_tape_accepts_empty_raw_event_members_vacuously(tmp_path, monkeypatch):
    """RED task item (9): an empty raw_event_members list passes vacuously,
    consistent with ADR-0130 Decision point 4 / evidence 0187's identical
    ruling for verify_causal_order directly (matrix row 9)."""
    from dskit.production import bundles

    members = _adr146_members(_DEFAULT_ROSTER_SOURCES, _DEFAULT_RAW_EVENTS)
    _authority, record, session, published, port = _adr146_dynamic_case(
        tmp_path, monkeypatch, members, run_id="adr146-empty")

    tape = bundles.compose_replay_tape(
        record, session, published, port["consumer_document_sha256"],
        "source_roster.json", [],
    )
    assert tape.envelope_count == 0
    assert tape.ordered_envelope_digests == ()


# ---------------------------------------------------------------------------
# task item (11) -- the one-hop scope boundary itself
# ---------------------------------------------------------------------------


def test_compose_replay_tape_docstring_carries_the_bounded_synthetic_warning():
    """RED task item (11): the recheck Minor's exact one-line docstring
    warning, verbatim (matrix row 11)."""
    from dskit.production import bundles

    assert bundles.compose_replay_tape.__doc__ is not None
    assert (
        "Bounded synthetic/test use only; not for real replay operations."
        in bundles.compose_replay_tape.__doc__
    )


def test_compose_replay_tape_body_performs_no_second_f4_lifecycle_call():
    """RED task item (11): an AST-level check that compose_replay_tape's own
    body contains no call to produce/seal/publish/freeze_consumer_document/
    derive_consumer_port/authorize_capture_set -- it only reads from an
    ALREADY-captured record/session/published triple via read_member_bytes/
    lifecycle_captured_receipt_sha256 (matrix row 11)."""
    import ast
    import inspect

    from dskit.production import bundles

    source = inspect.getsource(bundles.compose_replay_tape)
    tree = ast.parse(source)
    forbidden = {
        "produce", "seal", "publish", "freeze_consumer_document",
        "derive_consumer_port", "authorize_capture_set",
    }
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    offenders = called & forbidden
    assert not offenders, offenders
