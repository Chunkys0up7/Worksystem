"""Canonical JSON and hashing. One encoding, used everywhere a hash is taken."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical(obj: Any) -> bytes:
    """Stable byte encoding: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical(obj))
