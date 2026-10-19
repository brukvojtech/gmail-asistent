"""Category ordering and honest inbox coverage; no provider calls or mailbox writes."""

from __future__ import annotations

from datetime import datetime


TABS = ("primary", "updates", "promotions", "social", "forums")


def policy(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"enabled_tabs", "verified_ref", "processing_tabs", "scope_ref"}:
        raise ValueError("invalid inbox_priority fields")
    tabs = value.get("enabled_tabs", [])
    if (not isinstance(tabs, list) or any(tab not in TABS for tab in tabs)
            or len(tabs) != len(set(tabs))):
        raise ValueError("enabled_tabs must list distinct supported Gmail tabs")
    if tabs and ("primary" not in tabs or not isinstance(value.get("verified_ref"), str)
                 or not value["verified_ref"].strip()):
        raise ValueError("enabled tabs require Primary and actual setup confirmation")
    scope = value.get("processing_tabs", ["primary"])
    if (not isinstance(scope, list) or not scope or len(scope) != len(set(scope)) or
            any(tab not in (*TABS, "all") for tab in scope) or "all" in scope and len(scope) != 1):
        raise ValueError("processing_tabs must select distinct Gmail categories or all")
    if scope != ["primary"] and not str(value.get("scope_ref", "")).strip():
        raise ValueError("broader processing scope requires explicit owner scope_ref")
    return {**value, "enabled_tabs": [tab for tab in TABS if tab in tabs], "processing_tabs": scope}


def lane_queries(value):
    tabs = policy(value)["processing_tabs"]
    if tabs == ["all"]:
        return {"all": "in:inbox"}
    return {tab: "in:inbox category:" + tab for tab in TABS if tab in tabs}


def case_in_scope(state, case, snapshot=None):
    """Retain admitted archived work; an observed category move overrides admission.

    Unknown legacy work waits for scoped discovery. Scope exclusion never completes
    or deletes a business obligation, and urgency never expands the owner's scope.
    """
    lanes = lane_queries((state.get("config") or {}).get("inbox_priority"))
    if "all" in lanes:
        return True
    snap = snapshot or case.get("snapshot", {})
    categories = snap.get("categories")
    membership = state.get("thread_scope", {}).get(case.get("thread_id"))
    if membership and not scan_in_scope(state, membership.get("lane"), membership):
        membership = None
    # A later ID-only scan can admit an old conversation moved into Primary.
    # A fresh supplied pre-action snapshot always takes precedence.
    if (snapshot is None and membership and membership.get("observed_at") and snap.get("observed_at")
            and timestamp(membership["observed_at"]) > timestamp(snap["observed_at"])):
        return membership.get("lane") in lanes
    if categories:
        return bool(set(categories).intersection(lanes))
    if membership:
        return membership.get("lane") in lanes
    ids = set(snap.get("message_ids", []))
    return any(ids.intersection(scan.get("message_ids", []))
               for scan in active_scans(state).values())


def require_scope(state, case, snapshot=None):
    if not case_in_scope(state, case, snapshot):
        raise ValueError("conversation outside approved processing scope or scope not yet verified")


def active_scans(state):
    return {lane: scan for lane, scan in state.get("inbox_scans", {}).items()
            if scan_in_scope(state, lane, scan)}


def scan_in_scope(state, lane, scan):
    """Old lane names alone are not evidence of the current processing scope."""
    try:
        return scan.get("query") == query_for(
            (state.get("config") or {}).get("inbox_priority"), lane, scan["window_end"])
    except (KeyError, TypeError, AttributeError, ValueError):
        return False


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("inbox scan cutoff requires a timezone")
    return parsed


def query_for(value, lane, cutoff):
    queries = lane_queries(value)
    if lane not in queries:
        raise ValueError("scan lane is not enabled by the approved inbox priority")
    return queries[lane] + " before:" + str(int(timestamp(cutoff).timestamp()))


def discovery(state, cutoff):
    value = (state.get("config") or {}).get("inbox_priority")
    result = []
    for lane in lane_queries(value):
        previous = state.get("inbox_scans", {}).get(lane)
        resume = bool(previous and not previous["complete"] and scan_in_scope(state, lane, previous))
        end = previous["window_end"] if resume else cutoff
        query = query_for(value, lane, end)
        if resume and previous["query"] != query:
            raise ValueError("inbox priority changed during an unfinished scan; finish or explicitly restart it")
        result.append({"lane": lane, "query": query, "window_end": end,
                       "page_cursor": previous["next_cursor"] if resume else None, "resume": resume})
    return result


def ordered_queue(state):
    lanes = list(lane_queries((state.get("config") or {}).get("inbox_priority")))
    ranks = {lane: n for n, lane in enumerate(lanes)}
    from inbox_store import selected
    tids = {row["thread_id"] for row in state["queue"].values()}
    cases = {case["thread_id"]: case for case in selected(state, "cases", thread_id__in=tids)}
    return sorted((row for row in state["queue"].values() if case_in_scope(state,
                   cases.get(row["thread_id"], {"thread_id": row["thread_id"],
                       "snapshot": {"message_ids": [row["trigger_id"]]}}))), key=lambda row: (
        ranks.get(row.get("lane"), len(ranks)), -timestamp(row["received_at"]).timestamp()))


def coverage(state):
    result = []
    for lane in lane_queries((state.get("config") or {}).get("inbox_priority")):
        scan = active_scans(state).get(lane)
        ids = set(scan.get("message_ids", [])) if scan else set()
        unprocessed = {mid for mid in ids if mid not in state["seen_messages"]}
        from inbox_store import selected
        tids = {state["seen_messages"].get(mid) for mid in ids} - {None}
        cases = [case for case in selected(state, "cases", thread_id__in=tids)
                 if case_in_scope(state, case) and ids.intersection(case.get("snapshot", {}).get("message_ids", []))]
        pending = sum(case["state"] == "needs_action" for case in cases)
        blocked = sum(bool(case.get("hold") and case["hold"] != "owner_decision") for case in cases)
        result.append({"lane": lane, "as_of": scan["window_end"] if scan else None,
                       "scan_complete": bool(scan and scan["complete"]),
                       "assessed_complete": bool(scan and scan["complete"] and not unprocessed),
                       "fully_processed": bool(scan and scan["complete"] and not unprocessed and not pending and not blocked),
                       "discovered_messages": len(ids), "unprocessed_messages": len(unprocessed),
                       "needs_action": pending, "blocked": blocked,
                       "waiting_owner": sum(case["state"] == "waiting_owner" for case in cases)})
    return result
