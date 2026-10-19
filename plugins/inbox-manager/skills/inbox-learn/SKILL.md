---
name: inbox-learn
description: "Reconciles Inbox Manager candidates with observed Gmail outcomes and authenticated Codex answers, then maintains owner-controlled scoped knowledge. Use when returned work includes feedback reconciliation or the owner asks to inspect, search, forget, exclude, pause, resume, or maintain learning. It never mutates mail or expands authority."
---

# Inbox learning

For the AI Kruh edition, read the [AI Kruh profile](../inbox/references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

Turn reliable owner feedback into bounded evidence about future replies. Feedback may come
from a reliably attributed sent Gmail reply or an authenticated Codex answer. Read
[`../inbox/references/learning.md`](../inbox/references/learning.md) and
[`../inbox/references/feedback-loop.md`](../inbox/references/feedback-loop.md). Use the
runtime knowledge operations; never change email, case authority, or standing instructions.

## Reconcile Gmail feedback

If no candidate awaits an outcome, skip mailbox reconciliation. Otherwise build one complete
draft inventory when the provider requires mailbox-level listing and reuse it across pending
candidates. Read full bodies only for the relevant case, changed or ambiguous outcomes, or
the decision being assessed.

Match a candidate to complete draft and Sent evidence by mailbox, conversation, lineage,
recipients, time, and normalized content. Record direct, approved strong-inference, or
ambiguous linkage and actor evidence. Gmail removes a sent draft and creates a new Sent
identity, so absence alone proves neither send nor discard. Deduplicate every observation.

Reliably attributed owner-sent replies can teach from the final content. Unsent edits are
work in progress and stay preserved. Bot sends are execution evidence only.

## Reconcile Codex answers

Use the authenticated owner event already saved by the review flow; do not require Gmail
actor evidence or a sent draft. Deduplicate that parent event before deriving knowledge.
Separate the answer into case instructions, explicit scoped facts or procedures, writing
preferences, and requested standing rules. A case-specific answer stays with that case.

## Record the smallest useful knowledge

Save only the minimal example, fact, procedure, preference, or correction with scope,
supporting and contrary evidence, provenance, freshness, revision history, profile/source/
authority versions, and applicability. Explicit scoped facts, procedures, corrections, and
low-risk preferences from the owner may be usable immediately inside existing authority.
Inferred or repeated decisions that would change policy remain standing-rule proposals.

Never infer a new recipient class, disclosure, refund, promise, business action, follow-up
rule, send category, source, or higher limit. An exception in one case is evidence for that
case, not policy. Current instructions and authoritative sources beat learned knowledge;
contradictions mark the affected records conflicted until a new scoped record resolves them.

## Controls and retention

Use `knowledge-search` and `knowledge-lookup` to show records and provenance. The owner may
forget, exclude a case, pause, or resume through the matching `knowledge-forget` action, and
may request bounded `knowledge-maintain` work.

Copied email bodies use the configured 90-day default, except narrowly scoped material still
needed to reconcile an unresolved action. Useful knowledge lasts until corrected, forgotten,
or outdated. Compact operational history lasts one year. Preserve only the identifiers,
hashes, outcomes, receipts, and provenance needed after full comparison bodies expire.

## Exit

Every event is deduplicated and marked reliable, pending, or ambiguous. Adopted knowledge is
scoped, inspectable, owner-controlled, and recorded as a dependency when it affects a preparation.
Rule and authority changes remain proposals for their existing approval paths.
