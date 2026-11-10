"""Retro-tiering: run the rulebook over history and report the mix.

The share of past changes in each tier is the ceiling on lifecycle gain: only
the share that lands in A or B can move faster under tiered review. The share
that is Tier C only because paths are unmapped is the mapping backlog, and the
report lists the path prefixes that would shrink it most.

Point it at any repository with any path map: `harness retro --repo PATH`.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..policy import Policy
from .rulebook import TierCheckFailed, classify

_OBLIGATION = re.compile(r"\bOBL-\d{4}\b")


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    paths: list[str]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def parse_name_status(text: str) -> list[str]:
    """Paths from `--name-status -M` output. A rename counts on both sides."""
    paths: list[str] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        paths.extend(p for p in parts[1:] if p)
    return paths


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    return parse_name_status(_git(repo, "diff", "--name-status", "-M", base, head))


def history(repo: Path, since: str | None = None, limit: int | None = None) -> list[Commit]:
    args = ["log", "--no-merges", "--name-status", "-M", "--date=short",
            "--format=@@%H\t%ad\t%s"]
    if since:
        args.append(f"--since={since}")
    if limit:
        args.append(f"-n{limit}")
    commits: list[Commit] = []
    for block in _git(repo, *args).split("@@")[1:]:
        header, _, rest = block.partition("\n")
        sha, date, subject = (header.split("\t", 2) + ["", ""])[:3]
        commits.append(Commit(sha, date, subject, parse_name_status(rest)))
    return commits


def retro(repo: Path, policy: Policy, since: str | None = None,
          limit: int | None = None) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    by_default = 0
    unmapped_prefixes: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for commit in history(repo, since, limit):
        if not commit.paths:
            continue
        obligations = _OBLIGATION.findall(commit.subject)
        try:
            decision = classify(commit.paths, policy, obligations)
        except TierCheckFailed as exc:
            counts["check-failed"] += 1
            rows.append({"sha": commit.sha[:10], "date": commit.date,
                         "tier": "check-failed", "why": str(exc)})
            continue
        counts[decision.tier] += 1
        if decision.by_default:
            by_default += 1
        for finding in decision.findings:
            if finding.unmapped:
                parts = finding.path.split("/")
                prefix = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
                unmapped_prefixes[prefix] += 1
        rows.append({"sha": commit.sha[:10], "date": commit.date,
                     "tier": decision.tier, "by_default": decision.by_default,
                     "subject": commit.subject[:72]})

    total = sum(counts.values())
    share = {k: round(100 * v / total, 1) if total else 0.0 for k, v in counts.items()}
    return {
        "rulebook_hash": policy.rulebook_hash(),
        "changes": total,
        "counts": dict(counts),
        "share_percent": share,
        "tier_c_by_default": by_default,
        "tier_c_by_default_percent": round(100 * by_default / total, 1) if total else 0.0,
        "map_next": unmapped_prefixes.most_common(10),
        "rows": rows,
    }
