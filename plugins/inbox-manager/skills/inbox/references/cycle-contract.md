# One processing cycle

Use `python3 <plugin-root>/scripts/inbox_cycle.py <command> --home <absolute-home>
--input <private-json-file>`. JSON may instead arrive on stdin. Successful output is
`{"ok":true,"result":...}`; errors are structured on stderr, exit 2. The facade calls no
provider or scheduler. Supply actual observations and your semantic assessment; never
invent receipts, independent reviews, or host invocation evidence.

Load the narrow input contract with `inbox_engine.py schema --command <command>` once when
first using that command in the loaded runtime. Reuse it during the run; reload after a plugin
change or a contract rejection. Schemas own field shapes. This guide owns sequencing and meaning.

## Begin

`begin` takes `mailbox`, optional fresh `run_id`, and optional lease `minutes` (1–90,
default 15). It returns `run_id`, `token`, `expires_at`, UTC `cutoff`, compact `work`,
profile file references, approved sources, and ordered `discovery` scan plans. Each plan
contains `lane`, `query`, `window_end`, `page_cursor`, and `resume`. Use these exact scope queries: Primary only by default; other categories or all Inbox only
when explicitly selected in the approved profile. Older and already-read mail is included. Resume unfinished lanes independently.
Read the profile files; fetch source content only when a selected case needs it.
Use `run_id` and `token` unchanged in subsequent commands. Renew through engine `renew`
before expiry when necessary, passing `{run_id,token}` on stdin for another 15 minutes;
never reuse a completed run ID.

For owner follow-through, optional `case_ids` selects 1–50 existing in-scope cases and returns
no discovery plans. It is a manual bounded run; scheduled runs always use full scoped discovery.
`finish.complete` then describes those selected cases, while the owner report remains global.
An active cycle can handle the affected cases directly without starting another lease.

`next` takes the lease and returns remaining compact work. `mailbox_reconciliation` lists
tracked conversations to read even if no new inbound message was discovered, with `case_id`,
`thread_id`, existing `trigger_id`, `case_basis`, and saved context/obligations. Read including
Sent; preserve the inbound trigger for scope. Process all returned batches. `finish` lists
unreconciled tracked cases as incomplete processing.

Normal status, `work`, and report reads are compact and read-only. `work` is a bounded processing
batch with total and more-work indicators. After
intake, request the next batch until selected bot work is exhausted. This batching never caps
the complete every-run owner action table. Use full diagnostic output only for explicit
troubleshooting.

A scheduled begin additionally takes `registry:{root,mailbox,task_id,binding_id}`,
`provider_observation`, and `scheduler_readback`, exactly as described in
[operations-contract.md](operations-contract.md). The resolved home must match `--home`.
The facade records dispatch before claiming the engine; a rejected claim records a skipped
run without releasing another run's lease. A manual begin is not proof of native wake-up.

## Provider packet

`packet` is the complete input to the bundled snapshot helper:

```json
{
  "complete": true,
  "mailbox": "support@example.com",
  "thread_id": "actual-provider-thread-id",
  "trigger_id": "actual-inbound-message-id",
  "observed_at": "oldest-actual-read-completion-time-covering-both-arrays",
  "automated": false,
  "messages": ["complete raw message objects from the verified provider adapter"],
  "drafts": ["all complete raw draft objects from the verified provider adapter"]
}
```

