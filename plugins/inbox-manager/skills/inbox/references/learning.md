# Learning

This file owns Gmail/Codex feedback provenance, edit interpretation, knowledge scope,
contradictions, retention, and standing-rule proposals. Learning is private durable memory for
one inbox job, not model-weight training or proof that an accepted reply was factually correct.

## Observations

| Observed outcome | Interpretation and effect |
|---|---|
| Owner sent the candidate substantially unchanged with reliable actor/linkage | Positive evidence that the approach was useful in this context; retain a scoped example. |
| Owner edited and sent a reliably linked reply | Compare the final version; separate style, factual correction, and one-off business choice. |
| Draft edited but pending | Work in progress; preserve it and do not grade or overwrite it. |
| Draft untouched | No decision observed; age is not negative feedback. |
| Explicitly confirmed discard | Record non-use; apply negative learning only when a reason or meaningful replacement supports it. |
| Draft missing without a confidently matched sent reply | Ambiguous; suppress immediate recreation and reconcile later. |
| Different sent reply or uncertain actor | Learn only reliably linked elements; otherwise record ambiguity without adaptation. |
| Bot sent its own reply without an owner correction | Execution evidence only; no positive learning credit. |
| Authenticated Codex answer | Record explicit scoped facts, procedures, corrections, preferences, or case instructions immediately; no Gmail actor evidence is required. |

Match by mailbox, conversation, candidate, recipients, time, and normalized content—not
subject alone. Record direct, strong-inference, or ambiguous linkage and actor evidence.
Another automation must not be labeled as owner feedback.

Gmail removes a sent draft and creates a new sent-message identity. Missing draft does not
mean discard. Complete/paginated listings and bounded reconciliation are required; temporary
absence or read failure proves nothing.

An exclusive-editor assumption is usable only when the owner confirms its exact mailbox
scope and the system records it. Invalidate it when another writer appears. Without reliable
actor evidence, preserve observations without adapting send-versus-draft choices.

## Scoped knowledge

Keep the original candidate until reconciliation so edits can be compared. Ignore transport
formatting, quoted history, and routine signature changes where appropriate. Store only the
minimal accepted example, fact, procedure, preference, or correction after comparison, with:

- applicability across job, request, facts, recipients, and risk;
- supporting and contrary cases;
- direct/inferred provenance and observation time;
- profile, authority, source, and candidate versions;
- revision history and current active/paused/revoked state.

Explicit scoped facts, procedures, and corrections from the authenticated owner may be
immediately active. Automatically adopt low-risk preferences such as shorter openings or an
already-approved explanation style. These may affect send-versus-draft judgment only inside
an existing approved reply category with current facts, recipients, limits, and a fresh
independent check.

Never learn new authority, disclosure, recipient classes, refunds, promises, business actions,
follow-up rules, sources, or higher limits. Do not use a hardcoded acceptance count to unlock
sending. Explicit instructions and current authoritative knowledge win. Contrary evidence
marks affected knowledge conflicted until a new scoped record resolves or supersedes it.

One-case exceptions remain case evidence. Repeated or inferred business decisions become an occasional concrete proposal: show supporting cases
and exactly how future behavior would change. Bundle nonurgent proposals in the digest.
Applying one requires owner review and the setup amendment path.

## Retention and controls

Persist candidate/draft/message identities, normalized hashes, observation times, linkage,
outcome, and minimal knowledge provenance. Do not build a second mailbox archive. Copied
email bodies default to 90 days, with a narrow unresolved-action recovery exception. Useful
knowledge stays until corrected, forgotten, or outdated; compact operational history stays
one year. Prune other full temporary comparison bodies when no longer needed.

The owner can inspect/search through `knowledge-search` and `knowledge-lookup`; forget, exclude,
pause, or resume through `knowledge-forget`; and run bounded `knowledge-maintain` on demand.
Pausing learning does not pause inbox management. Controls are available without becoming a
mandatory daily review queue.
