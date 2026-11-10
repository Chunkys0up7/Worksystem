"""The console's operations and the local server in front of them.

Two layers:

    Console   a fixed set of operations, each a call to a mechanism that the
              command line already exposes. No operation takes a command, a
              module name or a file to write.
    serve()   an HTTP server bound to the loopback address. It refuses a request
              that names another host, and a write request that a web page in
              another tab could send.

The console has no login. Anyone who can reach the loopback address on this
machine can press its buttons. It runs on the published demo keys and writes
only to throwaway directories. That is a stated limit (`docs/limits.md`).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from attacks import pack
from conformance.journey import NOT_LIVE, run_journey
from conformance.scenario import load_scenario
from conformance.suite import run_suite
from harness import chain
from harness.knowledge import load_knowledge
from harness.policy import load_policy
from harness.tiering import TierCheckFailed, classify
from harness.tower import tower_factory

HERE = Path(__file__).resolve().parent
PAGE = HERE / "index.html"
SLOT = '<script id="recorded" type="application/json">null</script>'
WRITE_HEADER = "X-Console"
CONFORMANCE = {
    # executor: (swap drill partner, second model). The same pairs the command line uses.
    "recorded": ("loop", None),
    "loop": ("cli-stub", "loop-b"),
    "cli-stub": ("loop", None),
}
TIER_EXAMPLES = [
    ["docs/stages.md"],
    ["executors/loop.py"],
    ["services/decision_income/calc.py"],
    ["docs/a.md", "docs/../services/decision_income/calc.py"],
    ["some/new/place.py"],
    ["docs/stages.md", "policy/tier-rules.yaml"],
]
MAX_PATHS, MAX_PATH_LENGTH, MAX_BODY = 50, 300, 20_000


class Refused(Exception):
    """The console will not do this. `status` is the HTTP status it answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Console:
    def __init__(self, root: Path, keydir: Path) -> None:
        self.root, self.keydir = Path(root), Path(keydir)
        self.policy = load_policy(self.root)
        self.knowledge = load_knowledge(self.root)
        self._scratch = tempfile.TemporaryDirectory(prefix="console-")
        self._busy = threading.Lock()      # one mechanism runs at a time
        self._state = threading.Lock()     # guards the fields below
        self._runs = 0
        self._journey: dict[str, Any] = {"state": "idle", "steps": [], "totals": None, "error": None}
        self._env = None                   # the last journey's world, kept for the ledger view
        self._tampered: dict[str, Any] | None = None

    def close(self) -> None:
        self._scratch.cleanup()

    # ------------------------------------------------------------- overview

    def commit(self) -> str:
        try:
            out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.root,
                                 capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    def limits(self) -> list[dict[str, str]]:
        """`docs/limits.md`, by section. The page shows the document, not a summary of it."""
        text = (self.root / "docs" / "limits.md").read_text(encoding="utf-8")
        parts = re.split(r"^## ", text, flags=re.M)
        out = []
        for part in parts[1:]:
            title, _, body = part.partition("\n")
            out.append({"title": title.strip(), "body": body.strip()})
        return out

    def overview(self) -> dict[str, Any]:
        k = self.knowledge
        return {
            "mode": "live",
            "not_live": NOT_LIVE,
            "commit": self.commit(),
            "attacks": [{"n": i, "name": a.name, "claim": a.claim, "attempt": a.attempt}
                        for i, a in enumerate(pack.ATTACKS, start=1)],
            "decisions": sorted(d for d in k.decisions if d.startswith("PD-")),
            "specs": [{"id": s.id, "title": s.title, "status": s.status}
                      for s in sorted(k.specs.values(), key=lambda s: s.id)],
            "executors": list(CONFORMANCE),
            "tier_examples": TIER_EXAMPLES,
            "counts": {"roles": len({i.role for i in self.policy.identities.values()}),
                       "identities": len(self.policy.identities),
                       "decisions": len(k.decisions), "cards": len(k.cards),
                       "skills": len(k.skills)},
            "limits": self.limits(),
        }

    # -------------------------------------------------------------- journey

    def _run_journey(self) -> None:
        with self._state:
            self._runs += 1
            workdir = Path(self._scratch.name) / f"journey-{self._runs}"
            self._journey = {"state": "running", "steps": [], "totals": None, "error": None}
            self._env, self._tampered = None, None

        def on_step(step) -> None:
            with self._state:
                self._journey["steps"].append(dict(step.__dict__))

        try:
            journey = run_journey(self.root, self.keydir, workdir, on_step=on_step)
        except Exception as exc:  # shown on the page, never swallowed
            with self._state:
                self._journey.update(state="failed", error=f"{type(exc).__name__}: {exc}")
            return
        with self._state:
            self._env = journey.env
            self._journey.update(state="done", totals=journey.totals)

    def start_journey(self) -> dict[str, Any]:
        if not self._busy.acquire(blocking=False):
            raise Refused("another mechanism is running; wait for it to finish", 409)

        def work() -> None:
            try:
                self._run_journey()
            finally:
                self._busy.release()

        with self._state:
            self._journey = {"state": "running", "steps": [], "totals": None, "error": None}
        threading.Thread(target=work, daemon=True).start()
        return {"state": "running"}

    def run_journey_now(self) -> dict[str, Any]:
        with self._busy:
            self._run_journey()
        return self.journey()

    def journey(self) -> dict[str, Any]:
        with self._state:
            return json.loads(json.dumps(self._journey))

    # --------------------------------------------------------------- ledger

    def _world(self):
        with self._state:
            env = self._env
        if env is None:
            raise Refused("run the journey first: the ledger shown here is the one it writes", 409)
        return env

    def ledger(self) -> dict[str, Any]:
        env = self._world()
        entries = env.ledger.entries()
        identities = env.policy.identities
        rows = [{"seq": e.seq, "id": e.id, "ts": e.ts, "actor": e.actor,
                 "role": identities[e.actor].role if e.actor in identities else "?",
                 "fact_type": e.fact_type, "record_class": e.record_class,
                 "refs": list(e.refs), "payload": e.payload,
                 "prev_hash": e.prev_hash[:12], "entry_hash": e.entry_hash[:12]}
                for e in entries]
        with self._state:
            tampered = dict(self._tampered) if self._tampered else None
        if tampered:
            tampered.pop("original", None)
        return {"entries": rows, "problems": env.ledger.verify(), "tampered": tampered}

    def _db(self, env) -> Path:
        return env.workdir / "ledger" / "ledger.db"

    def tamper(self, seq: Any) -> dict[str, Any]:
        """Edit one entry the way a database owner could: drop the append-only trigger and
        rewrite the row. The ledger is the journey's throwaway one, nothing else."""
        env = self._world()
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise Refused("seq must be a whole number")
        with self._busy:
            with self._state:
                if self._tampered:
                    raise Refused("one entry is already edited; restore it first", 409)
            conn = sqlite3.connect(self._db(env))
            try:
                row = conn.execute("SELECT payload FROM ledger WHERE seq = ?", (seq,)).fetchone()
                if row is None:
                    raise Refused(f"LED-{seq} is not in this ledger", 404)
                payload = json.loads(row[0])
                if "outcome" in payload:
                    change = f"outcome: {payload['outcome']!r} rewritten as 'passed'"
                    payload["outcome"] = "passed" if payload["outcome"] != "passed" else "held-for-approval"
                elif "tier" in payload:
                    change = f"tier: {payload['tier']!r} rewritten as 'A'"
                    payload["tier"] = "A" if payload["tier"] != "A" else "C"
                else:
                    change = "a field was added to the payload"
                    payload["edited_after_the_fact"] = True
                conn.execute("DROP TRIGGER IF EXISTS ledger_no_update")
                conn.execute("UPDATE ledger SET payload = ? WHERE seq = ?",
                             (json.dumps(payload, sort_keys=True, separators=(",", ":")), seq))
                conn.commit()
            finally:
                conn.close()
            with self._state:
                self._tampered = {"seq": seq, "id": f"LED-{seq}", "change": change,
                                  "original": row[0]}
        return self.ledger()

    def restore(self) -> dict[str, Any]:
        env = self._world()
        with self._busy:
            with self._state:
                tampered, self._tampered = self._tampered, None
            if tampered:
                conn = sqlite3.connect(self._db(env))
                try:
                    conn.execute("DROP TRIGGER IF EXISTS ledger_no_update")
                    conn.execute("UPDATE ledger SET payload = ? WHERE seq = ?",
                                 (tampered["original"], tampered["seq"]))
                    conn.commit()
                finally:
                    conn.close()
                env.ledger.store.init()  # puts the append-only triggers back
        return self.ledger()

    # -------------------------------------------------------------- attacks

    def attack(self, number: Any) -> dict[str, Any]:
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(pack.ATTACKS):
            raise Refused(f"there is no attack {number!r}", 404)
        item = pack.ATTACKS[number - 1]
        with self._busy:
            started = time.monotonic()
            sandbox = pack.Sandbox()
            try:
                result = item.fn(sandbox)
            except Exception as exc:  # an attack that crashes the build has broken it
                result = pack.Result(False, f"unexpected {type(exc).__name__}: {exc}")
            finally:
                sandbox.close()
            seconds = round(time.monotonic() - started, 2)
        verdict = {True: "held", False: "broken", None: "limit"}[result.held]
        return {"n": number, "name": item.name, "verdict": verdict,
                "observed": result.observed, "seconds": seconds}

    # ----------------------------------------------------------------- tier

    def tier(self, paths: Any) -> dict[str, Any]:
        if (not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS
                or not all(isinstance(p, str) and 0 < len(p) <= MAX_PATH_LENGTH for p in paths)):
            raise Refused(f"paths must be 1 to {MAX_PATHS} short strings")
        try:
            decision = classify(paths, self.policy, [])
        except TierCheckFailed as exc:
            return {"paths": paths, "failed": str(exc)}
        return {"paths": paths, **decision.to_payload("CHG-unrecorded")}

    def line_of_sight(self, decision_id: Any) -> dict[str, Any]:
        with self._state:
            env = self._env
        knowledge = env.knowledge if env else self.knowledge
        policy = env.policy if env else self.policy
        if not isinstance(decision_id, str) or decision_id not in knowledge.decisions:
            raise Refused(f"{decision_id!r} is not in the decision register", 404)
        text = chain.render_forward(chain.forward(decision_id, knowledge, policy,
                                                  env.ledger if env else None))
        return {"decision": decision_id, "text": text,
                "ledger": "the journey's ledger" if env else "no ledger: run the journey to see results"}

    def why(self, path: Any) -> dict[str, Any]:
        if not isinstance(path, str) or not 0 < len(path) <= MAX_PATH_LENGTH:
            raise Refused("path must be a short string")
        return {"path": path, "text": chain.render_why(chain.why(path, self.knowledge, self.policy))}

    # ---------------------------------------------------------- conformance

    def conformance(self, executor: Any) -> dict[str, Any]:
        if executor not in CONFORMANCE:
            raise Refused(f"the console runs the suite for: {', '.join(CONFORMANCE)}", 404)
        swap, second = CONFORMANCE[executor]
        scenario = load_scenario(self.root, "pd-0002")
        with self._busy:
            workdir = Path(tempfile.mkdtemp(prefix="suite-", dir=self._scratch.name))
            try:
                run = run_suite(executor, scenario, self.root, self.keydir, workdir,
                                swap_with=swap, second_model=second, tower_factory=tower_factory)
                return {"executor": run.executor, "kind": run.kind, "scenario": run.scenario,
                        "verdict": run.verdict, "clauses": [dict(c.__dict__) for c in run.clauses]}
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

    # ------------------------------------------------------------ recording

    def record(self, say=lambda message: None) -> dict[str, Any]:
        """Run everything once and keep what came back. Nothing is written by hand."""
        overview = self.overview()
        say("the journey")
        journey = self.run_journey_now()
        if journey["state"] != "done":
            raise RuntimeError(f"the journey did not finish: {journey['error']}")
        clean = self.ledger()
        gate = next((e["seq"] for e in clean["entries"] if e["fact_type"] == "gate.result"), 1)
        edited = self.tamper(gate)
        tamper = {"tampered": edited["tampered"], "problems": edited["problems"],
                  "payload": next(e["payload"] for e in edited["entries"] if e["seq"] == gate)}
        if self.restore()["problems"]:
            raise RuntimeError("the ledger did not verify after the edit was undone")
        lines = {d: self.line_of_sight(d) for d in sorted(self._world().knowledge.decisions)
                 if d.startswith("PD-")}
        results = []
        for item in overview["attacks"]:
            say(f"attack {item['n']} of {len(overview['attacks'])}: {item['name']}")
            results.append(self.attack(item["n"]))
        say("conformance")
        return {
            "mode": "recorded",
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "overview": {**overview, "mode": "recorded"},
            "journey": journey,
            "ledger": clean,
            "tamper": tamper,
            "attacks": results,
            "tier": [self.tier(paths) for paths in TIER_EXAMPLES],
            "chain": lines,
            "why": [self.why(p) for p in ("services/decision_income/calc.py", "docs/stages.md")],
            "conformance": {"loop": self.conformance("loop")},
        }


