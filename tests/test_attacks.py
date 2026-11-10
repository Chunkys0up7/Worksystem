"""The attack pack must hold. A new BROKEN row fails the build.

The LIMIT rows are pinned by name: if an attack that used to be a stated
limit starts holding, or a held one becomes a limit, this test says so.
"""

from __future__ import annotations

from attacks.pack import run_all

STATED_LIMITS = {
    "rebuild the chain and the anchor",
    "weaken the check and re-sign the skill",
    "the executor reads the signing keys",
    "a command-line agent brings its own model",
}


def test_attack_pack():
    results = run_all()
    assert len(results) >= 68
    broken = [(a.name, r.observed) for a, r in results if r.held is False]
    assert broken == []
    limits = {a.name for a, r in results if r.held is None}
    assert limits == STATED_LIMITS
