"""What a source file imports, as full dotted names, for the tests that guard package boundaries."""

from __future__ import annotations

import ast


def imported_modules(source: str, package: str = "") -> set[str]:
    """Every module a source imports, and for `from m import x` also `m.x`, so
    `from hall_monitor import claims` counts as reaching `hall_monitor.claims`. Relative imports
    resolve against `package`."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            names.add(module)
            names |= {f"{module}.{a.name}" for a in node.names}
    return names


def reaches(source: str, prefixes: tuple[str, ...], package: str = "") -> set[str]:
    """The imported names that are one of `prefixes` or inside one."""
    return {
        m
        for m in imported_modules(source, package)
        if any(m == p or m.startswith(p + ".") for p in prefixes)
    }
