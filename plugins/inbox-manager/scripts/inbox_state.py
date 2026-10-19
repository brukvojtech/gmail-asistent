#!/usr/bin/env python3
"""Inbox Manager deterministic state rail (stdlib only).

The rail owns durable mechanics: schema migration, queue ordering, context-packet
storage, and draft provenance comparisons. It never decides whether an email is
legitimate, deserves a reply, has enough evidence, or is well written.

Every command takes ``--home <inbox-home-dir>``. JSON-writing commands read one
JSON value from stdin. Exit codes are 0 (ok), 2 (bad input), and 3 (state/storage
integrity error).
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


STATE = "state.json"
LEDGER = "ledger.jsonl"
RELATIONSHIPS = "relationships.json"
REPLY_QUEUE = "reply-queue.json"
QUESTIONS = "questions.md"
DOCTRINE = "doctrine.md"
CONTEXT_DIR = "context-packets"
RUNS_DIR = "runs"
CURRENT_SCHEMA = 4
_FENCED_HOMES = set()


def _assert_not_imported(home, connection=None):
    """A migrated home has one writer: the engine. Never repair legacy files."""
    root = Path(home).resolve()
    database = root / "inbox-engine.sqlite3"
    if connection is None and str(root) in _FENCED_HOMES:
        return
    if not database.exists():
        return
    if database.is_symlink():
        _die(3, "unsafe_engine_database")
    own = connection is None
    try:
        db = connection if connection is not None else sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
        if row and json.loads(row[0]).get("archive_import"):
            _die(3, "legacy_archive_imported", detail="Use inbox_engine.py cleanup and undo commands; originals are preserved evidence.")
    except (sqlite3.Error, ValueError) as exc:
        _die(3, "engine_migration_guard_failed", detail=str(exc))
    finally:
        if own and "db" in locals():
            db.close()


@contextmanager
def _legacy_engine_fence(home):
    """Serialize the entire legacy CLI operation with engine import transactions."""
    root = Path(home).resolve()
    database = root / "inbox-engine.sqlite3"
    if not database.exists():
        yield
        return
    if database.is_symlink():
        _die(3, "unsafe_engine_database")
    db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=15, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        _assert_not_imported(home, db)
        _FENCED_HOMES.add(str(root))
        yield
    finally:
        _FENCED_HOMES.discard(str(root))
        if db.in_transaction:
            db.execute("ROLLBACK")
        db.close()

# A run holds an exclusive lease for this long. A second `begin-run` inside a live
# lease is refused, not granted; only an expired lease is recovered as interrupted.
# `record` renews it so a long legitimate pass never loses its own lease.
RUN_LEASE_MINUTES = 90

SOURCE_DEFAULTS = {
    "email_history": True,
    "calendar": False,
    "meeting_notes": False,
    "tasks": False,
    "slack": False,
}
OUTCOMES = {
    "draft_created",
    "draft_updated",
    "draft_preserved",
    "draft_blocked",
    "no_draft",
}
TASK_SEARCH_STATUSES = {
    "not_searched",
    "no_match",
    "match_found",
    "search_failed",
}
TASK_MODES = {"off", "read_only", "create"}
DRAFT_CHECK_RESULTS = {
    "no_existing_draft",
    "update_allowed",
    "preserve_existing",
    "unavailable",
    "multiple_drafts",
}
GMAIL_URL = re.compile(
    r"^https://mail\.google\.com/mail/u/[0-9]+/#(?:all|inbox|drafts)/[A-Za-z0-9_-]+$"
)
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

# questions.md is the student's file. Parked items are markdown checkboxes so the
# rail can count what is still open without parsing prose; a pre-checkbox file
# falls back to plain bullets and says so.
OPEN_QUESTION = re.compile(r"^\s*[-*]\s+\[ \]\s*\S")
CLOSED_QUESTION = re.compile(r"^\s*[-*]\s+\[[xX]\]\s*\S")
BULLET = re.compile(r"^\s*[-*]\s+\S")
FENCE = re.compile(r"^\s*(?:```|~~~)")

DOCTRINE_BUCKETS = ("Discard", "Keep", "Escalate", "Suppress", "Default")
# Capabilities the product does not have. A doctrine rule commanding one of these is
# silently unenforceable, so the rail flags the line; the student decides the rewrite.
DROPPED_CAPABILITIES = (
    ("unsubscribe", re.compile(r"\bunsubscrib\w*|\bopt[- ]out\b|\bunsub\b", re.I)),
    ("delete", re.compile(r"\bdelete\w*|\btrash\b|\bpermanently remove\b", re.I)),
    ("send", re.compile(
        r"\bauto[- ]?(?:reply|replies|respond|send)\w*"
        r"|\bsend (?:a |an |the |my )?(?:reply|response|answer|email)\b"
        r"|\breply (?:to (?:them|it|these) )?(?:automatically|for me|on my behalf)\b",
        re.I)),
    ("forward", re.compile(r"\bforward\w*\s+(?:it|them|the|to)\b", re.I)),
    ("block_sender", re.compile(r"\bmark (?:it |them )?(?:as )?spam\b|\bblock (?:the )?sender\b", re.I)),
)

STATE_SKELETON = {
    "schema": CURRENT_SCHEMA,
    "upgrade_pending": False,
    "inbox_name": None,
    "watermark": None,
    "config": {
        "write_level": 3,
        "undo_label": "AI/Archived",
        "undo_label_id": None,
        "urgent_label": "Urgent",
        "urgent_label_id": None,
        "archive_enabled": True,
        "escalation_channel": None,
        "escalation_channel_decided_at": None,
        "escalation_channel_legacy": None,
        "label_map": {},
        "scope": None,
        "transport": None,
        "context_depth": None,
        "context_sources": SOURCE_DEFAULTS,
        "draft_mode": None,
        "reply_candidate_limit": 10,
        "schedule": None,
        "task_mode": "off",
    },
    "stats": {
        "runs": 0,
        "processed": 0,
        "archived": 0,
        "kept": 0,
        "escalated": 0,
        "drafted": 0,
        "tasks_created": 0,
    },
    "reply_records": {},
    "retry_set": [],
    "active_run": None,
    "run_lease": None,
    "last_run_at": None,
    "last_success_at": None,
    "recovery": {
        "pending": False,
        "completed_at": None,
    },
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _die(code, error, **detail):
    payload = {"error": error}
    payload.update(detail)
    print(json.dumps(payload), file=sys.stderr)
    raise SystemExit(code)


def _iso(value):
    """Parse ISO-8601 to an aware datetime; tolerate a trailing Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clone(value):
    return json.loads(json.dumps(value))


def _atomic_write(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except OSError as exc:
        _die(3, "storage_write_failed", path=path, detail=str(exc))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)  # skill-safety-check: ignore


def _write_json(path, value):
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_json(path, error_name):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        _die(3, error_name, path=path, detail=str(exc))


def _stdin_json():
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _die(2, "bad_stdin_json", detail=str(exc))


def _state_path(home):
    return os.path.join(home, STATE)


def _ledger_path(home):
    return os.path.join(home, LEDGER)


def _queue_path(home):
    return os.path.join(home, REPLY_QUEUE)


def _relationships_path(home):
    return os.path.join(home, RELATIONSHIPS)


def _context_path(home, candidate_id):
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()
    return os.path.join(home, CONTEXT_DIR, digest + ".json")


def _run_path(home, run_id):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        _die(2, "bad_run_id", value=run_id)
    return os.path.join(home, RUNS_DIR, run_id + ".json")


def _context_pointer_exists(home, pointer):
    if not isinstance(pointer, str):
        return False
    context_root = os.path.realpath(os.path.join(home, CONTEXT_DIR))
    candidate_path = os.path.realpath(os.path.join(home, pointer))
    return (os.path.dirname(candidate_path) == context_root and
            candidate_path.endswith(".json") and os.path.isfile(candidate_path))


def _source_setting_valid(name, value):
    if isinstance(value, bool):
        return name != "email_history" or value is True
    if not isinstance(value, dict):
        return False
    enabled = value.get("enabled", True)
    return isinstance(enabled, bool) and (name != "email_history" or enabled is True)


ESCALATION_CHANNEL_FIELDS = {
    "mcp", "target", "card_ref", "probed_at", "test_send_confirmed_at",
}


def _escalation_channel_complete(value):
    """True only for a fully probed, test-send-confirmed channel record."""
    if not isinstance(value, dict) or set(value) != ESCALATION_CHANNEL_FIELDS:
        return False
    for field in ("mcp", "target", "card_ref"):
        text = value.get(field)
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            return False
    return (_iso(value.get("probed_at")) is not None and
            _iso(value.get("test_send_confirmed_at")) is not None)


def _validate_escalation_channel(value):
    """Setup may only save a channel it probed and whose test send the student
    confirmed delivered. A half-configured channel is a silent escalation failure,
    so the rail refuses it rather than storing an untested target."""
    if value is None:
        return None
    if not isinstance(value, dict):
        _die(2, "bad_escalation_channel", value=value)
    unknown = sorted(set(value) - ESCALATION_CHANNEL_FIELDS)
    if unknown:
        _die(2, "unknown_escalation_channel_fields", fields=unknown)
    missing = sorted(ESCALATION_CHANNEL_FIELDS - set(value))
    if missing:
        _die(2, "incomplete_escalation_channel", missing=missing)
    for field in ("mcp", "target", "card_ref"):
        _required_string(value.get(field), "escalation_channel." + field)
    for field in ("probed_at", "test_send_confirmed_at"):
        if _iso(value.get(field)) is None:
            _die(2, "bad_escalation_channel_timestamp", field=field)
    return value


def _lease_active(state, now=None):
    """Return the live lease dict when one is held and unexpired, else None."""
    lease = state.get("run_lease")
    if not isinstance(lease, dict):
        return None
    if lease.get("run_id") != state.get("active_run"):
        return None
    expires = _iso(lease.get("expires_at"))
    if expires is None:
        return None
    return lease if expires > (now or datetime.now(timezone.utc)) else None


