# Conversation state

This file owns durable case state, due work, escalation, takeover, correction, and resumption.

## Business states

- **Needs action:** the bot owns a supported next move within the current job and authority.
- **Waiting for correspondent:** they own the next move; retain any due time or wake event.
- **Waiting for owner:** an owner-only decision or action, draft, or takeover blocks automation.
- **Handled:** the approved completion rule is met with evidence.

Operational holds sit beside these states: unavailable connector/source, live lease conflict,
invalid checker boundary, and uncertain send. Do not hide operational failure inside a
business outcome.

Every case retains mailbox/conversation identities, snapshots, candidates, receipts, and
stable obligations. Each obligation has an ask, responsibility type (owner/bot/correspondent),
responsible display name, next action, state,
due time, source dependencies, and version. Closing an obligation requires evidence; omitted
IDs remain saved. A case cannot be handled while an obligation is still open. Waiting on
another person is a valid business state, not an unfinished bot cycle. Unassessed queued mail
remains unprocessed. Legacy summary
fields remain readable, but new work uses explicit obligations.

Owner decisions bind the exact question to its obligation scope and message context. A saved
report mapping additionally binds each displayed question number to the decision version,
cases, obligations, and affected draft revisions.
Pending drafts, takeover and uncertain sends remain thread-level protections. A disjoint
obligation may receive one reviewed owner draft while another awaits a business decision;
it may not auto-send through that decision. Source changes and owner answers queue a recheck
with an identity that must be acknowledged after a fresh read.

## Wake and reopen

New mail, a due time, an authenticated owner answer, or a verified relevant source update may
reopen work. Read the latest thread before acting. Link different threads into one issue only
with supporting identity and context; a shared subject is insufficient.

An unchanged waiting case stays processed without repeating research or drafting. A confirmed
draft/sent outcome is also a wake event. Notification delivery, snooze, or mute is not.

Archiving changes Gmail presentation, not state. A repeated notice about a known unresolved
obligation reopens that obligation rather than becoming generic notification noise.

Dismissing or forgetting one named reminder closes only that obligation's reminder with an
explicit owner receipt. Preserve sibling obligations and case history; do not claim the business
request was resolved, authorize a reply, or archive the conversation because one reminder was
dismissed. Notification snooze or mute never removes the obligation from the every-run action
table.

## Owner-held work

Once a draft is offered for owner review, it stays owner-owned. Edits, an unchanged pending
draft, a missing draft without matched sent evidence, or ambiguous actor/linkage do not
release the case to automatic sending. A complete fresh inventory plus current thread including
Sent can retire an impossible “review this draft” prompt; the underlying ask and owner protection
remain. Use the packet-based presence reconciliation in `cycle-contract.md`. New evidence may
mark the draft stale. An authenticated Codex answer may revise the affected bot-linked draft
through the protected three-version merge; unrelated manual edits survive and conflicts leave
that draft untouched. New evidence alone must not overwrite a draft or generate another
automatic answer to the same ask.

When a reliably linked owner reply is sent, reconcile it, cancel the stale candidate, record
the resulting state, and evaluate later incoming messages normally. An explicit “I'll take
over” is stronger: block replies and follow-ups until an authenticated explicit release.

Owner decisions have three scopes: one case, standing rule, or takeover. One authenticated
parent event may answer several mapped decisions; clear answers apply independently. A one-case
answer reopens only its affected cases against their newest state. Standing changes use setup
approval.

## Due work and completion

Waiting cases may carry a due time even when automatic follow-ups are off. At due time,
re-evaluate under current evidence and authority. After the approved follow-up limit, stop
sending and surface any material obligation.

Handled requires the saved completion rule. “Reply sent” often means waiting, not handled.
When policy allows ending with no reply, record `closed without response` separately from a
resolved outcome.

Record handled state independently from any reversible archive result; archive failure does
not erase the business outcome, and archive success cannot hide pending work.

Unchanged decisions use the approved reminder cadence for additional alerts. Every run still
reports all unresolved owner obligations. Alert immediately only for a material urgency change.
A snooze or mute suppresses only notification nudges and never changes the decision or case
state. One blocked case never stops unrelated work.
