#!/usr/bin/env python3
"""Immutable owner reports and authenticated, replay-safe feedback batches.

The host maps ordinary owner language to the structured answer packet.  This
module verifies that mapping against a saved report and current engine state,
records every answer, and returns bounded follow-up work.  It never calls an
email provider.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


CONFIRMED_DRAFT_STATES = {"pending", "current", "updated", "confirmed"}
STALE_ERRORS = (
    "context changed", "obligation changed", "version changed", "fresh conversation",
    "decision is not pending", "reconcile uncertain send",
)


def _engine():
    import inbox_engine
    return inbox_engine


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _text(value: Any, field: str, maximum: int = 10_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid " + field)
    return value


def _stores(state: dict[str, Any]) -> None:
    state.setdefault("report_snapshots", {})
    state.setdefault("report_question_numbers", {})
    if "next_report_question_number" not in state:
        numbers = state["report_question_numbers"]
        # Schema-1 dictionaries are bounded during this additive migration. A
        # schema-2 Records mapping must stay lazy; this feature starts empty there.
        state["next_report_question_number"] = (
            max((value for value in numbers.values() if type(value) is int), default=0) + 1
            if type(numbers) is dict else 1)
    state.setdefault("feedback_batches", {})
    state.setdefault("owner_events", {})


def _hot_state(state: dict[str, Any]) -> dict[str, Any]:
    """Use indexed active records when schema-2 storage is available."""
    try:
        import inbox_store
    except ImportError:
        return state
    return inbox_store.hot_state(state)


def _case_records(state: dict[str, Any], name: str, case_id: str):
    rows = state.get(name, {})
    selector = getattr(rows, "select", None)
    if callable(selector):
        return list(selector(_with_keys=True, case_id=case_id))
    return [(key, row) for key, row in rows.items() if row.get("case_id") == case_id]


def _in_scope(state: dict[str, Any], case: dict[str, Any]) -> bool:
    try:
        import inbox_priority
    except ImportError:
        return True
    return inbox_priority.case_in_scope(state, case)


def _draft_revision(record: dict[str, Any]) -> str | None:
    for field in ("latest_revision_id", "revision_id", "draft_revision_id",
                  "provider_revision", "body_hash", "hash"):
        value = record.get(field)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return None


def _confirmed_drafts(state: dict[str, Any], case: dict[str, Any]) -> list[dict[str, Any]]:
    """Return provider-proved draft identities only; candidates are excluded."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    observed_ids = set(case.get("snapshot", {}).get("draft_ids", []))
    for candidate_id, record in _case_records(state, "drafts", case["id"]):
        if record.get("status") not in CONFIRMED_DRAFT_STATES:
            continue
        if (record.get("mailbox", state.get("mailbox")) != state.get("mailbox") or
                record.get("thread_id", case.get("thread_id")) != case.get("thread_id")):
            continue
        draft_id = record.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            continue
        proved = bool(record.get("provider_receipt") or record.get("provider_update_receipt") or
                      record.get("verified_at") or draft_id in observed_ids)
        if not proved:
            continue
        revision = _draft_revision(record)
        updated = bool(record.get("latest_revision_id") or record.get("provider_update_receipt") or
                       record.get("previous_revision_id") or
                       record.get("revision_number", 1) not in {None, 1})
        from inbox_views import draft_covers_obligation
        covered_ids = set(record.get("obligation_ids", [])) | set(record.get("review_obligation_versions", {}))
        needs_refresh = record.get("status") == "pending" and any(
            row.get("state") != "handled" and not draft_covers_obligation(state, case, record, row)
            for key, row in case.get("obligations", {}).items() if key in covered_ids)
        summary = (record.get("summary") if record.get("summary_content_current") and
                   record.get("summary_candidate_hash") == record.get("hash") and
                   not record.get("latest_revision_id") and not needs_refresh and
                   record.get("profile_version") == state.get("version") else None)
        revision_record = state.get('draft_revisions', {}).get(record.get('latest_revision_id'), {})
        revision_current = bool(revision_record.get('status') == 'confirmed' and
            revision_record.get('readback_proof', {}).get('snapshot', {}).get('content_fingerprint') ==
            case.get('snapshot', {}).get('content_fingerprint'))
        fallback_current = (record.get('summary_content_current') is not False and
                            not record.get('latest_revision_id')) or revision_current
        if not summary and fallback_current:
            asks = [item['ask'] for key, item in case.get('obligations', {}).items()
                    if key in covered_ids and draft_covers_obligation(state, case, record, item)]
            if asks:
                summary = 'Draft prepared for review: ' + '; '.join(asks)
        result.append({
            "candidate_id": candidate_id,
            "case_id": case["id"],
            "draft_id": draft_id,
            "revision_id": revision,
            "status": "updated_verified" if updated else "created_verified",
            "needs_refresh": needs_refresh,
            "summary": summary,
        })
        seen.add(draft_id)
    # A complete current provider snapshot proves existence, even when the draft
    # was created manually and therefore has no Inbox Manager candidate record.
    if case.get("snapshot", {}).get("complete") is True:
        for draft_id in sorted(observed_ids | set(case.get("owner_draft_ids", []))):
            if draft_id not in seen:
                result.append({"candidate_id": None, "case_id": case["id"],
                               "draft_id": draft_id, "revision_id": None,
                               "status": "current_verified", "summary": None})
    return sorted(result, key=lambda row: (row["draft_id"], row.get("candidate_id") or ""))


