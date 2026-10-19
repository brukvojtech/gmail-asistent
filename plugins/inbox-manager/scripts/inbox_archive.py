"""Mailbox presentation actions and lossless legacy import; no provider or DB writes.

Call mutators inside the engine transaction. Commit each returned intention before
performing its message-scoped provider mutations; pass actual complete readbacks.
"""
from __future__ import annotations

import copy
import inbox_priority
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from inbox_snapshot import build_snapshot

IMPORT_MARKER = "archive_import"
SYSTEM_LABELS = {"INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "UNREAD", "STARRED", "IMPORTANT",
                 "CHAT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS",
                 "CATEGORY_UPDATES", "CATEGORY_FORUMS"}

def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("timestamp must include timezone")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid " + field)
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _ids(value, field):
    if (not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value)
            or len(value) != len(set(value))):
        raise ValueError("invalid " + field)
    return sorted(value)


def policy(value=None):
    """Missing policy grants no cleanup authority; setup approval owns projection."""
    if value is None:
        return {"archive_on_resolution": False, "waiting_policy": "keep_inbox",
                "workflow_labels": {}, "draft_cleanup": "preserve"}
    if not isinstance(value, dict):
        raise ValueError("cleanup policy must be an object")
    result = copy.deepcopy(value)
    if type(result.get("archive_on_resolution")) is not bool:
        raise ValueError("explicit archive_on_resolution choice required")
    if result.get("waiting_policy") not in {"keep_inbox", "archive"}:
        raise ValueError("waiting_policy must be keep_inbox or archive")
    if result.get("draft_cleanup", "preserve") not in {"preserve", "plugin_obsolete"}:
        raise ValueError("unsupported draft_cleanup policy")
    result.setdefault("draft_cleanup", "preserve")
    labels = result.setdefault("workflow_labels", {})
    if not isinstance(labels, dict) or set(labels) - {"attention", "waiting"}:
        raise ValueError("workflow_labels supports attention and waiting only")
    all_labels = list(labels.values())
    if result.get("archive_on_resolution") or result.get("waiting_policy") == "archive":
        if "undo_label" not in result:
            raise ValueError("archive requires configured undo_label")
    if "undo_label" in result:
        all_labels.append(result["undo_label"])
    names, ids = [], []
    for label in all_labels:
        if not isinstance(label, dict) or set(label) != {"name", "id"}:
            raise ValueError("labels require exact name and provider id")
        names.append(_text(label["name"], "label name"))
        ids.append(_text(label["id"], "label id"))
        if label["id"] in SYSTEM_LABELS:
            raise ValueError("workflow and undo labels must be custom provider labels")
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise ValueError("undo and workflow labels must be distinct")
    if all_labels or result["draft_cleanup"] != "preserve":
        _text(result.get("approval_ref"), "cleanup approval_ref")
    return result


def _lease(state, p):
    current = state.get("lease")
    if (not current or current.get("run_id") != p.get("run_id") or
            current.get("token") != p.get("token") or _iso(current["expires_at"]) <= _now()):
        raise ValueError("current engine run lease required")
    if state.get("paused") or not state.get("active"):
        raise ValueError("cleanup authority inactive or paused")


def _basis(case, config):
    snapshot = copy.deepcopy(case.get("snapshot"))
    if isinstance(snapshot, dict):
        for field in ("observed_at", "thread_observed_at", "drafts_observed_at"):
            snapshot.pop(field, None)
        if snapshot.get("content_fingerprint"):
            snapshot.pop("fingerprint", None)
    return _hash({
        "case": {"snapshot": snapshot, **{k: case.get(k) for k in (
            "state", "obligations", "hold", "takeover", "pending_decision", "outcome",
            "outcome_evidence", "due_at")}},
        "cleanup_policy": config,
    })


def _legacy_basis(case):
    return _hash({k: case.get(k) for k in ("snapshot", "state", "obligations", "hold", "takeover",
                                          "pending_decision", "outcome", "outcome_evidence", "due_at")})


