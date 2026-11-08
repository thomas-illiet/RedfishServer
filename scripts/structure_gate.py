"""Require documentation for every Python definition and bound maintained module size."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_MODULE_LINES = 300
SOURCE_DIRECTORIES = ("src", "scripts", "tests")
UPSTREAM_GENERATOR = Path("src/redfish_mock/_fixtures/make_mockups.py")


def inspect_source(source: str, *, maximum_lines: int | None = MAX_MODULE_LINES) -> list[str]:
    """Report missing docstrings, including private and nested definitions, and oversized files."""
    problems = []
    tree = ast.parse(source)
    if not (ast.get_docstring(tree) or "").strip():
        problems.append("1: missing module docstring")
    problems.extend(
        f"{node.lineno}: {node.name} needs a non-empty docstring"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not (ast.get_docstring(node) or "").strip()
    )
    problems.extend(
        f"{node.lineno}: replace anonymous function with a named, documented function"
        for node in ast.walk(tree)
        if isinstance(node, ast.Lambda)
    )
    length = len(source.splitlines())
    if maximum_lines is not None and length > maximum_lines:
        problems.append(
            f"1: module has {length} lines; split responsibilities below {maximum_lines}"
        )
    return problems


def check(root: Path = ROOT) -> None:
    """Check every Python docstring, exempting only the upstream generator from the size limit."""
    failures = []
    count = 0
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root)
            maximum_lines = None if relative == UPSTREAM_GENERATOR else MAX_MODULE_LINES
            count += 1
            failures.extend(
                f"{relative}:{problem}"
                for problem in inspect_source(path.read_text(), maximum_lines=maximum_lines)
            )
    if failures:
        raise SystemExit("\n".join(failures))
    print(
        f"Structure: all {count} modules documented, including imported code; "
        f"maintained modules at most {MAX_MODULE_LINES} lines."
    )


if __name__ == "__main__":
    check()
