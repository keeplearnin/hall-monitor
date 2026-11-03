import pytest
import yaml

from hall_monitor.registry import (
    DEFAULT_REGISTRY_DIR,
    SCHEMA_FILENAME,
    AgentManifest,
    AgentNotActiveError,
    AgentStatus,
    Registry,
    ToolNotAllowedError,
    UnregisteredAgentError,
    manifest_json_schema,
)

EXPECTED_AGENTS = {"intake", "coverage_check", "critic", "risk_fraud", "recommendation"}


@pytest.fixture
def registry() -> Registry:
    return Registry.from_dir()


def test_every_pipeline_agent_has_a_hall_pass(registry):
    assert {m.id for m in registry} == EXPECTED_AGENTS


def test_intake_is_active(registry):
    assert registry.require("intake").model == "claude-haiku-5-5"


def test_unregistered_agent_is_refused(registry):
    with pytest.raises(UnregisteredAgentError):
        registry.require("shadow_agent")


def test_planned_agent_cannot_run(registry):
    planned = registry.get("critic").model_copy(update={"status": AgentStatus.PLANNED})
    with pytest.raises(AgentNotActiveError):
        Registry([planned]).require("critic")


def test_tool_outside_allow_list_is_refused(registry):
    with pytest.raises(ToolNotAllowedError):
        registry.check_tool("intake", "claims_history")


def test_duplicate_ids_are_rejected(registry):
    intake = registry.get("intake")
    with pytest.raises(ValueError, match="duplicate"):
        Registry([intake, intake])


def test_manifest_rejects_unknown_fields():
    raw = yaml.safe_load((DEFAULT_REGISTRY_DIR / "intake.yaml").read_text())
    raw["can_approve_payouts"] = True
    with pytest.raises(ValueError):
        AgentManifest.model_validate(raw)


def test_file_name_must_match_id(tmp_path):
    raw = yaml.safe_load((DEFAULT_REGISTRY_DIR / "intake.yaml").read_text())
    (tmp_path / "not_intake.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="must match"):
        Registry.from_dir(tmp_path)


def test_digest_changes_with_any_field(registry):
    intake = registry.get("intake")
    assert intake.digest() == intake.model_copy().digest()
    assert intake.digest() != intake.model_copy(update={"model": "claude-sonnet-5-5"}).digest()


def test_published_schema_is_current():
    published = (DEFAULT_REGISTRY_DIR / SCHEMA_FILENAME).read_text()
    assert published == manifest_json_schema(), "run: uv run python -m hall_monitor.registry"