def _basis_matches(value, case, config):
    return value in {_basis(case, config), _legacy_basis(case)}


def _waiting_due(case):
    dates = [row.get("due_at") for row in case.get("obligations", {}).values()
             if row.get("state") == "waiting_correspondent"] or [case.get("due_at")]
    return case["state"] == "waiting_correspondent" and any(at and _iso(at) <= _now() for at in dates)


def _packet(state, packet, after=None, drafts_not_before=None):
    from inbox_snapshot import normalize_packet
    packet = normalize_packet(packet)
    snap = build_snapshot(packet)
    if packet["mailbox"] != state["mailbox"]:
        raise ValueError("cleanup mailbox mismatch")
    maximum = state["config"]["transport"]["snapshot_max_age_seconds"]
    if any(not -5 <= (_now() - _iso(snap[field])).total_seconds() <= maximum
           for field in ("thread_observed_at", "drafts_observed_at")):
        raise ValueError("fresh complete cleanup snapshot required")
    if after is not None and _iso(snap["thread_observed_at"]) < _iso(after):
        raise ValueError("readback predates reserved provider action")
    if drafts_not_before is not None and _iso(snap["drafts_observed_at"]) < _iso(drafts_not_before):
        raise ValueError("draft inventory predates reserved cleanup evidence")
    labels = {m["id"]: _ids(m.get("label_ids"), "complete message label_ids") for m in packet["messages"]}
    return snap, labels, snap["content_fingerprint"].removeprefix("sha256:")


def _same_conversation(expected, actual):
    stable = expected.get("content_fingerprint")
    if stable:
        return actual.get("content_fingerprint") == stable
    return actual.get("fingerprint") == expected.get("fingerprint")


def _blocked(state, case, snap, config):
    if case["thread_id"] != snap["thread_id"]:
        return "thread_identity_changed"
    if any(case.get(field) for field in ("hold", "takeover", "pending_decision")):
        return "case_held"
    if any(a.get("case_id") == case["id"] and a.get("status") in {"attempting", "unknown"}
           for a in state.get("actions", {}).values()):
        return "send_unconfirmed"
    if any(d.get("case_id") == case["id"] and d.get("status") == "pending"
           for d in state.get("decisions", {}).values()):
        return "owner_decision_pending"
    if snap["draft_ids"]:
        return "preserve_existing_drafts"
    if set(snap["message_ids"]) != set(case.get("snapshot", {}).get("message_ids", [])):
        return "new_or_unassessed_messages"
    if any(mid in state.get("queue", {}) for mid in snap["message_ids"]):
        return "unassessed_messages"
    unresolved = [row for row in case.get("obligations", {}).values() if row.get("state") != "handled"]
    if case["state"] == "handled":
        if unresolved:
            return "unresolved_obligations"
        if not case.get("outcome_evidence"):
            return "completion_evidence_required"
        if not config["archive_on_resolution"]:
            return "archive_not_approved"
    elif case["state"] == "waiting_correspondent":
        if config["waiting_policy"] != "archive":
            return "waiting_archive_not_approved"
        if any(row.get("state") != "waiting_correspondent" for row in unresolved):
            return "unresolved_owner_or_bot_obligations"
    else:
        return "conversation_requires_attention"
    return None


def _pending(case, reason, action_id=None):
    case["cleanup"] = {"status": "pending", "reason": reason, "action_id": action_id}
    return copy.deepcopy(case["cleanup"])


def _retained(case, reason, basis):
    case["cleanup"] = {"status": "retained", "kind": "retained_by_policy",
                       "reason": reason, "basis": basis}
    return copy.deepcopy(case["cleanup"])


