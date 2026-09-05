"""`document.py` — the serve document: shape, default-deny, identity.

Three things are proved here and nothing else, because §10 places this
module before any registry exists: the §4.1 grammar is accepted whole and
a typo anywhere is refused by name; the §4.2 partition of the top level
into eighteen graded sections and four excluded ones is exactly what the
identity hash grades; and D9's live default-deny refuses a document that
could reach a venue without a size limit, a period loss limit and a real
accounting strategy.  The "every selector names a registry" assertion of
§4.3 belongs to `test_main.py`, after the twenty registries are built.

The identity tests RESTATE the pipeline recipe with `hashlib` and
`json.dumps` rather than calling `config_hash` to produce the expected
value — an assertion sourced from its subject asserts nothing (CLAUDE.md,
"Duplication that diverges"; the exception that is correct).  One test
then pins `doc_hash` against `config_hash` itself, because D24 forbids a
second recipe beside the pipeline's.
"""

import copy
import hashlib
import json
import uuid

import pytest

from dskit.pipeline.base import config_hash
from dskit.production.base import ProductionError
from dskit.production.document import (
    PRODUCTION_NON_IDENTITY_SECTIONS,
    ServeDocument,
)
from dskit.production.vocab import RUNGS

# --------------------------------------------------------------------------
# The §4.2 partition, restated.  Eighteen graded sections plus four excluded
# plus `name` and `notes` must account for every key in §4.1.
# --------------------------------------------------------------------------

GRADED_SECTIONS = (
    "series_id",
    "rung",
    "serving",
    "feed",
    "schedule",
    "guards",
    "execution",
    "accounting",
    "arming",
    "coordination",
    "reconcile",
    "monitors",
    "health",
    "durability",
    "resilience",
    "lifecycle",
    "readiness",
    "alerting",
)

EXCLUDED_SECTIONS = ("alert_endpoints", "heartbeat", "placement", "env")

#: Every section the document must declare — the four excluded sections are
#: optional except `placement`, which names the serve root (§4.2).
REQUIRED_SECTIONS = (
    "serving",
    "feed",
    "schedule",
    "guards",
    "execution",
    "accounting",
    "arming",
    "coordination",
    "reconcile",
    "monitors",
    "health",
    "durability",
    "resilience",
    "lifecycle",
    "readiness",
    "alerting",
    "placement",
)

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"


# --------------------------------------------------------------------------
# Fixtures: §4.1 typed out verbatim, and the smallest document that validates
# --------------------------------------------------------------------------