The array strings above denote objects, not literal payload values. Preserve full bodies,
identities, recipients, timestamps, labels and headers from the provider card's adapter.
Normalize message/draft fields to `id`, `thread_id`, `from`, `to`, `cc`, `subject`, `body`,
and `headers`; drafts also need `bcc`. Keep every extra returned field and exact empty values.
`automated` is the assessment from actual message evidence. Consult only the
[fresh-provider-snapshot section](runtime-contract.md#fresh-provider-snapshot) if the adapter shape needs clarification.
Do not pass a summary, a fabricated empty draft array, or a precomputed fingerprint.
The facade validates and hashes the packet itself.
It canonicalizes `label_ids`, native `labelIds`, and `labels` arrays of label ID strings to
`label_ids`, and header names case-insensitively. Contradictory aliases or case-varied header
values reject the observation. Bodies, recipients, attachments, header values and extra
provider fields remain exact; category movement remains fresh scope evidence.

Capture complete provider results programmatically and pass the real objects on noninteractive
stdin or in a private reusable `--input` file. Reuse that packet while it remains current; do
not make the model rewrite bodies, headers, hashes, or identical JSON between stages. Keep full
evidence private and print compact facts and receipts.

When drafts can only be listed globally, use the bundled [retrieval helper](retrieval-contract.md)
to resume and persist the inventory once **outside** the loop over threads, then build each packet
from it. A thread-reading helper must not list global
drafts internally. Share one fresh inventory across a batch of simultaneous thread reads;
after draft creation/sending or when freshness expires, refresh the inventory for the next
batch. Label-only writes do not invalidate its identity. Follow the canonical
[retrieval freshness rules](retrieval-contract.md): actual thread
readback must follow the reserved label action, while an unchanged draft inventory may retain
its real read time subject to the freshness limit. Keep `thread_observed_at` and
`drafts_observed_at` separately; `observed_at` remains the earlier time. Never refresh a
cached value's timestamp. Changed draft identity/content blocks cleanup.

## Intake

`intake` accepts the lease and optional `lane`, `query`, `window_end`, `pages`, `threads`.
For discovery pages, carry the matching begin plan's lane, query, cutoff, and saved cursor;
never narrow it with unread or recent-mail filters.
Each page has `page_cursor`, `complete`, `next_cursor`, `entries`, `provider_evidence`.
An entry has actual `trigger_id`, `thread_id`, `received_at`. Save ordered observed pages
before reading subsequent pages; preserve an interrupted scan's exact query/cutoff/cursor.

The normal thread item is `{packet,assessment}` or `{packet_ref,assessment}`. Use the shared
assessment below instead of separate case, preparation and candidate submissions. Case fields inside
the assessment are `case_id`, `unresolved_ask`,
`next_action`, display `responsible`, `responsibility_type` (`owner`, `bot`, or `correspondent`), optional `linked_cases`, `link_evidence`, `due_at`, `state`,
`outcome`, `outcome_evidence`, `obligations`, `source_ids`, `recheck_id`, `urgency`
(`normal` or `urgent`, based on current evidence), `topic`, `context`, `recommendation`,
`thread_url`, and `thread_url_evidence`. Save enough context to understand the request later;
retain actual provider thread IDs so the report can always build account-bound Gmail links.
Context describes the business request, promise, or decision; fetch timestamps and “thread read”
receipts belong in evidence, never in the owner-facing context.

Each obligation patch uses stable `id`, `ask`, display `responsible`, `responsibility_type`,
`next_action`, `state`, optional
`due_at` and approved `source_ids`; completion needs `outcome_evidence`. An obligation that
only reviews an existing draft records its actual `draft_review_id`; manual drafts may also be
retained as case `owner_draft_ids`. These identities must occur in the complete observation. Omitted IDs remain saved.
Use `waiting_owner` for owner decisions, `needs_action` for bot work, `waiting_correspondent`
for external waits, and `handled` for proved completion. No separate operations reference is
needed for ordinary intake. Do not omit pending asks from the assessment.

The result has `scans`, ordered `results`, and `errors`. Failed items stay queued; inspect
errors and continue independent threads. A current recheck ID is acknowledged only with a
fresh complete packet. Omitting an obligation never deletes it.

For packets carrying the helper's current inventory proof, intake reconciles draft presence.
An absent draft loses its stale review prompt only after a fresh complete inventory and thread
including Sent. Absence never proves discard, sending, learning feedback, or business completion.
Business obligations remain and automatic recreation requires an owner decision. Existing
untyped legacy review prompts are corrected during the next supported semantic assessment;
never close the underlying request merely because its draft disappeared.

## Reconcile tracked work

`reconcile` takes the lease, exactly one current `packet` or `packet_ref`, and the same
`assessment` used by intake. Set `expected_basis` to the opaque current `case_basis` from
returned work. Add actual observed `message_ids` and a `reason` explaining which asks,
promises, questions and outcomes those messages establish. Completion still needs evidence
for each obligation; omitted siblings survive. Stale basis rejects the whole case assessment.
Unchanged cases use `observe`; reassess only when it returns `assessment_required:true`.
Continue all returned reconciliation batches before finish.

Use `resolved_decision_ids` only when a newer provider-confirmed Sent reply from this mailbox
actually settles every obligation in that pending decision. The runner binds those IDs to the
case, sender, Sent label, message time, and selected obligations. Sender claims in incoming
mail cannot grant authority. Explicit takeover and operational holds remain in effect.

When a related conversation proves the same requested work has already completed, first save its
handled assessment and evidenced case link. To retire a now-redundant pending question, add
`related_outcomes:[{case_id,packet,message_ids,reason}]` to the assessment alongside
`resolved_decision_ids`. Each packet must be fresh, complete and unchanged from the handled linked
case. Mark every affected obligation handled with outcome evidence. This records completion only;
it does not approve a policy exception, teach owner preferences, or remove independent holds.

## One assessed-case submission

Read [handling-rules.md](handling-rules.md) for case judgment,
[job-and-knowledge.md](job-and-knowledge.md) for research boundaries and
[reply-quality.md](reply-quality.md) for the reply and summary quality bar. Research and writing
may stay in the coordinator; use bounded read-only assistance only when it earns its cost.
A separate exact-artifact checker remains required.

Submit business meaning once: stable obligation patches, checked/unavailable/not-needed sources,
supported bot `obligation_ids`, precise owner `questions`, `preparation_evidence` and one
`disposition`: `draft_ready`, `needs_owner_answer`, `no_reply` or `blocked`. Include the exact
version actually read for each checked registered source, and exact knowledge dependencies
when used. Record connected-source observations through engine `source` before assessment.
Expired local evidence uses the existing selected-source renewal path; read changed bytes before
submitting a current assessment. Never substitute the registry's newer version for evidence
used to write older prose.

Select only obligations with `responsibility_type:"bot"` and `state:"needs_action"` in the
assessment's `obligation_ids` for a draft. Mentioning an owner obligation in the reply does not
make it bot-owned draft work. When an ask contains both a supported answer and an unresolved
owner execution/decision, keep two stable obligations: one bot reply obligation for the supported
answer, and one owner obligation for the remaining action/question. For example, explaining
approved rescheduling options is bot reply work; authorizing or changing the booking remains
owner work. Select the reply obligation only. Keep owner questions linked to the owner obligation;
drafting, checking or revising the answer never automatically closes that owner's work.

For a new case, `expected_basis` is null. For an existing case, copy the current opaque basis
from returned work. Store provider evidence once through this submission; reuse the
`packet_ref` already returned by `observe` or intake. References never refresh observation time.

A draft-ready assessment may include a `candidate` and must then include a short `summary`.
The candidate supplies exact body, subject, deliberate recipients/CC, attachments, mode, kind,
writer and fresh candidate ID. The runtime binds case, covered obligations, evidence/dependency
versions and unambiguous observed reply IDs/headers from this assessment. Do not author those
same fields again inside the nested candidate.

Example semantic assessment inside an intake thread item:

```json
{
  "case_id": "case-42",
  "expected_basis": null,
  "unresolved_ask": "How do I enable exports?",
  "next_action": "Explain the documented steps",
  "responsible": "bot",
  "responsibility_type": "bot",
  "disposition": "draft_ready",
  "preparation_evidence": "Read help-v1, Exports section",
  "sources_checked": [
    {"source_id": "help", "version": "help-v1", "status": "checked", "reason": "Supports export steps"}
  ],
  "questions": [],
  "summary": "Draft explains the documented steps to enable exports.",
  "candidate": {
    "candidate_id": "reply-case-42-v1",
    "body": "Open Settings, choose Exports, and select Enable.",
    "subject": "Re: Export setup",
    "recipients": ["customer@example.com"],
    "mode": "draft",
    "kind": "reply",
    "writer_id": "actual-writer-identity"
  }
}
```

Use the real source IDs, versions and provider recipients. Multi-ask cases also supply stable
`obligations` and selected supported `obligation_ids`. Supported partial replies and owner
questions can coexist in a draft-ready assessment. If one decision controls the whole reply,
omit the candidate and summary, save researched options/recommendation and the precise question.
No-reply/blocked results include their concrete `no_draft_reason`.

Admission is staged per case: invalid structure, stale evidence or candidate admission rejects
that case without a half-written assessment. Other batch cases and saved discovery pages can
succeed. Read each result/error. An identical accepted replay returns its receipt while its
basis/dependencies remain current; changed content needs the current basis and a new candidate.
Later checker rejection retains valid case/evidence/questions; repair the rejected reply rather
than replaying an old check.

The result's `candidate` contains the immutable `candidate_id`, `candidate_hash` and exact
candidate. Pass it together with returned `checker_evidence` (including the complete raw
provider packet and frozen assessment) and approved profile/authority to the fresh checker.
Do not replace that evidence with the writer's summary or a mutable latest preparation.

`draft_first:true` still requires prepared substantive work. Existing homes enter that workflow
only through authenticated `adopt-workflow`; assessment does not change authority or migrate a
home. Separate `case`, `preparation` and `candidate` shapes remain supported for legacy/recovery
and selected-source renewal. They are not the normal new-case path; load their schemas only
when that branch is needed. Saved in-flight IDs, hashes and receipts stay unchanged.

## Batch owner answers

Cycle `answers` stores one authenticated parent event, the saved report reference, and distinct
mapped answers before any provider write. The runtime validates report numbers, decision
versions, cases, obligations, and provenance and returns independent per-answer results. Map
ordinary language in the host; do not make the owner operate the JSON contract. Preserve
ambiguous answers and continue clear ones. Replays of the same event do not duplicate decisions,
draft updates, or knowledge.

After storing answers, reuse the active run or call `begin` with only affected `case_ids`.
Refresh current threads and drafts, record applicable scoped knowledge, and prepare/revise
affected replies before `finish` in the same invocation.

## Existing-draft revision

After authenticated owner mapping, submit one full desired target through `revision-candidate`,
with its owner reference, exact draft ID, evidence/dependencies and deliberate recipients/subject.
Omit `requested_changes`. The runtime derives conservative unique-line edits against the exact
last confirmed bot revision, merges them with the fresh current draft, and returns `review_artifact`.
The checker reviews its actual `checked_content` and complete replacement `payload`, including
preserved manual content, attachments/inline MIME and threading, together with the frozen raw
`review_packet` and authenticated `review_owner_answer`. Bind the check to the returned candidate
ID/hash; checking only `requested_content` is insufficient.

At `revision-prepare`, supply a new complete read and the actual independent review. The merge
must reproduce the checked replacement exactly, with unchanged conversation/scope evidence.
Drift requires retiring the unreserved candidate and making a new target/candidate/check. Apply
only the returned `provider_patch`; confirm complete readback through `revision-confirm`.
Unique unrelated repeated lines and signatures can survive. An ambiguous affected span,
same-line overlap, missing base or conflicting protected fields leaves the draft untouched;
do not hand-author a replacement patch to bypass that result. Continue independent drafts.

Unknown/attempting updates use `revision-reconcile`, never blind retry or retirement. Missing or
already-sent drafts are not recreated. Providers without conditional updates still have a race
after the fresh read; exact readback is required. Legacy explicit `requested_changes` and saved
in-flight revisions retain their original contract and hashes. See [feedback-loop.md](feedback-loop.md)
for owner scope, retirement and recovery.

## Cleanup through the runner

`cleanup` takes the lease, `case_id`, and fresh complete `packet`. It returns actual label
`mutations`, status, business state, and an opaque `step_id` when a readback is required.
Perform exactly those mutations through the approved provider. Call `cleanup` again with
that `step_id`, the actual fresh `packet`, and `provider_ref`. The runner carries action ID,
attempt, reservation, and verification internally. Forward returned objects programmatically;
do not reconstruct IDs, message bodies, or receipts in prose. A failed provider call uses the
saved `step_id` and `error`; resume from actual readback before attempting another write.

Continue until `verified`, `retained` under policy, or a concrete blocker. An archive uses
verified undo-label placement before removing Inbox. `reconcile_required` means read back the
existing reservation using the returned step before any new write. Neither a saved handled
state nor an earlier verified Attention action proves archiving. Detailed exceptional recovery
is in [archive-state.md](archive-state.md).

## Candidate and independent review

Normal candidate creation is part of the assessed-case submission above. A fresh distinct checker
receives the exact saved candidate, complete returned checker evidence and approved authority.
Apply [reply-quality.md](reply-quality.md). An assessed candidate's short summary is checked with
its body and selected coverage; it describes what the draft proposes or answers, never an action
already fulfilled. A later manual-content or coverage mismatch suppresses stale summary claims.
Legacy drafts use a conservative covered-ask fallback.

Separate cycle `candidate` remains available for legacy/recovery and a diagnosed repair. Its schema
includes the full evidence/threading fields because those are not inherited from an assessment.
Every repair has a new candidate ID and fresh check; do not change a checked artifact in place.

## Prepare, perform, confirm

`prepare` accepts the lease, `candidate_id`, `mode`, freshly fetched `packet`, and actual
`review`. Review fields: `candidate_id`, `candidate_hash`, `reviewer_id`, `independent`,
`decision`, `assessments`, `reason`, `evidence_ref`. Assessments are the seven booleans
`grounded`, `answers_ask`, `identity`, `recipients`, `privacy`, `authority`, `no_loop`.
Assessed candidates with a summary also require `summary:true` for approval.
The check must reference the hash the checker actually saw; never rebind an old review.

The returned `provider_payload` is authoritative: `mailbox`, `thread_id`,
`reply_message_id`, `subject`, `body`, `to`, `cc`, `bcc`, `attachments`, `reply_headers`.
Map field names through the verified provider card. Do not alter content after review.
The reservation exists before the host performs the provider action.

`confirm` accepts the lease, `candidate_id`, `mode`, and actual `result`.
For a created draft, use `{status:"created",provider_receipt,draft}`. `draft` is the full
raw readback with `id`, `from`, `thread_id`, `subject`, `body`, `to`, `cc`, `bcc`, `headers`,
and `attachments` when present. All must match the saved candidate. An ID is insufficient.
A definitively failed creation uses `status:"failed"`, `definitive_not_created:true`,
`provider_receipt`, and `reason`. Otherwise record `status:"unknown"` and `reason`.
Unknown creation must be reconciled; never issue a duplicate external action.
After a verified creation, read the complete fresh conversation and drafts, then call
`observe`. An exact known bot draft with unchanged thread and dependencies returns
`assessment_required:false`; continue without restating the case or preparation. Draft-only
confirmation cannot prove that no new mail arrived. A verified scoped revision can carry its
answered obligations into draft review from its complete readback. Follow returned work;
changed mail, manual edits, dependencies or authority still require assessment or recovery.

For sends, use the existing exact `send-result` fields in
[runtime-contract.md](runtime-contract.md), including reserved action ID and actual
receipt/readback evidence. The facade does not weaken transport or uncertainty checks.

## Finish

`finish --format markdown` takes the lease and optional actual `scheduler_readback`. Use its full Markdown as the final response on every run, including unchanged runs. JSON remains available for programmatic consumers. A failed Markdown finish emits a read-only saved dashboard with an explicit failure warning, without releasing another run or claiming freshness. It releases only its
own lease and returns `report_id`, the complete rendered `owner_report`, `complete`, `degraded`, `remaining`, and separate
`business_work_remaining`/`business_work`. Pending owner decisions or verified drafts can
remain after a successfully completed cycle. The rendered coverage section separates each lane's
`scan_complete`, `assessed_complete`, and `fully_processed`, with its cutoff, discovered/unprocessed counts,
bot actions, blocks, and waiting-owner count. Assessed complete means every discovered
message was assessed even when actions remain. Fully processed means assessed with no bot
action or operational block remaining; owner-held work remains unresolved. Report the
cutoff and any incomplete category instead of claiming the whole inbox is clear. Unreadable queued work, unfinished scans,
rechecks, recoverable failures, and uncertain actions remain visible.

The scheduled binding is recovered from the saved run, never supplied again at finish.
The processing result is persisted and sent to that registry run. Missing or invalid
scheduler readback leaves next-run timing unverified. Exact finish retries reuse the saved
result. Present user decisions using the approved chat presentation in [owner-report.md](owner-report.md), retaining the rendered report's saved question numbers and mapping. Its
immutable mapping keeps stable references for later batch answers; do not expose internal JSON
as the normal inbox experience.

The report still returns every unresolved owner obligation, including carried items,
and a drafts section that distinguishes candidates from verified provider drafts. Render each
with saved context, status, recommendation, deadline, new/carried state, and a verified Gmail
link. Notification eligibility is separate and may be cadence-filtered.

The CLI avoids repeating the rendered report as structured fields. Use `finish --full` only for diagnostics; the Python API retains both representations.