def _policy_retention_reason(state, case, config):
    if any(case.get(field) for field in ("hold", "takeover", "pending_decision")):
        return None
    if any(a.get("case_id") == case["id"] and a.get("status") in {"attempting", "unknown"}
           for a in state.get("actions", {}).values()):
        return None
    if any(d.get("case_id") == case["id"] and d.get("status") == "pending"
           for d in state.get("decisions", {}).values()):
        return None
    if case.get("snapshot", {}).get("draft_ids"):
        return None
    if any(mid in state.get("queue", {}) for mid in case.get("snapshot", {}).get("message_ids", [])):
        return None
    unresolved = [row for row in case.get("obligations", {}).values() if row.get("state") != "handled"]
    if case["state"] == "handled":
        if not unresolved and case.get("outcome_evidence") and not config["archive_on_resolution"]:
            return "archive_not_approved"
    elif case["state"] == "waiting_correspondent" and config["waiting_policy"] != "archive":
        return "waiting_archive_not_approved"
    return None


def _desired_kind(state, case, config):
    policy_reason = _policy_retention_reason(state, case, config)
    if policy_reason:
        return "retained_by_policy", policy_reason
    if _waiting_due(case) and config["waiting_policy"] == "archive":
        return "restore_attention", None
    if case["state"] in {"needs_action", "waiting_owner"} and config["workflow_labels"].get("attention"):
        return "workflow_attention", None
    if case["state"] == "handled" and config["archive_on_resolution"]:
        return "archive", None
    if case["state"] == "waiting_correspondent" and config["waiting_policy"] == "archive":
        return "archive", None
    return None, None


def _view(action, mutations=None):
    return {"action_id": action["id"], "case_id": action.get("case_id"),
            "status": action["status"], "stage": action.get("stage"),
            "attempt": action.get("attempt", 0), "mutations": mutations or []}


def prepare(state, p):
    """Reserve undo-label write. A retry must first reconcile its existing receipt."""
    _lease(state, p)
    case = state["cases"].get(p.get("case_id"))
    if not case:
        raise ValueError("unknown cleanup case")
    config = policy(state["config"].get("cleanup"))
    snap, labels, content_hash = _packet(state, p.get("packet"))
    inbox_priority.require_scope(state, case, snap)
    if _waiting_due(case) and config["waiting_policy"] == "archive":
        return _prepare_attention(state, p, case, config, snap, labels, content_hash)
    if case["state"] in {"needs_action", "waiting_owner"} and config["workflow_labels"].get("attention"):
        return _prepare_attention(state, p, case, config, snap, labels, content_hash, restore=False)
    reason = _blocked(state, case, snap, config)
    if reason:
        if reason in {"archive_not_approved", "waiting_archive_not_approved"}:
            return _retained(case, reason, _basis(case, config))
        return _pending(case, reason)
    prior = next((a for a in state.get("mailbox_actions", {}).values()
                  if a.get("case_id") == case["id"] and a.get("status") in {"pending", "failed"}), None)
    if prior:
        return {**_view(prior), "reconcile_required": True}
    basis = _basis(case, config)
    key = _hash([state["mailbox"], case["id"], basis, state["version"], "archive"])
    actions = state.setdefault("mailbox_actions", {})
    if key in actions:
        existing = actions[key]
        if existing["status"] == "verified":
            expected = all(set(existing["add_label_ids"]) <= set(labels[mid]) and
                           not set(existing["remove_label_ids"]) & set(labels[mid]) for mid in existing["message_ids"])
            if not expected:
                return _pending(case, "verified_archive_changed_preserve", key)
        return _view(existing)
    known_content = any(a.get("case_id") == case["id"] and
                        a.get("status") in {"verified", "pending", "failed"} and
                        a.get("content_hash") == content_hash
                        for a in state.get("mailbox_actions", {}).values())
    if not _same_conversation(case["snapshot"], snap) and not known_content:
        return _pending(case, "thread_changed_reassess")
    inbox_ids = sorted(mid for mid, ids in labels.items() if "INBOX" in ids)
    waiting = config["workflow_labels"].get("waiting", {}).get("id")
    kept_waiting = {waiting} if case["state"] == "waiting_correspondent" and waiting else set()
    stale_labels = {row["id"] for row in config["workflow_labels"].values()} - kept_waiting
    selected = sorted(mid for mid, ids in labels.items() if "INBOX" in ids or stale_labels.intersection(ids))
    if not selected:
        case["cleanup"] = {"status": "verified", "kind": "archive", "reason": "already_outside_inbox", "basis": basis}
        return copy.deepcopy(case["cleanup"])
    undo_id = config["undo_label"]["id"]
    waiting = config["workflow_labels"].get("waiting", {}).get("id")
    add = sorted({undo_id} | ({waiting} if case["state"] == "waiting_correspondent" and waiting else set()))
    remove = {row["id"] for row in config["workflow_labels"].values()} - set(add)
    action = {"id": key, "kind": "archive", "case_id": case["id"], "thread_id": case["thread_id"],
              "mailbox": state["mailbox"], "run_id": p["run_id"], "authority_version": state["version"],
              "basis": basis, "content_hash": content_hash, "message_ids": selected,
              "inbox_message_ids": inbox_ids,
              "thread_observed_at": snap["thread_observed_at"],
              "drafts_observed_at": snap["drafts_observed_at"],
              "original_labels": labels, "add_label_ids": add, "remove_label_ids": sorted(remove | {"INBOX"}),
              "undo_label": copy.deepcopy(config["undo_label"]), "status": "pending", "stage": "undo",
              "created_at": _now().isoformat(), "reserved_at": _now().isoformat(), "attempt": 1, "receipts": []}
    actions[key] = action
    _pending(case, "provider_action_reserved", key)
    return _view(action, [{"message_id": mid, "add_label_ids": add, "remove_label_ids": []} for mid in selected])


