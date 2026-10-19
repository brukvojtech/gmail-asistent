#!/usr/bin/env python3
"""Build an Inbox Manager snapshot from one complete decoded provider thread."""

from __future__ import annotations

import datetime as dt
import copy
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class SnapshotError(ValueError):
    pass


LABEL_FIELDS = {"labels", "label_ids", "labelIds"}
HISTORY_FIELDS = {"historyId", "history_id"}
ATTACHMENT_LOCATOR_FIELDS = {"attachmentId", "attachment_id"}
THREADING_HEADERS = {"in-reply-to": "In-Reply-To", "references": "References"}


def canonical_headers(value: Any) -> dict[str, Any]:
    """Canonical names only; exact values and unrelated provider headers survive."""
    headers = _headers(value, "headers")
    result = {}
    for key, item in headers.items():
        name = THREADING_HEADERS.get(key.casefold(), key.casefold())
        if name in result and result[name] != item:
            raise SnapshotError("contradictory case-varied header: " + key)
        result[name] = copy.deepcopy(item)
    return result


def threading_headers(value: Any) -> dict[str, Any]:
    """Compare reply identity without equating provider-added presentation headers."""
    return {key: item for key, item in canonical_headers(value).items()
            if key in THREADING_HEADERS.values()}


def label_ids(item: dict[str, Any]) -> list[str] | None:
    """Accept supported arrays of label IDs; contradictory aliases fail closed."""
    aliases = []
    for field in sorted(LABEL_FIELDS.intersection(item)):
        value = item[field]
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            raise SnapshotError(field + " must be an array of nonempty label IDs")
        aliases.append(sorted(set(value)))
    if aliases and any(value != aliases[0] for value in aliases[1:]):
        raise SnapshotError("contradictory provider label aliases")
    return aliases[0] if aliases else None


def normalize_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise SnapshotError("provider message or draft must be an object")
    result = copy.deepcopy(item)
    labels = label_ids(result)
    if labels is not None:
        for field in LABEL_FIELDS:
            result.pop(field, None)
        result["label_ids"] = labels
    if "headers" in result:
        result["headers"] = canonical_headers(result["headers"])
    # Complete replacement MIME evidence must agree with decoded draft fields.
    if isinstance(result.get("payload"), dict) and "headers" in result["payload"]:
        result["payload"]["headers"] = canonical_headers(result["payload"]["headers"])
    return result


