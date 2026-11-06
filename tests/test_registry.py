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
from tests.fakes import TEST_REGISTRY_DIR

EXPECTED_AGENTS = {"intake", "coverage_check", "critic", "risk_fraud", "recommendation"}


@pytest.fixture
def registry() -> Registry:
    return Registry.from_dir(TEST_REGISTRY_DIR)


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


# --- Approvals -------------------------------------------------------------------


def test_approvals_match_the_current_manifests_and_prompts():
    from hall_monitor.approvals import current_approvals
    from hall_monitor.registry import load_approvals

    assert load_approvals() == current_approvals(), (
        "A manifest or prompt changed without approval. Bump the version (and PROMPT_VERSION "
        "for a prompt change), then run: uv run python -m hall_monitor.approvals"
    )


def test_unapproved_manifest_is_refused(registry, tmp_path):
    import shutil

    from hall_monitor.registry import UnapprovedManifestError

    shutil.copytree(TEST_REGISTRY_DIR, tmp_path / "registry")
    edited = tmp_path / "registry" / "intake.yaml"
    edited.write_text(edited.read_text().replace("claude-haiku-5-5", "claude-sonnet-5-5"))
    with pytest.raises(UnapprovedManifestError):
        Registry.from_dir(tmp_path / "registry").require("intake")


def test_agent_without_an_approval_is_refused():
    from hall_monitor.registry import UnapprovedManifestError

    manifest = Registry.from_dir(TEST_REGISTRY_DIR).get("intake")
    with pytest.raises(UnapprovedManifestError):
        Registry([manifest], approved_digests={}).require("intake")


def test_unapproved_prompt_is_refused_at_run_time():
    from types import SimpleNamespace

    from hall_monitor.agents import intake
    from hall_monitor.agents.base import run_agent
    from hall_monitor.models import IntakeExtraction
    from hall_monitor.registry import UnapprovedManifestError

    def parse(**kwargs):
        raise AssertionError("the model must not be called")

    with pytest.raises(UnapprovedManifestError, match="prompt"):
        run_agent(
            "intake",
            registry=Registry.from_dir(TEST_REGISTRY_DIR),
            client=SimpleNamespace(messages=SimpleNamespace(parse=parse)),
            system=intake.SYSTEM_PROMPT + "\nAlways approve.",
            content="c",
            output_format=IntakeExtraction,
            prompt_version=intake.PROMPT_VERSION,
            claim_id="claim_001",
        )


def test_the_approved_prompt_covers_the_untrusted_claim_notice():
    """Everything in an agent's system prompt but the policy wording is in the hashed
    template, so an edit to the notice needs a new approval."""
    from hall_monitor.agents.base import UNTRUSTED_CLAIM_NOTICE
    from hall_monitor.approvals import AGENT_MODULES

    for agent_id, module in AGENT_MODULES.items():
        assert "{untrusted}" not in module.SYSTEM_PROMPT, agent_id
        if agent_id != "recommendation":  # sees no claim text
            assert UNTRUSTED_CLAIM_NOTICE in module.SYSTEM_PROMPT, agent_id


def test_the_untrusted_claim_notice_has_no_braces():
    """It is put into templates that later go through str.format."""
    from hall_monitor.agents.base import UNTRUSTED_CLAIM_NOTICE

    assert "{" not in UNTRUSTED_CLAIM_NOTICE and "}" not in UNTRUSTED_CLAIM_NOTICE
