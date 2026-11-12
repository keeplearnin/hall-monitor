"""HM-29: when an agent hands work to another, the pass only narrows."""

from __future__ import annotations

import pytest

from hall_monitor.delegation import (
    MAX_HANDOFFS,
    DelegationError,
    Grant,
    GrantBook,
)
from hall_monitor.governance.audit import AuditLog
from hall_monitor.kill_switch import add_stop
from hall_monitor.registry import AgentManifest, DataClass, Registry, RegistryError
from hall_monitor.tools.gateway import ToolGateway

ALL = ("pii", "health", "financial")
POLICY = {"policy_number": "WTP-ESS-100231"}


def _manifest(agent_id: str, tools=(), data=(), delegates=()) -> AgentManifest:
    return AgentManifest(
        id=agent_id,
        name=agent_id,
        version="0.1.0",
        owner="own-501",
        purpose="test",
        risk_tier="low",
        status="active",
        model="claude-haiku-5-5",
        allowed_tools=tools,
        data_classes=data,
        may_delegate_to=delegates,
    )


CREW = [
    _manifest("lead", ("policy_lookup", "claims_history"), ALL, ("researcher", "analyst")),
    _manifest("researcher", ("policy_lookup",), ()),
    _manifest("analyst", ("policy_lookup", "claims_history"), ALL, ("sub",)),
    _manifest("sub", ("policy_lookup", "claims_history"), ALL, ("subsub", "analyst", "lead")),
    _manifest("subsub", ("policy_lookup",), ("pii", "financial")),
    _manifest("loner", ("policy_lookup",), ("pii", "financial")),
]


@pytest.fixture
def stops(tmp_path):
    path = tmp_path / "stops.yaml"
    path.write_text("stops: []\n")
    return path


@pytest.fixture
def registry(stops):
    return Registry(CREW, stops_path=stops)


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    yield log
    log.close()


@pytest.fixture
def book(registry, audit):
    return GrantBook(registry, "cfpb-1001", audit=audit)


def _events(audit, event):
    return [e for e in audit.entries("cfpb-1001") if e["event"] == event]


# 1. Only code issues the first grant, within the lead's manifest.


def test_the_first_grant_holds_the_lead_s_manifest_and_is_logged(book, audit):
    root = book.issue("lead", budget_usd=1.0)
    assert root.holder == "lead"
    assert root.chain == ("pipeline", "lead")
    assert set(root.tools) == {"policy_lookup", "claims_history"}
    assert set(root.data_classes) == set(DataClass)
    [entry] = _events(audit, "delegation_granted")
    assert entry["actor"] == "pipeline"
    assert entry["payload"]["grant"] == root.grant_id


def test_a_case_has_one_first_grant(book):
    book.issue("lead", budget_usd=1.0)
    with pytest.raises(DelegationError, match="already"):
        book.issue("analyst", budget_usd=1.0)


def test_a_scope_must_be_a_plain_id(registry):
    with pytest.raises(ValueError):
        GrantBook(registry, "../cfpb 1")


# 2 and 3. Handoffs are declared, and a child grant is never wider than its parent.


def test_a_child_grant_defaults_to_the_overlap_of_parent_and_manifest(book):
    root = book.issue("lead", budget_usd=1.0)
    child = book.delegate(root, "researcher", budget_usd=0.2)
    assert child.tools == ("policy_lookup",)
    assert child.data_classes == ()
    assert child.chain == ("pipeline", "lead", "researcher")
    assert child.parent == root.grant_id


def test_a_handoff_not_in_the_manifest_is_refused_and_logged(book, audit):
    root = book.issue("lead", budget_usd=1.0)
    with pytest.raises(DelegationError, match="may not delegate"):
        book.delegate(root, "loner", budget_usd=0.1)
    [entry] = _events(audit, "delegation_refused")
    assert entry["actor"] == "lead"
    assert entry["payload"]["to"] == "loner"
    assert entry["payload"]["code"] == "not_declared"


def test_a_helper_with_no_declared_handoffs_can_t_delegate(book):
    root = book.issue("lead", budget_usd=1.0)
    researcher = book.delegate(root, "researcher", budget_usd=0.2)
    with pytest.raises(DelegationError, match="may not delegate"):
        book.delegate(researcher, "analyst", budget_usd=0.1)


def test_asking_for_a_tool_the_helper_s_manifest_lacks_is_refused_not_trimmed(book, audit):
    root = book.issue("lead", budget_usd=1.0)
    with pytest.raises(DelegationError, match="claims_history"):
        book.delegate(root, "researcher", tools=("policy_lookup", "claims_history"), budget_usd=0.1)
    assert _events(audit, "delegation_refused")[0]["payload"]["code"] == "wider_than_allowed"


