"""The kill switch: an agent's owner can stop it at once, and only a reviewed change restarts it."""

import shutil

import pytest
import yaml

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.kill_switch import (
    STOPS_FILENAME,
    AppendOnlyError,
    add_stop,
    check_append_only,
)
from hall_monitor.models import Route
from hall_monitor.pipeline import triage
from hall_monitor.registry import (
    APPROVALS_FILENAME,
    DEFAULT_REGISTRY_DIR,
    OWNERS_FILENAME,
    AgentStoppedError,
    Registry,
    StopListError,
    load_owners,
)
from hall_monitor.tools.gateway import ToolGateway
from tests.fakes import fake_client, outputs_from_truth

OWNER = "own-301"  # owns intake in the shipped registry


@pytest.fixture
def registry_dir(tmp_path):
    path = tmp_path / "registry"
    shutil.copytree(DEFAULT_REGISTRY_DIR, path)
    return path


def _stop(path, agent="intake", by=OWNER, stop_id="stop-0001"):
    add_stop(path / STOPS_FILENAME, stop_id=stop_id, agent=agent, by=by, reason="test")


def _write_stops(path, stops):
    (path / STOPS_FILENAME).write_text(yaml.safe_dump({"stops": stops}))


# --- Owners --------------------------------------------------------------------------


def test_every_agent_is_owned_by_a_named_person():
    people = {p["id"] for p in load_owners()}
    for manifest in Registry.from_dir():
        assert manifest.owner in people, manifest.id


def test_a_team_as_owner_is_refused(registry_dir):
    edited = registry_dir / "intake.yaml"
    edited.write_text(edited.read_text().replace(f"owner: {OWNER}", "owner: claims-ai-platform"))
    with pytest.raises(ValueError, match="owner"):
        Registry.from_dir(registry_dir)


# --- Stopping ------------------------------------------------------------------------


def test_the_shipped_registry_has_no_stops():
    assert yaml.safe_load((DEFAULT_REGISTRY_DIR / STOPS_FILENAME).read_text()) == {"stops": []}


def test_a_stop_takes_effect_on_the_next_call(registry_dir):
    registry = Registry.from_dir(registry_dir)  # loaded before the stop, as a running process is
    registry.require("intake")
    _stop(registry_dir)
    with pytest.raises(AgentStoppedError, match="stop-0001"):
        registry.require("intake")


def test_a_stop_needs_no_approval(registry_dir):
    approvals = (registry_dir / APPROVALS_FILENAME).read_text()
    _stop(registry_dir)
    registry = Registry.from_dir(registry_dir)
    assert (registry_dir / APPROVALS_FILENAME).read_text() == approvals
    registry.require("coverage_check")  # the approvals still hold; only intake stopped


def test_a_stopped_agent_cannot_call_tools(registry_dir):
    registry = Registry.from_dir(registry_dir)
    registry.check_tool("coverage_check", "policy_lookup")
    _stop(registry_dir, agent="coverage_check")
    with pytest.raises(AgentStoppedError):
        registry.check_tool("coverage_check", "policy_lookup")


def test_a_stopped_agent_stops_the_claim_and_sends_it_to_a_person(registry_dir):
    registry = Registry.from_dir(registry_dir)
    _stop(registry_dir, agent="risk_fraud", by="own-302")
    claim = next(iter_claims())
    client = fake_client(outputs_from_truth(load_ground_truth(claim.claim_id)))

    result = triage(
        claim,
        registry=registry,
        gateway=ToolGateway(registry),
        wording=load_policy_wording(),
        client=client,
    )

    assert client.messages.formats() == ["IntakeExtraction", "CoverageFinding"]
    assert "stopped" in result.stopped_reason and "risk_fraud" in result.stopped_reason
    assert result.routing.route is Route.ADJUSTER


def test_the_refusal_names_the_stop_but_not_its_reason(registry_dir):
    add_stop(
        registry_dir / STOPS_FILENAME,
        stop_id="stop-0001",
        agent="intake",
        by=OWNER,
        reason="complaint from Jane Doe about claim_001",
    )
    with pytest.raises(AgentStoppedError) as refused:
        Registry.from_dir(registry_dir).require("intake")
    assert "stop-0001" in str(refused.value) and OWNER in str(refused.value)
    assert "Jane" not in str(refused.value)


