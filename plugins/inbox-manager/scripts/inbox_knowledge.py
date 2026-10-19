#!/usr/bin/env python3
"""Bounded, authenticated knowledge storage for Inbox Manager.

Knowledge shares ``inbox_engine.DB`` but lives in indexed relational tables so
ordinary retrieval never loads the engine JSON row.  Writes accept provenance
only from a saved authenticated owner event or a saved, learnable provider
observation.  The caller/model cannot manufacture either provenance route.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Mapping

import inbox_engine


DB = inbox_engine.DB
SCHEMA = 1
BODY_RETENTION_DAYS = 90
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
MAX_OFFSET = 1_000_000
MAX_SUMMARY = 4000
MAX_BODY = 200_000
MAX_SEARCH_PAGE_CHARS = 32_000
ACTIVE_STATUSES = {"active", "proposed"}
VISIBLE_STATUSES = {"active", "proposed", "conflicted"}
KINDS = {"fact", "procedure", "style", "example", "case_note", "business_rule"}
STATUSES = {"active", "proposed", "conflicted", "superseded", "paused", "undone",
            "invalidated", "forgotten", "outdated"}
WORDS = re.compile(r"[\w@.+-]+", re.UNICODE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _iso(value: Any, field: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError(field + " must include timezone") from None


def _text(value: Any, field: str, maximum: int = MAX_SUMMARY) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid " + field)
    return value.strip()


def _bounded_int(value: Any, default: int, maximum: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError("invalid " + field)
    return value


def _json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(field + " must be JSON-compatible") from None


def _contains_body_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in {"body", "final_body", "original_body", "source_excerpt", "raw_content"}:
                return True
            if _contains_body_field(child):
                return True
    elif isinstance(value, list):
        return any(_contains_body_field(child) for child in value)
    return False


def _normal(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = _json(value, "value")
    return " ".join(value.casefold().split())


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value, "value").encode()).hexdigest()


def _root(home: str | Path) -> tuple[Path, Path]:
    root = Path(home).expanduser().resolve()
    path = root / DB
    if not path.is_file():
        raise ValueError("inbox engine not initialized; run setup")
    if path.is_symlink():
        raise ValueError("state database must not be a symlink")
    return root, path


def _load_engine(home: str | Path) -> tuple[dict[str, Any], int]:
    """Copy the lazy engine view without materialising growing collections."""

    with inbox_engine.transaction(home, readonly=True) as (_, box):
        if box[0] is None:
            raise ValueError("inbox engine not initialized")
        state = copy.deepcopy(box[0])
        revision = state.get("engine_revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("invalid engine revision")
    return state, revision


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else path,
                         uri=readonly, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    if readonly:
        db.execute("PRAGMA query_only=ON")
    return db


def _provider_learning_invalidated(db: sqlite3.Connection) -> bool:
    """Read only the small engine header; schema-2 collections stay unloaded."""

    row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
    if not row:
        return False
    header = json.loads(row[0])
    return (header.get("config") or {}).get("learning", {}).get("invalidated") is True


@contextmanager
def _write(home: str | Path, state_revision: int | None = None):
    _, path = _root(home)
    db = _connect(path)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if state_revision is not None:
            row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
            current_revision = json.loads(row[0]).get("engine_revision", 0) if row else None
            if current_revision != state_revision:
                raise ValueError("engine state changed; retry knowledge write")
        _ensure_schema(db)
        yield db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _ensure_schema(db: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS knowledge_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS knowledge_notes (
            id TEXT PRIMARY KEY,
            mailbox TEXT NOT NULL,
            kind TEXT NOT NULL,
            topic TEXT,
            summary TEXT NOT NULL,
            normalized_summary TEXT NOT NULL,
            aliases TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            scope_job TEXT,
            scope_request TEXT,
            scope_risk TEXT,
            case_id TEXT,
            status TEXT NOT NULL,
            prior_status TEXT,
            authoritative INTEGER NOT NULL DEFAULT 0 CHECK(authoritative=0),
            version INTEGER NOT NULL,
            origin_type TEXT NOT NULL,
            origin_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            fresh_until TEXT,
            superseded_by TEXT,
            conflict_group TEXT,
            details TEXT NOT NULL,
            dedup_key TEXT NOT NULL UNIQUE
        )""",
        """CREATE INDEX IF NOT EXISTS knowledge_notes_retrieval
            ON knowledge_notes(mailbox, status, case_id, scope_job, scope_request)""",
        """CREATE INDEX IF NOT EXISTS knowledge_notes_topic
            ON knowledge_notes(mailbox, kind, topic, scope_key, status)""",
        """CREATE TABLE IF NOT EXISTS knowledge_evidence (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL REFERENCES knowledge_notes(id),
            origin_type TEXT NOT NULL,
            origin_id TEXT NOT NULL,
            source_case_id TEXT,
            observed_at TEXT NOT NULL,
            reliable INTEGER NOT NULL,
            excluded INTEGER NOT NULL DEFAULT 0,
            body TEXT,
            body_hash TEXT,
            body_expires_at TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(note_id, origin_type, origin_id)
        )""",
        """CREATE INDEX IF NOT EXISTS knowledge_evidence_note
            ON knowledge_evidence(note_id, excluded, observed_at)""",
        """CREATE INDEX IF NOT EXISTS knowledge_evidence_origin
            ON knowledge_evidence(origin_type, origin_id)""",
    )
    for statement in statements:
        db.execute(statement)
    evidence_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_evidence)")}
    if "source_case_id" not in evidence_columns:
        db.execute("ALTER TABLE knowledge_evidence ADD COLUMN source_case_id TEXT")
        try:
            db.execute("""UPDATE knowledge_evidence
                SET source_case_id=json_extract(metadata,'$.case_id')
                WHERE source_case_id IS NULL AND json_valid(metadata)""")
        except sqlite3.OperationalError:
            pass
    db.execute("CREATE INDEX IF NOT EXISTS knowledge_evidence_source_case ON knowledge_evidence(source_case_id,note_id)")
    db.execute("INSERT OR IGNORE INTO knowledge_meta VALUES ('schema', ?)", (str(SCHEMA),))
    if db.execute("SELECT value FROM knowledge_meta WHERE key='schema'").fetchone()[0] != str(SCHEMA):
        raise ValueError("unsupported knowledge schema")
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(note_id UNINDEXED, summary, aliases, scope)")
        db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('fts5', '1')")
    except sqlite3.OperationalError:
        db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('fts5', '0')")


