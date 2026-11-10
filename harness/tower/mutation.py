"""Mutation testing: do the checks notice when the code is wrong?

A passing test suite says the code does what the tests ask. It does not say the
tests ask much. So the gate changes the code in small ways (a `<` becomes `<=`,
a `+` becomes `-`, a 24 becomes 25) and re-runs the checks. A mutant the checks
fail on is killed. The score is killed over total, and a change to a critical
module merges only above a threshold.

Small on purpose: a handful of operators over the Python AST, applied to the
changed files of critical modules only. Equivalent mutants (a change that does
not alter behaviour) count against the score; that is why the threshold is
below 1. Survivors are listed so a person can tell which they are.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

COMPARE = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
           ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
           ast.Is: ast.IsNot, ast.IsNot: ast.Is}
BINARY = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
BOOLEAN = {ast.And: ast.Or, ast.Or: ast.And}
SYMBOL = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==",
          ast.NotEq: "!=", ast.In: "in", ast.NotIn: "not in", ast.Is: "is",
          ast.IsNot: "is not", ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
          ast.And: "and", ast.Or: "or"}


@dataclass
class Mutant:
    line: int
    description: str
    source: str


def _sites(tree: ast.AST) -> list[tuple[int, str, int]]:
    """(node index, kind, sub-index) for every place a mutation applies."""
    sites = []
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    for index, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare):
            sites += [(index, "compare", i) for i, op in enumerate(node.ops) if type(op) in COMPARE]
        elif isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            sites.append((index, "binary", 0))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOLEAN:
            sites.append((index, "boolean", 0))
        elif (isinstance(node, ast.Constant) and type(node.value) is int
              and id(node) not in docstrings):
            sites.append((index, "constant", 0))
    return sites


def mutants(source: str) -> list[Mutant]:
    tree = ast.parse(source)
    out = []
    for index, kind, sub in _sites(tree):
        clone = copy.deepcopy(tree)
        node = list(ast.walk(clone))[index]
        if kind == "compare":
            old = type(node.ops[sub])
            node.ops[sub] = COMPARE[old]()
            description = f"{SYMBOL[old]} became {SYMBOL[COMPARE[old]]}"
        elif kind == "binary":
            old = type(node.op)
            node.op = BINARY[old]()
            description = f"{SYMBOL[old]} became {SYMBOL[BINARY[old]]}"
        elif kind == "boolean":
            old = type(node.op)
            node.op = BOOLEAN[old]()
            description = f"{SYMBOL[old]} became {SYMBOL[BOOLEAN[old]]}"
        else:
            description = f"{node.value} became {node.value + 1}"
            node.value = node.value + 1
        out.append(Mutant(getattr(node, "lineno", 0), description,
                          ast.unparse(ast.fix_missing_locations(clone)) + "\n"))
    return out
