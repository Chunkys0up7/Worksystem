"""The tier rulebook: risk tier from what a change touches, never from its author.

Inputs: the paths changed, the obligation IDs the change cites, and three policy
files (tier rules, path map, skill map). There is no parameter through which an
author, human or agent, can state a tier.

Rulebook v0:
- a path nothing maps is Tier C until someone maps it
- a change takes the highest tier of anything it touches
- every matching rule applies and the highest wins, so rule order cannot matter
  and a broad rule can never lower a narrow one
- a skill change takes its tier from the runtime nodes that load the skill; a
  skill whose consuming nodes are unknown FAILS the check, it does not default
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from ..policy import Policy

TIERS = ("A", "B", "C")
RANK = {tier: index for index, tier in enumerate(TIERS)}


class TierCheckFailed(Exception):
    """The rulebook cannot classify this change. The change does not proceed."""


@dataclass
class PathFinding:
    path: str
    tier: str
    reasons: list[str] = field(default_factory=list)
    unmapped: bool = False


@dataclass
class TierDecision:
    tier: str
    findings: list[PathFinding]
    obligations: tuple[str, ...]
    rulebook_hash: str
    reasons: list[str] = field(default_factory=list)

    @property
    def by_default(self) -> bool:
        """True when the change is Tier C only because paths are unmapped."""
        if self.tier != "C" or self.reasons:
            return False
        top = [f for f in self.findings if f.tier == "C"]
        return bool(top) and all(f.unmapped for f in top)

    def to_payload(self, subject: str) -> dict[str, Any]:
        return {
            "subject": subject,
            "tier": self.tier,
            "by_default": self.by_default,
            "rulebook_hash": self.rulebook_hash,
            "obligations": list(self.obligations),
            "change_reasons": self.reasons,
            "findings": [
                {"path": f.path, "tier": f.tier, "reasons": f.reasons}
                for f in self.findings
            ],
        }


def _higher(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


def normalise(path: str) -> str | None:
    """Repository-relative POSIX path, or None if it leaves the repository.

    `docs/../services/x.py` is `services/x.py`. Backslashes are separators.
    Case is kept: `Docs/x` is not `docs/x`, so it is unmapped, so it is Tier C.
    """
    text = path.replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    norm = posixpath.normpath(text)
    if norm == "." or norm == ".." or norm.startswith("../"):
        return None
    return norm


@lru_cache(maxsize=512)
def glob_to_regex(glob: str) -> re.Pattern[str]:
    """`**` crosses directories, `*` and `?` stay inside one path segment."""
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches(glob: str, path: str) -> bool:
    return bool(glob_to_regex(glob).match(path))


def classify(
    paths: Iterable[str], policy: Policy, obligations: Iterable[str] = ()
) -> TierDecision:
    rules = policy.tier_rules
    unmapped_tier = rules.get("defaults", {}).get("unmapped", "C")
    data_floor: dict[str, str] = rules.get("data_class_floor", {})
    skill_roots: list[str] = rules.get("skill_roots", [])
    obligations = tuple(sorted(set(obligations)))

    raw = sorted(set(paths))
    if not raw:
        raise TierCheckFailed("a change with no paths cannot be tiered")

    findings: list[PathFinding] = []
    for original in raw:
        path = normalise(original)
        if path is None:
            findings.append(
                PathFinding(
                    original,
                    unmapped_tier,
                    ["path leaves the repository root, treated as unmapped"],
                    unmapped=True,
                )
            )
            continue

        finding = PathFinding(path, "A")
        mapped = False

        # Skill changes: tier comes from the runtime nodes that load the skill.
        if any(path.startswith(root) for root in skill_roots):
            entries = [s for s in policy.skill_map if _matches(s["path"], path)]
            if not entries:
                raise TierCheckFailed(
                    f"{path}: under a skill root but in no skill-map entry; "
                    "map the skill before changing it"
                )
            for skill in entries:
                nodes = skill.get("consuming_nodes") or []
                if not nodes:
                    raise TierCheckFailed(
                        f"{path}: skill {skill['id']} has no known consuming "
                        "nodes; the check fails rather than defaulting down"
                    )
                regulated = [n["node"] for n in nodes if n.get("regulated")]
                if regulated:
                    finding.tier = _higher(finding.tier, "C")
                    finding.reasons.append(
                        f"skill {skill['id']} is loaded by regulated node(s) "
                        + ", ".join(regulated)
                    )
                else:
                    finding.tier = _higher(finding.tier, "B")
                    finding.reasons.append(
                        f"skill {skill['id']} is loaded by "
                        + ", ".join(n["node"] for n in nodes)
                    )
            mapped = True

        for rule in policy.path_map:
            if not _matches(rule["glob"], path):
                continue
            mapped = True
            tier = rule["tier"]
            reason = f"{rule['glob']} is Tier {rule['tier']}: {rule.get('reason', '')}".rstrip(": ")
            for data_class in rule.get("data_classes", []):
                if data_class not in data_floor:
                    raise TierCheckFailed(
                        f"path-map rule {rule['glob']} names data class "
                        f"{data_class}, which the tier rules do not define"
                    )
                floor = data_floor[data_class]
                if RANK[floor] > RANK[tier]:
                    tier = floor
                    reason += f"; data class {data_class} raises it to Tier {floor}"
            finding.tier = _higher(finding.tier, tier)
            finding.reasons.append(reason)

        if not mapped:
            finding.tier = unmapped_tier
            finding.unmapped = True
            finding.reasons.append(
                f"no rule maps this path, Tier {unmapped_tier} until mapped"
            )
        findings.append(finding)

    tier = "A"
    for finding in findings:
        tier = _higher(tier, finding.tier)

    change_reasons: list[str] = []
    if obligations:
        floor = rules.get("obligation_floor", "C")
        if RANK[floor] > RANK[tier]:
            change_reasons.append(
                f"cites obligation(s) {', '.join(obligations)}, floor is Tier {floor}"
            )
        tier = _higher(tier, floor)

    return TierDecision(
        tier=tier,
        findings=findings,
        obligations=obligations,
        rulebook_hash=policy.rulebook_hash(),
        reasons=change_reasons,
    )
