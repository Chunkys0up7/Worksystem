"""The recorded executor: replays a stored hand-back. NO MODEL RUNS.

It exists so the pipeline and the conformance suite run the same way on every
commit, with no credentials and no network. It proves the harness side of the
contract. It proves nothing about any model or any coding agent, and it says so
wherever it appears: its kind is "recorded", the ledger entries carry
recorded=true, and the commit trailer reads "(recorded)".

A recording is a directory:

    evidence.json   the claim the original executor made
    files/          the changed files, at their paths, as they were handed back

If the skill the recording was made against has since been re-released, the
recorded claim names the old content hash and the hand-back is rejected. A
stale recording cannot pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from harness.workorder import HandBack, WorkOrder


class RecordedExecutor:
    id = "recorded"
    kind = "recorded"
    version = "1"
    identity = "executor-recorded"

    def __init__(self, recording: Path) -> None:
        self.recording = Path(recording)

    def run(self, order: WorkOrder) -> HandBack:
        claim = json.loads((self.recording / "evidence.json").read_text())
        files = self.recording / "files"
        for source in sorted(files.rglob("*")):
            if not source.is_file():
                continue
            target = order.worktree / source.relative_to(files)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        evidence = {
            **claim,
            "executor": {"id": self.id, "kind": self.kind, "version": self.version},
            "recorded": True,
            "canary": None,  # a recording cannot read a one-time token
        }
        return HandBack(evidence=evidence, transcript=json.dumps(claim, sort_keys=True))