def example_document(**overrides):
    """The §4.1 illustration, exactly as the plan prints it."""
    obj = {
        "name": "yourproject-serve",
        "series_id": SERIES_ID,
        "rung": "paper",
        "notes": "Why this process exists and how to promote it — the 'why'.",
        "serving": {
            "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",
            "adapter": "yourproject",
            "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
            "heads": ["select"],
            "required_universe": "configs/universe-serve.json",
            "proposer": {
                "uses": "intent-rows",
                "params": {
                    "output": "picks",
                    "fields": {"instrument": "symbol", "side": "side", "qty": "qty"},
                },
            },
            "replay": {"gate": "recorded", "stat_test": "recorded"},
            "max_artifact_age": "P30D",
        },
        "feed": {"uses": "entry-source", "params": {"pull": "acquire"}},
        "schedule": {
            "clock": {"uses": "wall"},
            "calendar": {
                "uses": "weekly-sessions",
                "params": {
                    "tz": "America/New_York",
                    "sessions": [
                        {
                            "days": ["mon", "tue", "wed", "thu", "fri"],
                            "open": "09:30",
                            "close": "16:00",
                        }
                    ],
                    "holidays": ["2026-11-26"],
                    "after_open_s": 60,
                    "before_close_s": 120,
                },
            },
            "cadence": {
                "uses": "aligned-bar",
                "params": {"bar_ms": 60000, "publish_delay_ms": 5000},
            },
            "overrun": {"policy": "coalesce", "max_lag_ms": 30000},
            "dead_after_ms": 600000,
            "max_staleness_ms": 120000,
            "max_quote_age_ms": 30000,
            "max_venue_skew_ms": 1000,
        },
        "guards": {
            "size": {
                "uses": "limit",
                "params": {
                    "measure": "quantity",
                    "bound": {"max": "100"},
                    "on_breach": "refuse",
                },
            },
            "exposure": {
                "uses": "limit",
                "params": {
                    "measure": "exposure_after",
                    "scope": "aggregate",
                    "include_working": True,
                    "bound": {"max": "20000"},
                    "warn_at": 0.8,
                    "on_breach": "refuse",
                },
            },
            "day_loss": {
                "uses": "limit",
                "params": {
                    "measure": "pnl",
                    "window": {"calendar": "session"},
                    "bound": {"min": "-500"},
                    "on_breach": "halt",
                },
            },
            "stale": {
                "uses": "limit",
                "params": {
                    "measure": "input_age_ms",
                    "bound": {"max": 30000},
                    "on_breach": "refuse",
                },
            },
            "sane": {
                "uses": "range",
                "params": {"field": "confidence", "min": 0, "max": 1, "nan": "refuse"},
            },
        },
        "execution": {
            "uses": "paper",
            "params": {"fill_rule": "touch", "fees": {"kind": "bps", "bps": 5}, "seed": 7},
            "submit_timeout_ms": 5000,
            "on_halt": {"cancel_open": True},
        },
        "accounting": {"uses": "paper", "params": {}, "max_valuation_age_ms": 60000},
        "arming": {
            "max_duration_s": 14400,
            "approval": {"uses": "deny-all", "params": {}},
        },
        "coordination": {
            "scope": {"venue": "paper", "account": "strategy-a"},
            "lease": {"uses": "process", "params": {}},
            "ttl_ms": 30000,
            "renew_every_ms": 10000,
            "renew_timeout_ms": 2000,
        },
        "reconcile": {
            "on_start": True,
            "every_s": 300,
            "on_mismatch": "halt",
            "lookback_ms": 86400000,
        },
        "monitors": {
            "pred_shift": {
                "uses": "psi",
                "params": {
                    "field": "prediction",
                    "bins": 10,
                    "reference": {"uses": "leading", "params": {"n": 500}},
                    "window": {"kind": "count", "n": 300},
                    "threshold": {"kind": "alpha", "alpha": 0.01},
                    "response": "warn",
                },
            },
            "coverage": {
                "uses": "coverage",
                "params": {
                    "window": {"kind": "count", "n": 50},
                    "threshold": {"kind": "constant", "min": 0.5},
                    "response": "warn",
                },
            },
        },
        "health": {
            "failure_threshold": 3,
            "success_threshold": 1,
            "timeout_s": 1.0,
            "probes": {
                "disk": {"uses": "ledger-writable"},
                "venue": {"uses": "executor-check", "scope": "dependency"},
            },
        },
        "durability": {"fsync": "every"},
        "resilience": {
            "retry": {
                "max_attempts": 3,
                "base_s": 0.05,
                "throttle_base_s": 1.0,
                "cap_s": 20.0,
                "jitter": "full",
                "retry_after": "honor",
                "retry_writes": "idempotent_only",
                "budget": {
                    "capacity": 500,
                    "transient_cost": 14,
                    "throttle_cost": 5,
                    "refund": 1,
                },
            },
            "breaker": {"min_calls": 5, "failure_rate": 0.5, "open_s": 30},
            "limiter": {
                "submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
                "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True},
            },
            "transport": {"uses": "urllib", "params": {"connect_s": 2.0, "read_s": 5.0}},
        },
        "lifecycle": {"cooling_off_s": 900, "shutdown_grace_s": 30},
        "readiness": {
            "checklist": "configs/readiness.json",
            "waivers": [],
            "valid_for_s": 86400,
        },
        "heartbeat": {"every_s": 60, "in_degraded": False, "emitters": {"file": {"uses": "file"}}},
        "alerting": {
            "sinks": {"ops": {"uses": "webhook"}},
            "routes": [
                {"severity": "critical", "sinks": ["ops"]},
                {"severity": "warning", "sinks": ["ops"]},
            ],
            "group_wait_s": 30,
            "repeat_interval_s": 14400,
            "rate_limit": {"max_per_hour": 20, "burst": 5},
        },
        "alert_endpoints": {
            "ops": {"url_env": "OPS_WEBHOOK_URL", "template": "slack", "timeout_s": 5}
        },
        "placement": {
            "ledger_root": "./serve",
            "rotate": {"by": "day", "max_bytes": 268435456},
            "log_dir": "./serve/logs",
        },
        "env": {"env_file": ".env", "require": ["OPS_WEBHOOK_URL"]},
    }
    obj.update(copy.deepcopy(overrides))
    return obj


def minimal_document(**overrides):
    """The smallest shadow document: every required section, nothing optional.

    `notes`, the three optional excluded sections, every `params` block,
    `serving.replay`, `serving.max_artifact_age`, `schedule.overrun`,
    `schedule.max_venue_skew_ms`, `execution.on_halt`, the `placement`
    knobs and the three `alerting` cadence knobs are all absent — each one
    has a named default or no meaning until it is declared.
    """
    obj = {
        "name": "minimal-serve",
        "series_id": SERIES_ID,
        "rung": "shadow",
        "serving": {
            "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",
            "adapter": "yourproject",
            "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
            "heads": ["select"],
            "required_universe": "configs/universe-serve.json",
            "proposer": {"uses": "intent-rows"},
        },
        "feed": {"uses": "entry-source"},
        "schedule": {
            "clock": {"uses": "wall"},
            "calendar": {"uses": "always-open"},
            "cadence": {"uses": "fixed-interval"},
            "dead_after_ms": 600000,
            "max_staleness_ms": 120000,
            "max_quote_age_ms": 30000,
        },
        "guards": {},
        "execution": {"uses": "shadow", "submit_timeout_ms": 5000},
        "accounting": {"uses": "paper", "max_valuation_age_ms": 60000},
        "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all"}},
        "coordination": {
            "scope": {"venue": "paper", "account": "strategy-a"},
            "lease": {"uses": "process"},
            "ttl_ms": 30000,
            "renew_every_ms": 10000,
            "renew_timeout_ms": 2000,
        },
        "reconcile": {
            "on_start": True,
            "every_s": 300,
            "on_mismatch": "halt",
            "lookback_ms": 86400000,
        },
        "monitors": {},
        "health": {
            "failure_threshold": 3,
            "success_threshold": 1,
            "timeout_s": 1.0,
            "probes": {"disk": {"uses": "ledger-writable"}},
        },
        "durability": {"fsync": "every"},
        "resilience": {
            "retry": {
                "max_attempts": 3,
                "base_s": 0.05,
                "throttle_base_s": 1.0,
                "cap_s": 20.0,
                "jitter": "full",
                "retry_after": "honor",
                "retry_writes": "idempotent_only",
                "budget": {
                    "capacity": 500,
                    "transient_cost": 14,
                    "throttle_cost": 5,
                    "refund": 1,
                },
            },
            "breaker": {"min_calls": 5, "failure_rate": 0.5, "open_s": 30},
            "limiter": {
                "submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
                "cancel": {"rate_per_s": 10, "burst": 10, "reserved": True},
            },
            "transport": {"uses": "urllib"},
        },
        "lifecycle": {"cooling_off_s": 900, "shutdown_grace_s": 30},
        "readiness": {
            "checklist": "configs/readiness.json",
            "waivers": [],
            "valid_for_s": 86400,
        },
        "alerting": {
            "sinks": {"ops": {"uses": "memory"}},
            "routes": [{"severity": "critical", "sinks": ["ops"]}],
        },
        "placement": {"ledger_root": "./serve"},
    }
    obj.update(copy.deepcopy(overrides))
    return obj