def normalize_packet(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise SnapshotError("input must be an object")
    result = copy.deepcopy(packet)
    for field in ("messages", "drafts"):
        if not isinstance(result.get(field), list):
            raise SnapshotError(field + " must be an array")
        result[field] = [normalize_item(item) for item in result[field]]
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(f"{field} must be a nonempty string")
    return value


def _addresses(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SnapshotError(f"{field} must be an array of nonempty strings")
    return value


def _headers(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError(f"{field} must be an object")
    for key in value:
        if not isinstance(key, str) or not key:
            raise SnapshotError(f"{field} keys must be nonempty strings")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SnapshotError(f"{field} must include a timezone")
    return text


def _validate_item(item: Any, kind: str, thread_id: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise SnapshotError(f"each {kind} must be an object")
    _text(item.get("id"), f"{kind}.id")
    if _text(item.get("thread_id"), f"{kind}.thread_id") != thread_id:
        raise SnapshotError(f"{kind} belongs to a different thread")
    _text(item.get("from"), f"{kind}.from")
    _addresses(item.get("to"), f"{kind}.to")
    _addresses(item.get("cc"), f"{kind}.cc")
    if not isinstance(item.get("subject"), str):
        raise SnapshotError(f"{kind}.subject must be a string")
    if not isinstance(item.get("body"), str):
        raise SnapshotError(f"{kind}.body must be a string")
    _headers(item.get("headers"), f"{kind}.headers")
    if kind == "draft":
        _addresses(item.get("bcc"), "draft.bcc")
    elif "bcc" in item:
        _addresses(item["bcc"], "message.bcc")
    return item


def _canonical_value(value: Any, field: str | None = None, *, content_only: bool = False,
                     volatile_attachment_locators: bool = False) -> Any:
    """Return stable provider evidence without discarding conversation content."""
    if isinstance(value, dict):
        digest_bound = isinstance(value.get("sha256"), str) and bool(value["sha256"].strip())
        return {
            key: _canonical_value(item, key, content_only=content_only,
                                  volatile_attachment_locators=volatile_attachment_locators)
            for key, item in value.items()
            if not (key in ATTACHMENT_LOCATOR_FIELDS and
                    (volatile_attachment_locators or digest_bound))
            and not (content_only and key in LABEL_FIELDS | HISTORY_FIELDS)
        }
    if isinstance(value, list):
        items = [_canonical_value(item, content_only=content_only,
                                  volatile_attachment_locators=volatile_attachment_locators)
                 for item in value]
        if field in LABEL_FIELDS or field == "attachments":
            return sorted(items, key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ))
        return items
    return value


def _fingerprint(thread_id: str, messages: list[dict[str, Any]], drafts: list[dict[str, Any]],
                 *, content_only: bool = False) -> str:
    canonical = {
        "thread_id": thread_id,
        "messages": sorted((_canonical_value(item, content_only=content_only,
                                               volatile_attachment_locators=True)
                            for item in messages),
                           key=lambda item: item["id"]),
        "drafts": sorted((_canonical_value(item, content_only=content_only) for item in drafts),
                         key=lambda item: item["id"]),
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _conversation_messages(packet, messages, drafts):
    """Separate inventoried Gmail draft messages from conversation identity.

    Full fingerprints and message_ids still retain every provider message. The
    stateful callers verify the inventory proof against persisted retrieval state;
    without the complete proof and an unambiguous provider message ID, keep the
    message in the conversation fingerprint (fail closed).
    """
    marker = packet.get("draft_inventory")
    completeness = packet.get("thread_completeness")
    if (not isinstance(marker, dict) or marker.get("complete") is not True or
            not marker.get("evidence_ref") or not marker.get("inventory_id") or
            marker.get("inventory_id") != packet.get("inventory_id") or
            marker.get("generation") is None or
            marker.get("generation") != packet.get("inventory_generation") or
            not isinstance(completeness, dict) or completeness.get("complete") is not True or
            not completeness.get("evidence_ref")):
        return messages
    mapped = {}
    for draft in drafts:
        message_id = draft.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return messages
        if message_id in mapped:
            return messages
        mapped[message_id] = draft
    result = []
    for message in messages:
        draft = mapped.get(message["id"])
        labels = message.get("label_ids", [])
        draft_labels = draft.get("label_ids", []) if draft else []
        if (draft and message["id"] != packet["trigger_id"] and
                "DRAFT" in labels and "SENT" not in labels and
                "DRAFT" in draft_labels and "SENT" not in draft_labels and
                _canonical_value({k: v for k, v in message.items() if k != "id"},
                                 content_only=True, volatile_attachment_locators=True) ==
                _canonical_value({k: draft[k] for k in message if k != "id" and k in draft},
                                 content_only=True, volatile_attachment_locators=True)):
            continue
        result.append(message)
    return result


def build_snapshot(packet: Any) -> dict[str, Any]:
    """Validate and fingerprint a caller-supplied complete provider response.

    Shape validation cannot establish that the provider actually returned every message;
    callers must pass the exact result of a complete thread read.
    """
    packet = normalize_packet(packet)
    if packet.get("complete") is not True:
        raise SnapshotError("complete provider thread required")
    thread_id = _text(packet.get("thread_id"), "thread_id")
    trigger_id = _text(packet.get("trigger_id"), "trigger_id")
    mailbox = _text(packet.get("mailbox"), "mailbox")
    observed_at = _timestamp(packet.get("observed_at"), "observed_at")
    # A combined observation cannot be fresher than either underlying read.
    thread_observed_at = _timestamp(packet.get("thread_observed_at", observed_at), "thread_observed_at")
    drafts_observed_at = _timestamp(packet.get("drafts_observed_at", observed_at), "drafts_observed_at")
    reads = [observed_at, thread_observed_at, drafts_observed_at]
    parse_time = lambda value: dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    observed_at = min(reads, key=parse_time)
    if type(packet.get("automated")) is not bool:
        raise SnapshotError("automated must be a boolean")
    messages = packet.get("messages")
    drafts = packet.get("drafts")
    if not isinstance(messages, list) or not messages:
        raise SnapshotError("messages must be a nonempty array")
    if not isinstance(drafts, list):
        raise SnapshotError("drafts must be an array")
    checked_messages = [_validate_item(item, "message", thread_id) for item in messages]
    checked_drafts = [_validate_item(item, "draft", thread_id) for item in drafts]
    ids = [item["id"] for item in checked_messages + checked_drafts]
    if len(ids) != len(set(ids)):
        raise SnapshotError("message and draft IDs must be unique")
    message_ids = sorted(item["id"] for item in checked_messages)
    draft_ids = sorted(item["id"] for item in checked_drafts)
    if trigger_id not in message_ids:
        raise SnapshotError("trigger_id must identify a message in the thread")

    mailbox_key = mailbox.casefold()
    correspondents: set[str] = set()
    for item in checked_messages:
        addresses = [item["from"], *item["to"], *item["cc"]]
        correspondents.update(address for address in addresses if address.casefold() != mailbox_key)

    try:
        fingerprint = _fingerprint(thread_id, checked_messages, checked_drafts)
        content_fingerprint = _fingerprint(thread_id, checked_messages, checked_drafts, content_only=True)
        message_fingerprint = _fingerprint(
            thread_id, _conversation_messages(packet, checked_messages, checked_drafts),
            [], content_only=True)
    except (TypeError, ValueError) as exc:
        raise SnapshotError("messages and drafts must contain valid JSON values") from exc
    trigger = next(item for item in checked_messages if item["id"] == trigger_id)
    categories = {"CATEGORY_PERSONAL": "primary", "CATEGORY_UPDATES": "updates",
                  "CATEGORY_PROMOTIONS": "promotions", "CATEGORY_SOCIAL": "social", "CATEGORY_FORUMS": "forums"}
    observed_categories = sorted({categories[label] for label in trigger.get("label_ids", []) if label in categories})
    return {
        "complete": True,
        "thread_id": thread_id,
        "trigger_id": trigger_id,
        "message_ids": message_ids,
        "fingerprint": fingerprint,
        "content_fingerprint": content_fingerprint,
        "message_fingerprint": message_fingerprint,
        "observed_at": observed_at,
        "thread_observed_at": thread_observed_at,
        "drafts_observed_at": drafts_observed_at,
        "draft_ids": draft_ids,
        "correspondents": sorted(correspondents, key=lambda value: (value.casefold(), value)),
        "automated": packet["automated"],
        **({"categories": observed_categories} if observed_categories else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Complete provider JSON file; omit for redirected stdin")
    args = parser.parse_args()
    try:
        if args.input:
            packet = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            if sys.stdin.isatty():
                raise SnapshotError("JSON input requires --input FILE or file redirection; interactive terminals are not supported")
            packet = json.load(sys.stdin)
        result = build_snapshot(packet)
    except (json.JSONDecodeError, SnapshotError, OSError) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True) + "\n")
        return 2
    sys.stdout.write(json.dumps({"ok": True, "result": result}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
