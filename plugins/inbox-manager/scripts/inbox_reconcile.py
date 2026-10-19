"""Evidence-bound mailbox reassessment; semantic decisions remain with the host model."""

import copy
import inbox_store


def basis(state, case):
    import inbox_engine as engine
    semantic = {key: value for key, value in case.items()
                if key not in {"cleanup", "mailbox_assessment", "updated_at"}}
    semantic["snapshot"] = {key: value for key, value in case.get("snapshot", {}).items()
                            if key not in {"observed_at", "thread_observed_at", "drafts_observed_at"}}
    return engine.digest({"case": semantic, "profile_version": state["version"],
                          "decisions": engine.pending_decisions(state, case)})


def work(state, selected=None, run_id=None):
    import inbox_engine as engine
    import inbox_priority
    import inbox_archive
    cleanup_ids = {row["case_id"] for row in inbox_archive.work(state)["cases"]}
    run = state.get("runs", {}).get(run_id or (state.get("lease") or {}).get("run_id"), {})
    checked = run.get("reconciled_cases", {})
    result = []
    for case in engine.ordered_cases(state, (state["cases"][key] for key in selected) if selected is not None else inbox_store.selected(state, "cases", active=1)):
        if selected is not None and case["id"] not in selected:
            continue
        if case["state"] == "handled" and case["id"] not in cleanup_ids and selected is None:
            continue
        if checked.get(case["id"]) == basis(state, case):
            continue
        result.append({"case_id": case["id"], "thread_id": case["thread_id"],
                       "trigger_id": case.get("snapshot", {}).get("trigger_id"),
                       "case_basis": basis(state, case),
                       "case": engine.summary_case(state, case)})
    return result


def _record_revision_review(root, state, case, packet, patches):
    """Bind explicit semantic review patches to current exact revision evidence."""
    import inbox_engine as engine
    import inbox_preparation
    import inbox_revisions
    draft = state['drafts'].get(case.get('pending_candidate_id'), {})
    revision = state.get('draft_revisions', {}).get(draft.get('latest_revision_id'), {})
    if (draft.get('status') != 'pending' or revision.get('kind') != 'candidate' or
            revision.get('status') != 'confirmed' or revision.get('case_id') != case['id'] or
            revision.get('draft_id') != draft.get('draft_id') or
            revision.get('profile_version') != state['version'] or case.get('takeover') or
            case.get('hold') not in {None, 'owner_decision'}):
        return
    selected = [key for key, patch in patches.items()
                if patch.get('draft_review_id') == draft.get('draft_id') and
                case['obligations'][key].get('state') == 'waiting_owner' and
                case['obligations'][key].get('responsibility_type') == 'owner']
    decisions = engine.pending_decisions(state, case)
    selected = [key for key in selected if not any(
        not row.get('obligation_ids') or key in row['obligation_ids'] for row in decisions)]
    if not selected:
        return
    try:
        owner = inbox_revisions._stored_owner_answer(state, revision['owner_ref'], case['id'])
        if owner['digest'] != revision['owner_answer_digest']:
            return
        if not inbox_preparation._dependencies_current(
                root, state, revision['source_versions'], revision.get('knowledge_dependencies', [])):
            return
        same, _, proof = inbox_revisions._same_readback(revision, packet, state, case)
        current = proof.get('snapshot', {})
        readback = revision.get('readback_proof', {}).get('snapshot', {})
        prepared = revision.get('prepare_proof', {}).get('snapshot', {})
        if (not same or not readback.get('complete') or
                current.get('content_fingerprint') != readback.get('content_fingerprint') or
                any(current.get('message_fingerprint') != snap.get('message_fingerprint')
                    for snap in (readback, prepared)) or
                any(current.get(key) != snap.get(key) for snap in (readback, prepared)
                    for key in ('trigger_id', 'automated', 'categories')) or
                any(row.get('case_id') == case['id'] and row.get('status') in {'attempting', 'unknown'}
                    for row in state.get('actions', {}).values()) or
                any(row.get('draft_id') == draft['draft_id'] and
                    row.get('created_at', '') > revision.get('created_at', '') and
                    row.get('status') in {'ready', 'attempting', 'unknown', 'failed', 'conflicted'}
                    for row in state.get('draft_revisions', {}).values())):
            return
    except (ValueError, KeyError, TypeError, OSError):
        return
    draft.setdefault('review_obligation_versions', {}).update(
        {key: case['obligations'][key]['version'] for key in selected})