def result(state, p):
    """Readback/recovery reserves the next safe label write or verifies completion.

    Supply action_id, attempt, packet, provider_ref. No receipt means no success;
    use error to record a failed call. A subsequent fresh readback can recover it.
    """
    _lease(state, p)
    action = state.get("mailbox_actions", {}).get(p.get("action_id"))
    if not action or action.get("kind") not in {"archive", "restore_attention", "workflow_attention"}:
        raise ValueError("unknown archive action")
    if action["status"] == "verified":
        return _view(action)
    if p.get("attempt") != action["attempt"]:
        raise ValueError("stale cleanup attempt; reload action")
    case = state["cases"][action["case_id"]]
    if p.get("error"):
        action["status"] = "failed"
        action["error"] = _text(p["error"], "provider error")
        _pending(case, "provider_failed", action["id"])
        return _view(action)
    snap, labels, content_hash = _packet(state, p.get("packet"), action["reserved_at"],
                                         action.get("drafts_observed_at"))
    inbox_priority.require_scope(state, case, snap)
    _text(p.get("provider_ref"), "actual provider readback reference")
    config = policy(state["config"].get("cleanup"))
    if action["kind"] == "archive":
        reason = _blocked(state, case, snap, config)
    elif action["kind"] == "restore_attention":
        reason = None if config["waiting_policy"] == "archive" and _waiting_due(case) else "waiting_policy_or_deadline_changed"
    else:
        reason = (None if config["workflow_labels"].get("attention") and case["state"] in {"needs_action", "waiting_owner"}
                  else "attention_policy_or_case_changed")
    if (reason or state["version"] != action["authority_version"] or
            not _basis_matches(action["basis"], case, config) or
            content_hash != action["content_hash"]):
        action["status"] = "blocked"
        return _pending(case, reason or "authority_or_conversation_changed", action["id"])
    targets = action["message_ids"]
    add, remove = set(action["add_label_ids"]), set(action["remove_label_ids"])
    # Verify custom user labels survived, and no action silently touched other mail.
    for mid, original in action["original_labels"].items():
        required = set(original) - (remove if mid in targets else set())
        if not required <= set(labels[mid]):
            action["status"] = "failed"
            return _pending(case, "unrelated_labels_changed", action["id"])
    undo_ok = all(add <= set(labels[mid]) for mid in targets)
    archive_ok = undo_ok and all(not (remove & set(labels[mid])) for mid in targets)
    if action["kind"] in {"restore_attention", "workflow_attention"}:
        if archive_ok:
            action["status"] = "verified"
            action["verified_at"] = _now().isoformat()
            action["final_labels"] = labels
            action["receipts"].append({"stage": action["kind"], "provider_ref": p["provider_ref"],
                                        "observed_at": snap["observed_at"], "labels": labels})
            case["cleanup"] = {"status": "verified", "kind": action["kind"], "action_id": action["id"],
                               "basis": action["basis"]}
            return _view(action)
        action["attempt"] += 1
        action["reserved_at"] = _now().isoformat()
        action["status"] = "pending"
        action["run_id"] = p["run_id"]
        return _view(action, [{"message_id": mid, "add_label_ids": sorted(add - set(labels[mid])),
                               "remove_label_ids": sorted(remove & set(labels[mid]))} for mid in targets
                              if add - set(labels[mid]) or remove & set(labels[mid])])
    if archive_ok and (action["stage"] == "archive" or action.get("undo_verified_at")):
        action["status"] = "verified"
        action["verified_at"] = _now().isoformat()
        action["final_labels"] = labels
        action["receipts"].append({"stage": "archive", "provider_ref": p["provider_ref"],
                                    "observed_at": snap["observed_at"], "labels": labels})
        case["cleanup"] = {"status": "verified", "kind": "archive", "action_id": action["id"], "basis": action["basis"]}
        return _view(action)
    inbox_targets = action.get("inbox_message_ids", [mid for mid in targets
                                if "INBOX" in action["original_labels"][mid]])
    if (not undo_ok or action["stage"] == "undo") and any("INBOX" not in labels[mid] for mid in inbox_targets):
        action["status"] = "failed"
        return _pending(case, "archive_missing_undo_proof", action["id"])
    if undo_ok:
        action["undo_verified_at"] = snap["observed_at"]
        action["receipts"].append({"stage": "undo", "provider_ref": p["provider_ref"],
                                    "observed_at": snap["observed_at"], "labels": labels})
        action["stage"] = "archive"
        mutations = [{"message_id": mid, "add_label_ids": [],
                      "remove_label_ids": sorted(remove & set(labels[mid]))} for mid in targets]
    else:
        action["stage"] = "undo"
        mutations = [{"message_id": mid, "add_label_ids": sorted(add - set(labels[mid])),
                      "remove_label_ids": []} for mid in targets]
    action["status"] = "pending"
    action["attempt"] += 1
    action["reserved_at"] = _now().isoformat()
    action["run_id"] = p["run_id"]
    _pending(case, "provider_action_reserved", action["id"])
    return _view(action, [m for m in mutations if m["add_label_ids"] or m["remove_label_ids"]])