def live_capable_document(rung="live_limited"):
    """A document that satisfies D9's live default-deny at any rung.

    A per-proposal size limit, a period loss limit backed by accounting, a
    non-`paper` accounting strategy and a child approval verifier — the
    four things §4.1's illustration cannot supply because it is `paper`.
    """
    obj = example_document(rung=rung)
    obj["accounting"]["uses"] = "yourproject.accounting:VenueAccounting"
    obj["arming"]["approval"]["uses"] = "yourproject.approvals:HmacVerifier"
    obj["coordination"]["lease"]["uses"] = "yourproject.leases:RedisLease"
    if rung in ("live_limited", "live"):
        obj["execution"]["uses"] = "live"
    return obj


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def set_path(obj, path, value):
    """Set a nested key by path and return the same object."""
    cur = obj
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value
    return obj


def drop_path(obj, path):
    """Delete a nested key by path and return the same object."""
    cur = obj
    for key in path[:-1]:
        cur = cur[key]
    del cur[path[-1]]
    return obj


def refusal(obj):
    """Validate `obj` and return the joined text of the single refusal."""
    with pytest.raises(ProductionError) as exc:
        ServeDocument.from_obj(obj)
    return str(exc.value)


def strip_notes_everywhere(obj):
    """The pipeline's `notes` rule, restated (never imported, see the module docstring)."""
    if isinstance(obj, dict):
        return {k: strip_notes_everywhere(v) for k, v in obj.items() if k != "notes"}
    if isinstance(obj, list):
        return [strip_notes_everywhere(v) for v in obj]
    return obj


def expected_identity_obj(obj):
    """§4.2's hash material: notes gone everywhere, the four sections dropped."""
    stripped = strip_notes_everywhere(obj)
    return {k: v for k, v in stripped.items() if k not in EXCLUDED_SECTIONS}


