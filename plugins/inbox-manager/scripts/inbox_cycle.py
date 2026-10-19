#!/usr/bin/env python3
"""Deterministic cycle facade for Inbox Manager. Python stdlib; no network calls.

The host supplies complete observed provider packets and semantic judgments. This
module only validates, binds, and sequences those inputs through the durable
engine; it never invents a decision, review, provider result, or read-back.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import sys
from typing import Any

import inbox_engine as engine
import inbox_snapshot
import inbox_registry
import inbox_priority
import inbox_reconcile
import inbox_archive
import inbox_preparation
import inbox_store


COMMANDS = {"answers", "revision-candidate", "revision-prepare", "revision-confirm", "revision-reconcile", "revision-retire", "preparation", "packet", "observe", "begin", "intake", "candidate", "prepare", "confirm", "finish", "next", "reconcile", "cleanup"}
CASE_FIELDS = {
    "case_id", "unresolved_ask", "next_action", "responsible", "linked_cases",
    "link_evidence", "due_at", "state", "outcome", "outcome_evidence",
    "obligations", "source_ids", "recheck_id", "urgency", "topic", "context",
    "recommendation", "thread_url", "thread_url_evidence", "responsibility_type", "owner_draft_ids",
}
CANDIDATE_FIELDS = {
    "candidate_id", "case_id", "body", "subject", "recipients", "cc", "bcc",
    "attachments", "reply_message_id", "reply_headers", "source_versions", "knowledge_dependencies",
    "evidence", "rule_id", "mode", "kind", "writer_id", "obligation_ids",
}
REVIEW_FIELDS = {
    "candidate_id", "candidate_hash", "reviewer_id", "independent", "decision",
    "assessments", "reason", "evidence_ref",
}
DRAFT_READBACK_FIELDS = {
    "id", "from", "thread_id", "subject", "body", "to", "cc", "bcc", "headers",
}
OPERATIONAL_HOLDS = {"source_unavailable", "connector_unavailable", "draft_failed", "send_failed"}
SCAN_PAGE_FIELDS = {"page_cursor", "complete", "next_cursor", "entries", "provider_evidence"}
MAX_INTAKE_PAGES = 25
MAX_INTAKE_THREADS = 50
PAYLOAD_FIELDS = {
    "packet": {"run_id", "token", "packet"},
    "observe": {"run_id", "token", "packet", "case_id"},
    "preparation": {"run_id", "token", "case_id", "packet", "disposition", "evidence", "sources_checked", "questions", "obligation_ids", "knowledge_dependencies", "no_draft_reason"},
    "begin": {"mailbox", "run_id", "minutes", "registry", "provider_observation", "scheduler_readback", "case_ids"},
    "next": {"run_id", "token"},
    "reconcile": {"run_id", "token", "packet", "case", "evidence", "assessment"},
    "cleanup": {"run_id", "token", "case_id", "packet", "step_id", "provider_ref", "error"},
    "intake": {"run_id", "token", "query", "window_end", "pages", "threads", "lane"},
    "candidate": {"run_id", "token", "packet", "candidate"},
    "prepare": {"run_id", "token", "candidate_id", "mode", "packet", "review"},
    "confirm": {"run_id", "token", "candidate_id", "mode", "result"},
    "finish": {"run_id", "token", "scheduler_readback"},
}


class CycleError(ValueError):
    """Structured boundary error suitable for both Python and the CLI."""

    def __init__(
        self,
        message: str,
        *,
        command: str,
        error_type: str = "rejected",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.error_type = error_type
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.error_type,
            "command": self.command,
            "message": str(self),
            **copy.deepcopy(self.details),
        }


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return value


def _only(value: Any, allowed: set[str], field: str) -> dict[str, Any]:
    result = _object(value, field)
    unexpected = sorted(set(result) - allowed)
    if unexpected:
        raise ValueError(f"unsupported {field} fields: {', '.join(unexpected)}")
    return copy.deepcopy(result)


def _json_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("input must contain valid JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def _auth(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "run_id": engine.text(payload.get("run_id"), "run_id"),
        "token": engine.text(payload.get("token"), "token"),
    }


def _read_state(home: str | Path, auth: dict[str, str] | None = None) -> dict[str, Any]:
    with engine.transaction(home, readonly=True) as (_, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        if auth is not None:
            engine.lease(state, auth)
        return copy.deepcopy(state)


def _candidate_values(
    state: dict[str, Any], auth: dict[str, str], fields: Any
) -> dict[str, Any]:
    semantic = _only(fields, CANDIDATE_FIELDS, "candidate")
    value = {
        **auth,
        **semantic,
        "profile_version": state["version"],
        "authority_version": state["version"],
    }
    return value


def _save_candidate(
    home: str | Path,
    auth: dict[str, str],
    packet: Any,
    fields: Any,
) -> dict[str, Any]:
    provider_packet = _object(packet, "packet")
    snapshot = inbox_snapshot.build_snapshot(provider_packet)
    with engine.transaction(home) as (_, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        engine.lease(state, auth)
        if engine.text(provider_packet.get("mailbox"), "packet.mailbox").lower() != state["mailbox"]:
            raise ValueError("provider packet mailbox does not match this inbox")
        if provider_packet.get("draft_inventory"):
            from inbox_retrieval import verify_packet_inventory
            verify_packet_inventory(state, provider_packet)
        semantic = _only(fields, CANDIDATE_FIELDS, "candidate")
        case_id = engine.text(semantic.get("case_id"), "candidate.case_id")
        _selected_case(state, auth, case_id)
        case = state["cases"].get(case_id)
        if not case:
            raise ValueError("unknown case")
        if case["thread_id"] != snapshot["thread_id"] or case["snapshot"]["fingerprint"] != snapshot["fingerprint"]:
            raise ValueError("provider packet does not match the durable case")
        if case["snapshot"]["trigger_id"] != snapshot["trigger_id"]:
            raise ValueError("provider packet trigger does not match the durable case")
        saved = engine.candidate(state, _candidate_values(state, auth, semantic))
        return copy.deepcopy(saved)


def _review_artifact(saved: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": saved["id"],
        "candidate_hash": saved["hash"],
        "candidate": copy.deepcopy(saved),
    }


def _provider_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "mailbox": candidate["mailbox"],
        "thread_id": candidate["thread_id"],
        "reply_message_id": candidate["reply_message_id"],
        "subject": candidate["subject"],
        "body": candidate["body"],
        "to": copy.deepcopy(candidate["recipients"]),
        "cc": copy.deepcopy(candidate["cc"]),
        "bcc": copy.deepcopy(candidate["bcc"]),
        "attachments": copy.deepcopy(candidate["attachments"]),
        "reply_headers": copy.deepcopy(candidate["reply_headers"]),
    }


def _begin(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    observed_mailbox = engine.addresses([payload.get("mailbox")])[0]
    state = _read_state(home)
    if observed_mailbox != state["mailbox"]:
        raise ValueError("observed mailbox does not match the configured inbox")
    if not state["active"] or not state.get("config"):
        raise ValueError("approved active inbox setup required")
    if state["paused"]:
        raise ValueError("inbox paused")
    if state["config"].get("chat_output", "full") == "profile":
        engine.validate_profile(Path(home).resolve(), state["config"]["profile_files"])
    selected = payload.get("case_ids")
    if selected is not None:
        if (not isinstance(selected, list) or not selected or len(selected) > 50 or
                any(not isinstance(key, str) or key not in state["cases"] for key in selected) or
                len(selected) != len(set(selected))):
            raise ValueError("case_ids must select 1-50 distinct existing cases")
        if payload.get("registry") is not None:
            raise ValueError("scheduled runs require full approved discovery")
        for key in selected:
            inbox_priority.require_scope(state, state["cases"][key])
    run_id = payload.get("run_id") or ("cycle-" + secrets.token_hex(12))
    claim_payload: dict[str, Any] = {"run_id": run_id}
    if "minutes" in payload:
        claim_payload["minutes"] = payload["minutes"]
    binding = None
    if payload.get("registry") is not None:
        binding = _only(payload["registry"], {"root", "mailbox", "task_id", "binding_id"}, "registry")
        binding["root"] = str(Path(engine.text(binding.get("root"), "registry.root")).resolve())
        resolved_binding = inbox_registry.execute(binding["root"], "resolve", {"mailbox": binding.get("mailbox")})
        if Path(resolved_binding["home"]).resolve() != Path(home).resolve() or resolved_binding["mailbox"] != observed_mailbox:
            raise ValueError("scheduled binding does not match cycle home and mailbox")
        binding["mailbox"] = resolved_binding["mailbox"]
        minutes = engine.number(payload.get("minutes", 15), 1, 90, "lease minutes")
        inbox_registry.execute(binding["root"], "dispatch", {
            **binding, "run_id": run_id, "lease_seconds": int(minutes * 60),
            "provider_observation": payload.get("provider_observation"),
            "scheduler_readback": payload.get("scheduler_readback"),
        })
    elif "provider_observation" in payload or "scheduler_readback" in payload:
        raise ValueError("scheduled observations require an exact registry binding")
    try:
        lease = engine.execute(home, "claim", claim_payload)
    except BaseException:
        if binding:
            inbox_registry.execute(binding["root"], "complete", {
                **binding, "run_id": run_id, "result": "skipped",
                "evidence_ref": "cycle-claim-rejected:" + str(run_id),
            })
        raise
    auth = {"run_id": lease["run_id"], "token": lease["token"]}
    try:
        with engine.transaction(home) as (_, box):
            state = box[0]
            if state is None:
                raise ValueError("inbox engine not initialized")
            engine.lease(state, auth)
            state["runs"][run_id]["registry"] = copy.deepcopy(binding)
            state["runs"][run_id]["auth_hash"] = _json_digest(auth)
            config = state["config"]
            sources = []
            for source_id, policy in sorted(config["source_policy"].items()):
                observed = state["sources"].get(source_id, {})
                sources.append({
                    "source_id": source_id,
                    "origin": policy["origin"],
                    "scope": policy["scope"],
                    "use": policy["use"],
                    **{key: copy.deepcopy(observed[key]) for key in (
                        "version", "status", "verified_at", "valid_until", "local_path", "content_hash"
                    ) if key in observed},
                })
            cutoff = engine.utcnow().replace(microsecond=0).isoformat()
            discovery = inbox_priority.discovery(state, cutoff) if selected is None else []
            state["runs"][run_id]["case_ids"] = selected
            state["runs"][run_id]["reconcile_required"] = [row["case_id"] for row in inbox_reconcile.work(state, selected)]
            state["runs"][run_id]["discovery"] = copy.deepcopy(discovery)
            work = _next_work(state, auth)
        return {
            **auth,
            "expires_at": lease["expires_at"],
            "cutoff": cutoff,
            "discovery": discovery,
            "work": work,
            "profile": {
                "version": state["version"],
                "job": config["job"],
                "files": copy.deepcopy(config["profile_files"]),
            },
            "sources": sources,
        }
    except BaseException:
        try:
            engine.execute(home, "release", auth)
        except BaseException:
            pass
        raise


ASSESSMENT_FIELDS = CASE_FIELDS | {
    "expected_basis", "disposition", "preparation_evidence", "sources_checked",
    "questions", "obligation_ids", "knowledge_dependencies", "no_draft_reason",
    "candidate", "summary", "message_ids", "reason", "resolved_decision_ids", "related_outcomes",
}


def _assessed_case(root, state, auth, packet, raw, *, reconciliation=False):
    """Stage one authored judgment; caller commits only after complete admission."""
    assessment = _only(raw, ASSESSMENT_FIELDS, "assessment")
    fields = {k: v for k, v in assessment.items() if k in CASE_FIELDS}
    case_id = engine.text(fields.get("case_id"), "assessment.case_id")
    _selected_case(state, auth, case_id)
    packet = inbox_snapshot.normalize_packet(_object(packet, "packet"))
    packet_hash = _json_digest(packet)
    packet_ref = 'packet-' + engine.digest(packet)
    input_hash = _json_digest({"packet_hash": packet_hash, "assessment": assessment,
                               "reconciliation": reconciliation})
    prior = state.get('evidence_packets', {}).get(packet_ref, {}).get('assessments', {}).get(input_hash)
    current = state['cases'].get(case_id)
    if prior:
        if not current or prior['result_basis'] != inbox_reconcile.basis(state, current):
            raise ValueError('accepted assessment changed; reload current work before replay')
        result = copy.deepcopy(prior['result'])
        if prior.get('candidate_id'):
            candidate = state['candidates'][prior['candidate_id']]
            if not inbox_preparation._dependencies_current(root, state, candidate['source_versions'], candidate.get('knowledge_dependencies', [])):
                raise ValueError('accepted assessment evidence changed; reload current work')
            result['candidate'] = _review_artifact(candidate)
            result['checker_evidence'] = {'packet': copy.deepcopy(packet), **candidate['assessed_evidence']}
        return {**result, 'idempotent': True}
    expected = assessment.get('expected_basis')
    if 'expected_basis' not in assessment or expected != (inbox_reconcile.basis(state, current) if current else None):
        raise ValueError('assessment basis changed; reload current work')
    engine.validate_profile(root, state['config']['profile_files'])
    cached = inbox_preparation.save_packet(state, auth, packet)
    snap = inbox_snapshot.build_snapshot(packet)
    if reconciliation:
        evidence = {k: assessment[k] for k in ('message_ids', 'reason', 'resolved_decision_ids', 'related_outcomes') if k in assessment}
        evidence['case_basis'] = expected
        inbox_reconcile.assess(state, auth, packet, fields, evidence, root=root)
        # Reconciliation returns a detached public value. Bind subsequent
        # preparation/version/receipt updates to the actual durable case.
        saved = state['cases'][case_id]
    else:
        saved = engine.update_case(state, {**auth, **fields, 'snapshot': snap})
        if packet.get('draft_inventory'):
            from inbox_drafts import reconcile_presence
            reconcile_presence(state, saved, packet)
    # Claim immutable source versions once. A registry's latest version is never
    # evidence that the writer read those bytes; reject before local TTL refresh.
    checked = assessment.get('sources_checked')
    if not isinstance(checked, list):
        raise ValueError('assessment sources_checked required')
    versions = {}
    for row in checked:
        if not isinstance(row, dict):
            raise ValueError('source check must be an object')
        if row.get('status') == 'checked' and row.get('source_id') != 'current_thread':
            key = row.get('source_id')
            source = state['sources'].get(key)
            if not source or row.get('version') != source.get('version'):
                raise ValueError('checked source version changed; read current evidence')
            versions[key] = row['version']
    dependencies = assessment.get('knowledge_dependencies', [])
    if not inbox_preparation._dependencies_current(root, state, versions, dependencies):
        raise ValueError('assessment source or knowledge evidence is not current')
    prep = {**auth, 'case_id': case_id, 'packet': packet,
            'evidence': assessment.get('preparation_evidence'),
            **{k: assessment[k] for k in ('disposition', 'sources_checked', 'questions', 'obligation_ids',
                'knowledge_dependencies', 'no_draft_reason') if k in assessment}}
    prepared = inbox_preparation.assess(state, auth, prep)
    if prepared['disposition'] == 'source_changed':
        raise ValueError('assessment source bytes changed; read current evidence')
    immutable_evidence = {'packet_ref': cached['packet_ref'], 'packet_hash': packet_hash,
                          'input_hash': input_hash, 'expected_basis': expected,
                          'profile_version': state['version'],
                          'sources': {k: copy.deepcopy(state['sources'][k]) for k in versions},
                          'preparation': copy.deepcopy(state['preparations'][case_id]),
                          'business': copy.deepcopy(fields),
                          'questions': copy.deepcopy(assessment.get('questions', [])),
                          'decisions': [copy.deepcopy(state['decisions'][key]) for key in prepared['decision_ids']]}
    row = {'case': engine.summary_case(state, saved), 'packet_ref': cached['packet_ref'],
           'input_hash': input_hash, 'preparation': prepared, 'idempotent': False}
    if assessment.get('candidate') is not None:
        if assessment.get('disposition') != 'draft_ready':
            raise ValueError('candidate requires draft_ready disposition')
        duplicate = {'case_id', 'source_versions', 'knowledge_dependencies', 'obligation_ids', 'evidence',
                     'reply_message_id', 'reply_headers'}
        fields = _only(assessment['candidate'], CANDIDATE_FIELDS - duplicate, 'assessment.candidate')
        trigger = next(m for m in packet['messages'] if m['id'] == snap['trigger_id'])
        message_id = trigger.get('headers', {}).get('message-id')
        if not message_id:
            raise ValueError('candidate needs an unambiguous observed Message-ID')
        references = trigger.get('headers', {}).get('References') or ''
        references = (references + ' ' + message_id).strip()
        values = _candidate_values(state, auth, {**fields, 'case_id': case_id,
            'source_versions': versions, 'knowledge_dependencies': dependencies,
            'obligation_ids': assessment.get('obligation_ids'),
            'evidence': assessment.get('preparation_evidence'), 'reply_message_id': snap['trigger_id'],
            'reply_headers': {'In-Reply-To': message_id, 'References': references}})
        values['summary'] = engine.text(assessment.get('summary'), 'draft summary', 2000)
        values['assessed_evidence'] = immutable_evidence
        candidate = engine.candidate(state, values)
        row['candidate'] = _review_artifact(candidate)
        # Complete raw provider evidence remains visible to the independent checker.
        row['checker_evidence'] = {'packet': copy.deepcopy(packet), **immutable_evidence}
    elif 'summary' in assessment:
        raise ValueError('draft summary requires a candidate')
    from inbox_preparation import reconcile_summaries
    reconcile_summaries(state, saved, packet)
    saved['assessment_version'] = saved.get('assessment_version', 0) + 1
    result_basis = inbox_reconcile.basis(state, saved)
    state['runs'][auth['run_id']].setdefault('reconciled_cases', {})[case_id] = result_basis
    state['evidence_packets'][packet_ref].setdefault('assessments', {})[input_hash] = {
        'result_basis': result_basis,
        'candidate_id': row.get('candidate', {}).get('candidate_id'),
        'result': {k: copy.deepcopy(v) for k, v in row.items() if k not in {'candidate', 'checker_evidence'}}}
    return row


def _intake(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    auth = _auth(payload)
    pages = _list(payload.get("pages", []), "pages")
    threads = _list(payload.get("threads", []), "threads")
    if len(pages) > MAX_INTAKE_PAGES or len(threads) > MAX_INTAKE_THREADS:
        raise ValueError(
            f"one intake accepts at most {MAX_INTAKE_PAGES} pages and {MAX_INTAKE_THREADS} threads"
        )
    scans: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    identities: dict[tuple[str, str], str] = {}
    admitted: set[tuple[str, str]] = set()
    with engine.transaction(home) as (root, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        engine.lease(state, auth)

        selected = state["runs"][auth["run_id"]].get("case_ids")
        if selected is not None and pages:
            raise ValueError("selected-case run does not perform discovery")
        if pages:
            query = engine.text(payload.get("query"), "query", 10000)
            window_end = engine.text(payload.get("window_end"), "window_end")
            for index, raw_page in enumerate(pages):
                try:
                    page = _only(raw_page, SCAN_PAGE_FIELDS, f"pages[{index}]")
                    staged = copy.deepcopy(state)
                    result = engine.scan(staged, {
                        **page, **auth, "query": query, "window_end": window_end,
                        **({"lane": payload["lane"]} if "lane" in payload else {}),
                    })
                    state = staged
                    box[0] = state
                    record = result["scan"]
                    scans.append({
                        "index": index,
                        **({"lane": record["lane"]} if record.get("lane") is not None else {}),
                        "complete": record["complete"],
                        "next_cursor": record.get("next_cursor"),
                        "queued": result["queued"],
                        "watermark": result["watermark"],
                    })
                except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                    errors.append({"scope": "scan", "index": index, "type": "rejected", "message": str(exc)})
                    break

        for index, raw_item in enumerate(threads):
            thread_id = None
            trigger_id = None
            try:
                item = _only(raw_item, {"packet", "packet_ref", "case", "candidate", "assessment"}, f"threads[{index}]")
                if 'packet_ref' in item:
                    if 'packet' in item:
                        raise ValueError('choose packet or packet_ref')
                    item['packet'] = inbox_preparation.resolve_packet(state, item.pop('packet_ref'))
                if 'assessment' in item:
                    snap = inbox_snapshot.build_snapshot(item.get('packet'))
                    thread_id, trigger_id = snap['thread_id'], snap['trigger_id']
                    if 'case' in item or 'candidate' in item:
                        raise ValueError('assessment replaces separately authored case/candidate payloads')
                    staged = copy.deepcopy(state)
                    row = _assessed_case(root, staged, auth, item.get('packet'), item['assessment'])
                    state = staged
                    box[0] = state
                    results.append({'index': index, 'case_id': item['assessment'].get('case_id'), **row})
                    continue
                provider_packet = inbox_snapshot.normalize_packet(_object(item.get("packet"), "packet"))
                item = {**item, "packet": provider_packet}
                semantic_case = _only(item.get("case"), CASE_FIELDS, "case")
                snapshot = inbox_snapshot.build_snapshot(provider_packet)
                thread_id, trigger_id = snapshot["thread_id"], snapshot["trigger_id"]
                case_id = engine.text(semantic_case.get("case_id"), "case.case_id")
                if selected is not None and case_id not in selected:
                    raise ValueError("case outside selected run")
                identity = _json_digest(item)
                for key in (("case", case_id), ("trigger", trigger_id)):
                    previous = identities.get(key)
                    if previous is not None and previous != identity:
                        raise ValueError(f"duplicate {key[0]} id has changed contents")
                    identities[key] = identity
                if (case_id, identity) in admitted:
                    results.append({"index": index, "case_id": case_id, "thread_id": thread_id,
                                    "trigger_id": trigger_id, "duplicate": True, "input_hash": identity})
                    continue
                staged = copy.deepcopy(state)
                if engine.text(provider_packet.get("mailbox"), "packet.mailbox").lower() != staged["mailbox"]:
                    raise ValueError("provider packet mailbox does not match this inbox")
                saved_case = engine.update_case(staged, {**auth, **semantic_case, "snapshot": snapshot})
                if provider_packet.get("draft_inventory"):
                    from inbox_drafts import reconcile_presence
                    reconcile_presence(staged, saved_case, provider_packet)
                inbox_preparation.reconcile_summaries(staged, saved_case, provider_packet)
                row: dict[str, Any] = {
                    "index": index,
                    "case_id": saved_case["id"],
                    "thread_id": thread_id,
                    "trigger_id": trigger_id,
                    "case": {
                        **engine.summary_case(staged, saved_case),
                        "obligations": copy.deepcopy(saved_case.get("obligations", {})),
                    },
                    "input_hash": identity,
                }
                if item.get("candidate") is not None:
                    candidate_case_id = engine.text(
                        _object(item["candidate"], "candidate").get("case_id"), "candidate.case_id"
                    )
                    if candidate_case_id != case_id:
                        raise ValueError("intake candidate must belong to the admitted case")
                    saved = engine.candidate(staged, _candidate_values(staged, auth, item["candidate"]))
                    row["candidate"] = _review_artifact(saved)
                if engine.fresh(snapshot["observed_at"], staged["config"]["transport"]["snapshot_max_age_seconds"]):
                    staged["runs"][auth["run_id"]].setdefault("reconciled_cases", {})[case_id] = inbox_reconcile.basis(staged, saved_case)
                state = staged
                box[0] = state
                admitted.add((case_id, identity))
                results.append(row)
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, inbox_snapshot.SnapshotError) as exc:
                errors.append({
                    "scope": "thread",
                    "index": index,
                    "thread_id": thread_id,
                    "trigger_id": trigger_id,
                    "type": "invalid_provider_packet" if isinstance(exc, inbox_snapshot.SnapshotError) else "rejected",
                    "message": str(exc),
                })
    return {"scans": scans, "results": results, "errors": errors}



def _selected_case(state, auth, case_id):
    selected = state["runs"][auth["run_id"]].get("case_ids")
    if selected is not None and case_id not in selected:
        raise ValueError("case outside selected run")


def _next_work(state, auth):
    selected = state["runs"][auth["run_id"]].get("case_ids")
    projected = inbox_store.hot_state(state)
    work = engine.compact_work(projected)
    rows = inbox_reconcile.work(projected, selected, auth["run_id"])
    preparations = [{"case_id": c["id"], "reason": "research_and_prepare", "case": engine.summary_case(state, c)}
                           for c in projected["cases"].values() if (selected is None or c["id"] in selected) and inbox_preparation.pending(state, c)]
    work["preparation"] = preparations[:25]
    work["preparation_total"] = len(preparations)
    revisions = [{"candidate_id":r["id"], "case_id":r["case_id"], "draft_id":r["draft_id"], "status":r["status"], "next":"revision-prepare" if r['status']=='ready' else 'revision-reconcile'} for r in inbox_store.selected(state, "draft_revisions", status__in={'ready','attempting','unknown'}) if selected is None or r['case_id'] in selected]
    work['draft_revisions'] = revisions[:25]
    work['revision_total'] = len(revisions)
    learnable = [{"observation_id":o['id'], "case_id":o.get('case_id'), "candidate_id":o.get('candidate_id'), "next":"inbox-learn"} for o in inbox_store.selected(state, 'observations', status='learning_pending') if selected is None or o.get('case_id') in selected] if (state.get('config') or {}).get('learning',{}).get('enabled') and not (state.get('config') or {}).get('learning',{}).get('invalidated') else []
    work['learning'] = learnable[:25]
    work['learning_total'] = len(learnable)
    work["mailbox_reconciliation"] = rows[:25]
    work["reconciliation_total"] = len(rows)
    work["more_work"] = work["more_work"] or len(rows) > 25 or len(revisions) > 25 or len(preparations) > 25 or len(learnable) > 25
    if selected is not None:
        work['preparation'] = [r for r in work['preparation'] if r['case_id'] in selected]
        threads = {state["cases"][key]["thread_id"] for key in selected}
        all_work = engine.work(state)
        queue = [row for row in all_work["queue"] if row["thread_id"] in threads]
        cases = [engine.summary_case(state, row) for row in all_work["cases"] if row["id"] in selected]
        work.update(queue=queue[:50], cases=cases[:25], queue_total=len(queue), cases_total=len(cases),
                    more_work=len(queue) > 50 or len(cases) > 25 or len(rows) > 25 or len(revisions) > 25 or len(preparations) > 25 or len(learnable) > 25,
                    resume_scan=None, resume_scans=[])
        work["cleanup"]["cases"] = [row for row in work["cleanup"]["cases"] if row["case_id"] in selected]
        work["cleanup"]["retained"] = [row for row in work["cleanup"].get("retained", []) if row["case_id"] in selected]
        work["cleanup"]["legacy_pending"] = []
        for field in ("reevaluations", "due", "uncertain_sends"):
            work[field] = [row for row in work[field] if row["case_id"] in selected]
        work["recoverable_holds"] = [row for row in work["recoverable_holds"] if row["id"] in selected]
        work["draft_candidates"] = [key for key in work["draft_candidates"] if state["drafts"][key]["case_id"] in selected]
    return work


def _next(home, payload):
    auth = _auth(payload)
    return _next_work(_read_state(home, auth), auth)


def _reconcile(home, payload):
    auth = _auth(payload)
    with engine.transaction(home) as (root, box):
        state = box[0]
        engine.lease(state, auth)
        if 'assessment' in payload:
            if 'case' in payload or 'evidence' in payload:
                raise ValueError('assessment replaces separately authored case/evidence payloads')
            staged = copy.deepcopy(state)
            result = _assessed_case(root, staged, auth, payload.get('packet'), payload['assessment'], reconciliation=True)
            box[0] = staged
            return result
        fields = _only(payload.get("case"), CASE_FIELDS, "case")
        _selected_case(state, auth, fields.get("case_id"))
        evidence = _only(payload.get("evidence"),
            {"case_basis", "message_ids", "reason", "resolved_decision_ids", "related_outcomes"}, "evidence")
        saved = inbox_reconcile.assess(state, auth, _object(payload.get("packet"), "packet"), fields, evidence, root=root)
        return {"case": engine.summary_case(state, saved),
                "cleanup": [row for row in inbox_archive.work(state)["cases"] if row["case_id"] == saved["id"]]}


def _cleanup(home, payload):
    """Carry action identity/attempt internally; the host forwards an opaque step."""
    auth = _auth(payload)
    with engine.transaction(home) as (root, box):
        state = box[0]
        engine.lease(state, auth)
        engine.validate_profile(root, state["config"]["profile_files"])
        case_id = engine.text(payload.get("case_id"), "case_id")
        _selected_case(state, auth, case_id)
        if case_id not in state["cases"]:
            raise ValueError("unknown cleanup case")
        steps = state["runs"][auth["run_id"]].setdefault("cleanup_steps", {})
        if payload.get("step_id"):
            step = steps.get(payload["step_id"])
            if not step or step["case_id"] != case_id:
                raise ValueError("cleanup step does not belong to this case/run")
            result = inbox_archive.result(state, {**auth, **step,
                **{key: payload[key] for key in ("packet", "provider_ref", "error") if key in payload}})
        else:
            if payload.get("error") or payload.get("provider_ref"):
                raise ValueError("cleanup result requires its saved step_id")
            result = inbox_archive.prepare(state, {**auth, "case_id": case_id, "packet": payload.get("packet")})
        action = state.get("mailbox_actions", {}).get(result.get("action_id"), {})
        if action.get("status") in {"pending", "failed"}:
            step = {"case_id": case_id, "action_id": result["action_id"], "attempt": action["attempt"]}
            step_id = _json_digest([auth, step])
            steps[step_id] = step
            result["step_id"] = step_id
        return {**result, "business_state": state["cases"][case_id]["state"]}


def _candidate(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    auth = _auth(payload)
    saved = _save_candidate(home, auth, payload.get("packet"), payload.get("candidate"))
    return _review_artifact(saved)


def _prepare(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    auth = _auth(payload)
    mode = payload.get("mode")
    if mode not in {"draft", "send"}:
        raise ValueError("mode must be draft or send")
    provider_packet = _object(payload.get("packet"), "packet")
    snapshot = inbox_snapshot.build_snapshot(provider_packet)
    review = _only(payload.get("review"), REVIEW_FIELDS, "review")
    if review.get("decision") != mode:
        raise ValueError("review decision must match prepare mode")
    candidate_id = engine.text(payload.get("candidate_id"), "candidate_id")
    with engine.transaction(home) as (root, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        engine.lease(state, auth)
        if engine.text(provider_packet.get("mailbox"), "packet.mailbox").lower() != state["mailbox"]:
            raise ValueError("provider packet mailbox does not match this inbox")
        if provider_packet.get("draft_inventory"):
            from inbox_retrieval import verify_packet_inventory
            verify_packet_inventory(state, provider_packet)
        saved = state["candidates"].get(candidate_id)
        if not saved:
            raise ValueError("unknown candidate")
        _selected_case(state, auth, saved["case_id"])
        if review.get("candidate_id") != candidate_id or review.get("candidate_hash") != saved["hash"]:
            raise ValueError("review does not bind the exact saved candidate")
        check = engine.check(state, {
            **auth,
            **review,
        })
        if mode == "draft":
            case = state["cases"][saved["case_id"]]
            run = state["runs"][auth["run_id"]]
            already_reconciled = (
                run.get("reconciled_cases", {}).get(case["id"])
                == inbox_reconcile.basis(state, case)
            )
            preparation = state.get('preparations', {}).get(case['id'])
            carry_preparation = bool(preparation and preparation.get('disposition') == 'draft_ready' and
                                     'source_versions' in preparation and
                                     not inbox_preparation.pending(state, case))
            reservation = engine.draft(state=state, root=root, p={
                **auth, "candidate_id": candidate_id, "phase": "reserve", "snapshot": snapshot,
            })
            if carry_preparation:
                reservation['own_draft_observation'] = {
                    'case_basis': inbox_reconcile.basis(state, case),
                    'preparation_hash': engine.digest(preparation),
                    'source_versions': copy.deepcopy(preparation['source_versions']),
                }
            if already_reconciled:
                # The reservation is our own deterministic case transition. Carry it only
                # when no prior mailbox debt or later owner/provider change is being hidden.
                run.setdefault("draft_confirmation_reconciliation", {})[candidate_id] = (
                    inbox_reconcile.basis(state, case)
                )
            result = {"reservation": copy.deepcopy(reservation)}
        else:
            prepared = engine.prepare_send(root, state, {
                **auth, "candidate_id": candidate_id, "snapshot": snapshot,
            })
            result = {"action": copy.deepcopy(prepared["action"])}
        return {
            **result,
            "check": copy.deepcopy(check),
            "candidate_id": candidate_id,
            "candidate_hash": saved["hash"],
            "provider_payload": _provider_payload(saved),
        }


def _same_readback(saved: dict[str, Any], draft: dict[str, Any], state: dict[str, Any]) -> str:
    missing = sorted(DRAFT_READBACK_FIELDS - set(draft))
    if missing:
        raise ValueError("complete draft read-back missing: " + ", ".join(missing))
    draft_id = engine.text(draft.get("id"), "draft.id")
    attachments = draft.get("attachments", [])
    if not isinstance(attachments, list):
        raise ValueError("draft.attachments must be an array when present")
    actual = {
        "mailbox": engine.addresses([draft.get("from")])[0],
        "thread_id": engine.text(draft.get("thread_id"), "draft.thread_id"),
        "subject": draft.get("subject"),
        "body": draft.get("body"),
        "to": engine.addresses(draft.get("to")),
        "cc": engine.addresses(draft.get("cc"), empty=True),
        "bcc": engine.addresses(draft.get("bcc"), empty=True),
        "reply_headers": inbox_snapshot.threading_headers(draft.get("headers")),
        "attachments": copy.deepcopy(attachments),
    }
    expected = {
        "mailbox": saved["mailbox"],
        "thread_id": saved["thread_id"],
        "subject": saved["subject"],
        "body": saved["body"],
        "to": saved["recipients"],
        "cc": saved["cc"],
        "bcc": saved["bcc"],
        "reply_headers": inbox_snapshot.threading_headers(saved["reply_headers"]),
        "attachments": saved["attachments"],
    }
    if actual != expected:
        changed = sorted(key for key in expected if actual[key] != expected[key])
        raise ValueError("draft read-back does not match saved candidate: " + ", ".join(changed))
    for other_id, record in state["drafts"].items():
        if other_id != saved["id"] and record.get("draft_id") == draft_id:
            raise ValueError("provider draft id already belongs to another candidate")
    return draft_id


def _confirm(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    auth = _auth(payload)
    mode = payload.get("mode")
    if mode not in {"draft", "send"}:
        raise ValueError("mode must be draft or send")
    candidate_id = engine.text(payload.get("candidate_id"), "candidate_id")
    result = _object(payload.get("result"), "result")
    with engine.transaction(home) as (root, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        engine.lease(state, auth)
        saved = state["candidates"].get(candidate_id)
        if not saved:
            raise ValueError("unknown candidate")
        _selected_case(state, auth, saved["case_id"])
        if mode == "draft":
            status = result.get("status")
            existing = state["drafts"].get(candidate_id)
            if existing and existing.get("status") == "unknown" and status == "unknown":
                raise ValueError("uncertain draft result already recorded; do not repeat the external action")
            if status == "created":
                draft = _object(result.get("draft"), "result.draft")
                draft_id = _same_readback(saved, draft, state)
                receipt = engine.text(result.get("provider_receipt"), "provider_receipt", 10000)
                if existing and existing.get("status") == "pending":
                    if existing.get("draft_id") != draft_id or existing.get("provider_receipt") != receipt:
                        raise ValueError("duplicate draft confirmation has changed identity or receipt")
                    return {"candidate_id": candidate_id, "draft": copy.deepcopy(existing), "idempotent": True}
                value = engine.draft(root, state, {
                    **auth,
                    "candidate_id": candidate_id,
                    "phase": "confirm",
                    "candidate_hash": saved["hash"],
                    "mailbox": saved["mailbox"],
                    "thread_id": saved["thread_id"],
                    "draft_id": draft_id,
                    "provider_receipt": receipt,
                })
                value['readback_verified_at'] = engine.stamp()
                if saved.get('summary'):
                    value['summary_content_current'] = True
                    value['summary_candidate_hash'] = saved['hash']
                run = state["runs"][auth["run_id"]]
                expected_basis = run.get("draft_confirmation_reconciliation", {}).pop(
                    candidate_id, None
                )
                case = state["cases"][saved["case_id"]]
                if not inbox_preparation.enabled(state) and expected_basis == inbox_reconcile.basis(state, case):
                    run.setdefault("reconciled_cases", {})[case["id"]] = expected_basis
            elif status in {"failed", "unknown"}:
                state["runs"][auth["run_id"]].get(
                    "draft_confirmation_reconciliation", {}
                ).pop(candidate_id, None)
                forwarded = {
                    **auth,
                    "candidate_id": candidate_id,
                    "phase": status,
                    "reason": result.get("reason"),
                }
                if status == "failed":
                    forwarded["definitive_not_created"] = result.get("definitive_not_created")
                    forwarded["provider_receipt"] = result.get("provider_receipt")
                value = engine.draft(root, state, forwarded)
            else:
                raise ValueError("draft result status must be created, failed or unknown")
            return {"candidate_id": candidate_id, "draft": copy.deepcopy(value), "idempotent": False}

        matching = [action for action in state["actions"].values() if action["candidate_id"] == candidate_id]
        if len(matching) != 1:
            raise ValueError("candidate must have exactly one reserved send action")
        action = matching[0]
        if result.get("action_id") != action["id"]:
            raise ValueError("send result belongs to a different action")
        if action["status"] == "unknown":
            raise ValueError("uncertain send result already recorded; reconcile it before any new result")
        if action["status"] in {"accepted", "sent", "failed"} and result.get("status") == action["status"]:
            if action["status"] in {"accepted", "sent"}:
                immutable = {
                    "message_id": action.get("message_id"),
                    "provider_receipt": action.get("provider_receipt"),
                    "candidate_hash": action.get("candidate_hash"),
                    "mailbox": action.get("mailbox"),
                    "thread_id": action.get("thread_id"),
                }
                if any(result.get(key) != value for key, value in immutable.items()):
                    raise ValueError("duplicate send result has changed contents")
                if action["status"] == "sent" and result.get("read_back") is not True:
                    raise ValueError("sent state needs provider read-back")
            elif (result.get("reason") != action.get("reason") or
                  result.get("definitive_not_sent") is not True):
                raise ValueError("duplicate send result has changed contents")
        value = engine.send_result(state, copy.deepcopy(result))
        return copy.deepcopy(value)


def _due_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Keep durable completion evidence useful without retaining a whole case."""
    return {
        "case_id": item["case_id"],
        **({"obligation_id": item["obligation_id"]} if item.get("obligation_id") else {}),
        "due_at": item["due_at"],
        "next": item["next"],
    }