def _new_lease(run_id, started_at=None):
    started = started_at or _now()
    expires = (_iso(started) + timedelta(minutes=RUN_LEASE_MINUTES)).isoformat()
    return {"run_id": run_id, "started_at": started, "expires_at": expires}


def _strip_fences(lines):
    """Yield only lines outside fenced blocks. Parked questions may quote untrusted
    mail inside a fence; quoted bullets are not parked items."""
    inside = False
    for line in lines:
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            yield line


def _open_question_count(home):
    """Count still-open parked questions. Checkboxes are the format; a pre-checkbox
    file falls back to counting bullets so legacy homes still surface review debt."""
    path = os.path.join(home, QUESTIONS)
    if not os.path.isfile(path):
        return 0, "absent"
    try:
        with open(path, encoding="utf-8") as handle:
            lines = list(_strip_fences(handle.read().splitlines()))
    except OSError as exc:
        _die(3, "questions_unreadable", path=path, detail=str(exc))
    open_items = sum(1 for line in lines if OPEN_QUESTION.match(line))
    closed_items = sum(1 for line in lines if CLOSED_QUESTION.match(line))
    if open_items or closed_items:
        return open_items, "checkbox"
    return sum(1 for line in lines if BULLET.match(line)), "legacy_bullets"


def _migrate_state(state):
    """Add current-schema fields without deleting or rewriting unrelated state."""
    if not isinstance(state, dict):
        _die(3, "state_not_object")
    schema = state.get("schema", 1)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        _die(3, "bad_state_schema", value=schema)
    if schema > CURRENT_SCHEMA:
        _die(3, "unsupported_state_schema", value=schema)

    legacy_upgrade = schema < CURRENT_SCHEMA
    changed = legacy_upgrade
    config = state.get("config")
    if config is None:
        state["config"] = {}
        config = state["config"]
        changed = True
    if not isinstance(config, dict):
        _die(3, "bad_state_config")

    if "upgrade_pending" not in state:
        state["upgrade_pending"] = legacy_upgrade
        changed = True
    elif not isinstance(state["upgrade_pending"], bool):
        _die(3, "bad_upgrade_pending", value=state["upgrade_pending"])
    elif legacy_upgrade and state["upgrade_pending"] is not True:
        state["upgrade_pending"] = True
        changed = True

    task_mode_missing = "task_mode" not in config
    # Add all known defaults, retaining unknown config keys and legacy settings.
    for key, default in STATE_SKELETON["config"].items():
        if key not in config:
            config[key] = _clone(default)
            changed = True

    limit = config.get("reply_candidate_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        _die(3, "bad_reply_candidate_limit", value=limit)

    sources = config.get("context_sources")
    if not isinstance(sources, dict):
        _die(3, "bad_context_sources", value=sources)
    for name, default in SOURCE_DEFAULTS.items():
        if name not in sources:
            sources[name] = default
            changed = True
    # Email history is a product invariant, including for legacy states that had
    # explicitly disabled it. Other values and unknown provider parameters survive.
    if not _source_setting_valid("email_history", sources["email_history"]):
        sources["email_history"] = True
        changed = True
    for name in ("calendar", "meeting_notes", "tasks", "slack"):
        if not _source_setting_valid(name, sources[name]):
            _die(3, "bad_context_source", source=name, value=sources[name])

    if task_mode_missing:
        tasks = sources.get("tasks")
        enabled = tasks is True or (isinstance(tasks, dict) and tasks.get("enabled", True))
        create_enabled = isinstance(tasks, dict) and tasks.get("create_enabled") is True
        config["task_mode"] = "create" if create_enabled else ("read_only" if enabled else "off")
        changed = True
    if config.get("task_mode") not in TASK_MODES:
        _die(3, "bad_task_mode", value=config.get("task_mode"))
    if config.get("write_level") == 4:
        config["write_level"] = 3
        changed = True
    elif config.get("write_level") != 3:
        _die(3, "bad_write_level", value=config.get("write_level"))

    # Schema 4: an escalation channel must be probed and test-send-confirmed. A
    # pre-v0.6 `{mcp, target}` was saved untested, so it is retained verbatim under
    # `escalation_channel_legacy` and the live choice is cleared — setup re-asks and
    # re-proves it rather than the runtime trusting a target nobody ever pinged.
    channel = config.get("escalation_channel")
    if channel is not None and not _escalation_channel_complete(channel):
        config["escalation_channel_legacy"] = channel
        config["escalation_channel"] = None
        config["escalation_channel_decided_at"] = None
        changed = True
    decided_at = config.get("escalation_channel_decided_at")
    if decided_at is not None and _iso(decided_at) is None:
        _die(3, "bad_escalation_channel_decided_at", value=decided_at)
    if not isinstance(config.get("archive_enabled"), bool):
        _die(3, "bad_archive_enabled", value=config.get("archive_enabled"))
    urgent_label = config.get("urgent_label")
    if not isinstance(urgent_label, str) or not urgent_label.strip() or len(urgent_label) > 500:
        _die(3, "bad_urgent_label", value=urgent_label)
    urgent_label_id = config.get("urgent_label_id")
    if urgent_label_id is not None and (
            not isinstance(urgent_label_id, str) or not urgent_label_id.strip()):
        _die(3, "bad_urgent_label_id", value=urgent_label_id)

    stats = state.get("stats")
    if stats is None:
        state["stats"] = {}
        stats = state["stats"]
        changed = True
    if not isinstance(stats, dict):
        _die(3, "bad_state_stats")
    for key, default in STATE_SKELETON["stats"].items():
        if key not in stats:
            stats[key] = default
            changed = True

    if "reply_records" not in state:
        state["reply_records"] = {}
        changed = True
    elif not isinstance(state["reply_records"], dict):
        _die(3, "bad_reply_records")

    if "retry_set" not in state:
        state["retry_set"] = []
        changed = True
    elif not isinstance(state["retry_set"], list):
        _die(3, "bad_retry_set")

    if "active_run" not in state:
        state["active_run"] = None
        changed = True
    elif state["active_run"] is not None:
        active_run = state["active_run"]
        if (not isinstance(active_run, str) or not RUN_ID.fullmatch(active_run) or
                active_run in {".", ".."}):
            _die(3, "bad_active_run", value=active_run)

    # A pre-v0.6 home can carry an `active_run` with no lease. Leaving `run_lease`
    # null makes that run look expired, so the existing interrupted-run recovery
    # path handles it exactly as it did before the lease existed.
    if "run_lease" not in state:
        state["run_lease"] = None
        changed = True
    elif state["run_lease"] is not None:
        lease = state["run_lease"]
        if (not isinstance(lease, dict) or
                set(lease) != {"run_id", "started_at", "expires_at"} or
                not isinstance(lease.get("run_id"), str) or
                not RUN_ID.fullmatch(lease["run_id"]) or
                _iso(lease.get("started_at")) is None or
                _iso(lease.get("expires_at")) is None):
            _die(3, "bad_run_lease", value=lease)

    if "last_success_at" not in state:
        state["last_success_at"] = None
        changed = True
    elif state["last_success_at"] is not None and _iso(state["last_success_at"]) is None:
        _die(3, "bad_last_success_at", value=state["last_success_at"])

    if "recovery" not in state:
        state["recovery"] = {
            "pending": state.get("watermark") is not None,
            "completed_at": None,
        }
        changed = True
    else:
        recovery = state["recovery"]
        if not isinstance(recovery, dict):
            _die(3, "bad_recovery")
        if "pending" not in recovery:
            recovery["pending"] = state.get("watermark") is not None
            changed = True
        elif not isinstance(recovery["pending"], bool):
            _die(3, "bad_recovery_pending", value=recovery["pending"])
        if "completed_at" not in recovery:
            recovery["completed_at"] = None
            changed = True
        elif recovery["completed_at"] is not None and _iso(recovery["completed_at"]) is None:
            _die(3, "bad_recovery_completed_at", value=recovery["completed_at"])

    state["schema"] = CURRENT_SCHEMA
    return changed


def _load_state(home, migrate=True):
    _assert_not_imported(home)
    path = _state_path(home)
    if not os.path.exists(path):
        return None
    state = _read_json(path, "state_unreadable")
    if migrate and _migrate_state(state):
        _write_json(path, state)
    return state


def _ensure_auxiliary(home):
    os.makedirs(os.path.join(home, "reports"), exist_ok=True)
    os.makedirs(os.path.join(home, CONTEXT_DIR), exist_ok=True)
    os.makedirs(os.path.join(home, RUNS_DIR), exist_ok=True)
    if not os.path.exists(_relationships_path(home)):
        _write_json(_relationships_path(home), {"schema": 1, "relationships": {}})
    if not os.path.exists(_queue_path(home)):
        _write_json(_queue_path(home), {"schema": 1, "items": []})


def _require_state(home):
    state = _load_state(home)
    if state is None:
        _die(3, "no_home", home=home)
    _ensure_auxiliary(home)
    return state


def _read_queue(home):
    queue = _read_json(_queue_path(home), "reply_queue_unreadable")
    if not isinstance(queue, dict) or queue.get("schema") != 1:
        _die(3, "bad_reply_queue_schema")
    items = queue.get("items")
    if not isinstance(items, list):
        _die(3, "bad_reply_queue_items")
    seen = set()
    for item in items:
        candidate_id = item.get("candidate_id") if isinstance(item, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen:
            _die(3, "bad_reply_queue_item", item=item)
        seen.add(candidate_id)
    return queue


def _read_ledger(home):
    path = _ledger_path(home)
    if not os.path.exists(path):
        return []
    output = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    output.append(json.loads(line))
                except json.JSONDecodeError:
                    # Evidence remains useful even if one append-only row is corrupt.
                    continue
    except OSError as exc:
        _die(3, "ledger_unreadable", detail=str(exc))
    return output


def _validate_candidate_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        _die(2, "bad_candidate_id", value=value)
    return value.strip()


def _bounded_optional_string(value, field, maximum=500):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _die(2, "bad_candidate_field", field=field)
    return value.strip()


def _required_string(value, field, maximum=1000):
    normalized = _bounded_optional_string(value, field, maximum=maximum)
    if normalized is None:
        _die(2, "missing_required_field", field=field)
    return normalized


def _validated_gmail_url(value, field="gmail_url"):
    normalized = _required_string(value, field, maximum=2000)
    if not GMAIL_URL.fullmatch(normalized):
        _die(2, "bad_gmail_url", field=field)
    return normalized


def _validated_http_url(value, field):
    normalized = _required_string(value, field, maximum=2000)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _die(2, "bad_url", field=field)
    return normalized


def _validate_task_search(value):
    if value is None:
        return {"status": "not_searched"}
    if not isinstance(value, dict):
        _die(2, "bad_task_search")
    unknown = sorted(set(value) - {"status", "task_id", "task_pointer", "task_url", "detail"})
    if unknown:
        _die(2, "unknown_task_search_fields", fields=unknown)
    status = value.get("status")
    if status not in TASK_SEARCH_STATUSES:
        _die(2, "bad_task_search_status", value=status)
    for field in ("task_id", "task_pointer", "task_url", "detail"):
        if field in value:
            maximum = 2000 if field == "task_url" else 1000
            _bounded_optional_string(value[field], field, maximum=maximum)
    return value


def _validate_context_list(packet, field, allowed_fields, required_fields=()):
    value = packet.get(field, [])
    if not isinstance(value, list) or len(value) > 25:
        _die(2, "bad_context_packet_field", field=field)
    clean = []
    for item in value:
        if not isinstance(item, dict):
            _die(2, "bad_context_packet_item", field=field)
        unknown = sorted(set(item) - set(allowed_fields))
        if unknown:
            _die(2, "unknown_context_packet_item_fields", field=field, fields=unknown)
        for required in required_fields:
            if required not in item:
                _die(2, "missing_context_packet_item_field", field=field, required=required)
        normalized = {}
        for key, maximum in allowed_fields.items():
            if key in item:
                normalized_value = _bounded_optional_string(item[key], key, maximum=maximum)
                if key in required_fields and normalized_value is None:
                    _die(2, "missing_context_packet_item_field", field=field, required=key)
                normalized[key] = normalized_value
        clean.append(normalized)
    return clean


def _validate_context_string_list(packet, field):
    value = packet.get(field, [])
    if not isinstance(value, list) or len(value) > 25:
        _die(2, "bad_context_packet_field", field=field)
    output = []
    for item in value:
        normalized = _bounded_optional_string(item, field, maximum=1000)
        if normalized is None:
            _die(2, "bad_context_packet_item", field=field)
        output.append(normalized)
    return output


def _validate_history_window(value):
    if value is None:
        _die(2, "missing_required_field", field="history_window")
    if not isinstance(value, dict) or set(value) - {"started_days", "expanded_days", "reason"}:
        _die(2, "bad_history_window")
    for field in ("started_days", "expanded_days"):
        days = value.get(field)
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 365):
            _die(2, "bad_history_window", field=field)
    if value.get("started_days") != 90:
        _die(2, "bad_history_window", field="started_days")
    expanded = value.get("expanded_days")
    if expanded is not None and expanded < 90:
        _die(2, "bad_history_window", field="expanded_days")
    if expanded is not None and expanded > 90 and not value.get("reason"):
        _die(2, "history_expansion_requires_reason")
    if "reason" in value:
        _bounded_optional_string(value["reason"], "history_window.reason", maximum=1000)
    return value


def _validate_draft_check(value):
    if value is None:
        _die(2, "missing_required_field", field="draft_check")
    if not isinstance(value, dict) or set(value) - {"result", "draft_id", "detail"}:
        _die(2, "bad_context_draft_check")
    if "result" not in value:
        _die(2, "bad_context_draft_check")
    if value.get("result") not in DRAFT_CHECK_RESULTS:
        _die(2, "bad_context_draft_check")
    for field in ("result", "draft_id", "detail"):
        if field in value:
            normalized = _bounded_optional_string(value[field], "draft_check." + field, maximum=1000)
            if field == "result" and normalized is None:
                _die(2, "bad_context_draft_check")
    return value


def _read_context_packet(home, pointer):
    if not _context_pointer_exists(home, pointer):
        _die(2, "missing_context_packet")
    return _read_json(os.path.join(home, pointer), "context_packet_unreadable")


def _context_for_run(home, record, candidate_id, run_id, grounded=False):
    packet = _read_context_packet(home, record.get("context_packet"))
    if packet.get("candidate_id") != candidate_id or packet.get("run_id") != run_id:
        _die(2, "stale_context_packet")
    if grounded:
        used = {item.get("source") for item in packet.get("sources_used", [])}
        missing = sorted({"current_thread", "email_history"} - used)
        if missing:
            _die(2, "grounded_outcome_missing_sources", sources=missing)
    return packet


def _bounded_string_list(value, field, maximum):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        _die(2, "bad_relationship_field", field=field)
    output = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            _die(2, "bad_relationship_field", field=field)
        clean = item.strip()
        if clean not in output:
            output.append(clean)
    return output


def _validate_run_window(value):
    if not isinstance(value, dict) or set(value) != {"after", "until"}:
        _die(2, "bad_run_window")
    after = value.get("after")
    until = value.get("until")
    parsed_after = None if after is None else _iso(after)
    parsed_until = _iso(until)
    if after is not None and parsed_after is None:
        _die(2, "bad_run_window_after", value=after)
    if parsed_until is None:
        _die(2, "bad_run_window_until", value=until)
    if parsed_after is not None and parsed_after > parsed_until:
        _die(2, "run_window_reversed")
    return {
        "after": parsed_after.isoformat() if parsed_after is not None else None,
        "until": parsed_until.isoformat(),
    }


def _validate_manifest_rows(value, field):
    if not isinstance(value, list):
        _die(2, "bad_manifest_rows", field=field)
    rows = []
    seen = set()
    allowed = {
        "message_id", "thread_id", "email_date", "sender", "subject",
        # A caller may reuse record-ready selected rows. The manifest retains only
        # identity/listing metadata; action/proof fields are validated later by record.
        "action", "run_id", "labels", "label_ids", "provider_id", "undo_verified", "reason",
    }
    for original in value:
        if not isinstance(original, dict):
            _die(2, "bad_manifest_row", field=field)
        unknown = sorted(set(original) - allowed)
        if unknown:
            _die(2, "unknown_manifest_row_fields", field=field, fields=unknown)
        message_id = _required_string(original.get("message_id"), "message_id")
        if message_id in seen:
            _die(2, "duplicate_manifest_message_id", field=field, message_id=message_id)
        seen.add(message_id)
        email_date = _iso(original.get("email_date"))
        if email_date is None:
            _die(2, "bad_manifest_email_date", message_id=message_id)
        row = {
            "message_id": message_id,
            "email_date": email_date.isoformat(),
        }
        for optional in ("thread_id", "sender", "subject"):
            normalized = _bounded_optional_string(original.get(optional), optional)
            if normalized is not None:
                row[optional] = normalized
        rows.append(row)
    return rows


def _retry_index(retry_set):
    output = {}
    for item in retry_set:
        if not isinstance(item, dict):
            _die(3, "bad_retry_item", item=item)
        message_id = item.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            _die(3, "bad_retry_item", item=item)
        output[message_id] = item
    return output


def _add_retry(state, row, reason, attempt_increment):
    retry = _retry_index(state["retry_set"])
    existing = retry.get(row["message_id"], {})
    item = dict(existing)
    for field in ("message_id", "thread_id", "email_date", "sender", "subject"):
        if row.get(field) is not None:
            item[field] = row[field]
    item["reason"] = reason
    item["deferred_at"] = _now()
    attempts = existing.get("attempts", row.get("attempts", 0))
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        _die(3, "bad_retry_attempts", message_id=row["message_id"])
    item["attempts"] = attempts + attempt_increment
    retry[row["message_id"]] = item
    state["retry_set"] = list(retry.values())


def _read_manifest(home, run_id):
    path = _run_path(home, run_id)
    if not os.path.isfile(path):
        _die(3, "run_manifest_missing", run_id=run_id)
    manifest = _read_json(path, "run_manifest_unreadable")
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or
            manifest.get("run_id") != run_id or
            not isinstance(manifest.get("entries"), list)):
        _die(3, "bad_run_manifest", run_id=run_id)
    return manifest


