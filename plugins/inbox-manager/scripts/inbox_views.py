"""Small read projections. Full evidence stays in the mailbox database."""

from collections import Counter
from html import escape
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

from inbox_identity import responsibility_type


def owner_responsible(state, value):
    return responsibility_type(state, {"responsible": value}) == "owner"


def owner_work(state, item):
    return (item.get("state") == "waiting_owner" or
            (item.get("state") == "needs_action" and responsibility_type(state, item) == "owner"))


def bot_work(state, case):
    rows = case.get("obligations", {}).values()
    return any(row["state"] == "needs_action" and responsibility_type(state, row) == "bot"
               for row in rows) if rows else (
        case["state"] == "needs_action" and responsibility_type(state, case) == "bot")


def unknown_work(state, case):
    rows = case.get("obligations", {}).values()
    return any(row["state"] == "needs_action" and responsibility_type(state, row) == "unknown"
               for row in rows) if rows else (
        case["state"] == "needs_action" and responsibility_type(state, case) == "unknown")


def verified_url(value, evidence):
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.netloc != "mail.google.com" or
            not parsed.path.startswith("/mail/") or not evidence):
        raise ValueError("thread_url needs a verified Gmail conversation URL and thread_url_evidence")
    return value


def _provider_url_for_case(state, case):
    """Return a provider URL only when it binds this mailbox and exact thread."""
    value = case.get("thread_url")
    try:
        verified_url(value, case.get("thread_url_evidence"))
    except (TypeError, ValueError):
        return None
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    mailbox = state.get("mailbox")
    account = query.get("authuser")
    if parsed.path != "/mail/" or set(query) != {"authuser"} or account != [mailbox]:
        return None
    fragment = parsed.fragment.split("/", 1)
    if len(fragment) != 2 or fragment[0] not in {"all", "inbox", "drafts"}:
        return None
    return value if unquote(fragment[1]) == str(case.get("thread_id") or "") else None


def gmail_thread_url(state, case):
    """Return an account-bound link for a durable Gmail thread identity."""
    thread_id = case.get("thread_id")
    mailbox = state.get("mailbox")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(mailbox, str) or not mailbox:
        raise ValueError("report row needs a bound Gmail thread ID and mailbox")
    provider_url = _provider_url_for_case(state, case)
    if provider_url:
        return provider_url
    return ("https://mail.google.com/mail/?authuser=" + quote(mailbox, safe="") +
            "#all/" + quote(thread_id, safe=""))


def summary_case(state, case):
    result = {key: case.get(key) for key in (
        "id", "thread_id", "state", "unresolved_ask", "next_action", "responsible", "responsibility_type",
        "due_at", "urgency",
        "hold", "hold_reason", "pending_decision", "pending_candidate_id", "takeover", "topic", "context",
        "linked_cases", "reply_repair",
        "thread_url", "recommendation") if case.get(key) is not None}
    result["obligations"] = [{key: row.get(key) for key in (
        "id", "ask", "state", "next_action", "responsible", "responsibility_type", "due_at", "version",
        "draft_review_id", "draft_status")}
        for row in case.get("obligations", {}).values() if row["state"] != "handled"]
    return result


def _dismissed(state, case_id, obligation_id):
    return bool(state.get("dismissals", {}).get(case_id, {}).get(obligation_id or "__case__"))


def _case_in_scope(state, case):
    import inbox_priority
    # The fallback keeps pre-field databases readable during the additive upgrade.
    predicate = getattr(inbox_priority, "case_in_scope", None)
    return True if predicate is None else predicate(state, case)


def _business_text(value):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"(?i)\bComplete Gmail thread read at\s+\d{4}-\d{2}-\d{2}T[0-9:.+\-]+Z?", "", value)
    return value.strip()


