#!/usr/bin/env python3
"""Compact discoverable input contracts for Inbox Manager commands.

This module is intentionally data-only.  It lets the host inspect one command
without loading implementation modules or the full durable state.
"""

from __future__ import annotations

import copy
from typing import Any


AUTH = {"run_id": "string", "token": "string"}
OWNER_AUTH = {"owner_confirmed": {"const": True}, "approval_ref": "string"}
TIMESTAMP = {"type": "string", "format": "ISO-8601 with timezone"}
PACKET = {
    "type": "object",
    "required": ["complete", "mailbox", "thread_id", "trigger_id", "observed_at", "automated", "messages", "drafts"],
    "optional": ["thread_observed_at", "drafts_observed_at", "draft_inventory"],
    "fields": {
        "complete": {"const": True}, "observed_at": TIMESTAMP,
        "thread_observed_at": TIMESTAMP, "drafts_observed_at": TIMESTAMP,
        "automated": "boolean", "messages": {"type": "array", "min": 1},
        "drafts": "array", "mailbox": "string", "thread_id": "string", "trigger_id": "string",
    },
    "rules": [
        "messages and drafts are complete decoded provider reads for one thread",
        "each item has id, thread_id, from, to[], cc[], subject, body, headers{}; drafts also have bcc[]",
        "trigger_id identifies one message; IDs are unique",
    ],
}
PACKET_OR_REF = {"one_of": [["packet"], ["packet_ref"]], "fields": {"packet": PACKET, "packet_ref": "string"}}
KNOWLEDGE_DEPENDENCIES = {
    "type": "array", "max": 100,
    "items": {"exact": ["note_id", "version", "updated_at"],
              "fields": {"note_id": {"type": "string", "max": 500},
                         "version": {"type": "integer", "min": 1}, "updated_at": TIMESTAMP}},
    "rules": ["note_id values are unique"],
}
RESPONSIBILITY = {"enum": ["owner", "bot", "correspondent"]}
STATES = {"enum": ["needs_action", "waiting_correspondent", "waiting_owner", "handled"]}
REVIEW = {
    "type": "object",
    "required": ["candidate_id", "candidate_hash", "reviewer_id", "independent", "decision", "assessments", "reason", "evidence_ref"],
    "fields": {
        "independent": {"const": True}, "decision": {"enum": ["draft", "send", "reject"]},
        "assessments": {"exact": ["grounded", "answers_ask", "identity", "recipients", "privacy", "authority", "no_loop"],
                        "values": {"type": "boolean"},
                        "conditional": "For an assessed candidate with summary, also require summary:boolean."},
    },
    "rules": ["reviewer_id differs from candidate writer_id", "send requires every assessment true; draft requires grounded, identity, recipients, privacy true; revisions require decision=draft"],
}


def _contract(command: str, required: list[str], optional: list[str] | None = None,
              fields: dict[str, Any] | None = None, rules: list[str] | None = None,
              *, surface: str = "engine") -> dict[str, Any]:
    value: dict[str, Any] = {"command": command, "surface": surface, "type": "object", "required": required}
    if optional:
        value["optional"] = optional
    if fields:
        value["fields"] = fields
    if rules:
        value["rules"] = rules
    return value


