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


# These serve every example, so they load claims code with the rest: the submit pages run in
# the shared panel (panel/demo.py), the evals' meta checks read every agent's approval
# (approvals.py), and the two offline comparisons run every agentic example, claims included.
SERVES_EVERY_EXAMPLE = (
    "hall_monitor.hr.submit",
    "hall_monitor.hr.evals",
    "hall_monitor.complaints.submit",
    "hall_monitor.complaints.evals",
    "hall_monitor.evals.agentic_compare",
    "hall_monitor.evals.stop_timing",
)


def _example_modules() -> list[str]:
    """Every module of the leave, complaint and partner examples, found on disk so a new one is
    checked too, less those that serve every example."""
    found = []
    for package in ("hr", "complaints", "partners"):
        for path in sorted((PACKAGE / package).rglob("*.py")):
            parts = path.relative_to(ROOT).with_suffix("").parts
            name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            if name not in SERVES_EVERY_EXAMPLE:
                found.append(name)
    return found


def _claims_modules_loaded_by(module: str) -> list[str]:
    code = (
        f"import sys, {module}; "
        "print(sorted(m for m in sys.modules if (m + '.').startswith('hall_monitor.claims.')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    return ast.literal_eval(out.stdout.strip())


@pytest.mark.parametrize("module", _example_modules())
def test_running_another_example_loads_no_claims_code(module) -> None:
    """Shared code (agents/base.py, the gateway's redaction and injection checks, the tool
    gateway and the audit log) imports no claims types, so running another example loads no
    hall_monitor.claims module (Review 42's deferred finding)."""
    assert _claims_modules_loaded_by(module) == []


@pytest.mark.parametrize("module", SERVES_EVERY_EXAMPLE)
def test_known_limit_code_serving_every_example_still_loads_claims_code(module) -> None:
    """The panel, approvals.py and the two offline comparisons import every example, claims
    included. Splitting them per example is a follow-up; this pins it until then."""
    assert _claims_modules_loaded_by(module) != []


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
