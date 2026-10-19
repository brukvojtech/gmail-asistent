#!/usr/bin/env python3
"""Persist draft discovery and build timestamp-honest Inbox Manager packets.

The host performs provider reads. This stdlib helper only validates and stores
draft IDs/thread IDs in the existing engine database, then combines one complete
decoded thread with the relevant draft bodies. It has no network dependency.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

import inbox_engine as engine
import inbox_snapshot


class RetrievalError(ValueError):
    pass


def _text(value: Any, field: str, maximum: int = 10000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RetrievalError(f"{field} must be a nonempty string")
    return value


def _cursor(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field, 2000)


def _time(value: Any, field: str) -> tuple[str, dt.datetime]:
    text = _text(value, field, 200)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetrievalError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RetrievalError(f"{field} must include a timezone")
    if parsed > engine.utcnow() + dt.timedelta(seconds=5):
        raise RetrievalError(f"{field} cannot be in the future")
    return text, parsed


def _identity(state: dict[str, Any], payload: Any) -> tuple[dict[str, Any], str, str]:
    if not isinstance(payload, dict):
        raise RetrievalError("input must be an object")
    mailbox = _text(payload.get("mailbox"), "mailbox")
    if mailbox != state.get("mailbox"):
        raise RetrievalError("retrieval mailbox mismatch")
    inventory_id = _text(payload.get("inventory_id"), "inventory_id", 500)
    return payload, mailbox, inventory_id


def _retrieval(state: dict[str, Any], mutate: bool = False) -> dict[str, Any]:
    value = state.get("retrieval")
    if value is None:
        value = {"generation": 0, "inventory": None, "invalidations": []}
        if mutate:
            state["retrieval"] = value
    if not isinstance(value, dict):
        raise RetrievalError("invalid retrieval state")
    return value


def _plan(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    _, mailbox, inventory_id = _identity(state, payload)
    retrieval = _retrieval(state)
    inventory = retrieval.get("inventory")
    if inventory is None:
        if any(row.get("inventory_id") == inventory_id for row in retrieval.get("invalidations", [])):
            raise RetrievalError("invalidated inventory_id cannot be reused; choose a new inventory_id")
        return {
            "mailbox": mailbox,
            "inventory_id": inventory_id,
            "generation": retrieval.get("generation", 0),
            "requested_cursor": None,
            "complete": False,
            "observed_at": None,
            "count": 0,
            "pages": 0,
        }
    if inventory.get("mailbox") != mailbox:
        raise RetrievalError("saved inventory mailbox mismatch")
    if inventory.get("inventory_id") != inventory_id:
        raise RetrievalError("another inventory is current; invalidate it before starting a new inventory")
    if inventory.get("generation") != retrieval.get("generation"):
        raise RetrievalError("saved inventory is not in the current retrieval generation")
    return {
        "mailbox": mailbox,
        "inventory_id": inventory_id,
        "generation": inventory["generation"],
        "requested_cursor": inventory["requested_cursor"],
        "complete": inventory["complete"],
        "observed_at": inventory["earliest_observed_at"],
        "count": len(inventory["drafts"]),
        "pages": len(inventory["page_cursors"]),
    }


def inventory_plan(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Return the one exact cursor the host may read next, without writing state."""
    if isinstance(payload, dict) and not payload.get("inventory_id"):
        if payload.get("mailbox") != state.get("mailbox"):
            raise RetrievalError("retrieval mailbox mismatch")
        current = _retrieval(state).get("inventory")
        if current:
            payload = {**payload, "inventory_id": current["inventory_id"]}
        else:
            return {"mailbox": state["mailbox"], "inventory_id": None,
                    "generation": _retrieval(state).get("generation", 0), "requested_cursor": None,
                    "complete": False, "observed_at": None, "count": 0, "pages": 0}
    return _plan(state, payload)