def _scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 20:
        raise ValueError("knowledge scope must be a bounded mapping")
    result = json.loads(_json(dict(value), "knowledge scope"))
    if len(_json(result, "knowledge scope")) > 8000:
        raise ValueError("knowledge scope is too large")
    if any(not isinstance(k, str) or not k.strip() for k in result):
        raise ValueError("knowledge scope keys must be nonempty strings")
    return result


def _aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > 30 or
            any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in value)):
        raise ValueError("aliases must be a bounded string list")
    return sorted(set(v.strip() for v in value), key=str.casefold)


def _field(scope: Mapping[str, Any], key: str) -> str | None:
    value = scope.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        value = " | ".join(str(v) for v in value)
    return _normal(value) or None


def _origin_from_state(state: Mapping[str, Any], origin: Any) -> dict[str, Any]:
    if not isinstance(origin, Mapping) or origin.get("type") not in {"owner_event", "provider_observation"}:
        raise ValueError("saved owner event or reliable provider observation required")
    origin_type = origin["type"]
    origin_id = _text(origin.get("id"), "origin id", 500)
    if origin_type == "owner_event":
        event = state.get("owner_events", {}).get(origin_id)
        if not isinstance(event, Mapping) or not event.get("approval_ref"):
            raise ValueError("unknown authenticated owner event")
        observed_at = event.get("at")
        body = event.get("answer")
        metadata = {"approval_ref": event["approval_ref"], "decision_id": event.get("decision_id"),
                    "case_id": event.get("source_case_id") or event.get("case_id"),
                    "scope": event.get("scope")}
    else:
        saved = state.get("observations", {}).get(origin_id)
        if not isinstance(saved, Mapping) or saved.get("result", {}).get("learnable") is not True:
            raise ValueError("unknown or ineligible provider observation")
        result = saved["result"]
        if result.get("signal") not in {"accepted", "edited", "supported_discard"}:
            raise ValueError("provider observation is not reliable learning evidence")
        observation = saved.get("observation", {})
        observed_at = observation.get("observed_at") or saved.get("at")
        body = result.get("final_body") or observation.get("discard_reason")
        source_case_id = saved.get("case_id")
        if not source_case_id and saved.get("candidate_id"):
            source_case_id = state.get("drafts", {}).get(saved["candidate_id"], {}).get("case_id")
        metadata = {"candidate_id": saved.get("candidate_id"), "signal": result.get("signal"),
                    "actor": observation.get("actor"), "actor_evidence": observation.get("actor_evidence"),
                    "case_id": source_case_id}
    _iso(observed_at, "origin observed_at")
    return {"type": origin_type, "id": origin_id, "observed_at": observed_at,
            "body": body if isinstance(body, str) else None, "metadata": metadata}


