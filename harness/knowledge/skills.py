"""Skills are a signed release unit, treated like tools.

A skill carries rules into every agent that loads it, so it gets the same
supply-chain protection as a tool: a content hash over every file, a signature
from the release identity, and verification before anything loads or runs it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import identity
from ..canonical import hash_obj, sha256_hex
from ..policy import Policy
from .model import Skill

RELEASE_FILE = "release.json"
PUBLISHER = "skill-publisher"

# A skill is instructions and checks. It makes no network call and holds no
# credential. This is a floor, a pattern lint, not a proof. See docs/limits.md.
_DENY = [
    (re.compile(r"https?://", re.I), "contains a URL"),
    (re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|httpx|aiohttp|subprocess)\b", re.M),
     "imports a network or process module"),
    (re.compile(r"\b(api[_-]?key|password|passwd|secret|bearer\s+[a-z0-9]|private[_-]?key)\b", re.I),
     "mentions a credential"),
    (re.compile(r"\b(eval|exec|__import__)\s*\("), "uses dynamic code execution"),
]


class SkillInvalid(Exception):
    """The skill is not what was released. Nothing loads or runs it."""


def _skill_files(directory: Path) -> list[Path]:
    return sorted(
        p
        for p in directory.rglob("*")
        if p.is_file()
        and p.name != RELEASE_FILE
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
    )


def content_hash(directory: Path) -> str:
    """Hash over every file's path and bytes. Adding, removing or editing a file changes it."""
    listing = [
        [p.relative_to(directory).as_posix(), sha256_hex(p.read_bytes())]
        for p in _skill_files(directory)
    ]
    return hash_obj(listing)


def lint(directory: Path) -> list[str]:
    findings = []
    for path in _skill_files(directory):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            findings.append(f"{path.relative_to(directory)}: not a text file")
            continue
        for pattern, message in _DENY:
            if pattern.search(text):
                findings.append(f"{path.relative_to(directory)}: {message}")
    return findings


def _release_digest(skill_id: str, version: str, digest: str) -> bytes:
    return hash_obj({"id": skill_id, "version": version, "content_hash": digest}).encode()


def publish(skill: Skill, keydir: Path) -> dict[str, str]:
    """Sign the skill as it stands and write release.json."""
    findings = lint(skill.dir)
    if findings:
        raise SkillInvalid("skill fails lint: " + "; ".join(findings))
    digest = content_hash(skill.dir)
    release = {
        "id": skill.id,
        "version": skill.version,
        "content_hash": digest,
        "signed_by": PUBLISHER,
        "signature": identity.sign(
            keydir, PUBLISHER, _release_digest(skill.id, skill.version, digest)
        ),
    }
    (skill.dir / RELEASE_FILE).write_text(json.dumps(release, indent=2) + "\n")
    return release


def verify(skill: Skill, policy: Policy) -> dict[str, str]:
    """Return the release record, or raise SkillInvalid."""
    path = skill.dir / RELEASE_FILE
    if not path.exists():
        raise SkillInvalid(f"{skill.id}: never released (no {RELEASE_FILE})")
    release = json.loads(path.read_text())

    if release.get("id") != skill.id or release.get("version") != skill.version:
        raise SkillInvalid(
            f"{skill.id}: manifest says {skill.ref}, release says "
            f"{release.get('id')}@{release.get('version')}"
        )
    digest = content_hash(skill.dir)
    if digest != release.get("content_hash"):
        raise SkillInvalid(
            f"{skill.ref}: content changed since release "
            f"(released {str(release.get('content_hash'))[:12]}, now {digest[:12]})"
        )
    if release.get("signed_by") != PUBLISHER or PUBLISHER not in policy.identities:
        raise SkillInvalid(f"{skill.ref}: not signed by {PUBLISHER}")
    if not identity.verify(
        policy.identities[PUBLISHER].public_key,
        _release_digest(skill.id, skill.version, digest),
        release.get("signature", ""),
    ):
        raise SkillInvalid(f"{skill.ref}: release signature does not verify")
    findings = lint(skill.dir)
    if findings:
        raise SkillInvalid(f"{skill.ref}: fails lint: " + "; ".join(findings))
    return release