def _prepare_attention(state, p, case, config, snap, labels, content_hash, restore=True):
    kind = "restore_attention" if restore else "workflow_attention"
    known_content = any(a.get("case_id") == case["id"] and a.get("status") in {"verified", "pending", "failed"} and
                        _basis_matches(a.get("basis"), case, config) and a.get("content_hash") == content_hash
                        for a in state.get("mailbox_actions", {}).values())
    if (snap["thread_id"] != case["thread_id"] or
            (not _same_conversation(case["snapshot"], snap) and not known_content) or
            any(mid in state.get("queue", {}) for mid in snap["message_ids"])):
        return _pending(case, "thread_changed_reassess")
    basis = _basis(case, config)
    actions = state.setdefault("mailbox_actions", {})
    key = _hash([state["mailbox"], case["id"], basis, state["version"], kind])
    if key in actions:
        action = actions[key]
        return {**_view(action), "reconcile_required": action["status"] != "verified"}
    waiting = config["workflow_labels"].get("waiting", {}).get("id")
    attention = config["workflow_labels"].get("attention", {}).get("id")
    add = sorted(({"INBOX"} if restore else set()) | ({attention} if attention else set()))
    remove = [waiting] if waiting else []
    selected = sorted(mid for mid, ids in labels.items() if set(add) - set(ids) or set(remove) & set(ids))
    if not selected:
        case["cleanup"] = {"status": "verified", "kind": kind, "basis": basis}
        return copy.deepcopy(case["cleanup"])
    action = {"id": key, "kind": kind, "case_id": case["id"], "thread_id": case["thread_id"],
              "mailbox": state["mailbox"], "run_id": p["run_id"], "authority_version": state["version"],
              "basis": basis, "content_hash": content_hash, "message_ids": selected, "original_labels": labels,
              "thread_observed_at": snap["thread_observed_at"],
              "drafts_observed_at": snap["drafts_observed_at"],
              "add_label_ids": add, "remove_label_ids": remove, "status": "pending", "stage": kind,
              "created_at": _now().isoformat(), "reserved_at": _now().isoformat(), "attempt": 1, "receipts": []}
    actions[key] = action
    _pending(case, "waiting_deadline_due" if restore else "attention_labels_reserved", key)
    return _view(action, [{"message_id": mid, "add_label_ids": sorted(set(add) - set(labels[mid])),
                           "remove_label_ids": sorted(set(remove) & set(labels[mid]))} for mid in selected])