def test_a_helper_never_gets_more_than_the_agent_that_asked(book):
    """The confused deputy: sub's own manifest allows health data, but the analyst handing it
    work wasn't given health data, so sub can't have it either."""
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(
        root, "analyst", tools=("policy_lookup",), data_classes=("pii", "financial"), budget_usd=0.5
    )
    with pytest.raises(DelegationError, match="health"):
        book.delegate(analyst, "sub", data_classes=("pii", "health"), budget_usd=0.1)
    with pytest.raises(DelegationError, match="claims_history"):
        book.delegate(analyst, "sub", tools=("claims_history",), budget_usd=0.1)
    sub = book.delegate(analyst, "sub", budget_usd=0.1)
    assert sub.tools == ("policy_lookup",)
    assert set(sub.data_classes) == {DataClass.PII, DataClass.FINANCIAL}


# 4. No loops, a fixed depth.


def test_an_agent_already_in_the_chain_can_t_be_handed_work_again(book, audit):
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(root, "analyst", budget_usd=0.5)
    sub = book.delegate(analyst, "sub", budget_usd=0.2)
    for back in ("analyst", "lead"):
        with pytest.raises(DelegationError, match="already in the chain"):
            book.delegate(sub, back, budget_usd=0.01)
    assert {e["payload"]["code"] for e in _events(audit, "delegation_refused")} == {"loop"}


def test_a_chain_stops_two_handoffs_below_the_pipeline(book):
    assert MAX_HANDOFFS == 2
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(root, "analyst", budget_usd=0.5)
    sub = book.delegate(analyst, "sub", budget_usd=0.2)
    with pytest.raises(DelegationError, match="too deep"):
        book.delegate(sub, "subsub", budget_usd=0.1)


# 5. Allotments only shrink.


def test_children_s_allotments_can_t_add_up_to_more_than_the_parent_s(book):
    root = book.issue("lead", budget_usd=1.0)
    book.delegate(root, "researcher", budget_usd=0.6)
    with pytest.raises(DelegationError, match="allotment"):
        book.delegate(root, "analyst", budget_usd=0.5)
    book.delegate(root, "analyst", budget_usd=0.4)


@pytest.mark.parametrize("budget", [-0.1, float("nan"), float("inf")])
def test_an_allotment_must_be_a_real_non_negative_amount(book, budget):
    with pytest.raises((DelegationError, ValueError)):
        book.issue("lead", budget_usd=budget)


# 6. Every use checks the whole chain.


def test_stopping_the_lead_stops_every_helper_working_for_it(book, stops):
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(root, "analyst", budget_usd=0.5)
    sub = book.delegate(analyst, "sub", budget_usd=0.2)
    book.check(sub, tool="policy_lookup")
    add_stop(stops, agent="lead", by="own-501", reason="test")
    for grant in (root, analyst, sub):
        with pytest.raises(RegistryError, match="stopped"):
            book.check(grant, tool="policy_lookup")
    with pytest.raises(RegistryError, match="stopped"):
        book.delegate(analyst, "sub", budget_usd=0.01)


def test_a_stopped_helper_can_t_be_handed_work(book, stops):
    root = book.issue("lead", budget_usd=1.0)
    add_stop(stops, agent="researcher", by="own-501", reason="test")
    with pytest.raises(RegistryError, match="stopped"):
        book.delegate(root, "researcher", budget_usd=0.1)


# 7. The book is the authority.


def test_a_grant_the_book_didn_t_issue_is_refused(book):
    root = book.issue("lead", budget_usd=1.0)
    forged = Grant(
        scope="cfpb-1001",
        holder="analyst",
        chain=("pipeline", "analyst"),
        tools=("claims_history",),
        data_classes=ALL,
        budget_usd=1.0,
        parent=None,
    )
    widened = root.model_copy(update={"tools": ("policy_lookup", "claims_history", "x")})
    for grant in (forged, widened):
        with pytest.raises(DelegationError, match="not issued"):
            book.check(grant, tool="policy_lookup")
        with pytest.raises(DelegationError, match="not issued"):
            book.delegate(grant, "sub", budget_usd=0.1)


def test_a_grant_from_another_case_is_refused(registry):
    other = GrantBook(registry, "cfpb-2002")
    grant = other.issue("lead", budget_usd=1.0)
    book = GrantBook(registry, "cfpb-1001")
    with pytest.raises(DelegationError, match="not issued"):
        book.check(grant, tool="policy_lookup")


def test_closing_the_book_ends_every_grant(book):
    root = book.issue("lead", budget_usd=1.0)
    child = book.delegate(root, "researcher", budget_usd=0.1)
    book.close()
    for grant in (root, child):
        with pytest.raises(DelegationError, match="closed"):
            book.check(grant, tool="policy_lookup")
    with pytest.raises(DelegationError, match="closed"):
        book.delegate(root, "analyst", budget_usd=0.1)


