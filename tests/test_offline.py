"""The suite never reaches the Anthropic API or Langfuse (Review 46, H6): a CLI test used to run
the real load_dotenv(), which put the repo's .env keys into the test process, so traces of fake
runs went to Langfuse and a test that forgot to pass a client would have made a paid call."""

import importlib
import os
from pathlib import Path

import dotenv
import pytest

from tests.conftest import OFFLINE_KEYS

ROOT = Path(__file__).resolve().parents[1]
LOADERS = sorted(
    ".".join(p.relative_to(ROOT).with_suffix("").parts)
    for p in (ROOT / "hall_monitor").rglob("*.py")
    if "load_dotenv" in p.read_text()
)


def test_some_modules_load_dotenv():
    assert "hall_monitor.claims.run" in LOADERS  # the scan below found what it should


@pytest.mark.parametrize("name", LOADERS)
def test_a_module_that_loads_dotenv_gets_the_offline_guard(name):
    module = importlib.import_module(name)
    loader = getattr(module, "load_dotenv", dotenv.load_dotenv)  # some import it inside a function
    assert getattr(loader, "offline_guard", False), name


def test_loading_dotenv_in_a_test_puts_no_key_in_the_environment():
    import hall_monitor.claims.run as claims_run

    claims_run.load_dotenv()
    assert not [k for k in OFFLINE_KEYS if os.environ.get(k)]
