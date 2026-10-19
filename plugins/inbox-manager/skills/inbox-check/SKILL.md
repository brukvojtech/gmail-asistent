---
name: inbox-check
description: "Independently checks one exact Inbox Manager reply candidate against evidence, recipients, privacy, conversation state, and current authority. Use when inbox-triage requests review before a possible automatic reply or after the one allowed repair. Runs as a fresh reviewer; automatic sending additionally requires enforced read-only tools. It never edits or sends the reply."
---

# Inbox check

For the AI Kruh edition, read the [AI Kruh profile](../inbox/references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

Judge the candidate independently. Read
[`../inbox/references/reply-quality.md`](../inbox/references/reply-quality.md),
[`../inbox/references/action-policy.md`](../inbox/references/action-policy.md), and
[`../inbox/references/conversation-state.md`](../inbox/references/conversation-state.md).

The coordinator must invoke this skill in a fresh worker, distinct from the writer, with
only the exact saved candidate, complete relevant raw evidence, frozen assessment, current case
snapshot, and approved profile/authority projection. For assessed drafts use returned
`checker_evidence`; for revisions use `review_artifact`, including its actual `checked_content`
replacement payload, `review_packet` and authenticated `review_owner_answer`. Judge the actual
merged reply rather than just the requested target. Do not receive earlier verdicts. Use read-only inspection tools; never write, edit, or send.
Automatic sending additionally requires the host to enforce that the reviewer has no email,
draft, label, archive, send, or runtime write capability. When that capability boundary is
unproven, the separate reviewer may assess an owner draft, but cannot grant `send-ready`.

Engine `check.independent: true` attests that this exact candidate was actually reviewed by
a fresh, distinct worker. It does not attest tool isolation. Keep the separate configured
`transport.independent_check` false unless host-enforced isolation has been proven. Never
invent a reviewer or change the attestation merely to get past an engine rejection.

## Check

Verify all of the following:

1. The reply answers the real ask and moves toward the saved completion rule.
2. Every material fact and commitment maps to current evidence; conflicts and stale sources
   are resolved or exposed.
3. To/CC/BCC, sender identity, reply thread, attachments, and sensitive disclosures are
   correct for this case.
4. The exact reply category, recipients, promises, limits, follow-up behavior, and action
   are inside active owner authority.
5. No unreconciled owner edit, revision conflict, takeover, new material message, prior send,
   ambiguous send, or due state makes the candidate obsolete or duplicate. For a requested
   draft revision, the candidate preserves every unrelated current manual edit.
6. The body is complete, useful, in voice, free of placeholders/internal notes, and does not
   claim an unperformed business action.
7. When an assessed candidate contains a summary, it accurately describes that exact draft and
   covered asks. Proposed, drafted and fulfilled are distinct; unresolved siblings remain explicit.

## Result

Return exactly one result:

- `send-ready`: exact candidate is grounded and inside current automatic-send authority;
- `draft-for-owner`: useful truthful reply exists, but preference or judgment belongs to
  the owner;
- `repair`: one bounded wording/evidence defect the reply stage can fix;
- `missing-fact`: a named evidence gap prevents a truthful useful reply;
- `missing-authority`: a named owner decision or authority expansion is required;
- `no-reply`: replying would not advance the case or the ask is already handled.

Include concise reasons and the exact body/recipient/attachment/evidence/case/authority
versions checked. Record `send-ready` as engine decision `send`, `draft-for-owner` as
`draft`, and every repair/missing/no-reply result as `reject`; the reason and evidence
reference retain the semantic outcome. Supply all seven assessment booleans, plus `summary`
for an assessed candidate that contains it. Approval requires `summary:true`. Do not edit the
candidate or broaden authority. A fluent reply is not send-ready merely because it sounds
plausible.

## Exit

Finish after one result. One repair may return to `inbox-reply`, followed by a fresh checker.
A second failed check cannot be self-approved and becomes a draft, owner decision, or hold.
