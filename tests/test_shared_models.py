"""The records every worked example shares live in `hall_monitor.shared_models`, not in the
claims types: the other examples must not import claims code to get them."""

import ast
from pathlib import Path

import hall_monitor.shared_models as shared_models

ROOT = Path(__file__).resolve().parent.parent
SHARED = {"AgentCall", "ToolCall", "StepOutcome", "StepRefusal"}
NON_CLAIMS = ("hr", "complaints", "partners")


def _imports_from(path: Path, module: str) -> list[set[str]]:
    tree = ast.parse(path.read_text())
    return [
        {alias.name for alias in node.names}
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module and node.level == 0
    ]


def test_shared_models_holds_the_shared_records() -> None:
    assert SHARED <= set(vars(shared_models))


def test_other_examples_do_not_import_claims_types() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for package in NON_CLAIMS
        for path in (ROOT / "hall_monitor" / package).rglob("*.py")
        if _imports_from(path, "hall_monitor.models")
    ]
    assert offenders == []


def test_shared_records_are_imported_from_shared_models() -> None:
    # hall_monitor.models imports these for its own records, so importing them from there would
    # still work; keep every caller on the one home.
    offenders = [
        str(path.relative_to(ROOT))
        for top in ("hall_monitor", "tests")
        for path in (ROOT / top).rglob("*.py")
        if path.name != "models.py" or path.parent.name != "hall_monitor"
        if any(names & SHARED for names in _imports_from(path, "hall_monitor.models"))
    ]
    assert offenders == []