def _terminal_message_ids(home, run_id):
    return {
        entry.get("message_id")
        for entry in _read_ledger(home)
        if entry.get("run_id") == run_id
        and entry.get("action") in {"archive", "keep", "escalate"}
        and isinstance(entry.get("message_id"), str)
    }


def cmd_init(home, args):
    _assert_not_imported(home)
    os.makedirs(home, exist_ok=True)
    path = _state_path(home)
    created = not os.path.exists(path)
    migrated = False
    if created:
        state = _clone(STATE_SKELETON)
        state["inbox_name"] = args.inbox_name or os.path.basename(os.path.abspath(home))
        _write_json(path, state)
    else:
        state = _read_json(path, "state_unreadable")
        migrated = _migrate_state(state)
        if migrated:
            _write_json(path, state)
    if not os.path.exists(_ledger_path(home)):
        _atomic_write(_ledger_path(home), "")
    _ensure_auxiliary(home)
    print(json.dumps({
        "ok": True,
        "home": home,
        "created_state": created,
        "migrated": migrated,
        "schema": CURRENT_SCHEMA,
    }))


def cmd_read(home, args):
    state = _load_state(home)
    if state is None:
        print(json.dumps({"error": "no_home", "home": home}))
        return
    _ensure_auxiliary(home)
    queue = _read_queue(home)
    limit = state["config"]["reply_candidate_limit"]
    draft_mode = state["config"].get("draft_mode")
    ready = queue["items"][:limit] if draft_mode == "auto" else []
    open_questions, question_basis = _open_question_count(home)
    # The retry set reaches thousands of rows during a backlog cleanup. Routing needs
    # its size, never its contents; triage pulls the rows it will work from
    # `retry-set`. Emitting the array here printed the whole backlog into context on
    # every routing decision.
    summary = dict(state)
    summary.pop("retry_set", None)
    summary["retry_set_size"] = len(state["retry_set"])
    print(json.dumps({
        "state": summary,
        "upgrade_pending": state.get("upgrade_pending", False),
        "ledger_entries": len(_read_ledger(home)),
        "watermark": state.get("watermark"),
        "retry_set_size": len(state["retry_set"]),
        "active_run": state.get("active_run"),
        "run_lease": state.get("run_lease"),
        "run_in_progress": _lease_active(state) is not None,
        "last_success_at": state.get("last_success_at"),
        "recovery": state["recovery"],
        "schedule": state["config"].get("schedule"),
        "open_question_count": open_questions,
        "open_question_basis": question_basis,
        "reply_queue": {
            "total": len(queue["items"]),
            "ready": ready,
            "paused_count": len(queue["items"]) if draft_mode != "auto" else 0,
            "overflow_count": max(0, len(queue["items"]) - len(ready)),
        },
    }, indent=2))