def save_authenticated_owner_event(state: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Save a general knowledge event inside an engine write transaction.

    This helper deliberately calls :func:`inbox_engine.owned`; parent engine
    integration can therefore create a provenance record from the current
    authenticated host message without accepting a caller-provided origin.
    """

    approval_ref = inbox_engine.owned(dict(payload))
    event_id = _text(payload.get("event_id"), "authenticated host event id", 500)
    answer = _text(payload.get("answer"), "owner answer", 10_000)
    identity = {"event_id": event_id, "answer": answer, "approval_ref": approval_ref,
                "case_id": payload.get("case_id"), "decision_id": payload.get("decision_id"),
                "source_case_id": payload.get("source_case_id") or payload.get("case_id"),
                "scope": payload.get("scope", "knowledge")}
    input_hash = _hash(identity)
    prior = state.setdefault("owner_events", {}).get(event_id)
    if prior:
        legacy_identity = {key: value for key, value in identity.items() if key != "source_case_id"}
        if prior.get("input_hash") not in {input_hash, _hash(legacy_identity)}:
            raise ValueError("owner event id reused with different input")
        return {**prior, "idempotent": True}
    result = {**identity, "input_hash": input_hash, "at": stamp()}
    state["owner_events"][event_id] = result
    return {**result, "idempotent": False}


def record_provider_observation(state: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Authenticate one owner-sent message from a fresh complete thread read.

    This is the no-candidate feedback route.  It records an eligible observation
    in engine state; semantic note creation remains a separate knowledge-record
    call after the engine transaction commits.
    """

    import inbox_snapshot

    if not isinstance(payload, Mapping):
        raise ValueError("provider observation payload required")
    packet = payload.get("packet")
    snapshot = inbox_snapshot.build_snapshot(packet)
    if packet.get("mailbox", "").casefold() != str(state.get("mailbox", "")).casefold():
        raise ValueError("provider observation mailbox mismatch")
    case_id = _text(payload.get("case_id"), "case_id", 500)
    case = state.get("cases", {}).get(case_id)
    if not isinstance(case, Mapping):
        raise ValueError("unknown provider observation case")
    if case.get("thread_id") != snapshot["thread_id"]:
        raise ValueError("provider observation case/thread mismatch")
    if case.get("learning_excluded") is True:
        raise ValueError("learning excluded for this case")
    maximum_age = (state.get("config") or {}).get("transport", {}).get("snapshot_max_age_seconds", 300)
    if not inbox_engine.fresh(snapshot["observed_at"], maximum_age):
        raise ValueError("provider observation must be fresh")
    learning = (state.get("config") or {}).get("learning", {})
    if learning.get("enabled") is not True:
        raise ValueError("learning paused")
    if learning.get("invalidated") is True:
        raise ValueError("learning capability invalidated")
    actor_mode = learning.get("actor_mode")
    actor = payload.get("actor")
    evidence = payload.get("actor_evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        raise ValueError("reliable owner actor evidence required")
    if actor_mode == "verified":
        source = evidence.get("source")
        if actor != "member" or not (evidence.get("verified") is True or
                                     isinstance(source, str) and source.strip() and
                                     source != "exclusive_editor_assumption"):
            raise ValueError("verified owner actor evidence required")
    elif actor_mode == "exclusive_editor":
        if (learning.get("exclusive_editor_confirmed") is not True or actor not in {"member", "unknown"}):
            raise ValueError("confirmed exclusive-editor evidence required")
    else:
        raise ValueError("learning actor capability does not permit attribution")
    message_id = _text(payload.get("message_id"), "sent message id", 1000)
    messages = [message for message in packet["messages"] if message.get("id") == message_id]
    if len(messages) != 1:
        raise ValueError("sent message is absent or ambiguous in provider packet")
    message = messages[0]
    if message.get("from", "").casefold() != state["mailbox"].casefold():
        raise ValueError("sent message is not from the owner mailbox")
    from inbox_snapshot import label_ids
    message_labels = label_ids(message) or []
    if not isinstance(message_labels, list) or "SENT" not in {str(label).upper() for label in message_labels}:
        raise ValueError("owner message lacks provider SENT evidence")
    actions = state.get("actions", {})
    if hasattr(actions, "select"):
        by_id = {item.get("id"): item for item in actions.select(case_id=case_id)}
        by_id.update({item.get("id"): item for item in actions.select(thread_id=snapshot["thread_id"])})
        relevant_actions = by_id.values()
    else:
        relevant_actions = (item for item in actions.values()
                            if item.get("case_id") == case_id or item.get("thread_id") == snapshot["thread_id"])
    if any(item.get("message_id") == message_id for item in relevant_actions):
        raise ValueError("bot-sent message is not independent owner feedback")
    if message_id in state.get("member_sends", {}):
        raise ValueError("sent message already attributed to a prepared candidate")
    observation_id = "owner_sent_" + hashlib.sha256(
        (state["mailbox"] + "\0" + snapshot["thread_id"] + "\0" + message_id).encode()
    ).hexdigest()[:24]
    observation = {
        "id": observation_id,
        "observed_at": snapshot["observed_at"],
        "mailbox": state["mailbox"],
        "thread_id": snapshot["thread_id"],
        "listing_complete": True,
        "status": "sent",
        "sent_message_id": message_id,
        "final_body": message["body"],
        "recipients": sorted(set(message.get("to", []) + message.get("cc", [])), key=str.casefold),
        "actor": actor,
        "actor_evidence": json.loads(_json(dict(evidence), "actor evidence")),
        "provider_fingerprint": snapshot["fingerprint"],
    }
    input_hash = _hash({"case_id": case_id, "mailbox": state["mailbox"],
                        "thread_id": snapshot["thread_id"], "message_id": message_id,
                        "from": message["from"], "to": message.get("to", []),
                        "cc": message.get("cc", []), "body": message["body"]})
    prior = state.setdefault("observations", {}).get(observation_id)
    if prior:
        if prior.get("input_hash") != input_hash:
            raise ValueError("owner-sent observation changed; use a fresh provider read")
        return {**prior, "idempotent": True}
    result = {"outcome": "sent", "learnable": True, "signal": "edited",
              "reason": "reliably_attributed_owner_sent_reply", "member_handled": True,
              "final_body": message["body"]}
    saved = {"id": observation_id, "candidate_id": None, "case_id": case_id,
             "input_hash": input_hash, "observation": observation, "result": result,
             "status": "learning_pending", "at": stamp()}
    state["observations"][observation_id] = saved
    return {**saved, "idempotent": False}


def _legacy_kind(value: Any) -> str:
    return {"factual_correction": "fact", "business_rule": "business_rule",
            "style": "style", "example": "example", "procedure": "procedure"}.get(value, "example")


def _status(value: Any, kind: str, origin_type: str) -> str:
    if value in STATUSES:
        result = value
    else:
        result = "active" if kind in {"fact", "procedure", "style", "example", "case_note"} else "proposed"
    if kind == "business_rule":
        result = "proposed" if result == "active" else result
    if origin_type == "provider_observation" and kind == "business_rule":
        result = "proposed"
    return result


def _note_values(mailbox: str, payload: Mapping[str, Any], origin: Mapping[str, Any],
                 *, legacy_id: str | None = None) -> dict[str, Any]:
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ValueError("unsupported knowledge kind")
    summary = _text(payload.get("summary"), "knowledge summary")
    scope = _scope(payload.get("scope", {}))
    aliases = _aliases(payload.get("aliases"))
    source_case_id = origin.get("metadata", {}).get("case_id")
    requested_case_id = payload.get("case_id") or scope.get("case_id")
    if source_case_id is not None:
        source_case_id = _text(source_case_id, "source case_id", 500)
    if requested_case_id is not None:
        requested_case_id = _text(requested_case_id, "case_id", 500)
    if source_case_id and requested_case_id and source_case_id != requested_case_id:
        raise ValueError("knowledge case scope differs from its provenance")
    local = payload.get("one_off") is True or kind == "case_note"
    case_id = (requested_case_id or source_case_id) if local else requested_case_id
    if payload.get("one_off") is True and not case_id:
        raise ValueError("one-off knowledge requires a case_id")
    if kind == "case_note" and not case_id:
        raise ValueError("case note requires a case_id")
    if case_id:
        scope["case_id"] = case_id
    topic = payload.get("topic")
    if topic is not None:
        topic = _normal(_text(topic, "topic", 500))
    fresh_until = payload.get("fresh_until")
    if fresh_until is not None:
        fresh_until = stamp(_iso(fresh_until, "fresh_until"))
    details = payload.get("details", {})
    if not isinstance(details, Mapping):
        raise ValueError("knowledge details must be a mapping")
    if _contains_body_field(details):
        raise ValueError("evidence bodies belong only in knowledge_evidence")
    scope_json = _json(scope, "knowledge scope")
    dedup = _hash({"mailbox": mailbox, "kind": kind, "summary": _normal(summary),
                   "scope": json.loads(scope_json)})
    note_id = legacy_id or payload.get("note_id") or "note_" + dedup[:24]
    note_id = _text(note_id, "note_id", 500)
    created_at = payload.get("created_at") or stamp()
    _iso(created_at, "created_at")
    status = _status(payload.get("status"), kind, origin["type"])
    return {"id": note_id, "mailbox": mailbox, "kind": kind, "topic": topic,
            "summary": summary, "normalized_summary": _normal(summary), "aliases": _json(aliases, "aliases"),
            "scope": scope_json, "scope_key": _hash(json.loads(scope_json)),
            "scope_job": _field(scope, "job"), "scope_request": _field(scope, "request"),
            "scope_risk": _field(scope, "risk"), "case_id": case_id, "status": status,
            "prior_status": None, "authoritative": 0, "version": int(payload.get("version", 1)),
            "origin_type": origin["type"], "origin_id": origin["id"], "created_at": created_at,
            "updated_at": payload.get("updated_at") or created_at, "fresh_until": fresh_until,
            "superseded_by": None, "conflict_group": None,
            "details": _json(dict(details), "knowledge details"), "dedup_key": dedup}


def _upsert_fts(db: sqlite3.Connection, note: Mapping[str, Any]) -> None:
    available = db.execute("SELECT value FROM knowledge_meta WHERE key='fts5'").fetchone()[0] == "1"
    if not available:
        return
    db.execute("DELETE FROM knowledge_fts WHERE note_id=?", (note["id"],))
    if note["status"] in VISIBLE_STATUSES:
        db.execute("INSERT INTO knowledge_fts(note_id, summary, aliases, scope) VALUES (?, ?, ?, ?)",
                   (note["id"], note["summary"], " ".join(json.loads(note["aliases"])), note["scope"]))


def _insert_note(db: sqlite3.Connection, note: Mapping[str, Any]) -> None:
    columns = tuple(note)
    db.execute(f"INSERT INTO knowledge_notes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
               tuple(note[key] for key in columns))
    _upsert_fts(db, note)


def _evidence_values(note_id: str, origin: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    body = origin.get("body")
    if body is not None and len(body) > MAX_BODY:
        body = body[:MAX_BODY]
    observed = _iso(origin["observed_at"], "origin observed_at")
    expires = observed + timedelta(days=BODY_RETENTION_DAYS) if body is not None else None
    evidence_id = "evidence_" + _hash({"note": note_id, "type": origin["type"], "id": origin["id"]})[:24]
    supplied_metadata = payload.get("evidence_metadata", {})
    if not isinstance(supplied_metadata, Mapping) or _contains_body_field(supplied_metadata):
        raise ValueError("evidence metadata must be body-free mapping")
    metadata = {**origin.get("metadata", {}), **dict(supplied_metadata)}
    return {"id": evidence_id, "note_id": note_id, "origin_type": origin["type"],
            "origin_id": origin["id"], "source_case_id": origin.get("metadata", {}).get("case_id"),
            "observed_at": origin["observed_at"], "reliable": 1,
            "excluded": 0, "body": body,
            "body_hash": hashlib.sha256(body.encode()).hexdigest() if body is not None else None,
            "body_expires_at": stamp(expires) if expires else None,
            "metadata": _json(metadata, "evidence metadata"), "created_at": stamp()}


def _insert_evidence(db: sqlite3.Connection, values: Mapping[str, Any]) -> bool:
    columns = tuple(values)
    cursor = db.execute(f"INSERT OR IGNORE INTO knowledge_evidence ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        tuple(values[key] for key in columns))
    return cursor.rowcount == 1


def _mark_provider_observation_learned(db: sqlite3.Connection, origin: Mapping[str, Any],
                                       note_id: str) -> None:
    """Atomically acknowledge provider feedback after its note is durable."""

    if origin["type"] != "provider_observation":
        return
    header_row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
    if not header_row:
        raise ValueError("inbox engine not initialized")
    header = json.loads(header_row[0])
    observation_id = origin["id"]
    learned_at = stamp()
    if header.get("storage_version") == 2:
        import inbox_store

        database_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
        state = inbox_store.load(db, database_path, header)
        observation = state["observations"].get(observation_id)
        if not observation:
            raise ValueError("provider observation disappeared before learning")
        status = observation.get("status")
        learned_ids = list(observation.get("learned_note_ids", []))
        if observation.get("learned_note_id") and observation["learned_note_id"] not in learned_ids:
            learned_ids.append(observation["learned_note_id"])
        if status == "learned":
            if note_id in learned_ids:
                return
        if status not in {None, "learning_pending"}:
            if status != "learned":
                raise ValueError("provider observation is no longer pending learning")
        learned_ids.append(note_id)
        observation.update(status="learned", learned_note_id=learned_ids[0],
                           learned_note_ids=sorted(set(learned_ids)), learned_at=learned_at)
        state["observations"][observation_id] = observation
        inbox_store.save(db, database_path, state)
        return
    observation = header.get("observations", {}).get(observation_id)
    if not observation:
        raise ValueError("provider observation disappeared before learning")
    status = observation.get("status")
    learned_ids = list(observation.get("learned_note_ids", []))
    if observation.get("learned_note_id") and observation["learned_note_id"] not in learned_ids:
        learned_ids.append(observation["learned_note_id"])
    if status == "learned":
        if note_id in learned_ids:
            return
    if status not in {None, "learning_pending"}:
        if status != "learned":
            raise ValueError("provider observation is no longer pending learning")
    learned_ids.append(note_id)
    observation.update(status="learned", learned_note_id=learned_ids[0],
                       learned_note_ids=sorted(set(learned_ids)), learned_at=learned_at)
    header["engine_revision"] = header.get("engine_revision", 0) + 1
    db.execute("UPDATE engine SET data=? WHERE id=1", (_json(header, "engine state"),))


def _row_note(row: sqlite3.Row, dependency: bool = True) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "kind", "topic", "summary", "status", "version",
                                        "created_at", "updated_at", "fresh_until", "case_id",
                                        "superseded_by", "conflict_group")}
    result["aliases"] = json.loads(row["aliases"])
    result["scope"] = json.loads(row["scope"])
    result["authoritative"] = False
    if dependency:
        result["dependency"] = {"note_id": row["id"], "version": row["version"],
                                "updated_at": row["updated_at"]}
    return result


def _set_status(db: sqlite3.Connection, note_id: str, status: str, **fields: Any) -> None:
    row = db.execute("SELECT * FROM knowledge_notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        raise ValueError("unknown knowledge note")
    updates = {"status": status, "prior_status": row["status"], "version": row["version"] + 1,
               "updated_at": stamp(), **fields}
    db.execute("UPDATE knowledge_notes SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
               (*updates.values(), note_id))
    _upsert_fts(db, db.execute("SELECT * FROM knowledge_notes WHERE id=?", (note_id,)).fetchone())


def migrate_legacy(home: str | Path) -> dict[str, Any]:
    """Move legacy ``state.lessons`` once, preserving every proposal/status."""

    state, state_revision = _load_engine(home)
    lessons = state.get("lessons", {})
    if not lessons:
        with _write(home, state_revision) as db:
            db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('legacy_migrated', '1')")
        return {"migrated": 0}
    migrated = 0
    with _write(home, state_revision) as db:
        for legacy_id, lesson in lessons.items():
            origin_id = lesson.get("observation_id") or (lesson.get("supporting_observations") or [legacy_id])[0]
            origin = {"type": "legacy_lesson", "id": str(origin_id),
                      "observed_at": lesson.get("created_at") or lesson.get("evidence", {}).get("observed_at") or stamp(),
                      "body": lesson.get("example", {}).get("body"),
                      "metadata": {"legacy_lesson_id": legacy_id, "evidence": lesson.get("evidence", {})}}
            legacy_details = {key: lesson[key] for key in ("signal", "versions", "source_versions",
                              "profile_version", "authoritative") if key in lesson}
            legacy_details["legacy_lesson_id"] = legacy_id
            if lesson.get("example", {}).get("polarity"):
                legacy_details["example_polarity"] = lesson["example"]["polarity"]
            payload = {"kind": _legacy_kind(lesson.get("kind")), "summary": lesson.get("summary"),
                       "scope": lesson.get("scope", {}), "status": lesson.get("status"),
                       "version": lesson.get("revision", 1), "created_at": lesson.get("created_at") or stamp(),
                       "details": legacy_details}
            note = _note_values(state["mailbox"], payload, origin, legacy_id=legacy_id)
            existing = db.execute("SELECT * FROM knowledge_notes WHERE dedup_key=?", (note["dedup_key"],)).fetchone()
            target = existing["id"] if existing else note["id"]
            if not existing:
                _insert_note(db, note)
                migrated += 1
            _insert_evidence(db, _evidence_values(target, origin, {}))
        # The relational table is authoritative after this same-file transaction.
        current = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
        current_state = json.loads(current[0])
        if current_state.get("engine_revision", 0) != state_revision:
            raise ValueError("engine state changed; retry legacy migration")
        if current_state.get("storage_version") == 2:
            db.execute("DELETE FROM im_lessons")
        else:
            current_state["lessons"] = {}
        current_state["knowledge_schema"] = SCHEMA
        current_state["engine_revision"] = state_revision + 1
        db.execute("UPDATE engine SET data=? WHERE id=1", (_json(current_state, "engine state"),))
        db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('legacy_migrated', '1')")
    return {"migrated": migrated}


def record(home: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    state, state_revision = _load_engine(home)
    if state.get("lessons"):
        migrate_legacy(home)
        state, state_revision = _load_engine(home)
    if (state.get("config") or {}).get("learning", {}).get("enabled") is False:
        raise ValueError("learning paused")
    origin = _origin_from_state(state, payload.get("origin"))
    source_case_id = origin.get("metadata", {}).get("case_id")
    if origin["type"] == "provider_observation" and source_case_id:
        source_case = state.get("cases", {}).get(source_case_id)
        if source_case and source_case.get("learning_excluded") is True:
            raise ValueError("learning excluded for this case")
    if origin["type"] == "provider_observation" and \
            (state.get("config") or {}).get("learning", {}).get("invalidated") is True:
        raise ValueError("learning capability invalidated")
    note = _note_values(state["mailbox"], payload, origin)
    supersedes = payload.get("supersedes", [])
    contradicts = payload.get("contradicts", [])
    if not isinstance(supersedes, list) or not isinstance(contradicts, list) or len(supersedes) > 20 or len(contradicts) > 20:
        raise ValueError("knowledge relationships must be bounded lists")
    with _write(home, state_revision) as db:
        paused = db.execute("SELECT value FROM knowledge_meta WHERE key='paused'").fetchone()
        if paused and paused[0] == "1":
            raise ValueError("learning paused")
        existing = db.execute("SELECT * FROM knowledge_notes WHERE dedup_key=?", (note["dedup_key"],)).fetchone()
        if existing:
            added = _insert_evidence(db, _evidence_values(existing["id"], origin, payload))
            _mark_provider_observation_learned(db, origin, existing["id"])
            return {**_row_note(existing), "idempotent": not added, "deduplicated": True}

        # Same scoped topic with incompatible content is withheld until resolved.
        conflicts = []
        if note["topic"] and note["kind"] in {"fact", "procedure"}:
            conflicts = db.execute("""SELECT * FROM knowledge_notes
                WHERE mailbox=? AND kind=? AND topic=? AND scope_key=? AND status IN ('active','proposed','conflicted')""",
                (note["mailbox"], note["kind"], note["topic"], note["scope_key"])).fetchall()
        related = set(supersedes) | set(contradicts)
        if conflicts and not all(row["id"] in related for row in conflicts):
            group = "conflict_" + _hash({"topic": note["topic"], "scope": note["scope_key"]})[:20]
            note["status"] = "conflicted"
            note["conflict_group"] = group
            for row in conflicts:
                _set_status(db, row["id"], "conflicted", conflict_group=group)

        _insert_note(db, note)
        _insert_evidence(db, _evidence_values(note["id"], origin, payload))
        for prior_id in supersedes:
            prior = db.execute("SELECT * FROM knowledge_notes WHERE id=?", (prior_id,)).fetchone()
            if not prior or prior["scope_key"] != note["scope_key"] or prior["kind"] != note["kind"]:
                raise ValueError("superseded note must exist in the same kind and scope")
            _set_status(db, prior_id, "superseded", superseded_by=note["id"])
        if contradicts:
            group = note["conflict_group"] or "conflict_" + _hash(sorted(contradicts + [note["id"]]))[:20]
            _set_status(db, note["id"], "conflicted", conflict_group=group)
            for prior_id in contradicts:
                _set_status(db, prior_id, "conflicted", conflict_group=group)
        final = db.execute("SELECT * FROM knowledge_notes WHERE id=?", (note["id"],)).fetchone()
        _mark_provider_observation_learned(db, origin, final["id"])
        return {**_row_note(final), "idempotent": False, "deduplicated": False}


def _query_terms(query: str) -> str:
    words = [word for word in WORDS.findall(query.casefold()) if len(word) > 1][:24]
    if not words:
        raise ValueError("knowledge search needs searchable words")
    return " OR ".join('"' + word.replace('"', '""') + '"' for word in words)


def _usable_evidence_predicate(db: sqlite3.Connection, *, provider_invalidated: bool) -> str:
    conditions = ["ke.excluded=0", "ke.reliable=1"]
    if provider_invalidated:
        conditions.append("ke.origin_type!='provider_observation'")
    elif "source_case_id" in {row[1] for row in db.execute("PRAGMA table_info(knowledge_evidence)")}:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='im_cases'").fetchone():
            excluded = """SELECT 1 FROM im_cases ic WHERE ic.id=ke.source_case_id
                           AND json_extract(ic.data,'$.learning_excluded')=1"""
        else:
            # Schema 1 has no indexed collections; keep upgrade compatibility
            # without returning the engine JSON to the caller/model.
            excluded = """SELECT 1 FROM json_each(json_extract(
                           (SELECT data FROM engine WHERE id=1),'$.cases')) ic
                           WHERE ic.key=ke.source_case_id
                           AND json_extract(ic.value,'$.learning_excluded')=1"""
        conditions.append("(ke.origin_type!='provider_observation' OR ke.source_case_id IS NULL OR "
                          "NOT EXISTS (" + excluded + "))")
    return " AND ".join(conditions)


def _note_has_usable_evidence(db: sqlite3.Connection, note_id: str,
                              provider_invalidated: bool = False) -> bool:
    predicate = _usable_evidence_predicate(db, provider_invalidated=provider_invalidated)
    return db.execute("SELECT 1 FROM knowledge_evidence ke WHERE ke.note_id=? AND " + predicate + " LIMIT 1",
                      (note_id,)).fetchone() is not None


def search(home: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    _, path = _root(home)
    query = _text(payload.get("query"), "knowledge query", 2000)
    limit = _bounded_int(payload.get("limit"), DEFAULT_LIMIT, MAX_LIMIT, "limit")
    offset = _bounded_int(payload.get("cursor"), 0, MAX_OFFSET, "cursor")
    scope = _scope(payload.get("scope", {}))
    include_inactive = payload.get("include_inactive") is True
    if payload.get("include_inactive") not in {None, True, False}:
        raise ValueError("include_inactive must be boolean")
    db = _connect(path, readonly=True)
    try:
        # A missing schema is an explicit capability diagnostic, never a JSON/full-table fallback.
        try:
            fts = db.execute("SELECT value FROM knowledge_meta WHERE key='fts5'").fetchone()
        except sqlite3.OperationalError:
            return {"results": [], "next_cursor": None, "capability": "knowledge_not_initialized"}
        statuses = tuple(STATUSES if include_inactive else ACTIVE_STATUSES)
        filters = ["n.status IN (" + ",".join("?" for _ in statuses) + ")"]
        args: list[Any] = list(statuses)
        usable = _usable_evidence_predicate(
            db, provider_invalidated=_provider_learning_invalidated(db)
        )
        filters.append("EXISTS (SELECT 1 FROM knowledge_evidence ke WHERE ke.note_id=n.id AND " + usable + ")")
        for column, key in (("scope_job", "job"), ("scope_request", "request"), ("scope_risk", "risk")):
            value = _field(scope, key)
            if value:
                filters.append(f"(n.{column} IS NULL OR n.{column}=?)")
                args.append(value)
        case_id = payload.get("case_id") or scope.get("case_id")
        if case_id:
            filters.append("(n.case_id IS NULL OR n.case_id=?)")
            args.append(_text(case_id, "case_id", 500))
        else:
            filters.append("n.case_id IS NULL")
        if fts and fts[0] == "1":
            sql = """SELECT n.* FROM knowledge_fts f JOIN knowledge_notes n ON n.id=f.note_id
                     WHERE knowledge_fts MATCH ? AND """ + " AND ".join(filters) + \
                  " ORDER BY bm25(knowledge_fts), n.updated_at DESC LIMIT ? OFFSET ?"
            rows = db.execute(sql, (_query_terms(query), *args, limit + 1, offset)).fetchall()
            capability = "fts5"
        else:
            # Explicitly bounded degraded mode: inspect at most 500 newest candidates.
            candidates = db.execute("SELECT * FROM knowledge_notes n WHERE " + " AND ".join(filters) +
                                    " ORDER BY n.updated_at DESC LIMIT 500", args).fetchall()
            terms = [word for word in WORDS.findall(query.casefold()) if len(word) > 1][:24]
            matches = [row for row in candidates if any(term in (row["normalized_summary"] + " " +
                      " ".join(json.loads(row["aliases"]))).casefold() for term in terms)]
            rows = matches[offset:offset + limit + 1]
            capability = "bounded_fallback_500"
        page = []
        page_chars = 0
        for row in rows[:limit]:
            item = _row_note(row)
            size = len(_json(item, "knowledge result"))
            if page and page_chars + size > MAX_SEARCH_PAGE_CHARS:
                break
            page.append(item)
            page_chars += size
        consumed = len(page)
        more = consumed < len(rows)
        return {"results": page,
                "next_cursor": offset + consumed if more else None, "capability": capability,
                "limit": limit}
    finally:
        db.close()


def lookup(home: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    _, path = _root(home)
    note_id = payload.get("note_id")
    evidence_id = payload.get("evidence_id")
    if bool(note_id) == bool(evidence_id):
        raise ValueError("select exactly one note_id or evidence_id")
    limit = _bounded_int(payload.get("limit"), DEFAULT_LIMIT, MAX_LIMIT, "limit")
    offset = _bounded_int(payload.get("cursor"), 0, MAX_OFFSET, "cursor")
    include_body = payload.get("include_body") is True
    db = _connect(path, readonly=True)
    try:
        if evidence_id:
            evidence = db.execute("SELECT * FROM knowledge_evidence WHERE id=?", (evidence_id,)).fetchone()
            if not evidence:
                raise ValueError("unknown knowledge evidence")
            note = db.execute("SELECT * FROM knowledge_notes WHERE id=?", (evidence["note_id"],)).fetchone()
            return {"note": _row_note(note), "evidence": _row_evidence(evidence, include_body)}
        note = db.execute("SELECT * FROM knowledge_notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            raise ValueError("unknown knowledge note")
        rows = db.execute("SELECT * FROM knowledge_evidence WHERE note_id=? ORDER BY observed_at DESC LIMIT ? OFFSET ?",
                          (note_id, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        result = _row_note(note)
        result["details"] = json.loads(note["details"])
        return {"note": result, "evidence": [_row_evidence(row, include_body) for row in rows[:limit]],
                "next_cursor": offset + limit if more else None}
    finally:
        db.close()


def _row_evidence(row: sqlite3.Row, include_body: bool) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "note_id", "origin_type", "origin_id", "observed_at",
                                        "body_hash", "body_expires_at", "created_at")}
    result.update(reliable=bool(row["reliable"]), excluded=bool(row["excluded"]),
                  metadata=json.loads(row["metadata"]), body_retained=row["body"] is not None)
    if include_body:
        result["body"] = row["body"]
    return result


def export_knowledge(home: str | Path, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a portable, body-free bounded page of notes."""

    payload = payload or {}
    _, path = _root(home)
    limit = _bounded_int(payload.get("limit"), MAX_LIMIT, 1000, "limit")
    offset = _bounded_int(payload.get("cursor"), 0, MAX_OFFSET, "cursor")
    db = _connect(path, readonly=True)
    try:
        rows = db.execute("SELECT * FROM knowledge_notes ORDER BY created_at,id LIMIT ? OFFSET ?",
                          (limit + 1, offset)).fetchall()
        return {"schema": SCHEMA, "notes": [_row_note(row) for row in rows[:limit]],
                "next_cursor": offset + limit if len(rows) > limit else None}
    finally:
        db.close()


def forget(home: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    state, state_revision = _load_engine(home)
    owner_origin = payload.get("owner_event")
    if not isinstance(owner_origin, Mapping) or owner_origin.get("type") != "owner_event":
        raise ValueError("authenticated owner event required for knowledge control")
    _origin_from_state(state, owner_origin)
    action = payload.get("action", "forget")
    if action not in {"forget", "pause", "resume", "exclude_evidence", "exclude_case"}:
        raise ValueError("invalid knowledge control action")
    with _write(home, state_revision) as db:
        if action in {"pause", "resume"}:
            db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('paused', ?)",
                       ("1" if action == "pause" else "0",))
            return {"action": action}
        changed = []
        if action == "forget":
            ids = payload.get("note_ids") or ([payload["note_id"]] if payload.get("note_id") else [])
            if not isinstance(ids, list) or not ids or len(ids) > 100:
                raise ValueError("select a bounded nonempty note list")
            for note_id in ids:
                _set_status(db, _text(note_id, "note_id", 500), "forgotten")
                db.execute("UPDATE knowledge_evidence SET excluded=1,body=NULL WHERE note_id=?", (note_id,))
                changed.append(note_id)
        elif action == "exclude_evidence":
            evidence_id = _text(payload.get("evidence_id"), "evidence_id", 500)
            row = db.execute("SELECT note_id FROM knowledge_evidence WHERE id=?", (evidence_id,)).fetchone()
            if not row:
                raise ValueError("unknown knowledge evidence")
            db.execute("UPDATE knowledge_evidence SET excluded=1,body=NULL WHERE id=?", (evidence_id,))
            _retire_unsupported(db, row["note_id"])
            changed.append(evidence_id)
        else:
            case_id = _text(payload.get("case_id"), "case_id", 500)
            rows = db.execute("SELECT id FROM knowledge_notes WHERE case_id=?", (case_id,)).fetchall()
            for row in rows:
                _set_status(db, row["id"], "forgotten")
                db.execute("UPDATE knowledge_evidence SET excluded=1,body=NULL WHERE note_id=?", (row["id"],))
                changed.append(row["id"])
        return {"action": action, "changed": changed}


def _retire_unsupported(db: sqlite3.Connection, note_id: str) -> None:
    remaining = db.execute("SELECT COUNT(*) FROM knowledge_evidence WHERE note_id=? AND excluded=0 AND reliable=1",
                           (note_id,)).fetchone()[0]
    if remaining == 0:
        row = db.execute("SELECT status FROM knowledge_notes WHERE id=?", (note_id,)).fetchone()
        if row and row["status"] in VISIBLE_STATUSES:
            _set_status(db, note_id, "undone")


def maintain(home: str | Path, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run bounded retention/revocation work and resume from an integer cursor."""

    payload = payload or {}
    migration = migrate_legacy(home)
    state, state_revision = _load_engine(home)
    budget = _bounded_int(payload.get("budget"), 100, 1000, "budget")
    explicit_cursor = "cursor" in payload
    now = _iso(payload.get("now") or stamp(), "now")
    with _write(home, state_revision) as db:
        if explicit_cursor:
            cursor = _bounded_int(payload.get("cursor"), 0, MAX_OFFSET, "cursor")
        else:
            saved_cursor = db.execute(
                "SELECT value FROM knowledge_meta WHERE key='maintenance_cursor'"
            ).fetchone()
            cursor = _bounded_int(int(saved_cursor[0]) if saved_cursor else 0,
                                  0, MAX_OFFSET, "maintenance cursor")
        rows = db.execute("SELECT rowid,* FROM knowledge_evidence WHERE rowid>? ORDER BY rowid LIMIT ?",
                          (cursor, budget + 1)).fetchall()
        pruned = revoked = invalidated = 0
        for row in rows[:budget]:
            if row["body"] is not None and row["body_expires_at"] and _iso(row["body_expires_at"]) <= now:
                db.execute("UPDATE knowledge_evidence SET body=NULL WHERE id=?", (row["id"],))
                pruned += 1
            valid = True
            if row["origin_type"] == "owner_event":
                valid = row["origin_id"] in state.get("owner_events", {})
            elif row["origin_type"] == "provider_observation":
                observation = state.get("observations", {}).get(row["origin_id"])
                valid = bool(observation and observation.get("result", {}).get("learnable") is True)
                if valid:
                    candidate = state.get("drafts", {}).get(observation.get("candidate_id"), {})
                    valid = not candidate.get("excluded")
                source_case_id = row["source_case_id"] or (observation or {}).get("case_id")
                if valid and source_case_id:
                    source_case = state.get("cases", {}).get(source_case_id)
                    valid = not (source_case and source_case.get("learning_excluded") is True)
                if (state.get("config") or {}).get("learning", {}).get("invalidated") is True:
                    note = db.execute("SELECT status FROM knowledge_notes WHERE id=?", (row["note_id"],)).fetchone()
                    if (note and note["status"] in VISIBLE_STATUSES and
                            not _note_has_usable_evidence(db, row["note_id"], provider_invalidated=True)):
                        _set_status(db, row["note_id"], "invalidated")
                        invalidated += 1
            if not valid and not row["excluded"]:
                db.execute("UPDATE knowledge_evidence SET excluded=1,body=NULL WHERE id=?", (row["id"],))
                _retire_unsupported(db, row["note_id"])
                revoked += 1
        outdated = 0
        for row in db.execute("SELECT id FROM knowledge_notes WHERE fresh_until IS NOT NULL AND fresh_until<=? AND status IN ('active','proposed') LIMIT ?",
                              (stamp(now), budget)).fetchall():
            _set_status(db, row["id"], "outdated")
            outdated += 1
        next_cursor = rows[budget - 1]["rowid"] if len(rows) > budget else None
        if not explicit_cursor:
            db.execute("INSERT OR REPLACE INTO knowledge_meta VALUES ('maintenance_cursor', ?)",
                       (str(next_cursor or 0),))
        return {"processed": min(len(rows), budget), "legacy_migrated": migration["migrated"],
                "bodies_pruned": pruned,
                "evidence_revoked": revoked, "provider_notes_invalidated": invalidated,
                "notes_outdated": outdated,
                "next_cursor": next_cursor, "maintenance_cursor": next_cursor or 0,
                "body_retention_days": BODY_RETENTION_DAYS}


def check_dependencies(home: str | Path, dependencies: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(dependencies, list) or len(dependencies) > 100:
        raise ValueError("dependencies must be a bounded list")
    _, path = _root(home)
    db = _connect(path, readonly=True)
    try:
        stale = []
        provider_invalidated = _provider_learning_invalidated(db)
        for dependency in dependencies:
            try:
                row = db.execute("SELECT status,version,updated_at,fresh_until,origin_type FROM knowledge_notes WHERE id=?",
                                 (dependency.get("note_id"),)).fetchone()
            except sqlite3.OperationalError:
                row = None
            if (not row or row["status"] not in ACTIVE_STATUSES or
                    not _note_has_usable_evidence(db, dependency.get("note_id"), provider_invalidated) or
                    row["version"] != dependency.get("version") or
                    row["updated_at"] != dependency.get("updated_at") or
                    (row["fresh_until"] and _iso(row["fresh_until"]) <= utcnow())):
                stale.append(dependency.get("note_id"))
        return {"current": not stale, "stale_note_ids": stale}
    finally:
        db.close()


def execute(home: str | Path, command: str, payload: Mapping[str, Any] | None = None) -> Any:
    p = payload or {}
    if not isinstance(p, Mapping):
        raise ValueError("JSON object required")
    if command == "knowledge-record":
        return record(home, p)
    if command == "knowledge-search":
        return search(home, p)
    if command == "knowledge-lookup":
        if p.get("export") is True:
            return export_knowledge(home, p)
        return lookup(home, p)
    if command == "knowledge-forget":
        return forget(home, p)
    if command == "knowledge-maintain":
        return maintain(home, p)
    raise ValueError("unknown knowledge command")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("knowledge-record", "knowledge-search", "knowledge-lookup",
                                            "knowledge-forget", "knowledge-maintain"))
    parser.add_argument("--home", required=True)
    parser.add_argument("--input", help="Private JSON payload file; stdin when omitted")
    args = parser.parse_args()
    try:
        raw = Path(args.input).read_text() if args.input else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        print(json.dumps(execute(args.home, args.command, payload), indent=2, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