def draft_covers_obligation(state, case, draft, item):
    """Verified creation/review covers the saved obligation version, not its ID."""
    key, version = item["id"], item.get("version")
    if version is None or draft.get("status") != "pending":
        return False
    if draft.get("obligation_versions", {}).get(key) == version:
        return True
    if (draft.get("review_obligation_versions", {}).get(key) != version or
            item.get("draft_review_id") != draft.get("draft_id") or item.get("draft_status") != "present"):
        return False
    if not draft.get("latest_revision_id"):
        return True
    revision = state.get("draft_revisions", {}).get(draft.get("latest_revision_id"), {})
    proof = revision.get("readback_proof", {}).get("snapshot", {})
    if (revision.get("status") == "confirmed" and revision.get("kind") == "candidate" and
            revision.get("case_id") == case["id"] and revision.get("draft_id") == draft["draft_id"] and
            draft["draft_id"] in proof.get("draft_ids", []) and
            not any(row.get("draft_id") == draft["draft_id"] and
                    row.get("status") in {"ready", "attempting", "unknown", "failed", "conflicted"}
                    for row in state.get("draft_revisions", {}).values()
                    if row.get("created_at", "") > revision.get("created_at", ""))):
        return True
    return False


def _review_draft(state, case, item, obligations):
    draft = state["drafts"].get(case.get("pending_candidate_id"), {})
    if draft.get("status") == "pending" and (not obligations or
            draft_covers_obligation(state, case, draft, item)):
        return draft
    return {}


