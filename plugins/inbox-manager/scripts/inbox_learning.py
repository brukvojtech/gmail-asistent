"""Deterministic mechanics for passive inbox learning.

This module interprets no email semantics.  It validates whether a recorded
observation is safe to hand to the model-owned lesson stage, and preserves the
distinction between evidence, examples, and authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence


_STATUSES = {"pending", "edited_pending", "sent", "discarded", "missing"}
_LINKAGES = {"direct", "strong", "ambiguous"}
_ACTORS = {"member", "bot", "unknown", "other"}
_ACTOR_MODES = {"verified", "exclusive_editor", "observe_only"}
_LESSON_KINDS = {"example", "style", "factual_correction", "business_rule"}
_SCOPE_FIELDS = {"job", "request", "facts", "risk"}


def normalize_body(text: str) -> str:
    """Normalize transport-level line whitespace without rewriting content.

    Newline encodings, trailing spaces, whitespace-only lines, and surrounding
    blank lines are normalized.  Leading indentation, quoted history, repeated
    blank lines, signatures, and every non-whitespace character are preserved.
    """

    if not isinstance(text, str):
        raise TypeError("body must be a string")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip(" \t") for line in lines]

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def body_hash(text: str) -> str:
    """Return a SHA-256 hash of :func:`normalize_body` output."""

    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


def reconcile(
    candidate: Mapping[str, Any],
    observation: Mapping[str, Any],
    capability: Mapping[str, Any],
    bot_message_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and classify one draft observation.

    The returned mapping always has ``outcome``, ``learnable``, ``signal``,
    ``reason``, ``member_handled``, and ``final_body``.  ``member_handled`` is
    true only for a fully validated, reliably linked member send.  It never
    means that a discard, missing draft, or bot send handled the conversation.
    """

    if not isinstance(candidate, Mapping):
        return _result("invalid", reason="invalid_candidate")
    if not isinstance(observation, Mapping):
        return _result("invalid", reason="invalid_observation")

    status = observation.get("status")
    outcome = status if status in _STATUSES else "invalid"
    raw_final_body = observation.get("final_body")
    final_body = (
        raw_final_body
        if status == "sent" and isinstance(raw_final_body, str)
        else None
    )

    candidate_error = _validate_candidate(candidate)
    if candidate_error:
        return _result("invalid", reason=candidate_error, final_body=final_body)
    observation_error = _validate_observation(observation)
    if observation_error:
        return _result("invalid", reason=observation_error, final_body=final_body)
    capability_error = _validate_capability(capability)
    if capability_error:
        return _result(outcome, reason=capability_error, final_body=final_body)

    if (
        observation["mailbox"].strip().casefold()
        != candidate["mailbox"].strip().casefold()
    ):
        return _result(outcome, reason="mailbox_mismatch", final_body=final_body)
    if observation["thread_id"] != candidate["thread_id"]:
        return _result(outcome, reason="thread_id_mismatch", final_body=final_body)

    for field in ("candidate_id", "case_id", "trigger_id", "draft_id"):
        if field in observation:
            candidate_field = "id" if field == "candidate_id" else field
            if observation[field] != candidate[candidate_field]:
                return _result(outcome, reason=f"{field}_mismatch", final_body=final_body)

    evidence_mismatch = _identity_evidence_mismatch(candidate, observation["linkage_evidence"])
    if evidence_mismatch:
        return _result(outcome, reason=evidence_mismatch, final_body=final_body)

    created_at = _parse_timestamp(candidate["created_at"])
    observed_at = _parse_timestamp(observation["observed_at"])
    if observed_at < created_at:
        return _result(outcome, reason="observation_precedes_candidate", final_body=final_body)

    if observation["listing_complete"] is not True:
        return _result(outcome, reason="incomplete_listing", final_body=final_body)

    if status == "pending":
        return _result(outcome, reason="pending_no_decision")
    if status == "edited_pending":
        return _result(outcome, reason="edited_pending_no_decision")
    if status == "missing":
        return _result(outcome, reason="missing_is_undetermined")

    reliable_linkage = observation["linkage"] in {"direct", "strong"}
    if not reliable_linkage:
        return _result(outcome, reason="ambiguous_linkage", final_body=final_body)
    if not _has_evidence(observation["linkage_evidence"]):
        return _result(outcome, reason="missing_linkage_evidence", final_body=final_body)

    if status == "sent":
        sent_error = _validate_sent(candidate, observation, created_at, observed_at)
        if sent_error:
            return _result(outcome, reason=sent_error, final_body=final_body)
    elif status == "discarded" and "recipients" in observation:
        if _validate_recipients(observation["recipients"]):
            return _result(outcome, reason="invalid_observation_recipients")
        if _canonical_recipients(observation["recipients"]) != _canonical_recipients(
            candidate["recipients"]
        ):
            return _result(outcome, reason="recipient_mismatch")

    bot_ids = set()
    for message_id in bot_message_ids:
        if not _nonempty_string(message_id):
            return _result(outcome, reason="invalid_bot_message_id", final_body=final_body)
        bot_ids.add(message_id)

    sent_message_id = observation.get("sent_message_id")
    if observation["actor"] == "bot" or (
        status == "sent" and sent_message_id in bot_ids
    ):
        return _result(outcome, reason="bot_action_is_not_feedback", final_body=final_body)

    actor_reliable, actor_reason = _member_actor_is_reliable(observation, capability)
    member_handled = status == "sent" and actor_reliable

    if not actor_reliable:
        return _result(
            outcome,
            reason=actor_reason,
            member_handled=False,
            final_body=final_body,
        )

    if candidate["body"] is None:
        return _result(
            outcome,
            reason="comparison_text_expired",
            member_handled=member_handled,
            final_body=final_body,
        )

    if capability["invalidated"]:
        return _result(
            outcome,
            reason="learning_capability_invalidated",
            member_handled=member_handled,
            final_body=final_body,
        )
    if not capability["enabled"]:
        return _result(
            outcome,
            reason="learning_disabled",
            member_handled=member_handled,
            final_body=final_body,
        )
    if capability["actor_mode"] == "observe_only":
        return _result(
            outcome,
            reason="actor_capability_observe_only",
            member_handled=member_handled,
            final_body=final_body,
        )

    if status == "discarded":
        discard_reason = observation.get("discard_reason")
        if not _nonempty_string(discard_reason):
            return _result(outcome, reason="discard_without_supported_reason")
        return _result(
            outcome,
            learnable=True,
            signal="supported_discard",
            reason="member_discarded_with_reason",
        )

    normalized_final = normalize_body(raw_final_body)
    if normalized_final == normalize_body(candidate["body"]):
        return _result(
            outcome,
            learnable=True,
            signal="accepted",
            reason="member_sent_unchanged",
            member_handled=True,
            final_body=raw_final_body,
        )
    return _result(
        outcome,
        learnable=True,
        signal="edited",
        reason="member_edited_and_sent",
        member_handled=True,
        final_body=raw_final_body,
    )