def _decision_group_key(decision: dict[str, Any]) -> str:
    value = decision.get("shared_question_id") or decision.get("question_group_id")
    return "shared:" + str(value) if value else "decision:" + decision["id"]


def _decision_drafts(state: dict[str, Any], case: dict[str, Any],
                     decision: dict[str, Any]) -> list[dict[str, Any]]:
    drafts = _confirmed_drafts(state, case)
    explicit = set(decision.get("draft_ids", []))
    if decision.get("draft_id"):
        explicit.add(decision["draft_id"])
    candidate_ids = set(decision.get("candidate_ids", []))
    if decision.get("candidate_id"):
        candidate_ids.add(decision["candidate_id"])
    if case.get("pending_candidate_id"):
        candidate_ids.add(case["pending_candidate_id"])
    if explicit or candidate_ids:
        return [row for row in drafts if row["draft_id"] in explicit or
                row.get("candidate_id") in candidate_ids]
    # A single provider-proved draft in the conversation is unambiguous. Several
    # drafts require an explicit link rather than revising all of them by guess.
    return drafts if len(drafts) == 1 else []


def _build_snapshot(state: dict[str, Any], *, created_at: str,
                    persist_numbers: bool = True) -> dict[str, Any]:
    store = state
    if persist_numbers:
        _stores(store)
    state = _hot_state(state)
    groups: dict[str, list[dict[str, Any]]] = {}
    for decision in state.get("decisions", {}).values():
        case = state.get("cases", {}).get(decision.get("case_id"))
        if (decision.get("status") not in {"pending", "rule_requested"} or not case or
                not _in_scope(state, case) or decision.get("mailbox") != state.get("mailbox") or
                decision.get("thread_id") != case.get("thread_id")):
            continue
        groups.setdefault(_decision_group_key(decision), []).append(decision)

    number_map = (store["report_question_numbers"] if persist_numbers else
                  dict(store.get("report_question_numbers", {})))
    next_number = store.get("next_report_question_number", 1)
    if type(next_number) is not int or next_number < 1:
        raise ValueError("invalid next report question number")
    questions = []
    for group_key, decisions in sorted(groups.items(), key=lambda row: min(
            (item.get("created_at", ""), item["id"]) for item in row[1])):
        existing = [number_map.get(item["id"]) for item in decisions if number_map.get(item["id"])]
        number = min(existing) if existing else next_number
        if not existing:
            next_number += 1
        for item in decisions:
            number_map[item["id"]] = number

        decisions = sorted(decisions, key=lambda item: item["id"])
        decision_bindings = []
        obligations = []
        drafts = []
        case_ids = []
        for decision in decisions:
            case = state["cases"][decision["case_id"]]
            case_ids.append(case["id"])
            decision_bindings.append({
                "decision_id": decision["id"], "version": decision["version"],
                "case_id": case["id"], "thread_id": case["thread_id"],
                "message_fingerprint": decision.get("message_fingerprint"),
                "profile_version": decision.get("profile_version"),
            })
            for obligation_id in decision.get("obligation_ids", []):
                obligation = case.get("obligations", {}).get(obligation_id)
                if obligation:
                    obligations.append({"case_id": case["id"], "obligation_id": obligation_id,
                                        "version": obligation.get("version")})
            drafts.extend(_decision_drafts(state, case, decision))
        first = decisions[0]
        first_case = state["cases"][first["case_id"]]
        context = (first.get("researched_context") or first.get("context") or
                   first_case.get("preparation", {}).get("researched_context") or
                   first_case.get("context"))
        unique_drafts = {(_digest(row)): row for row in drafts}
        questions.append({
            "number": number,
            "question": first["question"],
            "recommendation": first.get("recommendation"),
            "researched_context": context,
            "decision_ids": [item["id"] for item in decisions],
            "decisions": decision_bindings,
            "case_ids": sorted(set(case_ids)),
            "obligations": sorted(obligations, key=lambda row: (row["case_id"], row["obligation_id"])),
            "draft_revisions": sorted(unique_drafts.values(),
                                      key=lambda row: (row["case_id"], row["draft_id"])),
            "group_key": group_key,
        })

    questions.sort(key=lambda row: row["number"])
    if persist_numbers:
        store["next_report_question_number"] = next_number
    all_drafts = []
    for case in state.get("cases", {}).values():
        if _in_scope(state, case):
            all_drafts.extend(_confirmed_drafts(state, case))
    content = {"mailbox": state.get("mailbox"), "profile_version": state.get("version"),
               "questions": questions,
               "drafts": sorted(all_drafts, key=lambda row: (row["case_id"], row["draft_id"]))}
    return {**content, "created_at": created_at, "content_hash": _digest(content)}


