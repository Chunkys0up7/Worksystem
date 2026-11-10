"""The knowledge layer as data: blueprint moments, decisions, cards, skills.

`validate` checks every link between them. A missing link is a build failure,
not a warning: that is what makes the ID chain trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .. import ids
from ..policy import Policy
from ..tiering.rulebook import glob_to_regex

CHECKED_CARD_TYPES = ("FACT", "RULE", "CALC")


@dataclass
class Skill:
    dir: Path
    manifest: dict[str, Any]

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def implements(self) -> list[str]:
        return list(self.manifest.get("implements", []))

    @property
    def cards(self) -> list[str]:
        return list(self.manifest.get("cards", []))

    @property
    def checks(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("checks", []))

    @property
    def evals(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("evals", []))

    @property
    def pending(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("pending", []))


SPEC_STATUSES = ("draft", "approved", "done", "withdrawn")


@dataclass
class Spec:
    """A spec is markdown with a small front matter block the harness reads."""

    path: Path
    meta: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def cites(self) -> list[str]:
        return list(self.meta.get("cites", []))

    @property
    def allowed_paths(self) -> list[str]:
        return list(self.meta.get("allowed_paths", []))


def parse_front_matter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    return yaml.safe_load(text[4:end]) or {}


@dataclass
class Knowledge:
    root: Path
    blueprints: list[dict[str, Any]] = field(default_factory=list)
    moments: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    cards: dict[str, dict[str, Any]] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)
    specs: dict[str, Spec] = field(default_factory=dict)
    load_problems: list[str] = field(default_factory=list)


def _load_yaml_dir(directory: Path, knowledge: Knowledge, target: dict[str, Any]) -> None:
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        identifier = data.get("id")
        if identifier != path.stem:
            knowledge.load_problems.append(
                f"{path.relative_to(knowledge.root)}: id {identifier!r} does not match file name"
            )
        if identifier in target:
            knowledge.load_problems.append(f"duplicate id {identifier}")
        target[identifier] = data


def load_knowledge(root: Path) -> Knowledge:
    base = root / "knowledge"
    knowledge = Knowledge(root=root)
    for path in sorted((base / "blueprint").glob("*.yaml")):
        blueprint = yaml.safe_load(path.read_text()) or {}
        knowledge.blueprints.append(blueprint)
        for moment in blueprint.get("moments", []):
            if moment["id"] in knowledge.moments:
                knowledge.load_problems.append(f"duplicate id {moment['id']}")
            knowledge.moments[moment["id"]] = moment
    _load_yaml_dir(base / "decisions", knowledge, knowledge.decisions)
    _load_yaml_dir(base / "cards", knowledge, knowledge.cards)
    for manifest_path in sorted((base / "skills").glob("*/manifest.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        skill = Skill(dir=manifest_path.parent, manifest=manifest)
        if skill.id in knowledge.skills:
            knowledge.load_problems.append(f"duplicate id {skill.id}")
        knowledge.skills[skill.id] = skill
    for spec_path in sorted((root / "specs").glob("*.md")):
        spec = Spec(path=spec_path, meta=parse_front_matter(spec_path.read_text()))
        rel = spec_path.relative_to(root)
        if not spec.id:
            knowledge.load_problems.append(f"{rel}: no id in front matter")
            continue
        if not spec_path.name.startswith(spec.id):
            knowledge.load_problems.append(f"{rel}: file name does not start with {spec.id}")
        if spec.id in knowledge.specs:
            knowledge.load_problems.append(f"duplicate id {spec.id}")
        knowledge.specs[spec.id] = spec
    return knowledge


def _files(root: Path) -> list[str]:
    out = []
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts:
            out.append(path.relative_to(root).as_posix())
    return out


def validate(knowledge: Knowledge, policy: Policy) -> list[str]:
    """Every problem found. Empty means every link resolves."""
    problems = list(knowledge.load_problems)
    data_classes = set(policy.tier_rules.get("data_class_floor", {}))
    nodes = {m["node"] for m in knowledge.moments.values() if m.get("node")}

    for moment_id, moment in knowledge.moments.items():
        if ids.kind_of(moment_id) != "moment":
            problems.append(f"{moment_id}: not a valid moment id")
        for data_class in moment.get("data_classes", []):
            if data_class not in data_classes:
                problems.append(f"{moment_id}: unknown data class {data_class}")
        if moment.get("regulated"):
            if not moment.get("obligations"):
                problems.append(f"{moment_id}: regulated moment carries no obligation")
            if not moment.get("data_classes"):
                problems.append(f"{moment_id}: regulated moment carries no data class")
        for obligation in moment.get("obligations", []):
            if obligation not in knowledge.decisions:
                problems.append(f"{moment_id}: obligation {obligation} is not in the register")

    for decision_id in knowledge.decisions:
        if not ids.is_decision(decision_id):
            problems.append(f"{decision_id}: not a valid decision or obligation id")
        implemented = any(decision_id in s.implements for s in knowledge.skills.values())
        cited = any(decision_id in spec.cites for spec in knowledge.specs.values())
        if not implemented and not cited:
            problems.append(
                f"{decision_id}: no skill implements this decision and no spec cites it"
            )

    for card_id, card in knowledge.cards.items():
        if ids.kind_of(card_id) != "card":
            problems.append(f"{card_id}: not a valid card id")
        if card.get("moment") not in knowledge.moments:
            problems.append(f"{card_id}: moment {card.get('moment')} is not on a blueprint")
        for decision in card.get("implements", []):
            if decision not in knowledge.decisions:
                problems.append(f"{card_id}: implements unknown decision {decision}")
        for used in card.get("uses", []):
            if used not in knowledge.cards:
                problems.append(f"{card_id}: uses unknown card {used}")
        if not any(card_id in s.cards for s in knowledge.skills.values()):
            problems.append(f"{card_id}: no skill delivers this card")

    mapped = {s["id"]: s for s in policy.skill_map}
    repo_files = _files(knowledge.root)

    for skill in knowledge.skills.values():
        if ids.kind_of(skill.id) != "skill":
            problems.append(f"{skill.id}: not a valid skill id")
        if ids.kind_of(skill.ref) != "skill-version":
            problems.append(f"{skill.ref}: version is not major.minor.patch")
        for decision in skill.implements:
            if decision not in knowledge.decisions:
                problems.append(f"{skill.id}: implements unknown decision {decision}")
        for card in skill.cards:
            if card not in knowledge.cards:
                problems.append(f"{skill.id}: lists unknown card {card}")

        covered: set[str] = set()
        seen_checks: set[str] = set()
        for check in skill.checks:
            check_id = check.get("id", "")
            if ids.kind_of(check_id) != "check":
                problems.append(f"{skill.id}: {check_id!r} is not a valid check id")
            if check_id in seen_checks:
                problems.append(f"{skill.id}: duplicate check id {check_id}")
            seen_checks.add(check_id)
            if not (skill.dir / check.get("module", "")).is_file():
                problems.append(f"{check_id}: module {check.get('module')} does not exist")
            for card in check.get("covers", []):
                if card not in skill.cards:
                    problems.append(f"{check_id}: covers {card}, which the skill does not list")
                covered.add(card)

        for item in skill.evals:
            if ids.kind_of(item.get("id", "")) != "eval":
                problems.append(f"{skill.id}: {item.get('id')!r} is not a valid eval id")
            for card in item.get("covers", []):
                if card not in skill.cards:
                    problems.append(f"{item.get('id')}: covers {card}, which the skill does not list")
                elif ids.card_type(card) in CHECKED_CARD_TYPES:
                    continue  # an eval may add to a check; it never replaces one
                else:
                    covered.add(card)

        pending = {p["card"] for p in skill.pending if p.get("due_stage")}
        for card in skill.cards:
            if card in covered:
                continue
            if ids.card_type(card) in CHECKED_CARD_TYPES:
                problems.append(
                    f"{skill.id}: {card} has no paired deterministic check"
                )
            elif card not in pending:
                problems.append(
                    f"{skill.id}: {card} has no check and is not listed as pending"
                )

        entry = mapped.get(skill.id)
        if entry is None:
            problems.append(f"{skill.id}: not in the skill map")
            continue
        for glob in entry.get("governs", []):
            pattern = glob_to_regex(glob)
            if not any(pattern.match(f) for f in repo_files):
                problems.append(f"{skill.id}: governs {glob}, which matches no file")
        for node in entry.get("consuming_nodes") or []:
            if node["node"] not in nodes:
                problems.append(
                    f"{skill.id}: consuming node {node['node']} is on no blueprint moment"
                )

    for spec in knowledge.specs.values():
        if ids.kind_of(spec.id) != "spec":
            problems.append(f"{spec.id}: not a valid spec id")
        if spec.status not in SPEC_STATUSES:
            problems.append(f"{spec.id}: status {spec.status!r} is not one of {SPEC_STATUSES}")
        if not spec.cites:
            problems.append(f"{spec.id}: cites no decision or obligation")
        for cited_id in spec.cites:
            if cited_id not in knowledge.decisions:
                problems.append(f"{spec.id}: cites {cited_id}, which is not in the register")
        if not spec.allowed_paths:
            problems.append(f"{spec.id}: states no allowed paths")

    for skill_id in mapped:
        if skill_id not in knowledge.skills:
            problems.append(f"skill map names {skill_id}, which does not exist")

    return problems