def test_a_check_refuses_a_tool_or_data_class_outside_the_grant(book):
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(
        root, "analyst", tools=("policy_lookup",), data_classes=("pii",), budget_usd=0.1
    )
    with pytest.raises(DelegationError, match="claims_history"):
        book.check(analyst, tool="claims_history")
    with pytest.raises(DelegationError, match="financial"):
        book.check(analyst, carries=frozenset({DataClass.PII, DataClass.FINANCIAL}))


# 8. Agents in a crew can't work without a grant (the tool gateway).


def test_the_registry_knows_which_agents_are_in_a_crew(registry):
    assert {m.id for m in registry if registry.in_crew(m.id)} == {
        "lead",
        "researcher",
        "analyst",
        "sub",
        "subsub",
    }


def test_a_crew_agent_s_tool_call_without_a_grant_is_refused(registry):
    gateway = ToolGateway(registry)
    log = []
    result = gateway.call("lead", "policy_lookup", POLICY, log)
    assert "Refused" in result["error"]
    assert log[0].allowed is False
    assert "grant" in log[0].error


def test_a_crew_agent_s_tool_call_with_a_grant_goes_through(registry, book):
    gateway = ToolGateway(registry)
    root = book.issue("lead", budget_usd=1.0)
    log = []
    result = gateway.call("lead", "policy_lookup", POLICY, log, grants=book, grant=root)
    assert result.get("found") is True
    assert log[0].allowed is True


def test_the_gateway_refuses_what_the_grant_doesn_t_hold_though_the_manifest_does(registry, book):
    gateway = ToolGateway(registry)
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(
        root,
        "analyst",
        tools=("policy_lookup", "claims_history"),
        data_classes=("pii", "financial"),
        budget_usd=0.1,
    )
    log = []
    # claims_history returns health data; the analyst's manifest allows it, its grant doesn't.
    result = gateway.call("analyst", "claims_history", POLICY, log, grants=book, grant=analyst)
    assert "Refused" in result["error"]
    assert log[0].allowed is False


def test_the_gateway_refuses_a_grant_held_by_another_agent(registry, book):
    gateway = ToolGateway(registry)
    root = book.issue("lead", budget_usd=1.0)
    log = []
    result = gateway.call("analyst", "policy_lookup", POLICY, log, grants=book, grant=root)
    assert "Refused" in result["error"]


def test_the_gateway_needs_the_book_and_the_grant_together(registry, book):
    gateway = ToolGateway(registry)
    root = book.issue("lead", budget_usd=1.0)
    log = []
    result = gateway.call("lead", "policy_lookup", POLICY, log, grant=root)
    assert "Refused" in result["error"]


def test_a_crew_agent_is_offered_only_its_grant_s_tools(registry, book):
    gateway = ToolGateway(registry)
    root = book.issue("lead", budget_usd=1.0)
    analyst = book.delegate(
        root,
        "analyst",
        tools=("policy_lookup", "claims_history"),
        data_classes=("pii", "financial"),
        budget_usd=0.1,
    )
    assert [d["name"] for d in gateway.definitions("analyst", grants=book, grant=analyst)] == [
        "policy_lookup"
    ]
    assert gateway.definitions("analyst") == []


def test_agents_outside_a_crew_work_as_before(registry):
    gateway = ToolGateway(registry)
    log = []
    result = gateway.call("loner", "policy_lookup", POLICY, log)
    assert result.get("found") is True


# 9. The log holds IDs and names only.


def test_delegation_entries_hold_ids_names_and_amounts_only(book, audit):
    root = book.issue("lead", budget_usd=1.0)
    book.delegate(root, "analyst", budget_usd=0.5)
    with pytest.raises(DelegationError):
        book.delegate(root, "loner", budget_usd=0.1)
    granted = {"grant", "parent", "holder", "chain", "tools", "data_classes", "budget_usd"}
    refused = {"parent", "to", "code", "tools", "data_classes", "budget_usd"}
    for entry in _events(audit, "delegation_granted"):
        assert set(entry["payload"]) == granted
    for entry in _events(audit, "delegation_refused"):
        assert set(entry["payload"]) == refused
    assert audit.verify().ok


# The manifest field.


def test_an_empty_handoff_list_leaves_the_digest_unchanged():
    with_field = _manifest("loner", ("policy_lookup",), ("pii",))
    fields = with_field.model_dump(mode="json")
    fields.pop("may_delegate_to")
    import hashlib
    import json

    expected = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert with_field.digest() == expected
    assert _manifest("lead", delegates=("researcher",)).digest() != _manifest("lead").digest()


def test_a_manifest_can_t_delegate_to_itself():
    with pytest.raises(ValueError):
        _manifest("lead", delegates=("lead",))


def test_a_handoff_to_an_unregistered_agent_is_refused_when_the_registry_loads():
    with pytest.raises(ValueError, match="ghost"):
        Registry([_manifest("lead", delegates=("ghost",))])