def cmd_retry_set(home, args):
    state = _require_state(home)
    limit = args.limit
    if not isinstance(limit, int) or limit < 1 or limit > 5000:
        _die(2, "bad_retry_set_limit", value=limit)
    entries = state["retry_set"]
    returned = entries[:limit]
    print(json.dumps({
        "ok": True,
        "size": len(entries),
        "returned": len(returned),
        "truncated": len(entries) > len(returned),
        "entries": returned,
    }, indent=2))


def cmd_begin_run(home, args):
    state = _require_state(home)
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "begin_run_not_object")
    unknown = sorted(
        set(payload) - {"run_id", "window", "listing_complete", "selected", "deferred"}
    )
    if unknown:
        _die(2, "unknown_begin_run_fields", fields=unknown)
    run_id = _required_string(payload.get("run_id"), "run_id")
    path = _run_path(home, run_id)
    if os.path.exists(path):
        _die(2, "run_manifest_exists", run_id=run_id)
    window = _validate_run_window(payload.get("window"))
    listing_complete = payload.get("listing_complete")
    if not isinstance(listing_complete, bool):
        _die(2, "bad_listing_complete", value=listing_complete)
    selected = _validate_manifest_rows(payload.get("selected"), "selected")
    deferred = _validate_manifest_rows(payload.get("deferred"), "deferred")
    selected_ids = {row["message_id"] for row in selected}
    overlap = sorted(selected_ids & {row["message_id"] for row in deferred})
    if overlap:
        _die(2, "manifest_selection_overlap", message_ids=overlap)

    interrupted = []
    prior_run = state.get("active_run")
    # A live lease means another pass is mid-flight — commonly a manual `/inbox`
    # landing on top of the daily schedule. Seizing it stranded the displaced run's
    # Gmail writes with no ledger row and no undo proof, so refuse instead.
    live_lease = _lease_active(state)
    if live_lease is not None:
        _die(2, "run_in_progress",
             active_run=prior_run,
             started_at=live_lease.get("started_at"),
             lease_expires_at=live_lease.get("expires_at"),
             detail="another inbox run holds the lease; wait for it to finish or for "
                    "the lease to expire before starting a new pass")
    if prior_run is not None:
        prior = _read_manifest(home, prior_run)
        terminal = _terminal_message_ids(home, prior_run)
        for entry in prior["entries"]:
            message_id = entry.get("message_id")
            if isinstance(message_id, str) and message_id not in terminal:
                _add_retry(state, entry, "run_interrupted", 1)
                interrupted.append(message_id)

    retry_before_drain = _retry_index(state["retry_set"])
    for row in deferred:
        _add_retry(state, row, "pass_cap", 0)
    manifest_entries = []
    for row in selected:
        entry = dict(row, status="pending")
        previous_retry = retry_before_drain.get(row["message_id"])
        if previous_retry is not None:
            entry["attempts"] = previous_retry.get("attempts", 0)
        manifest_entries.append(entry)
    state["retry_set"] = [
        item for item in state["retry_set"]
        if item.get("message_id") not in selected_ids
    ]

    began_at = _now()
    manifest = {
        "schema": 1,
        "run_id": run_id,
        "window": window,
        "listing_complete": listing_complete,
        "entries": manifest_entries,
        "deferred": deferred,
        "began_at": began_at,
        "completed_at": None,
    }
    _write_json(path, manifest)
    state["active_run"] = run_id
    state["run_lease"] = _new_lease(run_id, began_at)
    _write_json(_state_path(home), state)
    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "selected": len(selected),
        "deferred": len(deferred),
        "listing_complete": listing_complete,
        "lease": state["run_lease"],
        "interrupted": {
            "run_id": prior_run,
            "folded": len(interrupted),
            "message_ids": interrupted,
        } if prior_run is not None else None,
        # Size plus a bounded sample. A cleanup chunk calls begin-run once per pass, and
        # dumping thousands of rows here would undo the reason `read` stopped doing it.
        "retry_set": {
            "size": len(state["retry_set"]),
            "sample": state["retry_set"][:10],
            "truncated": len(state["retry_set"]) > 10,
        },
    }, indent=2))


def _validated_ledger_entry(original, state, run_id):
    if not isinstance(original, dict):
        _die(2, "malformed_entry", entry=original)
    if original.get("action") == "task":
        _die(2, "task_requires_record_task")
    if original.get("action") == "draft":
        _die(2, "draft_requires_record_reply")
    for field in ("message_id", "thread_id", "action", "sender", "subject", "email_date", "run_id"):
        if field not in original:
            _die(2, "missing_ledger_field", field=field)
    if "label" in original or "label_id" in original:
        _die(2, "legacy_label_fields_rejected")

    entry = dict(original)
    for field in ("message_id", "thread_id", "sender", "subject", "run_id"):
        entry[field] = _required_string(entry.get(field), field)
    email_date = _iso(entry.get("email_date"))
    if email_date is None:
        _die(2, "bad_ledger_email_date", value=entry.get("email_date"))
    entry["email_date"] = email_date.isoformat()
    action = entry.get("action")
    if action == "kept":
        action = "keep"
    if action not in {"archive", "keep", "escalate"}:
        _die(2, "bad_ledger_action", value=entry.get("action"))
    entry["action"] = action
    if entry["run_id"] != run_id:
        _die(2, "run_id_mismatch", expected=run_id, value=entry["run_id"])

    labels_present = "labels" in entry
    labels = entry.get("labels", [])
    if labels_present:
        if (not isinstance(labels, list) or
                not all(isinstance(label, str) and label.strip() for label in labels)):
            _die(2, "bad_ledger_labels")
        entry["labels"] = [label.strip() for label in labels]
    label_ids = entry.get("label_ids", [])
    if "label_ids" in entry:
        if (not isinstance(label_ids, list) or
                not all(isinstance(label_id, str) and label_id.strip() for label_id in label_ids)):
            _die(2, "bad_ledger_label_ids")
        entry["label_ids"] = [label_id.strip() for label_id in label_ids]
        if not labels_present:
            _die(2, "ledger_label_ids_require_labels")
        if len(entry["label_ids"]) != len(entry["labels"]):
            _die(2, "ledger_label_arrays_mismatch")

    provider_write = action == "archive" or (
        action in {"keep", "escalate"} and bool(labels)
    )
    if provider_write:
        entry["provider_id"] = _required_string(entry.get("provider_id"), "provider_id")
    if action == "archive":
        if not labels:
            _die(2, "archive_requires_labels")
        undo_label = state["config"].get("undo_label")
        undo_label_id = state["config"].get("undo_label_id")
        claimed = set(entry["labels"]) | set(entry.get("label_ids", []))
        if not ((undo_label and undo_label in claimed) or
                (undo_label_id and undo_label_id in claimed)):
            _die(2, "archive_requires_undo_label")
        if entry.get("undo_verified") is not True:
            _die(2, "archive_requires_undo_verified")
    entry["recorded_at"] = _now()
    return entry


def cmd_record(home, args):
    state = _require_state(home)
    entries = _stdin_json()
    if not isinstance(entries, list):
        _die(2, "entries_not_list")
    run_id = state.get("active_run")
    if run_id is None:
        _die(2, "record_requires_active_run")
    manifest = _read_manifest(home, run_id)
    manifest_by_id = {
        entry.get("message_id"): entry
        for entry in manifest["entries"]
        if isinstance(entry, dict)
    }
    validated = []
    batch_ids = set()
    for original in entries:
        entry = _validated_ledger_entry(original, state, run_id)
        message_id = entry["message_id"]
        if message_id not in manifest_by_id:
            _die(2, "message_not_in_active_manifest", message_id=message_id)
        if message_id in batch_ids:
            _die(2, "duplicate_record_message_id", message_id=message_id)
        if manifest_by_id[message_id].get("status") == "terminal":
            _die(2, "message_already_terminal", message_id=message_id)
        batch_ids.add(message_id)
        validated.append(entry)

    lines = [json.dumps(entry) for entry in validated]

    if lines:
        try:
            with open(_ledger_path(home), "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            _die(3, "ledger_write_failed", detail=str(exc))
    for entry in validated:
        manifest_entry = manifest_by_id[entry["message_id"]]
        manifest_entry["status"] = "terminal"
        manifest_entry["action"] = entry["action"]
        manifest_entry["recorded_at"] = entry["recorded_at"]
        state["stats"]["processed"] += 1
        stat = {
            "keep": "kept",
            "archive": "archived",
            "escalate": "escalated",
        }[entry["action"]]
        state["stats"][stat] = state["stats"].get(stat, 0) + 1
    state["last_run_at"] = _now()
    # Renew while the run is demonstrably alive, so a long legitimate pass keeps its
    # own lease instead of becoming stealable mid-flight.
    state["run_lease"] = _new_lease(run_id, state["last_run_at"])
    _write_json(_run_path(home, run_id), manifest)
    _write_json(_state_path(home), state)
    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "recorded": len(lines),
        "watermark": state.get("watermark"),
        "lease_expires_at": state["run_lease"]["expires_at"],
    }))


