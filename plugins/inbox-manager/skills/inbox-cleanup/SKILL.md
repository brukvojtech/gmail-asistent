---
name: inbox-cleanup
description: "Compatibility entry for applying an approved Inbox Manager job to historical Gmail in reversible, resumable chunks. Use when the user asks to clean an old backlog, apply current rules to older mail, or tidy thousands of messages. It routes backlog mode to inbox-triage; historical replies are preview-only by default."
---

# Inbox cleanup

For the AI Kruh edition, read the [AI Kruh profile](../inbox/references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

This is a thin compatibility wrapper. Select the configured inbox home, confirm the date or
query boundary and chunk size, then invoke `inbox-triage` in backlog mode.

Reuse the active job, source, state, action, provider, and reporting contracts. Record every
bounded provider page with runtime `scan`; resume its exact saved query, cutoff, and cursor
after interruption. Archive only through the verified undo-label-first rail. Never delete mail.

Historical conversations are preview-only for replies unless the owner explicitly selects
a current unresolved case after seeing it. A cleanup request never wakes an old conversation
with an unexpected automatic email and never enables follow-ups. Report archived, retained,
previewed, held, and remaining counts from durable state.

Finish when the selected chunk is accounted for and the next cursor is saved, or the bounded
backlog is complete. End with the [owner report](../inbox/references/owner-report.md); cleanup
counts never replace the complete attention table.
