---
name: inbox-triage
description: "Runs one configured Inbox Manager cycle over new mail, obligations, due work, draft outcomes, owner answers, and source changes. Use for manual or scheduled inbox runs. Owns reviewed email actions and durable reporting, not setup or permission changes."
---

# Inbox triage

For the AI Kruh edition, read the [AI Kruh profile](../inbox/references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

Use `scripts/inbox_cycle.py` and read
[`cycle-contract.md`](../inbox/references/cycle-contract.md). Start with that contract and the
selected inbox profile/connection files; load another reference only when returned work enters
its branch. The host supplies real provider observations and semantic judgments. The runner
binds evidence, checks, actions, and receipts.

Use `inbox-learn` for returned feedback work, `inbox-research` for material context gaps,
`inbox-check` for a candidate, and `feedback-loop.md` for owner answers or draft revisions.
Use `archive-state.md` only for exceptional recovery. A background run never asks a live
question or starts setup; save its precise issue and continue independent work.

## Run one cycle

1. **Begin once.** Confirm the connected mailbox and claim the run. Stop for a wrong account,
   paused inbox, stale scheduled binding, or live lease.
2. **Discover within saved scope.** Use each returned lane's exact query, cutoff, and cursor,
   including old/read mail. Primary is the default; only an approved profile opens other
   categories. Save each page before continuing. Out-of-scope history stays saved and inactive.
3. **Refresh tracked work.** Process `mailbox_reconciliation` even when discovery is empty.
   Read the complete thread including Sent and drafts, then call `observe`. When it returns
   `assessment_required:false`, continue without rewriting the case. Otherwise use `reconcile`
   with the returned case basis. Reuse the provider packet while current.
Before creating an owner question, inspect already-linked conversations and, when the subject
   points to support or another active matter, make one targeted search for its outcome. A
   verified completed request needs reconciliation, not retroactive approval. Preserve unrelated
   obligations; the fact that support acted never creates a general exception policy.
4. **Assess once.** Apply [handling-rules.md](../inbox/references/handling-rules.md),
   read only evidence that could change the answer, and write to the
   [reply-quality.md](../inbox/references/reply-quality.md) bar. Research and reply may stay
   inline; their skills are bounded assistance when useful. Submit one shared `assessment`
   through intake for discovered work or reconcile for tracked work. It carries the business
   patches, checked source versions, supported scope, precise questions and optional exact
   reply plus summary. Reuse the current packet/reference; the runtime binds durable records,
   candidate dependencies and threading. Do not repeat separate case/preparation/candidate
   submissions for the same judgment.
   Select only bot `needs_action` reply obligations as draft scope. Split a supported answer
   from any unresolved owner execution/decision as described in the shared assessment contract.
5. **Check, act and verify.** Give the saved candidate and frozen `checker_evidence` to a
   fresh distinct checker. Check the summary with the body and coverage. Make the required fresh
   complete pre-action read, reserve, perform and confirm only the exact returned provider action.
   Reconcile unknown results before another write. Complete cleanup through its opaque step and
   actual readback.
6. **Exhaust bounded work.** Continue `next` until the selected preparation, reconciliation,
   actions, learning, and cleanup are done or each blocker is concrete. Owner/correspondent waits may
   remain after a successful bot cycle.
7. **Finish and report.** Call `inbox_cycle.py finish --format markdown` even when owner work
   remains. Retain its complete output in private durable run evidence and apply the approved
   presentation in `owner-report.md`: focused chat or the full dashboard. Do not reinterpret
   completion. The command also returns a saved-state dashboard when finishing fails. If the
   run could not begin, use the read-only report command and disclose the concrete blocker.
   Retrieve truncated output before presenting it. A hard host interruption is exposed as
   unfinished work on the next invocation.

## Durable judgment

Track every material ask as a stable obligation with responsibility type, next action, state,
deadline/wake event, source dependencies, and completion evidence. Omitted IDs never erase
saved work. Archiving never resolves an obligation, and one completed ask never closes siblings.
Use [`conversation-state.md`](../inbox/references/conversation-state.md) for direct-owner Sent
outcomes, takeover, reopening, due work, and completion.

Assessment belongs to the bot even when the business decision belongs to the owner.
Supported partial replies and precise owner questions coexist; a decision that controls the
whole reply gets researched options and a recommendation. An unchanged `observe` result skips
rewriting only when `assessment_required:false`. Mail, source/knowledge, owner-answer, due-work
and draft-outcome changes still invalidate dependent work.

## Replies and revisions

The coordinator owns packet/assessment persistence, source receipts and action transactions.
Research returns evidence; reply returns proposed text and a factual draft summary. Neither
writes runtime/provider state. Follow the shared assessment and revision sequencing in the
[cycle contract](../inbox/references/cycle-contract.md); use returned schemas for field shapes.
A partial draft preserves unresolved obligations and cannot bypass a pending owner decision.
Automatic sending still requires proven host-enforced checker isolation and current authority;
otherwise prepare a checked owner draft. No inbox answer authorizes refunds, bookings, CRM
edits, or other business-system writes.

For authenticated batch answers, store the mapped parent event before provider writes, refresh
only affected threads/drafts, and revise bot-linked drafts through the revision transaction.
Submit one desired revision target; the runtime freezes the actual merge for independent check.
Pre-action refresh must reproduce that checked payload. A conflict leaves that draft untouched
and does not block independent revisions. Answering never implies sending.

## Report

Follow [`owner-report.md`](../inbox/references/owner-report.md), including the selected chat
presentation. Preserve every unresolved obligation, coverage, verified draft/action status,
cleanup failure and Gmail link in private report evidence. Full mode ends with the saved
numbered question list; focused mode shows only the selected item with its original mapping.
Notification preferences never suppress durable reporting or conceal a processing failure.

## Durable Gmail transport

Before provider processing, read [gmail-adapter.md](../inbox/references/gmail-adapter.md). Use the bundled decoder and durable project-local orchestration; temporary files must never be required by a future scheduled run.
