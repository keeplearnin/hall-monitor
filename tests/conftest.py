"""Every test runs offline (Review 46, H6). The keys that would reach the Anthropic API or
Langfuse are removed from the environment, and dotenv's loader is replaced before any module
imports it, so a test that runs a CLI's load_dotenv() can't put the repo's .env keys back. With
no key the Anthropic client refuses to build a request (no network), and tracing is off.
tests/test_offline.py checks every module that loads dotenv gets this guard."""

from __future__ import annotations

import os
import sys

import dotenv
import dotenv.main
import pytest

OFFLINE_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LANGFUSE_BASE_URL",
)


def _no_dotenv(*_args, **_kwargs) -> bool:
    """Loads nothing, as when there is no .env file."""
    return False


_no_dotenv.offline_guard = True  # type: ignore[attr-defined]
dotenv.load_dotenv = _no_dotenv
dotenv.main.load_dotenv = _no_dotenv
for _key in OFFLINE_KEYS:
    os.environ.pop(_key, None)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Per test as well: a module imported before this file kept the real loader, and a test
    may have set a key without monkeypatch."""
    for key in OFFLINE_KEYS:
        monkeypatch.delenv(key, raising=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("hall_monitor") and hasattr(module, "load_dotenv"):
            if not getattr(module.load_dotenv, "offline_guard", False):
                monkeypatch.setattr(module, "load_dotenv", _no_dotenv)