def action_table(state, *, waiting=False, include_bot=False):
    """Every open owner obligation, independent of alerts or message age."""
    import inbox_engine as engine
    starts = [run["started_at"] for run in state["runs"].values()]
    latest = max(starts, key=engine.iso) if starts else None
    due_keys = {(row["case_id"], row.get("obligation_id")) for row in engine.due(state)
                if row["next"] == "owner_attention"}
    result = []
    for case in state["cases"].values():
        if not _case_in_scope(state, case):
            continue
        if case["state"] == "handled" and not case.get("hold"):
            continue
        obligations = list(case.get("obligations", {}).values())
        decisions = engine.pending_decisions(state, case)
        covered = False
        for item in obligations or [case]:
            oid = item["id"] if obligations else None
            if ((item["state"] == "handled" and (obligations or not case.get("hold"))) or
                    _dismissed(state, case["id"], oid)):
                continue
            # Provider-confirmed absence retires only the obsolete draft-review
            # artifact. Other business obligations on the case stay visible.
            if item.get("draft_review_id") and item.get("draft_status") == "absent":
                continue
            decision = next((d for d in decisions if not d["obligation_ids"] or oid in d["obligation_ids"]), {})
            draft = _review_draft(state, case, item, obligations)
            draft_review = bool(draft) and not decision
            saved_draft = state["drafts"].get(case.get("pending_candidate_id"), {})
            stale_draft = bool(obligations and not draft and saved_draft.get("status") == "pending" and
                (oid in saved_draft.get("obligation_ids", []) or
                 oid in saved_draft.get("review_obligation_versions", {}) or
                 item.get("draft_review_id") == saved_draft.get("draft_id")))
            typed = responsibility_type(state, item)
            selected = (item["state"] == "waiting_correspondent" and (case["id"], oid) not in due_keys
                        if waiting else owner_work(state, item) or bool(decision) or draft_review or
                        (case["id"], oid) in due_keys or
                        (item["state"] == "needs_action" and (typed == "unknown" or stale_draft or
                         (include_bot and typed == "bot"))))
            if not obligations and case.get("hold") and not waiting:
                selected = True
            if not selected:
                continue
            covered = True
            created = item.get("created_at", case.get("created_at"))
            request = _business_text(item.get("ask") or case.get("unresolved_ask"))
            context = _business_text(case.get("context")) or request
            absent_case_draft = not obligations and bool(case.get("draft_absence"))
            result.append({
                "case_id": case["id"], "obligation_id": oid, "decision_id": decision.get("id"),
                "draft_id": draft.get("draft_id") if draft_review else saved_draft.get("draft_id") if stale_draft else None,
                "draft_needs_refresh": stale_draft,
                "thread_id": case["thread_id"], "thread_url": case.get("thread_url"),
                "topic": case.get("topic") or case.get("unresolved_ask"),
                "correspondents": case.get("snapshot", {}).get("correspondents", []),
                "context": context,
                "request": request,
                "responsible": item.get("responsible"), "responsibility_type": typed,
                "status": ("draft_absent" if absent_case_draft else
                           "draft_awaiting_review" if draft_review else case.get("hold_reason") or
                           ("responsibility_unassigned" if typed == "unknown" else item["state"])),
                "recommended_action": ("Confirm whether a reply is still needed." if absent_case_draft else
                    "Review the prepared Gmail draft; edit and send it if approved." if draft_review else
                    item.get("next_action") if stale_draft and not decision else
                    decision.get("recommendation") or item.get("recommendation") or
                    case.get("recommendation") or item.get("next_action")),
                "due_at": item.get("due_at"), "urgency": case.get("urgency", "normal"),
                "carried_forward": not (created and latest and engine.iso(created) >= engine.iso(latest)),
            })
        # A case-wide operational failure/takeover may not be an obligation yet.
        absent_owner_hold = bool(case.get("draft_absence") and
                                 (case.get("hold") or case.get("state") == "waiting_owner"))
        if (obligations and not covered and not waiting and
                (case.get("hold") not in {None, "owner_decision"} or case.get("takeover") or absent_owner_hold) and
                not _dismissed(state, case["id"], None)):
            request = _business_text(case.get("unresolved_ask"))
            result.append({"case_id": case["id"], "obligation_id": None, "thread_id": case["thread_id"],
                "thread_url": case.get("thread_url"), "topic": case.get("topic") or case["unresolved_ask"],
                "correspondents": case.get("snapshot", {}).get("correspondents", []),
                "context": _business_text(case.get("context")) or request, "request": request,
                "responsible": case.get("responsible"), "responsibility_type": responsibility_type(state, case),
                "status": "draft_absent" if absent_owner_hold else case.get("hold_reason") or case["state"],
                "recommended_action": ("Confirm whether a reply is still needed." if absent_owner_hold else
                                       case.get("recommendation") or case["next_action"]),
                "due_at": case.get("due_at"), "urgency": case.get("urgency", "normal"), "carried_forward": True})
    return sorted(result, key=lambda row: (row["urgency"] != "urgent", row.get("due_at") or "9999", row["case_id"], row.get("obligation_id") or ""))


def _scan(value):
    return {k: v for k, v in value.items() if k != "message_ids"} if value else value


def compact_due(state, rows):
    return [{**{k: v for k, v in row.items() if k not in {"case", "obligation"}},
             "case": {k: row["case"].get(k) for k in ("id", "thread_id", "state", "responsible",
                       "next_action", "hold", "pending_decision", "pending_candidate_id")}} for row in rows]