def work(state):
    """Read-only projection: business-closed cases still expose failed cleanup."""
    config = policy((state.get("config") or {}).get("cleanup"))
    cases, retained = [], []
    from inbox_store import hot_state
    for case in hot_state(state).get("cases", {}).values():
        if not inbox_priority.case_in_scope(state, case):
            continue
        cleanup = case.get("cleanup", {})
        basis = _basis(case, config)
        desired, policy_reason = _desired_kind(state, case, config)
        referenced = state.get("mailbox_actions", {}).get(cleanup.get("action_id"))
        pending_reference = (cleanup.get("action_id") and cleanup.get("status") == "pending" and
                             (not isinstance(referenced, dict) or referenced.get("status") != "verified"))
        reserved = (cleanup.get("status") in {"failed", "unknown"} or
                    pending_reference) or any(
            action.get("case_id") == case["id"] and
            action.get("status") in {"pending", "failed", "unknown"}
            for action in state.get("mailbox_actions", {}).values())
        if desired == "retained_by_policy" and not reserved:
            retained.append({"case_id": case["id"], "thread_id": case["thread_id"],
                             "status": "retained", "kind": desired, "reason": policy_reason,
                             "basis": basis})
            continue
        # Old owner transitions wrote a pending marker even when there was no
        # authorized provider operation. Classify it without editing recovery data.
        if (not desired and not reserved and cleanup.get("status") == "pending" and
                cleanup.get("reason") in {"owner_state_changed", "case_held"} and
                not config["archive_on_resolution"] and config["waiting_policy"] == "keep_inbox" and
                not config["workflow_labels"]):
            retained.append({"case_id": case["id"], "thread_id": case["thread_id"],
                             "status": "retained", "kind": "retained_by_policy",
                             "reason": "no_authorized_cleanup", "basis": basis})
            continue
        satisfied = (cleanup.get("status") == "verified" and cleanup.get("kind") == desired and
                     _basis_matches(cleanup.get("basis"), case, config))
        if (desired and not satisfied) or cleanup.get("status") == "pending" or reserved:
            cases.append({"case_id": case["id"], "thread_id": case["thread_id"], **copy.deepcopy(cleanup)})
    imported = [{"action_id": a["id"], "message_id": a["message_id"], "thread_id": a.get("thread_id"),
                 "reason": "legacy_pending_requires_assessment"}
                for a in state.get("mailbox_actions", {}).values() if a.get("kind") == "legacy_pending"
                and a["status"] == "pending" and inbox_priority.case_in_scope(state, {"thread_id": a.get("thread_id"), "snapshot": {"message_ids": [a["message_id"]]}})]
    return {"cases": cases, "legacy_pending": imported, "retained": retained,
            "draft_cleanup": {"status": "unsupported" if config["draft_cleanup"] != "preserve" else "preserved",
                              "reason": "provider_draft_discard_unavailable"},
            "native_snooze": "unsupported"}


