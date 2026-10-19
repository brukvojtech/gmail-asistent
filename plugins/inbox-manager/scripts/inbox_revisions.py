#!/usr/bin/env python3
"""Durable, merge-safe updates for one Inbox Manager-owned Gmail draft.

The host owns provider calls.  This module stores an immutable revision request,
reserves one exact update, returns a complete replacement payload, and confirms a
fresh provider readback.  It never deletes or recreates a missing draft.
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping
import copy
import difflib
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import sys
from typing import Any

import inbox_engine as engine
import inbox_snapshot
from inbox_retrieval import verify_packet_inventory


COMMANDS = {
    "revision-candidate", "revision-prepare", "revision-confirm", "revision-reconcile",
    "revision-retire",
}
CONTENT_FIELDS = ("body", "subject", "recipients", "cc", "bcc")
ADDRESS_FIELDS = {"recipients", "cc", "bcc"}
REVIEW_ASSESSMENTS = {
    "grounded", "answers_ask", "identity", "recipients", "privacy", "authority", "no_loop"
}


class RevisionError(ValueError):
    pass


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RevisionError(f"{field} must be a JSON object")
    return value


def _text(value: Any, field: str, maximum: int = 10000) -> str:
    try:
        return engine.text(value, field, maximum)
    except ValueError as exc:
        raise RevisionError(str(exc)) from exc


def _auth(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    value = {
        "run_id": _text(payload.get("run_id"), "run_id"),
        "token": _text(payload.get("token"), "token"),
    }
    engine.lease(state, value)
    if not state.get("active"):
        raise RevisionError("approved setup required")
    return value


def _collections(state: dict[str, Any]) -> tuple[MutableMapping[str, Any], MutableMapping[str, Any]]:
    revisions = state.setdefault("draft_revisions", {})
    lineages = state.setdefault("draft_lineages", {})
    if not isinstance(revisions, MutableMapping) or not isinstance(lineages, MutableMapping):
        raise RevisionError("invalid draft revision storage")
    return revisions, lineages


def _stored_owner_answer(state: dict[str, Any], owner_ref: Any, case_id: str) -> dict[str, Any]:
    ref = _object(owner_ref, "owner_ref")
    kind = ref.get("kind")
    if kind == "owner_event":
        if set(ref) != {"kind", "event_id"}:
            raise RevisionError("owner_event ref requires only kind and event_id")
        event_id = _text(ref.get("event_id"), "owner_ref.event_id")
        stored = state.get("owner_events", {}).get(event_id)
        if not isinstance(stored, dict):
            raise RevisionError("owner_ref does not identify a stored authenticated owner event")
        if stored.get("case_id") != case_id:
            raise RevisionError("owner event belongs to another case")
        _text(stored.get("approval_ref"), "stored owner approval_ref")
        _text(stored.get("answer"), "stored owner answer")
        resolved = {"kind": kind, "event_id": event_id, "stored": copy.deepcopy(stored),
                    "ref": copy.deepcopy(ref)}
    elif kind == "feedback_batch_answer":
        if set(ref) != {"kind", "batch_id", "answer_id"}:
            raise RevisionError("feedback batch ref requires only kind, batch_id and answer_id")
        batch_id = _text(ref.get("batch_id"), "owner_ref.batch_id")
        answer_id = _text(ref.get("answer_id"), "owner_ref.answer_id")
        batch = state.get("feedback_batches", {}).get(batch_id)
        answers = batch.get("answers") if isinstance(batch, dict) else None
        if isinstance(answers, dict):
            stored = answers.get(answer_id)
        elif isinstance(answers, list):
            stored = next((row for row in answers if isinstance(row, dict)
                           and row.get("answer_id") == answer_id), None)
        else:
            stored = None
        if not isinstance(stored, dict):
            raise RevisionError("owner_ref does not identify a stored feedback answer")
        if stored.get("status") not in {"applied", "partial"}:
            raise RevisionError("feedback answer was not applied")
        if stored.get("owner_confirmed", batch.get("owner_confirmed")) is not True:
            raise RevisionError("feedback answer lacks authenticated owner provenance")
        for field in ("parent_event_id", "approval_ref", "answer"):
            _text(stored.get(field, batch.get(field)), "stored feedback " + field)
        cases = stored.get("case_ids")
        if not isinstance(cases, list) or case_id not in cases:
            raise RevisionError("feedback answer does not authorize this case")
        resolved = {
            "kind": kind, "batch_id": batch_id, "answer_id": answer_id,
            "stored": copy.deepcopy(stored), "ref": copy.deepcopy(ref),
        }
    else:
        raise RevisionError("owner_ref must identify a stored owner event or feedback batch answer")
    resolved["digest"] = engine.digest(resolved["stored"])
    return resolved


def _draft_content(draft: dict[str, Any]) -> dict[str, Any]:
    draft = inbox_snapshot.normalize_item(draft)
    if draft.get("revision_unsupported"):
        raise RevisionError("provider draft revision unsupported: " + str(draft["revision_unsupported"]))
    required = {"id", "from", "thread_id", "body", "subject", "to", "cc", "bcc", "headers",
                "attachments", "payload"}
    missing = sorted(required - set(draft))
    if missing:
        raise RevisionError("complete draft missing: " + ", ".join(missing))
    if not isinstance(draft["payload"], dict):
        raise RevisionError("draft.payload must be the complete MIME replacement object")
    if not isinstance(draft["attachments"], list):
        raise RevisionError("draft.attachments must be an array")
    recipients = engine.addresses(draft["to"])
    cc = engine.addresses(draft["cc"], empty=True)
    bcc = engine.addresses(draft["bcc"], empty=True)
    headers = draft["headers"]
    if not isinstance(headers, dict):
        raise RevisionError("draft.headers must be an object")
    result = {
        "body": draft["body"] if isinstance(draft["body"], str) else _text(None, "draft.body"),
        "subject": draft["subject"] if isinstance(draft["subject"], str) else _text(None, "draft.subject"),
        "recipients": recipients,
        "cc": cc,
        "bcc": bcc,
        "attachments": copy.deepcopy(draft["attachments"]),
        "headers": copy.deepcopy(headers),
        "payload": copy.deepcopy(draft["payload"]),
    }
    expected_payload = {
        "thread_id": draft["thread_id"], "body": result["body"], "subject": result["subject"],
        "to": result["recipients"], "cc": result["cc"], "bcc": result["bcc"],
        "headers": result["headers"], "attachments": result["attachments"],
    }
    missing_payload = sorted(set(expected_payload) - set(result["payload"]))
    if missing_payload:
        raise RevisionError("complete draft payload missing: " + ", ".join(missing_payload))
    if any(result["payload"][field] != value for field, value in expected_payload.items()):
        raise RevisionError("decoded draft fields contradict the complete replacement payload")
    return result


def _packet_basis(state: dict[str, Any], case: dict[str, Any], packet: Any) -> tuple[
        dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    value = inbox_snapshot.normalize_packet(_object(packet, "packet"))
    proof = verify_packet_inventory(state, value)
    snap = inbox_snapshot.build_snapshot(value)
    if value.get("mailbox") != state.get("mailbox") or snap["thread_id"] != case["thread_id"]:
        raise RevisionError("draft packet belongs to another mailbox or conversation")
    if not engine.fresh(snap["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
        raise RevisionError("fresh complete Gmail packet required")
    if engine.iso(snap["observed_at"]) < engine.iso(case["snapshot"]["observed_at"]):
        raise RevisionError("draft packet precedes the saved conversation")
    drafts = value.get("drafts")
    if not isinstance(drafts, list):
        raise RevisionError("complete packet drafts must be an array")
    return value, drafts, {"snapshot": snap, "inventory": proof}


def _packet(state: dict[str, Any], case: dict[str, Any], packet: Any,
            target_draft_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    value, drafts, proof = _packet_basis(state, case, packet)
    if not any(draft.get("id") == target_draft_id for draft in drafts):
        sent = any(message.get("source_draft_id") == target_draft_id
                   for message in value.get("messages", []) if isinstance(message, dict))
        suffix = " or was already sent" if sent else ""
        raise RevisionError("managed draft is missing" + suffix + "; do not recreate it")
    if len(drafts) != 1:
        raise RevisionError("exactly one current draft is required for revision")
    draft = drafts[0]
    if draft.get("from").casefold() != state["mailbox"].casefold():
        raise RevisionError("managed draft sender does not match mailbox")
    return draft, _draft_content(draft), proof


def _managed_draft(state: dict[str, Any], case_id: str, requested_id: Any) -> dict[str, Any]:
    draft_id = _text(requested_id, "draft_id")
    matches = [row for row in state.get("drafts", {}).values()
               if row.get("case_id") == case_id and row.get("draft_id") == draft_id]
    if len(matches) != 1 or matches[0].get("status") not in {"pending", "present"}:
        raise RevisionError("revision requires one exact active Inbox Manager-managed draft")
    return matches[0]


def _seed_lineage(state: dict[str, Any], managed: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    revisions, lineages = _collections(state)
    lineage_id = "draft-lineage-" + engine.digest(
        [state["mailbox"], managed["case_id"], managed["draft_id"]]
    )[:24]
    lineage = lineages.get(lineage_id)
    if lineage:
        if (lineage.get("draft_id") != managed["draft_id"] or
                lineage.get("case_id") != managed["case_id"]):
            raise RevisionError("draft lineage identity changed")
        return lineage
    base = {
        "body": managed["body"], "subject": managed["subject"],
        "recipients": copy.deepcopy(managed["recipients"]),
        "cc": copy.deepcopy(managed.get("cc", [])), "bcc": copy.deepcopy(managed.get("bcc", [])),
        "attachments": copy.deepcopy(managed.get("attachments", [])),
        "headers": copy.deepcopy(managed.get("reply_headers", observed["headers"])),
    }
    seed_id = "draft-revision-seed-" + engine.digest([lineage_id, managed["hash"]])[:24]
    seed = {
        "id": seed_id, "kind": "seed", "lineage_id": lineage_id,
        "case_id": managed["case_id"], "draft_id": managed["draft_id"],
        "candidate_id": managed["id"], "candidate_hash": managed["hash"],
        "status": "confirmed", "merged_content": base,
        "latest_bot_body": managed["body"], "created_at": managed.get("created_at", engine.stamp()),
    }
    seed["hash"] = engine.digest(seed)
    revisions[seed_id] = seed
    lineage = {
        "id": lineage_id, "mailbox": state["mailbox"], "case_id": managed["case_id"],
        "thread_id": managed["thread_id"], "draft_id": managed["draft_id"],
        "managed_candidate_id": managed["id"], "revision_ids": [seed_id],
        "latest_revision_id": seed_id, "latest_bot_body": managed["body"],
        "created_at": engine.stamp(),
    }
    lineages[lineage_id] = lineage
    return lineage


def _base_content(state: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    revision = state["draft_revisions"].get(lineage.get("latest_revision_id"))
    if not revision or revision.get("status") != "confirmed":
        raise RevisionError("draft lineage lacks a confirmed bot revision")
    if not isinstance(revision.get("merged_content"), dict):
        raise RevisionError("bot comparison text expired; preserve the current draft and ask the owner")
    return copy.deepcopy(revision["merged_content"])


def _target_content(candidate: dict[str, Any]) -> dict[str, Any]:
    body = _text(candidate.get("body"), "candidate.body", 100000)
    if engine.PLACEHOLDER.search(body):
        raise RevisionError("unfinished placeholder in revised reply")
    subject = _text(candidate.get("subject"), "candidate.subject")
    if "\r" in subject or "\n" in subject:
        raise RevisionError("subject cannot contain header separators")
    recipients = engine.addresses(candidate.get("recipients"))
    cc = engine.addresses(candidate.get("cc", []), empty=True)
    bcc = engine.addresses(candidate.get("bcc", []), empty=True)
    if bcc:
        raise RevisionError("BCC is outside this reply workflow")
    return {"body": body, "subject": subject, "recipients": recipients, "cc": cc, "bcc": bcc}


def _knowledge_dependencies(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise RevisionError("candidate.knowledge_dependencies must be a bounded list")
    result = []
    for index, dependency in enumerate(value):
        dependency = _object(dependency, f"candidate.knowledge_dependencies[{index}]")
        if set(dependency) != {"note_id", "version", "updated_at"}:
            raise RevisionError("each knowledge dependency requires exact note_id, version and updated_at")
        note_id = _text(dependency.get("note_id"), "knowledge dependency note_id", 500)
        version = dependency.get("version")
        if type(version) is not int or version < 1:
            raise RevisionError("knowledge dependency version must be a positive integer")
        updated_at = _text(dependency.get("updated_at"), "knowledge dependency updated_at", 200)
        engine.iso(updated_at)
        result.append({"note_id": note_id, "version": version, "updated_at": updated_at})
    if len({row["note_id"] for row in result}) != len(result):
        raise RevisionError("knowledge dependencies must not repeat note IDs")
    return result


def _apply_declared_changes(base: dict[str, Any], requested: Any) -> dict[str, Any]:
    changes = _object(requested, "requested_changes")
    if not changes or set(changes) - set(CONTENT_FIELDS):
        raise RevisionError("requested_changes must name supported changed fields")
    result = {key: copy.deepcopy(base[key]) for key in CONTENT_FIELDS}
    for field, spec in changes.items():
        if field == "body":
            if not isinstance(spec, list) or not spec:
                raise RevisionError("requested_changes.body must be a nonempty replacement list")
            for index, replacement in enumerate(spec):
                replacement = _object(replacement, f"requested_changes.body[{index}]")
                if set(replacement) != {"from", "to"}:
                    raise RevisionError("each body replacement requires exact from and to")
                old = _text(replacement.get("from"), "body replacement from", 100000)
                new = replacement.get("to")
                if not isinstance(new, str) or len(new) > 100000:
                    raise RevisionError("body replacement to must be a string")
                if result["body"].count(old) != 1:
                    raise RevisionError("body replacement must match the last bot revision exactly once")
                result["body"] = result["body"].replace(old, new, 1)
        else:
            spec = _object(spec, "requested_changes." + field)
            if set(spec) != {"from", "to"}:
                raise RevisionError(f"requested_changes.{field} requires exact from and to")
            before, after = spec["from"], spec["to"]
            if field in ADDRESS_FIELDS:
                before = engine.addresses(before, empty=field != "recipients")
                after = engine.addresses(after, empty=field != "recipients")
            elif not isinstance(before, str) or not isinstance(after, str):
                raise RevisionError(f"requested_changes.{field} values must be strings")
            if before != result[field]:
                raise RevisionError(f"requested_changes.{field}.from is not the last bot revision")
            result[field] = after
    return result


def _derive_changes(base: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Use unique complete lines as conservative edit spans, never guessed offsets.

    A manual change in the same line is an overlap. Repeated untouched lines are
    harmless; a repeated changed span or insertion anchor requires owner repair.
    """
    changes = {}
    before, after = base["body"], target["body"]
    if before != after:
        left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
        replacements = []
        for kind, start, end, new_start, new_end in difflib.SequenceMatcher(
                None, left, right, autojunk=False).get_opcodes():
            if kind == "equal":
                continue
            old, new = "".join(left[start:end]), "".join(right[new_start:new_end])
            if not old:
                # Anchor insertions in an adjacent unchanged line. Never use an
                # empty-string match or position alone against a manual draft.
                if start:
                    old = left[start - 1]
                    new = old + new
                elif end < len(left):
                    old = left[end]
                    new = new + old
            if not old or before.count(old) != 1:
                raise RevisionError("ambiguous affected body span; preserve the draft and prepare a new target")
            replacements.append({"from": old, "to": new})
        # Anchors must not overlap another change or silently invent ordering.
        spans = sorted((before.index(row["from"]), before.index(row["from"]) + len(row["from"]))
                       for row in replacements)
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            raise RevisionError("overlapping derived body spans; preserve the draft")
        changes["body"] = replacements
    for field in CONTENT_FIELDS[1:]:
        if base[field] != target[field]:
            changes[field] = {"from": copy.deepcopy(base[field]), "to": copy.deepcopy(target[field])}
    if not changes:
        raise RevisionError("revision target equals the last bot revision")
    if _apply_declared_changes(base, changes) != target:
        raise RevisionError("derived changes do not reproduce the exact target; preserve the draft")
    return changes


