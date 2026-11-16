"""The records every worked example shares live in `hall_monitor.shared_models`, not in the
claims types: the other examples must not import claims code to get them."""

import ast
from pathlib import Path

import pytest

import hall_monitor.shared_models as shared_models

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "hall_monitor"
CLAIMS = "hall_monitor.models"
SHARED = {"AgentCall", "ToolCall", "StepOutcome", "StepRefusal"}
WHOLE_MODULE = "*"  # the claims module itself was imported, so any of its names is reachable


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).parent.parts)


def _claims_imports(source: str, package: str) -> set[str]:
    """Names taken from the claims module in `source`, in any import form: `from ... import`,
    `import hall_monitor.models`, `from hall_monitor import models`, or relative imports that
    resolve to it. `WHOLE_MODULE` stands for the module itself."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == CLAIMS or a.name.startswith(CLAIMS + ".") for a in node.names):
                names.add(WHOLE_MODULE)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if module == CLAIMS:
                names |= {a.name for a in node.names}
            elif module == "hall_monitor" and any(a.name == "models" for a in node.names):
                names.add(WHOLE_MODULE)
    return names


def _python_files(*tops: str) -> list[Path]:
    return [path for top in tops for path in (ROOT / top).rglob("*.py")]


def _other_example_packages() -> list[Path]:
    # Every worked example but claims has its own package with its own models.py.
    return sorted(p.parent for p in PACKAGE.glob("*/models.py"))


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        (
            "from hall_monitor.models import AgentCall, Route",
            "hall_monitor.hr",
            {"AgentCall", "Route"},
        ),
        ("import hall_monitor.models as m\nx = m.AgentCall", "hall_monitor.hr", {WHOLE_MODULE}),
        ("from hall_monitor import models", "hall_monitor.hr", {WHOLE_MODULE}),
        ("from ..models import StepOutcome", "hall_monitor.hr", {"StepOutcome"}),
        ("from .. import models", "hall_monitor.hr", {WHOLE_MODULE}),
        ("from .models import StepOutcome", "hall_monitor", {"StepOutcome"}),
        ("from .models import LeaveRequest", "hall_monitor.hr", set()),
        ("from hall_monitor.shared_models import AgentCall", "hall_monitor.hr", set()),
    ],
)
def test_every_import_form_of_the_claims_module_is_found(source, package, expected) -> None:
    assert _claims_imports(source, package) == expected


def test_the_shared_records_are_defined_in_shared_models() -> None:
    for name in SHARED:
        assert getattr(shared_models, name).__module__ == "hall_monitor.shared_models", name


def test_every_other_example_package_is_checked() -> None:
    names = {p.name for p in _other_example_packages()}
    assert {"hr", "complaints", "partners"} <= names


def test_other_examples_do_not_import_claims_types() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for package in _other_example_packages()
        for path in package.rglob("*.py")
        if _claims_imports(path.read_text(), _package_of(path))
    ]
    assert offenders == []


def test_shared_records_are_imported_from_shared_models() -> None:
    # hall_monitor.models imports these for its own records, so importing them from there would
    # still work; keep every caller on the one home.
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files("hall_monitor", "tests")
        if path != PACKAGE / "models.py"
        if (names := _claims_imports(path.read_text(), _package_of(path)))
        and (names & SHARED or WHOLE_MODULE in names)
    ]
    assert offenders == []
