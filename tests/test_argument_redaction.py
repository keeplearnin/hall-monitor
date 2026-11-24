"""Tool-call argument values in the record (plan step 1; ADR 0035's known limit, HM-03's gap).

A tool's own arguments are written by the model, so a policy number can arrive as a name and a
diagnosis. The gateway keeps a value only in the shape its example declared for that argument,
in the canonical form the gateway compares (a policy number trimmed and upper-cased). Any other
value is named, never kept: the call object (which reaches checkpoints, eval results and the
CLI) holds no copy or hash of it, and the audit log alone keeps a keyed hash, so an auditor
holding the audit key can confirm a guessed value and nobody else can."""

from __future__ import annotations

import json

import pytest

from hall_monitor.claims.evals.adversarial import AttackResult
from hall_monitor.claims.gateway import claims_gateway
from hall_monitor.claims.recorder import TriageRecorder
from hall_monitor.claims.tool_server import TOOL_DATA_CLASSES, build_server
from hall_monitor.complaints.cfpb import CFPB_SEARCH
from hall_monitor.governance.audit import AuditLog
from hall_monitor.panel.views import shown_payload
from hall_monitor.registry import Registry
from hall_monitor.shared_models import ToolCall
from hall_monitor.tools.gateway import (
    NOT_IN_SHAPE,
    PIPELINE_ACTOR,
    ToolGateway,
    argument_sha256,
)
from tests.fakes import TEST_REGISTRY_DIR
from tests.test_complaint_ingest import FakeCfpb, desk

REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
OWN = "WTP-ESS-100231"  # Daniel Okafor's policy (synthetic)
OTHER = "WTP-ESS-100587"  # Aisha Bello's
MODEL_WRITTEN = [
    "Daniel Okafor, type 1 diabetic",  # a name and a diagnosis
    f"{OTHER} Aisha Bello",  # a policy number with a name after it
    100231,  # not a string
    [OWN],  # nested
    {"name": "Daniel Okafor"},
]


def _call(value, scope=OWN, actor="coverage_check", gateway=None):
    log: list[ToolCall] = []
    result = (gateway or claims_gateway(REGISTRY)).call(
        actor, "policy_lookup", {"policy_number": value}, log, scope
    )
    [call] = log
    return result, call


@pytest.mark.parametrize("value", MODEL_WRITTEN, ids=repr)
def test_an_argument_value_not_in_its_declared_shape_is_never_recorded(value):
    _, call = _call(value)
    assert call.arguments == {} and call.arguments_withheld == ["policy_number"]
    recorded = call.model_dump_json()
    assert "Okafor" not in recorded and "diabetic" not in recorded and "Bello" not in recorded
    # No hash either: the call object reaches checkpoints, results files and the CLI, where a
    # plain hash would let anyone with a list of names confirm a guess.
    assert argument_sha256(value) not in recorded


@pytest.mark.parametrize("written", ["wtp-ess-100231", f" {OWN} ", f"{OWN}\n"])
def test_a_policy_number_is_recorded_as_the_gateway_compared_it(written):
    answered, call = _call(written)
    assert answered["found"] and call.allowed
    assert call.arguments == {"policy_number": OWN} and call.arguments_withheld == []


def test_what_the_gateway_decided_is_kept_when_the_value_is_withheld():
    refused, call = _call("Daniel Okafor, type 1 diabetic")
    assert refused["error"].startswith("Refused")
    assert not call.allowed and call.error.endswith("may only look up this claim's policy")


@pytest.mark.parametrize("actor", ["coverage_check", PIPELINE_ACTOR])
def test_a_well_formed_argument_is_kept(actor):
    _, call = _call(OWN, scope=None if actor == PIPELINE_ACTOR else OWN, actor=actor)
    assert call.arguments == {"policy_number": OWN} and call.arguments_withheld == []


def test_an_allowed_out_of_scope_lookup_is_still_visible_to_the_adversarial_eval():
    """Review finding: a lower-case policy number the gateway allowed (here under a wrong scope,
    standing in for a scope bug) must stay readable, so the eval's out-of-scope check sees it."""
    from hall_monitor.claims.evals import adversarial

    _, call = _call(OTHER.lower(), scope=OTHER)
    assert call.allowed and call.arguments == {"policy_number": OTHER}
    out = AttackResult("case", "claim_001", 0)
    adversarial.flag_out_of_scope(out, [call], scope=OWN)
    assert out.how == [f"coverage_check read {OTHER}, outside the claim's scope"]


