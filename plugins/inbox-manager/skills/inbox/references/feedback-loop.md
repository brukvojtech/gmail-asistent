# Research, answers, draft revisions, and learning

This file owns the two feedback routes after Inbox Manager prepares a reply. The owner can
edit or send the Gmail draft, or answer the numbered questions in the authenticated Codex
task. Both routes preserve the same case, candidate lineage, evidence, and authority limits.

## Prepare before asking

Apply [handling-rules.md](handling-rules.md) and [reply-quality.md](reply-quality.md) before
asking. The normal [shared assessment](cycle-contract.md#one-assessed-case-submission) records
supported content, current evidence and precise questions once. Draft independently supported
parts; if a decision controls the whole response, show researched options and a recommendation.
An unchanged current assessment may be reused until dependent evidence or work changes.

## Ask once, answer naturally

The final report lists all verified current drafts, then ends with one numbered question
list. Each question includes enough context to answer, a recommendation, and every affected
draft. The saved report mapping binds each displayed number to its decision version, cases,
obligations, and draft revisions. The owner can answer several numbers in one ordinary or
dictated message; names and topics also work when unambiguous.

Store the authenticated parent answer event and its report reference before provider writes.
Apply clear mapped answers independently and preserve ambiguous ones for clarification. A
partial answer does not answer a whole decision: retain every missing subquestion as pending
owner work before revising the draft. One failed draft update does not roll back another successful answer or revision. An answer is
case-specific unless the owner explicitly requests a reusable rule; action authority still
changes only through setup approval.

## Revise current drafts safely

After saving answers, refresh affected threads/drafts and follow the
[target-only revision path](cycle-contract.md#existing-draft-revision). Author the desired target
once; the runtime derives conservative local changes from the exact bot base and freezes the
actual merged replacement before independent checking. Preserve unrelated manual content,
recipients/subject, threading, attachments and inline MIME. New material drift requires a new
candidate/check. An ambiguous affected span or overlap leaves that draft untouched; do not
reintroduce a hand-authored patch as a workaround. Unaffected drafts continue independently.

Reserve before updating and confirm complete readback. If the provider cannot update the complete
payload safely, keep the existing draft and report the blocker. A fresh read reduces simultaneous
edit risk but cannot make a provider without conditional updates atomic. `revision-reconcile`
checks a fresh complete packet and receipt against an unknown update; it returns no write/retry.
Missing or sent drafts are never blindly recreated. Legacy explicit patch inputs and saved
in-flight revisions retain their original bindings and recovery semantics.

Answering a question does not send email. Automatic sending retains its separate authority,
fresh independent check, identity, privacy, recipient, and pre-send requirements.

## Learn from either route

Reliably attributed owner-sent replies can teach from their final content. Unsent Gmail edits
remain work in progress. Authenticated Codex answers can immediately record a scoped fact,
procedure, writing preference, or explicit correction, even when no draft was sent. A one-case
exception remains case evidence; it does not become policy. Inferred business rules remain
proposals for owner approval.

Keep source event, scope, freshness, contradictions, and revision history. Current owner
instructions and authoritative sources win over learned notes. Candidate preparation records
which notes it used so a corrected, forgotten, or outdated note reopens only dependent work.
The owner can inspect, search, forget, exclude, pause, resume, and maintain this knowledge. Run
`python3 <plugin-root>/scripts/inbox_engine.py schema --command <exact-command>` when first using
that command in the loaded runtime; reuse it until the runtime changes or rejects the contract; the user does not
need to operate the structured payload.

Copied email bodies are retained for 90 days by default, except narrowly scoped unresolved
action recovery material. Useful scoped knowledge remains until corrected, forgotten, or
outdated. Compact operational history is retained for one year. Pruning comparison bodies
must preserve the identities, hashes, outcomes, receipts, and knowledge provenance needed for
safe reconciliation.

For indexed storage, maintenance, migration, and offline backup commands, read [storage](storage.md).

Cycle `next.learning` retains eligible sent observations until knowledge is recorded, including
when a previous run stopped early. Read only the selected observation with `lookup --observation-id ID`. After extracting
useful notes, `knowledge-record` marks that observation processed. If it offers no reusable
knowledge, use leased `knowledge-reviewed` with the observation ID and reason. Do not create
a note merely to clear the queue.

If saved answers are stale because the provider read expired, refresh and reconcile affected
threads first, then resubmit the exact saved batch with `resume:true`. This reuses the original
authenticated answer and retries only unresolved children; it never reapplies successful ones.
Do not ask the owner to dictate the same answer again or invent a new authenticated event.
Changed answer text or approval needs a new real owner message.

If source or profile changes make a never-reserved ready revision obsolete, use `revision-retire` with its ID and reason, then prepare a fresh candidate. Attempting or unknown updates must use `revision-reconcile` instead.