def undo_window(state, since=None):
    threshold = _iso(since) if since else None
    rows = []
    for action in state.get("mailbox_actions", {}).values():
        if (action.get("kind") == "archive" and action["status"] == "verified" and
                action.get("inbox_message_ids", action.get("message_ids"))):
            if threshold is None or _iso(action["verified_at"]) >= threshold:
                rows.append(copy.deepcopy(action))
        elif action.get("kind") == "legacy_history" and action["legacy"].get("action") == "archive":
            at = action["legacy"].get("recorded_at", action["legacy"].get("email_date"))
            if threshold is None or _iso(at) >= threshold:
                rows.append(copy.deepcopy(action))
    return {"archived": rows, "count": len(rows), "since": since}


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate legacy JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite legacy JSON")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def read_legacy(home):
    """Read originals unchanged. Caller holds engine transaction/legacy CLI fence."""
    root = Path(home)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("legacy home must be a real directory")
    if (root / "runs").is_symlink():
        raise ValueError("legacy run directory must not be a symlink")
    paths = [root / "state.json", root / "ledger.jsonl"] + sorted((root / "runs").glob("*.json"))
    hashes, documents = {}, {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or unsafe legacy source: " + path.name)
        raw = path.read_bytes()
        name = str(path.relative_to(root))
        hashes[name] = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
        documents[name] = [_json(line) for line in text.splitlines() if line.strip()] if name == "ledger.jsonl" else _json(text)
    # Detect source changes during the read without rewriting or repairing them.
    if paths[2:] != sorted((root / "runs").glob("*.json")):
        raise ValueError("legacy source inventory changed during import")
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != hashes[str(path.relative_to(root))] for path in paths):
        raise ValueError("legacy sources changed during import")
    return {"source_hashes": hashes, "documents": documents, "equivalence_hash": _hash(documents)}