def cmd_complete_run(home, args):
    state = _require_state(home)
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "complete_run_not_object")
    unknown = sorted(set(payload) - {"run_id", "defer", "recovery"})
    if unknown:
        _die(2, "unknown_complete_run_fields", fields=unknown)
    run_id = _required_string(payload.get("run_id"), "run_id")
    _run_path(home, run_id)
    if state.get("active_run") != run_id:
        _die(2, "active_run_mismatch", expected=state.get("active_run"), value=run_id)
    manifest = _read_manifest(home, run_id)

    defer_value = payload.get("defer", [])
    if not isinstance(defer_value, list):
        _die(2, "bad_complete_run_defer")
    deferred = {}
    for item in defer_value:
        if not isinstance(item, dict) or set(item) != {"message_id", "reason"}:
            _die(2, "bad_complete_run_defer_item")
        message_id = _required_string(item.get("message_id"), "message_id")
        if message_id in deferred:
            _die(2, "duplicate_complete_run_defer", message_id=message_id)
        deferred[message_id] = _required_string(item.get("reason"), "reason")

    manifest_by_id = {
        entry.get("message_id"): entry
        for entry in manifest["entries"]
        if isinstance(entry, dict) and isinstance(entry.get("message_id"), str)
    }
    unknown_deferred = sorted(set(deferred) - set(manifest_by_id))
    if unknown_deferred:
        _die(2, "deferred_message_not_in_manifest", message_ids=unknown_deferred)
    terminal = _terminal_message_ids(home, run_id)
    terminal_and_deferred = sorted(terminal & set(deferred))
    if terminal_and_deferred:
        _die(2, "terminal_message_cannot_be_deferred", message_ids=terminal_and_deferred)
    unaccounted = sorted(set(manifest_by_id) - terminal - set(deferred))
    if unaccounted:
        _die(2, "run_entries_unaccounted", message_ids=unaccounted)

    for message_id, reason in deferred.items():
        row = manifest_by_id[message_id]
        _add_retry(state, row, reason, 1)
        row["status"] = "deferred"
        row["deferred_at"] = _now()
        row["defer_reason"] = reason

    recovery_value = payload.get("recovery")
    if recovery_value is not None:
        if (not isinstance(recovery_value, dict) or
                set(recovery_value) != {"swept", "remaining"} or
                not isinstance(recovery_value.get("swept"), bool) or
                not isinstance(recovery_value.get("remaining"), int) or
                isinstance(recovery_value.get("remaining"), bool) or
                recovery_value.get("remaining") < 0):
            _die(2, "bad_complete_run_recovery")

    watermark_advanced = False
    previous_watermark = state.get("watermark")
    if manifest.get("listing_complete") is True:
        current = _iso(previous_watermark)
        until = _iso(manifest.get("window", {}).get("until"))
        if until is None:
            _die(3, "bad_run_manifest_window", run_id=run_id)
        if current is None or until > current:
            state["watermark"] = until.isoformat()
            watermark_advanced = True

    completed_at = _now()
    if (recovery_value is not None and recovery_value["swept"] and
            recovery_value["remaining"] == 0):
        state["recovery"]["pending"] = False
        state["recovery"]["completed_at"] = completed_at
    state["last_success_at"] = completed_at
    state["stats"]["runs"] += 1
    state["active_run"] = None
    state["run_lease"] = None
    manifest["completed_at"] = completed_at
    manifest["status"] = "complete"
    _write_json(_run_path(home, run_id), manifest)
    _write_json(_state_path(home), state)
    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "watermark": state.get("watermark"),
        "watermark_advanced": watermark_advanced,
        "watermark_held_reason": None if manifest.get("listing_complete") else "listing_incomplete",
        "deferred": len(deferred),
        "retry_set_size": len(state["retry_set"]),
        "recovery": state["recovery"],
    }, indent=2))


ALLOWED_CONFIG = {
    "write_level", "undo_label", "undo_label_id", "urgent_label", "urgent_label_id",
    "archive_enabled", "escalation_channel", "escalation_channel_decided_at",
    "label_map", "scope", "transport", "context_depth",
    "context_sources", "draft_mode", "reply_candidate_limit", "schedule", "task_mode",
}