def compact_report(state, full=None):
    from inbox_store import hot_state
    state = hot_state(state)
    import inbox_engine as engine
    full = full if full is not None else engine.report(state)
    scoped_cases = [case for case in state["cases"].values() if _case_in_scope(state, case)]
    scoped_ids = {case["id"] for case in scoped_cases}
    scoped_threads = {case["thread_id"] for case in scoped_cases}
    result = {key: full[key] for key in ("mailbox", "active", "paused", "version", "coverage",
        "source_refresh")}
    result.update(
        queued=full["queued"],
        conversations=dict(Counter(case["state"] for case in scoped_cases)),
        outcomes=dict(Counter(case.get("outcome") for case in scoped_cases if case.get("outcome"))),
        send_results=dict(Counter(action["status"] for action in state["actions"].values()
                                  if action.get("case_id") in scoped_ids)),
        drafts=dict(Counter(draft["status"] for draft in state["drafts"].values()
                            if draft.get("case_id") in scoped_ids)),
        obligations=dict(Counter(row["state"] for case in scoped_cases
                                 for row in case.get("obligations", {}).values())),
        reevaluations=[row for row in full["reevaluations"] if row.get("case_id") in scoped_ids],
    )
    result.update(scan=_scan(full["scan"]), inbox_scans={k: _scan(v) for k, v in full["inbox_scans"].items()},
        attention=[{k: case.get(k) for k in ("id", "thread_id", "hold", "hold_reason", "pending_decision", "pending_candidate_id")}
                   for case in full["attention"] if case.get("hold") and case["id"] in scoped_ids],
        due=compact_due(state, [row for row in full["due"] if row["case_id"] in scoped_ids]),
        owner_actions=full["owner_actions"], waiting_on_others=full["waiting_on_others"],
        bot_actionable_cases=sum(bot_work(state, c) and not engine.held(state, c) for c in scoped_cases),
        unassigned_actionable_cases=sum(unknown_work(state, c) and not engine.held(state, c) for c in scoped_cases),
        lease={k: v for k, v in state["lease"].items() if k != "token"} if state["lease"] else None)
    result["cleanup"] = {
        **full["cleanup"],
        "cases": [row for row in full["cleanup"]["cases"] if row["case_id"] in scoped_ids],
        "retained": [row for row in full["cleanup"].get("retained", []) if row["case_id"] in scoped_ids],
        "legacy_pending": [row for row in full["cleanup"]["legacy_pending"]
                           if row.get("thread_id") in scoped_threads],
    }
    result["cleanup_pending"] = len(result["cleanup"]["cases"]) + len(result["cleanup"]["legacy_pending"])
    result["resolved_cleanup_pending"] = [
        {"case_id": row["case_id"], "thread_url": gmail_thread_url(state, state["cases"][row["case_id"]]),
         "topic": state["cases"][row["case_id"]].get("topic") or state["cases"][row["case_id"]].get("unresolved_ask"),
         "business_status": "resolved", "mailbox_status": "cleanup_pending",
         "reason": row.get("reason") or "fresh verification required"}
        for row in result["cleanup"]["cases"] if state["cases"][row["case_id"]]["state"] == "handled"]
    result["cleanup_results"] = dict(Counter(
        action["status"] for action in state.get("mailbox_actions", {}).values()
        if action.get("case_id") in scoped_ids or action.get("thread_id") in scoped_threads
    ))
    return result


def _cell(value):
    """Keep saved email text inside one inert Markdown table cell."""
    value = escape(str(value or "Not recorded"), quote=False)
    value = re.sub(r"([\\`*_\[\]|])", r"\\\1", value)
    return "<br>".join(value.splitlines())


def _saved_question_numbers(state):
    """Return only durable question mappings from the latest saved report."""
    key = state.get("latest_report_id")
    snapshot = state.get("report_snapshots", {}).get(key) if key else None
    if not snapshot:
        return {}
    return {decision_id: row["number"] for row in snapshot.get("questions", [])
            for decision_id in row.get("decision_ids", [])}


def _draft_status(state, row):
    if row.get("draft_needs_refresh"):
        return "Existing draft needs refresh; current ask not verified"
    if not row.get("draft_id"):
        return "No verified draft"
    record = next((item for item in state.get("drafts", {}).values()
                   if item.get("case_id") == row["case_id"] and
                   item.get("draft_id") == row["draft_id"]), {})
    revision = state.get("draft_revisions", {}).get(record.get("latest_revision_id"), {})
    if (record.get("status") == "pending" and revision.get("kind") == "candidate" and
            revision.get("status") == "confirmed" and revision.get("draft_id") == row["draft_id"]):
        return "Updated and verified"
    return {"pending": "Ready for review", "current": "Current", "updated": "Updated",
            "confirmed": "Verified", "creating": "Creation pending", "unknown": "Status unknown",
            "failed": "Creation failed", "conflicted": "Conflict needs review",
            "blocked": "Blocked"}.get(record.get("status"), "Verified draft")


