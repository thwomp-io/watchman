"""A small history store: typed rows in SQLite, with a JSON-lines export that version control carries.

The database is one file in the state directory, shared by every store that uses it; each store owns
its own tables and its own export file. The database is never committed. After every write a store
rewrites its export in full, and `rebuild` reproduces its tables from that export, so a fresh
checkout has its history: a read that finds the export and none of the store's rows rebuilds first.

A store is declared by subclassing `Row` once per table and passing those types to `Store`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from harness.bus.store import harness_state_dir

DB_NAME = "ledger.db"
_SQL_TYPES = {"int": "INTEGER", "float": "REAL"}


def default_db_path() -> Path:
    return harness_state_dir() / DB_NAME


class Row(BaseModel):
    """One row of one table. The fields are the columns; a dict field is stored as JSON text."""

    table: ClassVar[str]

    id: int = 0  # assigned by the store


def _columns(row_type: type[Row]) -> list[str]:
    return [name for name in row_type.model_fields if name != "id"]


def _json_fields(row_type: type[Row]) -> list[str]:
    return [name for name, f in row_type.model_fields.items() if "dict" in str(f.annotation)]


class Store:
    def __init__(self, db_path: Path, export_path: Path, row_types: Sequence[type[Row]]) -> None:
        self.db_path = db_path
        self.export_path = export_path
        self.row_types = tuple(row_types)
        self._by_table = {row_type.table: row_type for row_type in self.row_types}

    @contextmanager
    def _connect(self, *, restore: bool = True) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            self._create(conn)
            if restore and self.export_path.exists() and self._is_empty(conn):
                self._load_export(conn)
                conn.commit()
            yield conn

    def _create(self, conn: sqlite3.Connection) -> None:
        for row_type in self.row_types:
            defs = ["id INTEGER PRIMARY KEY"]
            for name in _columns(row_type):
                annotation = str(row_type.model_fields[name].annotation)
                kind = next((sql for py, sql in _SQL_TYPES.items() if py in annotation), "TEXT")
                defs.append(f"{name} {kind}")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {row_type.table} ({', '.join(defs)})")

    def _is_empty(self, conn: sqlite3.Connection) -> bool:
        return not any(
            conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in self._by_table  # noqa: S608
        )

    # ---- writing ----

    def add(self, *rows: Row) -> None:
        """Append rows, then rewrite the export so it matches the database."""
        if not rows:
            return
        with self._connect() as conn, conn:
            for row in rows:
                self._insert(conn, row)
        self.export()

    def update(self, row: Row) -> None:
        """Replace the stored row that has `row.id`, then rewrite the export."""
        names = _columns(type(row))
        sets = ", ".join(f"{name} = ?" for name in names)
        with self._connect() as conn, conn:
            conn.execute(f"UPDATE {row.table} SET {sets} WHERE id = ?", [*self._values(row, names), row.id])  # noqa: S608
        self.export()

    @staticmethod
    def _values(row: Row, names: list[str]) -> list[Any]:
        return [
            json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
            for value in (getattr(row, name) for name in names)
        ]

    def _insert(self, conn: sqlite3.Connection, row: Row, *, keep_id: bool = False) -> None:
        names = _columns(type(row)) + (["id"] if keep_id else [])
        marks = ", ".join("?" for _ in names)
        conn.execute(
            f"INSERT INTO {row.table} ({', '.join(names)}) VALUES ({marks})",  # noqa: S608
            self._values(row, names),
        )

    def export(self) -> None:
        """Write every row as one JSON line (`{"table": …, …}`), tables in a fixed order, oldest first."""
        lines = [
            json.dumps({"table": row.table, **row.model_dump()}, ensure_ascii=False)
            for row_type in self.row_types
            for row in self.all(row_type)
        ]
        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.export_path.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        tmp.replace(self.export_path)

    def rebuild(self) -> int:
        """Replace this store's tables with the export's contents. Returns the number of rows loaded.
        Other stores' tables in the same database are left alone."""
        with self._connect(restore=False) as conn, conn:
            for table in self._by_table:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
            return self._load_export(conn)

    def _load_export(self, conn: sqlite3.Connection) -> int:
        count = 0
        for line in self.export_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                self._insert(conn, self._by_table[record.pop("table")].model_validate(record), keep_id=True)
                count += 1
        return count

    # ---- reading ----

    def all[R: Row](self, row_type: type[R]) -> list[R]:
        """Every row of one table, oldest first."""
        with self._connect() as conn:
            found = conn.execute(f"SELECT * FROM {row_type.table} ORDER BY id").fetchall()  # noqa: S608
        json_fields = _json_fields(row_type)
        rows = []
        for record in found:
            values = dict(record)
            for name in json_fields:
                values[name] = json.loads(values[name] or "{}")
            rows.append(row_type.model_validate(values))
        return rows
