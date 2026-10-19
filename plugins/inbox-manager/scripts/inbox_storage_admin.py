#!/usr/bin/env python3
"""Offline health, backup, and safe restore for an Inbox Manager database.

The database is the portable unit: schema-2 engine records, knowledge tables,
FTS indexes, and any compatible future tables remain together.  Operations use
SQLite itself and never materialize case or knowledge history as JSON.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any

import inbox_engine


DB = inbox_engine.DB


def _mailbox(value: Any) -> str:
    return inbox_engine.addresses([value])[0]


def _existing_database(home: str | Path) -> tuple[Path, Path]:
    raw = Path(home).expanduser()
    if raw.is_symlink():
        raise ValueError("inbox home must not be a symlink")
    root = raw.resolve()
    path = root / DB
    if path.is_symlink():
        raise ValueError("state database must not be a symlink")
    if not path.is_file():
        raise ValueError("inbox engine not initialized; run setup")
    return root, path


def _backup_path(value: Any, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError(field + " is required")
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError(field + " must not be a symlink")
    return raw.resolve()


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else path,
                         uri=readonly, timeout=15, isolation_level=None)
    if readonly:
        db.execute("PRAGMA query_only=ON")
    return db


def _header(db: sqlite3.Connection) -> dict[str, Any]:
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='engine'").fetchone()
    if not table:
        raise ValueError("backup does not contain an Inbox Manager engine")
    valid = db.execute("SELECT json_valid(data) FROM engine WHERE id=1").fetchone()
    if not valid:
        raise ValueError("backup has no Inbox Manager header")
    if valid[0] != 1:
        raise ValueError("Inbox Manager header is invalid")
    # Extract only fixed header scalars. A legacy schema-1 engine row may also
    # contain years of JSON history, which must not enter the Python process.
    row = db.execute("""SELECT json_extract(data,'$.mailbox'),
        json_extract(data,'$.schema'), json_extract(data,'$.storage_version')
        FROM engine WHERE id=1""").fetchone()
    return {"mailbox": row[0], "schema": row[1], "storage_version": row[2]}


def _prove_mailbox(db: sqlite3.Connection, expected: str) -> dict[str, Any]:
    header = _header(db)
    actual = _mailbox(header.get("mailbox"))
    if actual != expected:
        raise ValueError("database belongs to another mailbox")
    return header


def _integrity(db: sqlite3.Connection) -> None:
    rows = [row[0] for row in db.execute("PRAGMA integrity_check(20)")]
    if rows != ["ok"]:
        raise ValueError("SQLite integrity check failed: " + "; ".join(map(str, rows[:10])))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    names = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: int(db.execute("SELECT count(*) FROM " + _quote_identifier(name)).fetchone()[0])
            for name in names}


def _health_path(path: Path, mailbox: str) -> dict[str, Any]:
    with _connect(path, readonly=True) as db:
        header = _prove_mailbox(db, mailbox)
        _integrity(db)
        counts = _counts(db)
        journal_mode = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
    wal = Path(str(path) + "-wal")
    return {
        "status": "ok",
        "mailbox": mailbox,
        "schema": header.get("schema"),
        "storage_version": header.get("storage_version") or 1,
        "journal_mode": journal_mode,
        "table_counts": counts,
        "page_size": page_size,
        "page_count": page_count,
        "database_bytes": path.stat().st_size,
        "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
    }


def health(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    mailbox = _mailbox(payload.get("mailbox"))
    _, path = _existing_database(home)
    return _health_path(path, mailbox)


def _reserve(path: Path) -> None:
    if not path.parent.is_dir():
        raise ValueError("backup parent directory does not exist")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("destination already exists; refusing to overwrite") from None
    os.close(fd)


def _sqlite_copy(source_path: Path, destination_path: Path) -> None:
    with _connect(source_path, readonly=True) as source, _connect(destination_path) as destination:
        source.backup(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    mailbox = _mailbox(payload.get("mailbox"))
    _, source_path = _existing_database(home)
    target = _backup_path(payload.get("target_path"), "target_path")
    _reserve(target)
    try:
        with _connect(source_path, readonly=True) as source:
            _prove_mailbox(source, mailbox)
            with _connect(target) as destination:
                source.backup(destination)
        os.chmod(target, 0o600)
        verified = _health_path(target, mailbox)
        return {"status": "created", "mailbox": mailbox,
                "backup_path": str(target), "sha256": _sha256(target),
                "database_bytes": verified["database_bytes"],
                "storage_version": verified["storage_version"],
                "table_counts": verified["table_counts"]}
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _empty_restore_home(home: str | Path) -> tuple[Path, bool]:
    raw = Path(home).expanduser()
    if raw.is_symlink():
        raise ValueError("restore home must not be a symlink")
    root = raw.resolve()
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise ValueError("restore requires a new empty inbox home")
        return root, False
    if not root.parent.is_dir():
        raise ValueError("restore parent directory does not exist")
    root.mkdir(mode=0o700)
    return root, True


def restore(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    mailbox = _mailbox(payload.get("mailbox"))
    source = _backup_path(payload.get("backup_path"), "backup_path")
    if source.is_symlink() or not source.is_file():
        raise ValueError("backup_path must identify a regular backup file")
    with _connect(source, readonly=True) as backup_db:
        _prove_mailbox(backup_db, mailbox)
        _integrity(backup_db)
    expected_hash = payload.get("sha256")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or expected_hash != _sha256(source):
            raise ValueError("backup hash does not match")

    root, created_root = _empty_restore_home(home)
    destination = root / DB
    reserved = False
    try:
        _reserve(destination)
        reserved = True
        if any(entry != destination for entry in root.iterdir()):
            raise ValueError("restore home changed and is no longer empty")
        _sqlite_copy(source, destination)
        os.chmod(destination, 0o600)
        verified = _health_path(destination, mailbox)
        return {"status": "restored", "mailbox": mailbox, "home": str(root),
                "database_path": str(destination), "sha256": _sha256(destination),
                "database_bytes": verified["database_bytes"],
                "storage_version": verified["storage_version"],
                "table_counts": verified["table_counts"]}
    except BaseException:
        if reserved:
            destination.unlink(missing_ok=True)
        if created_root:
            try:
                root.rmdir()
            except OSError:
                pass
        raise


def execute(home: str | Path, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    value = payload or {}
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    if command == "storage-health":
        return health(home, value)
    if command == "storage-backup":
        return backup(home, value)
    if command == "storage-restore":
        return restore(home, value)
    raise ValueError("unknown storage command")
