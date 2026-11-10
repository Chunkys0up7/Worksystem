"""Loads the governance plane from policy/ and refuses to load a broken one.

Policy is data in git. The harness reads it; it never writes it. A change to
anything under policy/ is itself a Tier C change (see policy/path-map.yaml).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .canonical import hash_obj


class PolicyError(Exception):
    """The policy files are inconsistent. Nothing runs on a broken policy."""


def repo_root() -> Path:
    env = os.environ.get("HARNESS_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    public_key: str
    may_write: tuple[str, ...]


@dataclass(frozen=True)
class RecordClass:
    name: str
    floor_days: int
    ceiling_days: int
    fact_types: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    root: Path
    identities: dict[str, Identity]
    record_classes: dict[str, RecordClass]
    personal_data_keys: frozenset[str]
    completeness: tuple[dict[str, Any], ...]
    tier_rules: dict[str, Any]
    path_map: tuple[dict[str, Any], ...]
    skill_map: tuple[dict[str, Any], ...]

    def writer_of(self, fact_type: str) -> str | None:
        for ident in self.identities.values():
            if fact_type in ident.may_write:
                return ident.name
        return None

    def record_class_of(self, fact_type: str) -> RecordClass | None:
        for rc in self.record_classes.values():
            if fact_type in rc.fact_types:
                return rc
        return None

    def rulebook_hash(self) -> str:
        """Hash of every input the tier rulebook reads. Recorded with each tier."""
        return hash_obj(
            {
                "tier_rules": self.tier_rules,
                "path_map": list(self.path_map),
                "skill_map": list(self.skill_map),
            }
        )


def _read(root: Path, name: str) -> Any:
    path = root / "policy" / name
    if not path.exists():
        raise PolicyError(f"missing policy file: policy/{name}")
    return yaml.safe_load(path.read_text()) or {}


def load_policy(root: Path | None = None) -> Policy:
    root = root or repo_root()

    keys = _read(root, "identity-keys.yaml").get("public_keys", {})
    identities: dict[str, Identity] = {}
    for row in _read(root, "identities.yaml").get("identities", []):
        name = row["name"]
        if name not in keys:
            raise PolicyError(f"identity {name} has no registered public key")
        identities[name] = Identity(
            name=name,
            role=row.get("role", ""),
            public_key=keys[name],
            may_write=tuple(row.get("may_write", [])),
        )
    if len(set(keys.values())) != len(keys):
        raise PolicyError("two identities share a signing key")

    schedule = _read(root, "records-schedule.yaml")
    record_classes: dict[str, RecordClass] = {}
    for row in schedule.get("record_classes", []):
        rc = RecordClass(
            name=row["name"],
            floor_days=int(row["floor_days"]),
            ceiling_days=int(row["ceiling_days"]),
            fact_types=tuple(row.get("fact_types", [])),
        )
        if rc.floor_days > rc.ceiling_days:
            raise PolicyError(f"record class {rc.name}: floor is above ceiling")
        record_classes[rc.name] = rc

    # One record class per fact type, and one authoritative writer per fact type.
    seen: dict[str, str] = {}
    for rc in record_classes.values():
        for fact_type in rc.fact_types:
            if fact_type in seen:
                raise PolicyError(
                    f"fact type {fact_type} is in two record classes: "
                    f"{seen[fact_type]} and {rc.name}"
                )
            seen[fact_type] = rc.name
    for fact_type in seen:
        writers = [i.name for i in identities.values() if fact_type in i.may_write]
        if len(writers) != 1:
            raise PolicyError(
                f"fact type {fact_type} must have exactly one authoritative "
                f"writer, found {writers or 'none'}"
            )
    for ident in identities.values():
        for fact_type in ident.may_write:
            if fact_type not in seen:
                raise PolicyError(
                    f"identity {ident.name} may write {fact_type}, which has "
                    "no record class"
                )

    tier_rules = _read(root, "tier-rules.yaml")
    path_map = tuple(_read(root, "path-map.yaml").get("paths", []))
    skill_map = tuple(_read(root, "skill-map.yaml").get("skills", []))
    for rule in path_map:
        if rule.get("tier") not in ("A", "B", "C"):
            raise PolicyError(f"path-map rule {rule.get('glob')}: bad tier")

    return Policy(
        root=root,
        identities=identities,
        record_classes=record_classes,
        personal_data_keys=frozenset(
            k.lower() for k in schedule.get("personal_data_keys", [])
        ),
        completeness=tuple(schedule.get("completeness", [])),
        tier_rules=tier_rules,
        path_map=path_map,
        skill_map=skill_map,
    )