def _validate_config_patch(patch):
    unknown = sorted(set(patch) - ALLOWED_CONFIG)
    if unknown:
        _die(2, "unknown_config_keys", keys=unknown)
    if "write_level" in patch and patch["write_level"] != 3:
        _die(2, "bad_write_level", value=patch["write_level"])
    if "escalation_channel" in patch:
        _validate_escalation_channel(patch["escalation_channel"])
    if "escalation_channel_decided_at" in patch:
        value = patch["escalation_channel_decided_at"]
        if value is not None and _iso(value) is None:
            _die(2, "bad_escalation_channel_decided_at", value=value)
    if "archive_enabled" in patch and not isinstance(patch["archive_enabled"], bool):
        _die(2, "bad_archive_enabled", value=patch["archive_enabled"])
    for field in ("undo_label", "urgent_label"):
        if field in patch:
            _required_string(patch[field], field, maximum=500)
    for field in ("undo_label_id", "urgent_label_id"):
        if field in patch and patch[field] is not None:
            _required_string(patch[field], field, maximum=500)
    if "scope" in patch and patch["scope"] not in ("primary", "all"):
        _die(2, "bad_scope", value=patch["scope"])
    if "transport" in patch and patch["transport"] not in ("cli", "mcp"):
        _die(2, "bad_transport", value=patch["transport"])
    if "context_depth" in patch and patch["context_depth"] not in ("cheap", "scheduled_capped", "full"):
        _die(2, "bad_context_depth", value=patch["context_depth"])
    if "draft_mode" in patch and patch["draft_mode"] not in ("auto", "off"):
        _die(2, "bad_draft_mode", value=patch["draft_mode"])
    if "task_mode" in patch and patch["task_mode"] not in TASK_MODES:
        _die(2, "bad_task_mode", value=patch["task_mode"])
    if "reply_candidate_limit" in patch:
        limit = patch["reply_candidate_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            _die(2, "bad_reply_candidate_limit", value=limit)
    if "context_sources" in patch:
        sources = patch["context_sources"]
        if not isinstance(sources, dict):
            _die(2, "bad_context_sources", value=sources)
        allowed = set(SOURCE_DEFAULTS)
        unknown_sources = sorted(set(sources) - allowed)
        if unknown_sources:
            _die(2, "unknown_context_sources", sources=unknown_sources)
        for name, value in sources.items():
            if not _source_setting_valid(name, value):
                _die(2, "bad_context_source", source=name, value=value)
            enabled = value is True or (isinstance(value, dict) and value.get("enabled", True))
            if name != "email_history" and enabled:
                if not isinstance(value, dict):
                    _die(2, "enabled_source_missing_card_ref", source=name)
                card_ref = value.get("card_ref")
                probed_at = value.get("probed_at")
                if not isinstance(card_ref, str) or not card_ref.strip():
                    _die(2, "enabled_source_missing_card_ref", source=name)
                if _iso(probed_at) is None:
                    _die(2, "enabled_source_missing_probe_time", source=name)
    if "schedule" in patch:
        schedule = patch["schedule"]
        if schedule is not None:
            if not isinstance(schedule, dict):
                _die(2, "bad_schedule", value=schedule)
            unknown = sorted(set(schedule) - {"cadence", "task_id", "verified_at"})
            if unknown:
                _die(2, "unknown_schedule_fields", fields=unknown)
            for field in ("cadence", "task_id"):
                value = schedule.get(field)
                if not isinstance(value, str) or not value.strip() or len(value) > 500:
                    _die(2, "bad_schedule_field", field=field)
            if _iso(schedule.get("verified_at")) is None:
                _die(2, "bad_schedule_field", field="verified_at")


def cmd_set_config(home, args):
    state = _require_state(home)
    patch = _stdin_json()
    if not isinstance(patch, dict):
        _die(2, "patch_not_object")
    _validate_config_patch(patch)
    config = state["config"]
    for key, value in patch.items():
        if key in ("label_map", "context_sources") and isinstance(value, dict):
            config.setdefault(key, {}).update(value)
        else:
            config[key] = value
    # This is redundant with validation but keeps the invariant obvious after merge.
    config["context_sources"]["email_history"] = config["context_sources"].get("email_history", True)
    tasks = config["context_sources"].get("tasks", False)
    tasks_enabled = tasks is True or (isinstance(tasks, dict) and tasks.get("enabled", True))
    if config.get("task_mode", "off") != "off" and not tasks_enabled:
        _die(2, "task_mode_requires_enabled_source", value=config.get("task_mode"))
    _write_json(_state_path(home), state)
    print(json.dumps({"ok": True, "config": config}))


def cmd_queue_replies(home, args):
    state = _require_state(home)
    if state["config"].get("draft_mode") != "auto":
        _die(2, "reply_preparation_disabled",
             draft_mode=state["config"].get("draft_mode"))
    incoming = _stdin_json()
    if not isinstance(incoming, list):
        _die(2, "candidates_not_list")

    prepared = []
    incoming_ids = set()
    allowed = {
        "candidate_id", "message_id", "thread_id", "gmail_url", "sender",
        "subject", "safe_reason", "priority", "enqueued_at",
    }
    for item in incoming:
        if not isinstance(item, dict):
            _die(2, "malformed_candidate", candidate=item)
        unknown = sorted(set(item) - allowed)
        if unknown:
            _die(2, "unsafe_candidate_fields", fields=unknown)
        candidate_id = _validate_candidate_id(item.get("candidate_id"))
        if candidate_id in incoming_ids:
            continue
        incoming_ids.add(candidate_id)
        candidate = {"candidate_id": candidate_id}
        for field in ("message_id", "thread_id", "sender", "subject", "safe_reason"):
            candidate[field] = _required_string(item.get(field), field)
        candidate["gmail_url"] = _validated_gmail_url(item.get("gmail_url"))
        priority = item.get("priority")
        if priority is not None:
            if (not isinstance(priority, int) or isinstance(priority, bool) or
                    not 0 <= priority <= 100):
                _die(2, "bad_candidate_field", field="priority")
            candidate["priority"] = priority
        enqueued_at = item.get("enqueued_at")
        if enqueued_at is not None and _iso(enqueued_at) is None:
            _die(2, "bad_candidate_field", field="enqueued_at")
        candidate["enqueued_at"] = enqueued_at or _now()
        prepared.append(candidate)

    queue = _read_queue(home)
    existing = {item["candidate_id"] for item in queue["items"]}
    additions = [item for item in prepared if item["candidate_id"] not in existing]
    queue["items"].extend(additions)
    _write_json(_queue_path(home), queue)
    limit = state["config"]["reply_candidate_limit"]
    print(json.dumps({
        "ok": True,
        "added": len(additions),
        "deduplicated": len(incoming) - len(additions),
        "total": len(queue["items"]),
        "limit": limit,
        "ready": queue["items"][:limit],
        "overflow_count": max(0, len(queue["items"]) - limit),
    }, indent=2))


def cmd_dequeue_replies(home, args):
    state = _require_state(home)
    candidate_ids = _stdin_json()
    if not isinstance(candidate_ids, list):
        _die(2, "candidate_ids_not_list")
    remove = {_validate_candidate_id(value) for value in candidate_ids}
    queue = _read_queue(home)
    queued = {item["candidate_id"] for item in queue["items"]}
    incomplete = sorted(
        candidate_id for candidate_id in remove
        if candidate_id in queued
        if state.get("reply_records", {}).get(candidate_id, {}).get("outcome") not in OUTCOMES
    )
    if incomplete:
        _die(2, "cannot_dequeue_without_terminal_outcome", candidate_ids=incomplete)
    before = len(queue["items"])
    queue["items"] = [item for item in queue["items"] if item["candidate_id"] not in remove]
    _write_json(_queue_path(home), queue)
    print(json.dumps({"ok": True, "removed": before - len(queue["items"]), "total": len(queue["items"])}))


def cmd_upsert_relationship(home, args):
    _require_state(home)
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "relationship_not_object")
    allowed = {
        "relationship_id", "email_addresses", "display_names", "company_domains",
        "source_pointers", "last_verified",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        _die(2, "unknown_relationship_fields", fields=unknown)
    relationship_id = _validate_candidate_id(payload.get("relationship_id"))
    emails = _bounded_string_list(payload.get("email_addresses"), "email_addresses", 20)
    names = _bounded_string_list(payload.get("display_names"), "display_names", 10)
    domains = _bounded_string_list(payload.get("company_domains"), "company_domains", 10)
    pointers = payload.get("source_pointers", [])
    if not isinstance(pointers, list) or len(pointers) > 25:
        _die(2, "bad_relationship_field", field="source_pointers")
    clean_pointers = []
    for pointer in pointers:
        if not isinstance(pointer, dict) or set(pointer) - {"source", "pointer"}:
            _die(2, "bad_relationship_pointer")
        source = pointer.get("source")
        value = pointer.get("pointer")
        if (not isinstance(source, str) or not source.strip() or len(source) > 100 or
                not isinstance(value, str) or not value.strip() or len(value) > 1000):
            _die(2, "bad_relationship_pointer")
        clean = {"source": source.strip(), "pointer": value.strip()}
        if clean not in clean_pointers:
            clean_pointers.append(clean)
    last_verified = payload.get("last_verified")
    if last_verified is not None and _iso(last_verified) is None:
        _die(2, "bad_last_verified", value=last_verified)

    path = _relationships_path(home)
    index = _read_json(path, "relationships_unreadable")
    if (not isinstance(index, dict) or index.get("schema") != 1 or
            not isinstance(index.get("relationships"), dict)):
        _die(3, "bad_relationships_schema")
    record = {
        "email_addresses": emails,
        "display_names": names,
        "company_domains": domains,
        "source_pointers": clean_pointers,
        "last_verified": last_verified,
        "updated_at": _now(),
    }
    index["relationships"][relationship_id] = record
    _write_json(path, index)
    print(json.dumps({"ok": True, "relationship_id": relationship_id, "relationship": record}, indent=2))


def cmd_save_context(home, args):
    state = _require_state(home)
    packet = _stdin_json()
    if not isinstance(packet, dict):
        _die(2, "context_packet_not_object")
    allowed = {
        "candidate_id", "run_id", "message_id", "thread_id", "gmail_url", "request_summary",
        "ownership", "history_window", "sources_used", "sources_skipped",
        "sources_unavailable", "linked_artifacts_inspected", "tone_samples",
        "evidence_count", "evidence_cap", "evidence_cap_reason", "draft_check", "task_search",
        "remaining_uncertainty",
    }
    unknown = sorted(set(packet) - allowed)
    if unknown:
        _die(2, "unknown_context_packet_fields", fields=unknown)
    required = {
        "candidate_id", "run_id", "message_id", "thread_id", "gmail_url",
        "request_summary", "ownership", "history_window", "sources_used",
        "sources_skipped", "sources_unavailable", "linked_artifacts_inspected",
        "tone_samples", "evidence_count", "evidence_cap", "draft_check",
        "task_search", "remaining_uncertainty",
    }
    missing = sorted(required - set(packet))
    if missing:
        _die(2, "missing_context_packet_fields", fields=missing)
    candidate_id = _validate_candidate_id(packet.get("candidate_id"))
    run_id = _required_string(packet.get("run_id"), "run_id")
    task_search = _validate_task_search(packet.get("task_search"))

    saved = {"schema": 1, "candidate_id": candidate_id, "run_id": run_id}
    for field in ("message_id", "thread_id", "request_summary", "ownership"):
        saved[field] = _required_string(packet.get(field), field, maximum=1000)
    saved["gmail_url"] = _validated_gmail_url(packet.get("gmail_url"))
    saved["history_window"] = _validate_history_window(packet.get("history_window"))

    saved["sources_used"] = _validate_context_list(
        packet,
        "sources_used",
        {"source": 100, "pointer": 2000, "time_range": 1000, "finding": 1000},
        required_fields=("source", "pointer"),
    )
    saved["sources_skipped"] = _validate_context_list(
        packet,
        "sources_skipped",
        {"source": 100, "reason": 1000},
        required_fields=("source", "reason"),
    )
    saved["sources_unavailable"] = _validate_context_list(
        packet,
        "sources_unavailable",
        {"source": 100, "reason": 1000},
        required_fields=("source", "reason"),
    )
    saved["linked_artifacts_inspected"] = _validate_context_list(
        packet,
        "linked_artifacts_inspected",
        {"pointer": 2000, "inspected": 1000},
        required_fields=("pointer", "inspected"),
    )
    saved["remaining_uncertainty"] = _validate_context_string_list(packet, "remaining_uncertainty")

    tone_samples = packet["tone_samples"]
    if not isinstance(tone_samples, int) or isinstance(tone_samples, bool) or not 0 <= tone_samples <= 5:
        _die(2, "bad_context_packet_field", field="tone_samples")
    saved["tone_samples"] = tone_samples
    evidence_count = packet["evidence_count"]
    if not isinstance(evidence_count, int) or isinstance(evidence_count, bool) or not 0 <= evidence_count <= 25:
        _die(2, "bad_context_packet_field", field="evidence_count")
    evidence_cap = packet["evidence_cap"]
    if evidence_cap not in (15, 25):
        _die(2, "bad_context_packet_field", field="evidence_cap")
    retained = len(saved["sources_used"]) + len(saved["linked_artifacts_inspected"])
    if evidence_count != retained:
        _die(2, "evidence_count_mismatch", declared=evidence_count, retained=retained)
    if evidence_count > evidence_cap:
        _die(2, "context_evidence_cap_exceeded", declared=evidence_count, cap=evidence_cap)
    saved["evidence_count"] = evidence_count
    saved["evidence_cap"] = evidence_cap
    if evidence_cap == 25:
        saved["evidence_cap_reason"] = _required_string(
            packet.get("evidence_cap_reason"), "evidence_cap_reason", maximum=1000
        )
    elif packet.get("evidence_cap_reason") is not None:
        _die(2, "unexpected_evidence_cap_reason")

    represented = {
        item["source"] for item in saved["sources_used"] + saved["sources_unavailable"]
    }
    for source in ("current_thread", "email_history"):
        if source not in represented:
            _die(2, "missing_required_source", source=source)

    saved["draft_check"] = _validate_draft_check(packet.get("draft_check"))
    saved["task_search"] = task_search

    saved["saved_at"] = _now()
    path = _context_path(home, candidate_id)
    _write_json(path, saved)
    pointer = os.path.relpath(path, home)
    record = state["reply_records"].setdefault(candidate_id, {})
    record["context_packet"] = pointer
    record["context_saved_at"] = saved["saved_at"]
    _write_json(_state_path(home), state)
    print(json.dumps({"ok": True, "candidate_id": candidate_id, "context_packet": pointer}))


def cmd_record_reply(home, args):
    state = _require_state(home)
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "reply_record_not_object")
    allowed = {
        "candidate_id", "run_id", "outcome", "reason", "needs_attention", "disposition",
        "plugin_draft_id", "body_hash", "message_id", "thread_id", "task_search",
        "provider_response_id", "draft_url", "email_url", "update_authorization",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        _die(2, "unknown_reply_record_fields", fields=unknown)
    candidate_id = _validate_candidate_id(payload.get("candidate_id"))
    outcome = payload.get("outcome")
    if outcome not in OUTCOMES:
        _die(2, "bad_reply_outcome", value=outcome)
    if outcome in {"draft_created", "draft_updated"} and state["config"].get("draft_mode") != "auto":
        _die(2, "draft_mode_off", draft_mode=state["config"].get("draft_mode"))
    for field in ("plugin_draft_id", "body_hash", "message_id", "thread_id", "run_id"):
        if field in payload and payload[field] is not None and not isinstance(payload[field], str):
            _die(2, "bad_reply_record_field", field=field)
    previous = state["reply_records"].get(candidate_id, {})
    packet = None
    if outcome in {"draft_created", "draft_updated", "draft_blocked"}:
        run_id = _required_string(payload.get("run_id"), "run_id")
        packet = _context_for_run(
            home, previous, candidate_id, run_id,
            grounded=outcome in {"draft_created", "draft_updated"},
        )
        for field in ("message_id", "thread_id"):
            if _required_string(payload.get(field), field) != packet.get(field):
                _die(2, "reply_context_mismatch", field=field)
        if _validated_gmail_url(payload.get("email_url"), "email_url") != packet.get("gmail_url"):
            _die(2, "reply_context_mismatch", field="email_url")
    if outcome in {"draft_created", "draft_updated"}:
        if not payload.get("plugin_draft_id") or not payload.get("body_hash"):
            _die(2, "missing_draft_provenance")
        _required_string(payload.get("provider_response_id"), "provider_response_id")
        _validated_http_url(payload.get("draft_url"), "draft_url")
        if (outcome == "draft_created" and previous.get("outcome") == outcome and
                previous.get("provider_response_id") == payload.get("provider_response_id")):
            print(json.dumps({"ok": True, "idempotent": True,
                              "candidate_id": candidate_id, "record": previous}, indent=2))
            return
    if outcome == "draft_created" and packet.get("draft_check", {}).get("result") != "no_existing_draft":
        _die(2, "draft_create_requires_no_existing_draft")
    if outcome == "draft_updated":
        if packet.get("draft_check", {}).get("result") != "update_allowed":
            _die(2, "draft_update_requires_check")
        authorization = previous.get("draft_update_authorization")
        submitted_authorization = _required_string(
            payload.get("update_authorization"), "update_authorization"
        )
        if (not isinstance(authorization, dict) or
                authorization.get("token") != submitted_authorization):
            _die(2, "draft_update_requires_authorization")
        checked_at = _iso(authorization.get("checked_at"))
        if checked_at is None or datetime.now(timezone.utc) - checked_at > timedelta(minutes=10):
            _die(2, "draft_update_authorization_expired")
        if authorization.get("draft_id") != payload.get("plugin_draft_id"):
            _die(2, "draft_update_authorization_mismatch")
    if outcome in {"draft_preserved", "draft_blocked", "no_draft"}:
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            _die(2, "missing_reply_reason")
    if "needs_attention" in payload and not isinstance(payload["needs_attention"], bool):
        _die(2, "bad_needs_attention")
    if outcome == "draft_preserved" and payload.get("needs_attention") is not True:
        _die(2, "preserved_draft_requires_attention")
    if outcome == "draft_preserved":
        _required_string(payload.get("message_id"), "message_id")
        _required_string(payload.get("thread_id"), "thread_id")
        _validated_gmail_url(payload.get("email_url"), "email_url")
    if outcome == "no_draft" and payload.get("needs_attention") is True:
        _required_string(payload.get("thread_id"), "thread_id")
        _validated_gmail_url(payload.get("email_url"), "email_url")
    for field in ("reason", "disposition", "provider_response_id", "draft_url", "email_url"):
        if field in payload:
            maximum = 2000 if field.endswith("url") else 1000
            _bounded_optional_string(payload[field], field, maximum=maximum)
    task_search = (_validate_task_search(payload["task_search"])
                   if "task_search" in payload
                   else (packet.get("task_search") if packet else
                         previous.get("task_search", {"status": "not_searched"})))
    if packet is not None and task_search != packet.get("task_search"):
        _die(2, "reply_task_search_mismatch")

    record = dict(previous)
    for key, value in payload.items():
        if key not in {"candidate_id", "recorded_at"}:
            record[key] = value
    record["outcome"] = outcome
    # Only draft_created/draft_updated append a ledger row, so the ledger alone cannot
    # count the three outcomes that exist to make failure visible. Stamp the run here and
    # `run-counts` can tally every outcome, not just the two that wrote to the ledger.
    if record.get("run_id") is None and state.get("active_run") is not None:
        record["run_id"] = state["active_run"]
    record["task_search"] = task_search
    record["recorded_at"] = _now()
    record.pop("draft_update_authorization", None)
    if outcome in {"draft_created", "draft_updated"}:
        ledger_entry = {
            "message_id": payload["message_id"],
            "thread_id": payload["thread_id"],
            "candidate_id": candidate_id,
            "action": "draft",
            "run_id": run_id,
            "outcome": outcome,
            "plugin_draft_id": payload["plugin_draft_id"],
            "provider_response_id": payload["provider_response_id"],
            "draft_url": payload["draft_url"],
            "email_url": payload["email_url"],
            "recorded_at": record["recorded_at"],
        }
        try:
            with open(_ledger_path(home), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(ledger_entry) + "\n")
        except OSError as exc:
            _die(3, "ledger_write_failed", detail=str(exc))
        state["stats"]["drafted"] += 1
    state["reply_records"][candidate_id] = record
    _write_json(_state_path(home), state)
    print(json.dumps({"ok": True, "candidate_id": candidate_id, "record": record}, indent=2))


def cmd_record_task(home, args):
    state = _require_state(home)
    if state["config"].get("task_mode") != "create":
        _die(2, "task_create_not_consented")
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "task_record_not_object")
    allowed = {
        "candidate_id", "run_id", "provider_task_id", "provider_response_id",
        "task_url", "task_search",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        _die(2, "unknown_task_record_fields", fields=unknown)
    candidate_id = _validate_candidate_id(payload.get("candidate_id"))
    run_id = _required_string(payload.get("run_id"), "run_id")
    record = state["reply_records"].get(candidate_id, {})
    packet = _context_for_run(home, record, candidate_id, run_id)
    if "tasks" not in {item.get("source") for item in packet.get("sources_used", [])}:
        _die(2, "task_create_requires_task_source_evidence")
    task_search = _validate_task_search(payload.get("task_search"))
    if task_search.get("status") != "no_match" or packet.get("task_search") != task_search:
        _die(2, "task_create_requires_no_match_search")
    provider_task_id = _required_string(payload.get("provider_task_id"), "provider_task_id")
    provider_response_id = _required_string(
        payload.get("provider_response_id"), "provider_response_id"
    )
    task_url = _validated_http_url(payload.get("task_url"), "task_url")
    existing = record.get("task")
    if isinstance(existing, dict):
        if existing.get("provider_task_id") == provider_task_id:
            print(json.dumps({"ok": True, "idempotent": True, "task": existing}, indent=2))
            return
        _die(2, "task_already_recorded", candidate_id=candidate_id)

    created_at = _now()
    task = {
        "provider_task_id": provider_task_id,
        "provider_response_id": provider_response_id,
        "task_url": task_url,
        "task_search": task_search,
        "run_id": run_id,
        "recorded_at": created_at,
    }
    record["task"] = task
    state["reply_records"][candidate_id] = record
    state["stats"]["tasks_created"] += 1
    ledger_entry = {
        "message_id": packet["message_id"],
        "thread_id": packet["thread_id"],
        "candidate_id": candidate_id,
        "action": "task",
        "run_id": run_id,
        "provider_task_id": provider_task_id,
        "provider_response_id": provider_response_id,
        "task_url": task_url,
        "recorded_at": created_at,
    }
    try:
        with open(_ledger_path(home), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(ledger_entry) + "\n")
    except OSError as exc:
        _die(3, "ledger_write_failed", detail=str(exc))
    _write_json(_state_path(home), state)
    print(json.dumps({"ok": True, "idempotent": False, "task": task}, indent=2))


def cmd_check_draft(home, args):
    state = _require_state(home)
    payload = _stdin_json()
    if not isinstance(payload, dict):
        _die(2, "draft_check_not_object")
    required = ("candidate_id", "current_draft_id", "current_body_hash")
    for field in required:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            _die(2, "bad_draft_check_field", field=field)
    candidate_id = _validate_candidate_id(payload["candidate_id"])
    record = state["reply_records"].setdefault(candidate_id, {})
    plugin_draft_id = record.get("plugin_draft_id")
    recorded_body_hash = record.get("body_hash")
    same_draft = isinstance(plugin_draft_id, str) and plugin_draft_id == payload["current_draft_id"]
    same_body = isinstance(recorded_body_hash, str) and recorded_body_hash == payload["current_body_hash"]
    untouched = same_draft and same_body
    response = {
        "ok": True,
        "untouched": untouched,
        "update_allowed": untouched,
        "decision": "update_allowed" if untouched else "preserve_existing",
        "same_draft": same_draft,
        "same_body_hash": same_body,
    }
    if untouched:
        checked_at = _now()
        authorization_digest = hashlib.sha256(
            "\0".join((candidate_id, plugin_draft_id, recorded_body_hash, checked_at)).encode("utf-8")
        ).hexdigest()
        record["draft_update_authorization"] = {
            "token": authorization_digest,
            "draft_id": plugin_draft_id,
            "recorded_body_hash": recorded_body_hash,
            "checked_at": checked_at,
        }
        response["update_authorization"] = token
    else:
        record.pop("draft_update_authorization", None)
    _write_json(_state_path(home), state)
    print(json.dumps(response))


def _upgrade_gate_missing(state):
    """Setup's exit gate, as data. Returns the fields that are still unset or invalid.

    The marker used to clear on `draft_mode` alone, which let a home sit half
    configured — null transport/scope, no escalation decision — with a verified
    schedule running against it.
    """
    config = state.get("config", {})
    missing = []
    if state.get("schema") != CURRENT_SCHEMA:
        missing.append("schema")
    if config.get("write_level") != 3:
        missing.append("config.write_level")
    if config.get("draft_mode") not in {"auto", "off"}:
        missing.append("config.draft_mode")
    if config.get("scope") not in {"primary", "all"}:
        missing.append("config.scope")
    if config.get("transport") not in {"cli", "mcp"}:
        missing.append("config.transport")
    if config.get("task_mode") not in TASK_MODES:
        missing.append("config.task_mode")
    limit = config.get("reply_candidate_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        missing.append("config.reply_candidate_limit")

    sources = config.get("context_sources")
    if not isinstance(sources, dict):
        missing.append("config.context_sources")
    else:
        if not _source_setting_valid("email_history", sources.get("email_history")):
            missing.append("config.context_sources.email_history")
        for name in ("calendar", "meeting_notes", "tasks", "slack"):
            if name not in sources:
                missing.append("config.context_sources." + name)

    archive_enabled = config.get("archive_enabled")
    if not isinstance(archive_enabled, bool):
        missing.append("config.archive_enabled")
        archive_enabled = False
    # Labels only have to resolve when the run may actually write them; a home that
    # honestly degraded to read-only is complete without them.
    undo_label = config.get("undo_label")
    if not isinstance(undo_label, str) or not undo_label.strip():
        missing.append("config.undo_label")
    if archive_enabled:
        for field in ("undo_label_id", "urgent_label_id"):
            value = config.get(field)
            if not isinstance(value, str) or not value.strip():
                missing.append("config." + field)
    urgent_label = config.get("urgent_label")
    if not isinstance(urgent_label, str) or not urgent_label.strip():
        missing.append("config.urgent_label")

    # Declining a channel is a decision; never having been asked is not.
    if _iso(config.get("escalation_channel_decided_at")) is None:
        missing.append("config.escalation_channel_decided_at")
    channel = config.get("escalation_channel")
    if channel is not None and not _escalation_channel_complete(channel):
        missing.append("config.escalation_channel")
    return missing


def cmd_complete_upgrade(home, args):
    state = _require_state(home)
    missing = _upgrade_gate_missing(state)
    if missing:
        _die(2, "upgrade_gate_incomplete", missing=missing)
    state["upgrade_pending"] = False
    _write_json(_state_path(home), state)
    print(json.dumps({"ok": True, "upgrade_pending": False, "validated": True}))


def cmd_run_counts(home, args):
    """Per-run tallies straight off the ledger and manifest.

    The run report is the only artifact most students read, and a model recounting
    its own pass from memory has already disagreed with the ledger it wrote. This is
    the count; the model narrates it, it does not re-derive it.
    """
    state = _require_state(home)
    run_id = args.run_id or state.get("active_run")
    if run_id is None:
        completed = []
        runs_dir = os.path.join(home, RUNS_DIR)
        for name in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
            if not name.endswith(".json"):
                continue
            manifest = _read_json(os.path.join(runs_dir, name), "run_manifest_unreadable")
            if isinstance(manifest, dict) and manifest.get("completed_at"):
                completed.append((manifest["completed_at"], manifest.get("run_id")))
        if not completed:
            _die(2, "no_run_to_count", home=home)
        run_id = sorted(completed)[-1][1]
    manifest = _read_manifest(home, run_id)

    counts = {"archive": 0, "keep": 0, "escalate": 0, "draft": 0, "task": 0}
    for entry in _read_ledger(home):
        if entry.get("run_id") != run_id:
            continue
        action = entry.get("action")
        if action == "kept":
            action = "keep"
        if action in counts:
            counts[action] += 1

    # Draft outcomes come from the reply records, not the ledger: `draft_blocked`,
    # `draft_preserved`, and `no_draft` never write a ledger row, and those are exactly
    # the three the report's Blocked drafts and Needs attention queues are built from.
    draft_outcomes = {outcome: 0 for outcome in OUTCOMES}
    needs_attention = 0
    for record in state.get("reply_records", {}).values():
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            continue
        if record.get("outcome") in draft_outcomes:
            draft_outcomes[record["outcome"]] += 1
        if record.get("needs_attention") is True:
            needs_attention += 1

    entries = [e for e in manifest["entries"] if isinstance(e, dict)]
    open_questions, question_basis = _open_question_count(home)
    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "status": manifest.get("status", "active" if state.get("active_run") == run_id else "open"),
        "listing_complete": manifest.get("listing_complete"),
        "selected": len(entries),
        "cap_deferred": len(manifest.get("deferred", [])),
        "terminal": sum(1 for e in entries if e.get("status") == "terminal"),
        "run_deferred": sum(1 for e in entries if e.get("status") == "deferred"),
        "pending": sum(1 for e in entries if e.get("status") == "pending"),
        "counts": counts,
        "draft_outcomes": draft_outcomes,
        "needs_attention": needs_attention,
        "processed": counts["archive"] + counts["keep"] + counts["escalate"],
        "retry_set_size": len(state["retry_set"]),
        "open_question_count": open_questions,
        "open_question_basis": question_basis,
        "watermark": state.get("watermark"),
        "recovery": state["recovery"],
    }, indent=2))


