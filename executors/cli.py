"""The CLI executor: any headless coding-agent command, run inside the worktree.

A different kind of executor from the loop on purpose. A CLI product has its
own file tools and its own model access, so the harness cannot enforce anything
before an action. Everything rests on what holds for any executor: a worktree
without keys, and a hand-back validated from what actually changed.

The command is given a task file (HARNESS_TASK) and must write its evidence to
the path named in it. Its environment is stripped to PATH.

Two stated gaps, both in docs/limits.md: the command can read outside its
worktree (no operating-system sandbox), and its model calls do not pass through
this build's gateway, so the pin and the budget are the product's, not ours. In
a real deployment the product is pointed at the gateway's endpoint.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from harness.workorder import HandBack, WorkOrder


class CliExecutor:
    id = "cli"
    version = "1"
    identity = "executor-cli"

    def __init__(self, command: list[str], kind: str, *, extra_env: dict[str, str] | None = None,
                 timeout: int = 300) -> None:
        self.command = command
        self.kind = kind
        self.extra_env = extra_env or {}
        self.timeout = timeout

    def run(self, order: WorkOrder) -> HandBack:
        with tempfile.TemporaryDirectory(prefix="cli-executor-") as tmp:
            handback_path = Path(tmp) / "handback.json"
            task_path = Path(tmp) / "task.json"
            task_path.write_text(json.dumps({
                "change": order.change_id, "spec": order.spec_id, "task": order.task,
                "allowed_paths": list(order.allowed_paths),
                "skills": [{"path": s.path, **s.as_dict()} for s in order.skills],
                "handback": str(handback_path),
            }))
            env = {"PATH": os.environ.get("PATH", ""), "HARNESS_TASK": str(task_path),
                   **self.extra_env}
            proc = subprocess.run(self.command, cwd=order.worktree, env=env, capture_output=True,
                                  text=True, timeout=self.timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"command exited {proc.returncode}: {proc.stderr.strip()[-200:]}")
            if not handback_path.exists():
                raise RuntimeError("the command wrote no hand-back")
            evidence = json.loads(handback_path.read_text())
        evidence["executor"] = {"id": self.id, "kind": self.kind, "version": self.version}
        return HandBack(evidence=evidence, transcript=proc.stdout[-4000:])