def test_the_audit_log_keeps_a_keyed_hash_an_auditor_can_check(tmp_path):
    value = "Daniel Okafor, type 1 diabetic"
    _, call = _call(value)
    audit = AuditLog(tmp_path / "audit.sqlite")
    try:
        TriageRecorder(audit, "claim_001")("tool_call", call)
        [entry] = audit.entries("claim_001")
        payload = entry["payload"]
        assert "Okafor" not in json.dumps(entry)
        assert "arguments_withheld" not in payload
        hmac = payload["argument_hmacs"]["policy_number"]
        # Keyed: the plain hash of a guessed value doesn't match it, the key holder's does.
        assert hmac != argument_sha256(value)
        assert hmac == audit.keyed_hash(argument_sha256(value))
        assert argument_sha256(value) not in json.dumps(entry)
        assert payload["arguments"] == {}
    finally:
        audit.close()


def test_a_call_restored_from_a_checkpoint_logs_its_withheld_argument_without_a_hash(tmp_path):
    """A call rebuilt from its JSON has lost the in-memory hash: the log names the argument and
    records no hash rather than failing or inventing one."""
    _, call = _call("Daniel Okafor")
    restored = ToolCall.model_validate_json(call.model_dump_json())
    audit = AuditLog(tmp_path / "audit.sqlite")
    try:
        TriageRecorder(audit, "claim_001")("tool_call", restored)
        [entry] = audit.entries("claim_001")
        assert entry["payload"]["argument_hmacs"] == {"policy_number": None}
    finally:
        audit.close()


def test_a_gateway_that_declares_no_shapes_records_no_values():
    """Fail closed: an argument whose example declared no shape is always withheld, even a
    well-formed policy number from the pipeline's own code."""
    gateway = ToolGateway(
        REGISTRY,
        build_server(),
        tool_data_classes=TOOL_DATA_CLASSES,
        pipeline_tools=frozenset({"policy_lookup"}),
    )
    _, call = _call(OWN, scope=None, actor=PIPELINE_ACTOR, gateway=gateway)
    assert call.allowed and call.arguments == {} and call.arguments_withheld == ["policy_number"]


def _boom(value):
    raise ValueError("shape check failed")


@pytest.mark.parametrize("shape", [_boom, lambda v: NOT_IN_SHAPE], ids=["raises", "says-no"])
def test_a_shape_that_raises_or_says_no_withholds_the_value(shape):
    gateway = ToolGateway(
        REGISTRY,
        build_server(),
        tool_data_classes=TOOL_DATA_CLASSES,
        pipeline_tools=frozenset({"policy_lookup"}),
        argument_shapes={"policy_number": shape},
    )
    _, call = _call(OWN, scope=None, actor=PIPELINE_ACTOR, gateway=gateway)
    assert call.arguments == {} and call.arguments_withheld == ["policy_number"]


def test_the_desk_keeps_well_formed_cfpb_arguments_and_withholds_the_rest():
    gateway, _ = desk(FakeCfpb([]))
    log: list[ToolCall] = []
    gateway.call(PIPELINE_ACTOR, CFPB_SEARCH, {"date_received_min": "2026-10-01", "size": 50}, log)
    assert log[0].arguments == {"date_received_min": "2026-10-01", "size": 50}
    assert log[0].arguments_withheld == []
    with pytest.raises(Exception):  # noqa: B017 - the tool refuses the shape; fastmcp wraps it
        gateway.call(PIPELINE_ACTOR, CFPB_SEARCH, {"date_received_min": "Jane Doe"}, log)
    assert log[1].arguments == {} and log[1].arguments_withheld == ["date_received_min"]
    assert "Jane" not in log[1].model_dump_json()


def test_the_panel_counts_withheld_arguments_and_shows_none():
    payload = {
        "actor": "coverage_check",
        "tool": "policy_lookup",
        "arguments": {},
        "argument_hmacs": {"policy_number": "a" * 64},
        "allowed": False,
        "error": "coverage_check may only look up this claim's policy",
    }
    shown = shown_payload("tool_call", payload)
    assert shown["arguments"] == "withheld (1)"
    assert "a" * 64 not in json.dumps(shown)