def _is_business_due(state: dict[str, Any], item: dict[str, Any]) -> bool:
    return item.get("next") in {"owner_attention", "owner_work"} and not engine.bot_work(
        state, item["case"]
    )


def _finish_receipt(
    state: dict[str, Any],
    auth: dict[str, str],
    full: dict[str, Any] | None = None,
    compact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the small immutable processing outcome stored with the run."""
    full = full if full is not None else engine.report(inbox_store.hot_state(state))
    compact = compact if compact is not None else engine.compact_report(state, full)
    run = state["runs"][auth["run_id"]]
    missing_scans = []
    for plan in run.get("discovery", []):
        scan = state["inbox_scans"].get(plan["lane"])
        if (not scan or not scan["complete"] or scan["run_id"] != auth["run_id"]
                or scan["query"] != plan["query"] or scan["window_end"] != plan["window_end"]):
            missing_scans.append(plan["lane"])

    selected = run.get("case_ids")
    if selected is not None:
        threads = {state["cases"][key]["thread_id"] for key in selected}
        full = copy.deepcopy(full)
        compact = copy.deepcopy(compact)
        full["queued"] = sum(row["thread_id"] in threads for row in state["queue"].values())
        full["attention"] = [row for row in full["attention"] if row["id"] in selected]
        full["due"] = [row for row in full["due"] if row["case_id"] in selected]
        full["reevaluations"] = [row for row in full["reevaluations"] if row["case_id"] in selected]
        full["scan"] = None
        full["inbox_scans"] = {}
        full["send_results"] = dict(__import__("collections").Counter(a["status"] for a in state["actions"].values() if a["case_id"] in selected))
        full["drafts"] = dict(__import__("collections").Counter(a["status"] for a in state["drafts"].values() if a["case_id"] in selected))
        compact["bot_actionable_cases"] = sum(engine.bot_work(state, state["cases"][key]) and not engine.held(state, state["cases"][key]) for key in selected)
        compact["cleanup_pending"] = sum(row["case_id"] in selected for row in inbox_archive.work(state)["cases"])
    attention = full["attention"]
    operational = [case for case in attention if case.get("hold") in OPERATIONAL_HOLDS]
    business_due = [item for item in full["due"] if _is_business_due(state, item)]
    cycle_due = [item for item in full["due"] if item not in business_due]
    remaining = {
        "unreconciled_cases": [row["case_id"] for row in inbox_reconcile.work(state, selected, auth["run_id"])
                               if row["case_id"] in run.get("reconcile_required", [])],
        "preparation": [c["id"] for c in inbox_store.hot_state(state)["cases"].values()
                        if (selected is None or c["id"] in selected) and inbox_preparation.pending(state, c)],
        "queue": full["queued"],
        "actionable_cases": compact["bot_actionable_cases"],
        "due": len(cycle_due),
        "due_items": [_due_summary(item) for item in cycle_due],
        "unscanned_lanes": missing_scans,
        "incomplete_scan": bool(missing_scans) or bool(full["scan"] and not full["scan"]["complete"]) or
                           any(not row["complete"] for row in full["inbox_scans"].values()),
        "rechecks": [{"case_id": row["case_id"], "recheck_id": row["id"]}
                     for row in full.get("reevaluations", [])],
        "operational_holds": [{"case_id": case["id"], "hold": case.get("hold")}
                              for case in operational],
        "learning": [{"observation_id":o['id'],"case_id":o.get('case_id')} for o in inbox_store.selected(state,'observations',status='learning_pending') if selected is None or o.get('case_id') in selected] if (state.get('config') or {}).get('learning',{}).get('enabled') and not (state.get('config') or {}).get('learning',{}).get('invalidated') else [],
        "draft_revisions": [{"candidate_id":r['id'],"case_id":r['case_id'],"status":r['status']} for r in inbox_store.selected(state,"draft_revisions",status__in={'ready','attempting','unknown'}) if selected is None or r['case_id'] in selected],
        "uncertain_sends": sum(full["send_results"].get(key, 0) for key in ("attempting", "unknown")),
        "unresolved_drafts": sum(full["drafts"].get(key, 0) for key in ("creating", "unknown")),
        "failed_sends": sum(case.get("hold") == "send_failed" for case in attention),
        "failed_drafts": sum(case.get("hold") == "draft_failed" for case in attention),
        "cleanup_pending": compact.get("cleanup_pending", 0),
    }
    complete = not any((
        remaining.get("learning"), remaining.get("draft_revisions"), remaining.get("preparation"), remaining["unreconciled_cases"], remaining["queue"], remaining["actionable_cases"], remaining["due"],
        remaining["incomplete_scan"], remaining["rechecks"], remaining["operational_holds"],
        remaining["uncertain_sends"], remaining["unresolved_drafts"],
        remaining["failed_sends"], remaining["failed_drafts"], remaining["cleanup_pending"],
    ))
    degraded = bool(
        remaining["operational_holds"] or remaining["uncertain_sends"] or
        remaining["unresolved_drafts"] or remaining["failed_sends"] or remaining["failed_drafts"]
    )
    business_counts = {
        "owner_attention": len(compact["owner_actions"]),
        "due": len(business_due),
        "waiting_correspondent": full["conversations"].get("waiting_correspondent", 0),
        "pending_drafts": full["drafts"].get("pending", 0),
    }
    return {
        "schema": 1,
        "released": True,
        "finished_at": run["finished_at"],
        "scope": {"kind": "selected_cases", "case_ids": selected} if selected is not None else {"kind": "mailbox"},
        "auth_hash": run["auth_hash"],
        "result": "failed" if degraded else ("success" if complete else "incomplete"),
        "complete": complete,
        "degraded": degraded,
        "remaining": remaining,
        "business_work_remaining": any(business_counts.values()),
        "business_counts": business_counts,
    }


def compact_finish_receipt(
    result: dict[str, Any], *, auth_hash: str | None = None, finished_at: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Convert a legacy stored full cycle result into the compact receipt schema."""
    remaining = copy.deepcopy(result.get("remaining", {}))
    remaining["due_items"] = [_due_summary(row) for row in remaining.get("due_items", [])]
    remaining["rechecks"] = [
        {"case_id": row["case_id"], "recheck_id": row.get("recheck_id", row.get("id"))}
        for row in remaining.get("rechecks", [])
    ]
    remaining["operational_holds"] = [
        {"case_id": row.get("case_id", row.get("id")), "hold": row.get("hold")}
        for row in remaining.get("operational_holds", [])
    ]
    remaining.setdefault("cleanup_pending", 0)
    business = result.get("business_work", {})
    business_counts = {
        "owner_attention": len(business.get("owner_attention", [])),
        "due": len(business.get("due", [])),
        "waiting_correspondent": business.get("waiting_correspondent", 0),
        "pending_drafts": business.get("pending_drafts", 0),
    }
    complete = bool(result.get("complete"))
    degraded = bool(result.get("degraded"))
    receipt = {
        "schema": 1,
        "released": bool(result.get("released", True)),
        "finished_at": finished_at or result.get("finished_at"),
        "auth_hash": auth_hash or result.get("auth_hash"),
        "result": "failed" if degraded else ("success" if complete else "incomplete"),
        "complete": complete,
        "degraded": degraded,
        "remaining": remaining,
        "business_work_remaining": bool(result.get("business_work_remaining", any(business_counts.values()))),
        "business_counts": business_counts,
    }
    if run_id:
        receipt["registry_evidence_ref"] = "cycle-report:" + run_id + ":" + _json_digest(result)
    return receipt


def _finish_response(
    state: dict[str, Any],
    receipt: dict[str, Any],
    full: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine immutable run outcome with the current complete owner action table."""
    full = full if full is not None else engine.report(inbox_store.hot_state(state))
    report = report if report is not None else engine.compact_report(state, full)
    business_due = [_due_summary(item) for item in full["due"] if _is_business_due(state, item)]
    owner_refs = [{"case_id": row["case_id"], "obligation_id": row.get("obligation_id")}
                  for row in report["owner_actions"]]
    business_work = {
        "owner_attention": owner_refs,
        "due": business_due,
        "waiting_correspondent": full["conversations"].get("waiting_correspondent", 0),
        "pending_drafts": full["drafts"].get("pending", 0),
    }
    return {
        "released": receipt["released"],
        "scope": receipt.get("scope", {"kind": "mailbox"}),
        "complete": receipt["complete"],
        "degraded": receipt["degraded"],
        "remaining": copy.deepcopy(receipt["remaining"]),
        "business_work_remaining": receipt["business_work_remaining"],
        "business_work": business_work,
        "report": report,
    }


def _finish_bound(home: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Finish the processing lease and reconcile its exact scheduler run independently."""
    auth = _auth(payload)
    with engine.transaction(home) as (_, box):
        state = box[0]
        if state is None:
            raise ValueError("inbox engine not initialized")
        run = state["runs"].get(auth["run_id"], {})
        if run.get("auth_hash") != _json_digest(auth):
            raise ValueError("current run credentials required")
        binding = copy.deepcopy(run.get("registry"))
        full = None
        compact = None
        receipt = run.get("finish_receipt")
        if receipt is None:
            legacy = run.get("cycle_result")
            if legacy is not None:
                receipt = compact_finish_receipt(
                    legacy, auth_hash=run["auth_hash"], finished_at=run.get("finished_at"),
                    run_id=auth["run_id"],
                )
            else:
                current = state.get("lease")
                if (not current or current.get("run_id") != auth["run_id"] or
                        current.get("token") != auth["token"]):
                    raise ValueError("cycle lease was replaced or already released")
                run["finished_at"] = engine.stamp()
                state["lease"] = None
                full = engine.report(inbox_store.hot_state(state))
                compact = engine.compact_report(state, full)
                receipt = _finish_receipt(state, auth, full, compact)
            run["finish_receipt"] = copy.deepcopy(receipt)
        else:
            full = engine.report(inbox_store.hot_state(state))
            compact = engine.compact_report(state, full)
        if full is None:
            full = engine.report(inbox_store.hot_state(state))
            compact = engine.compact_report(state, full)
        result = _finish_response(state, receipt, full, compact)
        import inbox_feedback
        import inbox_maintenance
        saved_report = inbox_feedback.report_snapshot(state)
        result["report_id"] = saved_report["report_id"]
        result["questions"] = saved_report["questions"]
        result["drafts"] = saved_report["drafts"]
        result["owner_report"] = inbox_feedback.render(state, saved_report["report_id"], base_report=engine.owner_report(state))
        result["maintenance"] = inbox_maintenance.maintain(state)
    if (state.get("config") or {}).get("draft_first"):
        import inbox_knowledge
        try:
            result["knowledge_maintenance"] = inbox_knowledge.execute(home, "knowledge-maintain", {})
        except (ValueError, sqlite3.Error) as exc:
            result["knowledge_maintenance"] = {"status":"pending", "reason":str(exc)}
    if binding:
        completion = {
            **binding, "run_id": auth["run_id"],
            "result": receipt["result"],
            "evidence_ref": receipt.get("registry_evidence_ref") or (
                "cycle-receipt:" + auth["run_id"] + ":" + _json_digest(receipt)
            ),
        }
        try:
            result["schedule"] = inbox_registry.execute(binding["root"], "complete", {
                **completion, **({"scheduler_readback": payload["scheduler_readback"]}
                                if payload.get("scheduler_readback") is not None else {}),
            })
        except inbox_registry.RegistryError as exc:
            # A bad host readback must not lose the processing result or claim a next wake.
            result["schedule"] = inbox_registry.execute(binding["root"], "complete", completion)
            result["schedule"]["readback_error"] = str(exc)
    return result


def _preparation_command(home, command, payload):
    auth = _auth(payload)
    with engine.transaction(home) as (root, box):
        state = box[0]
        if payload.get("case_id"):
            _selected_case(state, auth, payload["case_id"])
        if command == 'packet':
            return inbox_preparation.save_packet(state, auth, payload['packet'])
        if command == 'observe':
            return inbox_preparation.observe(state, auth, payload['packet'], payload['case_id'], root=root)
        return inbox_preparation.assess(state, auth, payload)


def execute(home: str | Path, command: str, payload: Any) -> dict[str, Any]:
    """Execute one bounded cycle operation, raising a structured CycleError."""
    if command not in COMMANDS:
        raise CycleError("unknown cycle command", command=command, error_type="unknown_command")
    try:
        payload = dict(payload)
        if "packet" in payload:
            payload["packet"] = inbox_snapshot.normalize_packet(payload["packet"])
        if command == "answers":
            return engine.execute(home, command, payload)
        if command.startswith("revision-"):
            return engine.execute(home, command, payload)
        if "packet_ref" in payload:
            if command not in {"preparation","observe","candidate","prepare","reconcile","cleanup"} or "packet" in payload:
                raise ValueError("packet_ref requires a single evidence-consuming operation")
            with engine.transaction(home, readonly=True) as (_, box):
                payload["packet"] = inbox_preparation.resolve_packet(box[0], payload.pop("packet_ref"))
        request = _only(payload, PAYLOAD_FIELDS[command], "payload")
        if command in {"preparation", "packet", "observe"}:
            return _preparation_command(home, command, request)
        dispatch = {
            "begin": _begin,
            "intake": _intake,
            "next": _next,
            "reconcile": _reconcile,
            "cleanup": _cleanup,
            "candidate": _candidate,
            "prepare": _prepare,
            "confirm": _confirm,
            "finish": _finish_bound,
        }
        return dispatch[command](home, request)
    except CycleError:
        raise
    except inbox_snapshot.SnapshotError as exc:
        raise CycleError(str(exc), command=command, error_type="invalid_provider_packet") from exc
    except (ValueError, KeyError, TypeError, inbox_registry.RegistryError) as exc:
        raise CycleError(str(exc), command=command) from exc
    except (OSError, sqlite3.Error) as exc:
        raise CycleError(str(exc), command=command, error_type="operational_error") from exc


def failure_report(home, reason):
    """A failed finish must still expose saved work without changing its state."""
    warning = "**Run incomplete: " + str(reason).replace("\n", " ") + "**\n\nSaved-state dashboard; provider freshness and completion were not reverified.\n\n"
    try:
        with engine.transaction(home, readonly=True) as (_, box):
            import inbox_feedback
            return warning + inbox_feedback.render(box[0], report_id="failure-preview", base_report=engine.owner_report(box[0]))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        return warning + "\n\n".join("### " + title + "\n\nUnavailable: saved inbox state could not be read; no empty inbox or completed work is implied." for title in (
            "Needs your attention", "Waiting on others", "Completed this run", "Drafts", "Questions")) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--home", required=True)
    parser.add_argument("--input", help="JSON input file; omit to read stdin")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Use markdown for the complete final dashboard from finish")
    parser.add_argument("--full", action="store_true", help="Include duplicate machine report fields in finish diagnostics")
    args = parser.parse_args()
    try:
        if args.input:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            if sys.stdin.isatty():
                raise CycleError("JSON input requires --input FILE or file redirection; interactive terminals are not supported",
                                 command=args.command, error_type="invalid_input")
            payload = json.load(sys.stdin)
        if args.format == "markdown" and args.command != "finish":
            raise CycleError("markdown output is available for finish only", command=args.command)
        result = execute(args.home, args.command, payload)
        if args.format == "markdown":
            print(result["owner_report"], end="")
            return 0
        if args.command == "finish" and not args.full:
            result = {key:value for key,value in result.items() if key not in {"report","questions","drafts"}}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, separators=(",", ":")))
    except CycleError as exc:
        if args.format == "markdown":
            print(failure_report(args.home, str(exc)), end="")
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        error = CycleError(str(exc), command=args.command, error_type="invalid_input")
        if args.format == "markdown":
            print(failure_report(args.home, str(exc)), end="")
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