# ---------------------------------------------------------------------- page

def render_page(recording: dict[str, Any] | None = None, *, standalone: bool = True) -> str:
    """The page, with a recording in it or without. `standalone` wraps it as a whole document;
    without it the page is a fragment for a host that supplies its own document."""
    page = PAGE.read_text(encoding="utf-8")
    if SLOT not in page:
        raise RuntimeError("the page has lost the slot the recording goes in")
    if recording is not None:
        data = json.dumps(recording, separators=(",", ":")).replace("</", "<\\/")
        page = page.replace(SLOT, SLOT.replace("null", data))
    if not standalone:
        return page
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "<style>body{margin:0}[hidden]{display:none!important}</style>\n</head>\n<body>\n"
            + page + "\n</body>\n</html>\n")


# -------------------------------------------------------------------- server

def make_handler(console: Console):
    gets = {
        "/api/overview": lambda q: console.overview(),
        "/api/journey": lambda q: console.journey(),
        "/api/ledger": lambda q: console.ledger(),
        "/api/chain": lambda q: console.line_of_sight((q.get("decision") or [None])[0]),
        "/api/why": lambda q: console.why((q.get("path") or [None])[0]),
    }
    posts = {
        "/api/journey": lambda b: console.start_journey(),
        "/api/ledger/tamper": lambda b: console.tamper(b.get("seq")),
        "/api/ledger/restore": lambda b: console.restore(),
        "/api/attack": lambda b: console.attack(b.get("n")),
        "/api/tier": lambda b: console.tier(b.get("paths")),
        "/api/conformance": lambda b: console.conformance(b.get("executor")),
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "console"

        def log_message(self, *args) -> None:  # quiet: the page is the output
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _local(self) -> bool:
            """Refuse a request addressed to any name but this machine's loopback."""
            port = self.server.server_address[1]
            if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return True
            self._json(403, {"error": "this console answers only on the loopback address"})
            return False

        def _answer(self, fn, arg) -> None:
            try:
                self._json(200, fn(arg))
            except Refused as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:  # the page shows what went wrong
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self) -> None:
            if not self._local():
                return
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, render_page().encode(), "text/html; charset=utf-8")
            elif url.path in gets:
                self._answer(gets[url.path], parse_qs(url.query))
            else:
                self._json(404, {"error": "the console serves one page and a fixed set of operations"})

        def do_POST(self) -> None:
            if not self._local():
                return
            path = urlparse(self.path).path
            if path not in posts:
                self._json(404, {"error": "the console serves one page and a fixed set of operations"})
                return
            # A page in another tab cannot set this header without asking first, and the
            # console never says yes.
            if self.headers.get(WRITE_HEADER) != "1":
                self._json(403, {"error": f"a request that triggers something must carry {WRITE_HEADER}"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._json(400, {"error": "the body must be a small JSON object"})
                return
            self._answer(posts[path], body)

    return Handler


def make_server(console: Console, port: int = 0) -> ThreadingHTTPServer:
    """Bound to the loopback address and nothing else. There is no option to change that."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(console))
    server.daemon_threads = True
    return server


def serve(root: Path, keydir: Path, port: int) -> int:
    console = Console(root, keydir)
    try:
        server = make_server(console, port)
    except OSError as exc:
        print(f"could not listen on 127.0.0.1:{port}: {exc}")
        return 2
    print(f"console at http://127.0.0.1:{server.server_address[1]}/  (Ctrl-C to stop)")
    print(NOT_LIVE)
    print("No login. Loopback only. It runs on the published demo keys and writes only to "
          "throwaway directories.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
        console.close()
    return 0


def export(root: Path, keydir: Path, out: Path) -> int:
    console = Console(root, keydir)
    try:
        recording = console.record(say=lambda message: print(f"  recording: {message}", flush=True))
    finally:
        console.close()
    out.write_text(render_page(recording), encoding="utf-8")
    broken = [r["name"] for r in recording["attacks"] if r["verdict"] == "broken"]
    print(f"wrote {out}: a recorded run at {recording['overview']['commit']}, "
          f"{len(recording['attacks'])} attacks, {len(recording['ledger']['entries'])} ledger entries")
    return 1 if broken else 0
