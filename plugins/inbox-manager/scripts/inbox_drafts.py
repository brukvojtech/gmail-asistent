"""Reconcile draft presentation without inferring an owner decision or learning."""

import inbox_priority
from inbox_retrieval import verify_packet_inventory
from inbox_snapshot import build_snapshot


def reconcile_presence(state, case, packet):
    import inbox_engine as engine
    proof = verify_packet_inventory(state, packet)
    snap = build_snapshot(packet)
    if snap["thread_id"] != case["thread_id"]:
        raise ValueError("draft reconciliation belongs to another conversation")
    inbox_priority.require_scope(state, case, snap)
    if not engine.fresh(snap["observed_at"], state["config"]["transport"]["snapshot_max_age_seconds"]):
        raise ValueError("draft reconciliation requires fresh thread and inventory reads")
    if engine.iso(snap["observed_at"]) < engine.iso(case["snapshot"]["observed_at"]):
        raise ValueError("draft reconciliation precedes the saved conversation")
    present = set(snap["draft_ids"])
    records = [record for record in state["drafts"].values()
               if record["case_id"] == case["id"] and record.get("draft_id")
               and record.get("status") in {"pending", "absent"}]
    obligations = list(case.get("obligations", {}).values())
    known = set(case.get("owner_draft_ids", [])) | {r["draft_id"] for r in records}
    known.update(row["draft_review_id"] for row in obligations if row.get("draft_review_id"))
    released = set(case.get("released_draft_absence", []))
    missing = known - present - released
    for row in obligations:
        draft_id = row.get("draft_review_id")
        if draft_id and row["state"] != "handled":
            status = "absent" if draft_id not in present else "present"
            if row.get("draft_status") != status:
                row.update(draft_status=status, version=row["version"] + 1, updated_at=engine.stamp())
    for record in records:
        record["status"] = "absent" if record["draft_id"] not in present else "pending"
        record["presence_evidence"] = {"observed_at": snap["observed_at"], "inventory": proof}
    if missing:
        prior = case.get("draft_absence")
        case["draft_absence"] = {"draft_ids": sorted(missing), "observed_at": snap["observed_at"],
                                 "evidence_ref": proof["inventory_evidence_ref"]}
        # Preserve the exact candidate identity for a later verified send. The
        # absent record status removes its review prompt, not its provenance.
        # Absence retires an impossible review prompt, never the underlying ask.
        # Keep owner protection; neither a send nor an intentional discard was proved.
        if case["state"] != "handled":
            case["state"] = "waiting_owner"
            case["pending_decision"] = True
            if case.get("hold") in {None, "owner_decision"}:
                case["hold"] = "owner_decision"
        if not prior or prior["draft_ids"] != sorted(missing):
            engine.event(state, "draft_presence_changed", case_id=case["id"], draft_ids=sorted(missing),
                         outcome="absent_not_interpreted", inventory_ref=proof["inventory_evidence_ref"])
    else:
        case.pop("draft_absence", None)
    return {"case_id": case["id"], "absent_draft_ids": sorted(missing),
            "business_completed": False, "learnable": False}