def _validate_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    content = {key: snapshot.get(key) for key in
               ("mailbox", "profile_version", "questions", "drafts")}
    if snapshot.get("content_hash") != _digest(content):
        raise ValueError("saved report contents do not match their immutable hash")
    return snapshot


def report_snapshot(state: dict[str, Any], report_id: str | None = None) -> dict[str, Any]:
    """Persist and return an immutable report/question mapping."""
    engine = _engine()
    _stores(state)
    candidate = _build_snapshot(state, created_at=engine.stamp())
    key = report_id or "report-" + candidate["content_hash"][:20]
    _text(key, "report_id", 200)
    candidate["report_id"] = key
    prior = state["report_snapshots"].get(key)
    if prior:
        _validate_report(prior)
        if prior.get("content_hash") != candidate["content_hash"]:
            raise ValueError("report id already identifies different contents")
        state["latest_report_id"] = key
        return copy.deepcopy(prior)
    state["report_snapshots"][key] = candidate
    state["latest_report_id"] = key
    return copy.deepcopy(candidate)


def _safe(value: Any) -> str:
    from html import escape
    import re
    value = escape(str(value or "Not recorded"), quote=False)
    value = re.sub(r"([\\`*_\[\]|])", r"\\\1", value)
    return "<br>".join(value.splitlines())


