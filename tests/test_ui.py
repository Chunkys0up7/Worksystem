"""SPEC-0007: the console. It shows and triggers the build, and holds no control."""

from __future__ import annotations

import http.client
import json
import re
import sqlite3
import threading
import time

import pytest
import yaml

from attacks import pack
from harness.tiering import classify
from harness.tower import fitness
from ui.server import PAGE, WRITE_HEADER, Console, Refused, make_server, render_page


@pytest.fixture(scope="module")
def console(root, keydir):
    c = Console(root, keydir)
    yield c
    c.close()


@pytest.fixture(scope="module")
def server(console):
    s = make_server(console)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def ask(server, method, path, body=None, **headers):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        sent = {"Host": f"127.0.0.1:{port}", **headers}
        if method == "POST":
            sent.setdefault(WRITE_HEADER, "1")
            sent.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=json.dumps(body or {}) if method == "POST" else None,
                     headers={k: v for k, v in sent.items() if v is not None})
        response = conn.getresponse()
        raw = response.read().decode()
        kind = response.getheader("Content-Type", "")
        return response.status, json.loads(raw) if kind.startswith("application/json") else raw
    finally:
        conn.close()


# ------------------------------------------------------------------ the page

def test_the_page_is_served_whole(server):
    status, page = ask(server, "GET", "/")
    assert status == 200 and page.startswith("<!doctype html>")
    assert "<title>Worksystem console</title>" in page


def test_the_page_asks_nobody_else():
    """U5: no script, style, font or image from anywhere. Not one outside address."""
    page = PAGE.read_text(encoding="utf-8")
    assert not re.search(r"https?://", page)
    assert not re.search(r"<(link|img|iframe)\b", page)
    assert not re.search(r"<script[^>]+src=", page)


def test_the_page_says_what_is_not_live(console):
    """U7: the notice sits outside every view, so no view can be shown without it."""
    page = PAGE.read_text(encoding="utf-8")
    notice = page.index('id="notice"')
    assert "No live model" in page[notice:page.index("<main")]
    assert "SCRIPTED" in console.overview()["not_live"]


def test_no_dashes_in_the_console(root):
    for path in (root / "ui").glob("*"):
        if path.is_file():
            assert not re.search("[" + chr(0x2013) + chr(0x2014) + "]", path.read_text(encoding="utf-8")), path


# ------------------------------------------------------- the real mechanisms

def test_overview_lists_the_pack(server):
    status, data = ask(server, "GET", "/api/overview")
    assert status == 200 and data["mode"] == "live"
    assert [a["name"] for a in data["attacks"]] == [a.name for a in pack.ATTACKS]
    assert data["limits"] and data["limits"][0]["title"].startswith("1.")


def test_tier_is_the_rulebook(server, policy):
    """U1: the page's answer is the rulebook's answer, including for a path that hides."""
    paths = ["docs/a.md", "docs/../services/decision_income/calc.py"]
    status, data = ask(server, "POST", "/api/tier", {"paths": paths})
    expected = classify(paths, policy, [])
    assert status == 200 and data["tier"] == expected.tier == "C"
    assert data["rulebook_hash"] == expected.rulebook_hash
    status, data = ask(server, "POST", "/api/tier", {"paths": ["some/new/place.py"]})
    assert data["tier"] == "C" and data["by_default"]


def test_one_attack(server):
    """U1."""
    status, data = ask(server, "POST", "/api/attack", {"n": 4})
    assert status == 200 and data["verdict"] == "held" and "LED-" in data["observed"]
    assert data["name"] == pack.ATTACKS[3].name


def test_why_and_line_of_sight(server):
    status, data = ask(server, "GET", "/api/why?path=services/decision_income/calc.py")
    assert status == 200 and "Tier C" in data["text"] and "SKL-income-calculation" in data["text"]
    status, data = ask(server, "GET", "/api/chain?decision=PD-0001")
    assert status == 200 and "PD-0001" in data["text"]
    assert ask(server, "GET", "/api/chain?decision=PD-9999")[0] == 404


# ------------------------------------------------------------ what is refused

@pytest.mark.parametrize("method, path, body, headers, expect", [
    ("POST", "/api/journey", {}, {WRITE_HEADER: None}, 403),
    ("POST", "/api/attack", {"n": 1}, {WRITE_HEADER: "0"}, 403),
    ("POST", "/api/attack", {"n": 1}, {"Host": "elsewhere.example"}, 403),
    ("GET", "/api/overview", None, {"Host": "elsewhere.example"}, 403),
    ("GET", "/", None, {"Host": "elsewhere.example"}, 403),
])
def test_another_origin_is_refused(server, console, method, path, body, headers, expect):
    """U4: a page in another tab can send a plain form post, and can point a name of its own
    at the loopback address. Neither gets an answer."""
    assert ask(server, method, path, body, **headers)[0] == expect
    assert console.journey()["state"] == "idle"


@pytest.mark.parametrize("method, path, body, expect", [
    ("GET", "/ui/server.py", None, 404),
    ("GET", "/../CLAUDE.md", None, 404),
    ("GET", "/demo-keys/ci-gates.key", None, 404),
    ("POST", "/api/run", {"command": "id"}, 404),
    ("POST", "/api/attack", {"n": 0}, 404),
    ("POST", "/api/attack", {"n": "1; id"}, 404),
    ("POST", "/api/attack", {"n": True}, 404),
    ("POST", "/api/conformance", {"executor": "cli"}, 404),
    ("POST", "/api/conformance", {"executor": "../x"}, 404),
    ("POST", "/api/tier", {"paths": "docs/a.md"}, 400),
    ("POST", "/api/tier", {"paths": ["x"] * 51}, 400),
    ("POST", "/api/tier", {"paths": [7]}, 400),
    ("POST", "/api/ledger/tamper", {"seq": 1}, 409),
    ("GET", "/api/ledger", None, 409),
])
def test_only_fixed_operations(server, method, path, body, expect):
    """U3: one page, a fixed set of operations, and nothing that names a file or a command."""
    assert ask(server, method, path, body)[0] == expect