def _schemas() -> dict[str, dict[str, Any]]:
    preparation_fields = {
        **AUTH, **PACKET_OR_REF["fields"], "case_id": "string",
        "disposition": {"enum": ["draft_ready", "needs_owner_answer", "no_reply", "blocked"]},
        "evidence": {"type": "string", "max": 12000},
        "sources_checked": {"type": "array", "max": 30, "items": {
            "required": ["source_id", "status", "reason"],
            "fields": {"source_id": "current_thread or registered source_id",
                       "status": {"enum": ["checked", "unavailable", "not_needed"]},
                       "reason": {"type": "string", "max": 4000}}}},
        "questions": {"type": "array", "max": 10, "items": {
            "required": ["question"], "optional": ["recommendation", "obligation_ids", "decision_id"]}},
        "obligation_ids": "array[string]", "knowledge_dependencies": KNOWLEDGE_DEPENDENCIES,
    }
    schemas = {
        "packet": _contract("packet", ["run_id", "token", "packet"], fields={**AUTH, "packet": PACKET}, surface="cycle"),
        "preparation": _contract(
            "preparation", ["run_id", "token", "case_id", "disposition", "evidence", "sources_checked"],
            ["packet", "packet_ref", "questions", "obligation_ids", "knowledge_dependencies", "no_draft_reason"], {**preparation_fields, "no_draft_reason":{"enum":["already_resolved","no_reply_needed","another_owner","decision_controls_whole_reply","blocker"]}},
            ["supply exactly one of packet or packet_ref", "draft_ready obligation_ids select only needs_action bot obligations",
             "needs_owner_answer requires a saved precise question", "Explain why no draft is appropriate; draft_ready cannot have no_draft_reason", "source_changed means read the returned local source before resubmitting preparation"], surface="cycle"),
        "answers": _contract(
            "answers", ["owner_confirmed", "approval_ref", "event_id", "answers"], ["report_id", "batch_id", "resume"],
            {**OWNER_AUTH, "event_id": {"type": "string", "max": 2000}, "report_id": "string", "batch_id": {"type": "string", "max": 200},
             "answers": {"type": "array", "min": 1, "max": 100, "items": {
                 "required": ["answer"],
                 "optional": ["answer_id", "report_id", "question_number", "decision_id", "expected_version", "case_ids", "scope", "once_rule", "kind", "case_id", "draft_id", "knowledge"],
                 "fields": {"scope": {"enum": ["once", "standing_rule", "takeover"]}, "kind": {"enum": ["draft_correction"]},
                            "knowledge": {"type": "array", "max": 20}}}}},
            ["map each normal answer by report question_number or by decision_id plus expected_version",
             "draft_correction uses scope once and explicit case_id plus draft_id"], surface="engine"),
        "revision-candidate": _contract(
            "revision-candidate", ["run_id", "token", "packet", "candidate"], fields={**AUTH, "packet": PACKET,
             "candidate": {"type": "object",
                 "required": ["candidate_id", "case_id", "draft_id", "owner_ref", "body", "subject", "recipients", "source_versions", "evidence", "writer_id"],
                 "optional": ["cc", "bcc", "knowledge_dependencies", "requested_changes"],
                 "fields": {"owner_ref": {"one_of": [
                                {"exact": ["kind", "event_id"], "kind": "owner_event"},
                                {"exact": ["kind", "batch_id", "answer_id"], "kind": "feedback_batch_answer"}]},
                            "requested_changes": {"fields": ["body", "subject", "recipients", "cc", "bcc"],
                                                  "rule": "each changed field declares exact from and to; body is a nonempty replacement list"},
                            "source_versions": "object[string,string]", "knowledge_dependencies": KNOWLEDGE_DEPENDENCIES}}},
            rules=["packet draft includes attachments and the complete replacement payload",
                   "omit requested_changes to derive conservative unique-line edits from the exact last bot revision",
                   "target-only candidates return review_artifact with the actual merged replacement; review its candidate_id/hash",
                   "legacy requested_changes remains accepted and must produce the exact target"], surface="engine"),
        "revision-prepare": _contract(
            "revision-prepare", ["run_id", "token", "candidate_id", "packet", "review"],
            fields={**AUTH, "candidate_id": "string", "packet": PACKET, "review": REVIEW},
            rules=["packet draft includes attachments and the complete replacement payload",
                   "target-only candidates recompute the merged replacement and require exact checked-content equality and unchanged messages"], surface="engine"),
        "revision-confirm": _contract(
            "revision-confirm", ["run_id", "token", "candidate_id", "result"], fields={**AUTH, "candidate_id": "string",
             "result": {"required": ["action_id", "attempt_token", "status"],
                        "optional": ["packet", "provider_receipt", "reason", "definitive_not_updated"],
                        "fields": {"status": {"enum": ["updated", "failed", "unknown"]}}}},
            rules=["updated requires provider_receipt and complete packet readback; failed requires provider_receipt, reason, and definitive_not_updated true; unknown requires reason"], surface="engine"),
        "revision-reconcile": _contract(
            "revision-reconcile", ["run_id", "token", "candidate_id", "packet", "provider_receipt"],
            fields={**AUTH, "candidate_id": "string", "packet": PACKET, "provider_receipt": {"type": "string", "max": 10000}}, surface="engine"),
        "owner-event": _contract(
            "owner-event", ["owner_confirmed", "approval_ref", "event_id", "answer"], ["case_id", "decision_id", "scope"],
            {**OWNER_AUTH, "event_id": {"type": "string", "max": 500}, "answer": {"type": "string", "max": 10000},
             "case_id": "string", "decision_id": "string", "scope": "string"}, surface="engine"),
        "owner-sent-observe": _contract(
            "owner-sent-observe", ["case_id", "message_id", "packet", "actor", "actor_evidence"],
            fields={"case_id": {"type": "string", "max": 500}, "message_id": {"type": "string", "max": 1000},
                    "packet": PACKET, "actor": {"enum": ["member", "unknown"]},
                    "actor_evidence": {"type": "object", "min_keys": 1}},
            rules=["packet is a fresh complete read whose selected message is from the owner mailbox and has provider SENT evidence",
                   "verified actor mode requires member plus verified or independent provider evidence",
                   "exclusive_editor mode permits member or unknown only when exclusive_editor_confirmed is current"], surface="engine"),
        "source": _contract(
            "source", ["source_id", "origin", "version", "status", "verified_at", "valid_until"], ["local_path", "content_hash"],
            {"status": {"enum": ["current", "unavailable", "conflicting"]}, "verified_at": TIMESTAMP, "valid_until": TIMESTAMP,
             "local_path": "string", "content_hash": "string"},
            ["origin must equal the approved source policy", "valid_until is after verified_at", "local_path requires matching content_hash"], surface="engine"),
        "case": _contract(
            "case", ["run_id", "token", "case_id", "snapshot", "unresolved_ask", "next_action", "responsible"],
            ["responsibility_type", "linked_cases", "link_evidence", "due_at", "state", "outcome", "outcome_evidence", "obligations", "source_ids", "recheck_id", "urgency", "topic", "context", "recommendation", "thread_url", "thread_url_evidence", "owner_draft_ids"],
            {**AUTH, "snapshot": {"required": ["complete", "thread_id", "trigger_id", "fingerprint", "observed_at", "draft_ids", "message_ids", "correspondents", "automated"],
                                      "fields": {"complete": {"const": True}, "observed_at": TIMESTAMP}},
             "responsibility_type": RESPONSIBILITY, "state": STATES, "due_at": TIMESTAMP, "urgency": {"enum": ["normal", "urgent"]},
             "obligations": {"type": "array", "min": 1, "max": 50, "items": {
                 "required_new": ["id", "ask", "responsible", "responsibility_type", "next_action"],
                 "optional": ["state", "due_at", "draft_review_id", "source_ids", "outcome_evidence"],
                 "fields": {"responsibility_type": RESPONSIBILITY, "state": STATES, "due_at": TIMESTAMP}}}},
            ["new cases require responsibility_type; responsible is the actor label", "handled case or obligation requires outcome evidence"], surface="engine"),
        "candidate": _contract(
            "candidate", ["run_id", "token", "candidate"], ["packet", "packet_ref"],
            fields={**AUTH, "packet": PACKET, "packet_ref": "string",
             "candidate": {"required": ["candidate_id", "case_id", "body", "subject", "recipients", "reply_message_id", "reply_headers", "source_versions", "evidence", "mode", "kind", "writer_id"],
                           "optional": ["cc", "bcc", "attachments", "knowledge_dependencies", "rule_id", "obligation_ids"],
                           "fields": {"mode": {"enum": ["send", "draft"]}, "kind": {"enum": ["reply", "followup", "backlog"]},
                                      "knowledge_dependencies": KNOWLEDGE_DEPENDENCIES,
                                      "attachments": {"type": "array", "max": 10, "items": {"required": ["id", "sha256"]}}}}},
            rules=["supply exactly one of packet or packet_ref", "reply_message_id equals the packet trigger; bcc must be empty"], surface="cycle"),
    }

    schemas["prepare"] = _contract(
        "prepare", ["run_id", "token", "candidate_id", "mode", "review"], ["packet", "packet_ref"],
        fields={**AUTH, "candidate_id": "string", "mode": {"enum": ["draft", "send"]}, "packet": PACKET, "packet_ref": "string",
                "review": {"required": ["candidate_id", "candidate_hash", "reviewer_id", "independent", "decision", "assessments", "reason", "evidence_ref"],
                           "fields": {"independent": {"const": True}, "decision": {"enum": ["draft", "send"]},
                                      "assessments": {"exact": ["grounded", "answers_ask", "identity", "recipients", "privacy", "authority", "no_loop"],
                                                      "conditional": "Assessed candidates with summary additionally require summary:boolean.",
                                                      "values": "boolean"}}}},
        rules=["supply exactly one of packet or packet_ref", "review decision equals mode; writer and reviewer differ"], surface="cycle")
    schemas["confirm"] = _contract(
        "confirm", ["run_id", "token", "candidate_id", "mode", "result"], fields={**AUTH,
         "candidate_id": "string", "mode": {"enum": ["draft", "send"]},
         "result": {"fields": {"status": {"draft": ["created", "failed", "unknown"],
                                            "send": ["accepted", "sent", "failed", "unknown"]},
                               "draft": "complete provider draft readback", "provider_receipt": "string",
                               "reason": "string", "definitive_not_created": "boolean",
                               "action_id": "string", "send_token": "string", "message_id": "string",
                               "mailbox": "string", "thread_id": "string", "candidate_hash": "string",
                               "read_back": "boolean", "definitive_not_sent": "boolean"}}},
        rules=["created draft requires draft and provider_receipt; failed draft requires reason, provider_receipt and definitive_not_created true",
               "send result binds action_id and send_token; accepted/sent also require message_id, provider_receipt, mailbox, thread_id and candidate_hash; sent requires read_back true",
               "failed send requires reason and definitive_not_sent true; unknown outcomes reconcile before retry"], surface="cycle")

    schemas["reconcile"] = _contract(
        "reconcile", ["run_id", "token", "case", "evidence"], ["packet", "packet_ref"],
        {**AUTH, **PACKET_OR_REF["fields"], "case": {"allowed": sorted([
            "case_id", "unresolved_ask", "next_action", "responsible", "linked_cases", "link_evidence", "due_at", "state", "outcome", "outcome_evidence", "obligations", "source_ids", "recheck_id", "urgency", "topic", "context", "recommendation", "thread_url", "thread_url_evidence", "responsibility_type", "owner_draft_ids"]),
             "fields": {"responsibility_type": RESPONSIBILITY, "state": STATES}},
         "evidence": {"allowed": ["case_basis", "message_ids", "reason", "resolved_decision_ids", "related_outcomes"],
                      "required": ["case_basis", "message_ids", "reason"],
                      "fields": {"related_outcomes": {"type":"array", "max":5,
                          "items":{"required":["case_id","packet","message_ids","reason"]},
                          "rule":"Fresh complete packet matching an evidenced handled linked case; retires fulfilled obligations only, never grants owner approval."}}}},
        ["supply exactly one of packet or packet_ref"], surface="cycle")
    schemas["revision-retire"] = _contract("revision-retire", ["run_id","token","candidate_id","reason"], rules=["Only a never-reserved ready candidate can retire; attempting, unknown, confirmed or reserved revisions are immutable."])
    schemas["knowledge-reviewed"] = _contract("knowledge-reviewed", ["run_id","token","observation_id","reason"], rules=["Use after assessing an eligible learning observation with no reusable knowledge; does not send or change mail."])
    schemas["knowledge-record"] = _contract(
        "knowledge-record", ["origin", "kind", "summary"],
        ["note_id", "topic", "scope", "aliases", "case_id", "one_off", "fresh_until", "details", "status", "version", "created_at", "updated_at", "supersedes", "contradicts", "evidence_metadata"],
        {"origin": {"required": ["type", "id"], "fields": {"type": {"enum": ["owner_event", "provider_observation"]}}},
         "kind": {"enum": ["fact", "procedure", "style", "example", "case_note", "business_rule"]},
         "summary": {"type": "string", "max": 4000}, "scope": {"type": "object", "max_keys": 20, "max_json_chars": 8000},
         "aliases": {"type": "array", "max": 30, "item_max_chars": 300}, "fresh_until": TIMESTAMP,
         "supersedes": {"type": "array", "max": 20}, "contradicts": {"type": "array", "max": 20}},
        ["origin must already exist in authenticated owner events or eligible provider observations", "details and evidence_metadata contain no message body fields"], surface="engine")
    schemas["knowledge-search"] = _contract(
        "knowledge-search", ["query"], ["limit", "cursor", "scope", "case_id", "include_inactive"],
        {"query": {"type": "string", "max": 2000}, "limit": {"type": "integer", "default": 10, "max": 100},
         "cursor": {"type": "integer", "min": 0, "max": 1000000}, "scope": {"type": "object", "max_keys": 20}}, surface="engine")
    schemas["knowledge-lookup"] = _contract(
        "knowledge-lookup", [], ["note_id", "evidence_id", "limit", "cursor", "include_body", "export"],
        {"cursor": {"type": "integer", "min": 0, "max": 1000000},
         "include_body": "boolean", "export": {"const": True},
         "limit": {"type": "integer", "normal_default": 10, "normal_max": 100,
                   "export_default": 100, "export_max": 1000}},
        ["normal lookup selects exactly one of note_id or evidence_id", "export true returns a body-free notes page and needs neither selector"], surface="engine")
    schemas["knowledge-forget"] = _contract(
        "knowledge-forget", ["owner_event"], ["action", "note_id", "note_ids", "evidence_id", "case_id"],
        {"owner_event": {"required": ["type", "id"], "fields": {"type": {"enum": ["owner_event", "provider_observation"]}}},
         "action": {"enum": ["forget", "pause", "resume", "exclude_evidence", "exclude_case"], "default": "forget"},
         "note_ids": {"type": "array", "min": 1, "max": 100}},
        ["forget selects note_id or note_ids; exclude_evidence requires evidence_id; exclude_case requires case_id"], surface="engine")
    schemas["knowledge-maintain"] = _contract(
        "knowledge-maintain", [], ["budget", "cursor", "now"],
        {"budget": {"type": "integer", "default": 100, "max": 1000}, "cursor": {"type": "integer", "min": 0, "max": 1000000}, "now": TIMESTAMP}, surface="engine")
    schemas["maintain"] = _contract("maintain", [], ["limit"], {"limit": {"type": "integer", "min": 1, "max": 500, "default": 100}}, surface="engine")
    schemas["storage-health"] = _contract("storage-health", ["mailbox"], surface="engine")
    schemas["storage-backup"] = _contract("storage-backup", ["mailbox", "target_path"], rules=["destination parent exists and target does not"], surface="engine")
    schemas["storage-restore"] = _contract("storage-restore", ["mailbox", "backup_path"], ["sha256"],
                                            rules=["home is new and empty; backup must be a regular file for this mailbox"], surface="engine")
    schemas["begin"] = _contract(
        "begin", ["mailbox"],
        ["run_id", "minutes", "registry", "provider_observation", "scheduler_readback", "case_ids"],
        {"mailbox": "string", "run_id": "string",
         "minutes": {"type": "number", "min": 1, "max": 90, "default": 15},
         "case_ids": {"type": "array", "min": 1, "max": 50, "items": "existing case_id", "unique": True},
         "registry": {"type": "object", "exact": ["root", "mailbox", "task_id", "binding_id"],
                      "required": ["root", "mailbox", "task_id", "binding_id"]},
         "provider_observation": {"type": "object",
             "required": ["provider", "mailbox", "account_id", "connector_ref", "card_ref", "observed_at", "evidence_ref"],
             "fields": {"observed_at": TIMESTAMP}},
         "scheduler_readback": {"type": "object",
             "required": ["task_id", "cadence", "timezone", "execution_location", "host_required", "enabled", "kind", "prompt", "observed_at", "evidence_ref"],
             "optional": ["next_run_at", "target_thread_id", "target_readback"],
             "fields": {"host_required": "boolean", "enabled": "boolean", "kind": {"enum": ["heartbeat", "cron"]},
                        "observed_at": TIMESTAMP, "next_run_at": TIMESTAMP,
                        "target_readback": {"required": ["thread_id", "archived", "observed_at", "evidence_ref"],
                                            "fields": {"archived": "boolean", "observed_at": TIMESTAMP}}}}},
        ["run_id is generated when omitted", "case_ids starts a selected-case run and cannot be combined with registry",
         "registry runs require matching provider_observation and scheduler_readback; heartbeat readback requires its bound target"],
        surface="cycle")
    schemas["next"] = _contract("next", ["run_id", "token"], fields=AUTH, surface="cycle")
    schemas["intake"] = _contract(
        "intake", ["run_id", "token"], ["query", "window_end", "pages", "threads", "lane"],
        {**AUTH, "query": {"type": "string", "max": 10000}, "window_end": TIMESTAMP, "lane": "string",
         "pages": {"type": "array", "max": 25, "items": {
             "type": "object", "exact": ["page_cursor", "complete", "next_cursor", "entries", "provider_evidence"],
             "required": ["complete", "entries", "provider_evidence"], "optional": ["page_cursor", "next_cursor"],
             "fields": {"page_cursor": "string", "complete": "boolean", "next_cursor": "string",
                        "provider_evidence": {"type": "string", "max": 10000},
                        "entries": {"type": "array", "max": 1000, "items": {
                            "required": ["trigger_id", "thread_id", "received_at"],
                            "fields": {"trigger_id": "string", "thread_id": "string", "received_at": TIMESTAMP}}}}}},
         "threads": {"type": "array", "max": 50, "items": {
             "type": "object", "exact": ["packet", "case", "candidate"],
             "required": ["packet", "case"], "optional": ["candidate"],
             "fields": {"packet": PACKET,
                        "case": {"type": "object",
                            "required": ["case_id", "unresolved_ask", "next_action", "responsible"],
                            "optional": ["responsibility_type", "linked_cases", "link_evidence", "due_at", "state", "outcome", "outcome_evidence", "obligations", "source_ids", "recheck_id", "urgency", "topic", "context", "recommendation", "thread_url", "thread_url_evidence", "owner_draft_ids"],
                            "fields": {"responsibility_type": RESPONSIBILITY, "state": STATES, "due_at": TIMESTAMP,
                                       "urgency": {"enum": ["normal", "urgent"]},
                                       "obligations": {"type": "array", "min": 1, "max": 50, "items": {
                                           "required_new": ["id", "ask", "responsible", "responsibility_type", "next_action"],
                                           "optional": ["state", "due_at", "draft_review_id", "source_ids", "outcome_evidence"]}}}},
                        "candidate": {"type": "object",
                            "required": ["candidate_id", "case_id", "body", "subject", "recipients", "reply_message_id", "reply_headers", "source_versions", "evidence", "mode", "kind", "writer_id"],
                            "optional": ["cc", "bcc", "attachments", "knowledge_dependencies", "rule_id", "obligation_ids"],
                            "fields": {"mode": {"enum": ["send", "draft"]},
                                       "kind": {"enum": ["reply", "followup", "backlog"]},
                                       "knowledge_dependencies": KNOWLEDGE_DEPENDENCIES}}}}}},
        ["query and window_end are required when pages is nonempty; selected-case runs do not accept pages",
         "an incomplete page requires an advancing next_cursor; a complete page has no next_cursor",
         "new cases and obligations require responsibility_type; an intake candidate belongs to the same case"],
        surface="cycle")
    # One normal semantic submission, with legacy shapes retained for recovery.
    from inbox_cycle import ASSESSMENT_FIELDS, CANDIDATE_FIELDS
    assessed = {
        "type": "object", "exact": sorted(ASSESSMENT_FIELDS),
        "required": ["case_id", "expected_basis", "unresolved_ask", "next_action", "responsible",
                     "disposition", "preparation_evidence", "sources_checked"],
        "fields": {
            "expected_basis": {"type": ["string", "null"], "rule": "null only for a new case"},
            "disposition": {"enum": ["draft_ready", "needs_owner_answer", "no_reply", "blocked"]},
            "sources_checked": {"type": "array", "max": 30, "items": {
                "required": ["source_id", "status", "reason"], "optional": ["version"],
                "rules": ["checked sources other than current_thread require the exact version actually read"]}},
            "candidate": {"type": "object", "exact": sorted(CANDIDATE_FIELDS - {
                "case_id", "source_versions", "knowledge_dependencies", "obligation_ids", "evidence",
                "reply_message_id", "reply_headers"}),
                "required": ["candidate_id", "body", "subject", "recipients", "mode", "kind", "writer_id"]},
            "summary": {"type": "string", "max": 2000},
            "knowledge_dependencies": KNOWLEDGE_DEPENDENCIES,
        },
        "rules": ["candidate requires summary and draft_ready; independent checker must assess summary",
                  "candidate scope, dependencies and threading are bound from this one assessment",
                  "reconcile additionally requires observed message_ids and reason",
                  "expected_basis is the current opaque case_basis from work; identical accepted replay returns its receipt"],
    }
    thread_contract = schemas['intake']['fields']['threads']['items']
    thread_contract['exact'] += ['packet_ref', 'assessment']
    thread_contract['required'] = []
    thread_contract['optional'] += ['packet', 'packet_ref', 'case', 'assessment']
    thread_contract['fields'].update(packet_ref='string', assessment=assessed)
    thread_contract['rules'] = ['choose exactly one packet or packet_ref; assessment replaces case/candidate']
    schemas['reconcile']['optional'] += ['assessment', 'case', 'evidence']
    schemas['reconcile']['required'] = ['run_id', 'token']
    schemas['reconcile']['fields']['assessment'] = assessed
    schemas['reconcile']['rules'].append('assessment replaces case/evidence; choose packet or packet_ref')
    schemas["observe"] = _contract(
        "observe", ["run_id", "token", "case_id"], ["packet", "packet_ref"],
        {**AUTH, "case_id": "string", "packet": PACKET, "packet_ref": "string"},
        ["supply exactly one of packet or packet_ref", "packet_ref reuses saved evidence and does not refresh it"],
        surface="cycle")
    schemas["cleanup"] = _contract(
        "cleanup", ["run_id", "token", "case_id"],
        ["packet", "packet_ref", "step_id", "provider_ref", "error"],
        {**AUTH, "case_id": "string", "packet": PACKET, "packet_ref": "string",
         "step_id": "opaque string returned by cleanup", "provider_ref": "string", "error": "string"},
        ["first call omits step_id and supplies exactly one of packet or packet_ref",
         "a successful step readback supplies step_id, provider_ref, and exactly one of packet or packet_ref",
         "a definite provider failure supplies step_id and error; forward returned mutations without editing them"],
        surface="cycle")
    schemas["finish"] = _contract(
        "finish", ["run_id", "token"], ["scheduler_readback"],
        {**AUTH, "scheduler_readback": {"type": "object",
             "required": ["task_id", "cadence", "timezone", "execution_location", "host_required", "enabled", "kind", "prompt", "observed_at", "evidence_ref"],
             "optional": ["next_run_at", "target_thread_id", "target_readback"],
             "fields": {"host_required": "boolean", "enabled": "boolean", "kind": {"enum": ["heartbeat", "cron"]},
                        "observed_at": TIMESTAMP, "next_run_at": TIMESTAMP,
                        "target_readback": {"required": ["thread_id", "archived", "observed_at", "evidence_ref"],
                                            "fields": {"archived": "boolean", "observed_at": TIMESTAMP}}}}},
        ["scheduler_readback is used only for the exact registry-bound run; omission leaves next-run verification unavailable"],
        surface="cycle")
    return schemas


SCHEMAS = _schemas()
ALIASES = {"backup": "storage-backup", "restore": "storage-restore"}


def schema(command: str) -> dict[str, Any]:
    """Return a detached compact input contract for one command."""

    if not isinstance(command, str) or not command.strip():
        raise ValueError("schema command must be a nonempty string")
    key = ALIASES.get(command.strip(), command.strip())
    if key not in SCHEMAS:
        raise ValueError("unknown schema command: " + command.strip())
    return copy.deepcopy(SCHEMAS[key])