def render(state: dict[str, Any], report_id: str | None = None, base_report: str | None = None) -> str:
    """Append verified drafts and the saved numbered questions to the full report."""
    state = _hot_state(state)
    key = report_id or state.get("latest_report_id")
    snapshot = state.get("report_snapshots", {}).get(key) if key else None
    persisted = snapshot is not None
    if snapshot is None:
        # This fallback keeps read-only diagnostics useful. Answerable reports are
        # persisted by report_snapshot before rendering in the cycle finish path.
        snapshot = _build_snapshot(state, created_at=_engine().stamp(), persist_numbers=False)
        snapshot["report_id"] = "unpersisted-" + snapshot["content_hash"][:20]
    else:
        _validate_report(snapshot)
    if base_report is None:
        from inbox_views import owner_report
        base_report = owner_report(state)
    from inbox_views import gmail_thread_url
    question_numbers_by_draft = {}
    for question in snapshot["questions"]:
        for item in question.get("draft_revisions", []):
            question_numbers_by_draft.setdefault((item["case_id"], item["draft_id"]), set()).add(
                question["number"])
    unresolved_drafts = []
    for candidate_id, record in state.get("drafts", {}).items():
        if record.get("status") not in {"creating", "unknown", "failed", "conflicted", "blocked"}:
            continue
        case = state.get("cases", {}).get(record.get("case_id"))
        if case and _in_scope(state, case):
            unresolved_drafts.append((candidate_id, record, case))
    draft_lines = []
    if not snapshot["drafts"] and not unresolved_drafts:
        explanations = []
        for case_id, preparation in state.get("preparations", {}).items():
            case = state.get("cases", {}).get(case_id)
            if not case or not _in_scope(state, case) or case.get("state") == "handled":
                continue
            explanations.append((case, preparation))
        if not explanations:
            draft_lines.append("None.")
        else:
            draft_lines.extend(["| Conversation | Draft status | Reason | Email |",
                                "| --- | --- | --- | --- |"])
            labels = {"no_reply": "No draft needed", "needs_owner_answer": "Waiting for owner answer",
                      "blocked": "Draft blocked", "draft_ready": "Draft expected; none verified"}
            for case, preparation in sorted(explanations, key=lambda row: row[0]["id"]):
                url = gmail_thread_url(state, case)
                cells = [case.get("topic") or case.get("unresolved_ask"),
                         labels.get(preparation.get("disposition"), preparation.get("disposition")),
                         preparation.get("evidence") or "Reason not recorded"]
                draft_lines.append("| " + " | ".join([*map(_safe, cells),
                    "[Open email](" + url + ")"]) + " |")
    else:
        draft_lines.extend(["| Conversation | Draft status | Summary | Questions | Email |",
                            "| --- | --- | --- | --- | --- |"])
        labels = {"created_verified": "Created and verified", "updated_verified": "Updated and verified",
                  "current_verified": "Current in latest provider read"}
        for row in snapshot["drafts"]:
            case = state.get("cases", {}).get(row["case_id"], {})
            url = gmail_thread_url(state, case)
            topic = case.get("topic") or case.get("unresolved_ask")
            numbers = sorted(question_numbers_by_draft.get((row["case_id"], row["draft_id"]), []))
            questions = ", ".join(f"Question {number}" for number in numbers) if numbers else "None"
            status = labels.get(row["status"], row["status"].replace("_", " ").capitalize())
            if row.get("needs_refresh"):
                status += "; needs refresh for current ask"
            cells = [topic, status,
                     row.get("summary") or "Summary not recorded", questions]
            draft_lines.append("| " + " | ".join([*map(_safe, cells), "[Open email](" + url + ")"]) + " |")
        unresolved_labels = {"creating": "Creation pending", "unknown": "Status unknown",
                             "failed": "Creation failed", "conflicted": "Conflict needs review",
                             "blocked": "Blocked"}
        for _, row, case in sorted(unresolved_drafts, key=lambda item: (item[2]["id"], item[0])):
            topic = case.get("topic") or case.get("unresolved_ask")
            cells = [topic, unresolved_labels[row["status"]],
                     row.get("reason") or "Resolution evidence not recorded", "None"]
            draft_lines.append("| " + " | ".join([*map(_safe, cells),
                "[Open email](" + gmail_thread_url(state, case) + ")"]) + " |")
    question_lines = []
    if not snapshot["questions"]:
        question_lines.append("None.")
    else:
        if persisted:
            question_lines.append("Saved report reference: `" + snapshot["report_id"] + "`")
        else:
            question_lines.append("Report reference: `" + snapshot["report_id"] + "` (read-only preview). "
                                  "Its ordering is not saved and cannot be used for answer mapping; "
                                  "finish a cycle to save an answerable numbered report.")
        for row in snapshot["questions"]:
            first_case = state.get("cases", {}).get(row.get("case_ids", [None])[0], {})
            url = gmail_thread_url(state, first_case)
            prefix = f"{row['number']}." if persisted else "-"
            question_lines.append(f"{prefix} [{_safe(row['question'])}]({url})")
            question_lines.append("   Recommendation: " +
                                  _safe(row.get("recommendation") or "No recommendation recorded"))
            question_lines.append("   Researched context: " +
                                  _safe(row.get("researched_context") or
                                        "No researched context recorded"))
            if row["draft_revisions"]:
                links = []
                for item in row["draft_revisions"]:
                    case = state.get("cases", {}).get(item["case_id"], {})
                    topic = case.get("topic") or case.get("unresolved_ask") or "conversation"
                    links.append("[Open draft for " + _safe(topic) + "](" +
                                 gmail_thread_url(state, case) + ")")
                question_lines.append("   Affected drafts: " + ", ".join(links))
            else:
                question_lines.append("   Affected drafts: None.")
    return (base_report.rstrip() + "\n\n### Drafts\n\n" + "\n".join(draft_lines) +
            "\n\n### Questions\n\n" + "\n".join(question_lines) + "\n")


