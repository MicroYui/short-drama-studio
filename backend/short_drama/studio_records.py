"""SQL storage for Studio lifecycle records, separate from creative content."""

from __future__ import annotations

import base64
from collections.abc import Sequence
import json
import sqlite3
from typing import Literal, cast

from pydantic import JsonValue

from short_drama.models import ProjectDocument


RecordKind = Literal["studio_exports", "studio_changes"]
RECORD_FIELDS: tuple[RecordKind, ...] = ("studio_exports", "studio_changes")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def write_record(connection: sqlite3.Connection, kind: RecordKind, project_id: str,
                 value: dict[str, JsonValue], *, replace: bool = False, legacy_id: str | None = None) -> None:
    identity = _text(value.get("id")) or legacy_id
    if not identity:
        raise ValueError("Studio record needs an id")
    created_at = _text(value.get("created_at"))
    updated_at = _text(value.get("updated_at")) or created_at
    status = _text(value.get("status"))
    if kind == "studio_changes":
        raw_items = value.get("items")
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        status = "pending" if any(item.get("status", "pending") == "pending" for item in items) else "resolved"
        updated_at = max([created_at, *(_text(item.get("decided_at")) for item in items)])
    conflict = ("DO UPDATE SET created_at=excluded.created_at, updated_at=excluded.updated_at, "
                "status=excluded.status, run_id=excluded.run_id, record_json=excluded.record_json") if replace else "DO NOTHING"
    connection.execute(f"INSERT INTO {kind}(project_id, id, created_at, updated_at, status, run_id, record_json) "
        f"VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, id) {conflict}",
        (project_id, identity, created_at, updated_at, status, value.get("run_id"), json.dumps(value, ensure_ascii=False)))


def import_document_records(connection: sqlite3.Connection, document: ProjectDocument) -> None:
    """Only introduce unknown history. A stale content snapshot cannot update it."""
    for kind in RECORD_FIELDS:
        for index, value in enumerate(getattr(document, kind)):
            write_record(connection, kind, document.project.id, value, legacy_id=f"legacy-{index}")


def content_json(document: ProjectDocument) -> str:
    return document.model_dump_json(exclude=set(RECORD_FIELDS))


def cursor_for_record(value: dict[str, JsonValue]) -> str:
    data = json.dumps([_text(value.get("created_at")), _text(value.get("id"))], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_record_cursor(value: str) -> tuple[str, str]:
    try:
        decoded: object = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("记录分页位置无效，请重新读取列表。") from exc
    if not isinstance(decoded, list):
        raise ValueError("记录分页位置无效，请重新读取列表。")
    parts = cast(list[object], decoded)
    if len(parts) != 2 or not all(isinstance(item, str) for item in parts):
        raise ValueError("记录分页位置无效，请重新读取列表。")
    return cast(tuple[str, str], tuple(parts))


def read_records(connection: sqlite3.Connection, kind: RecordKind, project_id: str | None, *,
                 limit: int | None = None, before: str | None = None, statuses: Sequence[str] | None = None,
                 run_id: str | None = None, descending: bool = True) -> list[dict[str, JsonValue]]:
    clauses: list[str] = []
    parameters: list[str | int] = []
    if project_id is not None:
        if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise KeyError(project_id)
        clauses.append("project_id=?")
        parameters.append(project_id)
    if statuses is not None:
        if not statuses:
            return []
        clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
        parameters.extend(statuses)
    if run_id is not None:
        clauses.append("run_id=?")
        parameters.append(run_id)
    if before is not None:
        clauses.append("(created_at, id) < (?, ?)")
        parameters.extend(decode_record_cursor(before))
    query = f"SELECT record_json FROM {kind}"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    order = "DESC" if descending else "ASC"
    query += f" ORDER BY created_at {order}, id {order}"
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(max(1, limit))
    return [cast(dict[str, JsonValue], json.loads(row["record_json"])) for row in connection.execute(query, parameters)]


def hydrate_records(connection: sqlite3.Connection, document: ProjectDocument) -> None:
    for kind in RECORD_FIELDS:
        setattr(document, kind, read_records(connection, kind, document.project.id, descending=False))


def migrate_records(connection: sqlite3.Connection) -> None:
    for kind in RECORD_FIELDS:
        connection.execute(f"""CREATE TABLE IF NOT EXISTS {kind} (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            status TEXT NOT NULL, run_id TEXT, record_json TEXT NOT NULL,
            PRIMARY KEY(project_id, id))""")
        connection.execute(f"CREATE INDEX IF NOT EXISTS {kind}_project_created ON {kind}(project_id, created_at DESC, id DESC)")
        connection.execute(f"CREATE INDEX IF NOT EXISTS {kind}_project_status ON {kind}(project_id, status, created_at DESC, id DESC)")
        connection.execute(f"CREATE INDEX IF NOT EXISTS {kind}_status ON {kind}(status, created_at DESC, id DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS studio_changes_project_run_status ON studio_changes(project_id, run_id, status, created_at DESC, id DESC)")
    if connection.execute("SELECT 1 FROM schema_migrations WHERE version=12").fetchone():
        return
    # Preserve original JSON values and timestamps; this migration is not a
    # content edit and must not emit content.updated or restart old work.
    for row in connection.execute("SELECT id, document_json FROM projects"):
        payload = cast(dict[str, JsonValue], json.loads(row["document_json"]))
        changed = False
        for kind in RECORD_FIELDS:
            values = payload.pop(kind, None)
            if values is not None:
                changed = True
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, dict):
                        write_record(connection, kind, row["id"], value, legacy_id=f"legacy-{index}")
        if changed:
            connection.execute("UPDATE projects SET document_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), row["id"]))
    connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(12, datetime('now'))")
