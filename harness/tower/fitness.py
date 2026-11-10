"""Fitness functions: architecture rules as code. The number is violations found.

Rules are data in policy/fitness.yaml and are read from the trusted repository,
then applied to the code under test. They turn "controls never live in a tool"
and "a regulated figure is exact arithmetic" from sentences into gates.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ..tiering.rulebook import glob_to_regex


def _imports(tree: ast.AST, package: str) -> list[tuple[str, int]]:
    """Every module imported anywhere in the file, with relative imports resolved."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                name = ".".join([*base, node.module] if node.module else base)
                out.append((name, node.lineno))
                out += [(f"{name}.{alias.name}", node.lineno) for alias in node.names]
            else:
                out.append((node.module or "", node.lineno))
                out += [(f"{node.module}.{alias.name}", node.lineno) for alias in node.names]
    return out


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def check_file(rel: str, source: str, rule: dict[str, Any]) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: does not parse"]
    package = ".".join(Path(rel).with_suffix("").parts[:-1])
    found = []
    imports = _imports(tree, package)

    allow = rule.get("allow_imports")
    if allow is not None:
        top = rel.split("/")[0]
        for name, line in imports:
            if _under(name, top) or any(_under(name, a) for a in allow):
                continue
            found.append(f"{rel}:{line}: imports {name}, which {rule['id']} does not allow")
    for name, line in imports:
        for banned in rule.get("forbid_imports", []):
            if _under(name, banned):
                found.append(f"{rel}:{line}: imports {name}, forbidden by {rule['id']}")
                break
    if rule.get("forbid_float"):
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                found.append(f"{rel}:{node.lineno}: a floating-point literal ({rule['id']})")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "float"):
                found.append(f"{rel}:{node.lineno}: float() ({rule['id']})")
    return sorted(set(found))


def run(code_root: Path, rules: list[dict[str, Any]]) -> list[str]:
    """Every violation of every rule in the code under `code_root`."""
    files = sorted(
        p.relative_to(code_root).as_posix() for p in code_root.rglob("*.py")
        if "__pycache__" not in p.parts and ".git" not in p.parts)
    violations: list[str] = []
    for rule in rules:
        include = [glob_to_regex(g) for g in rule["paths"]]
        exclude = [glob_to_regex(g) for g in rule.get("except", [])]
        for rel in files:
            if not any(p.match(rel) for p in include) or any(p.match(rel) for p in exclude):
                continue
            violations += check_file(rel, (code_root / rel).read_text(), rule)
    return violations
