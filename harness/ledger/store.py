"""Ledger stores. One contract, two backends that pass the same tests.

SQLite is the default so the build runs from a clone with no setup. Postgres is
the target store (stage 6 writes the runtime checkpoint and the ledger entry in
one transaction there). Both refuse UPDATE and DELETE at the database level.
That refusal is a convenience, not the control: anyone who owns the database
can drop a trigger. The control is the signature, the chain and the anchor.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from .api import Entry

COLUMNS = (
    "seq",
    "ts",
    "actor",
    "fact_type",
    "record_class",
    "retain_until",
    "dispose_after",
    "payload",
    "refs",
    "content_hash",
    "signature",
    "prev_hash",
    "entry_hash",
)


def _to_row(entry: Entry) -> tuple[Any, ...]:
    return (
        entry.seq,
        entry.ts,
        entry.actor,
        entry.fact_type,
        entry.record_class,
        entry.retain_until,
        entry.dispose_after,
        json.dumps(entry.payload, sort_keys=True),
        json.dumps(list(entry.refs)),
        entry.content_hash,
        entry.signature,
        entry.prev_hash,
        entry.entry_hash,
    )


def _from_row(row: tuple[Any, ...]) -> Entry:
    data = dict(zip(COLUMNS, row))
    data["payload"] = json.loads(data["payload"])
    data["refs"] = tuple(json.loads(data["refs"]))
    return Entry(**data)


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.parent.exists():
            raise sqlite3.OperationalError(f"ledger location missing: {self.path.parent}")
        return sqlite3.connect(self.path)

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger (
                    seq INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    fact_type TEXT NOT NULL,
                    record_class TEXT NOT NULL,
                    retain_until TEXT NOT NULL,
                    dispose_after TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    refs TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS ledger_no_update
                    BEFORE UPDATE ON ledger
                    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS ledger_no_delete
                    BEFORE DELETE ON ledger
                    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
                """
            )

    def head(self) -> tuple[int, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else None

    def append(self, entry: Entry) -> None:
        marks = ",".join("?" for _ in COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO ledger ({','.join(COLUMNS)}) VALUES ({marks})",
                _to_row(entry),
            )

    def rows(self) -> Iterator[Entry]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {','.join(COLUMNS)} FROM ledger ORDER BY seq"
            ).fetchall()
        for row in rows:
            yield _from_row(row)


    def begin(self) -> "_SqliteTx":
        if not self.path.parent.exists():
            raise sqlite3.OperationalError(f"ledger location missing: {self.path.parent}")
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")  # one writer; the head cannot move under us
        return _SqliteTx(conn)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()


class _SqliteTx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.execute(sql, params)

    def head(self) -> tuple[int, str] | None:
        row = self.conn.execute(
            "SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else None

    def append(self, entry: Entry) -> None:
        marks = ",".join("?" for _ in COLUMNS)
        self.conn.execute(
            f"INSERT INTO ledger ({','.join(COLUMNS)}) VALUES ({marks})", _to_row(entry))

    def commit(self) -> None:
        self.conn.execute("COMMIT")
        self.conn.close()

    def rollback(self) -> None:
        try:
            self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()


class PostgresStore:
    def __init__(self, dsn: str, table: str = "ledger") -> None:
        self.dsn = dsn
        self.table = table

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def init(self) -> None:
        t = self.table
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    seq BIGINT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    fact_type TEXT NOT NULL,
                    record_class TEXT NOT NULL,
                    retain_until TEXT NOT NULL,
                    dispose_after TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    refs TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute(
                f"""
                CREATE OR REPLACE FUNCTION {t}_append_only() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'ledger is append-only'; END;
                $$ LANGUAGE plpgsql
                """
            )
            conn.execute(f"DROP TRIGGER IF EXISTS {t}_no_change ON {t}")
            conn.execute(
                f"""
                CREATE TRIGGER {t}_no_change BEFORE UPDATE OR DELETE ON {t}
                FOR EACH ROW EXECUTE FUNCTION {t}_append_only()
                """
            )

    def head(self) -> tuple[int, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT seq, entry_hash FROM {self.table} ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return (int(row[0]), row[1]) if row else None

    def append(self, entry: Entry) -> None:
        marks = ",".join("%s" for _ in COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self.table} ({','.join(COLUMNS)}) VALUES ({marks})",
                _to_row(entry),
            )

    def rows(self) -> Iterator[Entry]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {','.join(COLUMNS)} FROM {self.table} ORDER BY seq"
            ).fetchall()
        for row in rows:
            yield _from_row(tuple(row))


    def begin(self) -> "_PostgresTx":
        conn = self._connect()
        conn.execute("SELECT pg_advisory_xact_lock(7243001)")  # one writer at a time
        return _PostgresTx(conn, self.table)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._connect() as conn:
            return [tuple(r) for r in conn.execute(sql.replace("?", "%s"), params).fetchall()]


class _PostgresTx:
    def __init__(self, conn, table: str) -> None:
        self.conn, self.table = conn, table

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.execute(sql.replace("?", "%s"), params)

    def head(self) -> tuple[int, str] | None:
        row = self.conn.execute(
            f"SELECT seq, entry_hash FROM {self.table} ORDER BY seq DESC LIMIT 1").fetchone()
        return (int(row[0]), row[1]) if row else None

    def append(self, entry: Entry) -> None:
        marks = ",".join("%s" for _ in COLUMNS)
        self.conn.execute(
            f"INSERT INTO {self.table} ({','.join(COLUMNS)}) VALUES ({marks})", _to_row(entry))

    def commit(self) -> None:
        self.conn.commit()
        self.conn.close()

    def rollback(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


def open_store(location: str | Path):
    """A postgres:// DSN opens Postgres; anything else is a SQLite file path."""
    text = str(location)
    if text.startswith(("postgres://", "postgresql://")):
        return PostgresStore(text)
    return SqliteStore(Path(text))