def _metadata(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise RetrievalError("drafts must be an array of metadata objects")
    result = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise RetrievalError("each draft metadata row must be an object")
        if "body" in row:
            raise RetrievalError("inventory accepts draft metadata only, never bodies")
        draft_id = _text(row.get("id"), "draft.id", 1000)
        thread_id = _text(row.get("thread_id"), "draft.thread_id", 1000)
        if draft_id in seen:
            raise RetrievalError("duplicate draft id in inventory page")
        seen.add(draft_id)
        result.append({"id": draft_id, "thread_id": thread_id})
    return result


def inventory_page(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Commit one provider page after validating its exact expected cursor."""
    payload, mailbox, inventory_id = _identity(state, payload)
    engine.lease(state, payload)
    if "page_cursor" not in payload:
        raise RetrievalError("page_cursor is required; use null for the first page")
    page_cursor = _cursor(payload["page_cursor"], "page_cursor")
    if "next_page_token" not in payload:
        raise RetrievalError("next_page_token is required; use explicit null only after full pagination")
    next_cursor = _cursor(payload["next_page_token"], "next_page_token")
    observed_text, observed = _time(payload.get("observed_at"), "observed_at")
    evidence_ref = _text(payload.get("evidence_ref"), "evidence_ref")
    rows = _metadata(payload.get("drafts"))

    retrieval = _retrieval(state, mutate=True)
    inventory = retrieval.get("inventory")
    if inventory is None:
        if page_cursor is not None:
            raise RetrievalError("a new inventory must start with page_cursor null")
        if any(row.get("inventory_id") == inventory_id for row in retrieval.get("invalidations", [])):
            raise RetrievalError("invalidated inventory_id cannot be reused; choose a new inventory_id")
        inventory = {
            "inventory_id": inventory_id,
            "mailbox": mailbox,
            "generation": retrieval.get("generation", 0),
            "complete": False,
            "requested_cursor": None,
            "page_cursors": [],
            "page_observed_at": [],
            "page_evidence_refs": [],
            "earliest_observed_at": None,
            "latest_observed_at": None,
            "drafts": {},
        }
        retrieval["inventory"] = inventory
    elif inventory.get("inventory_id") != inventory_id:
        raise RetrievalError("another inventory is current; invalidate it before starting a new inventory")
    if inventory.get("mailbox") != mailbox:
        raise RetrievalError("saved inventory mailbox mismatch")
    if inventory.get("generation") != retrieval.get("generation"):
        raise RetrievalError("saved inventory is not in the current retrieval generation")
    if inventory.get("complete"):
        raise RetrievalError("inventory is already complete; use inventory-plan")
    if page_cursor != inventory.get("requested_cursor"):
        raise RetrievalError("page_cursor does not match the saved requested_cursor")
    if page_cursor in inventory["page_cursors"]:
        raise RetrievalError("page_cursor was already consumed")
    if next_cursor is not None and (next_cursor == page_cursor or next_cursor in inventory["page_cursors"]):
        raise RetrievalError("provider cursor loop rejected")
    if inventory["latest_observed_at"] is not None:
        _, latest = _time(inventory["latest_observed_at"], "saved latest_observed_at")
        if observed < latest:
            raise RetrievalError("page observed_at cannot precede an earlier page read")
    for row in rows:
        prior = inventory["drafts"].get(row["id"])
        if prior is not None:
            if prior["thread_id"] != row["thread_id"]:
                raise RetrievalError("draft id is already bound to a different thread")
            raise RetrievalError("duplicate draft id across inventory pages")

    inventory["page_cursors"].append(page_cursor)
    inventory["page_observed_at"].append(observed_text)
    inventory["page_evidence_refs"].append(evidence_ref)
    inventory["earliest_observed_at"] = inventory["earliest_observed_at"] or observed_text
    inventory["latest_observed_at"] = observed_text
    for row in rows:
        inventory["drafts"][row["id"]] = row
    inventory["requested_cursor"] = next_cursor
    inventory["complete"] = next_cursor is None
    if inventory["complete"]:
        engine.event(
            state,
            "retrieval_inventory_completed",
            inventory_id=inventory_id,
            generation=inventory["generation"],
            count=len(inventory["drafts"]),
        )
    return _plan(state, payload)


def invalidate_state(state: dict[str, Any], mailbox: str, inventory_id: str, reason: str) -> dict[str, Any]:
    """Invalidate current retrieval state after a provider write.

    Engine/cycle write paths may call this after enforcing their own lease. The
    CLI wrapper below enforces the lease before it delegates here.
    """
    mailbox = _text(mailbox, "mailbox")
    inventory_id = _text(inventory_id, "inventory_id", 500)
    reason = _text(reason, "reason")
    if mailbox != state.get("mailbox"):
        raise RetrievalError("retrieval mailbox mismatch")
    retrieval = _retrieval(state, mutate=True)
    inventory = retrieval.get("inventory")
    if inventory is None:
        prior = next((row for row in reversed(retrieval.get("invalidations", []))
                      if row.get("inventory_id") == inventory_id and row.get("mailbox") == mailbox), None)
        if prior is not None:
            return {
                "invalidated": True,
                "idempotent": True,
                "inventory_id": inventory_id,
                "generation": retrieval["generation"],
                "reason": prior["reason"],
                "invalidated_at": prior["invalidated_at"],
            }
        raise RetrievalError("no current inventory to invalidate")
    if inventory.get("inventory_id") != inventory_id or inventory.get("mailbox") != mailbox:
        raise RetrievalError("inventory identity mismatch")
    if inventory.get("generation") != retrieval.get("generation"):
        raise RetrievalError("saved inventory is not in the current retrieval generation")
    invalidated_at = engine.stamp()
    record = {
        "inventory_id": inventory_id,
        "mailbox": mailbox,
        "generation": inventory["generation"],
        "reason": reason,
        "invalidated_at": invalidated_at,
    }
    retrieval.setdefault("invalidations", []).append(record)
    retrieval["generation"] = inventory["generation"] + 1
    retrieval["inventory"] = None
    engine.event(state, "retrieval_inventory_invalidated", **record)
    return {
        "invalidated": True,
        "idempotent": False,
        "inventory_id": inventory_id,
        "generation": retrieval["generation"],
        "reason": reason,
        "invalidated_at": invalidated_at,
    }


def invalidate(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Lease-guarded CLI adapter for invalidating the current inventory."""
    payload, mailbox, inventory_id = _identity(state, payload)
    engine.lease(state, payload)
    return invalidate_state(state, mailbox, inventory_id, payload.get("reason"))


def _complete_thread(payload: dict[str, Any], mailbox: str) -> tuple[dict[str, Any], str, dt.datetime]:
    thread = payload.get("thread")
    if not isinstance(thread, dict):
        raise RetrievalError("thread must be one decoded provider thread object")
    completeness = payload.get("completeness")
    if not isinstance(completeness, dict) or type(completeness.get("complete")) is not bool:
        raise RetrievalError("completeness must include an explicit complete boolean")
    if completeness["complete"] is not True:
        raise RetrievalError("partial thread result cannot build a packet")
    _text(completeness.get("evidence_ref"), "completeness.evidence_ref")
    if thread.get("complete") is False:
        raise RetrievalError("thread contradicts completeness evidence")
    if thread.get("truncated") is True or thread.get("has_more") is True:
        raise RetrievalError("thread result declares partial provider data")
    for field in ("next_page_token", "next_cursor"):
        if thread.get(field) is not None:
            raise RetrievalError("thread result has an incomplete pagination cursor")
    if "drafts" in thread:
        raise RetrievalError("pass relevant draft bodies in the top-level drafts array")
    if thread.get("mailbox", mailbox) != mailbox:
        raise RetrievalError("thread mailbox contradicts requested mailbox")
    observed_text, observed = _time(payload.get("thread_observed_at"), "thread_observed_at")
    for field in ("observed_at", "thread_observed_at"):
        if field in thread:
            _, duplicate = _time(thread[field], f"thread.{field}")
            if duplicate != observed:
                raise RetrievalError(f"thread.{field} contradicts thread_observed_at")
    return thread, observed_text, observed


def _inventory_marker(inventory: dict[str, Any]) -> dict[str, Any]:
    evidence_encoded = json.dumps(
        inventory["page_evidence_refs"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "inventory_id": inventory["inventory_id"],
        "generation": inventory["generation"],
        "complete": True,
        "observed_at": inventory["earliest_observed_at"],
        "pages": len(inventory["page_cursors"]),
        "count": len(inventory["drafts"]),
        "evidence_ref": "sha256:" + hashlib.sha256(evidence_encoded).hexdigest(),
    }


def verify_packet_inventory(state: dict[str, Any], value: Any) -> dict[str, Any]:
    """Bind a packet's absence proof to the current persisted draft inventory."""
    if not isinstance(value, dict):
        raise RetrievalError("packet must be an object")
    mailbox = _text(value.get("mailbox"), "packet.mailbox")
    if mailbox != state.get("mailbox"):
        raise RetrievalError("packet mailbox mismatch")
    retrieval = _retrieval(state)
    inventory = retrieval.get("inventory")
    if inventory is None or not inventory.get("complete"):
        raise RetrievalError("packet requires a completed current draft inventory")
    if inventory.get("mailbox") != mailbox or inventory.get("generation") != retrieval.get("generation"):
        raise RetrievalError("packet inventory is not current for this mailbox")
    marker = value.get("draft_inventory")
    if not isinstance(marker, dict) or marker.get("complete") is not True:
        raise RetrievalError("packet draft_inventory completion proof required")
    expected_marker = _inventory_marker(inventory)
    if marker != expected_marker:
        raise RetrievalError("packet draft_inventory proof does not match persisted inventory")
    if value.get("inventory_id") != marker["inventory_id"]:
        raise RetrievalError("packet inventory_id contradicts draft_inventory proof")
    if value.get("inventory_generation") != marker["generation"]:
        raise RetrievalError("packet inventory_generation contradicts draft_inventory proof")

    completeness = value.get("thread_completeness")
    if not isinstance(completeness, dict) or completeness.get("complete") is not True:
        raise RetrievalError("packet thread completeness proof required")
    thread_evidence = _text(completeness.get("evidence_ref"), "thread_completeness.evidence_ref")
    thread_id = _text(value.get("thread_id"), "packet.thread_id", 1000)
    drafts = value.get("drafts")
    if not isinstance(drafts, list):
        raise RetrievalError("packet drafts must be an array")
    actual_ids = []
    for row in drafts:
        if not isinstance(row, dict):
            raise RetrievalError("each packet draft must be an object")
        draft_id = _text(row.get("id"), "packet draft.id", 1000)
        if row.get("thread_id") != thread_id:
            raise RetrievalError("packet draft belongs to a different thread")
        if row.get("mailbox", mailbox) != mailbox:
            raise RetrievalError("packet draft mailbox mismatch")
        actual_ids.append(draft_id)
    if len(actual_ids) != len(set(actual_ids)):
        raise RetrievalError("packet has duplicate draft IDs")
    expected_ids = sorted(
        draft_id for draft_id, row in inventory["drafts"].items()
        if row["thread_id"] == thread_id
    )
    if sorted(actual_ids) != expected_ids:
        raise RetrievalError("packet draft IDs do not match current inventory for thread")

    thread_text, thread_at = _time(value.get("thread_observed_at"), "packet.thread_observed_at")
    drafts_text, drafts_at = _time(value.get("drafts_observed_at"), "packet.drafts_observed_at")
    saved_text, saved_at = _time(inventory.get("earliest_observed_at"), "inventory.earliest_observed_at")
    if drafts_at != saved_at or marker["observed_at"] != saved_text:
        raise RetrievalError("packet draft timestamp does not match persisted inventory")
    observed_text, observed_at = _time(value.get("observed_at"), "packet.observed_at")
    if observed_at != min(thread_at, drafts_at):
        raise RetrievalError("packet observed_at is not the conservative retrieval bound")
    return {
        "mailbox": mailbox,
        "thread_id": thread_id,
        "inventory_id": marker["inventory_id"],
        "generation": marker["generation"],
        "inventory_evidence_ref": marker["evidence_ref"],
        "thread_evidence_ref": thread_evidence,
        "thread_observed_at": thread_text,
        "drafts_observed_at": drafts_text,
        "observed_at": observed_text,
        "draft_ids": expected_ids,
    }


def packet(state: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Build one canonical packet from a complete inventory and thread read."""
    payload, mailbox, inventory_id = _identity(state, payload)
    retrieval = _retrieval(state)
    inventory = retrieval.get("inventory")
    if inventory is None or not inventory.get("complete"):
        raise RetrievalError("a completed current draft inventory is required")
    if inventory.get("mailbox") != mailbox or inventory.get("inventory_id") != inventory_id:
        raise RetrievalError("completed inventory identity mismatch")
    if inventory.get("generation") != retrieval.get("generation"):
        raise RetrievalError("completed inventory is not in the current retrieval generation")

    thread, thread_observed_text, thread_observed = _complete_thread(payload, mailbox)
    thread_id = _text(thread.get("thread_id"), "thread.thread_id", 1000)
    draft_bodies = payload.get("drafts")
    if not isinstance(draft_bodies, list):
        raise RetrievalError("drafts must be an array of relevant decoded draft bodies")
    draft_ids = []
    for row in draft_bodies:
        if not isinstance(row, dict):
            raise RetrievalError("each relevant draft must be an object")
        draft_id = _text(row.get("id"), "draft.id", 1000)
        if row.get("thread_id") != thread_id:
            raise RetrievalError("draft body belongs to a different thread")
        if row.get("mailbox", mailbox) != mailbox:
            raise RetrievalError("draft mailbox contradicts requested mailbox")
        draft_ids.append(draft_id)
    if len(draft_ids) != len(set(draft_ids)):
        raise RetrievalError("duplicate relevant draft body id")
    expected = sorted(
        draft_id
        for draft_id, row in inventory["drafts"].items()
        if row["thread_id"] == thread_id
    )
    if sorted(draft_ids) != expected:
        raise RetrievalError("relevant draft bodies must exactly match the completed inventory")

    drafts_observed_text, drafts_observed = _time(
        inventory.get("earliest_observed_at"), "inventory.earliest_observed_at"
    )
    if "drafts_observed_at" in payload:
        _, claimed = _time(payload["drafts_observed_at"], "drafts_observed_at")
        if claimed != drafts_observed:
            raise RetrievalError("drafts_observed_at contradicts the saved inventory")
    observed_at = thread_observed_text if thread_observed <= drafts_observed else drafts_observed_text
    inventory_proof = _inventory_marker(inventory)
    canonical = {
        "complete": True,
        "mailbox": mailbox,
        "thread_id": thread_id,
        "trigger_id": thread.get("trigger_id"),
        "observed_at": observed_at,
        "thread_observed_at": thread_observed_text,
        "drafts_observed_at": drafts_observed_text,
        "inventory_id": inventory_id,
        "inventory_generation": inventory["generation"],
        "draft_inventory": inventory_proof,
        "thread_completeness": copy.deepcopy(payload["completeness"]),
        "automated": thread.get("automated"),
        "messages": copy.deepcopy(thread.get("messages")),
        "drafts": copy.deepcopy(draft_bodies),
    }
    built = inbox_snapshot.build_snapshot(canonical)
    verify_packet_inventory(state, canonical)
    return {"packet": canonical, "snapshot": built}


def execute(home: str | Path, command: str, payload: Any) -> dict[str, Any]:
    readonly = command in {"inventory-plan", "packet"}
    if command not in {"inventory-plan", "inventory-page", "invalidate", "packet"}:
        raise RetrievalError("unknown command")
    with engine.transaction(home, readonly=readonly) as (_, box):
        state = box[0]
        if state is None:
            raise RetrievalError("inbox engine not initialized; run setup")
        if command == "inventory-plan":
            return inventory_plan(state, payload)
        if command == "inventory-page":
            return inventory_page(state, payload)
        if command == "invalidate":
            return invalidate(state, payload)
        return packet(state, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory-plan", "inventory-page", "invalidate", "packet"))
    parser.add_argument("--home", required=True)
    parser.add_argument("--input", help="Private JSON file; omit for redirected stdin")
    args = parser.parse_args()
    try:
        if args.input:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            if sys.stdin.isatty():
                raise RetrievalError(
                    "JSON input requires --input FILE or file redirection; interactive terminals are not supported"
                )
            payload = json.load(sys.stdin)
        result = execute(args.home, args.command, payload)
    except (json.JSONDecodeError, RetrievalError, inbox_snapshot.SnapshotError,
            ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True) + "\n")
        return 2
    sys.stdout.write(json.dumps({"ok": True, "result": result}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