def cmd_validate_doctrine(home, args):
    """Flag structural problems and rules that command a capability we do not have.

    It flags; it never edits. `doctrine.md` is the student's file, and the rewrite of
    a rule commanding (say) auto-unsubscribe is their call, not the rail's.
    """
    _require_state(home)
    path = os.path.join(home, DOCTRINE)
    if not os.path.isfile(path):
        print(json.dumps({"ok": False, "error": "doctrine_missing", "path": path}, indent=2))
        return
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read().splitlines()
    except OSError as exc:
        _die(3, "doctrine_unreadable", path=path, detail=str(exc))

    findings = []
    seen_buckets = []
    current = None
    inside_fence = False
    for number, line in enumerate(raw, start=1):
        if FENCE.match(line):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1)
            current = next((b for b in DOCTRINE_BUCKETS if title.lower().startswith(b.lower())), None)
            if current is None:
                findings.append({"line": number, "issue": "unknown_section", "text": title})
            elif current in seen_buckets:
                findings.append({"line": number, "issue": "duplicate_section", "text": title})
            else:
                seen_buckets.append(current)
            continue
        if not BULLET.match(line):
            continue
        if current is None:
            findings.append({"line": number, "issue": "rule_outside_bucket",
                             "text": line.strip()[:300]})
        for capability, pattern in DROPPED_CAPABILITIES:
            if pattern.search(line):
                findings.append({
                    "line": number,
                    "issue": "dropped_capability",
                    "capability": capability,
                    "bucket": current,
                    "text": line.strip()[:300],
                })

    for bucket in DOCTRINE_BUCKETS:
        # Suppress is confirmed-only, so a doctrine that has never had a confirmed
        # suppression correctly has no such section. Its absence is not a finding.
        if bucket not in seen_buckets and bucket != "Suppress":
            findings.append({"issue": "missing_section", "text": bucket})
    print(json.dumps({
        "ok": not findings,
        "path": path,
        "sections": seen_buckets,
        "findings": findings,
        "dropped_capability_count": sum(
            1 for f in findings if f["issue"] == "dropped_capability"),
    }, indent=2))