def _attention_markdown(state, rows):
    if not rows:
        return "None."
    numbers = _saved_question_numbers(state)
    lines = ["| Conversation | Context | Decision/action needed | Recommendation | Draft status | Email |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        context = row.get("context") or "Business context not recorded."
        if row.get("request") and row["request"] != context:
            context += "\nRequest: " + row["request"]
        source = "\n".join(filter(None, [row.get("topic"), ", ".join(row.get("correspondents", []))]))
        status = {"waiting_owner": "Needs your decision", "waiting_correspondent": "Waiting for reply",
                  "draft_awaiting_review": "Draft ready for review", "needs_action": "Action needed",
                  "draft_absent": "Draft no longer exists",
                  "responsibility_unassigned": "Responsibility needs assignment"}.get(
                      row.get("status"), str(row.get("status") or "Not recorded").replace("_", " "))
        deadline = "\nDeadline: " + row["due_at"] if row.get("due_at") else ""
        number = numbers.get(row.get("decision_id"))
        if number:
            status = f"Question {number}: " + status
        elif row.get("decision_id"):
            status = "Pending decision (number is not saved yet): " + status
        case = state["cases"].get(row["case_id"], {})
        url = gmail_thread_url(state, case)
        link = "[Open email](" + quote(url, safe="/:#?=&%+@") + ")"
        needed = status + deadline
        recommendation = row.get("recommended_action") or "No recommendation recorded"
        cells = [source, context, needed, recommendation, _draft_status(state, row)]
        lines.append("| " + " | ".join([*map(_cell, cells), link]) + " |")
    return "\n".join(lines)


def _waiting_markdown(state, rows):
    if not rows:
        return "None."
    lines = ["| Conversation | Context | Waiting for | Follow-up / deadline | Email |",
             "| --- | --- | --- | --- | --- |"]
    for row in rows:
        context = row.get("context") or row.get("request") or "Business context not recorded."
        source = "\n".join(filter(None, [row.get("topic"), ", ".join(row.get("correspondents", []))]))
        waiting_for = "Waiting for reply"
        if row.get("request"):
            waiting_for += ": " + row["request"]
        followup = row.get("recommended_action") or "Follow up when due"
        if row.get("due_at"):
            followup += "\nDeadline: " + row["due_at"]
        url = gmail_thread_url(state, state["cases"].get(row["case_id"], {}))
        lines.append("| " + " | ".join([*map(_cell, [source, context, waiting_for, followup]),
                                           "[Open email](" + quote(url, safe="/:#?=&%+@") + ")"]) + " |")
    return "\n".join(lines)


def _remaining_reasons(receipt):
    labels = {
        "unreconciled_cases": "conversations still need reconciliation",
        "preparation": "conversations still need reply preparation",
        "queue": "queued messages remain",
        "actionable_cases": "bot-actionable conversations remain",
        "due": "due workflow items remain",
        "unscanned_lanes": "inbox lanes were not scanned",
        "incomplete_scan": "the inbox scan is incomplete",
        "rechecks": "conversation rechecks remain",
        "operational_holds": "operational holds remain",
        "learning": "learning reconciliation remains",
        "draft_revisions": "draft revisions remain",
        "uncertain_sends": "send outcomes remain uncertain",
        "unresolved_drafts": "draft outcomes remain unresolved",
        "failed_sends": "send attempts failed",
        "failed_drafts": "draft attempts failed",
        "cleanup_pending": "mailbox cleanup actions remain",
    }
    result = []
    for key, value in receipt.get("remaining", {}).items():
        if key == "due_items" or not value:
            continue
        amount = len(value) if isinstance(value, (list, dict)) else value
        result.append(f"{amount} {labels.get(key, key.replace('_', ' '))}" if type(amount) is int
                      else labels.get(key, key.replace("_", " ")))
    return result


def _completed_markdown(state, run, events):
    rows = []
    labels = {"draft_created": "Gmail draft created and verified",
              "send_sent": "Email sent and verified",
              "send_accepted": "Email accepted by provider; delivery unverified"}
    seen = set()
    for event in events:
        if event.get("kind") not in labels or not event.get("case_id"):
            continue
        key = (event["kind"], event.get("case_id"), event.get("action_id"), event.get("candidate_id"))
        if key in seen:
            continue
        seen.add(key)
        rows.append((event["case_id"], labels[event["kind"]], event.get("at")))
    for action in state.get("mailbox_actions", {}).values():
        if action.get("status") != "verified" or not action.get("case_id"):
            continue
        verified_at = action.get("verified_at")
        if not verified_at or not (run.get("started_at") <= verified_at <= run.get("finished_at")):
            continue
        label = "Archived from Inbox" if action.get("kind") == "archive" else "Mailbox labels updated and verified"
        rows.append((action["case_id"], label, verified_at))
    if not rows:
        return "None."
    lines = ["| Conversation | Completed action | Completed at | Email |",
             "| --- | --- | --- | --- |"]
    for case_id, action, at in rows:
        case = state["cases"].get(case_id)
        if not case:
            continue
        topic = case.get("topic") or case.get("unresolved_ask") or "Conversation"
        url = gmail_thread_url(state, case)
        lines.append("| " + " | ".join([_cell(topic), _cell(action), _cell(at),
                                          "[Open email](" + quote(url, safe="/:#?=&%+@") + ")"]) + " |")
    return "\n".join(lines) if len(lines) > 2 else "None."


def owner_report(state):
    """Render every saved obligation; reading a report never changes mailbox state."""
    import inbox_engine as engine
    from inbox_store import hot_state
    state = hot_state(state)
    report = compact_report(state)
    active_lanes = {row["lane"] for row in report["coverage"]}
    scans = list(engine.inbox_priority.active_scans(state).values())
    if not scans and active_lanes == {"all"} and state.get("scan"):
        scans.append(state["scan"])
    discovered = {mid for scan in scans for mid in scan.get("message_ids", [])}
    unassessed = {mid for mid in discovered if mid not in state["seen_messages"]}
    covered = bool(report["coverage"]) and all(
        row["scan_complete"] and row.get("assessed_complete", not row["unprocessed_messages"])
        for row in report["coverage"])
    coverage = (f"{len(discovered) - len(unassessed)} of {len(discovered)} discovered messages assessed; "
                f"{len(unassessed)} remain unassessed.")
    runs = state.get("runs", {})
    latest = max(runs, key=lambda key: engine.iso(runs[key]["started_at"])) if runs else None
    run = runs.get(latest, {})
    receipt = run.get("finish_receipt") or run.get("cycle_result") or {}
    if not run.get("finished_at"):
        result = "RUNNING" if latest else "NOT RUN"
        result_detail = "Run still in progress; no completion receipt exists." if latest else "No run recorded."
    else:
        result = str(receipt.get("result") or (
            "success" if receipt.get("complete") is True else
            "incomplete" if receipt.get("complete") is False else "not recorded")).upper()
        result_detail = "Actual saved completion receipt: " + result + "."
    lines = ["### Run result", f"**{result_detail}**"]
    reasons = _remaining_reasons(receipt)
    if reasons:
        lines.append("Incomplete because: " + "; ".join(reasons) + ".")
    lines.append("**" + ("Saved scan coverage: " if covered else "Partial review: ") + coverage + "**")
    if not covered:
        lines.append("The tables contain all recorded open items; more may remain in unreviewed mail.")
    lines.append(f"Queued messages: {report['queued']}. " + (
        "Inbox scan complete." if report["coverage"] and all(row["scan_complete"] for row in report["coverage"])
        else "Inbox scan incomplete or not yet recorded."))
    for row in report["coverage"]:
        lines.append(f"- {_cell(row['lane'])}: {row['discovered_messages'] - row['unprocessed_messages']} of "
                     f"{row['discovered_messages']} assessed; {row['unprocessed_messages']} unassessed; "
                     f"{row['needs_action']} needing action; {row['blocked']} blocked. "
                     f"Scan {'complete' if row['scan_complete'] else 'incomplete'}; as of {_cell(row['as_of'])}.")
    queued = engine.inbox_priority.ordered_queue(state)
    if queued:
        lines.append("Unassessed messages:\n" + "\n".join(
            "- " + _cell(row["trigger_id"]) + " ([Open email](" +
            gmail_thread_url(state, row) + "))" for row in queued))
    owner_keys = {(row["case_id"], row.get("obligation_id")) for row in report["owner_actions"]}
    bot_rows = [row for row in action_table(state, include_bot=True)
                if (row["case_id"], row.get("obligation_id")) not in owner_keys]
    if bot_rows:
        lines.append("Inbox Manager work still pending:\n\n" + _attention_markdown(state, bot_rows))
    lines.extend(["### Needs your attention", _attention_markdown(state, report["owner_actions"]),
                  "### Waiting on others", _waiting_markdown(state, report["waiting_on_others"]),
                  "### Completed this run"])
    if not run.get("finished_at"):
        lines.append("None.")
    else:
        start, end = engine.iso(run["started_at"]), engine.iso(run["finished_at"])
        during = lambda value: bool(value and start <= engine.iso(value) <= end)
        scoped_ids = {case["id"] for case in state["cases"].values() if _case_in_scope(state, case)}
        candidates = state["events"].since(run["started_at"]) if hasattr(state["events"], "since") else state["events"]
        events = [event for event in candidates if during(event.get("at")) and
                  (not event.get("case_id") or event["case_id"] in scoped_ids)]
        sent = sum(event["kind"] == "send_sent" for event in events)
        accepted = sum(event["kind"] == "send_accepted" for event in events)
        drafts = sum(event["kind"] == "draft_created" for event in events)
        cleanup = [a for a in state.get("mailbox_actions", {}).values()
                   if a.get("status") == "verified" and a.get("case_id") in scoped_ids and
                   during(a.get("verified_at"))]
        archived = sum(a.get("kind") == "archive" and bool(a.get("inbox_message_ids", a.get("message_ids", [True]))) for a in cleanup)
        lines.append(_completed_markdown(state, run, events))
        lines.append(f"Receipt totals: {drafts} drafts created; {sent} sends verified; "
                     f"{accepted} send-acceptance receipts (delivery unverified); {archived} threads archived; "
                     f"{len(cleanup) - archived} other label actions verified.")
        lines.append("Bot cycle complete." if receipt.get("complete") is True else
                     "Bot cycle incomplete." if receipt.get("complete") is False else
                     "Cycle completion was not recorded.")
    problems = []
    for label, count in (
        ("bot cases needing action", report["bot_actionable_cases"]),
        ("cases needing responsibility assignment", report["unassigned_actionable_cases"]),
        ("uncertain sends", sum(report["send_results"].get(k, 0) for k in ("attempting", "unknown"))),
        ("unconfirmed drafts", sum(report["drafts"].get(k, 0) for k in ("creating", "unknown"))),
        ("held cases", len(report["attention"])),
        ("cleanup actions pending", report["cleanup_pending"]),
        ("case rechecks", len(report["reevaluations"])),
    ):
        if count:
            problems.append(f"{count} {label}")
    if report["resolved_cleanup_pending"]:
        lines.append("Resolved; mailbox cleanup still pending:\n" + "\n".join(
            "- " + _cell(row["topic"]) + ": " + _cell(row["reason"].replace("_", " ")) +
            " ([Open email](" + row["thread_url"] + "))" for row in report["resolved_cleanup_pending"]))
    if report["cleanup"]["retained"]:
        lines.append(str(len(report["cleanup"]["retained"])) + " conversations kept in Inbox under the approved policy; no cleanup retry is needed.")
    if problems:
        lines.append("Still pending: " + "; ".join(problems) + ".")
    return "\n\n".join(lines) + "\n"


def compact_work(state):
    import inbox_engine as engine
    full = engine.work(state)
    result = {k: v for k, v in full.items() if k not in {
        "cases", "due", "reevaluations", "recoverable_holds", "resume_scan", "resume_scans", "knowledge_gaps"}}
    result.update(cases=[summary_case(state, c) for c in full["cases"]], due=compact_due(state, full["due"]),
        reevaluations=[{k: v for k, v in row.items() if k != "case"} for row in full["reevaluations"]],
        recoverable_holds=[summary_case(state, c) for c in full["recoverable_holds"]],
        resume_scan=_scan(full["resume_scan"]), resume_scans=[_scan(row) for row in full["resume_scans"]],
        knowledge_gaps=[{k: row[k] for k in ("id", "question", "status")} for row in full["knowledge_gaps"] if row["status"] == "open"],
        lookup="lookup with case_id, thread_id, candidate_id, action_id, or gap_id for exact evidence")
    # Completed intake removes queue entries. Repeating work yields the next batch;
    # these limits never apply to the owner's complete action table.
    result["queue_total"] = len(result["queue"])
    result["cases_total"] = len(result["cases"])
    result["queue"] = result["queue"][:50]
    result["cases"] = result["cases"][:25]
    reconciliation = result.get("mailbox_reconciliation", [])
    result["reconciliation_total"] = len(reconciliation)
    result["mailbox_reconciliation"] = reconciliation[:25]
    result["more_work"] = (result["queue_total"] > len(result["queue"]) or result["cases_total"] > len(result["cases"])
                           or len(reconciliation) > 25)
    return result


def lookup(state, p):
    import inbox_store
    selectors = {k: p[k] for k in ("case_id", "thread_id", "candidate_id", "action_id", "gap_id", "observation_id", "revision_id") if p.get(k)}
    if len(selectors) != 1:
        raise ValueError("lookup needs exactly one case_id, thread_id, candidate_id, action_id, gap_id, observation_id, or revision_id")
    field, key = next(iter(selectors.items()))
    if field in {"case_id", "thread_id"}:
        case = state["cases"].get(key) if field == "case_id" else next(inbox_store.selected(state, "cases", thread_id=key), None)
        if not case:
            raise ValueError("unknown case")
        return {"case": case,
            "sources": {key: state["sources"].get(key) for key in set(case.get("source_ids", [])) |
                        {key for row in case.get("obligations", {}).values() for key in row.get("source_ids", [])}},
            "decisions": list(inbox_store.selected(state, "decisions", case_id=case["id"], status__in={"pending", "rule_requested"})),
            "candidates": [{k: c.get(k) for k in ("id", "status", "hash")} for c in inbox_store.selected(state, "candidates", case_id=case["id"])],
            "actions": [{k: a.get(k) for k in ("id", "status", "kind")} for a in list(inbox_store.selected(state, "actions", case_id=case["id"])) + list(inbox_store.selected(state, "mailbox_actions", case_id=case["id"]))]}
    collections = {"candidate_id": "candidates", "action_id": "actions", "gap_id": "knowledge_gaps", "observation_id":"observations", "revision_id":"draft_revisions"}
    item = state[collections[field]].get(key)
    if item is None and field == "action_id":
        item = state.get("mailbox_actions", {}).get(key)
    if item is None:
        raise ValueError("unknown " + field)
    return item