def expected_doc_hash(obj):
    """sha256 over the canonical JSON of the hash material — the golden value."""
    canon = json.dumps(
        expected_identity_obj(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def notes_everywhere(obj):
    """Bury a `notes` string at six different depths of the §4.1 grammar."""
    obj["notes"] = "a different top-level note"
    obj["serving"]["notes"] = "why this run is the one served"
    obj["serving"]["entry"]["notes"] = "why this window"
    obj["guards"]["size"]["notes"] = "why 100"
    obj["guards"]["size"]["params"]["notes"] = "the bound's own note"
    obj["alerting"]["sinks"]["ops"]["notes"] = "why webhook"
    obj["alerting"]["routes"][0]["notes"] = "critical pages"
    return obj


# --------------------------------------------------------------------------
# the grammar is accepted whole
# --------------------------------------------------------------------------


def test_the_section_4_1_example_validates():
    doc = ServeDocument.from_obj(example_document())
    assert doc.name == "yourproject-serve"
    assert doc.series_id == SERIES_ID
    assert doc.rung == "paper"


def test_the_minimal_shadow_document_validates():
    doc = ServeDocument.from_obj(minimal_document())
    assert doc.rung == "shadow"
    assert doc.name == "minimal-serve"


def test_from_obj_never_mutates_the_callers_object():
    obj = example_document()
    before = copy.deepcopy(obj)
    ServeDocument.from_obj(obj)
    assert obj == before


def test_round_trip_is_exact_for_the_example():
    obj = example_document()
    assert ServeDocument.from_obj(obj).to_obj() == obj


def test_round_trip_never_materialises_an_absent_optional_key():
    # Optional fields are emitted ONLY WHEN PRESENT: a default rendered into
    # to_obj() would move the identity of every document that omits it.
    obj = minimal_document()
    assert ServeDocument.from_obj(obj).to_obj() == obj


def test_to_obj_hands_out_a_copy_the_caller_cannot_mutate_back_in():
    doc = ServeDocument.from_obj(example_document())
    first = doc.to_obj()
    pinned = doc.doc_hash
    first["schedule"]["max_staleness_ms"] = 1
    first["guards"]["size"]["params"]["bound"]["max"] = "9999"
    assert doc.to_obj()["schedule"]["max_staleness_ms"] == 120000
    assert doc.to_obj()["guards"]["size"]["params"]["bound"]["max"] == "100"
    assert doc.doc_hash == pinned


def test_load_reads_a_json_file(tmp_path):
    obj = example_document()
    path = tmp_path / "serve.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    doc = ServeDocument.load(path)
    assert doc.to_obj() == obj
    assert doc.doc_hash == ServeDocument.from_obj(obj).doc_hash


def test_every_fixed_grammar_key_is_reachable_by_attribute():
    doc = ServeDocument.from_obj(example_document())
    assert doc.serving.run_dir == "pipeline_runs/train-2026-01-01-abcd1234"
    assert doc.serving.adapter == "yourproject"
    assert doc.serving.entry.node == "bars"
    assert doc.serving.entry.param == "since_ms"
    assert doc.serving.entry.window_ms == 14400000
    assert list(doc.serving.heads) == ["select"]
    assert doc.serving.required_universe == "configs/universe-serve.json"
    assert doc.serving.proposer.uses == "intent-rows"
    assert doc.serving.max_artifact_age == "P30D"
    assert doc.feed.uses == "entry-source"
    assert doc.feed.params["pull"] == "acquire"
    assert doc.schedule.clock.uses == "wall"
    assert doc.schedule.cadence.uses == "aligned-bar"
    assert doc.schedule.overrun.policy == "coalesce"
    assert doc.schedule.overrun.max_lag_ms == 30000
    assert doc.schedule.dead_after_ms == 600000
    assert doc.schedule.max_staleness_ms == 120000
    assert doc.schedule.max_quote_age_ms == 30000
    assert doc.schedule.max_venue_skew_ms == 1000
    assert doc.execution.uses == "paper"
    assert doc.execution.submit_timeout_ms == 5000
    assert doc.execution.on_halt.cancel_open is True
    assert doc.accounting.uses == "paper"
    assert doc.accounting.max_valuation_age_ms == 60000
    assert doc.arming.max_duration_s == 14400
    assert doc.arming.approval.uses == "deny-all"
    assert doc.coordination.scope.venue == "paper"
    assert doc.coordination.scope.account == "strategy-a"
    assert doc.coordination.lease.uses == "process"
    assert doc.coordination.ttl_ms == 30000
    assert doc.coordination.renew_every_ms == 10000
    assert doc.coordination.renew_timeout_ms == 2000
    assert doc.reconcile.on_start is True
    assert doc.reconcile.every_s == 300
    assert doc.reconcile.on_mismatch == "halt"
    assert doc.reconcile.lookback_ms == 86400000
    assert doc.health.failure_threshold == 3
    assert doc.health.success_threshold == 1
    assert doc.health.timeout_s == 1.0
    assert doc.durability.fsync == "every"
    assert doc.resilience.retry.max_attempts == 3
    assert doc.resilience.retry.budget.capacity == 500
    assert doc.resilience.breaker.failure_rate == 0.5
    assert doc.resilience.limiter.submit.max_in_flight == 1
    assert doc.resilience.limiter.cancel.reserved is True
    assert doc.resilience.transport.uses == "urllib"
    assert doc.lifecycle.cooling_off_s == 900
    assert doc.lifecycle.shutdown_grace_s == 30
    assert doc.readiness.checklist == "configs/readiness.json"
    assert list(doc.readiness.waivers) == []
    assert doc.readiness.valid_for_s == 86400
    assert doc.alerting.group_wait_s == 30
    assert doc.alerting.repeat_interval_s == 14400
    assert doc.alerting.rate_limit.max_per_hour == 20
    assert doc.alerting.rate_limit.burst == 5
    assert doc.heartbeat.every_s == 60
    assert doc.heartbeat.in_degraded is False
    assert doc.placement.ledger_root == "./serve"
    assert doc.placement.rotate.by == "day"
    assert doc.placement.rotate.max_bytes == 268435456
    assert doc.placement.log_dir == "./serve/logs"
    assert doc.env.env_file == ".env"
    assert list(doc.env.require) == ["OPS_WEBHOOK_URL"]


def test_user_named_maps_are_mappings_keyed_by_the_authors_names():
    doc = ServeDocument.from_obj(example_document())
    assert sorted(doc.guards) == ["day_loss", "exposure", "sane", "size", "stale"]
    assert "size" in doc.guards
    assert doc.guards["size"].uses == "limit"
    assert doc.guards["size"].params["measure"] == "quantity"
    assert sorted(doc.monitors) == ["coverage", "pred_shift"]
    assert doc.monitors["pred_shift"].uses == "psi"
    assert sorted(doc.health.probes) == ["disk", "venue"]
    assert doc.health.probes["venue"].uses == "executor-check"
    assert doc.health.probes["venue"].scope == "dependency"
    assert sorted(doc.alerting.sinks) == ["ops"]
    assert doc.alerting.sinks["ops"].uses == "webhook"
    assert sorted(doc.alert_endpoints) == ["ops"]
    assert doc.alert_endpoints["ops"].url_env == "OPS_WEBHOOK_URL"
    assert doc.alert_endpoints["ops"].template == "slack"
    assert doc.alert_endpoints["ops"].timeout_s == 5
    assert sorted(doc.heartbeat.emitters) == ["file"]
    assert doc.serving.replay["gate"] == "recorded"
    assert [r.severity for r in doc.alerting.routes] == ["critical", "warning"]
    assert list(doc.alerting.routes[0].sinks) == ["ops"]


def test_params_are_opaque_to_the_document():
    # Default-deny inside `params` is the seam class's `_PARAMS` job at
    # construction; the document cannot know a family it must not import.
    obj = example_document()
    obj["feed"]["params"]["invented_knob"] = 1
    obj["guards"]["size"]["params"]["invented_knob"] = 2
    obj["schedule"]["calendar"]["params"]["invented_knob"] = 3
    doc = ServeDocument.from_obj(obj)
    assert doc.feed.params["invented_knob"] == 1


def test_notes_are_allowed_at_every_level():
    ServeDocument.from_obj(notes_everywhere(example_document()))


# --------------------------------------------------------------------------
# default-deny
# --------------------------------------------------------------------------


def test_unknown_top_level_key_refuses_by_name():
    text = refusal(example_document(exceution={"uses": "paper"}))
    assert "exceution" in text


@pytest.mark.parametrize("section", ("outputs", "tracking", "pipeline", "ledger"))
def test_a_top_level_key_in_neither_list_refuses(section):
    # The partition cannot silently drift: a section that is neither graded
    # nor excluded is a validation error, not an ignored extra.
    assert section in refusal(example_document(**{section: {}}))


@pytest.mark.parametrize(
    "section",
    (
        "serving",
        "feed",
        "schedule",
        "execution",
        "accounting",
        "arming",
        "coordination",
        "reconcile",
        "health",
        "durability",
        "resilience",
        "lifecycle",
        "readiness",
        "alerting",
        "placement",
        "heartbeat",
        "env",
    ),
)
def test_unknown_key_inside_a_section_refuses_by_name(section):
    obj = set_path(example_document(), (section, "nonsuch"), 1)
    text = refusal(obj)
    assert "nonsuch" in text
    assert section in text


@pytest.mark.parametrize(
    "path",
    (
        ("serving", "entry", "nonsuch"),
        ("serving", "proposer", "nonsuch"),
        ("guards", "size", "nonsuch"),
        ("monitors", "coverage", "nonsuch"),
        ("health", "probes", "disk", "nonsuch"),
        ("alerting", "sinks", "ops", "nonsuch"),
        ("alerting", "rate_limit", "nonsuch"),
        ("alert_endpoints", "ops", "nonsuch"),
        ("coordination", "scope", "nonsuch"),
        ("coordination", "lease", "nonsuch"),
        ("execution", "on_halt", "nonsuch"),
        ("placement", "rotate", "nonsuch"),
        ("schedule", "overrun", "nonsuch"),
        ("resilience", "retry", "nonsuch"),
        ("resilience", "retry", "budget", "nonsuch"),
        ("resilience", "limiter", "submit", "nonsuch"),
        ("arming", "approval", "nonsuch"),
    ),
)
def test_unknown_key_nested_inside_a_section_refuses_by_name(path):
    assert "nonsuch" in refusal(set_path(example_document(), path, 1))


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_missing_required_section_refuses_by_name(section):
    obj = example_document()
    del obj[section]
    assert section in refusal(obj)


@pytest.mark.parametrize("key", ("name", "series_id", "rung"))
def test_missing_required_scalar_refuses_by_name(key):
    obj = example_document()
    del obj[key]
    assert key in refusal(obj)


def test_the_optional_sections_may_all_be_absent():
    obj = example_document()
    for section in ("notes", "heartbeat", "alert_endpoints", "env"):
        del obj[section]
    ServeDocument.from_obj(obj)


@pytest.mark.parametrize(
    "path",
    (
        ("serving", "run_dir"),
        ("serving", "adapter"),
        ("serving", "entry"),
        ("serving", "heads"),
        ("serving", "required_universe"),
        ("serving", "proposer"),
        ("serving", "entry", "node"),
        ("serving", "entry", "param"),
        ("serving", "entry", "window_ms"),
        ("serving", "proposer", "uses"),
        ("feed", "uses"),
        ("schedule", "clock"),
        ("schedule", "calendar"),
        ("schedule", "cadence"),
        ("schedule", "dead_after_ms"),
        ("schedule", "max_staleness_ms"),
        ("schedule", "max_quote_age_ms"),
        ("execution", "uses"),
        ("execution", "submit_timeout_ms"),
        ("accounting", "uses"),
        ("accounting", "max_valuation_age_ms"),
        ("arming", "max_duration_s"),
        ("arming", "approval"),
        ("arming", "approval", "uses"),
        ("coordination", "scope"),
        ("coordination", "lease"),
        ("coordination", "ttl_ms"),
        ("coordination", "renew_every_ms"),
        ("coordination", "renew_timeout_ms"),
        ("durability", "fsync"),
        ("lifecycle", "cooling_off_s"),
        ("lifecycle", "shutdown_grace_s"),
        ("readiness", "checklist"),
        ("readiness", "valid_for_s"),
        ("alerting", "sinks"),
        ("alerting", "routes"),
        ("health", "probes"),
        ("placement", "ledger_root"),
    ),
)
def test_missing_required_key_inside_a_section_refuses_by_name(path):
    assert path[-1] in refusal(drop_path(example_document(), path))


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("schedule", "max_staleness_ms"), "120000"),
        (("schedule", "dead_after_ms"), 1.5),
        (("serving", "heads"), "select"),
        (("serving", "entry", "window_ms"), "14400000"),
        (("guards",), []),
        (("monitors",), []),
        (("reconcile", "on_start"), "yes"),
        (("alerting", "routes"), {"severity": "critical"}),
        (("alerting", "sinks"), ["ops"]),
        (("readiness", "waivers"), "none"),
        (("coordination", "scope"), "paper/strategy-a"),
        (("placement", "ledger_root"), 7),
        (("execution", "on_halt", "cancel_open"), "true"),
        (("health", "failure_threshold"), "3"),
    ),
)
def test_a_wrong_type_refuses_naming_the_key(path, value):
    assert path[-1] in refusal(set_path(example_document(), path, value))


