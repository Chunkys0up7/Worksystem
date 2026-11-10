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
class Role:
    name: str
    may_write: tuple[str, ...]


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    public_key: str


@dataclass(frozen=True)
class RecordClass:
    name: str
    floor_days: int
    ceiling_days: int
    fact_types: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    root: Path
    roles: dict[str, Role]
    identities: dict[str, Identity]
    record_classes: dict[str, RecordClass]
    personal_data_keys: frozenset[str]
    completeness: tuple[dict[str, Any], ...]
    tier_rules: dict[str, Any]
    path_map: tuple[dict[str, Any], ...]
    skill_map: tuple[dict[str, Any], ...]
    deny_read: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    enforce_commits_after: str | None = None

    def writer_role_of(self, fact_type: str) -> str | None:
        """The one role that may write this fact type."""
        for role in self.roles.values():
            if fact_type in role.may_write:
                return role.name
        return None

    def may_write(self, identity_name: str, fact_type: str) -> bool:
        ident = self.identities.get(identity_name)
        return ident is not None and self.writer_role_of(fact_type) == ident.role

    def identities_in_role(self, role: str) -> list[str]:
        return sorted(i.name for i in self.identities.values() if i.role == role)

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
    identity_doc = _read(root, "identities.yaml")

    roles: dict[str, Role] = {}
    for row in identity_doc.get("roles", []):
        if row["name"] in roles:
            raise PolicyError(f"role {row['name']} is defined twice")
        roles[row["name"]] = Role(row["name"], tuple(row.get("may_write", [])))

    identities: dict[str, Identity] = {}
    for row in identity_doc.get("identities", []):
        name, role = row["name"], row.get("role")
        if not isinstance(role, str):
            raise PolicyError(f"identity {name} must hold exactly one role")
        if role not in roles:
            raise PolicyError(f"identity {name} holds unknown role {role}")
        if name in identities:
            raise PolicyError(f"identity {name} is defined twice")
        if name not in keys:
            raise PolicyError(f"identity {name} has no registered public key")
        identities[name] = Identity(name=name, role=role, public_key=keys[name])
    used = [keys[name] for name in identities]
    if len(set(used)) != len(used):
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
        writers = [r.name for r in roles.values() if fact_type in r.may_write]
        if len(writers) != 1:
            raise PolicyError(
                f"fact type {fact_type} must have exactly one authoritative "
                f"role, found {writers or 'none'}"
            )
    for role in roles.values():
        for fact_type in role.may_write:
            if fact_type not in seen:
                raise PolicyError(
                    f"role {role.name} may write {fact_type}, which has "
                    "no record class"
                )

    tier_rules = _read(root, "tier-rules.yaml")
    path_map = tuple(_read(root, "path-map.yaml").get("paths", []))
    skill_map = tuple(_read(root, "skill-map.yaml").get("skills", []))
    for rule in path_map:
        if rule.get("tier") not in ("A", "B", "C"):
            raise PolicyError(f"path-map rule {rule.get('glob')}: bad tier")

    deny = _read(root, "deny.yaml")
    commits = _read(root, "commits.yaml")

    return Policy(
        root=root,
        roles=roles,
        identities=identities,
        record_classes=record_classes,
        personal_data_keys=frozenset(
            k.lower() for k in schedule.get("personal_data_keys", [])
        ),
        completeness=tuple(schedule.get("completeness", [])),
        tier_rules=tier_rules,
        path_map=path_map,
        skill_map=skill_map,
        deny_read=tuple(deny.get("read", [])),
        deny_write=tuple(deny.get("write", [])),
        enforce_commits_after=str(commits["enforce_after"]) if commits.get("enforce_after") else None,
    )