def assess(state, auth, packet, fields, evidence, root=None):
    """Reconcile a complete fresh read without granting new sending authority.

    Ordinary mailbox outcomes may settle obligations. A pending owner decision can
    only be retired by a newer provider-confirmed Sent message from this mailbox,
    explicitly tied to every obligation in that decision. Explicit takeover and
    operational/unknown-action holds survive all mailbox assessments.
    """
    import inbox_engine as engine
    import inbox_snapshot
    import inbox_priority
    engine.lease(state, auth)
    case = state["cases"].get(fields.get("case_id"))
    if not case:
        raise ValueError("reconcile requires an existing tracked case")
    if evidence.get("case_basis") != basis(state, case):
        raise ValueError("case changed; reload reconciliation work")
    snap = inbox_snapshot.build_snapshot(packet)
    if packet.get("mailbox", "").casefold() != state["mailbox"].casefold() or snap["thread_id"] != case["thread_id"]:
        raise ValueError("reconciliation mailbox/thread mismatch")
    inbox_priority.require_scope(state, case, snap)
    if not engine.fresh(snap["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
        raise ValueError("reconciliation requires fresh complete evidence")
    if packet.get("draft_inventory"):
        from inbox_retrieval import verify_packet_inventory
        verify_packet_inventory(state, packet)
    ids = evidence.get("message_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(mid not in snap["message_ids"] for mid in ids):
        raise ValueError("evidence message_ids must identify observed messages")
    reason = engine.text(evidence.get("reason"), "mailbox assessment reason", 10000)
    decisions = evidence.get("resolved_decision_ids", [])
    if not isinstance(decisions, list) or len(decisions) != len(set(decisions)):
        raise ValueError("resolved_decision_ids must be distinct")
    patches = {row["id"]: row for row in fields.get("obligations", [])}
    # Completion elsewhere retires a fulfilled request; it never records owner approval.
    related = evidence.get("related_outcomes", [])
    if not isinstance(related, list) or len(related) > 5:
        raise ValueError("related outcomes must be a bounded list")
    related_proofs = []
    from inbox_store import linked_cases
    linked_ids = {row["id"] for row in linked_cases(state, case)} if related else set()
    for item in related:
        if not isinstance(item, dict):
            raise ValueError("related outcome must be an object")
        other = state["cases"].get(item.get("case_id"))
        if (not other or other["id"] == case["id"] or other["id"] not in linked_ids or
                other["state"] != "handled" or other.get("hold") or other.get("pending_decision") or
                any(o["state"] != "handled" for o in other.get("obligations", {}).values()) or
                not other.get("outcome_evidence")):
            raise ValueError("related outcome needs an evidenced, handled linked case")
        current = inbox_snapshot.build_snapshot(item.get("packet", {}))
        if (item["packet"].get("mailbox", "").casefold() != state["mailbox"].casefold() or
                current["thread_id"] != other["thread_id"] or
                not engine.fresh(current["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]) or
                current["content_fingerprint"] != other["snapshot"].get("content_fingerprint")):
            raise ValueError("related outcome requires a fresh unchanged complete thread")
        inbox_priority.require_scope(state, other, current)
        mids = item.get("message_ids")
        if not isinstance(mids, list) or not mids or any(mid not in current["message_ids"] for mid in mids):
            raise ValueError("related outcome must cite observed messages")
        related_proofs.append({"case_id": other["id"], "message_ids": mids,
            "reason": engine.text(item.get("reason"), "related completion reason", 10000)})
    from email.utils import parseaddr
    for key in decisions:
        decision = next((d for d in engine.pending_decisions(state, case) if d["id"] == key), None)
        if not decision:
            raise ValueError("selected owner decision is not pending for this case")
        sent = []
        for message in packet["messages"]:
            from inbox_snapshot import label_ids
            labels = label_ids(message) or []
            if (message["id"] in ids and "SENT" in labels and
                    parseaddr(message["from"])[1].casefold() == state["mailbox"].casefold()):
                date = message.get("sent_at") or message.get("received_at")
                if date and engine.iso(date) > engine.iso(decision["created_at"]):
                    sent.append(message["id"])
        if not sent and not related_proofs:
            raise ValueError("owner decision needs a newer provider-confirmed Sent reply from this mailbox")
        selected = decision.get("obligation_ids", [])
        if selected and any(patches.get(oid, {}).get("state") != "handled" for oid in selected):
            raise ValueError("owner decision resolution must complete every selected obligation")
        if not selected and fields.get("state") != "handled":
            raise ValueError("case-wide owner decision needs evidenced completion")
        decision.update(status="resolved_in_mailbox", resolved_at=engine.stamp(),
                        resolution_evidence={"message_ids": sent, "reason": reason,
                            **({"related_outcomes": related_proofs, "completion_only": True}
                               if related_proofs else {})})
    if decisions:
        case["pending_decision"] = bool(engine.pending_decisions(state, case))
        if not case["pending_decision"] and case.get("hold") == "owner_decision":
            case["hold"] = None
            case.pop("hold_reason", None)
    fields = dict(fields)
    if (case.get("state") == "handled" and fields.get("state") == "handled" and
            case["snapshot"].get("content_fingerprint", case["snapshot"]["fingerprint"]) ==
            snap.get("content_fingerprint", snap["fingerprint"])):
        fields.setdefault("outcome", case.get("outcome"))
        fields.setdefault("outcome_evidence", case.get("outcome_evidence"))
        if fields.get("obligations") == [] and all(row["state"] == "handled" for row in case.get("obligations", {}).values()):
            fields.pop("obligations")
    values = {**auth, **fields, "snapshot": snap}
    queued = state["reevaluations"].get(case["id"])
    if queued:
        values["recheck_id"] = queued["id"]
    saved = engine.update_case(state, values)
    if packet.get("draft_inventory"):
        from inbox_drafts import reconcile_presence
        reconcile_presence(state, saved, packet)
    _record_revision_review(root, state, saved, packet, patches)
    from inbox_preparation import reconcile_summaries
    reconcile_summaries(state, saved, packet)
    saved["mailbox_assessment"] = {"message_ids": ids, "reason": reason, "observed_at": snap["observed_at"]}
    state["runs"][auth["run_id"]].setdefault("reconciled_cases", {})[case["id"]] = basis(state, saved)
    engine.event(state, "mailbox_reconciled", case_id=case["id"], message_ids=ids,
                 resolved_decision_ids=decisions)
    return copy.deepcopy(saved)
