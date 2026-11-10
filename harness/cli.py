"""Command line for the reference build. `python -m harness --help`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import chain as chain_mod
from .change import run_change
from .knowledge import SkillInvalid, load_knowledge, publish, validate, verify
from .ledger import FileAnchor, Ledger, open_store, sign_submission
from .policy import PolicyError, load_policy, repo_root
from .tiering import TierCheckFailed, classify
from .tiering.retro import changed_paths, retro


class Context:
    def __init__(self, root: Path | None = None, ledger_dir: Path | None = None) -> None:
        self.root = root or repo_root()
        self.policy = load_policy(self.root)
        self.knowledge = load_knowledge(self.root)
        self.keydir = Path(os.environ.get("HARNESS_KEYS", self.root / "demo-keys"))
        self.ledger_dir = ledger_dir or Path(
            os.environ.get("HARNESS_LEDGER_DIR", self.root / ".ledger")
        )
        location = os.environ.get("LEDGER_DSN", str(self.ledger_dir / "ledger.db"))
        self.store = open_store(location)
        self.ledger = Ledger(
            self.store, self.policy, FileAnchor(self.ledger_dir / "anchor.json")
        )


def _print_decision(decision) -> None:
    print(f"Tier {decision.tier}" + ("  (by default: unmapped paths)" if decision.by_default else ""))
    for reason in decision.reasons:
        print(f"  change: {reason}")
    for finding in decision.findings:
        print(f"  {finding.tier}  {finding.path}")
        for reason in finding.reasons:
            print(f"       {reason}")
    print(f"  rulebook {decision.rulebook_hash[:12]}")


def cmd_init(ctx: Context, args) -> int:
    ctx.store.init()
    print(f"ledger ready at {ctx.ledger_dir}")
    return 0


def cmd_validate(ctx: Context, args) -> int:
    problems = validate(ctx.knowledge, ctx.policy)
    for skill in ctx.knowledge.skills.values():
        try:
            verify(skill, ctx.policy)
        except SkillInvalid as exc:
            problems.append(str(exc))
    if problems:
        print(f"{len(problems)} broken link(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    k = ctx.knowledge
    print(
        f"ok: {len(k.moments)} moments, {len(k.decisions)} decisions, "
        f"{len(k.cards)} cards, {len(k.skills)} skill(s); every link resolves, "
        "every skill verifies"
    )
    return 0


def cmd_tier(ctx: Context, args) -> int:
    paths = list(args.paths or [])
    if args.diff:
        paths += changed_paths(ctx.root, args.diff[0], args.diff[1])
    try:
        decision = classify(paths, ctx.policy, args.obligation or [])
    except TierCheckFailed as exc:
        print(f"TIER CHECK FAILED: {exc}")
        return 2
    if args.json:
        print(json.dumps(decision.to_payload(args.subject or "CHG-unrecorded"), indent=2))
    else:
        _print_decision(decision)
    return 0


def cmd_retro(ctx: Context, args) -> int:
    report = retro(Path(args.repo or ctx.root), ctx.policy, args.since, args.limit)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{report['changes']} changes tiered with rulebook {report['rulebook_hash'][:12]}")
    for tier in ("A", "B", "C", "check-failed"):
        if tier in report["counts"]:
            print(f"  {tier:<12} {report['counts'][tier]:>5}  {report['share_percent'][tier]:>5}%")
    print(
        f"  Tier C only because paths are unmapped: {report['tier_c_by_default']} "
        f"({report['tier_c_by_default_percent']}%)"
    )
    if report["map_next"]:
        print("  map these next:")
        for prefix, count in report["map_next"]:
            print(f"    {prefix}  ({count} touches)")
    return 0


def cmd_skill(ctx: Context, args) -> int:
    targets = [args.id] if args.id else list(ctx.knowledge.skills)
    status = 0
    for skill_id in targets:
        skill = ctx.knowledge.skills.get(skill_id)
        if skill is None:
            print(f"{skill_id}: no such skill")
            status = 1
            continue
        try:
            if args.action == "publish":
                release = publish(skill, ctx.keydir)
                print(f"{skill.ref} released, content {release['content_hash'][:12]}")
            else:
                release = verify(skill, ctx.policy)
                print(f"{skill.ref} verified, content {release['content_hash'][:12]}")
        except SkillInvalid as exc:
            print(f"INVALID: {exc}")
            status = 1
    return status


def _record_skill_release(ctx: Context) -> None:
    for skill in ctx.knowledge.skills.values():
        release = verify(skill, ctx.policy)
        if ctx.ledger.find(fact_type="skill.published", ref=skill.ref):
            continue
        entry = ctx.ledger.append(
            sign_submission(
                ctx.keydir, "skill-publisher", "skill.published",
                {"subject": skill.ref, "content_hash": release["content_hash"],
                 "implements": skill.implements, "cards": skill.cards},
                [skill.id, skill.ref, *skill.implements],
            )
        )
        print(f"{entry.id} skill.published {skill.ref}")


def cmd_change(ctx: Context, args) -> int:
    run = run_change(
        args.id, args.paths, ledger=ctx.ledger, keydir=ctx.keydir,
        policy=ctx.policy, knowledge=ctx.knowledge, obligations=args.obligation or [],
    )
    print(f"{run.change_id}: {run.outcome.upper()}")
    for step in run.steps:
        print(f"  {step}")
    return 0 if run.outcome in ("passed", "held-for-approval") else 1


def cmd_chain(ctx: Context, args) -> int:
    try:
        data = chain_mod.forward(args.decision, ctx.knowledge, ctx.policy, ctx.ledger)
    except KeyError as exc:
        print(exc.args[0])
        return 1
    print(json.dumps(data, indent=2) if args.json else chain_mod.render_forward(data))
    return 0


def cmd_why(ctx: Context, args) -> int:
    data = chain_mod.why(args.path, ctx.knowledge, ctx.policy)
    print(json.dumps(data, indent=2) if args.json else chain_mod.render_why(data))
    return 0


def cmd_ledger(ctx: Context, args) -> int:
    if args.action == "verify":
        problems = ctx.ledger.verify()
        if problems:
            print(f"LEDGER NOT INTACT: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"ledger intact: {len(ctx.ledger.entries())} entries, chain and anchor agree")
        return 0
    if args.action == "reconcile":
        missing = ctx.ledger.reconcile()
        entry = ctx.ledger.append(
            sign_submission(
                ctx.keydir, "ci-reconciler", "reconciliation.result",
                {"subject": "reconciliation", "missing": missing, "complete": not missing}, [],
            )
        )
        print(f"{entry.id} reconciliation: " + ("complete" if not missing else f"{len(missing)} gap(s)"))
        for gap in missing:
            print(f"  {gap}")
        return 0 if not missing else 1
    for entry in ctx.ledger.entries():
        subject = entry.payload.get("subject", "")
        extra = entry.payload.get("outcome") or entry.payload.get("tier") or ""
        print(f"{entry.id:<8} {entry.ts}  {entry.actor:<16} {entry.fact_type:<22} {subject} {extra}")
    return 0


def cmd_demo(ctx: Context, args) -> int:
    """One pipeline, three changes, three different paths through it."""
    if ctx.store.__class__.__name__ == "SqliteStore":
        for name in ("ledger.db", "anchor.json"):
            (ctx.ledger_dir / name).unlink(missing_ok=True)
    ctx.store.init()

    print("== knowledge: every link resolves and the skill is the released one")
    if cmd_validate(ctx, args):
        return 1
    _record_skill_release(ctx)

    changes = [
        ("CHG-demo-a", ["docs/limits.md"], "a documentation fix"),
        ("CHG-demo-b", ["harness/cli.py", "tests/test_tiering.py"], "a harness feature"),
        ("CHG-demo-c", ["services/decision_income/calc.py"], "a change to the regulated calculation"),
    ]
    for change_id, paths, label in changes:
        print(f"\n== {change_id}: {label}")
        run = run_change(change_id, paths, ledger=ctx.ledger, keydir=ctx.keydir,
                         policy=ctx.policy, knowledge=ctx.knowledge)
        print(f"  outcome: {run.outcome.upper()}")
        for step in run.steps:
            print(f"  {step}")

    print("\n== line of sight: decision to code to evidence")
    print(chain_mod.render_forward(
        chain_mod.forward("PD-0001", ctx.knowledge, ctx.policy, ctx.ledger)))
    print("\n== and back: why does this code look like this")
    print(chain_mod.render_why(
        chain_mod.why("services/decision_income/calc.py", ctx.knowledge, ctx.policy)))
    print("\n== evidence")
    cmd_ledger(ctx, argparse.Namespace(action="verify"))
    cmd_ledger(ctx, argparse.Namespace(action="reconcile"))
    return 0


def cmd_spec(ctx: Context, args) -> int:
    for spec in ctx.knowledge.specs.values():
        print(f"{spec.id}  {spec.status:<9} {spec.title}")
        print(f"    cites: {', '.join(spec.cites)}")
        print(f"    may touch: {', '.join(spec.allowed_paths)}")
    return 0


def cmd_commits(ctx: Context, args) -> int:
    from .trailers import check_commits

    results = check_commits(ctx.root, ctx.knowledge, ctx.policy)
    bad = [(sha, problems) for sha, problems in results if problems]
    for sha, problems in results:
        print(f"{sha}  {'ok' if not problems else 'FAIL'}")
        for problem in problems:
            print(f"    {problem}")
    print(f"{len(results) - len(bad)} of {len(results)} commit(s) cite a spec and stay inside it")
    return 1 if bad else 0


DEFAULT_PARTNERS = {
    # executor: (swap drill partner, second model for portability)
    "recorded": ("loop", None),
    "loop": ("cli-stub", "loop-b"),
    "loop-b": ("cli-stub", "loop"),
    "cli-stub": ("loop", None),
    "cli": ("loop", None),
}


def cmd_conformance(ctx: Context, args) -> int:
    import tempfile

    sys.path.insert(0, str(ctx.root))
    import executors
    from conformance.scenario import load_scenario
    from conformance.suite import render, run_suite, to_json

    try:
        scenario = load_scenario(ctx.root, args.scenario)
        executors.load(args.executor, scenario.dir, ctx.policy)
    except KeyError as exc:
        print(exc.args[0])
        return 2
    swap, second = DEFAULT_PARTNERS.get(args.executor, (None, None))
    with tempfile.TemporaryDirectory(prefix="conformance-") as tmp:
        run = run_suite(args.executor, scenario, ctx.root, ctx.keydir, Path(tmp),
                        swap_with=args.swap or swap, second_model=args.second_model or second,
                        tower_factory=_tower_factory())
        print(to_json(run) if args.json else render(run))
        if args.show_run and run.delivery:
            print("\nthe run:")
            for step in run.delivery.steps:
                print(f"  {step}")
            print("\nline of sight:")
            from . import chain as chain_mod
            from .knowledge import load_knowledge
            from .policy import load_policy
            print(chain_mod.render_forward(chain_mod.forward(
                scenario.decision, load_knowledge(run.root), load_policy(run.root), run.ledger)))
    return 1 if run.count("fail") else 0


def cmd_journey(ctx: Context, args) -> int:
    import tempfile

    sys.path.insert(0, str(ctx.root))
    from conformance.journey import render, run_journey

    with tempfile.TemporaryDirectory(prefix="journey-") as tmp:
        journey = run_journey(ctx.root, ctx.keydir, Path(tmp))
        if args.json:
            print(json.dumps(journey.as_dict(), indent=2))
        else:
            print(render(journey))
        ok = journey.totals["intact"] and journey.totals["reconciled"] and not journey.totals["misplaced"]
    return 0 if ok else 1


def cmd_ui(ctx: Context, args) -> int:
    """The console is presentation: it lives outside the harness and is loaded here, at the edge."""
    sys.path.insert(0, str(ctx.root))
    from ui.server import export, serve

    if args.export:
        return export(ctx.root, ctx.keydir, Path(args.export))
    return serve(ctx.root, ctx.keydir, args.port)


def _tower_factory():
    """The confidence tower, when it is built (stage 5)."""
    try:
        from .tower import tower_factory
    except ImportError:
        return None
    return tower_factory


def cmd_attacks(ctx: Context, args) -> int:
    sys.path.insert(0, str(ctx.root))
    from attacks.pack import main as attack_main

    return attack_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the ledger").set_defaults(fn=cmd_init)
    sub.add_parser("validate", help="check every link in the knowledge layer").set_defaults(fn=cmd_validate)

    p = sub.add_parser("tier", help="tier a set of paths (reads only, records nothing)")
    p.add_argument("--paths", nargs="*")
    p.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"))
    p.add_argument("--obligation", action="append")
    p.add_argument("--subject")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_tier)

    p = sub.add_parser("retro", help="tier past commits and report the mix")
    p.add_argument("--repo")
    p.add_argument("--since")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_retro)

    p = sub.add_parser("skill", help="release or verify a skill")
    p.add_argument("action", choices=["publish", "verify"])
    p.add_argument("id", nargs="?")
    p.set_defaults(fn=cmd_skill)

    p = sub.add_parser("change", help="run one change: propose, tier, check, gate")
    p.add_argument("id")
    p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--obligation", action="append")
    p.set_defaults(fn=cmd_change)

    p = sub.add_parser("chain", help="decision to skill to check to ledger")
    p.add_argument("decision")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_chain)

    p = sub.add_parser("why", help="path back to its skill, cards and decisions")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_why)

    p = sub.add_parser("ledger", help="show, verify or reconcile the ledger")
    p.add_argument("action", choices=["show", "verify", "reconcile"])
    p.set_defaults(fn=cmd_ledger)

    sub.add_parser("spec", help="list the specs and what each may touch").set_defaults(fn=cmd_spec)

    p = sub.add_parser("commits", help="check that commits cite a spec and stay inside it")
    p.add_argument("action", choices=["check"])
    p.set_defaults(fn=cmd_commits)

    p = sub.add_parser("conformance", help="run the executor conformance suite")
    p.add_argument("executor")
    p.add_argument("--scenario", default="pd-0002")
    p.add_argument("--show-run", action="store_true", help="also print the run and the ID chain")
    p.add_argument("--swap", help="second executor for the swap drill")
    p.add_argument("--second-model", help="second executor/model for model portability")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_conformance)

    p = sub.add_parser("journey", help="the whole road, discovery to change, on one ledger")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_journey)

    p = sub.add_parser("ui", help="the console: show and trigger all of it from a browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--export", metavar="FILE",
                   help="run everything once and write a self-contained recorded page")
    p.set_defaults(fn=cmd_ui)

    sub.add_parser("demo", help="one pipeline, three changes, three tiers").set_defaults(fn=cmd_demo)
    sub.add_parser("attacks", help="run the attack pack").set_defaults(fn=cmd_attacks)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Context()
    except PolicyError as exc:
        print(f"POLICY REFUSED TO LOAD: {exc}")
        return 2
    return args.fn(ctx, args)
