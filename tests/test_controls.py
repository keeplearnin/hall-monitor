"""The controls register can't drift from the code, the tests or the frameworks."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from hall_monitor.governance.controls import DOC, ROOT, load_frameworks, load_register, render

REGISTER = load_register()
FRAMEWORKS = load_frameworks()
CONTROLS = REGISTER["controls"]
NIST = set(FRAMEWORKS["nist_ai_rmf"]["subcategories"])
RISKS = set(FRAMEWORKS["finos_aigf"]["risks"])
MITIGATIONS = set(FRAMEWORKS["finos_aigf"]["mitigations"])


def _defined(path: Path, symbol: str) -> bool:
    return (
        re.search(rf"^\s*(?:async\s+)?(?:def|class) {re.escape(symbol)}\b", path.read_text(), re.M)
        is not None
    )


def _check_ref(ref: str) -> None:
    path, _, symbol = ref.partition("::")
    file = ROOT / path
    assert file.exists(), f"{path} doesn't exist"
    if symbol:
        assert _defined(file, symbol), f"{symbol} isn't defined in {path}"


def test_control_ids_are_unique_and_ordered():
    ids = [c["id"] for c in CONTROLS]
    assert ids == sorted(set(ids))


@pytest.mark.parametrize("control", CONTROLS, ids=lambda c: c["id"])
def test_every_reference_exists(control):
    for ref in control["code"] + control["tests"]:
        _check_ref(ref)
    for ref in control["evidence"]:
        if not ref.startswith("pending:"):
            _check_ref(ref)


@pytest.mark.parametrize("control", CONTROLS, ids=lambda c: c["id"])
def test_every_control_is_tested_and_mapped(control):
    assert control["tests"], "a control without a test doesn't count"
    assert all(t.startswith("tests/") for t in control["tests"])
    assert control["nist"] and set(control["nist"]) <= NIST
    assert set(control["finos"]["risks"]) <= RISKS
    assert set(control["finos"]["mitigations"]) <= MITIGATIONS
    assert control["finos"]["risks"] or control["finos"]["mitigations"]


def test_every_finos_entry_is_mapped_or_explained():
    mapped = {i for c in CONTROLS for i in c["finos"]["risks"] + c["finos"]["mitigations"]}
    explained = {n["ref"] for n in REGISTER["not_covered"]}
    assert (RISKS | MITIGATIONS) - mapped - explained == set()
    assert not mapped & explained, "an entry is both mapped and listed as not covered"


def test_every_nist_subcategory_is_mapped_or_explained():
    mapped = {n for c in CONTROLS for n in c["nist"]}
    explained = {n["ref"] for n in REGISTER["not_covered"]}
    assert NIST - mapped - explained == set()
    assert explained <= NIST | RISKS | MITIGATIONS


def _audit_events_in_code() -> set[str]:
    """Event names written to the audit log: literal appends (read from the AST) and the
    triage recorder's events."""
    events = set()
    for file in (ROOT / "hall_monitor").rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                events.add(node.args[1].value)
    # Triage events are written by TriageRecorder, one _<event> method each.
    from hall_monitor.governance.audit import TriageRecorder

    events |= {n[1:] for n in vars(TriageRecorder) if n.startswith("_") and not n.startswith("__")}
    return events


def test_audit_events_are_all_mapped():
    mapped = set(REGISTER["audit_events"])
    assert _audit_events_in_code() == mapped
    ids = {c["id"] for c in CONTROLS}
    assert all(set(v) <= ids for v in REGISTER["audit_events"].values())


def test_generated_doc_is_current():
    assert DOC.read_text() == render(REGISTER, FRAMEWORKS), (
        "docs/controls.md is stale: uv run python -m hall_monitor.governance.controls"
    )


# HM-11: traces carry metadata only.
def test_traces_never_capture_inputs_or_outputs(monkeypatch):
    import langfuse

    from hall_monitor import tracing

    seen = {}

    def fake_observe(**kwargs):
        seen.update(kwargs)
        return lambda fn: fn

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(langfuse, "observe", fake_observe)

    @tracing.observe(name="probe")
    def probe(claim_text):
        return claim_text

    assert probe("medical details") == "medical details"
    assert seen["capture_input"] is False and seen["capture_output"] is False


def test_the_triage_span_wraps_triage():
    """A helper placed between the decorator and triage would take its span (review 6)."""
    from hall_monitor import pipeline

    assert pipeline.triage.__wrapped__.__name__ == "triage"
    assert not hasattr(pipeline._keep_partial, "__wrapped__")


def test_an_exception_never_carries_its_text_into_a_trace(monkeypatch):
    """Langfuse records an exception's message on the span, and pydantic's repeats the model's
    output; the span gets the error type only, and the caller still gets the exception."""
    import langfuse

    from hall_monitor import tracing

    escaped, marked = [], []

    def fake_observe(**kwargs):
        def wrap(fn):
            def traced(*args, **kw):
                try:
                    return fn(*args, **kw)
                except BaseException as exc:
                    escaped.append(str(exc))
                    raise

            return traced

        return wrap

    client = SimpleNamespace(update_current_span=lambda **kw: marked.append(kw))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(langfuse, "observe", fake_observe)
    monkeypatch.setattr(langfuse, "get_client", lambda: client)

    @tracing.observe(name="probe")
    def probe():
        raise ValueError("input_value='Jane Doe, DOB 1980-02-03'")

    with pytest.raises(ValueError, match="Jane Doe"):
        probe()
    assert escaped == []
    assert marked == [{"level": "ERROR", "status_message": "ValueError"}]


def test_the_model_cards_are_current_and_every_agent_has_written_limits():
    """docs/model-cards.md is generated from the registry and results; a new agent needs its
    workflow and limits written in hall_monitor/governance/model_cards.py."""
    from hall_monitor.governance import model_cards
    from hall_monitor.registry import DEFAULT_REGISTRY_DIR, NON_MANIFEST_FILES

    ids = {p.stem for p in DEFAULT_REGISTRY_DIR.glob("*.yaml") if p.name not in NON_MANIFEST_FILES}
    assert ids == set(model_cards.LIMITS) == set(model_cards.WORKFLOWS)
    assert model_cards.DOC.read_text() == model_cards.current(), (
        "docs/model-cards.md is stale: uv run python -m hall_monitor.governance.model_cards"
    )