def derive_lesson(
    candidate: Mapping[str, Any],
    observation: Mapping[str, Any],
    result: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an inspectable scoped lesson from a learnable reconciliation.

    ``proposal`` must contain ``kind``, ``summary``, and a ``scope`` mapping
    with job, request, facts, and risk dimensions.  Example and style lessons
    become active precedents.  Factual corrections and business rules are only
    proposals; the function never creates authoritative knowledge or expands
    action authority.
    """

    if not isinstance(result, Mapping) or result.get("learnable") is not True:
        raise ValueError("a lesson requires a learnable reconciliation result")
    if not isinstance(candidate, Mapping) or _validate_candidate(candidate):
        raise ValueError("lesson candidate is invalid")
    if candidate["body"] is None:
        raise ValueError("cannot derive a lesson after comparison text expired")
    if not isinstance(observation, Mapping) or _validate_observation(observation):
        raise ValueError("lesson observation is invalid")
    if not isinstance(proposal, Mapping):
        raise ValueError("lesson proposal must be a mapping")

    kind = proposal.get("kind")
    if kind not in _LESSON_KINDS:
        raise ValueError(f"unsupported lesson kind: {kind!r}")
    if not _nonempty_string(proposal.get("summary")):
        raise ValueError("lesson proposal requires a non-empty summary")

    scope = proposal.get("scope")
    if not isinstance(scope, Mapping):
        raise ValueError("lesson proposal requires a scope mapping")
    missing_scope = sorted(
        field for field in _SCOPE_FIELDS if not _scope_value_present(scope.get(field))
    )
    if missing_scope:
        raise ValueError("lesson scope is missing: " + ", ".join(missing_scope))

    signal = result.get("signal")
    if signal not in {"accepted", "edited", "supported_discard"}:
        raise ValueError("unsupported learning signal")
    if signal == "accepted" and kind != "example":
        raise ValueError("an unchanged acceptance supports only an example lesson")
    if result.get("outcome") != observation.get("status"):
        raise ValueError("reconciliation result does not match observation outcome")

    try:
        proposal_copy = copy.deepcopy(dict(proposal))
        json.dumps(proposal_copy, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("lesson proposal must contain JSON-compatible values") from exc

    status = "active" if kind in {"example", "style"} else "proposed"
    evidence = {
        "candidate_id": candidate["id"],
        "case_id": candidate["case_id"],
        "observation_id": observation["id"],
        "observed_at": observation["observed_at"],
        "mailbox": candidate["mailbox"],
        "thread_id": candidate["thread_id"],
        "draft_id": candidate["draft_id"],
        "sent_message_id": observation.get("sent_message_id"),
        "linkage": observation["linkage"],
        "linkage_evidence": copy.deepcopy(observation["linkage_evidence"]),
        "actor": observation["actor"],
        "actor_evidence": copy.deepcopy(observation["actor_evidence"]),
        "original_body_hash": body_hash(candidate["body"]),
        "final_body_hash": (
            body_hash(observation["final_body"])
            if observation.get("status") == "sent"
            else None
        ),
        "discard_reason": observation.get("discard_reason"),
    }
    versions = {
        "profile": copy.deepcopy(candidate["profile_version"]),
        "authority": copy.deepcopy(candidate["authority_version"]),
        "sources": copy.deepcopy(candidate["source_versions"]),
    }

    identity = {
        "kind": kind,
        "summary": proposal_copy["summary"].strip(),
        "scope": proposal_copy["scope"],
        "evidence": evidence,
        "versions": versions,
    }
    lesson_id = "lesson_" + hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]

    lesson = {
        "id": lesson_id,
        "kind": kind,
        "status": status,
        "authoritative": False,
        "summary": proposal_copy.pop("summary").strip(),
        "scope": copy.deepcopy(proposal_copy.pop("scope")),
        "signal": signal,
        "supporting_observations": [observation["id"]],
        "contrary_observations": [],
        "versions": versions,
        "evidence": evidence,
        "revision": 1,
    }
    proposal_copy.pop("kind", None)
    if proposal_copy:
        lesson["details"] = proposal_copy

    if kind == "example":
        if signal == "supported_discard":
            example_body = candidate["body"]
            polarity = "negative"
        else:
            example_body = observation["final_body"]
            polarity = "positive"
        lesson["example"] = {
            "body": normalize_body(example_body),
            "polarity": polarity,
        }
    return lesson


def prune(
    records: Iterable[Mapping[str, Any]],
    now: str | datetime,
    retention_days: int,
) -> list[dict[str, Any]]:
    """Expire full comparison bodies after the bounded retention window.

    Evidence and normalized hashes remain.  This bound applies to pending
    records too: a later member send can still be marked handled, but without
    the original text it cannot be graded as accepted or edited.
    """

    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days < 0
    ):
        raise ValueError("retention_days must be a non-negative integer")
    now_dt = _parse_timestamp(now) if isinstance(now, str) else now
    if not isinstance(now_dt, datetime) or now_dt.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime or ISO timestamp")
    cutoff = now_dt - timedelta(days=retention_days)

    pruned = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("records must contain mappings")
        item = copy.deepcopy(dict(record))
        timestamp = item.get("observed_at") or item.get("created_at")
        if timestamp is not None:
            record_dt = _parse_timestamp(timestamp)
            if record_dt <= cutoff:
                for field in ("body", "original_body", "final_body"):
                    value = item.get(field)
                    if isinstance(value, str):
                        item.setdefault(f"{field}_hash", body_hash(value))
                        item[field] = None
                item["comparison_bodies_pruned"] = True
        pruned.append(item)
    return pruned


def _result(
    outcome: str,
    *,
    learnable: bool = False,
    signal: str = "none",
    reason: str,
    member_handled: bool = False,
    final_body: str | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "learnable": learnable,
        "signal": signal,
        "reason": reason,
        "member_handled": member_handled,
        "final_body": final_body,
    }


def _validate_candidate(candidate: Mapping[str, Any]) -> str | None:
    for field in ("id", "case_id", "mailbox", "thread_id", "trigger_id", "draft_id"):
        if not _nonempty_string(candidate.get(field)):
            return f"invalid_candidate_{field}"
    body = candidate.get("body")
    if not isinstance(body, str) and not (
        body is None and _nonempty_string(candidate.get("body_hash"))
    ):
        return "invalid_candidate_body"
    if _validate_recipients(candidate.get("recipients")):
        return "invalid_candidate_recipients"
    if _timestamp_error(candidate.get("created_at")):
        return "invalid_candidate_created_at"
    for field in ("profile_version", "authority_version"):
        if not _version_present(candidate.get(field)):
            return f"invalid_candidate_{field}"
    if not isinstance(candidate.get("source_versions"), Mapping):
        return "invalid_candidate_source_versions"
    return None


def _validate_observation(observation: Mapping[str, Any]) -> str | None:
    for field in ("id", "mailbox", "thread_id"):
        if not _nonempty_string(observation.get(field)):
            return f"invalid_observation_{field}"
    if _timestamp_error(observation.get("observed_at")):
        return "invalid_observation_observed_at"
    if type(observation.get("listing_complete")) is not bool:
        return "invalid_observation_listing_complete"
    if observation.get("status") not in _STATUSES:
        return "invalid_observation_status"
    if observation.get("linkage") not in _LINKAGES:
        return "invalid_observation_linkage"
    if "linkage_evidence" not in observation:
        return "missing_linkage_evidence"
    if observation.get("actor") not in _ACTORS:
        return "invalid_observation_actor"
    if "actor_evidence" not in observation:
        return "missing_actor_evidence"
    return None


def _validate_capability(capability: Mapping[str, Any]) -> str | None:
    if not isinstance(capability, Mapping):
        return "invalid_learning_capability"
    for field in ("enabled", "exclusive_editor_confirmed", "invalidated"):
        if type(capability.get(field)) is not bool:
            return f"invalid_capability_{field}"
    if capability.get("actor_mode") not in _ACTOR_MODES:
        return "invalid_capability_actor_mode"
    if (
        capability["actor_mode"] == "exclusive_editor"
        and not capability["exclusive_editor_confirmed"]
    ):
        return "exclusive_editor_not_confirmed"
    return None


def _validate_sent(
    candidate: Mapping[str, Any],
    observation: Mapping[str, Any],
    created_at: datetime,
    observed_at: datetime,
) -> str | None:
    if not _nonempty_string(observation.get("sent_message_id")):
        return "invalid_sent_message_id"
    if observation["sent_message_id"] == candidate["draft_id"]:
        return "sent_message_id_reuses_draft_id"
    if not isinstance(observation.get("final_body"), str):
        return "invalid_final_body"
    if _validate_recipients(observation.get("recipients")):
        return "invalid_observation_recipients"
    if _canonical_recipients(observation["recipients"]) != _canonical_recipients(
        candidate["recipients"]
    ):
        return "recipient_mismatch"
    if _timestamp_error(observation.get("sent_at")):
        return "invalid_sent_at"
    sent_at = _parse_timestamp(observation["sent_at"])
    if sent_at < created_at:
        return "send_precedes_candidate"
    if sent_at > observed_at:
        return "send_follows_observation"
    return None


def _member_actor_is_reliable(
    observation: Mapping[str, Any], capability: Mapping[str, Any]
) -> tuple[bool, str]:
    actor = observation["actor"]
    evidence = observation["actor_evidence"]
    if actor == "member":
        if not _has_evidence(evidence):
            return False, "missing_actor_evidence"
        if (
            capability["actor_mode"] == "exclusive_editor"
            and capability["invalidated"]
            and not _has_independent_actor_evidence(evidence)
        ):
            return False, "exclusive_editor_assumption_invalidated"
        return True, "member_actor_verified"
    if actor == "unknown" and capability["actor_mode"] == "exclusive_editor":
        if capability["exclusive_editor_confirmed"] and not capability["invalidated"]:
            if _has_evidence(evidence):
                return True, "member_actor_inferred_from_exclusive_editor"
        return False, "unknown_actor"
    if actor == "other":
        return False, "other_actor_is_not_member_feedback"
    return False, "unknown_actor"


def _has_independent_actor_evidence(evidence: Any) -> bool:
    """Return whether evidence stands apart from an exclusive-editor assumption."""

    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("verified") is True:
        return True
    source = evidence.get("source")
    return _nonempty_string(source) and source != "exclusive_editor_assumption"


def _identity_evidence_mismatch(
    candidate: Mapping[str, Any], evidence: Any
) -> str | None:
    if not isinstance(evidence, Mapping):
        return None
    pairs = {
        "candidate_id": "id",
        "case_id": "case_id",
        "trigger_id": "trigger_id",
        "draft_id": "draft_id",
        "mailbox": "mailbox",
        "thread_id": "thread_id",
    }
    for evidence_field, candidate_field in pairs.items():
        if evidence_field not in evidence:
            continue
        left = evidence[evidence_field]
        right = candidate[candidate_field]
        if evidence_field == "mailbox" and isinstance(left, str):
            if left.strip().casefold() != right.strip().casefold():
                return "linkage_evidence_mailbox_mismatch"
        elif left != right:
            return f"linkage_evidence_{evidence_field}_mismatch"
    return None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _timestamp_error(value: Any) -> bool:
    if not _nonempty_string(value):
        return True
    try:
        _parse_timestamp(value)
    except ValueError:
        return True
    return False


def _validate_recipients(value: Any) -> bool:
    canonical = _canonical_recipients(value) if isinstance(value, list) else ()
    return (
        not isinstance(value, list)
        or not value
        or any(not _nonempty_string(recipient) for recipient in value)
        or len(set(canonical)) != len(value)
    )


def _canonical_recipients(recipients: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(recipient.strip().casefold() for recipient in recipients))


def _has_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, set)):
        return bool(value)
    return value is not None


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _version_present(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float))


def _scope_value_present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set)):
        return bool(value)
    return True