def _merge(base: dict[str, Any], current: dict[str, Any], requested: dict[str, Any],
           changes: dict[str, Any]) -> dict[str, Any]:
    merged = {key: copy.deepcopy(current[key]) for key in CONTENT_FIELDS}
    conflicts: list[str] = []
    for field, spec in changes.items():
        if field == "body":
            for replacement in spec:
                old, new = replacement["from"], replacement["to"]
                if merged["body"].count(old) == 1:
                    merged["body"] = merged["body"].replace(old, new, 1)
                elif new and merged["body"].count(new) == 1:
                    continue
                else:
                    conflicts.append("body:" + old[:80])
        else:
            before = base[field]
            if merged[field] == before:
                merged[field] = copy.deepcopy(requested[field])
            elif merged[field] != requested[field]:
                conflicts.append(field)
    if conflicts:
        raise RevisionError("manual edit conflicts with requested revision: " + ", ".join(conflicts))
    return merged


def _merge_derived(base, current, target, changes):
    # Exact line boundaries prevent a changed manual line that happens to end
    # with the old text from being treated as an unaffected passage.
    def occurrences(body, span):
        if not span:
            return 0
        count, offset = 0, 0
        while True:
            index = body.find(span, offset)
            if index < 0:
                return count
            end = index + len(span)
            if (index == 0 or body[index - 1] == "\n") and (
                    span.endswith("\n") or end == len(body) or body[end] == "\n"):
                count += 1
            offset = index + 1
    for row in changes.get("body", []):
        old, new = row["from"], row["to"]
        if not (occurrences(current["body"], old) == 1 and current["body"].count(old) == 1) and not (
                old not in current["body"] and occurrences(current["body"], new) == 1 and
                current["body"].count(new) == 1):
            raise RevisionError("manual edit conflicts with affected body span; preserve the draft")
    return _merge(base, current, target, changes)