def test_validation_accumulates_every_problem_into_one_error():
    obj = example_document()
    obj["nonsuch_section"] = {}
    obj["series_id"] = "not-a-uuid"
    obj["rung"] = "production"
    del obj["durability"]
    set_path(obj, ("schedule", "nonsuch_key"), 1)
    set_path(obj, ("coordination", "scope"), {"venue": "paper"})
    set_path(obj, ("lifecycle", "shutdown_grace_s"), 4000)
    with pytest.raises(ProductionError) as exc:
        ServeDocument.from_obj(obj)
    text = str(exc.value)
    for token in (
        "nonsuch_section",
        "series_id",
        "rung",
        "durability",
        "nonsuch_key",
        "account",
        "shutdown_grace_s",
    ):
        assert token in text
    assert len(exc.value.problems) >= 7


# --------------------------------------------------------------------------
# the required identity: series UUID, rung, execution scope
# --------------------------------------------------------------------------


def test_series_id_must_be_a_uuid():
    assert "series_id" in refusal(example_document(series_id="strategy-a"))
    assert "series_id" in refusal(example_document(series_id=""))
    assert "series_id" in refusal(example_document(series_id=SERIES_ID.replace("-", "")[:-1]))
    fresh = str(uuid.uuid4())
    doc = ServeDocument.from_obj(example_document(series_id=fresh))
    assert doc.series_id == fresh
    assert str(uuid.UUID(doc.series_id)) == fresh


