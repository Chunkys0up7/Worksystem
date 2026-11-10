from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.knowledge import load_knowledge  # noqa: E402
from harness.ledger import FileAnchor, Ledger  # noqa: E402
from harness.ledger.store import PostgresStore, SqliteStore  # noqa: E402
from harness.policy import load_policy  # noqa: E402

PG_DSN = os.environ.get("LEDGER_PG_DSN")
BACKENDS = ["sqlite"] + (["postgres"] if PG_DSN else [])


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def policy(root):
    return load_policy(root)


@pytest.fixture(scope="session")
def knowledge(root):
    return load_knowledge(root)


@pytest.fixture(scope="session")
def keydir(root) -> Path:
    return root / "demo-keys"


def fixed_clock() -> datetime:
    return datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path):
    if request.param == "sqlite":
        s = SqliteStore(tmp_path / "ledger" / "ledger.db")
        s.init()
        yield s
    else:
        table = f"ledger_{uuid.uuid4().hex[:12]}"
        s = PostgresStore(PG_DSN, table=table)
        s.init()
        yield s
        import psycopg

        with psycopg.connect(PG_DSN) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")


@pytest.fixture
def ledger(store, policy, tmp_path):
    return Ledger(store, policy, FileAnchor(tmp_path / "anchor.json"), clock=fixed_clock)


@pytest.fixture
def sqlite_ledger(policy, tmp_path):
    s = SqliteStore(tmp_path / "ledger" / "ledger.db")
    s.init()
    return Ledger(s, policy, FileAnchor(tmp_path / "anchor.json"), clock=fixed_clock)
