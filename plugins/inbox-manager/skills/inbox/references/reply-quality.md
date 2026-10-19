# Reply quality and checker contract

This file owns claim support, usefulness, voice, candidate completeness, and independent
checking.

## A useful reply

A good inbox reply:

- answers the real ask early and accounts for every material question or obligation;
- performs the smallest email action that advances the job;
- preserves relevant prior commitments and dates;
- maps every material fact and promise to current evidence;
- asks only necessary questions the correspondent can answer;
- follows explicit voice instructions, then applicable learned preferences;
- contains no filler, hidden internal note, unfinished placeholder, or claim that an
  unperformed refund, booking, task, CRM update, or other action occurred.

Uncertainty about wording or preference can produce a useful owner draft. A claim that
depends on a missing material fact cannot appear in it; hedging does not make it safe.
Independently supported parts of a multi-part request may still form a useful owner draft,
with the unresolved decision recorded separately for the owner. Never present that partial
reply as complete resolution or use it to bypass a pending owner decision.

Draft the independently supported part rather than withholding the entire reply. Withhold the
body when the missing decision controls the whole response. A no-draft result must set
`no_draft_reason` to `already_resolved`, `no_reply_needed`, `another_owner`,
`decision_controls_whole_reply`, or `blocker`. Generic holding copy,
acknowledgements without an answer, and promises to investigate are not useful drafts.

Match the provider's supported body format. Plain-text email should read as ordinary email,
without Markdown bold markers, code fences, or headings copied from source documents.
Use richer formatting only when the provider's rendering is verified and the saved voice
calls for it. Formatting in a knowledge source is not an instruction for the reply.

## Candidate artifact

The reply stage returns the exact body, sender identity, To/CC/BCC, reply-thread identity,
attachment identities, claim-to-source mapping, commitments, remaining uncertainty, intended
case state, and the profile/source/case versions used. Any change to those fields creates a
new candidate and invalidates an older check.

An assessed candidate includes a short summary checked against the exact body and covered asks.
Describe what the draft answers or proposes; never claim a booking, refund or other outcome was
fulfilled merely because the reply discusses it. A later manual-content or coverage mismatch
makes that summary stale. Return `summary:true` only when that exact summary is supported.

There is no separate humanizer pass. Draft directly in the saved voice and check the whole
reply once it is complete.

For a requested revision, check the runtime-frozen actual merged replacement, including current
Gmail content and preserved non-conflicting manual edits. The desired target alone is not the
provider replacement. The owner answer authorizes only its mapped changes; it does not
authorize a clean rewrite of unrelated content or a send.

## Independent check

Every possible automatic send requires a fresh checker worker that:

- did not research or draft that candidate;
- receives the exact candidate and a compact evidence/authority packet;
- has read-only inspection tools and no email, runtime, or provider write capability;
- checks actual ask, evidence, recipients, attachments, privacy, commitments, authority,
  case freshness, duplicate/obsolete risk, and completion quality;
- binds its result to exact candidate, case, evidence, and authority versions.

If the host cannot isolate a checker this way, the candidate may become an owner draft but
cannot auto-send. The coordinator cannot substitute a self-check.

## Results and repair

The checker returns one semantic result: send-ready, draft-for-owner, repair, missing fact,
missing authority, or no reply. Store send-ready as runtime decision `send`, draft-for-owner
as `draft`, and the other outcomes as `reject`, preserving the specific semantic result in
the reason/evidence artifact. It explains the concrete defect without silently editing the
body.

One bounded repair may return to the reply stage, followed by a new checker worker. A second
failure becomes an owner draft, owner decision, or operational hold. New evidence or an
owner answer may produce a new candidate; repeated rewrites cannot reset the repair limit.

Send-ready means only that this exact reply is grounded and inside active authority. The
coordinator still must perform the live pre-send checks in `action-policy.md`.