def _source_and_profile_current(root: Path, state: dict[str, Any], revision: dict[str, Any]) -> None:
    if revision["profile_version"] != state["version"]:
        raise RevisionError("profile or authority changed; prepare a new revision candidate")
    engine.validate_profile(root, state["config"]["profile_files"])
    for source_id, version in revision["source_versions"].items():
        source = state["sources"].get(source_id)
        if (not source or engine.source_policy(state, source_id) is None or
                source.get("version") != version or source.get("status") != "current" or
                engine.iso(source["valid_until"]) <= engine.utcnow() or
                engine.iso(source["verified_at"]) > engine.utcnow() + timedelta(seconds=5)):
            raise RevisionError("source changed, unavailable or stale: " + source_id)
        if source.get("local_path"):
            path = Path(source["local_path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["content_hash"]:
                raise RevisionError("local knowledge changed: " + source_id)


def _review(revision: dict[str, Any], value: Any) -> dict[str, Any]:
    review = _object(value, "review")
    if review.get("candidate_id") != revision["id"] or review.get("candidate_hash") != revision["hash"]:
        raise RevisionError("review does not bind the exact revision candidate")
    reviewer = _text(review.get("reviewer_id"), "review.reviewer_id")
    if reviewer == revision["writer_id"] or review.get("independent") is not True:
        raise RevisionError("fresh independent revision reviewer required")
    if review.get("decision") != "draft":
        raise RevisionError("revision review must approve a draft")
    assessments = review.get("assessments")
    if (not isinstance(assessments, dict) or set(assessments) != REVIEW_ASSESSMENTS or
            not all(value is True for value in assessments.values())):
        raise RevisionError("all independent revision assessments must pass")
    return {
        "candidate_id": revision["id"], "candidate_hash": revision["hash"],
        "reviewer_id": reviewer, "independent": True, "decision": "draft",
        "assessments": copy.deepcopy(assessments),
        "reason": _text(review.get("reason"), "review.reason", 10000),
        "evidence_ref": _text(review.get("evidence_ref"), "review.evidence_ref"),
        "at": engine.stamp(),
    }


def _rule_allows(state: dict[str, Any], case: dict[str, Any], managed: dict[str, Any],
                 current: dict[str, Any], merged: dict[str, Any], snap: dict[str, Any]) -> None:
    rule = next((row for row in state["config"]["rules"] if row["id"] == managed.get("rule_id")), None)
    if rule is None:
        raise RevisionError("approved draft rule changed or is unavailable")
    allowed = (engine.addresses(snap["correspondents"])
               if rule["recipient_mode"] == "correspondents" else rule["recipients"])
    if not set(merged["recipients"] + merged["cc"]) <= set(allowed):
        raise RevisionError("revised recipients exceed the approved rule")
    if state["mailbox"] in merged["recipients"]:
        raise RevisionError("revised recipients create a self-reply loop")
    if merged["bcc"]:
        raise RevisionError("BCC is outside this reply workflow")
    attachments = current["attachments"]
    if attachments and not rule["allow_attachments"]:
        raise RevisionError("current draft attachments are not approved by the reply rule")
    if len(attachments) > 10:
        raise RevisionError("too many draft attachments")
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise RevisionError("invalid draft attachment")
        _text(attachment.get("id"), "attachment id")
        digest = _text(attachment.get("sha256"), "attachment sha256")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise RevisionError("attachment requires raw SHA-256 hex")
    if case.get("takeover"):
        raise RevisionError("owner takeover protects the current draft")


def _replacement_payload(current: dict[str, Any], merged: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(current["payload"])
    payload.update({
        "body": merged["body"], "subject": merged["subject"], "to": merged["recipients"],
        "cc": merged["cc"], "bcc": merged["bcc"],
        "attachments": copy.deepcopy(current["attachments"]),
        "headers": copy.deepcopy(current["headers"]),
    })
    return payload


def _candidate(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    _auth(state, payload)
    fields = _object(payload.get("candidate"), "candidate")
    case_id = _text(fields.get("case_id"), "candidate.case_id")
    case = state.get("cases", {}).get(case_id)
    if not case:
        raise RevisionError("unknown case")
    managed = _managed_draft(state, case_id, fields.get("draft_id"))
    draft, observed, proof = _packet(state, case, payload.get("packet"), managed["draft_id"])
    lineage = _seed_lineage(state, managed, observed)
    base = _base_content(state, lineage)
    owner = _stored_owner_answer(state, fields.get("owner_ref"), case_id)
    affected_drafts = owner["stored"].get("draft_ids")
    if affected_drafts is not None and (
            not isinstance(affected_drafts, list) or managed["draft_id"] not in affected_drafts):
        raise RevisionError("stored owner answer does not authorize this draft")
    key = _text(fields.get("candidate_id"), "candidate.candidate_id")
    revisions, _ = _collections(state)
    if key in revisions:
        raise RevisionError("revision candidate is immutable; use a new candidate_id")
    if any(row.get("lineage_id") == lineage["id"] and row.get("status") in {"ready", "attempting", "unknown"}
           for row in revisions.values()):
        raise RevisionError("draft lineage already has an unresolved revision")
    target = _target_content(fields)
    derived = "requested_changes" not in fields
    declared = (_derive_changes(base, target) if derived else
                copy.deepcopy(_object(fields.get("requested_changes"), "candidate.requested_changes")))
    if _apply_declared_changes(base, declared) != target:
        raise RevisionError("requested_changes do not produce the exact revision candidate")
    versions = fields.get("source_versions")
    if not isinstance(versions, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in versions.items()):
        raise RevisionError("candidate.source_versions must be an object")
    value = {
        "id": key, "kind": "candidate", "lineage_id": lineage["id"], "case_id": case_id,
        "mailbox": state["mailbox"], "thread_id": case["thread_id"], "draft_id": draft["id"],
        "base_revision_id": lineage["latest_revision_id"], "base_content": base,
        "observed_content": observed, "observed_proof": proof,
        "requested_content": target, "requested_changes": declared,
        "owner_ref": copy.deepcopy(owner["ref"]),
        "owner_answer_digest": owner["digest"], "profile_version": state["version"],
        "source_versions": copy.deepcopy(versions),
        "knowledge_dependencies": _knowledge_dependencies(fields.get("knowledge_dependencies")),
        "evidence": _text(fields.get("evidence"), "candidate.evidence", 30000),
        "rule_id": managed.get("rule_id"), "writer_id": _text(fields.get("writer_id"), "candidate.writer_id"),
        "status": "ready", "created_at": engine.stamp(),
    }
    if derived:
        merged = _merge_derived(base, observed, target, declared)
        _rule_allows(state, case, managed, observed, merged, proof["snapshot"])
        value["checked_content"] = {
            **copy.deepcopy(merged), "attachments": copy.deepcopy(observed["attachments"]),
            "headers": copy.deepcopy(observed["headers"]),
            "payload": _replacement_payload(observed, merged),
        }
        value["revision_contract"] = "target-merged-v1"
        value["review_packet"] = inbox_snapshot.normalize_packet(copy.deepcopy(payload["packet"]))
        value["review_owner_answer"] = copy.deepcopy(owner["stored"])
    value["hash"] = engine.digest(value)
    revisions[key] = value
    lineage["revision_ids"].append(key)
    engine.event(state, "draft_revision_candidate", case_id=case_id, candidate_id=key,
                 lineage_id=lineage["id"])
    return {"candidate_id": key, "candidate_hash": value["hash"], "lineage_id": lineage["id"],
            "status": value["status"],
            **({"review_artifact": copy.deepcopy(value)} if derived else {})}


def _prepare(root: Path, state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    auth = _auth(state, payload)
    revisions, lineages = _collections(state)
    key = _text(payload.get("candidate_id"), "candidate_id")
    revision = revisions.get(key)
    if not revision or revision.get("kind") != "candidate":
        raise RevisionError("unknown revision candidate")
    if revision["status"] != "ready":
        raise RevisionError("revision candidate already reserved or consumed; never blindly retry")
    lineage = lineages.get(revision["lineage_id"])
    if not lineage or lineage.get("latest_revision_id") != revision["base_revision_id"]:
        raise RevisionError("latest bot revision changed; prepare a new candidate")
    case = state["cases"].get(revision["case_id"])
    if not case:
        raise RevisionError("revision case is unavailable")
    owner = _stored_owner_answer(state, revision["owner_ref"], revision["case_id"])
    if owner["digest"] != revision["owner_answer_digest"]:
        raise RevisionError("stored owner answer changed; prepare a new candidate")
    _source_and_profile_current(root, state, revision)
    review = _review(revision, payload.get("review"))
    _, current, proof = _packet(state, case, payload.get("packet"), revision["draft_id"])
    if inbox_snapshot.threading_headers(current["headers"]) != inbox_snapshot.threading_headers(revision["observed_content"]["headers"]):
        raise RevisionError("draft threading headers changed; preserve the draft")
    merge = _merge_derived if revision.get("revision_contract") == "target-merged-v1" else _merge
    merged = merge(revision["base_content"], current, revision["requested_content"],
                   revision["requested_changes"])
    _rule_allows(state, case, state["drafts"][lineage["managed_candidate_id"]], current, merged,
                 proof["snapshot"])
    replacement = _replacement_payload(current, merged)
    if revision.get("revision_contract") == "target-merged-v1":
        recomputed = {**copy.deepcopy(merged), "attachments": copy.deepcopy(current["attachments"]),
                      "headers": copy.deepcopy(current["headers"]), "payload": replacement}
        if recomputed != revision["checked_content"]:
            raise RevisionError("current merged replacement differs from the checked artifact; retire and prepare a new candidate/check")
        if any(proof["snapshot"].get(field) != revision["observed_proof"]["snapshot"].get(field)
               for field in ("message_fingerprint", "trigger_id", "automated", "categories")):
            raise RevisionError("conversation changed since revision check; retire and prepare a new candidate/check")
    action_id = "draft-revision-action-" + engine.digest(
        [lineage["id"], revision["hash"], proof["inventory"]["inventory_id"]]
    )[:24]
    if any(row.get("action_id") == action_id for row in revisions.values()):
        raise RevisionError("revision action already reserved; never blindly retry")
    dependencies = revision.get("knowledge_dependencies", [])
    if dependencies:
        import inbox_knowledge
        checked = inbox_knowledge.check_dependencies(root, dependencies)
        if checked.get("current") is not True:
            stale = ", ".join(checked.get("stale_note_ids", [])) or "unknown"
            raise RevisionError("knowledge dependencies changed or were forgotten: " + stale)
    revision.update({
        "status": "attempting", "review": review, "merged_content": {
            **copy.deepcopy(merged), "attachments": copy.deepcopy(current["attachments"]),
            "headers": copy.deepcopy(current["headers"]), "payload": replacement,
        },
        "prepare_proof": proof, "action_id": action_id, "attempt_token": secrets.token_hex(24),
        "reserved_at": engine.stamp(), "run_id": auth["run_id"],
    })
    import inbox_preparation
    import inbox_reconcile
    preparation = state.get('preparations', {}).get(case['id'])
    if inbox_preparation.enabled(state) and preparation and 'source_versions' in preparation:
        revision['own_revision_observation'] = {
            'case_basis': inbox_reconcile.basis(state, case),
            'preparation_hash': engine.digest(preparation),
            'reevaluation': copy.deepcopy(state.get('reevaluations', {}).get(case['id'])),
        }
    engine.event(state, "draft_revision_reserved", case_id=case["id"], candidate_id=key,
                 action_id=action_id)
    return {
        "candidate_id": key, "candidate_hash": revision["hash"], "lineage_id": lineage["id"],
        "action_id": action_id, "attempt_token": revision["attempt_token"],
        "provider_patch": {
            "tool": "gmail_update_draft",
            "arguments": {"draft_id": revision["draft_id"], "payload": copy.deepcopy(replacement)},
        },
        "limitations": [
            "provider update has no conditional version field; edits after the fresh pre-read cannot be made atomic",
            "a timeout after the provider call is an unknown result and must be reconciled before any new update",
        ],
    }


def _same_readback(revision: dict[str, Any], packet: Any, state: dict[str, Any],
                   case: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
    try:
        _, current, proof = _packet(state, case, packet, revision["draft_id"])
    except (ValueError, RevisionError, KeyError, TypeError) as exc:
        return False, str(exc), {}
    changed = _content_mismatches(revision["merged_content"], current)
    return not changed, (", ".join(changed) if changed else None), proof


def _content_mismatches(expected: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changed = [field for field in CONTENT_FIELDS if current[field] != expected[field]]
    if current["attachments"] != expected["attachments"]:
        changed.append("attachments")
    if inbox_snapshot.threading_headers(current["headers"]) != inbox_snapshot.threading_headers(expected["headers"]):
        changed.append("threading headers")
    if _verification_payload(current["payload"]) != _verification_payload(expected["payload"]):
        changed.append("MIME payload")
    return changed


def _verification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ignore generated delivery headers; preserve every MIME part and content header."""
    value = copy.deepcopy(payload)
    if "headers" in value:
        value["headers"] = {key: item for key, item in inbox_snapshot.canonical_headers(value["headers"]).items()
                            if key not in {"date", "message-id"}}
    return value


def _advance_lineage(state: dict[str, Any], revision: dict[str, Any], proof: dict[str, Any],
                     provider_receipt: str, *, reconciled: bool = False) -> None:
    _, lineages = _collections(state)
    revision["status"] = "confirmed"
    revision["provider_receipt"] = provider_receipt
    revision["readback_proof"] = proof
    revision["confirmed_at"] = engine.stamp()
    if reconciled:
        revision["reconciled_at"] = revision["confirmed_at"]
    lineage = lineages[revision["lineage_id"]]
    lineage["latest_revision_id"] = revision["id"]
    lineage["latest_bot_body"] = revision["merged_content"]["body"]
    lineage["updated_at"] = revision["confirmed_at"]
    managed = state["drafts"][lineage["managed_candidate_id"]]
    managed["latest_revision_id"] = revision["id"]
    managed["latest_bot_body"] = lineage["latest_bot_body"]


def _carry_revision_review(root, state, payload, revision, proof):
    """A scoped answered decision plus exact complete readback needs no restatement."""
    import inbox_preparation
    import inbox_reconcile
    case = state['cases'][revision['case_id']]
    carry = revision.get('own_revision_observation', {})
    item = state.get('preparations', {}).get(case['id'])
    snap = proof['snapshot']
    ref = revision['owner_ref']
    if (not carry or not item or ref.get('kind') != 'owner_event' or
            engine.digest(item) != carry.get('preparation_hash') or
            inbox_reconcile.basis(state, case) != carry.get('case_basis') or
            case.get('takeover') or case.get('hold') not in {None, 'owner_decision'} or
            engine.iso(snap['thread_observed_at']) < engine.iso(revision['reserved_at']) or
            snap.get('message_fingerprint') != case['snapshot'].get('message_fingerprint') or
            snap.get('message_fingerprint') != revision['prepare_proof']['snapshot'].get('message_fingerprint') or
            any(snap.get(key) != case['snapshot'].get(key) for key in ('trigger_id', 'automated', 'categories')) or
            any(row['case_id'] == case['id'] for row in engine.due(state))):
        return False
    owner = _stored_owner_answer(state, ref, case['id'])
    decision = state.get('decisions', {}).get(owner['stored'].get('decision_id'), {})
    selected = decision.get('obligation_ids', [])
    if (owner['digest'] != revision['owner_answer_digest'] or owner['stored'].get('scope') != 'once' or
            decision.get('status') != 'answered' or not selected or
            case.get('owner_answers', {}).get(decision.get('id')) != owner['stored'] or
            any(case.get('obligations', {}).get(key, {}).get('state') != 'needs_action' or
                case['obligations'][key].get('responsibility_type') != 'bot' for key in selected)):
        return False
    queued = state.get('reevaluations', {}).get(case['id'])
    if (queued != carry.get('reevaluation') or queued and
            queued.get('reasons') != ['owner decision answered: ' + decision['id']]):
        return False
    if (not inbox_preparation._dependencies_current(root, state, revision['source_versions'], revision.get('knowledge_dependencies', [])) or
            not inbox_preparation._dependencies_current(root, state, item['source_versions'], item.get('knowledge_dependencies', [])) or
            any(row.get('case_id') == case['id'] and row.get('status') in {'attempting', 'unknown'}
                for row in state.get('actions', {}).values()) or
            any(row.get('case_id') == case['id'] and row.get('status') in {'creating', 'unknown'}
                for row in state.get('drafts', {}).values())):
        return False
    inbox_preparation._record_draft_review(state, _auth(state, payload), case, snap,
                                         revision['draft_id'], selected, copy.deepcopy(item))
    if queued:
        state['reevaluations'].pop(case['id'])
    revision['review_obligation_ids'] = selected
    revision.pop('own_revision_observation', None)
    return True


def _confirm(state: dict[str, Any], payload: dict[str, Any], root=None) -> dict[str, Any]:
    _auth(state, payload)
    revisions, lineages = _collections(state)
    key = _text(payload.get("candidate_id"), "candidate_id")
    revision = revisions.get(key)
    if not revision or revision.get("kind") != "candidate":
        raise RevisionError("unknown revision candidate")
    result = _object(payload.get("result"), "result")
    if result.get("action_id") != revision.get("action_id") or result.get("attempt_token") != revision.get("attempt_token"):
        raise RevisionError("revision result does not bind the reserved action")
    result_hash = engine.digest(result)
    if revision.get("result_hash"):
        if revision["result_hash"] != result_hash:
            raise RevisionError("terminal revision result cannot change")
        return {"candidate_id": key, "lineage_id": revision["lineage_id"],
                "status": revision["status"], "idempotent": True,
                **({"reason": revision["reason"]} if revision.get("reason") else {})}
    if revision.get("status") != "attempting":
        raise RevisionError("revision reservation required")
    status = result.get("status")
    if status not in {"updated", "failed", "unknown"}:
        raise RevisionError("revision result status must be updated, failed or unknown")
    case = state["cases"][revision["case_id"]]
    same, mismatch, readback_proof = False, None, {}
    if status == "updated":
        _text(result.get("provider_receipt"), "provider_receipt", 10000)
        same, mismatch, readback_proof = _same_readback(revision, result.get("packet"), state, case)
    elif status == "failed":
        if result.get("definitive_not_updated") is not True:
            raise RevisionError("a failed result requires definitive_not_updated; use unknown otherwise")
        _text(result.get("reason"), "failure reason", 10000)
        _text(result.get("provider_receipt"), "failure provider_receipt", 10000)
    else:
        _text(result.get("reason"), "unknown result reason", 10000)
    inventory_id = revision["prepare_proof"]["inventory"]["inventory_id"]
    from inbox_retrieval import invalidate_state
    invalidation = invalidate_state(state, state["mailbox"], inventory_id,
                                    "draft revision result: " + status)
    if status == "updated":
        if same:
            _advance_lineage(state, revision, readback_proof, result["provider_receipt"])
            _carry_revision_review(root, state, payload, revision, readback_proof)
        else:
            revision["status"] = "unknown"
            revision["reason"] = "provider readback mismatch: " + (mismatch or "unknown mismatch")
    elif status == "failed":
        revision["status"] = "failed"
        revision["reason"] = _text(result.get("reason"), "failure reason", 10000)
        revision["provider_receipt"] = _text(result.get("provider_receipt"), "failure provider_receipt", 10000)
    else:
        revision["status"] = "unknown"
        revision["reason"] = _text(result.get("reason"), "unknown result reason", 10000)
    revision["result_hash"] = result_hash
    revision["invalidation"] = invalidation
    revision["updated_at"] = engine.stamp()
    engine.event(state, "draft_revision_" + revision["status"], case_id=case["id"],
                 candidate_id=key, action_id=revision["action_id"])
    return {"candidate_id": key, "lineage_id": revision["lineage_id"],
            "status": revision["status"], "idempotent": False,
            **({"reason": revision["reason"]} if revision.get("reason") else {})}


def _reconcile(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile an interrupted/unknown update from provider state; never write Gmail."""
    _auth(state, payload)
    revisions, _ = _collections(state)
    key = _text(payload.get("candidate_id"), "candidate_id")
    revision = revisions.get(key)
    if not revision or revision.get("kind") != "candidate":
        raise RevisionError("unknown revision candidate")
    provider_receipt = _text(payload.get("provider_receipt"), "provider_receipt", 10000)
    reconcile_hash = engine.digest({"packet": payload.get("packet"),
                                    "provider_receipt": provider_receipt})
    prior = next((row for row in revision.get("reconciliations", [])
                  if row.get("input_hash") == reconcile_hash), None)
    if prior:
        return {"candidate_id": key, "lineage_id": revision["lineage_id"],
                "status": revision["status"], "idempotent": True,
                **({"reason": prior["reason"]} if prior.get("reason") else {})}
    if revision.get("status") == "confirmed":
        raise RevisionError("confirmed revision cannot be reconciled with changed evidence")
    if revision.get("status") not in {"attempting", "unknown"}:
        raise RevisionError("only an attempting or unknown revision can be reconciled")
    case = state["cases"].get(revision["case_id"])
    if not case:
        raise RevisionError("revision case is unavailable")
    value, drafts, proof = _packet_basis(state, case, payload.get("packet"))
    if engine.iso(proof["snapshot"]["observed_at"]) < engine.iso(revision["reserved_at"]):
        raise RevisionError("revision reconciliation packet predates the reserved update")

    target = [draft for draft in drafts if draft.get("id") == revision["draft_id"]]
    reason = None
    if len(drafts) != 1 or len(target) != 1:
        sent = any(message.get("source_draft_id") == revision["draft_id"]
                   for message in value.get("messages", []) if isinstance(message, dict))
        if not target:
            reason = ("managed draft is absent or was sent; keep the update unknown and do not "
                      "recreate or retry it" if sent else
                      "managed draft is absent; keep the update unknown and do not recreate or retry it")
        else:
            reason = "multiple drafts make revision reconciliation ambiguous; preserve all drafts"
    else:
        try:
            current = _draft_content(target[0])
            changed = _content_mismatches(revision["merged_content"], current)
            if changed:
                reason = "current draft differs from the reserved revision: " + ", ".join(changed)
        except (RevisionError, ValueError, KeyError, TypeError) as exc:
            reason = "current draft could not be verified: " + str(exc)

    record = {"input_hash": reconcile_hash, "provider_receipt": provider_receipt,
              "observed_proof": proof, "at": engine.stamp()}
    if reason is None:
        record["status"] = "confirmed"
        revision.setdefault("reconciliations", []).append(record)
        _advance_lineage(state, revision, proof, provider_receipt, reconciled=True)
        revision.pop("reason", None)
    else:
        record.update(status="unknown", reason=reason)
        revision.setdefault("reconciliations", []).append(record)
        revision["status"] = "unknown"
        revision["reason"] = reason
        revision["updated_at"] = engine.stamp()
    engine.event(state, "draft_revision_reconciled_" + revision["status"],
                 case_id=case["id"], candidate_id=key, action_id=revision.get("action_id"))
    return {"candidate_id": key, "lineage_id": revision["lineage_id"],
            "status": revision["status"], "idempotent": False,
            **({"reason": reason} if reason else {})}


def _retire(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Retire an untouched candidate so its lineage can accept a replacement."""
    auth = _auth(state, payload)
    revisions, _ = _collections(state)
    key = _text(payload.get("candidate_id"), "candidate_id")
    reason = _text(payload.get("reason"), "retirement reason", 10000)
    revision = revisions.get(key)
    if not revision or revision.get("kind") != "candidate":
        raise RevisionError("unknown revision candidate")
    retirement_hash = engine.digest({"candidate_id": key, "reason": reason})
    if revision.get("status") == "retired":
        if revision.get("retirement_hash") != retirement_hash:
            raise RevisionError("terminal revision retirement cannot change")
        return {"candidate_id": key, "lineage_id": revision["lineage_id"],
                "status": "retired", "reason": revision["reason"], "idempotent": True}
    if revision.get("status") != "ready":
        raise RevisionError("only an unreserved ready revision can be retired")
    reservation_fields = {
        "action_id", "attempt_token", "prepare_proof", "reserved_at", "merged_content",
    }
    if any(field in revision for field in reservation_fields):
        raise RevisionError("a revision with a reserved action cannot be retired")
    revision.update({
        "status": "retired", "reason": reason, "retirement_hash": retirement_hash,
        "retired_at": engine.stamp(), "retired_by_run_id": auth["run_id"],
    })
    engine.event(state, "draft_revision_retired", case_id=revision["case_id"],
                 candidate_id=key, lineage_id=revision["lineage_id"])
    return {"candidate_id": key, "lineage_id": revision["lineage_id"],
            "status": "retired", "reason": reason, "idempotent": False}


def execute(home: str | Path, command: str, payload: Any) -> dict[str, Any]:
    if command not in COMMANDS:
        raise RevisionError("unknown revision command")
    data = _object(payload, "payload")
    with engine.transaction(home) as (root, box):
        state = box[0]
        if state is None:
            raise RevisionError("inbox engine not initialized; run setup")
        _collections(state)
        if command == "revision-candidate":
            result = _candidate(state, data)
        elif command == "revision-prepare":
            result = _prepare(root, state, data)
        elif command == "revision-confirm":
            result = _confirm(state, data, root=root)
        elif command == "revision-reconcile":
            result = _reconcile(state, data)
        else:
            result = _retire(state, data)
        box[0] = state
        return copy.deepcopy(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--home", required=True)
    parser.add_argument("--input", help="Private JSON file; omit for redirected stdin")
    args = parser.parse_args()
    try:
        if args.input:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            if sys.stdin.isatty():
                raise RevisionError("JSON input requires --input FILE or file redirection")
            payload = json.load(sys.stdin)
        result = execute(args.home, args.command, payload)
    except (json.JSONDecodeError, RevisionError, ValueError, KeyError, TypeError, OSError,
            sqlite3.Error) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True) + "\n")
        return 2
    sys.stdout.write(json.dumps({"ok": True, "result": result}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