def test_rung_must_be_a_member_of_rungs():
    assert "rung" in refusal(example_document(rung="production"))
    assert "rung" in refusal(example_document(rung="LIVE"))
    assert "rung" in refusal(example_document(rung=None))


@pytest.mark.parametrize("rung", RUNGS)
def test_every_rung_is_a_legal_document(rung):
    assert ServeDocument.from_obj(live_capable_document(rung)).rung == rung


def test_coordination_scope_requires_a_venue_and_an_account():
    assert "venue" in refusal(
        set_path(example_document(), ("coordination", "scope"), {"account": "strategy-a"})
    )
    assert "account" in refusal(
        set_path(example_document(), ("coordination", "scope"), {"venue": "paper"})
    )
    assert "venue" in refusal(set_path(example_document(), ("coordination", "scope"), {}))
    for empty in ("", None):
        assert "venue" in refusal(
            set_path(example_document(), ("coordination", "scope", "venue"), empty)
        )


def test_ttl_must_exceed_twice_the_renewal_budget():
    # §5.7.2: ttl_ms > 2 * (renew_every_ms + renew_timeout_ms), so a missed
    # renewal deadline can never be mistaken for a still-valid permit.
    obj = example_document()
    bound = 2 * (
        obj["coordination"]["renew_every_ms"] + obj["coordination"]["renew_timeout_ms"]
    )
    assert "ttl_ms" in refusal(set_path(example_document(), ("coordination", "ttl_ms"), bound))
    assert "ttl_ms" in refusal(
        set_path(example_document(), ("coordination", "ttl_ms"), bound - 1)
    )
    ServeDocument.from_obj(set_path(example_document(), ("coordination", "ttl_ms"), bound + 1))


def test_fsync_none_is_legal_only_at_shadow():
    ServeDocument.from_obj(
        set_path(minimal_document(rung="shadow"), ("durability", "fsync"), "none")
    )
    for rung in ("paper", "live_limited", "live"):
        obj = set_path(live_capable_document(rung), ("durability", "fsync"), "none")
        text = refusal(obj)
        assert "fsync" in text
        assert "shadow" in text


def test_heartbeat_every_s_must_be_at_least_one():
    for bad in (0, -1):
        assert "every_s" in refusal(
            set_path(example_document(), ("heartbeat", "every_s"), bad)
        )
    ServeDocument.from_obj(set_path(example_document(), ("heartbeat", "every_s"), 1))


def test_a_route_naming_an_undeclared_sink_refuses():
    obj = set_path(
        example_document(),
        ("alerting", "routes"),
        [{"severity": "critical", "sinks": ["pager"]}],
    )
    text = refusal(obj)
    assert "pager" in text


def test_an_endpoint_for_an_undeclared_sink_refuses():
    obj = example_document()
    obj["alert_endpoints"]["pager"] = {"url_env": "OPS_WEBHOOK_URL"}
    assert "pager" in refusal(obj)


def test_a_sink_url_env_absent_from_env_require_refuses():
    obj = set_path(example_document(), ("env", "require"), [])
    assert "OPS_WEBHOOK_URL" in refusal(obj)
    obj = example_document()
    del obj["env"]
    assert "OPS_WEBHOOK_URL" in refusal(obj)


def test_readiness_valid_for_s_must_be_positive():
    for bad in (0, -1):
        assert "valid_for_s" in refusal(
            set_path(example_document(), ("readiness", "valid_for_s"), bad)
        )
    ServeDocument.from_obj(set_path(example_document(), ("readiness", "valid_for_s"), 1))


@pytest.mark.parametrize(
    ("path", "bad", "good"),
    (
        (("lifecycle", "shutdown_grace_s"), (0, 301, -1), (1, 30, 300)),
        (("alerting", "group_wait_s"), (-1, 601), (0, 30, 600)),
        (("alerting", "repeat_interval_s"), (59, 86401, 0), (60, 14400, 86400)),
    ),
)
def test_a_bounded_knob_refuses_outside_its_range(path, bad, good):
    for value in bad:
        assert path[-1] in refusal(set_path(example_document(), path, value))
    for value in good:
        ServeDocument.from_obj(set_path(example_document(), path, value))


def test_max_venue_skew_ms_may_be_declared_null():
    obj = set_path(example_document(), ("schedule", "max_venue_skew_ms"), None)
    doc = ServeDocument.from_obj(obj)
    assert doc.schedule.max_venue_skew_ms is None
    assert doc.to_obj()["schedule"]["max_venue_skew_ms"] is None
    # Declared-null and declared-1000 are different documents.
    assert doc.doc_hash != ServeDocument.from_obj(example_document()).doc_hash