# --- Restarting ----------------------------------------------------------------------


def test_clearing_the_stop_in_a_reviewed_approval_restarts_it(registry_dir):
    _stop(registry_dir)
    approvals_file = registry_dir / APPROVALS_FILENAME
    approvals = yaml.safe_load(approvals_file.read_text())
    approvals["agents"]["intake"]["cleared_stops"] = ["stop-0001"]
    approvals_file.write_text(yaml.safe_dump(approvals))

    Registry.from_dir(registry_dir).require("intake")


def test_clearing_another_agents_stop_does_not_restart_this_one(registry_dir):
    _stop(registry_dir)
    approvals_file = registry_dir / APPROVALS_FILENAME
    approvals = yaml.safe_load(approvals_file.read_text())
    approvals["agents"]["critic"]["cleared_stops"] = ["stop-0001"]
    approvals_file.write_text(yaml.safe_dump(approvals))

    with pytest.raises(AgentStoppedError):
        Registry.from_dir(registry_dir).require("intake")


def test_a_second_stop_after_a_restart_stops_it_again(registry_dir):
    _stop(registry_dir)
    approvals_file = registry_dir / APPROVALS_FILENAME
    approvals = yaml.safe_load(approvals_file.read_text())
    approvals["agents"]["intake"]["cleared_stops"] = ["stop-0001"]
    approvals_file.write_text(yaml.safe_dump(approvals))
    registry = Registry.from_dir(registry_dir)
    registry.require("intake")

    _stop(registry_dir, stop_id="stop-0002")
    with pytest.raises(AgentStoppedError, match="stop-0002"):
        registry.require("intake")


def test_regenerating_approvals_keeps_cleared_stops():
    from hall_monitor.approvals import current_approvals

    previous = {"intake": {"cleared_stops": ["stop-0001"]}}
    assert current_approvals(previous)["intake"]["cleared_stops"] == ["stop-0001"]
    assert "cleared_stops" not in current_approvals(previous)["critic"]


def test_resume_adds_the_stop_to_the_agents_approval():
    from hall_monitor.approvals import current_approvals

    agents = current_approvals({}, resume={"intake": "stop-0003"})
    assert agents["intake"]["cleared_stops"] == ["stop-0003"]


# --- Fail closed ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "stops",
    [
        "stops: [",  # not YAML
        "stops: nope",
        yaml.safe_dump({"stops": [{"id": "stop-0001", "agent": "intake"}]}),  # fields missing
    ],
    ids=["not-yaml", "not-a-list", "incomplete"],
)
def test_an_unreadable_stop_list_refuses_every_agent(registry_dir, stops):
    registry = Registry.from_dir(registry_dir)
    (registry_dir / STOPS_FILENAME).write_text(stops)
    for agent in ("intake", "critic"):
        with pytest.raises(StopListError):
            registry.require(agent)


def test_a_missing_stop_list_refuses_every_agent(registry_dir):
    registry = Registry.from_dir(registry_dir)
    (registry_dir / STOPS_FILENAME).unlink()
    with pytest.raises(StopListError):
        registry.require("critic")


def test_a_stop_for_an_unknown_agent_refuses_every_agent(registry_dir):
    """A typo in the agent's name must not leave the intended agent running unnoticed."""
    registry = Registry.from_dir(registry_dir)
    _stop(registry_dir, agent="intak")
    with pytest.raises(StopListError, match="intak"):
        registry.require("critic")


def test_a_stop_by_someone_not_listed_refuses_every_agent(registry_dir):
    registry = Registry.from_dir(registry_dir)
    _stop(registry_dir, by="Jane Doe")
    with pytest.raises(StopListError):
        registry.require("critic")


def test_duplicate_stop_ids_refuse_every_agent(registry_dir):
    registry = Registry.from_dir(registry_dir)
    _write_stops(registry_dir, [_entry(), _entry(agent="critic")])
    with pytest.raises(StopListError):
        registry.require("coverage_check")


