"""What every published eval result records about the run, so the gate can tell it's current."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from hall_monitor.approvals import AGENT_MODULES, prompt_sha256
from hall_monitor.registry import Registry


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def agent_versions(registry: Registry, agent_ids: list[str] | None = None) -> list[dict]:
    """The hall passes and prompt templates a run used: by default, every agent."""
    rows = []
    for manifest in registry:
        if agent_ids is not None and manifest.id not in agent_ids:
            continue
        module = AGENT_MODULES[manifest.id]
        rows.append(
            {
                "agent_id": manifest.id,
                "agent_version": manifest.version,
                "model": manifest.model,
                "manifest_digest": manifest.digest(),
                "prompt_version": module.PROMPT_VERSION,
                "prompt_template_sha256": prompt_sha256(module),
            }
        )
    return rows


def run_meta(registry: Registry, agent_ids: list[str] | None = None, **extra) -> dict:
    return {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        **extra,
        "agents": agent_versions(registry, agent_ids),
    }