def test_a_body_that_is_not_a_small_object_is_refused(server):
    port = server.server_address[1]
    oversized = json.dumps({"paths": ["docs/a.md"], "padding": "x" * 30_000}).encode()
    for raw in (b"[1]", b"not json", oversized):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("POST", "/api/tier", body=raw,
                     headers={"Host": f"127.0.0.1:{port}", WRITE_HEADER: "1"})
        assert conn.getresponse().status == 400
        conn.close()


def test_the_server_listens_on_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_the_harness_never_imports_the_console(root):
    """U8."""
    rules = {r["id"]: r for r in yaml.safe_load((root / "policy/fitness.yaml").read_text())["rules"]}
    found = fitness.check_file("harness/thing.py", "from ui.server import Console\n", rules["FIT-003"])
    assert any("imports ui" in f for f in found), found
    assert fitness.run(root, [rules["FIT-003"]]) == []


# ------------------------------------------------- the journey and its ledger

@pytest.fixture(scope="module")
def travelled(server, console):
    assert ask(server, "POST", "/api/journey")[1] == {"state": "running"}
    assert ask(server, "POST", "/api/journey")[0] == 409  # one at a time
    seen = set()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status, data = ask(server, "GET", "/api/journey")
        seen.add(len(data["steps"]))
        if data["state"] != "running":
            break
        time.sleep(0.5)
    return data, seen


def test_journey_from_the_page(travelled, server):
    """U1: the same twelve steps, arriving one at a time, ending on a whole ledger."""
    data, seen = travelled
    assert data["state"] == "done" and data["error"] is None
    assert [s["number"] for s in data["steps"]] == list(range(1, 13))
    assert len(seen) > 3, "the page should see steps as they close, not all at the end"
    totals = data["totals"]
    assert totals["intact"] and totals["reconciled"] and totals["live_models"] == 0
    status, ledger = ask(server, "GET", "/api/ledger")
    assert status == 200 and len(ledger["entries"]) == totals["entries"]
    assert ledger["problems"] == [] and ledger["tampered"] is None
    assert ask(server, "GET", "/api/chain?decision=PD-0002")[0] == 200


def test_edit_an_entry_and_verification_says_so(travelled, server, console):
    """U2."""
    _, ledger = ask(server, "GET", "/api/ledger")
    target = next(e for e in ledger["entries"] if e["fact_type"] == "gate.result")
    status, edited = ask(server, "POST", "/api/ledger/tamper", {"seq": target["seq"]})
    assert status == 200 and edited["tampered"]["id"] == target["id"]
    assert "original" not in edited["tampered"]
    assert edited["problems"] and all(p.startswith(target["id"] + ":") for p in edited["problems"])
    changed = next(e for e in edited["entries"] if e["seq"] == target["seq"])
    assert changed["payload"] != target["payload"]
    assert ask(server, "POST", "/api/ledger/tamper", {"seq": 1})[0] == 409
    assert ask(server, "POST", "/api/ledger/tamper", {"seq": 10**6})[0] in (404, 409)

    status, restored = ask(server, "POST", "/api/ledger/restore")
    assert status == 200 and restored["problems"] == [] and restored["tampered"] is None
    assert restored["entries"] == ledger["entries"]
    # the append-only trigger is back
    conn = sqlite3.connect(console._db(console._world()))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE ledger SET actor = 'x' WHERE seq = 1")
    conn.close()


def test_the_edit_touches_only_the_journeys_ledger(travelled, console, root):
    assert root not in console._db(console._world()).parents


def test_no_ledger_before_a_journey(root, keydir):
    fresh = Console(root, keydir)
    try:
        with pytest.raises(Refused):
            fresh.ledger()
    finally:
        fresh.close()


# --------------------------------------------------------------- a recording

def test_a_recording_is_labelled_and_whole(travelled, console, monkeypatch):
    """U6: a recording is what the run returned, says so, and cannot break out of its slot."""
    monkeypatch.setattr(pack, "ATTACKS", pack.ATTACKS[:2] + pack.ATTACKS[7:8])
    monkeypatch.setattr(console, "conformance", lambda executor: {
        "executor": executor, "kind": "scripted", "scenario": "pd-0002",
        "verdict": "</script><script>not run in this test", "clauses": []})
    recording = console.record()
    assert recording["mode"] == recording["overview"]["mode"] == "recorded"
    assert recording["recorded_at"].endswith("UTC") and recording["overview"]["commit"]
    assert [r["verdict"] for r in recording["attacks"]] == ["held", "held", "limit"]
    assert len(recording["journey"]["steps"]) == 12
    assert recording["ledger"]["problems"] == [] and recording["tamper"]["problems"]
    assert recording["tamper"]["tampered"]["id"] in recording["tamper"]["problems"][0]
    assert console.ledger()["problems"] == [], "recording must leave the ledger as it found it"
    assert len(recording["tier"]) == 6 and set(recording["chain"]) == {"PD-0001", "PD-0002"}

    page = render_page(recording)
    assert page.startswith("<!doctype html>")
    body = page.split('<script id="recorded" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(body) == recording
    fragment = render_page(recording, standalone=False)
    assert not fragment.startswith("<!doctype") and "<body" not in fragment
    assert json.loads(render_page().split('type="application/json">', 1)[1].split("</script>", 1)[0]) is None