def cmd_undo_window(home, args):
    _require_state(home)
    since = _iso(args.since)
    hits = []
    for entry in _read_ledger(home):
        if entry.get("action") != "archive":
            continue
        recorded = _iso(entry.get("recorded_at"))
        if since is None or (recorded and recorded >= since):
            hits.append(entry)
    print(json.dumps({"since": args.since, "archived": hits, "count": len(hits)}, indent=2))


def _ledger_labels(entry):
    names = entry.get("labels")
    ids = entry.get("label_ids")
    if names is None:
        names = [entry["label"]] if isinstance(entry.get("label"), str) else []
    if ids is None:
        ids = [entry["label_id"]] if isinstance(entry.get("label_id"), str) else []
    if not isinstance(names, list) or not all(isinstance(value, str) for value in names):
        names = []
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        ids = []
    return names, ids


def cmd_find_corrections(home, args):
    _require_state(home)
    current = _stdin_json()
    if not isinstance(current, list):
        _die(2, "current_inbox_not_list")
    visible = {}
    for message in current:
        if not isinstance(message, dict):
            _die(2, "malformed_current_inbox_item")
        message_id = message.get("message_id") or message.get("id")
        if not isinstance(message_id, str) or not message_id:
            _die(2, "malformed_current_inbox_item")
        labels = message.get("label_ids")
        if labels is None:
            labels = message.get("labelIds", [])
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            _die(2, "malformed_current_inbox_item")
        visible[message_id] = set(labels)
    last = {}
    for entry in _read_ledger(home):
        if entry.get("message_id"):
            last[entry["message_id"]] = entry
    rescued, unlabeled = [], []
    for message_id, entry in last.items():
        if message_id not in visible:
            continue
        labels = visible[message_id]
        if entry.get("action") == "archive" and "INBOX" in labels:
            rescued.append({"message_id": message_id, "sender": entry.get("sender"),
                            "subject": entry.get("subject"), "was": "archived"})
        applied_names, applied_ids = _ledger_labels(entry)
        if entry.get("action") in ("archive", "keep", "kept"):
            for index, applied in enumerate(applied_ids):
                if applied not in labels:
                    name = applied_names[index] if index < len(applied_names) else applied
                    unlabeled.append({
                        "message_id": message_id,
                        "sender": entry.get("sender"),
                        "subject": entry.get("subject"),
                        "removed_label": name,
                    })
    print(json.dumps({
        "rescued_from_archive": rescued,
        "label_removed": unlabeled,
        "total_candidates": len(rescued) + len(unlabeled),
    }, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="inbox-manager state rail")
    subcommands = parser.add_subparsers(dest="cmd", required=True)
    names = (
        "init", "read", "retry-set", "begin-run", "record", "complete-run", "set-config",
        "undo-window", "find-corrections", "run-counts", "validate-doctrine",
        "queue-replies", "dequeue-replies", "upsert-relationship", "save-context",
        "record-reply", "record-task", "check-draft", "complete-upgrade",
    )
    for name in names:
        command = subcommands.add_parser(name)
        command.add_argument("--home", required=True)
        if name == "init":
            command.add_argument("--inbox-name", default=None)
        if name == "undo-window":
            command.add_argument("--since", default=None)
        if name == "retry-set":
            command.add_argument("--limit", type=int, default=200)
        if name == "run-counts":
            command.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    dispatch = {
        "init": cmd_init,
        "read": cmd_read,
        "retry-set": cmd_retry_set,
        "run-counts": cmd_run_counts,
        "validate-doctrine": cmd_validate_doctrine,
        "begin-run": cmd_begin_run,
        "record": cmd_record,
        "complete-run": cmd_complete_run,
        "set-config": cmd_set_config,
        "undo-window": cmd_undo_window,
        "find-corrections": cmd_find_corrections,
        "queue-replies": cmd_queue_replies,
        "dequeue-replies": cmd_dequeue_replies,
        "upsert-relationship": cmd_upsert_relationship,
        "save-context": cmd_save_context,
        "record-reply": cmd_record_reply,
        "record-task": cmd_record_task,
        "check-draft": cmd_check_draft,
        "complete-upgrade": cmd_complete_upgrade,
    }
    with _legacy_engine_fence(args.home):
        dispatch[args.cmd](args.home, args)


if __name__ == "__main__":
    main()