# --------------------------------------------------------------------------
# D9 — a document that can reach a venue declares its limits
# --------------------------------------------------------------------------


def test_a_live_capable_document_validates_with_its_limits_and_accounting():
    for rung in ("live_limited", "live"):
        doc = ServeDocument.from_obj(live_capable_document(rung))
        assert doc.rung == rung


@pytest.mark.parametrize("rung", ("live_limited", "live"))
def test_live_refuses_without_a_per_proposal_size_limit(rung):
    obj = live_capable_document(rung)
    del obj["guards"]["size"]
    text = refusal(obj)
    assert "quantity" in text or "notional" in text
    assert "guards" in text


@pytest.mark.parametrize("measure", ("quantity", "notional"))
def test_either_size_measure_satisfies_the_per_proposal_limit(measure):
    obj = live_capable_document()
    obj["guards"]["size"]["params"]["measure"] = measure
    ServeDocument.from_obj(obj)


@pytest.mark.parametrize("rung", ("live_limited", "live"))
def test_live_refuses_without_a_period_loss_limit(rung):
    obj = live_capable_document(rung)
    del obj["guards"]["day_loss"]
    assert "pnl" in refusal(obj)


def test_live_refuses_a_loss_limit_with_no_window():
    obj = live_capable_document()
    del obj["guards"]["day_loss"]["params"]["window"]
    text = refusal(obj)
    assert "window" in text
    assert "pnl" in text


@pytest.mark.parametrize("rung", ("live_limited", "live"))
def test_live_refuses_paper_accounting(rung):
    obj = set_path(live_capable_document(rung), ("accounting", "uses"), "paper")
    text = refusal(obj)
    assert "accounting" in text
    assert "paper" in text


@pytest.mark.parametrize("rung", ("live_limited", "live"))
def test_live_refuses_the_deny_all_approval_default(rung):
    # §5.6: `deny-all` is the core shadow/paper default; a live document must
    # name a child verifier class, so an arm cannot be self-approved.
    obj = set_path(live_capable_document(rung), ("arming", "approval", "uses"), "deny-all")
    text = refusal(obj)
    assert "approval" in text
    assert "deny-all" in text


@pytest.mark.parametrize("rung", ("shadow", "paper"))
def test_shadow_and_paper_need_no_limits_and_may_deny_all(rung):
    obj = minimal_document(rung=rung)
    obj["guards"] = {}
    doc = ServeDocument.from_obj(obj)
    assert doc.arming.approval.uses == "deny-all"
    assert sorted(doc.guards) == []


# --------------------------------------------------------------------------
# §4.2 identity
# --------------------------------------------------------------------------


def test_the_non_identity_sections_are_exactly_the_four():
    assert PRODUCTION_NON_IDENTITY_SECTIONS == (
        "alert_endpoints",
        "heartbeat",
        "placement",
        "env",
    )


def test_the_example_top_level_accounts_for_every_key_in_the_partition():
    assert set(example_document()) == set(GRADED_SECTIONS) | set(EXCLUDED_SECTIONS) | {
        "name",
        "notes",
    }
    assert len(GRADED_SECTIONS) == 18


def test_doc_hash_is_the_golden_sha256_of_the_hash_material():
    for obj in (example_document(), minimal_document()):
        doc = ServeDocument.from_obj(obj)
        assert doc.doc_hash == expected_doc_hash(obj)
        assert len(doc.doc_hash) == 64
        assert doc.doc_hash == doc.doc_hash.lower()
        int(doc.doc_hash, 16)


def test_doc_hash_reuses_the_pipeline_recipe_and_never_a_second_one():
    # D24 rejects a second identity recipe beside `config_hash`.
    doc = ServeDocument.from_obj(example_document())
    assert doc.doc_hash == config_hash(doc, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)


def test_identity_obj_is_the_stripped_canonical_object():
    obj = notes_everywhere(example_document())
    doc = ServeDocument.from_obj(obj)
    material = doc.identity_obj()
    assert material == expected_identity_obj(obj)
    for section in EXCLUDED_SECTIONS:
        assert section not in material

    def has_notes(value):
        if isinstance(value, dict):
            return "notes" in value or any(has_notes(v) for v in value.values())
        if isinstance(value, list):
            return any(has_notes(v) for v in value)
        return False

    assert not has_notes(material)
    canon = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(canon.encode("ascii")).hexdigest() == doc.doc_hash


def test_notes_anywhere_never_change_identity():
    plain = ServeDocument.from_obj(example_document())
    annotated = ServeDocument.from_obj(notes_everywhere(example_document()))
    assert annotated.doc_hash == plain.doc_hash
    stripped = example_document()
    del stripped["notes"]
    assert ServeDocument.from_obj(stripped).doc_hash == plain.doc_hash


@pytest.mark.parametrize(
    ("section", "path", "value"),
    (
        ("placement", ("placement", "ledger_root"), "/var/lib/other-serve"),
        ("placement", ("placement", "log_dir"), "/var/log/serve"),
        ("placement", ("placement", "rotate", "by"), "size"),
        ("env", ("env", "env_file"), ".env.production"),
        ("env", ("env", "require"), ["OPS_WEBHOOK_URL", "EXTRA_TOKEN"]),
        ("heartbeat", ("heartbeat", "every_s"), 120),
        ("heartbeat", ("heartbeat", "in_degraded"), True),
        ("alert_endpoints", ("alert_endpoints", "ops", "template"), "plain"),
        ("alert_endpoints", ("alert_endpoints", "ops", "timeout_s"), 9),
    ),
)
def test_an_excluded_section_never_changes_identity(section, path, value):
    base = ServeDocument.from_obj(example_document())
    moved = ServeDocument.from_obj(set_path(example_document(), path, value))
    assert moved.to_obj() != base.to_obj()
    assert moved.doc_hash == base.doc_hash