def _report_question(state: dict[str, Any], report_id: Any, number: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(report_id, str) or report_id not in state["report_snapshots"]:
        return None, "unknown report reference"
    _validate_report(state["report_snapshots"][report_id])
    if type(number) is not int or number < 1:
        return None, "question_number must be a positive integer"
    matches = [row for row in state["report_snapshots"][report_id]["questions"] if row["number"] == number]
    if len(matches) != 1:
        return None, "question number is not unique in that report"
    return matches[0], None


def _current_draft(state: dict[str, Any], binding: dict[str, Any]) -> bool:
    case = state.get("cases", {}).get(binding["case_id"])
    if not case:
        return False
    live = next((row for row in _confirmed_drafts(state, case)
                 if row["draft_id"] == binding["draft_id"]), None)
    return bool(live and live.get("revision_id") == binding.get("revision_id"))


def _resolve_answer(state: dict[str, Any], batch: dict[str, Any], item: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    report_id = item.get("report_id", batch.get("report_id"))
    question = None
    if item.get("question_number") is not None:
        question, error = _report_question(state, report_id, item["question_number"])
        if error:
            return [], [], error
    explicit_id = item.get("decision_id")
    if explicit_id is not None:
        if not isinstance(explicit_id, str) or not explicit_id:
            return [], [], "invalid decision_id"
        if question and explicit_id not in question["decision_ids"]:
            return [], [], "decision does not match the report question"
        decisions = [next((row for row in (question or {}).get("decisions", [])
                           if row["decision_id"] == explicit_id), None)] if question else [{
                               "decision_id": explicit_id, "version": item.get("expected_version") }]
    elif question:
        decisions = list(question["decisions"])
    else:
        return [], [], "answer needs a report question number or explicit decision id"
    if any(row is None for row in decisions):
        return [], [], "decision is not mapped by that report"
    if not question and any(type(row.get("version")) is not int for row in decisions):
        return [], [], "explicit decision answer needs expected_version"
    case_ids = item.get("case_ids")
    mapped_cases = {row["case_id"] for row in decisions if row.get("case_id")}
    if case_ids is not None:
        if (not isinstance(case_ids, list) or not case_ids or len(set(case_ids)) != len(case_ids) or
                not all(isinstance(value, str) for value in case_ids)):
            return [], [], "case_ids must be distinct explicit cases"
        if mapped_cases and not set(case_ids).issubset(mapped_cases):
            return [], [], "answer case scope exceeds the mapped question"
        decisions = [row for row in decisions if not row.get("case_id") or row["case_id"] in case_ids]
    return decisions, list((question or {}).get("draft_revisions", [])), None


def _child_id(parent_event_id: str, item: dict[str, Any]) -> str:
    return "answer-" + _digest({"parent_event_id": parent_event_id, "answer": item})[:20]


def _direct_correction(state: dict[str, Any], item: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if item.get("kind") != "draft_correction":
        return None, None
    if item.get("scope", "once") != "once":
        return None, "draft correction scope must be once"
    case_id, draft_id = item.get("case_id"), item.get("draft_id")
    if not isinstance(case_id, str) or not isinstance(draft_id, str):
        return None, "draft correction needs explicit case_id and draft_id"
    case = state.get("cases", {}).get(case_id)
    if not case or not _in_scope(state, case):
        return None, "unknown or out-of-scope correction case"
    engine = _engine()
    if engine.pending_decisions(state, case):
        return None, "draft correction has a pending owner question"
    try:
        current = engine.fresh(case.get("snapshot", {}).get("observed_at"),
                               state["config"]["transport"]["snapshot_max_age_seconds"])
    except (KeyError, TypeError, ValueError):
        current = False
    if not current:
        return None, "draft correction needs a fresh conversation read"
    matches = [row for row in _confirmed_drafts(state, case)
               if row["draft_id"] == draft_id and row.get("candidate_id")]
    if len(matches) != 1:
        return None, "draft correction does not identify one current managed draft"
    return matches[0], None


def _answer_child(state: dict[str, Any], batch: dict[str, Any], item: dict[str, Any],
                  index: int, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply one immutable saved answer, retaining its original provenance on retry."""
    engine = _engine()
    parent_event_id = batch["parent_event_id"]
    approval = batch["approval_ref"]
    batch_id = batch["batch_id"]
    answer_id = item.get("answer_id") or _child_id(parent_event_id, item)
    _text(answer_id, "answer_id", 200)
    answer = _text(item.get("answer"), "owner answer")
    scope = item.get("scope", "once")
    if scope not in {"once", "standing_rule", "takeover"}:
        raise ValueError("explicit owner decision scope required")
    owner_ref = {"kind": "feedback_batch_answer", "batch_id": batch_id, "answer_id": answer_id}
    child_event_id = "feedback-note-" + _digest({"batch": batch_id, "answer": answer_id})[:24]
    immutable = {
        "answer_id": answer_id, "index": index, "answer": answer, "scope": scope,
        "parent_event_id": parent_event_id, "approval_ref": approval, "owner_confirmed": True,
        "owner_ref": owner_ref, "owner_event_id": child_event_id,
    }
    if previous:
        if any(previous.get(key) != value for key, value in immutable.items()):
            raise ValueError("saved feedback child does not match immutable owner input")
        created_at = previous.get("created_at")
        _text(created_at, "saved answer created_at", 200)
    else:
        created_at = engine.stamp()
    child = {**immutable, "decision_ids": [], "case_ids": [], "obligation_ids": [],
             "draft_ids": [], "actions": [], "created_at": created_at,
             "attempt_count": (previous.get("attempt_count", 1) + 1 if previous else 1)}

    correction, correction_error = _direct_correction(state, item)
    decisions, draft_bindings, error = ([], [], None)
    if item.get("kind") != "draft_correction":
        decisions, draft_bindings, error = _resolve_answer(state, batch, item)
    elif correction_error:
        error = correction_error
    if error:
        child.update(status="ambiguous", reason=error, next="clarify_mapping")
    elif correction:
        child.update(status="applied", next="recheck", case_ids=[correction["case_id"]],
                     draft_ids=[correction["draft_id"]],
                     actions=[{"kind": "revise_draft", "case_id": correction["case_id"],
                               "draft_id": correction["draft_id"],
                               "revision_id": correction.get("revision_id"), "owner_ref": owner_ref}])
        engine.enqueue_recheck(state, state["cases"][correction["case_id"]],
                               "authenticated owner draft correction: " + answer_id)
    elif any(not _current_draft(state, binding) for binding in draft_bindings):
        child.update(status="stale", reason="a mapped draft revision is no longer current",
                     next="refresh_context")
    else:
        applications = []
        for mapped in decisions:
            decision = state.get("decisions", {}).get(mapped["decision_id"])
            case_id = mapped.get("case_id") or (decision or {}).get("case_id")
            application = {"decision_id": mapped["decision_id"], "case_id": case_id,
                           "expected_version": mapped.get("version")}
            try:
                result = engine.decision_answer(state, {
                    "owner_confirmed": True, "approval_ref": approval,
                    "event_id": "owner-child-" + _digest({"batch": batch_id, "answer": answer_id,
                                                           "decision": mapped["decision_id"]})[:24],
                    "decision_id": mapped["decision_id"], "expected_version": mapped.get("version"),
                    "case_id": case_id, "scope": scope, "answer": answer,
                    **({"once_rule": item["once_rule"]} if "once_rule" in item else {}),
                })
                application.update(status="applied", event_id=result["event_id"], next=result.get("next"))
                saved_event = state["owner_events"][result["event_id"]]
                saved_event.update(kind="feedback_batch_answer", parent_event_id=parent_event_id,
                                   batch_id=batch_id, answer_id=answer_id, owner_confirmed=True,
                                   provenance_approved=True, owner_ref=owner_ref)
            except ValueError as exc:
                message = str(exc)
                application.update(status="stale" if any(mark in message for mark in STALE_ERRORS) else "blocked",
                                   reason=message)
            applications.append(application)
        applied = [row for row in applications if row["status"] == "applied"]
        if applied:
            child["status"] = "applied" if len(applied) == len(applications) else "partial"
            child["next"] = "recheck"
        else:
            child["status"] = ("stale" if applications and
                               all(row["status"] == "stale" for row in applications)
                               else "blocked")
            child["next"] = "refresh_context" if child["status"] == "stale" else "review"
        child["applications"] = applications
        child["decision_ids"] = [row["decision_id"] for row in decisions]
        child["case_ids"] = sorted({row["case_id"] for row in applied if row.get("case_id")})
        obligations = set()
        saved_report = state.get("report_snapshots", {}).get(
            item.get("report_id", batch.get("report_id")), {})
        applied_ids = {row["decision_id"] for row in applied}
        for saved_question in saved_report.get("questions", []):
            if applied_ids.intersection(saved_question.get("decision_ids", [])):
                obligations.update(binding["obligation_id"] for binding in
                                   saved_question.get("obligations", [])
                                   if binding["case_id"] in child["case_ids"])
        if not saved_report:
            for decision_id in applied_ids:
                obligations.update(state["decisions"][decision_id].get("obligation_ids", []))
        child["obligation_ids"] = sorted(obligations)
        child["draft_ids"] = sorted({row["draft_id"] for row in draft_bindings
                                      if row["case_id"] in child["case_ids"]})
        for case_id in child["case_ids"]:
            case_drafts = [row for row in draft_bindings if row["case_id"] == case_id]
            if case_drafts:
                child["actions"].extend({"kind": "revise_draft", "case_id": case_id,
                                          "draft_id": row["draft_id"], "revision_id": row.get("revision_id"),
                                          "owner_ref": owner_ref} for row in case_drafts)
            else:
                child["actions"].append({"kind": "reprepare", "case_id": case_id,
                                         "owner_ref": owner_ref})
    knowledge = item.get("knowledge", [])
    if (not isinstance(knowledge, list) or len(knowledge) > 20 or
            not all(isinstance(row, dict) for row in knowledge)):
        child.setdefault("warnings", []).append("knowledge must be a bounded list of records")
    elif child["status"] in {"applied", "partial"}:
        for record in knowledge:
            child["actions"].append({
                "kind": "record_knowledge", "origin": {"type": "owner_event", "id": child_event_id},
                "record": copy.deepcopy(record),
            })
    note = copy.deepcopy(state["owner_events"].get(child_event_id, {}))
    note.update({**copy.deepcopy(child), "kind": "feedback_answer_note", "event_id": child_event_id,
                 "input_hash": _digest(item), "provenance_approved": True})
    note.setdefault("at", child["created_at"])
    note["source_case_id"] = child["case_ids"][0] if len(child["case_ids"]) == 1 else None
    state["owner_events"][child_event_id] = note
    return child


def _statuses(batch: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for child in batch["answers"].values():
        counts[child["status"]] = counts.get(child["status"], 0) + 1
    return counts


def _record_attempt(batch: dict[str, Any], answer_ids: list[str], at: str) -> None:
    count = batch.get("attempt_count", 0) + 1
    batch["attempt_count"] = count
    history = list(batch.get("attempt_history", []))
    history.append({"attempt": count, "at": at, "answer_ids": answer_ids,
                    "statuses": copy.deepcopy(batch["statuses"])})
    omitted = max(0, len(history) - 20)
    if omitted:
        batch["attempts_omitted"] = batch.get("attempts_omitted", 0) + omitted
        history = history[-20:]
    batch["attempt_history"] = history


def _answer_batch(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    _stores(state)
    if "resume" in payload and type(payload["resume"]) is not bool:
        raise ValueError("resume must be a boolean")
    resume = payload.get("resume", False)
    approval = engine.owned(payload)
    parent_event_id = _text(payload.get("event_id"), "authenticated host event id", 2000)
    answers = payload.get("answers")
    if (not isinstance(answers, list) or not answers or len(answers) > 100 or
            not all(isinstance(row, dict) for row in answers)):
        raise ValueError("answers must be a nonempty bounded list")
    report_id = payload.get("report_id")
    if report_id is not None and report_id not in state["report_snapshots"]:
        raise ValueError("unknown report reference")
    if report_id is not None:
        _validate_report(state["report_snapshots"][report_id])
    canonical = {"event_id": parent_event_id, "approval_ref": approval, "report_id": report_id,
                 "answers": answers, "mailbox": state.get("mailbox")}
    input_hash = _digest(canonical)
    batch_id = payload.get("batch_id") or "feedback-" + _digest(
        {"mailbox": state.get("mailbox"), "event_id": parent_event_id})[:20]
    _text(batch_id, "batch_id", 200)
    prior = state["feedback_batches"].get(batch_id)
    if prior:
        if prior.get("input_hash") != input_hash:
            raise ValueError("owner event id reused with different answer mapping")
        if not resume:
            return {**copy.deepcopy(prior), "idempotent": True}
        request = prior.get("request", canonical)
        if request != canonical or _digest(request) != input_hash:
            raise ValueError("saved feedback request does not match immutable owner input")
        parent_note = state["owner_events"].get(parent_event_id)
        if (not parent_note or parent_note.get("kind") != "feedback_batch" or
                parent_note.get("batch_id") != batch_id or parent_note.get("input_hash") != input_hash or
                parent_note.get("approval_ref") != approval or parent_note.get("owner_confirmed") is not True):
            raise ValueError("saved owner provenance is missing or invalid")
        batch = copy.deepcopy(prior)
        retryable = {"stale", "blocked", "ambiguous"}
        retry_ids = [answer_id for answer_id, child in batch["answers"].items()
                     if child.get("status") in retryable]
        if not retry_ids:
            return {**copy.deepcopy(prior), "idempotent": True}
        if "attempt_count" not in batch:
            # Batches written before resumable feedback already made one attempt.
            batch["attempt_count"] = 1
            batch["attempt_history"] = [{
                "attempt": 1, "at": batch["created_at"],
                "answer_ids": list(batch["answers"]),
                "statuses": copy.deepcopy(batch.get("statuses", _statuses(batch))),
            }]
        items: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, item in enumerate(request["answers"]):
            answer_id = item.get("answer_id") or _child_id(parent_event_id, item)
            if answer_id in items:
                raise ValueError("duplicate answer_id in saved batch")
            items[answer_id] = (index, item)
        for answer_id in retry_ids:
            if answer_id not in items:
                raise ValueError("saved feedback child is missing its immutable owner input")
            previous = batch["answers"][answer_id]
            child_note = state["owner_events"].get(previous.get("owner_event_id"))
            if (not child_note or child_note.get("owner_ref") != previous.get("owner_ref") or
                    child_note.get("answer_id") != answer_id or
                    child_note.get("parent_event_id") != parent_event_id or
                    child_note.get("approval_ref") != approval or
                    child_note.get("owner_confirmed") is not True):
                raise ValueError("saved answer provenance is missing or invalid")
            index, item = items[answer_id]
            batch["answers"][answer_id] = _answer_child(state, batch, item, index, previous)
        attempted_at = engine.stamp()
        batch["statuses"] = _statuses(batch)
        batch["last_attempt_at"] = attempted_at
        _record_attempt(batch, retry_ids, attempted_at)
        state["feedback_batches"][batch_id] = batch
        return {**copy.deepcopy(batch), "idempotent": False, "resumed": True,
                "retried_answer_ids": retry_ids}
    if resume:
        raise ValueError("cannot resume an unknown feedback batch")

    existing_parent = state["owner_events"].get(parent_event_id)
    if existing_parent and (existing_parent.get("kind") != "feedback_batch" or
                            existing_parent.get("input_hash") != input_hash):
        raise ValueError("owner event id already identifies a different owner action")
    batch = {"batch_id": batch_id, "kind": "feedback_batch", "parent_event_id": parent_event_id,
             "report_id": report_id, "mailbox": state.get("mailbox"), "input_hash": input_hash,
             "request": copy.deepcopy(canonical), "approval_ref": approval, "owner_confirmed": True,
             "created_at": engine.stamp(), "answers": {}}
    state["feedback_batches"][batch_id] = batch
    state["owner_events"][parent_event_id] = {
        "kind": "feedback_batch", "event_id": parent_event_id, "batch_id": batch_id,
        "input_hash": input_hash, "approval_ref": approval, "owner_confirmed": True,
        "at": batch["created_at"],
    }
    seen_ids = set()
    for index, item in enumerate(answers):
        answer_id = item.get("answer_id") or _child_id(parent_event_id, item)
        _text(answer_id, "answer_id", 200)
        if answer_id in seen_ids:
            raise ValueError("duplicate answer_id in batch")
        seen_ids.add(answer_id)
        batch["answers"][answer_id] = _answer_child(state, batch, item, index)
    batch.update(status="complete", statuses=_statuses(batch), completed_at=engine.stamp())
    _record_attempt(batch, list(batch["answers"]), batch["created_at"])
    state["owner_events"][parent_event_id]["status"] = "complete"
    state["owner_events"][parent_event_id]["answer_ids"] = list(batch["answers"])
    return {**copy.deepcopy(batch), "idempotent": False}


def execute(home: Any, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute the feedback facade against the real engine transaction."""
    if command != "answers":
        raise ValueError("unknown feedback command")
    value = payload or {}
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    engine = _engine()
    with engine.transaction(home) as (_, box):
        if box[0] is None:
            raise ValueError("inbox engine not initialized")
        return _answer_batch(box[0], value)
