#!/usr/bin/env python3
"""Inbox Manager conversation/action rail. Python stdlib; no network calls.

The host supplies observed provider facts and semantic judgments. This rail binds
their versions, serializes concurrent writers, preserves human ownership, and
records an irreversible intent BEFORE the host calls Gmail. It is not a tool
sandbox, an owner authenticator, or a promise of exactly-once email delivery.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import inbox_priority
import inbox_store
import inbox_archive
from inbox_identity import normalize_responsibility_type
from inbox_views import (action_table, bot_work, compact_due, compact_report, compact_work,
                         lookup, owner_report, owner_work, summary_case, verified_url)


DB = "inbox-engine.sqlite3"
SCHEMA = 2
STATES = {"needs_action", "waiting_correspondent", "waiting_owner", "handled"}
UNRESOLVED_SEND = {"attempting", "unknown"}
EMAIL = re.compile(r"^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$")
PLACEHOLDER = re.compile(r"\{\{.+?\}\}|\[(?:TODO|INSERT|TBD|YOUR |NAME|LINK|DATE)[^\]]*\]|\b(?:TODO|TBD)\b", re.I)


def utcnow():
    return datetime.now(timezone.utc)


def stamp():
    return utcnow().isoformat()


def iso(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise ValueError("timestamp must include timezone") from None


def text(value, field="value", maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid " + field)
    return value


def number(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid " + field)
    return value


def addresses(value, empty=False):
    if not isinstance(value, list) or (not value and not empty) or len(value) > 20:
        raise ValueError("invalid recipients")
    if not all(isinstance(v, str) and EMAIL.fullmatch(v) for v in value):
        raise ValueError("use bare verified email addresses")
    return sorted(set(v.lower() for v in value))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def fresh(value, seconds):
    age = (utcnow() - iso(value)).total_seconds()
    return -5 <= age <= seconds


def owned(payload):
    # The authenticated host establishes this; arbitrary email is never a caller.
    if payload.get("owner_confirmed") is not True:
        raise ValueError("authenticated owner decision required")
    return text(payload.get("approval_ref"), "approval_ref")


def initial(mailbox):
    return {"schema": SCHEMA, "mailbox": mailbox, "active": False, "paused": False,
            "version": 0, "config": None, "sources": {}, "cases": {}, "candidates": {},
            "checks": {}, "actions": {}, "drafts": {}, "observations": {}, "lessons": {},
            "lease": None, "runs": {}, "events": [], "queue": {}, "scan": None, "inbox_scans": {}, "watermark": None,
            "member_sends": {}, "seen_messages": {}, "decisions": {}, "owner_events": {},
            "notifications": {}, "reminders": {}, "knowledge_gaps": {}, "reevaluations": {}}


@contextmanager
def transaction(home, create=False, readonly=False):
    root = Path(home).expanduser().resolve()
    if not create and not (root / DB).is_file():
        raise ValueError("inbox engine not initialized; run setup")
    if not readonly:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / DB
    if path.is_symlink():
        raise ValueError("state database must not be a symlink")
    db = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else path,
                         uri=readonly, timeout=15, isolation_level=None)
    try:
        # A consistent local backup precedes the first schema-2 write. No secrets
        # leave the inbox home; backups are never overwritten.
        if not readonly:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='engine'").fetchone()
            previous = db.execute("SELECT data FROM engine WHERE id=1").fetchone() if exists else None
            if previous and json.loads(previous[0]).get("storage_version") != inbox_store.VERSION:
                backup = root / ("migration-" + hashlib.sha256(previous[0].encode()).hexdigest()[:16] + ".sqlite3")
                if not backup.exists():
                    import os
                    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(fd)
                    try:
                        with sqlite3.connect(backup) as destination:
                            db.backup(destination)
                    except BaseException:
                        backup.unlink(missing_ok=True)
                        raise
        if readonly:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
        else:
            path.chmod(0o600)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS engine (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        row = db.execute("SELECT data FROM engine WHERE id=1").fetchone()
        state = inbox_store.load(db, path, json.loads(row[0])) if row else None
        if state is not None and state.get("schema") not in {1, SCHEMA}:
            raise ValueError("unsupported engine schema")
        if state is not None:
            state["schema"] = SCHEMA
            # Additive upgrade: existing cases, authority and provider history survive.
            for field in ("decisions", "owner_events", "notifications", "reminders", "knowledge_gaps", "reevaluations", "inbox_scans"):
                state.setdefault(field, {})
        box = [state]
        yield root, box
        if not readonly and box[0] is not None:
            inbox_store.save(db, path, box[0])
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def event(state, kind, **fields):
    state["events"].append({"kind": kind, "at": stamp(), **fields})


def lease(state, payload):
    current = state["lease"]
    if (not current or current["token"] != payload.get("token") or
            current["run_id"] != payload.get("run_id") or iso(current["expires_at"]) <= utcnow()):
        raise ValueError("current run lease required")
    if state["paused"]:
        raise ValueError("inbox paused")
    return current


def get_case(state, payload):
    key = text(payload.get("case_id"), "case_id")
    if key not in state["cases"]:
        raise ValueError("unknown case")
    return state["cases"][key]


def get_candidate(state, payload):
    key = text(payload.get("candidate_id"), "candidate_id")
    if key not in state["candidates"]:
        raise ValueError("unknown candidate")
    return state["candidates"][key]


def snapshot(value):
    if not isinstance(value, dict) or value.get("complete") is not True:
        raise ValueError("complete thread and draft snapshot required")
    for field in ("thread_id", "trigger_id", "fingerprint", "observed_at"):
        text(value.get(field), field)
    iso(value["observed_at"])
    if not isinstance(value.get("draft_ids"), list) or not all(isinstance(x, str) for x in value["draft_ids"]):
        raise ValueError("complete draft ids required")
    message_ids = value.get("message_ids")
    if (not isinstance(message_ids, list) or not all(isinstance(x, str) and x for x in message_ids) or
            value["trigger_id"] not in message_ids):
        raise ValueError("complete thread message ids must include trigger")
    # Self-addressed notes/notifications may have no external correspondents.
    # This validates observed participants, not a reply recipient list.
    addresses(value.get("correspondents"), empty=True)
    if type(value.get("automated")) is not bool:
        raise ValueError("automated-message assessment required")
    return value


def validate_profile(root, files):
    if not isinstance(files, dict) or not {"job.md", "instructions.md", "voice.md"} <= set(files):
        raise ValueError("job.md, instructions.md and voice.md hashes required")
    for relative, expected in files.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("profile file must be inside this inbox home")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("profile file changed: " + relative)


def configure(root, state, p):
    approval = owned(p)
    if p.get("base_version") != state["version"]:
        raise ValueError("configuration changed; reload before approval")
    config = p.get("config")
    if not isinstance(config, dict):
        raise ValueError("config required")
    text(config.get("job"), "job", 10000)
    validate_profile(root, config.get("profile_files"))
    chat_output = config.get("chat_output", "full")
    if chat_output not in {"full", "profile"}:
        raise ValueError("chat_output must be full or profile")
    if chat_output == "profile" and "chat-preferences.md" not in config["profile_files"]:
        raise ValueError("profile chat output requires approved chat-preferences.md hash")
    rules = config.get("rules")
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("explicit rules list required (may be empty for drafts only)")
    ids = set()
    for rule in rules:
        key = text(rule.get("id"), "rule id")
        if key in ids:
            raise ValueError("duplicate rule")
        ids.add(key)
        text(rule.get("description"), "rule description", 10000)
        if rule.get("recipient_mode") not in {"correspondents", "allowlist"}:
            raise ValueError("rule recipient_mode required")
        if rule["recipient_mode"] == "allowlist":
            rule["recipients"] = addresses(rule.get("recipients"))
        if type(rule.get("allow_attachments")) is not bool:
            raise ValueError("rule attachment choice required")
    limits = config.get("limits", {})
    number(limits.get("per_run"), 1, 50, "per_run")
    number(limits.get("per_day"), 1, 500, "per_day")
    followup = config.get("followups", {})
    if type(followup.get("enabled")) is not bool:
        raise ValueError("followup decision required")
    number(followup.get("max_attempts"), 0, 5, "max_attempts")
    number(followup.get("delay_hours"), 1, 720, "delay_hours")
    if followup["enabled"] and followup["max_attempts"] == 0:
        raise ValueError("enabled followups need a finite nonzero limit")
    transport = config.get("transport", {})
    if transport.get("mailbox") != state["mailbox"]:
        raise ValueError("transport mailbox mismatch")
    text(transport.get("card_ref"), "card_ref")
    for field in ("send", "drafts", "independent_check"):
        if type(transport.get(field)) is not bool:
            raise ValueError("explicit transport capability required: " + field)
    iso(transport.get("proven_at"))
    number(transport.get("snapshot_max_age_seconds"), 1, 300, "snapshot_max_age_seconds")
    policy = config.get("source_policy")
    if not isinstance(policy, dict):
        raise ValueError("source_policy registry required")
    config.setdefault("context_access", "workspace")
    if config["context_access"] not in {"workspace", "listed_only"}:
        raise ValueError("context_access must be workspace or listed_only")
    for key, value in policy.items():
        text(key, "source id")
        for field in ("origin", "scope", "use"):
            text(value.get(field), "source " + field, 10000)
    if 'draft_first' in config and type(config['draft_first']) is not bool:
        raise ValueError('draft_first must be a boolean')
    guide = config.get('research_guide', [])
    if not isinstance(guide, list) or len(guide) > 30:
        raise ValueError('research_guide must be a bounded source guide')
    for entry in guide:
        if not isinstance(entry, dict) or not entry.get('source_id') or not entry.get('purpose'):
            raise ValueError('research guide entries need source_id and purpose')
        if entry['source_id'] != 'current_thread' and entry['source_id'] not in policy:
            raise ValueError('research guide must reference approved sources')
    notifications = config.setdefault("notifications", {"destination_ref": "current-task", "reminder_hours": 24})
    notifications.setdefault("reminder_hours", 24)
    text(notifications.get("destination_ref"), "notification destination")
    number(notifications.get("reminder_hours"), 1, 720, "notification reminder hours")
    digest_fields = ("digest_hour", "timezone")
    if any(field in notifications for field in digest_fields):
        if not all(field in notifications for field in digest_fields):
            raise ValueError("notification digest_hour and timezone must be configured together")
        number(notifications["digest_hour"], 0, 23, "notification digest hour")
        zone_name = text(notifications["timezone"], "notification timezone", 200)
        try:
            ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            raise ValueError("invalid notification timezone") from None
    learning = config.get("learning", {})
    learning.setdefault("invalidated", False)
    learning.setdefault("exclusive_editor_confirmed", False)
    if type(learning.get("enabled")) is not bool:
        raise ValueError("learning decision required")
    number(learning.get("retention_days"), 1, 365, "retention_days")
    if learning.get("actor_mode") not in {"verified", "exclusive_editor", "observe_only"}:
        raise ValueError("learning actor capability required")
    if learning["actor_mode"] == "exclusive_editor" and learning.get("exclusive_editor_confirmed") is not True:
        raise ValueError("owner must confirm exclusive-editor assumption")
    config["inbox_priority"] = inbox_priority.policy(config.get("inbox_priority"))
    aliases = config.get("owner_aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(v, str) or not v.strip() for v in aliases):
        raise ValueError("owner_aliases must be confirmed nonempty names")
    config["cleanup"] = inbox_archive.policy(config.get("cleanup"))
    if (state.get("config") or {}).get("inbox_priority") != config.get("inbox_priority"):
        state.setdefault("scope_history", []).append(state["inbox_scans"])
        for lane, scan in state["inbox_scans"].items():
            for mid in scan.get("message_ids", []):
                tid = state["seen_messages"].get(mid) or state["queue"].get(mid, {}).get("thread_id")
                if tid:
                    state.setdefault("thread_scope", {}).setdefault(tid, {"lane": lane,
                        "query": scan.get("query"), "window_end": scan.get("window_end"),
                        "observed_at": scan.get("at"), "evidence": scan.get("provider_evidence")})
        state["inbox_scans"] = {}
    old_cleanup = (state.get("config") or {}).get("cleanup")
    if old_cleanup != config.get("cleanup"):
        for case in state["cases"].values():
            if case.get("state") == "handled":
                case.setdefault("cleanup", {})["status"] = "pending"
    state["version"] += 1
    state["config"] = config
    state["active"] = True
    # Approval versions are conservative: changed instructions require a fresh check.
    for lesson in state["lessons"].values():
        if lesson.get("status") == "active":
            lesson["status"] = "invalidated"
    event(state, "configured", version=state["version"], approval_ref=approval)
    return {"version": state["version"], "active": True}


def source_ids(state, value):
    if not isinstance(value, list) or len(value) > 100 or not all(isinstance(x, str) for x in value):
        raise ValueError("source_ids must be a bounded list")
    if any(source_policy(state, x) is None for x in value):
        raise ValueError("source dependency is not approved")
    return sorted(set(value))


def source_policy(state, key):
    config = state.get("config") or {}
    if key in config.get("source_policy", {}):
        return config["source_policy"][key]
    item = state.get("context_sources", {}).get(key)
    if config.get("context_access") == "workspace" and item and item["profile_version"] == state["version"]:
        return item
    return None


def register_context_source(state, p):
    lease(state, p)
    if (state.get("config") or {}).get("context_access") != "workspace":
        raise ValueError("this profile only permits listed sources")
    key = text(p.get("source_id"), "source_id")
    if key in state["config"]["source_policy"]:
        raise ValueError("configured source already exists; use source")
    item = {field: text(p.get(field), field, 10000) for field in ("origin", "scope", "use", "access_evidence")}
    item["profile_version"] = state["version"]
    prior = state.get("context_sources", {}).get(key)
    if prior and prior["origin"] != item["origin"]:
        raise ValueError("source id cannot change origin")
    # Host must establish workspace read authority; email text cannot grant it.
    state.setdefault("context_sources", {})[key] = item
    return {"source_id": key, **item}


def update_obligations(state, case, rows):
    """Patch stable obligations; omission never deletes an outstanding commitment."""
    if not isinstance(rows, list) or not rows or len(rows) > 50:
        raise ValueError("obligations must be a nonempty bounded list")
    items = case.setdefault("obligations", {})
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("obligation must be an object")
        key = text(row.get("id"), "obligation id", 200)
        if key in seen:
            raise ValueError("duplicate obligation id")
        seen.add(key)
        prior = items.get(key, {})
        item = {"id": key}
        for field in ("ask", "responsible", "next_action"):
            item[field] = text(row.get(field, prior.get(field)), "obligation " + field, 10000)
        item["state"] = row.get("state", prior.get("state", "needs_action"))
        if item["state"] not in STATES:
            raise ValueError("invalid obligation state")
        prior_type = prior.get("responsibility_type") if item["responsible"] == prior.get("responsible") else None
        item["responsibility_type"] = normalize_responsibility_type(state,
            value=row.get("responsibility_type", prior_type), responsible=item["responsible"], item_state=item["state"])
        if item["responsibility_type"] == "unknown" and not prior:
            raise ValueError("new obligation requires responsibility_type: owner, bot, or correspondent")
        if item["state"] == "needs_action" and item["responsibility_type"] in {"owner", "correspondent"}:
            item["state"] = "waiting_" + item["responsibility_type"]
        item["due_at"] = row.get("due_at", prior.get("due_at"))
        if item["due_at"] is not None:
            iso(item["due_at"])
        draft_review_id = row.get("draft_review_id", prior.get("draft_review_id"))
        if draft_review_id is not None:
            text(draft_review_id, "draft_review_id")
            if draft_review_id != prior.get("draft_review_id") and draft_review_id not in case["snapshot"]["draft_ids"]:
                raise ValueError("draft_review_id must identify an observed existing draft")
            item["draft_review_id"] = draft_review_id
            if prior.get("draft_status"):
                item["draft_status"] = prior["draft_status"]
        item["source_ids"] = source_ids(state, row.get("source_ids", prior.get("source_ids", [])))
        if item["state"] == "handled":
            item["outcome_evidence"] = text(row.get("outcome_evidence", prior.get("outcome_evidence")),
                                            "obligation completion evidence", 10000)
        # A new email cannot answer an authenticated-owner decision.
        if any(key in d.get("obligation_ids", []) for d in pending_decisions(state, case)):
            if item["state"] != "waiting_owner":
                raise ValueError("obligation has an unresolved owner decision")
        item["created_at"] = prior.get("created_at", prior.get("updated_at", stamp()))
        content = {k: v for k, v in prior.items() if k not in {"version", "updated_at"}}
        item["version"] = prior.get("version", 0) + (content != item)
        item["updated_at"] = stamp() if content != item else prior["updated_at"]
        items[key] = item
    if len(items) > 100:
        raise ValueError("too many obligations in one conversation")


def obligation_selection(case, values, required=False):
    items = case.get("obligations", {})
    if values is None or (values == [] and not items):
        if required and items:
            raise ValueError("explicit obligation_ids required")
        return []
    if (not isinstance(values, list) or not values or not all(isinstance(x, str) for x in values)
            or len(set(values)) != len(values) or any(x not in items for x in values)):
        raise ValueError("obligation_ids must identify distinct existing obligations")
    return sorted(values)


def obligation_state(case):
    rows = list(case.get("obligations", {}).values())
    if not rows:
        return case["state"]
    for state_name in ("needs_action", "waiting_owner", "waiting_correspondent"):
        if any(row["state"] == state_name for row in rows):
            return state_name
    return "handled"


def pending_decisions(state, case):
    return list(inbox_store.selected(state, "decisions", case_id=case["id"], status__in={"pending", "rule_requested"}))


def enqueue_recheck(state, case, reason):
    old = state["reevaluations"].get(case["id"], {})
    reasons = sorted(set(old.get("reasons", []) + [reason]))
    if old.get("reasons") == reasons:
        return
    state["reevaluations"][case["id"]] = {"id": "recheck-" + secrets.token_hex(8),
        "case_id": case["id"], "reasons": reasons, "at": stamp()}


def source_work(state):
    result = []
    for key, policy in (state["config"] or {}).get("source_policy", {}).items():
        source = state["sources"].get(key)
        local_reason = None
        if source and source.get("local_path"):
            try:
                if hashlib.sha256(Path(source["local_path"]).read_bytes()).hexdigest() != source.get("content_hash"):
                    local_reason = "local content changed"
            except OSError:
                local_reason = "local source unavailable"
        if not source or source["status"] != "current" or iso(source["valid_until"]) <= utcnow() or local_reason:
            result.append({"source_id": key, "origin": policy["origin"],
                           "reason": local_reason or ("missing" if not source else source["status"] if source["status"] != "current" else "expired")})
    return result


def record_gap(state, p):
    lease(state, p)
    case = get_case(state, p)
    key = text(p.get("gap_id"), "knowledge gap id")
    question = text(p.get("question"), "knowledge gap question", 10000)
    deps = source_ids(state, p.get("source_ids", []))
    selected = obligation_selection(case, p.get("obligation_ids"), required=bool(case.get("obligations")))
    gap = state["knowledge_gaps"].get(key)
    if gap and gap["question"] != question:
        raise ValueError("gap id already identifies a different question")
    if gap is None:
        gap = {"id": key, "question": question, "cases": {}, "source_ids": [], "status": "open", "created_at": stamp()}
        state["knowledge_gaps"][key] = gap
    gap["cases"][case["id"]] = selected
    gap["source_ids"] = sorted(set(gap["source_ids"] + deps))
    gap["updated_at"] = stamp()
    case["source_ids"] = sorted(set(case.get("source_ids", []) + deps))
    return gap


def resolve_gap(state, p):
    approval = owned(p)
    gap = state["knowledge_gaps"].get(p.get("gap_id"))
    if not gap:
        raise ValueError("unknown knowledge gap")
    # The answer must have gone through approved source registration first.
    versions = p.get("source_versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError("approved current source versions required")
    source_ids(state, list(versions))
    for key, version in versions.items():
        source = state["sources"].get(key)
        if not source or source["version"] != version or source["status"] != "current" or iso(source["valid_until"]) <= utcnow():
            raise ValueError("knowledge gap answer needs a current approved source")
    gap.update(status="answered", source_versions=versions, evidence=text(p.get("evidence"), "answer evidence", 10000),
               approval_ref=approval, updated_at=stamp())
    for key in gap["cases"]:
        enqueue_recheck(state, state["cases"][key], "knowledge gap answered: " + gap["id"])
    return gap


def create_decision(state, case, p):
    selected = obligation_selection(case, p.get("obligation_ids"), required=bool(case.get("obligations")))
    question = text(p.get("question"), "precise owner question", 10000)
    basis = {"question": question, "obligation_ids": selected,
             "message_fingerprint": case["snapshot"].get("message_fingerprint", case["snapshot"]["fingerprint"]),
             "profile_version": state["version"]}
    for prior in pending_decisions(state, case):
        if (all(prior.get(k) == v for k, v in basis.items())
                and all(case["obligations"][key]["version"] == version
                        for key, version in prior.get("obligation_versions", {}).items())):
            return prior
        if set(prior["obligation_ids"]) & set(selected) and prior["obligation_ids"] != selected:
            raise ValueError("owner questions must have non-overlapping obligation scopes")
    key = p.get("decision_id") or "decision-" + secrets.token_hex(8)
    text(key, "decision id")
    if key in state["decisions"]:
        raise ValueError("decision id already used; changed questions need a new id")
    # A revised question for the same obligation replaces its old question, not an
    # unrelated decision elsewhere in the thread.
    for prior in pending_decisions(state, case):
        if prior["obligation_ids"] == selected:
            prior["status"] = "superseded"
            prior["superseded_by"] = key
    value = {"id": key, "case_id": case["id"], "thread_id": case["thread_id"], "mailbox": state["mailbox"],
             **basis, "recommendation": p.get("recommendation"), "status": "pending", "version": 1, "created_at": stamp()}
    state["decisions"][key] = value
    for obligation_id in selected:
        row = case["obligations"][obligation_id]
        row.update(state="waiting_owner", version=row["version"] + 1, updated_at=stamp())
    value["obligation_versions"] = {key: case["obligations"][key]["version"] for key in selected}
    return value


def decision_answer(state, p):
    approval = owned(p)
    event_id = text(p.get("event_id"), "authenticated host event id")
    input_hash = digest(p)
    prior = state["owner_events"].get(event_id)
    if prior:
        if prior["input_hash"] != input_hash:
            raise ValueError("owner event id reused with different input")
        return {**prior, "idempotent": True}
    decision = state["decisions"].get(p.get("decision_id"))
    if not decision or decision["status"] not in {"pending", "rule_requested"}:
        raise ValueError("decision is not pending")
    if p.get("expected_version") != decision["version"]:
        raise ValueError("decision version changed")
    case = state["cases"][decision["case_id"]]
    if p.get("case_id", case["id"]) != case["id"]:
        raise ValueError("decision case mismatch")
    if decision["message_fingerprint"] != case["snapshot"].get("message_fingerprint", case["snapshot"]["fingerprint"]) or decision["profile_version"] != state["version"]:
        raise ValueError("decision context changed; refresh the question before applying an answer")
    if any(case["obligations"][key]["version"] != version for key, version in decision.get("obligation_versions", {}).items()):
        raise ValueError("decision obligation changed; refresh the question")
    if not fresh(case["snapshot"]["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
        raise ValueError("owner answer needs a fresh conversation read")
    if any(a["case_id"] == case["id"] and a["status"] in UNRESOLVED_SEND for a in state["actions"].values()):
        raise ValueError("reconcile uncertain send before answering")
    scope = p.get("scope")
    if scope not in {"once", "standing_rule", "takeover"}:
        raise ValueError("explicit owner decision scope required")
    answer = text(p.get("answer"), "owner answer", 10000)
    validated_once_rule = None
    if p.get("once_rule") is not None:
        if scope != "once":
            raise ValueError("one-off authority requires once scope")
        rule = p["once_rule"]
        if not isinstance(rule, dict) or type(rule.get("allow_attachments")) is not bool:
            raise ValueError("one-off rule needs explicit attachment choice")
        validated_once_rule = {"id": "once:" + approval, "recipient_mode": "allowlist",
                             "recipients": addresses(rule.get("recipients")),
                             "allow_attachments": rule["allow_attachments"], "description": answer,
                             "trigger_id": case["snapshot"]["trigger_id"], "version": state["version"], "used": False}
    result = {"event_id": event_id, "decision_id": decision["id"], "case_id": case["id"], "input_hash": input_hash,
              "scope": scope, "answer": answer, "approval_ref": approval, "at": stamp()}
    decision["answer"] = result
    if scope == "standing_rule":
        decision["status"] = "rule_requested"
        result["next"] = "setup_approval"
    else:
        decision["status"] = "answered"
        case.setdefault("owner_answers", {})[decision["id"]] = result
        if validated_once_rule is not None:
            case["once_rule"] = validated_once_rule
        for key in decision["obligation_ids"]:
            row = case["obligations"][key]
            row.update(state="needs_action", responsible="inbox-manager", responsibility_type="bot", version=row["version"] + 1, updated_at=stamp())
        if scope == "takeover":
            case["takeover"] = True
        if not pending_decisions(state, case):
            if case["hold"] == "owner_decision":
                case["hold"] = None
            case["pending_decision"] = bool(case.get("pending_candidate_id"))
        case["state"] = "waiting_owner" if case["takeover"] or case["pending_decision"] else "needs_action"
        enqueue_recheck(state, case, "owner decision answered: " + decision["id"])
        result["next"] = "takeover" if case["takeover"] else "recheck"
        case["cleanup"] = {"status": "pending", "reason": "owner_state_changed",
                           "action_id": case.get("cleanup", {}).get("action_id")}
    state["owner_events"][event_id] = result
    event(state, "owner_decision_answered", decision_id=decision["id"], event_id=event_id, scope=scope)
    return {**result, "idempotent": False}


def decision_notifications(state):
    config = (state["config"] or {}).get("notifications", {"destination_ref": "current-task", "reminder_hours": 24})
    if "digest_hour" in config:
        return reminder_notifications(state, config)
    result = []
    for decision in state["decisions"].values():
        if decision["status"] not in {"pending", "rule_requested"}:
            continue
        dismissed = state.get("dismissals", {}).get(decision["case_id"], {})
        if all(key in dismissed for key in decision.get("obligation_ids", []) or ["__case__"]):
            continue
        if not reminder_allowed(state, state["cases"][decision["case_id"]]):
            continue
        slot = max(0, int((utcnow() - iso(decision["created_at"])).total_seconds() // (config["reminder_hours"] * 3600)))
        key = f'{decision["id"]}:{decision["version"]}:{slot}'
        if key not in state["notifications"]:
            result.append({"notification_id": key, "destination_ref": config["destination_ref"], "decision": decision,
                           "instruction": "Reply through the authenticated owner task with this decision id and explicit scope."})
    return result


def reminder_allowed(state, case):
    if not inbox_priority.case_in_scope(state, case):
        return False
    dismissed = state.get("dismissals", {}).get(case["id"], {})
    open_ids = [row["id"] for row in case.get("obligations", {}).values() if row["state"] != "handled"]
    if all(key in dismissed for key in open_ids or ["__case__"]):
        return False
    control = state["reminders"].get(case["id"], {})
    if control.get("muted"):
        return False
    return not control.get("resume_at") or iso(control["resume_at"]) <= utcnow()


def reminder_case(state, case):
    gaps = sorted(gap["id"] for gap in state["knowledge_gaps"].values()
                  if gap["status"] == "open" and case["id"] in gap["cases"])
    decisions = sorted(row["id"] for row in pending_decisions(state, case))
    reasons = []
    if case.get("pending_candidate_id"):
        reasons.append("pending_draft")
    if decisions:
        reasons.append("owner_decision")
    if gaps:
        reasons.append("knowledge_gap")
    if case.get("takeover"):
        reasons.append("takeover")
    if case.get("hold") and not reasons:
        reasons.append(case["hold"])
    if not reasons:
        reasons.append("owner_action")
    return {"case_id": case["id"], "thread_id": case["thread_id"], "reasons": reasons,
            "unresolved_ask": case.get("unresolved_ask"), "next_action": case.get("next_action"),
            "responsible": case.get("responsible"), "urgency": case.get("urgency"),
            "pending_draft_id": case.get("pending_candidate_id"), "decision_ids": decisions,
            "knowledge_gap_ids": gaps}


def urgency_notification_id(case):
    version = case.get("urgency_version")
    if case.get("urgency") != "urgent" or not version:
        return None
    return f'reminder:v1:urgency:{case["id"]}:{version}'


def reminder_notifications(state, config):
    zone = ZoneInfo(config["timezone"])
    local_now = utcnow().astimezone(zone)
    destination = config["destination_ref"]
    gap_cases = {case_id for gap in state["knowledge_gaps"].values()
                 if gap["status"] == "open" for case_id in gap["cases"]}
    waiting = [case for case in state["cases"].values()
               if (case["state"] == "waiting_owner" or
                   (case["state"] != "handled" and case["id"] in gap_cases))
               and reminder_allowed(state, case)]
    result = []
    urgent_ids = {}
    for case in sorted(waiting, key=lambda row: row["id"]):
        key = urgency_notification_id(case)
        if not key:
            continue
        urgent_ids[case["id"]] = key
        if key not in state["notifications"]:
            result.append({"kind": "urgency_change", "notification_id": key,
                           "destination_ref": destination, "case": reminder_case(state, case),
                           "instruction": "Surface this changed urgent owner item without expanding authority."})
    # A new question still needs its initial owner notification before digest time.
    for decision in state["decisions"].values():
        if decision["status"] not in {"pending", "rule_requested"}:
            continue
        case = state["cases"][decision["case_id"]]
        if case not in waiting or case["id"] in urgent_ids:
            continue
        key = f'{decision["id"]}:{decision["version"]}:0'
        if not any(row.get("decision_id") == decision["id"] for row in state["notifications"].values()):
            result.append({"notification_id": key, "destination_ref": destination, "decision": decision,
                           "instruction": "Reply through the authenticated owner task with this decision id and explicit scope."})
    if local_now.hour < config["digest_hour"]:
        return result
    local_date = local_now.date().isoformat()
    cases = []
    for case in sorted(waiting, key=lambda row: row["id"]):
        urgent_id = urgent_ids.get(case["id"])
        urgent_receipt = state["notifications"].get(urgent_id) if urgent_id else None
        if urgent_id and urgent_id not in state["notifications"]:
            continue
        if urgent_receipt and iso(urgent_receipt["at"]).astimezone(zone).date().isoformat() == local_date:
            continue
        cases.append(reminder_case(state, case))
    key = "reminder:v1:digest:" + local_date
    if cases and key not in state["notifications"]:
        result.append({"kind": "daily_digest", "notification_id": key,
                       "destination_ref": destination, "local_date": local_date, "cases": cases,
                       "instruction": "Handle each item through the authenticated owner task; this digest grants no new authority."})
    return result


def reminder_action(state, p):
    approval = owned(p)
    case = get_case(state, p)
    has_resume = "resume_at" in p
    has_muted = "muted" in p
    if has_resume == has_muted or (has_muted and type(p["muted"]) is not bool):
        raise ValueError("set exactly one of resume_at or muted")
    resume_at = p.get("resume_at") if has_resume else None
    if has_resume:
        iso(resume_at)
    muted = p["muted"] if has_muted else False
    previous = state["reminders"].get(case["id"])
    values = {"muted": muted, "resume_at": resume_at}
    if previous and all(previous.get(key) == value for key, value in values.items()):
        return {**previous, "idempotent": True}
    value = {"case_id": case["id"], **values, "version": (previous or {}).get("version", 0) + 1,
             "approval_ref": approval, "updated_at": stamp()}
    state["reminders"][case["id"]] = value
    event(state, "case_reminder_changed", case_id=case["id"], version=value["version"], approval_ref=approval)
    return {**value, "idempotent": False}


def notification_receipt(state, p):
    key = text(p.get("notification_id"), "notification id")
    if key in state["notifications"]:
        prior = state["notifications"][key]
        if any(p.get(k) != prior[k] for k in ("destination_ref", "receipt", "message_ref")):
            raise ValueError("notification receipt id reused")
        return {**prior, "idempotent": True}
    pending = next((row for row in decision_notifications(state) if row["notification_id"] == key), None)
    if not pending or p.get("destination_ref") != pending["destination_ref"]:
        raise ValueError("notification is stale or destination is not approved")
    value = {"notification_id": key, "destination_ref": pending["destination_ref"], "at": stamp(),
             "receipt": text(p.get("receipt"), "host notification receipt", 10000),
             "message_ref": text(p.get("message_ref"), "host message reference", 10000)}
    if pending.get("decision"):
        value["decision_id"] = pending["decision"]["id"]
    else:
        value["kind"] = pending["kind"]
        value["case_ids"] = ([pending["case"]["case_id"]] if pending.get("case")
                             else [case["case_id"] for case in pending["cases"]])
    state["notifications"][key] = value
    return {**value, "idempotent": False}


def candidate_held(state, case, c):
    for linked in related_cases(state, case):
        if not (linked["hold"] or linked["takeover"] or linked["pending_decision"] or pending_decisions(state, linked)):
            continue
        # An unrelated unanswered obligation may coexist with one useful owner
        # draft. Existing drafts, takeover, uncertainty and linked-case holds stay
        # absolute. Automatic sends never use this exception.
        selected = set(c.get("obligation_ids") or [])
        decisions = pending_decisions(state, linked)
        partial = (linked["id"] == case["id"] and c.get("mode") == "draft" and selected
                   and linked["hold"] == "owner_decision" and not linked["takeover"]
                   and not linked.get("pending_candidate_id") and decisions
                   and all(d["obligation_ids"] and selected.isdisjoint(d["obligation_ids"]) for d in decisions)
                   and all(linked["obligations"][key]["state"] == "needs_action" for key in selected))
        if not partial:
            return True
    return False


def related_cases(state, case):
    """Traverse only explicit evidenced links, including links made in reverse."""
    return inbox_store.linked_cases(state, case)


def held(state, case):
    return any(c["hold"] or c["takeover"] or c["pending_decision"] or pending_decisions(state, c)
               for c in related_cases(state, case))


def hold(state, p):
    lease(state, p)
    case = get_case(state, p)
    reason = text(p.get("reason"), "reason", 10000)
    if p.get("kind") not in {"owner_decision", "source_unavailable", "connector_unavailable", "draft_unknown"}:
        raise ValueError("invalid hold kind")
    # Do not downgrade an uncertain irreversible action to an ordinary exception.
    if case["hold"] in {"send_unknown", "send_unconfirmed"}:
        raise ValueError("send uncertainty needs reconciliation")
    case["hold"] = p["kind"]
    case["hold_reason"] = reason
    if p["kind"] in {"owner_decision", "draft_unknown"}:
        case["pending_decision"] = True
        case["state"] = "waiting_owner"
        if p["kind"] == "owner_decision":
            decision = create_decision(state, case, p)
            case["decision_id"] = decision["id"]
            if not case.get("obligations"):
                case["pending_candidate_id"] = None
    case["question"] = p.get("question")
    case["recommendation"] = p.get("recommendation")
    if p["kind"] == "owner_decision":
        text(p.get("question"), "precise owner question", 10000)
    if "source_ids" in p:
        case["source_ids"] = sorted(set(case.get("source_ids", []) + source_ids(state, p["source_ids"])))
    event(state, "case_held", case_id=case["id"], reason=reason)
    return case


def recover(state, p):
    lease(state, p)
    case = get_case(state, p)
    if case["hold"] not in {"source_unavailable", "connector_unavailable", "draft_failed"}:
        raise ValueError("only reversible operational holds can recover automatically")
    decisions = pending_decisions(state, case)
    if case["takeover"] or case.get("pending_candidate_id") or (case["pending_decision"] and not decisions):
        raise ValueError("member-owned decision requires owner handling")
    text(p.get("evidence"), "recovery evidence", 10000)
    case["hold"] = "owner_decision" if decisions else None
    case["pending_decision"] = bool(decisions)
    case["state"] = "waiting_owner" if decisions else "needs_action"
    event(state, "case_recovered", case_id=case["id"], evidence=p["evidence"])
    return case


def update_source(state, p):
    if not state["config"]:
        raise ValueError("setup required")
    key = text(p.get("source_id"), "source_id")
    policy = source_policy(state, key)
    if not policy:
        raise ValueError("source access not approved")
    if p.get("origin") != policy["origin"]:
        raise ValueError("source origin differs from approved registry")
    version = text(p.get("version"), "source version")
    if p.get("status") not in {"current", "unavailable", "conflicting"}:
        raise ValueError("source status required")
    iso(p.get("verified_at"))
    iso(p.get("valid_until"))
    if iso(p["verified_at"]) > utcnow() + timedelta(seconds=5) or iso(p["valid_until"]) <= iso(p["verified_at"]):
        raise ValueError("source verification and expiry times are invalid")
    source = {k: p[k] for k in ("source_id", "origin", "version", "status", "verified_at", "valid_until")}
    if "local_path" in p:
        if str(Path(p["local_path"]).resolve()) != policy["origin"]:
            raise ValueError("local source path must match approved origin")
        source["local_path"] = p["local_path"]
        source["content_hash"] = text(p.get("content_hash"), "content_hash")
    old = state["sources"].get(key)
    state["sources"][key] = source
    material_change = old is None or any(old.get(field) != source.get(field)
                                        for field in ("version", "status", "content_hash"))
    if material_change:
        for lesson in state["lessons"].values():
            if key in lesson.get("source_versions", {}):
                lesson["status"] = "invalidated"
    if material_change:
        for case in state["cases"].values():
            dependencies = set(case.get("source_ids", []))
            dependencies.update(x for row in case.get("obligations", {}).values() for x in row["source_ids"])
            dependencies.update(x for c in state["candidates"].values() if c["case_id"] == case["id"] for x in c["source_versions"])
            if key in dependencies and case["state"] != "handled":
                enqueue_recheck(state, case, "source changed: " + key)
    elif old and old != source:
        # Renewing unchanged evidence can unblock the affected hold, but must not
        # replay every dependent conversation or invalidate stable lessons.
        for case in state["cases"].values():
            if case.get("hold") == "source_unavailable" and key in case.get("source_ids", []):
                enqueue_recheck(state, case, "source available: " + key)
    return source


def update_case(state, p):
    lease(state, p)
    key = text(p.get("case_id"), "case_id")
    snap = snapshot(p.get("snapshot"))
    current = state["cases"].get(key)
    inbox_priority.require_scope(state, current or {"thread_id": snap["thread_id"]}, snap)
    cleanup_basis = inbox_archive._basis(current, inbox_archive.policy(state["config"].get("cleanup"))) if current else None
    previous_content = digest({k: v for k, v in current.items() if k != "updated_at"}) if current else None
    if current is None:
        # One case per provider thread unless an explicit, evidenced issue link exists.
        if any(inbox_store.selected(state, "cases", thread_id=snap["thread_id"])):
            raise ValueError("thread already belongs to a case")
        current = {"id": key, "thread_id": snap["thread_id"], "state": "needs_action",
                   "hold": None, "takeover": False, "pending_decision": False,
                   "followup_count": 0, "last_sent_at": None, "due_at": None, "created_at": stamp()}
        state["cases"][key] = current
    if current["thread_id"] != snap["thread_id"]:
        raise ValueError("case thread identity cannot change")
    changed = current.get("snapshot", {}).get("fingerprint") != snap["fingerprint"]
    current["snapshot"] = snap
    current["unresolved_ask"] = text(p.get("unresolved_ask"), "unresolved_ask", 10000)
    current["next_action"] = text(p.get("next_action"), "next_action", 10000)
    responsible = text(p.get("responsible"), "responsible")
    prior_type = current.get("responsibility_type") if responsible == current.get("responsible") else None
    current["responsibility_type"] = normalize_responsibility_type(state,
        value=p.get("responsibility_type", prior_type), responsible=responsible, item_state=p.get("state", "needs_action"))
    if current["responsibility_type"] == "unknown" and previous_content is None:
        raise ValueError("new case requires responsibility_type: owner, bot, or correspondent")
    current["responsible"] = responsible
    if "owner_draft_ids" in p:
        values = p["owner_draft_ids"]
        if not isinstance(values, list) or any(v not in snap["draft_ids"] for v in values):
            raise ValueError("owner_draft_ids must identify observed existing drafts")
        current["owner_draft_ids"] = sorted(set(current.get("owner_draft_ids", []) + values))
    for field in ("topic", "context", "recommendation"):
        if field in p:
            current[field] = text(p[field], field, 10000)
    if "thread_url" in p:
        current["thread_url"] = verified_url(text(p["thread_url"], "thread_url"),
                                           text(p.get("thread_url_evidence"), "thread_url_evidence"))
        current["thread_url_evidence"] = p["thread_url_evidence"]
    if "urgency" in p:
        urgency = text(p["urgency"], "urgency", 100).strip().lower()
        if urgency not in {"normal", "urgent"}:
            raise ValueError("urgency must be normal or urgent")
        if urgency != current.get("urgency"):
            current["urgency"] = urgency
            current["urgency_version"] = current.get("urgency_version", 0) + 1
            current["urgency_changed_at"] = stamp()
            event(state, "case_urgency_changed", case_id=current["id"], urgency=urgency,
                  version=current["urgency_version"])
    if "obligations" in p:
        update_obligations(state, current, p["obligations"])
    if "source_ids" in p:
        current["source_ids"] = source_ids(state, p["source_ids"])
    if "linked_cases" in p:
        links = p["linked_cases"]
        if not isinstance(links, list) or any(k not in state["cases"] or k == key for k in links):
            raise ValueError("linked cases must already exist and differ from this case")
        if links:
            text(p.get("link_evidence"), "issue link evidence", 10000)
        # Removing a link could bypass another case's hold; keep established links.
        current["linked_cases"] = sorted(set(current.get("linked_cases", []) + links))
        current["link_evidence"] = p.get("link_evidence")
    if p.get("due_at") is not None:
        iso(p["due_at"])
    current["due_at"] = p.get("due_at", current.get("due_at"))
    requested = p.get("state", "needs_action")
    if requested not in STATES:
        raise ValueError("invalid conversation state")
    if requested == "needs_action" and current["responsibility_type"] in {"owner", "correspondent"}:
        requested = "waiting_" + current["responsibility_type"]
    if current.get("obligations"):
        derived = obligation_state(current)
        if requested == "handled" and derived != "handled":
            raise ValueError("open obligations prevent conversation completion")
        requested = derived
    if current["pending_decision"] or current["takeover"]:
        current["state"] = "waiting_owner"
    elif not current["hold"]:
        current["state"] = requested
    if requested == "handled" and not current["pending_decision"] and not current["takeover"] and not current["hold"]:
        current["outcome"] = text(p.get("outcome"), "completion outcome")
        text(p.get("outcome_evidence"), "completion evidence", 10000)
        current["outcome_evidence"] = p["outcome_evidence"]
    elif changed:
        current.pop("outcome", None)
        current.pop("outcome_evidence", None)
    # A new trigger cannot clear a member-owned decision or unknown send.
    if previous_content != digest({k: v for k, v in current.items() if k != "updated_at"}):
        current["updated_at"] = stamp()
    if p.get("recheck_id") is not None:
        queued = state["reevaluations"].get(key)
        if not queued or p["recheck_id"] != queued["id"]:
            raise ValueError("recheck changed; reload queued work")
        if not fresh(snap["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
            raise ValueError("recheck needs fresh conversation evidence")
        state["reevaluations"].pop(key)
    # Admit the complete conversation once even when discovery returned several
    # messages in that thread. Old overlapping pages cannot reopen older triggers.
    for message_id in snap["message_ids"]:
        queued = state["queue"].get(message_id)
        if queued and queued["thread_id"] != snap["thread_id"]:
            raise ValueError("queued message belongs to a different thread")
        prior_thread = state["seen_messages"].get(message_id)
        if prior_thread and prior_thread != snap["thread_id"]:
            raise ValueError("observed message changed threads")
        state["seen_messages"][message_id] = snap["thread_id"]
        state["queue"].pop(message_id, None)
    if current.get("cleanup") and cleanup_basis != inbox_archive._basis(current, inbox_archive.policy(state["config"].get("cleanup"))):
        current["cleanup"] = {"status": "pending", "reason": "conversation_reassessed",
                              "action_id": current["cleanup"].get("action_id")}
    if not current.get("cleanup") and current["state"] == "handled" and state["config"].get("cleanup", {}).get("archive_on_resolution"):
        current["cleanup"] = {"status":"pending","reason":"resolved_cleanup_required"}
    inbox_archive.reconcile_legacy_pending(state, current)
    return current


def scan(state, p):
    lease(state, p)
    query = text(p.get("query"), "scan query", 10000)
    window_end = p.get("window_end")
    if iso(window_end) > utcnow() + timedelta(seconds=5):
        raise ValueError("scan cutoff is in the future")
    lane = p.get("lane")
    if lane is None and "all" not in inbox_priority.lane_queries((state.get("config") or {}).get("inbox_priority")):
        raise ValueError("scoped discovery requires the approved lane")
    if lane is not None:
        expected = inbox_priority.query_for((state.get("config") or {}).get("inbox_priority"), lane, window_end)
        if query != expected:
            raise ValueError("scan query must match the complete inbox lane discovery plan")
    previous = state["inbox_scans"].get(lane) if lane is not None else state["scan"]
    if previous and lane is not None and not inbox_priority.scan_in_scope(state, lane, previous):
        # Keep the older broader scan as history, without resuming its cursor or
        # admitting its messages under a narrower current policy.
        state.setdefault("scope_history", []).append({lane: previous})
        previous = None
    cursor = p.get("page_cursor")
    if previous and not previous["complete"]:
        if cursor != previous["next_cursor"] or query != previous["query"] or window_end != previous["window_end"]:
            raise ValueError("resume the saved scan cursor and window")
    elif cursor is not None:
        raise ValueError("new scan must begin at its first page")
    if type(p.get("complete")) is not bool:
        raise ValueError("explicit listing completeness required")
    if p["complete"]:
        if p.get("next_cursor") is not None:
            raise ValueError("complete scan cannot have a next cursor")
    else:
        text(p.get("next_cursor"), "next_cursor")
        if p["next_cursor"] == cursor:
            raise ValueError("scan cursor did not advance")
    entries = p.get("entries")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("bounded scan page required")
    message_ids = set(previous.get("message_ids", [])) if previous and not previous["complete"] else set()
    cases_by_thread = {case["thread_id"]: case for case in state["cases"].values()}
    for entry in entries:
        message_id = text(entry.get("trigger_id"), "trigger_id")
        thread_id = text(entry.get("thread_id"), "thread_id")
        date = entry.get("received_at")
        iso(date)
        if lane is not None and iso(date).timestamp() >= int(iso(window_end).timestamp()):
            raise ValueError("listed message is outside the scan cutoff")
        message_ids.add(message_id)
        previous_membership = state.get("thread_scope", {}).get(thread_id, {})
        moved_scope = bool(previous_membership and previous_membership.get("lane") != lane) or (
            thread_id in cases_by_thread and not inbox_priority.case_in_scope(state, cases_by_thread[thread_id]))
        if lane is not None:
            state.setdefault("thread_scope", {})[thread_id] = {"lane": lane, "observed_at": stamp(),
                "query": query, "window_end": window_end, "evidence": p.get("provider_evidence")}
        if message_id in state["queue"] and state["queue"][message_id]["thread_id"] != thread_id:
            raise ValueError("provider message identity changed threads")
        if message_id in state["seen_messages"]:
            if state["seen_messages"][message_id] != thread_id:
                raise ValueError("provider message identity changed threads")
            if not moved_scope:
                continue
        state["queue"].setdefault(message_id, {"trigger_id": message_id, "thread_id": thread_id,
                                              "received_at": date, "first_seen_at": stamp()})
        if lane is not None:
            state["queue"][message_id]["lane"] = lane
    text(p.get("provider_evidence"), "scan provider evidence", 10000)
    record = {"query": query, "window_end": window_end, "complete": p["complete"],
                     "next_cursor": p.get("next_cursor"), "run_id": p["run_id"],
                     "provider_evidence": p["provider_evidence"], "at": stamp()}
    if lane is not None:
        record.update(lane=lane, message_ids=sorted(message_ids))
        state["inbox_scans"][lane] = record
    else:
        state["scan"] = record
    if lane is None and p["complete"] and (state["watermark"] is None or iso(window_end) > iso(state["watermark"])):
        # Advancing discovery is safe because every discovered row is durably queued.
        state["watermark"] = window_end
    return {"scan": record, "queued": len(state["queue"]), "watermark": state["watermark"]}


def ordered_cases(state, cases):
    lanes = list(inbox_priority.lane_queries((state["config"] or {}).get("inbox_priority")))
    scans = inbox_priority.active_scans(state)
    def priority(case):
        ids = set(case.get("snapshot", {}).get("message_ids", []))
        rank = next((index for index, lane in enumerate(lanes)
                     if ids.intersection(scans.get(lane, {}).get("message_ids", []))), len(lanes))
        return (case.get("urgency") != "urgent", rank, case.get("due_at") or "9999", case["id"])
    return sorted((case for case in cases if inbox_priority.case_in_scope(state, case)), key=priority)


def work(state):
    state = inbox_store.hot_state(state)
    import inbox_reconcile
    return {"mailbox_reconciliation": inbox_reconcile.work(state), "queue": inbox_priority.ordered_queue(state), "cleanup": inbox_archive.work(state),
            "coverage": inbox_priority.coverage(state),
            "resume_scans": [row for row in inbox_priority.active_scans(state).values() if not row["complete"]],
            "cases": ordered_cases(state, [c for c in state["cases"].values() if bot_work(state, c) and not held(state, c)]),
            "due": due(state), "draft_candidates": [d["id"] for d in state["drafts"].values()
                                                      if d["status"] in {"pending", "creating", "unknown"} and inbox_priority.case_in_scope(state, state["cases"][d["case_id"]])],
            "uncertain_sends": [a for a in state["actions"].values() if a["status"] in UNRESOLVED_SEND and inbox_priority.case_in_scope(state, state["cases"][a["case_id"]])],
            "resume_scan": state["scan"] if "all" in inbox_priority.lane_queries((state.get("config") or {}).get("inbox_priority")) and state["scan"] and not state["scan"]["complete"] else None,
            "reevaluations": [{**row, "case": state["cases"][key]} for key, row in state["reevaluations"].items() if inbox_priority.case_in_scope(state, state["cases"][key])],
            "source_refresh": source_work(state), "decisions": decision_notifications(state),
            "knowledge_gaps": list(state["knowledge_gaps"].values()),
            "recoverable_holds": [c for c in state["cases"].values()
                                  if inbox_priority.case_in_scope(state, c) and c["hold"] in {"source_unavailable", "connector_unavailable", "draft_failed"}
                                  and not c.get("pending_candidate_id") and not c["takeover"]
                                  and (not c["pending_decision"] or pending_decisions(state, c))]}


def owner_action(state, p):
    approval = owned(p)
    action = p.get("action")
    if action in {"pause", "resume_inbox"}:
        state["paused"] = action == "pause"
        event(state, action, approval_ref=approval)
        return {"paused": state["paused"]}
    case = get_case(state, p)
    if action in {"forget", "restore_reminder"}:
        selected = obligation_selection(case, p.get("obligation_ids"), required=bool(case.get("obligations")))
        dismissals = state.setdefault("dismissals", {}).setdefault(case["id"], {})
        for key in selected or ["__case__"]:
            if action == "forget":
                dismissals[key] = {"approval_ref": approval, "at": stamp()}
            else:
                dismissals.pop(key, None)
        event(state, "owner_" + action, case_id=case["id"], obligation_ids=selected, approval_ref=approval)
        return {"case_id": case["id"], "obligation_ids": selected, "dismissed": action == "forget"}
    if action == "answer" and case.get("obligations") and pending_decisions(state, case):
        decisions = pending_decisions(state, case)
        key = p.get("decision_id")
        if key is None and len(decisions) != 1:
            raise ValueError("select the exact decision_id to answer")
        selected = next((d for d in decisions if d["id"] == key), None) if key else decisions[0]
        if selected is None:
            raise ValueError("decision does not belong to this case")
        return decision_answer(state, {**p, "decision_id": selected["id"],
                                        "expected_version": p.get("expected_version", selected["version"]),
                                        "scope": p.get("scope", "once"),
                                        "event_id": p.get("event_id", "owner:" + approval)})
    if action == "takeover":
        case["takeover"] = True
        case["state"] = "waiting_owner"
    elif action in {"resume", "answer", "close"}:
        if any(a["case_id"] == case["id"] and a["status"] in UNRESOLVED_SEND for a in state["actions"].values()):
            raise ValueError("reconcile uncertain send before resuming or closing")
        if action == "answer":
            case["owner_answer"] = {"text": text(p.get("answer"), "answer", 10000),
                                    "approval_ref": approval, "at": stamp()}
            if p.get("once_rule") is not None:
                rule = p["once_rule"]
                if not isinstance(rule, dict) or type(rule.get("allow_attachments")) is not bool:
                    raise ValueError("one-off rule needs explicit attachment choice")
                case["once_rule"] = {"id": "once:" + approval, "recipient_mode": "allowlist",
                                     "recipients": addresses(rule.get("recipients")),
                                     "allow_attachments": rule["allow_attachments"],
                                     "description": case["owner_answer"]["text"],
                                     "trigger_id": case["snapshot"]["trigger_id"],
                                     "version": state["version"], "used": False}
        case["pending_decision"] = False
        case["pending_candidate_id"] = None
        case["takeover"] = False
        case["hold"] = None
        case["state"] = "handled" if action == "close" else "needs_action"
        if action != "close":
            absence = case.pop("draft_absence", {})
            case["released_draft_absence"] = sorted(set(case.get("released_draft_absence", []) + absence.get("draft_ids", [])))
            case["responsible"] = "inbox-manager"
            case["responsibility_type"] = "bot"
        for decision in pending_decisions(state, case):
            decision["status"] = "answered" if action == "answer" else "released"
            decision["approval_ref"] = approval
        for row in case.get("obligations", {}).values():
            if action == "close":
                row.update(state="handled", outcome_evidence="Owner close: " + text(p.get("outcome"), "outcome"),
                           version=row["version"] + 1, updated_at=stamp())
            elif row["state"] == "waiting_owner":
                row.update(state="needs_action", responsible="inbox-manager", responsibility_type="bot", version=row["version"] + 1, updated_at=stamp())
        if action == "close":
            case["outcome"] = text(p.get("outcome"), "outcome")
            case["outcome_evidence"] = "Authenticated owner resolution: " + approval
        # Never overwrite/delete a Gmail draft. A fresh snapshot must prove its state.
        for candidate in state["candidates"].values():
            if candidate["case_id"] == case["id"] and candidate["status"] == "ready":
                candidate["status"] = "cancelled"
    else:
        raise ValueError("unknown owner action; standing rules use configure")
    case["updated_at"] = stamp()
    # An earlier Attention receipt is history, not verification of the new state.
    prior_cleanup = case.get("cleanup", {})
    case["cleanup"] = {"status": "pending", "reason": "owner_state_changed",
                       "action_id": prior_cleanup.get("action_id")}
    event(state, "owner_" + action, case_id=case["id"], approval_ref=approval)
    return case


def candidate(state, p):
    lease(state, p)
    if not state["active"]:
        raise ValueError("approved setup required")
    case = get_case(state, p)
    inbox_priority.require_scope(state, case)
    if case.get("draft_absence"):
        raise ValueError("missing owner draft needs an explicit owner decision before recreating a reply")
    import inbox_preparation
    if inbox_preparation.pending(state, case):
        raise ValueError('assess research and preparation before creating a candidate')
    key = text(p.get("candidate_id"), "candidate_id")
    if key in state["candidates"]:
        raise ValueError("candidate is immutable; use a new id for a repair")
    selected = obligation_selection(case, p.get("obligation_ids"), required=bool(case.get("obligations")))
    if candidate_held(state, case, {**p, "obligation_ids": selected}):
        raise ValueError("conversation held for owner or reconciliation")
    required_state = "waiting_correspondent" if p.get("kind") == "followup" else "needs_action"
    if any(case["obligations"][key]["state"] != required_state for key in selected):
        raise ValueError("selected obligation is not ready for a reply")
    snap = case["snapshot"]
    if snap["draft_ids"]:
        raise ValueError("existing draft is member-owned; preserve it")
    if p.get("profile_version") != state["version"] or p.get("authority_version") != state["version"]:
        raise ValueError("stale profile or authority")
    body = text(p.get("body"), "body", 100000)
    if PLACEHOLDER.search(body):
        raise ValueError("unfinished placeholder in reply")
    recipients = addresses(p.get("recipients"))
    cc, bcc = addresses(p.get("cc", []), empty=True), addresses(p.get("bcc", []), empty=True)
    if bcc:
        raise ValueError("BCC is outside this reply workflow")
    attachments = p.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 10:
        raise ValueError("invalid attachments")
    for attachment in attachments:
        for field in ("id", "sha256"):
            text(attachment.get(field), "attachment " + field)
        if not re.fullmatch(r"[0-9a-f]{64}", attachment["sha256"]):
            raise ValueError("attachment requires raw SHA-256 hex")
    subject = text(p.get("subject"), "subject")
    headers = p.get("reply_headers", {})
    from inbox_snapshot import canonical_headers
    headers = canonical_headers(headers)
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject cannot contain header separators")
    if not isinstance(headers, dict) or not set(headers) <= {"In-Reply-To", "References"}:
        raise ValueError("only verified reply threading headers are allowed")
    for value in headers.values():
        text(value, "reply header", 8000)
        if "\r" in value or "\n" in value:
            raise ValueError("reply headers cannot contain header separators")
    versions = p.get("source_versions")
    if not isinstance(versions, dict):
        raise ValueError("source_versions required")
    if p.get("mode") not in {"send", "draft"} or p.get("kind") not in {"reply", "followup", "backlog"}:
        raise ValueError("candidate mode/kind required")
    from inbox_revisions import _knowledge_dependencies
    dependencies = _knowledge_dependencies(p.get("knowledge_dependencies"))
    value = {"id": key, "case_id": case["id"], "mailbox": state["mailbox"],
             "thread_id": case["thread_id"], "trigger_id": snap["trigger_id"],
             "snapshot_fingerprint": snap["fingerprint"], "body": body,
             "recipients": recipients, "cc": cc, "bcc": bcc, "attachments": attachments,
             "subject": subject,
             "reply_message_id": text(p.get("reply_message_id"), "reply_message_id"),
             "reply_headers": headers,
             "profile_version": state["version"], "authority_version": state["version"],
             "source_versions": versions, "evidence": text(p.get("evidence"), "evidence", 30000),
             "rule_id": p.get("rule_id"), "mode": p["mode"], "kind": p["kind"],
             "writer_id": text(p.get("writer_id"), "writer_id"), "created_at": stamp(),
             "status": "ready"}
    if dependencies:
        value["knowledge_dependencies"] = dependencies
    if selected:
        value["obligation_ids"] = selected
        value["obligation_versions"] = {key: case["obligations"][key]["version"] for key in selected}
    if value["reply_message_id"] != snap["trigger_id"]:
        raise ValueError("reply must bind the observed triggering message")
    if p.get('summary') is not None:
        value['summary'] = text(p['summary'], 'draft summary', 2000)
        value['assessed_evidence'] = p['assessed_evidence']
    value["hash"] = digest(value)
    state["candidates"][key] = value
    return value


def check(state, p):
    lease(state, p)
    c = get_candidate(state, p)
    if c["status"] != "ready" or p.get("candidate_hash") != c["hash"]:
        raise ValueError("check must bind exact ready candidate")
    reviewer = text(p.get("reviewer_id"), "reviewer_id")
    if reviewer == c["writer_id"] or p.get("independent") is not True:
        raise ValueError("fresh independent reviewer required")
    if p.get("decision") not in {"send", "draft", "reject"}:
        raise ValueError("invalid checker decision")
    assessments = p.get("assessments", {})
    required = {"grounded", "answers_ask", "identity", "recipients", "privacy", "authority", "no_loop"}
    if c.get('summary'):
        required.add('summary')
    if set(assessments) != required or not all(type(v) is bool for v in assessments.values()):
        raise ValueError("all checker assessments required")
    if p["decision"] == "send" and not all(assessments.values()):
        raise ValueError("failed assessment blocks send")
    if c.get('summary') and p['decision'] != 'reject' and assessments.get('summary') is not True:
        raise ValueError('independent checker must verify exact draft summary')
    if p["decision"] == "draft" and not all(assessments[k] for k in ("grounded", "identity", "recipients", "privacy")):
        raise ValueError("a draft must still be truthful and correctly addressed")
    record = {"candidate_hash": c["hash"], "decision": p["decision"], "reviewer_id": reviewer,
              "assessments": assessments, "reason": text(p.get("reason"), "reason", 10000),
              "evidence_ref": text(p.get("evidence_ref"), "independent reviewer evidence_ref"), "at": stamp()}
    state["checks"][c["id"]] = record
    if c.get('assessed_evidence'):
        case = state['cases'][c['case_id']]
        if p['decision'] == 'reject':
            case['reply_repair'] = {'candidate_id': c['id'], 'candidate_hash': c['hash'],
                                    'reason': record['reason'], 'next': 'new_assessment_candidate'}
        elif case.get('reply_repair', {}).get('candidate_id') == c['id']:
            case.pop('reply_repair', None)
    return record


def current_candidate(root, state, c, live, sending):
    if c["status"] != "ready":
        raise ValueError("candidate already consumed or cancelled")
    if not state["active"] or state["paused"] or c["authority_version"] != state["version"]:
        raise ValueError("authority changed or inactive")
    validate_profile(root, state["config"]["profile_files"])
    if c.get("knowledge_dependencies"):
        import inbox_knowledge
        if not inbox_knowledge.check_dependencies(root, c["knowledge_dependencies"])["current"]:
            raise ValueError("learned knowledge changed; candidate obsolete")
    case = state["cases"][c["case_id"]]
    if candidate_held(state, case, c):
        raise ValueError("case held")
    if any(case.get("obligations", {}).get(key, {}).get("version") != version
           for key, version in c.get("obligation_versions", {}).items()):
        raise ValueError("obligation changed; candidate obsolete")
    snapshot(live)
    inbox_priority.require_scope(state, case, live)
    if (live["thread_id"] != c["thread_id"] or live["trigger_id"] != c["trigger_id"] or
            live["fingerprint"] != c["snapshot_fingerprint"] or
            case["snapshot"]["fingerprint"] != c["snapshot_fingerprint"]):
        raise ValueError("thread changed; candidate obsolete")
    if live["draft_ids"]:
        raise ValueError("preserve existing draft")
    if not fresh(live["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
        raise ValueError("fresh live snapshot required")
    for key, version in c["source_versions"].items():
        source = state["sources"].get(key)
        if (not source or source_policy(state, key) is None or source["version"] != version or
                source["status"] != "current" or iso(source["valid_until"]) <= utcnow() or
                iso(source["verified_at"]) > utcnow() + timedelta(seconds=5)):
            raise ValueError("source changed, unavailable or stale: " + key)
        if source.get("local_path"):
            path = Path(source["local_path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["content_hash"]:
                raise ValueError("local knowledge changed: " + key)
    review = state["checks"].get(c["id"])
    if not review or review["candidate_hash"] != c["hash"] or review["decision"] == "reject":
        raise ValueError("candidate needs independent check")
    if sending and review["decision"] != "send":
        raise ValueError("checker requires member review")
    return case


def prepare_send(root, state, p):
    lease(state, p)
    c = get_candidate(state, p)
    live = p.get("snapshot")
    case = current_candidate(root, state, c, live, True)
    config = state["config"]
    if c["mode"] != "send" or c["kind"] == "backlog" or live["automated"]:
        raise ValueError("draft-only, backlog or automated-message candidate")
    if not config["transport"]["send"] or not config["transport"]["independent_check"]:
        raise ValueError("send transport and independent checker capability required")
    rule = next((r for r in config["rules"] if r["id"] == c["rule_id"]), None)
    once = case.get("once_rule")
    if (once and c["rule_id"] == once["id"] and not once["used"] and
            once["trigger_id"] == c["trigger_id"] and once["version"] == state["version"] and
            c["kind"] == "reply"):
        rule = once
    if not rule:
        raise ValueError("approved reply rule required")
    allowed = addresses(live["correspondents"]) if rule["recipient_mode"] == "correspondents" else rule["recipients"]
    if not set(c["recipients"] + c["cc"]) <= set(allowed) or state["mailbox"] in c["recipients"]:
        raise ValueError("recipients exceed rule or create self-reply loop")
    if c["attachments"] and not rule["allow_attachments"]:
        raise ValueError("attachments not approved")
    if c["kind"] == "followup":
        followup = config["followups"]
        selected = [case["obligations"][key] for key in c.get("obligation_ids", [])]
        due_times = [row.get("due_at") for row in selected] if selected else [case.get("due_at")]
        if (not followup["enabled"] or rule.get("allow_followups") is not True or
                case["state"] != "waiting_correspondent" or not case["last_sent_at"] or
                case["followup_count"] >= followup["max_attempts"] or
                utcnow() < iso(case["last_sent_at"]) + timedelta(hours=followup["delay_hours"]) or
                any(not due_at or iso(due_at) > utcnow() for due_at in due_times)):
            raise ValueError("followup not eligible or limit reached")
    # Same mailbox/thread/trigger cannot be re-attempted after an unknown outcome.
    key = digest([state["mailbox"], c["thread_id"], c["trigger_id"], c["kind"],
                  case["followup_count"] if c["kind"] == "followup" else 0])
    if key in state["actions"]:
        raise ValueError("trigger already reserved; never blindly retry")
    actions = list(state["actions"].values())
    today = utcnow().date()
    if (sum(a["run_id"] == p["run_id"] for a in actions) >= config["limits"]["per_run"] or
            sum(iso(a["created_at"]).date() == today for a in actions) >= config["limits"]["per_day"]):
        raise ValueError("send limit reached (uncertain attempts count)")
    action = {"id": key, "candidate_id": c["id"], "candidate_hash": c["hash"],
              "case_id": case["id"], "mailbox": state["mailbox"], "thread_id": c["thread_id"],
              "trigger_id": c["trigger_id"], "run_id": p["run_id"], "status": "attempting",
              "created_at": stamp(), "send_token": secrets.token_hex(24)}
    state["actions"][key] = action
    if rule is once:
        once["used"] = True
    c["status"] = "attempting"
    case["hold"] = "send_unconfirmed"
    if c["kind"] == "followup":
        case["followup_count"] += 1
    event(state, "send_intent", action_id=key, case_id=case["id"])
    return {"action": action, "payload": c}


def invalidate_draft_inventory(state, reason):
    inventory = (state.get("retrieval") or {}).get("inventory")
    if inventory:
        from inbox_retrieval import invalidate_state
        invalidate_state(state, state["mailbox"], inventory["inventory_id"], reason)


def send_result(state, p, reconciliation=False):
    key = text(p.get("action_id"), "action_id")
    action = state["actions"].get(key)
    if not action:
        raise ValueError("unknown send action")
    if not reconciliation and p.get("send_token") != action["send_token"]:
        raise ValueError("send token mismatch")
    status = p.get("status")
    if status not in {"accepted", "sent", "unknown", "failed"}:
        raise ValueError("invalid send result")
    if action["status"] in {"accepted", "sent", "failed"}:
        if action["status"] == status and p.get("message_id") == action.get("message_id"):
            return {"action": action, "idempotent": True}
        if not (action["status"] == "accepted" and status == "sent"):
            raise ValueError("terminal action cannot change")
    if reconciliation:
        text(p.get("reconciliation_evidence"), "reconciliation_evidence", 10000)
    if status in {"accepted", "sent"}:
        for field in ("message_id", "provider_receipt"):
            text(p.get(field), field, 10000)
        if p.get("mailbox") != state["mailbox"] or p.get("thread_id") != action["thread_id"]:
            raise ValueError("receipt identity mismatch")
        if p.get("candidate_hash") != action["candidate_hash"]:
            raise ValueError("receipt content not bound to candidate")
        if status == "sent" and p.get("read_back") is not True:
            raise ValueError("sent state needs provider read-back")
        if any(a["id"] != key and a.get("message_id") == p["message_id"] for a in state["actions"].values()):
            raise ValueError("provider message already recorded against another action")
        action.update({k: p[k] for k in ("message_id", "provider_receipt", "candidate_hash")})
    if status == "failed" and p.get("definitive_not_sent") is not True:
        raise ValueError("absence or timeout cannot prove failure; use unknown")
    if status in {"failed", "unknown"}:
        text(p.get("reason"), "reason", 10000)
        action["reason"] = p["reason"]
    invalidate_draft_inventory(state, "send result: " + status)
    action["status"] = status
    action["updated_at"] = stamp()
    if reconciliation:
        action["reconciliation_evidence"] = p["reconciliation_evidence"]
    c = state["candidates"][action["candidate_id"]]
    c["status"] = status
    case = state["cases"][action["case_id"]]
    case["hold"] = "send_unknown" if status == "unknown" else ("send_failed" if status == "failed" else None)
    if status in {"accepted", "sent"}:
        case["state"] = "waiting_owner" if case["takeover"] or case["pending_decision"] else "waiting_correspondent"
        case["last_sent_at"] = action["created_at"]
        if state["config"]["followups"]["enabled"]:
            case["due_at"] = (iso(case["last_sent_at"]) + timedelta(hours=state["config"]["followups"]["delay_hours"])).isoformat()
        for key in c.get("obligation_ids", []):
            row = case["obligations"][key]
            row.update(state="waiting_correspondent", version=row["version"] + 1, updated_at=stamp())
            if state["config"]["followups"]["enabled"] and not row.get("due_at"):
                row["due_at"] = case["due_at"]
        if case.get("obligations") and not (case["takeover"] or case["pending_decision"]):
            case["state"] = obligation_state(case)
    event(state, "send_" + status, action_id=key, case_id=case["id"])
    return {"action": action, "idempotent": False}


def draft(root, state, p):
    """Reserve before provider write, then confirm it. Unknown creation holds case."""
    lease(state, p)
    c = get_candidate(state, p)
    if p.get("phase") == "reserve":
        case = current_candidate(root, state, c, p.get("snapshot"), False)
        if not state["config"]["transport"]["drafts"]:
            raise ValueError("draft capability unavailable")
        from inbox_learning import body_hash
        state["drafts"][c["id"]] = {**c, "draft_id": None, "status": "creating", "reserved_at": stamp(),
                                   "body_hash": body_hash(c["body"]), "excluded": case.get("learning_excluded", False)}
        c["status"] = "draft_reserved"
        case["pending_decision"] = True
        case["pending_candidate_id"] = c["id"]
        case["state"] = "waiting_owner"
        event(state, "draft_reserved", case_id=case["id"], candidate_id=c["id"])
    elif p.get("phase") == "confirm":
        record = state["drafts"].get(c["id"])
        if not record or record["status"] not in {"creating", "unknown"}:
            raise ValueError("draft reservation required")
        if p.get("candidate_hash") != c["hash"] or p.get("thread_id") != c["thread_id"] or p.get("mailbox") != state["mailbox"]:
            raise ValueError("draft receipt mismatch")
        record["draft_id"] = text(p.get("draft_id"), "draft_id")
        record["provider_receipt"] = text(p.get("provider_receipt"), "provider_receipt", 10000)
        record["status"] = "pending"
        c["status"] = "drafted"
        case = state["cases"][c["case_id"]]
        if case["hold"] == "draft_unknown":
            case["hold"] = "owner_decision" if pending_decisions(state, case) else None
        event(state, "draft_created", case_id=c["case_id"], candidate_id=c["id"])
    elif p.get("phase") in {"failed", "unknown"}:
        record = state["drafts"].get(c["id"])
        if not record or record["status"] not in {"creating", "unknown"}:
            raise ValueError("unconfirmed draft reservation required")
        text(p.get("reason"), "draft result reason", 10000)
        case = state["cases"][c["case_id"]]
        if p["phase"] == "failed":
            if p.get("definitive_not_created") is not True:
                raise ValueError("missing draft alone cannot prove creation failed")
            text(p.get("provider_receipt"), "failure provider receipt", 10000)
            case["pending_decision"] = bool(pending_decisions(state, case))
            case["pending_candidate_id"] = None
            case["hold"] = "draft_failed"
            case["state"] = "waiting_owner" if case["pending_decision"] else "needs_action"
        else:
            case["hold"] = "draft_unknown"
        record["status"] = p["phase"]
        record["reason"] = p["reason"]
        c["status"] = "draft_" + p["phase"]
    else:
        raise ValueError("draft phase must be reserve, confirm, failed or unknown")
    if p["phase"] != "reserve":
        invalidate_draft_inventory(state, "draft result: " + p["phase"])
    return state["drafts"][c["id"]]


def observe(state, p):
    from inbox_learning import reconcile
    key = text(p.get("candidate_id"), "candidate_id")
    record = state["drafts"].get(key)
    if not record:
        raise ValueError("unknown draft candidate")
    observation = p.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("observation required")
    obs_id = text(observation.get("id"), "observation id")
    prior = state["observations"].get(obs_id)
    if prior:
        if prior["candidate_id"] != key or prior["input_hash"] != digest(observation):
            raise ValueError("observation id reused with different evidence")
        return prior
    bot_ids = [a["message_id"] for a in state["actions"].values() if a.get("message_id")]
    comparison = dict(record)
    comparison["body"] = record.get("latest_bot_body", record.get("body"))
    result = reconcile(comparison, observation, state["config"]["learning"], bot_ids)
    send_id = observation.get("sent_message_id")
    if result.get("member_handled") and send_id in state["member_sends"]:
        result.update({"member_handled": False, "learnable": False,
                       "reason": "provider member-send event already reconciled"})
    if record.get("excluded") or record.get("body") is None:
        result.update({"learnable": False, "reason": "excluded or comparison text expired"})
    item = {"id": obs_id, "candidate_id": key, "input_hash": digest(observation),
            "observation": observation, "result": result, "at": stamp(),
            "case_id": record["case_id"], "status": "learning_pending" if result.get("learnable") else "observed"}
    state["observations"][obs_id] = item
    record["last_observation"] = obs_id
    if result.get("member_handled"):
        state["member_sends"][send_id] = key
        case = state["cases"][record["case_id"]]
        absence = case.get("draft_absence", {})
        if record.get("draft_id") in absence.get("draft_ids", []):
            absence["draft_ids"].remove(record["draft_id"])
            case["released_draft_absence"] = sorted(set(
                case.get("released_draft_absence", []) + [record["draft_id"]]))
            if not absence["draft_ids"]:
                case.pop("draft_absence", None)
        # Adoption releases only the decision that offered this exact draft. A
        # later owner exception cannot be cleared by re-observing older mail.
        if case.get("pending_candidate_id") == key and not case.get("draft_absence"):
            case["pending_decision"] = False
            case["pending_candidate_id"] = None
            case["state"] = "waiting_owner" if case["takeover"] else "needs_action"
            if case["hold"] in {None, "owner_decision"}:
                case["hold"] = None
            if pending_decisions(state, case):
                case.update(hold="owner_decision", pending_decision=True, state="waiting_owner")
        for obligation_id in record.get("obligation_ids", []):
            row = case["obligations"][obligation_id]
            if row["state"] != "handled":
                row.update(state="needs_action", version=row["version"] + 1, updated_at=stamp())
        if record.get("obligation_ids"):
            enqueue_recheck(state, case, "owner sent draft: " + key)
        record["status"] = "member_sent"
        for c in state["candidates"].values():
            if c["case_id"] == case["id"] and c["status"] == "ready":
                c["status"] = "cancelled"
    return item


def learn(state, p):
    from inbox_learning import derive_lesson, reconcile
    if not state["config"]["learning"]["enabled"]:
        raise ValueError("learning paused")
    observation = state["observations"].get(p.get("observation_id"))
    if not observation:
        raise ValueError("unknown observation")
    c = state["drafts"][observation["candidate_id"]]
    if c.get("excluded") or c.get("body") is None:
        raise ValueError("learning excluded or original text expired")
    if c["profile_version"] != state["version"]:
        raise ValueError("old profile lesson must be reviewed, not applied automatically")
    for source_id, version in c["source_versions"].items():
        source = state["sources"].get(source_id)
        if (not source or source["status"] != "current" or source["version"] != version or
                iso(source["valid_until"]) <= utcnow() or source_policy(state, source_id) is None):
            raise ValueError("lesson source is stale, changed or unavailable")
        if source.get("local_path"):
            path = Path(source["local_path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["content_hash"]:
                raise ValueError("lesson local source changed")
    bot_ids = [a["message_id"] for a in state["actions"].values() if a.get("message_id")]
    current_result = reconcile(c, observation["observation"], state["config"]["learning"], bot_ids)
    if not observation["result"]["learnable"] or not current_result["learnable"]:
        raise ValueError("learning evidence no longer eligible under current capability")
    result = derive_lesson(c, observation["observation"], current_result, p.get("proposal"))
    key = text(p.get("lesson_id"), "lesson_id")
    if key in state["lessons"]:
        raise ValueError("lesson revisions need a new id; undo or contradict the old lesson")
    if any(l["observation_id"] == observation["id"] and l["kind"] == result["kind"] and l["scope"] == result["scope"]
           for l in state["lessons"].values()):
        raise ValueError("this scoped observation already supports a lesson")
    result.update({"id": key, "observation_id": observation["id"], "created_at": stamp(),
                   "source_versions": c["source_versions"], "profile_version": c["profile_version"]})
    for prior_id in p.get("contradicts", []):
        prior = state["lessons"].get(prior_id)
        if not prior:
            raise ValueError("unknown contradicted lesson")
        prior["status"] = "paused"
        prior["contrary_observation"] = observation["id"]
    state["lessons"][key] = result
    return result


def learning_control(state, p):
    approval = owned(p)
    action = p.get("action")
    if action in {"pause", "resume", "invalidate_actor"}:
        if action == "invalidate_actor":
            state["config"]["learning"]["invalidated"] = True
            for item in state["lessons"].values():
                if item["status"] == "active":
                    item["status"] = "invalidated"
        else:
            state["config"]["learning"]["enabled"] = action == "resume"
    elif action in {"undo", "contradict"}:
        item = state["lessons"].get(p.get("lesson_id"))
        if not item:
            raise ValueError("unknown lesson")
        item["status"] = "undone" if action == "undo" else "paused"
        item["control_reason"] = text(p.get("reason"), "reason", 10000)
    elif action == "exclude":
        case = get_case(state, p)
        case["learning_excluded"] = True
        for item in state["drafts"].values():
            if item["case_id"] == case["id"]:
                item["excluded"] = True
                item["body"] = None
        for item in state["lessons"].values():
            obs = state["observations"].get(item["observation_id"])
            if obs and (obs.get("case_id") == case["id"] or state["drafts"].get(obs.get("candidate_id"), {}).get("case_id") == case["id"]):
                item["status"] = "undone"
        for item in state["observations"].values():
            if item.get("case_id") == case["id"] or state["drafts"].get(item.get("candidate_id"), {}).get("case_id") == case["id"]:
                item["observation"].pop("final_body", None)
                item["result"].pop("final_body", None)
        for item in state["candidates"].values():
            if item["case_id"] == case["id"] and item["status"] != "ready":
                item["body"] = None
    else:
        raise ValueError("invalid learning control")
    event(state, "learning_" + action, approval_ref=approval)
    return {"action": action}


def due(state):
    result = []
    for case in state["cases"].values():
        if not inbox_priority.case_in_scope(state, case):
            continue
        if case.get("obligations"):
            for row in case["obligations"].values():
                if row["state"] == "handled" or not row.get("due_at") or iso(row["due_at"]) > utcnow():
                    continue
                followup = state["config"]["followups"]
                can = (followup["enabled"] and row["state"] == "waiting_correspondent" and not held(state, case)
                       and case["followup_count"] < followup["max_attempts"])
                result.append({"case_id": case["id"], "obligation_id": row["id"], "due_at": row["due_at"],
                               "next": "evaluate_followup" if can else "owner_attention", "obligation": row, "case": case})
            continue
        if case["state"] == "handled" or not case.get("due_at") or iso(case["due_at"]) > utcnow():
            continue
        followup = state["config"]["followups"] if state["config"] else {"enabled": False, "max_attempts": 0}
        can = (followup["enabled"] and case["state"] == "waiting_correspondent" and
               not held(state, case) and
               case["followup_count"] < followup["max_attempts"])
        result.append({"case_id": case["id"], "due_at": case["due_at"],
                       "next": "evaluate_followup" if can else "owner_attention", "case": case})
    return sorted(result, key=lambda item: iso(item["due_at"]))


def report(state):
    return {"mailbox": state["mailbox"], "active": state["active"], "paused": state["paused"],
            "cleanup": inbox_archive.work(state),
            "cleanup_results": dict(Counter(a["status"] for a in state.get("mailbox_actions", {}).values())),
            "version": state["version"], "queued": len(inbox_priority.ordered_queue(state)), "scan": state["scan"] if "all" in inbox_priority.lane_queries((state.get("config") or {}).get("inbox_priority")) else None,
            "inbox_scans": inbox_priority.active_scans(state), "coverage": inbox_priority.coverage(state),
            "conversations": dict(Counter(c["state"] for c in state["cases"].values() if inbox_priority.case_in_scope(state, c))),
            "outcomes": dict(Counter(c.get("outcome") for c in state["cases"].values() if c.get("outcome"))),
            "send_results": dict(Counter(a["status"] for a in state["actions"].values() if inbox_priority.case_in_scope(state, state["cases"][a["case_id"]]))),
            "drafts": dict(Counter(d["status"] for d in state["drafts"].values() if inbox_priority.case_in_scope(state, state["cases"][d["case_id"]]))),
            "attention": [c for c in state["cases"].values() if inbox_priority.case_in_scope(state, c) and (c["hold"] or owner_work(state, c) or
                          any(owner_work(state, row) for row in c.get("obligations", {}).values()))],
            "owner_actions": action_table(state), "waiting_on_others": action_table(state, waiting=True),
            "due": due(state), "lessons": state["lessons"], "lease": state["lease"],
            "obligations": dict(Counter(row["state"] for c in state["cases"].values() for row in c.get("obligations", {}).values())),
            "decisions": [d for d in state["decisions"].values() if d["status"] in {"pending", "rule_requested"}],
            "notifications_due": decision_notifications(state), "knowledge_gaps": list(state["knowledge_gaps"].values()),
            "source_refresh": source_work(state), "reevaluations": [row for key, row in state["reevaluations"].items() if inbox_priority.case_in_scope(state, state["cases"][key])]}


def prune(state):
    days = state["config"]["learning"]["retention_days"] if state["config"] else 30
    cutoff = utcnow() - timedelta(days=days)
    count = 0
    for collection in ("drafts", "candidates"):
        for item in state[collection].values():
            if iso(item["created_at"]) < cutoff and item.get("body") is not None:
                # Reserved/unknown actions retain identifiers, hashes and receipts, not bodies forever.
                item["body"] = None
                item.pop("evidence", None)
                if collection == "candidates" and item["status"] == "ready":
                    item["status"] = "expired"
                count += 1
    for item in state["observations"].values():
        if iso(item["at"]) < cutoff:
            item["observation"].pop("final_body", None)
            item["result"].pop("final_body", None)
    compacted = 0
    active_run = (state.get("lease") or {}).get("run_id")
    for run_id, run in state["runs"].items():
        if (run_id == active_run or not run.get("finished_at") or
                iso(run["finished_at"]) >= cutoff or not isinstance(run.get("cycle_result"), dict)):
            continue
        # Keep the outcome and exact registry evidence identity, without retaining
        # another copy of the whole mailbox in every historical run.
        if not run.get("finish_receipt"):
            from inbox_cycle import compact_finish_receipt
            run["finish_receipt"] = compact_finish_receipt(
                run["cycle_result"], auth_hash=run.get("auth_hash"),
                finished_at=run["finished_at"], run_id=run_id,
            )
        del run["cycle_result"]
        compacted += 1
    return {"comparison_bodies_pruned": count, "cycle_reports_compacted": compacted,
            "retention_days": days}


def _execute(home, command, payload=None, *, full=True):
    p = payload or {}
    if not isinstance(p, dict):
        raise ValueError("JSON object required")
    if command == "schema":
        import inbox_contracts
        return inbox_contracts.schema(p.get("command"))
    if command.startswith("storage-"):
        import inbox_storage_admin
        return inbox_storage_admin.execute(home, command, p)
    if command.startswith("knowledge-") and command != "knowledge-reviewed":
        import inbox_knowledge
        return inbox_knowledge.execute(home, command, p)
    if command.startswith("revision-"):
        import inbox_revisions
        return inbox_revisions.execute(home, command, p)
    if command == "answers":
        import inbox_feedback
        result = inbox_feedback.execute(home, command, p)
        # Semantic note contents come from the host; store only authenticated actions.
        import inbox_knowledge
        receipts = []
        for answer in result.get("answers", {}).values():
            for action in answer.get("actions", []):
                if action.get("kind") == "record_knowledge":
                    try:
                        receipts.append(inbox_knowledge.execute(home, "knowledge-record", {**action["record"], "origin": action["origin"]}))
                    except (ValueError, sqlite3.Error) as exc:
                        receipts.append({"status":"pending", "reason":str(exc), "origin":action["origin"]})
        if receipts:
            result["knowledge"] = receipts
        return result
    with transaction(home, create=command == "init", readonly=command in {
            "status", "work", "due", "report", "lookup", "cleanup-work", "undo-window"}) as (root, box):
        state = box[0]
        if command == "init":
            mailbox = addresses([p.get("mailbox")])[0]
            if state and state["mailbox"] != mailbox:
                raise ValueError("this home belongs to another mailbox")
            if state is None:
                state = initial(mailbox)
                box[0] = state
            return {"mailbox": mailbox, "active": state["active"], "legacy_preserved": (root / "state.json").exists()}
        if state is None:
            raise ValueError("inbox engine not initialized")
        if command == "report" and p.get("format") == "markdown":
            import inbox_feedback
            return inbox_feedback.render(state, base_report=owner_report(state))
        if command == "status":
            if full:
                return inbox_store.plain(state)
            return {**compact_report(state), "config": state["config"],
                    "lookup": "Use lookup for one case/thread/candidate/action/gap; --full is diagnostic only."}
        if command == "lookup":
            return lookup(state, p)
        if command == "adopt-workflow":
            approval = owned(p)
            if not state.get("config"):
                raise ValueError("configure the inbox first")
            if state.get("workflow_version") == 11 and state["config"].get("draft_first") and state["config"]["learning"].get("retention_days") == 90:
                return {"workflow_version":11,"draft_first":True,"body_retention_days":90,"idempotent":True}
            state["version"] += 1
            state["config"]["draft_first"] = True
            state["config"]["learning"]["retention_days"] = 90
            state["config"].setdefault("research_guide", [{"source_id":key,"purpose":v["use"]} for key,v in state["config"]["source_policy"].items()])
            state["workflow_version"] = 11
            event(state, "workflow_adopted", approval_ref=approval)
            return {"workflow_version":11,"draft_first":True,"body_retention_days":90}
        if command == "knowledge-reviewed":
            lease(state, p)
            observation = state["observations"].get(p.get("observation_id"))
            if not observation or observation.get("status") not in {"learning_pending", "learned", "reviewed"}:
                raise ValueError("unknown eligible learning observation")
            observation.update(status="reviewed", review_reason=text(p.get("reason"), "learning review reason", 4000))
            return {"observation_id":observation["id"], "status":"reviewed"}
        if command == "owner-sent-observe":
            import inbox_knowledge
            return inbox_knowledge.record_provider_observation(state, p)
        if command == "owner-event":
            import inbox_knowledge
            return inbox_knowledge.save_authenticated_owner_event(state, p)
        if command == "maintain":
            import inbox_maintenance
            return inbox_maintenance.maintain(state, p.get("limit", 100))
        if command == "configure":
            return configure(root, state, p)
        if command == "source":
            return update_source(state, p)
        if command == "context-source":
            return register_context_source(state, p)
        if command in inbox_archive.COMMANDS:
            validate_profile(root, state["config"]["profile_files"])
            return inbox_archive.COMMANDS[command](state, p)
        if command == "cleanup-work":
            return inbox_archive.work(state)
        if command == "undo-window":
            return inbox_archive.undo_window(state, p.get("since"))
        if command == "import-archive":
            return inbox_archive.import_legacy(state, inbox_archive.read_legacy(root), p)
        if command == "owner":
            return owner_action(state, p)
        if command == "reminder":
            return reminder_action(state, p)
        if command == "claim":
            if state["paused"]:
                raise ValueError("inbox paused")
            current = state["lease"]
            if current and iso(current["expires_at"]) > utcnow():
                raise ValueError("another run owns the inbox")
            run_id = text(p.get("run_id"), "run_id")
            if run_id in state["runs"]:
                raise ValueError("run id already used; choose a fresh id")
            minutes = number(p.get("minutes", 15), 1, 90, "lease minutes")
            for action in inbox_store.selected(state, "actions", status="attempting"):
                if action["status"] == "attempting":
                    action["status"] = "unknown"
                    state["cases"][action["case_id"]]["hold"] = "send_unknown"
            state["lease"] = {"run_id": run_id, "token": secrets.token_hex(24),
                              "expires_at": (utcnow() + timedelta(minutes=minutes)).isoformat()}
            state["runs"][run_id] = {"started_at": stamp()}
            return state["lease"]
        if command == "renew":
            current = lease(state, p)
            current["expires_at"] = (utcnow() + timedelta(minutes=15)).isoformat()
            return current
        if command == "release":
            current = lease(state, p)
            state["runs"][current["run_id"]]["finished_at"] = stamp()
            state["lease"] = None
            return {"released": True}
        dispatch = {"case": lambda: update_case(state, p), "hold": lambda: hold(state, p),
                    "scan": lambda: scan(state, p), "work": lambda: work(state) if full else compact_work(state),
                    "recover": lambda: recover(state, p), "candidate": lambda: candidate(state, p),
                    "check": lambda: check(state, p), "prepare-send": lambda: prepare_send(root, state, p),
                    "send-result": lambda: send_result(state, p),
                    "reconcile-send": lambda: send_result(state, p, True),
                    "draft": lambda: draft(root, state, p), "observe": lambda: observe(state, p),
                    "lesson": lambda: learn(state, p), "learning-control": lambda: learning_control(state, p),
                    "due": lambda: due(state) if full else compact_due(state, due(state)),
                    "report": lambda: report(state) if full else compact_report(state), "prune": lambda: prune(state),
                    "gap": lambda: record_gap(state, p), "gap-resolve": lambda: resolve_gap(state, p),
                    "decision-answer": lambda: decision_answer(state, p),
                    "notification-receipt": lambda: notification_receipt(state, p)}
        if command not in dispatch:
            raise ValueError("unknown command")
        return dispatch[command]()


def execute(home, command, payload=None, *, full=True):
    # Public values are detached; lazy storage is an internal implementation detail.
    result = inbox_store.plain(_execute(home, command, payload, full=full))
    if command == "adopt-workflow" or (command == "configure" and (payload or {}).get("config", {}).get("draft_first")):
        import inbox_knowledge
        result["knowledge_migration"] = inbox_knowledge.migrate_legacy(home)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("schema", "storage-health", "storage-backup", "storage-restore", "answers", "owner-event", "owner-sent-observe", "knowledge-reviewed", "maintain", "knowledge-record", "knowledge-search", "knowledge-lookup", "knowledge-forget", "knowledge-maintain", "revision-candidate", "revision-prepare", "revision-confirm", "revision-reconcile", "revision-retire", "adopt-workflow", "init", "status", "configure", "source", "case", "hold", "recover", "owner", "reminder", "claim", "scan", "work",
                        "renew", "release", "candidate", "check", "prepare-send", "send-result", "reconcile-send",
                        "draft", "observe", "lesson", "learning-control", "due", "report", "prune",
                        "gap", "gap-resolve", "decision-answer", "notification-receipt", "lookup", "context-source",
                        "prepare-cleanup", "cleanup-result", "cleanup-work", "undo-window", "import-archive"))
    parser.add_argument("--home", help="Inbox home; not required for schema")
    parser.add_argument("--command", dest="schema_command", help="Command to describe with schema")
    parser.add_argument("--input", help="Reuse an existing private JSON packet instead of stdin")
    parser.add_argument("--full", action="store_true", help="Full diagnostic output for status/work/report/due")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",
                        help="Use report --format markdown for the complete owner-facing report")
    parser.add_argument("--since", help="Earliest timestamp for undo-window")
    for field in ("case_id", "thread_id", "candidate_id", "action_id", "gap_id", "observation_id", "revision_id"):
        parser.add_argument("--" + field.replace("_", "-"), help="Direct lookup selector; no JSON file needed")
    args = parser.parse_args()
    try:
        if args.command != "schema" and not args.home:
            raise ValueError("--home is required for inbox operations")
        if args.schema_command and args.command != "schema":
            raise ValueError("--command requires schema")
        if args.format == "markdown" and (args.command != "report" or args.full or args.input):
            raise ValueError("--format markdown requires report without --full or --input")
        selectors = {field: getattr(args, field) for field in
                     ("case_id", "thread_id", "candidate_id", "action_id", "gap_id", "observation_id", "revision_id") if getattr(args, field)}
        if args.schema_command:
            if args.input or selectors:
                raise ValueError("schema --command cannot also use input/selectors")
            p = {"command":args.schema_command}
        elif selectors:
            if args.command != "lookup" or args.input:
                raise ValueError("direct lookup selectors require lookup and no --input")
            p = selectors
        elif args.command == "undo-window":
            p = {"since": args.since} if args.since else {}
        elif args.command in {"status", "due", "report", "prune", "work", "cleanup-work"}:
            p = {"format": "markdown"} if args.format == "markdown" else {}
        elif args.input:
            p = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            if sys.stdin.isatty():
                raise ValueError("JSON input requires file redirection; interactive terminals are not supported")
            p = json.load(sys.stdin)
        result = execute(args.home, args.command, p, full=args.full)
        if args.format == "markdown":
            print(result, end="")
        else:
            print(json.dumps({"ok": True, "result": result}, separators=(",", ":"), ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