def import_legacy(state, bundle, p):
    """Atomically import history, pending inventory and undo proof without authority."""
    _lease(state, p)
    documents = bundle["documents"]
    if _hash(documents) != bundle.get("equivalence_hash"):
        raise ValueError("legacy equivalence hash mismatch")
    prior = state.get(IMPORT_MARKER)
    if prior:
        if (prior["source_hashes"] != bundle["source_hashes"] or
                prior["equivalence_hash"] != bundle["equivalence_hash"]):
            raise ValueError("legacy sources changed after import; manual reconciliation required")
        restored = {"state.json": prior["legacy_state"], **prior["run_manifests"],
                    "ledger.jsonl": [a["legacy"] for a in sorted(
                        (a for a in state.get("mailbox_actions", {}).values() if a.get("kind") == "legacy_history"),
                        key=lambda a: a["legacy_index"])]}
        if _hash(restored) != prior["equivalence_hash"]:
            raise ValueError("imported legacy evidence no longer equivalent")
        return copy.deepcopy(prior["receipt"])
    legacy = documents.get("state.json")
    ledger = documents.get("ledger.jsonl")
    if not isinstance(legacy, dict) or not isinstance(ledger, list):
        raise ValueError("invalid legacy state or ledger")
    if type(legacy.get("schema")) is not int or not 1 <= legacy["schema"] <= 4:
        raise ValueError("unsupported legacy state schema")
    lease = legacy.get("run_lease")
    if lease is not None:
        if not isinstance(lease, dict):
            raise ValueError("invalid legacy lease")
        if _iso(lease.get("expires_at")) > _now():
            raise ValueError("active legacy lease prevents import")
    runs = {name: value for name, value in documents.items() if name.startswith("runs/")}
    active = legacy.get("active_run")
    if active and "runs/" + active + ".json" not in runs:
        raise ValueError("active legacy run manifest missing")
    staged, terminals = {}, set()
    for index, row in enumerate(ledger):
        if not isinstance(row, dict):
            raise ValueError("invalid legacy ledger row")
        for field in ("message_id", "run_id", "action"):
            _text(row.get(field), "legacy " + field)
        if row["action"] in {"archive", "keep", "kept", "escalate"}:
            terminals.add((row["run_id"], row["message_id"]))
        if row["action"] == "archive":
            _text(row.get("provider_id"), "legacy archive provider_id")
            _iso(row.get("recorded_at", row.get("email_date")))
            if row.get("undo_verified") is not True or not (row.get("labels") or row.get("label_ids")):
                raise ValueError("legacy archive undo proof missing")
            for field in ("labels", "label_ids"):
                values = row.get(field, [])
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValueError("invalid legacy archive " + field)
            # History binds the label in effect at the time of the action. The
            # current legacy config may have been changed after that archive.
        key = "legacy-history-" + str(index) + "-" + _hash(row)
        staged[key] = {"id": key, "kind": "legacy_history", "status": "imported", "legacy_index": index,
                       "legacy": copy.deepcopy(row)}
    pending = {}
    def retain(row, run_id=None):
        if not isinstance(row, dict):
            raise ValueError("invalid legacy pending row")
        mid = _text(row.get("message_id"), "legacy pending message_id")
        item = pending.setdefault(mid, {"message_id": mid, "thread_id": row.get("thread_id"), "evidence": []})
        item["evidence"].append({"run_id": run_id, "row": copy.deepcopy(row)})
    retry = legacy.get("retry_set", [])
    if not isinstance(retry, list):
        raise ValueError("invalid legacy retry_set")
    for row in retry:
        retain(row)
    for name, run in runs.items():
        if (not isinstance(run, dict) or run.get("schema") != 1 or
                name != "runs/" + str(run.get("run_id")) + ".json" or not isinstance(run.get("entries"), list)):
            raise ValueError("invalid legacy run manifest")
        seen = set()
        for row in run["entries"]:
            if not isinstance(row, dict) or not isinstance(row.get("message_id"), str):
                raise ValueError("invalid legacy manifest entry")
            if not row["message_id"] or row["message_id"] in seen:
                raise ValueError("duplicate or empty legacy manifest message_id")
            seen.add(row["message_id"])
            terminal = (run["run_id"], row["message_id"]) in terminals
            if row.get("status") == "terminal" and not terminal:
                raise ValueError("legacy terminal entry missing ledger proof")
            if not terminal:
                retain(row, run["run_id"])
        if not isinstance(run.get("deferred", []), list):
            raise ValueError("invalid legacy deferred inventory")
        for row in run.get("deferred", []):
            retain(row, run["run_id"])
    for mid, row in pending.items():
        key = "legacy-pending-" + _hash(mid)
        staged[key] = {"id": key, "kind": "legacy_pending", "status": "pending", **row}
    receipt = {"ledger_rows": len(ledger), "run_manifests": len(runs), "pending_messages": len(pending),
               "undo_rows": sum(row.get("action") == "archive" for row in ledger),
               "equivalence_hash": bundle["equivalence_hash"], "originals_preserved": True}
    # Keep one canonical copy of ledger rows in action history. State/manifests carry
    # interrupted-run evidence; source hashes bind the untouched original files.
    imported = {"source_hashes": copy.deepcopy(bundle["source_hashes"]),
                "equivalence_hash": bundle["equivalence_hash"], "legacy_state": copy.deepcopy(legacy),
                "run_manifests": copy.deepcopy(runs), "receipt": receipt, "imported_at": _now().isoformat()}
    actions = state.setdefault("mailbox_actions", {})
    if set(staged) & set(actions):
        raise ValueError("legacy action identity collision")
    actions.update(staged)
    state[IMPORT_MARKER] = imported
    return copy.deepcopy(receipt)


def reconcile_legacy_pending(state, case):
    """Call after normal case assessment; discovery alone never settles legacy work."""
    assessed = set(case.get("snapshot", {}).get("message_ids", []))
    for action in state.get("mailbox_actions", {}).values():
        if (action.get("kind") == "legacy_pending" and action["status"] == "pending" and
                action["message_id"] in assessed and action.get("thread_id") in {None, case["thread_id"]}):
            action.update(status="assessed", case_id=case["id"], assessed_at=_now().isoformat())


COMMANDS = {"prepare-cleanup": prepare, "cleanup-result": result}
READ_COMMANDS = {"cleanup-work": work}
