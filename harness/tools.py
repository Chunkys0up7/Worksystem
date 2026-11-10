"""The tool box an executor works through. This is where the deny list is
enforced BEFORE an action, and where each refused attempt is recorded.

It belongs to the harness, not to any executor. An executor that uses it gets
pre-action enforcement. An executor that goes around it (its own file tools, a
CLI product) is still caught at hand-back, where what changed is derived from a
snapshot. See docs/limits.md for what neither catches: reads outside the
worktree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tiering.rulebook import glob_to_regex, normalise

TOOL_SPECS = [
    {"name": "list_files", "description": "List files under a directory of the worktree.",
     "args": {"prefix": "string"}},
    {"name": "read_file", "description": "Read one file from the worktree.",
     "args": {"path": "string"}},
    {"name": "write_file", "description": "Write one file. Only paths the spec allows.",
     "args": {"path": "string", "content": "string"}},
    {"name": "run_checks", "description": "Run the paired checks of the governing skills.",
     "args": {}},
    {"name": "finish", "description": "Hand back. Pass your evidence.", "args": {"evidence": "object"}},
]


def _matches(globs, path: str) -> bool:
    return any(glob_to_regex(g).match(path) for g in globs)


@dataclass
class ToolBox:
    worktree: Path
    allowed_paths: tuple[str, ...]
    deny_read: tuple[str, ...]
    deny_write: tuple[str, ...]
    check_runner: Any = None  # callable returning {check_id: "pass" | "fail"}
    attempts: list[dict[str, str]] = field(default_factory=list)
    calls: int = 0

    def _refuse(self, action: str, path: str, why: str) -> dict[str, Any]:
        self.attempts.append({"action": action, "path": path, "why": why})
        return {"ok": False, "error": f"refused: {why}"}

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        handler = getattr(self, f"_{name}", None)
        if handler is None or name.startswith("_"):
            return self._refuse(name, "", "no such tool")
        try:
            return handler(**(args or {}))
        except TypeError as exc:
            return {"ok": False, "error": f"bad arguments: {exc}"}

    def _list_files(self, prefix: str = "") -> dict[str, Any]:
        norm = normalise(prefix) if prefix else ""
        if norm is None:
            return self._refuse("list", prefix, "path leaves the worktree")
        base = self.worktree / norm
        if not base.exists():
            return {"ok": True, "files": []}
        files = sorted(p.relative_to(self.worktree).as_posix() for p in base.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts)
        return {"ok": True, "files": [f for f in files if not _matches(self.deny_read, f)]}

    def _read_file(self, path: str) -> dict[str, Any]:
        norm = normalise(path)
        if norm is None:
            return self._refuse("read", path, "path leaves the worktree")
        if _matches(self.deny_read, norm):
            return self._refuse("read", norm, "on the read deny list")
        target = self.worktree / norm
        if target.is_symlink() or not target.is_file():
            return {"ok": False, "error": "no such file"}
        return {"ok": True, "content": target.read_text()}

    def _write_file(self, path: str, content: str) -> dict[str, Any]:
        norm = normalise(path)
        if norm is None:
            return self._refuse("write", path, "path leaves the worktree")
        if _matches(self.deny_write, norm):
            return self._refuse("write", norm, "on the write deny list")
        if not _matches(self.allowed_paths, norm):
            return self._refuse("write", norm, "outside the paths the spec allows")
        target = self.worktree / norm
        if target.is_symlink():
            return self._refuse("write", norm, "target is a symbolic link")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"ok": True}

    def _run_checks(self) -> dict[str, Any]:
        if self.check_runner is None:
            return {"ok": False, "error": "checks are not available"}
        # Feedback for the executor. CI runs them again and only CI's run counts.
        return {"ok": True, "results": self.check_runner()}
