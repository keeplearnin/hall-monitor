"""The records every worked example shares live in `hall_monitor.shared_models`, not in the
claims types: the other examples must not import claims code to get them."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import hall_monitor.shared_models as shared_models
from tests.import_names import reaches

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "hall_monitor"
CLAIMS = "hall_monitor.claims.models"
CLAIMS_PACKAGE = "hall_monitor.claims"
OTHER_EXAMPLES = (
    "hall_monitor.hr",
    "hall_monitor.complaints",
    "hall_monitor.partners",
    "hall_monitor.redteam",
)
SHARED = {"AgentCall", "ToolCall", "StepOutcome", "StepRefusal"}
WHOLE_MODULE = "*"  # the claims module itself was imported, so any of its names is reachable


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).parent.parts)


def _claims_imports(source: str, package: str, target: str = CLAIMS) -> set[str]:
    """Names taken from the claims module in `source`, in any import form: `from ... import`,
    `import hall_monitor.claims.models`, `from hall_monitor.claims import models`, or relative
    imports that resolve to it. `WHOLE_MODULE` stands for the module itself. `target` is the
    claims module, or the whole claims package (`CLAIMS_PACKAGE`), whose other modules hand out
    the same types."""
    parent, leaf = target.rsplit(".", 1)
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == target or a.name.startswith(target + ".") for a in node.names):
                names.add(WHOLE_MODULE)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if module == target or module.startswith(target + "."):
                names |= {a.name for a in node.names}
            elif module == parent and any(a.name == leaf for a in node.names):
                names.add(WHOLE_MODULE)
    return names


def _python_files(*tops: str) -> list[Path]:
    return [path for top in tops for path in (ROOT / top).rglob("*.py")]


def _other_example_packages() -> list[Path]:
    # Every worked example has its own package with its own models.py; claims is the one checked.
    return sorted(p.parent for p in PACKAGE.glob("*/models.py") if p.parent.name != "claims")


@pytest.mark.parametrize(
    ("source", "package", "expected"),
    [
        (
            "from hall_monitor.claims.models import AgentCall, Route",
            "hall_monitor.hr",
            {"AgentCall", "Route"},
        ),
        (
            "import hall_monitor.claims.models as m\nx = m.AgentCall",
            "hall_monitor.hr",
            {WHOLE_MODULE},
        ),
        ("from hall_monitor.claims import models", "hall_monitor.hr", {WHOLE_MODULE}),
        ("from ..claims.models import StepOutcome", "hall_monitor.hr", {"StepOutcome"}),
        ("from ..claims import models", "hall_monitor.hr", {WHOLE_MODULE}),
        ("from .models import StepOutcome", "hall_monitor.claims", {"StepOutcome"}),
        ("from ..models import StepOutcome", "hall_monitor.hr", set()),
        ("from .models import LeaveRequest", "hall_monitor.hr", set()),
        ("from hall_monitor.shared_models import AgentCall", "hall_monitor.hr", set()),
    ],
)
def test_every_import_form_of_the_claims_module_is_found(source, package, expected) -> None:
    assert _claims_imports(source, package) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from hall_monitor.claims.routing import Route", {"Route"}),
        ("from hall_monitor.claims.pipeline import TriageResult", {"TriageResult"}),
        ("from hall_monitor import claims", {WHOLE_MODULE}),
        ("import hall_monitor.claims", {WHOLE_MODULE}),
        ("from ..claims.routing import Route", {"Route"}),
        ("from .models import LeaveRequest", set()),
    ],
)
def test_every_claims_module_counts_for_the_other_examples(source, expected) -> None:
    assert _claims_imports(source, "hall_monitor.hr", CLAIMS_PACKAGE) == expected


def test_the_claims_package_imports_no_other_example() -> None:
    """Code that runs more than one example (agentic_compare, stop_timing) stays shared."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in (PACKAGE / "claims").rglob("*.py")
        if reaches(path.read_text(), OTHER_EXAMPLES, _package_of(path))
    ]
    assert offenders == []


def test_known_limit_the_other_examples_still_load_the_claims_package() -> None:
    """agents/base.py, the redaction and injection checks and the audit log import claims types,
    so running another example loads hall_monitor.claims. Untangling them is a follow-up
    (Review 42); this pins it until then."""
    for module in ("hall_monitor.hr.pipeline", "hall_monitor.complaints.pipeline"):
        code = (
            f"import sys, {module}; "
            "print(any(m.startswith('hall_monitor.claims') for m in sys.modules))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
        )
        assert out.stdout.strip() == "True", module


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
        if _claims_imports(path.read_text(), _package_of(path), CLAIMS_PACKAGE)
    ]
    assert offenders == []


def test_shared_records_are_imported_from_shared_models() -> None:
    # hall_monitor.claims.models imports these for its own records, so importing them from there
    # would still work; keep every caller on the one home.
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files("hall_monitor", "tests")
        if path != PACKAGE / "claims" / "models.py"
        if (names := _claims_imports(path.read_text(), _package_of(path)))
        and (names & SHARED or WHOLE_MODULE in names)
    ]
    assert offenders == []