def test_stop_ids_cannot_be_reused(registry_dir):
    _stop(registry_dir)
    with pytest.raises(ValueError, match="stop-0001"):
        _stop(registry_dir, agent="critic")


def test_owners_and_stops_are_not_manifests(registry_dir):
    assert {OWNERS_FILENAME, STOPS_FILENAME} <= {
        f.name for f in registry_dir.glob("*.yaml")
    }  # they sit beside the manifests
    assert len(Registry.from_dir(registry_dir)) == 5


# --- Append only ---------------------------------------------------------------------


def _entry(stop_id="stop-0001", agent="intake"):
    return {
        "id": stop_id,
        "agent": agent,
        "stopped_by": OWNER,
        "stopped_at": "2026-11-05T10:00:00Z",
        "reason": "test",
    }


def test_adding_a_stop_is_append_only():
    check_append_only({"stops": [_entry()]}, {"stops": [_entry(), _entry("stop-0002")]})


@pytest.mark.parametrize(
    "head",
    [
        {"stops": []},
        {"stops": [_entry(agent="critic")]},
        {"stops": [_entry("stop-0002")]},
    ],
    ids=["removed", "edited", "replaced"],
)
def test_removing_or_editing_a_stop_fails_the_check(head):
    with pytest.raises(AppendOnlyError, match="stop-0001"):
        check_append_only({"stops": [_entry()]}, head)


def test_the_append_only_check_runs_from_the_command_line(tmp_path, capsys):
    from hall_monitor.kill_switch import main

    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump({"stops": [_entry()]}))
    head = tmp_path / "head.yaml"
    head.write_text(yaml.safe_dump({"stops": []}))

    assert main(["check-append-only", str(base), str(head)]) == 1
    assert main(["check-append-only", str(base), str(base)]) == 0


def test_the_stop_command_stops_the_agent(registry_dir):
    from hall_monitor.kill_switch import main

    registry = Registry.from_dir(registry_dir)
    stops = str(registry_dir / STOPS_FILENAME)
    assert main(["stop", "intake", "--by", OWNER, "--reason", "test", "--stops", stops]) == 0
    with pytest.raises(AgentStoppedError, match="stop-0001"):
        registry.require("intake")


@pytest.mark.parametrize(
    "args", [["intak", "--by", OWNER], ["intake", "--by", "Jane Doe"]], ids=["agent", "owner"]
)
def test_the_stop_command_refuses_a_typo_instead_of_stopping_everything(registry_dir, args):
    from hall_monitor.kill_switch import main

    before = (registry_dir / STOPS_FILENAME).read_text()
    with pytest.raises(SystemExit):
        main(["stop", *args, "--reason", "test", "--stops", str(registry_dir / STOPS_FILENAME)])
    assert (registry_dir / STOPS_FILENAME).read_text() == before


def test_a_stop_during_an_agents_tool_rounds_halts_its_next_model_request(registry_dir):
    """A refused tool call goes back to the model as an error, so the loop must check too."""
    from types import SimpleNamespace

    from hall_monitor.agents import coverage_check
    from hall_monitor.agents.base import ToolAccess, run_agent
    from hall_monitor.models import CoverageFinding

    registry = Registry.from_dir(registry_dir)
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            _stop(registry_dir, agent="coverage_check")  # the owner stops it mid-claim
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use", id="t1", name="policy_lookup", input={"policy_number": "x"}
                )
            ],
            parsed_output=None,
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    access = ToolAccess(ToolGateway(registry))
    with pytest.raises(AgentStoppedError):
        run_agent(
            "coverage_check",
            registry=registry,
            client=SimpleNamespace(messages=SimpleNamespace(parse=parse)),
            system=coverage_check.SYSTEM_PROMPT,
            content="c",
            output_format=CoverageFinding,
            prompt_version=coverage_check.PROMPT_VERSION,
            claim_id="claim_001",
            tools=access,
        )
    assert len(calls) == 1
    assert [c.allowed for c in access.log] == [False]