def test_moving_a_sink_url_env_with_its_required_name_never_changes_identity():
    # §5.11: the sink's url_env must be declared in env.require, so renaming
    # the variable moves BOTH excluded sections — and still not the identity.
    base = ServeDocument.from_obj(example_document())
    doc = set_path(example_document(), ("env", "require"), ["OTHER_WEBHOOK_URL"])
    doc = set_path(doc, ("alert_endpoints", "ops", "url_env"), "OTHER_WEBHOOK_URL")
    moved = ServeDocument.from_obj(doc)
    assert moved.to_obj() != base.to_obj()
    assert moved.doc_hash == base.doc_hash


def test_dropping_an_optional_excluded_section_never_changes_identity():
    base = ServeDocument.from_obj(example_document())
    without_heartbeat = example_document()
    del without_heartbeat["heartbeat"]
    assert ServeDocument.from_obj(without_heartbeat).doc_hash == base.doc_hash
    without_endpoints = example_document()
    del without_endpoints["alert_endpoints"]
    del without_endpoints["env"]
    assert ServeDocument.from_obj(without_endpoints).doc_hash == base.doc_hash


#: One mutation per graded section — the completeness half of §4.2.
GRADED_MUTATIONS = (
    ("series_id", ("series_id",), "018f0f4e-7b21-7d3a-9c31-6d8f36d806a2"),
    ("rung", ("rung",), "paper"),
    ("serving", ("serving", "run_dir"), "pipeline_runs/train-2026-02-02-beef0000"),
    ("feed", ("feed", "params", "pull"), "store"),
    ("schedule", ("schedule", "max_staleness_ms"), 60000),
    ("guards", ("guards", "size", "params", "bound", "max"), "50"),
    ("execution", ("execution", "submit_timeout_ms"), 6000),
    ("accounting", ("accounting", "max_valuation_age_ms"), 30000),
    ("arming", ("arming", "max_duration_s"), 3600),
    ("coordination", ("coordination", "scope", "account"), "strategy-b"),
    ("reconcile", ("reconcile", "every_s"), 600),
    ("monitors", ("monitors", "coverage", "params", "threshold", "min"), 0.7),
    ("health", ("health", "failure_threshold"), 5),
    ("durability", ("durability", "fsync"), "none"),
    ("resilience", ("resilience", "retry", "max_attempts"), 5),
    ("lifecycle", ("lifecycle", "cooling_off_s"), 60),
    ("readiness", ("readiness", "valid_for_s"), 3600),
    ("alerting", ("alerting", "group_wait_s"), 45),
)


def test_the_graded_mutations_cover_every_graded_section():
    assert tuple(case[0] for case in GRADED_MUTATIONS) == GRADED_SECTIONS


@pytest.mark.parametrize(("section", "path", "value"), GRADED_MUTATIONS)
def test_every_graded_section_changes_identity(section, path, value):
    # Base at shadow so `durability.fsync: none` is a legal mutation there.
    base_obj = example_document(rung="shadow")
    base = ServeDocument.from_obj(base_obj)
    moved = ServeDocument.from_obj(set_path(example_document(rung="shadow"), path, value))
    assert moved.doc_hash != base.doc_hash
    assert moved.doc_hash == expected_doc_hash(moved.to_obj())


def test_the_display_name_is_in_the_hash_material_like_every_pipeline_config():
    # `config_hash` drops only the excluded SECTIONS; `name` is graded by the
    # recipe D24 reuses, and nothing in production may re-decide that.
    base = ServeDocument.from_obj(example_document())
    renamed = ServeDocument.from_obj(example_document(name="yourproject-serve-2"))
    assert renamed.doc_hash != base.doc_hash


def test_the_sink_kind_is_policy_while_its_endpoint_is_placement():
    # §4.2: switching a route's only sink from `webhook` to `memory` silences
    # it exactly as effectively as emptying `routes`, so the kind is graded.
    base = ServeDocument.from_obj(example_document())
    rekinded = ServeDocument.from_obj(
        set_path(example_document(), ("alerting", "sinks", "ops", "uses"), "memory")
    )
    assert rekinded.doc_hash != base.doc_hash
    rerouted = ServeDocument.from_obj(
        set_path(
            example_document(),
            ("alerting", "routes"),
            [{"severity": "critical", "sinks": ["ops"]}],
        )
    )
    assert rerouted.doc_hash != base.doc_hash


def test_key_order_never_changes_identity():
    obj = example_document()
    reordered = {k: obj[k] for k in sorted(obj, reverse=True)}
    assert ServeDocument.from_obj(reordered).doc_hash == ServeDocument.from_obj(obj).doc_hash


def test_a_non_finite_number_refuses_rather_than_reaching_the_hash():
    # `config_hash` refuses NaN with its OWN error type; the document must
    # catch it first so every refusal a caller sees is a ProductionError.
    assert "timeout_s" in refusal(
        set_path(example_document(), ("health", "timeout_s"), float("nan"))
    )
    assert "window_ms" in refusal(
        set_path(example_document(), ("serving", "entry", "window_ms"), float("inf"))
    )
