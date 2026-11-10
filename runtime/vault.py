"""The payload vault: where personal data lives, so the ledger never has to hold it.

Case state and ledger entries carry references (vault://case/field). The values
are here, and here only, and can be erased per case. After erasure the record
of what happened (which nodes ran, which rules applied, who decided) is intact
and the personal data is gone.

A directory of JSON files stands in for an encrypted, access-controlled store.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

SCHEME = "vault://"


class VaultMiss(Exception):
    """The value is not there: never stored, or erased."""


class Vault:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, ref: str) -> Path:
        if not ref.startswith(SCHEME):
            raise VaultMiss(f"{ref!r} is not a vault reference")
        case_id, _, name = ref[len(SCHEME):].partition("/")
        if not case_id or not name or ".." in ref or "/" in name:
            raise VaultMiss(f"{ref!r} is not a vault reference")
        return self.directory / case_id / f"{name}.json"

    def put(self, case_id: str, name: str, value: Any) -> str:
        ref = f"{SCHEME}{case_id}/{name}"
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return ref

    def get(self, ref: str) -> Any:
        path = self._path(ref)
        if not path.exists():
            raise VaultMiss(f"{ref} is not in the vault (never stored, or erased)")
        return json.loads(path.read_text())

    def erase(self, case_id: str) -> int:
        """Remove every value held for a case. Returns how many were removed."""
        directory = self.directory / case_id
        if not directory.exists():
            return 0
        count = sum(1 for _ in directory.glob("*.json"))
        shutil.rmtree(directory)
        return count
