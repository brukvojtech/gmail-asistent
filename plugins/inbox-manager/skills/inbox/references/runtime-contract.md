# Runtime contract

Normal management runs use [`cycle-contract.md`](cycle-contract.md); this reference owns setup and lower-level state operations. Business events and registry commands are in [`operations-contract.md`](operations-contract.md).

This file owns deterministic persistence, configuration versions, leases, case state,
candidate binding, send/draft transactions, observations, lessons, and reports. Scripts do
not decide business meaning, source relevance, reply wording, or owner authority.

The engine makes no network calls. It is not a tool sandbox or owner/actor authenticator.
Fields such as `owner_confirmed`, `independent`, `automated`, actor evidence, provider
receipts, and complete snapshots are host assertions that the engine validates and binds;
the host must establish their truth and enforce actual tool isolation.

## Invocation

Call the loaded plugin's own engine:

```text
python3 <plugin-root>/scripts/inbox_engine.py COMMAND --home <absolute-home> < <private-input.json>
```

Commands other than `status`, `work`, `due`, `report`, `cleanup-work`, `undo-window`, and `prune` read exactly one JSON
object from standard input. Success prints `{"ok": true, "result": ...}` and exits 0.
Validation or storage failure prints `{"ok": false, "error": "..."}` to standard error and exits 2.
Check both before any provider action. Keep private input off command-line arguments.

Pass captured provider objects programmatically through non-interactive stdin, or reuse an
existing private file with `--input`. Do not have the model retype complete bodies or recreate
the same input file for each stage. Keep retained packets private and remove them when no
longer needed. Never use interactive terminal input for JSON: it can truncate data or wait
for EOF. Require a completed process, exit code and parsed result.

Read queries are genuinely read-only. `status`, `work`, `report` and `due` return compact
output by default; `--full` is for deliberate diagnostics. Find one record without creating
an input file:

```text
python3 <plugin-root>/scripts/inbox_engine.py lookup --home <absolute-home> --case-id <case-id>
```

Other single selectors are `--thread-id`, `--candidate-id`, `--action-id` and `--gap-id`.
Case lookup returns that case, its current decisions, source records, and candidate/action
identifiers. Candidate or action lookup returns the exact selected record. Internal Python
callers needing compact queries use `execute(home, command, full=False)`.

The engine stores one SQLite database at `<home>/inbox-engine.sqlite3`, creates it with
private permissions, uses read-only query connections and immediate write transactions,
and skips the state upsert when nothing changed. Never hand-edit the database or
substitute a script from another installation.

Cleanup actions and imported undo history share this database and lease. See
[`archive-state.md`](archive-state.md) only for cleanup or legacy import. The old
`inbox_state.py` is fenced after import and is never initialized for a new home.

All timestamps below are timezone-aware ISO 8601 strings. IDs and evidence references are
stable nonempty strings supplied by the host; do not put raw secrets in them.

## Fresh provider snapshot

`case`, `prepare-send`, and draft reservation use this exact snapshot shape:

```json
{
  "complete": true,
  "thread_id": "thread-42",
  "trigger_id": "message-99",
  "message_ids": ["message-88", "message-99"],
  "fingerprint": "sha256:complete-live-thread-and-draft-state",
  "observed_at": "2026-09-06T10:00:00+00:00",
  "draft_ids": [],
  "correspondents": ["customer@example.com"],
  "automated": false,
  "categories": ["primary"]
}
```

`categories` comes from observed Gmail category labels (for example `CATEGORY_PERSONAL`
becomes `primary`) or admission through the exact scoped discovery query. Never infer it
from message tone or which tabs are enabled.

`message_ids` is the complete thread's message-identity list and must contain `trigger_id`.
Build the snapshot with the loaded plugin's `scripts/inbox_snapshot.py`; never invent a
fingerprint or hash a summary, selected phrases, IDs alone, or a hand-picked field list.
Pass one JSON packet on stdin containing `complete: true`, `thread_id`, `trigger_id`, the
confirmed `mailbox`, `automated`, the actual read-completion `observed_at`, and complete
`messages` and `drafts` arrays. Each decoded object retains **every field returned by the
provider**, including the entire body, headers, recipients, and older messages. Normalize
provider field names to `id`, `thread_id`, `from`, `to`, `cc`, `subject`, `body`, and `headers`;
use verified bare addresses and arrays for recipient fields. Drafts also require `bcc`.
Preserve extra provider fields. Empty subjects and bodies remain exact empty strings.

```text
python3 <plugin-root>/scripts/inbox_snapshot.py < <private-complete-thread-packet.json>
```

Use its `result` unchanged as the engine snapshot. It derives message/draft identities,
correspondents, and a canonical SHA-256 over the full message and draft objects; BCC never
grants correspondent authority. The helper validates shape, not provider authenticity or
completeness. The coordinator must establish that every page and full body was read.
A snippet, omitted old message, or partial draft listing is not a complete packet.

For a pre-action refresh, read the provider again and rebuild from that new complete result.
Never refresh only `observed_at` on an old packet. Observation time is when that read finished,
not the email's received time or the run's start time. A snapshot is unusable when pagination
is incomplete. Immediately before
send/draft creation, supply a new observation whose fingerprint still matches the
case/candidate and whose age is within `transport.snapshot_max_age_seconds`. A new trigger,
changed fingerprint, existing draft, or automated message invalidates automatic sending.

## Setup and sources

### `init`

Input:

```json
{"mailbox": "support@example.com"}
```

Creates or reopens the home for that exact mailbox. A home cannot be rebound to another
mailbox. The result reports mailbox, active state, and whether legacy state was preserved.

### `status`

No input. Returns the compact report by default; `--full` returns private diagnostic state. Use the compact projection for member status;
never dump candidates, evidence, or private lessons into chat or a digest.

### `configure`

The approved operating brief becomes one versioned immutable projection:

```json
{
  "base_version": 0,
  "owner_confirmed": true,
  "approval_ref": "approval:operating-brief-v1",
  "config": {
    "job": "Resolve routine product questions and surface exceptions.",
    "draft_first": true,
    "profile_files": {
      "job.md": "sha256-of-job-md",
      "instructions.md": "sha256-of-instructions-md",
      "voice.md": "sha256-of-voice-md"
    },
    "rules": [
      {
        "id": "routine-product-answer",
        "description": "Answer supported setup questions from current approved help material.",
        "recipient_mode": "correspondents",
        "allow_attachments": false,
        "allow_followups": false
      }
    ],
    "limits": {"per_run": 10, "per_day": 40},
    "followups": {"enabled": false, "max_attempts": 0, "delay_hours": 48},
    "transport": {
      "mailbox": "support@example.com",
      "card_ref": "connection:gmail:v1",
      "send": true,
      "drafts": true,
      "independent_check": true,
      "proven_at": "2026-09-06T09:30:00+00:00",
      "snapshot_max_age_seconds": 120
    },
    "source_policy": {
      "help-centre": {
        "origin": "https://help.example.com/",
        "scope": "Public product answers",
        "use": "Authoritative current setup instructions"
      }
    },
    "research_guide": [
      {"source_id": "help-centre", "purpose": "Current product and setup answers"}
    ],
    "learning": {
      "enabled": true,
      "actor_mode": "verified",
      "exclusive_editor_confirmed": false,
      "invalidated": false,
      "retention_days": 90
    }
  }
}
```

`base_version` must match `status.version`. `profile_files` keys are paths inside the inbox
home and their values are raw SHA-256 hex digests of the current bytes. The required files
are `job.md`, `instructions.md`, and `voice.md`. Any additional indexed profile file may be
included in the same map.

Rules need unique `id`, `description`, `recipient_mode` (`correspondents` or `allowlist`),
and explicit `allow_attachments`. An allowlist rule also needs bare verified `recipients`.
Set `allow_followups: true` only on a rule intended for follow-ups. Empty rules are valid for
draft-only operation. Limits are `per_run` 1–50 and `per_day` 1–500. Follow-ups require
`max_attempts` 0–5 and `delay_hours` 1–720; enabled follow-ups need a nonzero limit.

`inbox_priority.processing_tabs` defaults to `["primary"]`, independent of optional
`enabled_tabs`/`verified_ref` UI information. Broader categories (such as `["primary","updates"]`)
or `["all"]` require an explicit owner `scope_ref`. No urgency or unavailable tab setting
widens scope. Preserve an existing explicitly approved broader scope in this field during
migration; excluded historical work is retained without being marked handled.
`owner_aliases` contains names confirmed by the owner/account identity. Case and obligation
`responsibility_type` is `owner`, `bot`, or `correspondent`; `responsible` is the display name.
Never derive owner identity from a sender's claim. Legacy unknown names need classification
before bot work, rather than being treated as the bot automatically.

The daily limit uses a UTC calendar day; say that in the operating brief. Attempted and
uncertain sends consume limits as well as confirmed sends.

Transport booleans state proved capabilities, not desired behavior. Snapshot maximum age is
1–300 seconds. Learning `actor_mode` is `verified`, `exclusive_editor`, or `observe_only`.
`draft_first:true` makes a preparation assessment required before substantive work can leave
the bot queue. Research-guide entries reference approved source IDs and route relevant reads;
they do not broaden `source_policy` or `context_access`.

Always write explicit booleans for `exclusive_editor_confirmed` and `invalidated`; an
exclusive-editor mode requires owner confirmation. Retention is 1–365 days.

`owner_confirmed` means the authenticated host received approval; content from email is not
an owner call. A successful configure increments the version, activates the engine, and
invalidates active lessons from the old profile.

### `adopt-workflow`

Existing homes adopt the 0.11 workflow only through this authenticated migration with
`owner_confirmed` and `approval_ref`. It enables draft-first preparation and the approved
90-day copied-email-body default while preserving mailbox identity, source/context boundaries,
authority, schedules, drafts, decisions, cases, and existing knowledge. Installation alone
does not change the active profile.

### `source`

Input:

```json
{
  "source_id": "help-centre",
  "origin": "https://help.example.com/",
  "version": "etag-or-content-version",
  "status": "current",
  "verified_at": "2026-09-06T09:45:00+00:00",
  "valid_until": "2026-09-07T09:45:00+00:00"
}
```

`source_id` and `origin` must match the approved `source_policy`. Status is `current`,
`unavailable`, or `conflicting`. A local source also supplies `local_path` equal to the
approved absolute origin and `content_hash`; the engine rechecks that file before action.
Changed sources invalidate dependent lessons.

## Lease and case commands

### `claim`, `renew`, and `release`

Claim input:

```json
{"run_id": "run-20260906-1000", "minutes": 15}
```

The result contains `run_id`, a secret `token`, and `expires_at`. Run IDs cannot be reused.
A live lease blocks a second coordinator. Claiming after an interrupted attempt converts any
`attempting` send to `unknown` and holds its case for reconciliation.

Renew and release use:

```json
{"run_id": "run-20260906-1000", "token": "lease-token-from-claim"}
```

Renew extends the current lease by 15 minutes. Release records finish time and clears it.
Every leased operation below carries the same `run_id` and `token`.

### `scan` and `work`

Normal cycles use the exact `discovery` plans from cycle `begin`. Optional approved
`config.inbox_priority` is `{"enabled_tabs":["primary","updates"],"verified_ref":"actual setup confirmation"}`.
Only include tabs the owner or Gmail UI confirmed enabled; category labels alone do not
prove visible tabs. Supported tabs are Primary, Updates, Promotions, Social, and Forums
(lowercase in JSON). Missing/empty configuration processes Primary only. Enabled tabs do
not expand authority: broader `processing_tabs` require an explicit owner `scope_ref`. Changing this approved policy starts fresh category scans while
preserving cases and queued work.

Add `lane` from the discovery plan to the page below and use its exact query and cutoff.
Each lane has an independent saved cursor and message inventory in `inbox_scans`.
`work.resume_scans` lists unfinished lanes; `work.queue` follows category priority, newest
first within each category. `work.coverage` and `report.coverage` distinguish completed
listing from completed bot processing and unresolved owner work. A legacy scan without a
lane requires explicitly approved all-inbox scope.

Record each provider listing page before processing its messages:

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "query": "in:inbox after:2026/09/05 before:2026/09/07",
  "window_end": "2026-09-06T10:00:00+00:00",
  "page_cursor": null,
  "complete": false,
  "next_cursor": "provider-page-2",
  "entries": [
    {
      "trigger_id": "message-99",
      "thread_id": "thread-42",
      "received_at": "2026-09-06T09:55:00+00:00"
    }
  ],
  "provider_evidence": "gmail-search-response-page-1"
}
```

A scan starts with `page_cursor: null` and a fixed query/cutoff. An incomplete page requires
a distinct nonempty `next_cursor`. Resume with the saved cursor, identical query, and identical
`window_end`; the engine rejects a different window. A complete final page uses
`"complete": true` and `"next_cursor": null`. Each page accepts at most 1,000 entries.
Provider message identity cannot change threads.

Every discovered trigger enters the durable queue before the watermark advances. Creating or
updating a case with a complete snapshot atomically admits every listed message in that thread,
removes their queued triggers, and records their thread identity so an overlapping rescan
cannot reopen old work. `work` takes no input and returns the queue, actionable unheld cases,
due work, pending/creating/unknown draft candidates, unresolved sends, and `resume_scan` when
pagination was interrupted.

### `case`

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "case_id": "case-thread-42",
  "snapshot": {
    "complete": true,
    "thread_id": "thread-42",
    "trigger_id": "message-99",
    "message_ids": ["message-88", "message-99"],
    "fingerprint": "sha256:complete-live-thread-and-draft-state",
    "observed_at": "2026-09-06T10:00:00+00:00",
    "draft_ids": [],
    "correspondents": ["customer@example.com"],
    "automated": false,
  "categories": ["primary"]
  },
  "unresolved_ask": "Customer needs the current setup steps.",
  "next_action": "Answer from the current help article.",
  "responsible": "inbox-manager",
  "due_at": null,
  "state": "needs_action",
  "linked_cases": [],
  "link_evidence": null
}
```

State is `needs_action`, `waiting_correspondent`, `waiting_owner`, or `handled`. A handled
update also requires nonempty `outcome` and `outcome_evidence`. `due_at` is null or a
timezone-aware timestamp. One provider thread belongs to one case and its identity cannot
change.

To relate separate threads, create their cases first, then add existing case IDs in
`linked_cases` with nonempty `link_evidence`. Links are additive and holds propagate across
the connected issue so a fresh thread cannot bypass a pending owner decision.

### `hold` and `recover`

Hold input:

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "case_id": "case-thread-42",
  "kind": "owner_decision",
  "reason": "Refund request is outside standing authority.",
  "question": "Approve this refund exception once?",
  "recommendation": "Keep the published policy and decline the exception."
}
```

Kinds are `owner_decision`, `source_unavailable`, `connector_unavailable`, and
`draft_unknown`. An owner decision requires one precise `question`; recommendation is
optional. Owner decisions and unknown draft creation mark the case owner-held. Send
uncertainty cannot be downgraded through this command.

Only reversible `source_unavailable`, `connector_unavailable`, or `draft_failed` holds may
recover automatically:

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "case_id": "case-thread-42",
  "evidence": "Current source fetched and its registered version verified."
}
```

Owner decisions, takeover, unknown draft creation, and uncertain sends need their specific
owner/provider reconciliation path.

### `owner`

Global pause/resume:

```json
{
  "action": "pause",
  "owner_confirmed": true,
  "approval_ref": "owner:pause:20260906"
}
```

Actions `pause` and `resume_inbox` need no case. Case actions `takeover`, `resume`, `answer`,
and `close` add `case_id`. `answer` also adds `answer`. A one-off send exception may add:

```json
{
  "action": "answer",
  "case_id": "case-thread-42",
  "answer": "Send this answer once; do not make it a standing rule.",
  "once_rule": {
    "recipients": ["customer@example.com"],
    "allow_attachments": false
  },
  "owner_confirmed": true,
  "approval_ref": "owner:case-thread-42:once"
}
```

The engine binds a one-off rule to the current trigger and configuration version and consumes
it once. Standing rules use `configure`. `close` requires an `outcome`. Owner resume/answer/
close cannot bypass an unresolved send; reconcile it first. Owner actions cancel unconsumed
ready candidates but never delete or overwrite Gmail drafts.

## Candidate and independent check

### `candidate`

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "case_id": "case-thread-42",
  "candidate_id": "candidate-thread-42-v1",
  "profile_version": 1,
  "authority_version": 1,
  "body": "Hi Sam,\n\nOpen Settings, then choose Billing.\n\nBest,\nAlex",
  "recipients": ["customer@example.com"],
  "cc": [],
  "bcc": [],
  "attachments": [],
  "subject": "Re: Billing settings",
  "reply_message_id": "message-99",
  "reply_headers": {"In-Reply-To": "<original-rfc-id@example.com>", "References": "<original-rfc-id@example.com>"},
  "source_versions": {"help-centre": "etag-or-content-version"},
  "knowledge_dependencies": [
    {"note_id": "note-current-billing-path", "version": 2, "updated_at": "2026-09-15T09:00:00+00:00"}
  ],
  "evidence": "Current thread asks for billing settings; help-centre section 4 supports the steps.",
  "rule_id": "routine-product-answer",
  "mode": "send",
  "kind": "reply",
  "writer_id": "reply-worker:run-20260906-1000:1"
}
```

Mode is `send` or `draft`; kind is `reply`, `followup`, or `backlog`. Backlog cannot send.
`knowledge_dependencies` is optional. When learned knowledge affected the candidate, include
each exact `note_id`, integer `version`, and `updated_at` returned by lookup. Omit it otherwise.
BCC is rejected. Each attachment needs `id` and `sha256`. Profile and authority versions must
equal current engine version, `reply_message_id` must equal the snapshot trigger, and no live
draft or related-case hold may exist. The returned immutable candidate includes `hash`.
Repair always creates a new candidate ID.

`reply_message_id` is the provider's message identity. MIME `In-Reply-To` and `References`
use the verified original RFC Message-ID values, which are different from Gmail API IDs.
Leave `reply_headers` empty only when the mapped reply tool supplies correct threading from
`reply_message_id`. Arbitrary headers and CR/LF injection are rejected; the provider adapter
must never add unchecked recipients or sender overrides while constructing MIME.

### `check`

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "candidate_id": "candidate-thread-42-v1",
  "candidate_hash": "hash-returned-by-candidate",
  "reviewer_id": "check-worker:run-20260906-1000:1",
  "independent": true,
  "decision": "send",
  "assessments": {
    "grounded": true,
    "answers_ask": true,
    "identity": true,
    "recipients": true,
    "privacy": true,
    "authority": true,
    "no_loop": true
  },
  "reason": "Exact reply is supported, correctly addressed, and inside the routine-answer rule.",
  "evidence_ref": "check-packet:candidate-thread-42-v1"
}
```

Decision is `send`, `draft`, or `reject`. `reviewer_id` must differ from `writer_id`; all
seven booleans are required. Assessed candidates with a summary additionally require a
`summary` boolean; non-rejected approval requires it true. `independent: true` requires a real fresh reviewer distinct from
the writer for this exact candidate, including owner drafts. It does not mean tool isolation:
that separate proven capability is configured as `transport.independent_check`. Keep it
false when isolation is unproven; the draft check can still be independent. Never fabricate
a reviewer to satisfy this field. Send requires all true. Draft still requires grounded, identity,
recipients, and privacy true. This rail validates the declared independence and binding; the
host must actually enforce read-only checker isolation before automatic send. Map the
checker skill's send-ready result to `send`, draft-for-owner to `draft`, and repair/missing-
fact/missing-authority/no-reply to `reject`; preserve the semantic result in `reason` and the
referenced check artifact.

## Send transaction

### `prepare-send`

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "candidate_id": "candidate-thread-42-v1",
  "snapshot": {
    "complete": true,
    "thread_id": "thread-42",
    "trigger_id": "message-99",
    "message_ids": ["message-88", "message-99"],
    "fingerprint": "sha256:complete-live-thread-and-draft-state",
    "observed_at": "2026-09-06T10:01:00+00:00",
    "draft_ids": [],
    "correspondents": ["customer@example.com"],
    "automated": false,
  "categories": ["primary"]
  }
}
```

This atomically validates current profile hashes, sources, authority, checker, rule,
recipients, attachments, snapshot, automation/loop status, follow-up eligibility, and send
limits. It records irreversible intent before returning `result.action`, its one-use
`send_token`, and exact `result.payload` for the provider call. Uncertain attempts count
toward limits.

### `send-result`

For a provider-accepted action:

```json
{
  "action_id": "action-id-from-prepare-send",
  "send_token": "send-token-from-prepare-send",
  "status": "accepted",
  "message_id": "provider-message-123",
  "provider_receipt": "provider response reference",
  "mailbox": "support@example.com",
  "thread_id": "thread-42",
  "candidate_hash": "hash-returned-by-candidate"
}
```

Use `status: "sent"` only after provider read-back and add `"read_back": true`. Accepted may
later reconcile to sent. A definite provider failure uses `status: "failed"`, nonempty
`reason`, and `"definitive_not_sent": true`. Timeout, connection loss, or uncertain absence
uses `status: "unknown"` plus `reason`; never call it failed by inference.

### `reconcile-send`

Uses the same status-specific fields as `send-result`, replaces `send_token` with nonempty
`reconciliation_evidence`, and resolves an existing action from fresh provider evidence.
Unknown remains held. Terminal results are idempotent only for the exact same status and
message identity; `accepted` may advance to `sent`.

## Draft transaction

### `draft`

Reserve before calling Gmail:

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "candidate_id": "candidate-thread-42-v1",
  "phase": "reserve",
  "snapshot": {
    "complete": true,
    "thread_id": "thread-42",
    "trigger_id": "message-99",
    "message_ids": ["message-88", "message-99"],
    "fingerprint": "sha256:complete-live-thread-and-draft-state",
    "observed_at": "2026-09-06T10:01:00+00:00",
    "draft_ids": [],
    "correspondents": ["customer@example.com"],
    "automated": false,
  "categories": ["primary"]
  }
}
```

Reservation records the pending owner decision before provider actuation. After successful
creation, confirm:

```json
{
  "run_id": "run-20260906-1000",
  "token": "lease-token-from-claim",
  "candidate_id": "candidate-thread-42-v1",
  "phase": "confirm",
  "candidate_hash": "hash-returned-by-candidate",
  "thread_id": "thread-42",
  "mailbox": "support@example.com",
  "draft_id": "provider-draft-123",
  "provider_receipt": "provider response reference"
}
```

A definite creation failure uses `phase: "failed"`, `reason`,
`"definitive_not_created": true`, and a nonempty failure `provider_receipt`. This clears the
member-decision flag and creates a recoverable `draft_failed` operational hold. An ambiguous
result uses `phase: "unknown"` and `reason`; it remains owner-held until a complete provider
read proves and confirms the matching draft or the owner resolves it. Never create a second
draft while the reservation is unresolved.

### Existing draft revision

Normal revisions follow [the cycle contract](cycle-contract.md#existing-draft-revision): supply
one target without `requested_changes`, check the frozen actual merged replacement, refresh,
reserve and confirm exact readback. Load the exact command schema on first use and reuse it
within the loaded runtime. Legacy explicit `requested_changes` and saved in-flight revisions
remain supported with their original candidate/action hashes. An attempting or unknown update
uses `revision-reconcile`; only a never-reserved ready revision can use `revision-retire`.
Never approximate old payloads, edit returned replacement content or blindly retry an unknown write.

## Learning and scoped knowledge

### `knowledge-record`, `knowledge-search`, `knowledge-lookup`, `knowledge-forget`, `knowledge-maintain`

These commands provide the authoritative store for Gmail- and Codex-origin knowledge.
`knowledge-record` preserves authenticated source event, scope, freshness, contradictions,
revision history, and any case-only exception boundary. Search and lookup return bounded
relevant records without loading the whole store. Forget is an authenticated owner control
with actions `forget`, `pause`, `resume`, `exclude_evidence`, and `exclude_case`.
Maintain applies contradiction, freshness, and retention work in bounded batches. Run
`python3 <plugin-root>/scripts/inbox_engine.py schema --command <exact-command>` on first use in
the loaded runtime and reuse the returned shape. Preserve returned
note IDs, versions, and update times as preparation dependencies. Forgotten status and
provenance remain inspectable for manual recovery; there is no runtime undo action.

### `observe`

```json
{
  "candidate_id": "candidate-thread-42-v1",
  "observation": {
    "id": "observation-provider-draft-123-v2",
    "candidate_id": "candidate-thread-42-v1",
    "case_id": "case-thread-42",
    "trigger_id": "message-99",
    "draft_id": "provider-draft-123",
    "mailbox": "support@example.com",
    "thread_id": "thread-42",
    "observed_at": "2026-09-06T12:00:00+00:00",
    "listing_complete": true,
    "status": "sent",
    "linkage": "direct",
    "linkage_evidence": {"thread_id": "thread-42", "draft_id": "provider-draft-123"},
    "actor": "member",
    "actor_evidence": "Authenticated provider actor evidence",
    "sent_message_id": "provider-sent-456",
    "sent_at": "2026-09-06T11:58:00+00:00",
    "recipients": ["customer@example.com"],
    "final_body": "Hi Sam,\n\nOpen Settings, then choose Billing.\n\nBest,\nAlex"
  }
}
```

Status is `pending`, `edited_pending`, `sent`, `discarded`, or `missing`; linkage is
`direct`, `strong`, or `ambiguous`; actor is `member`, `bot`, `unknown`, or `other`.
`linkage_evidence` and `actor_evidence` must be present. A sent observation additionally
needs `sent_message_id` distinct from draft ID, `sent_at`, exact recipients, and `final_body`.
A discard may include `discard_reason` and recipients. Observation IDs are idempotent only
for identical evidence.

Reliable member sent outcomes clear the ordinary pending decision and reopen the case for its
new state. Explicit takeover remains. Pending, missing, ambiguous, bot, and unsupported bare
discard outcomes do not adapt behavior. After comparison text expires under retention, a
reliable owner send may settle the draft lifecycle, but no content lesson can be derived.

### `lesson` (backward compatibility)

```json
{
  "observation_id": "observation-provider-draft-123-v2",
  "lesson_id": "lesson-short-openings-v1",
  "proposal": {
    "kind": "example",
    "summary": "A direct answer before explanation worked for this routine setup request.",
    "scope": {
      "job": "customer-support",
      "request": "routine product setup",
      "facts": "current help-centre answer",
      "risk": "low; no account-specific disclosure or commitment"
    }
  },
  "contradicts": []
}
```

Kinds are `example`, `style`, `factual_correction`, and `business_rule`. All four scope
dimensions are required. Unchanged acceptance supports only an example. Existing example/style
lessons remain readable. New explicit scoped corrections use `knowledge-record` and may be
active immediately; inferred business rules remain proposals. `contradicts` may name existing
lessons to pause with this contrary observation. A new revision needs a new identity.

### `learning-control`

Every control requires authenticated owner fields:

```json
{
  "action": "undo",
  "lesson_id": "lesson-short-openings-v1",
  "reason": "This style applies only to billing replies.",
  "owner_confirmed": true,
  "approval_ref": "owner:learning:undo-1"
}
```

Actions `pause`, `resume`, and `invalidate_actor` need no lesson. `undo` and `contradict`
need `lesson_id` and `reason`. `exclude` needs `case_id` and removes retained comparison
content and lessons for that case. `resume` does not clear an invalidated actor assumption;
reconfigure the capability explicitly.

## Queues, reports, and retention

`due` takes no input and returns overdue non-handled cases with `evaluate_followup` only when
the approved follow-up rule, timing, attempt limit, and every related-case hold allow it;
otherwise it returns `owner_attention`.

`report` takes no input and derives mailbox, active/paused/version, queued count and scan
state, conversation and outcome counts, send results, verified draft states, owner attention,
numbered questions, due cases, scoped knowledge, and lease from stored state. Render only
member-safe summaries and verified links.

`prune` takes no input and applies configured retention. It removes copied email/comparison
bodies after 90 days by default while preserving identifiers, hashes, outcomes, receipts, and
knowledge provenance. A narrow unresolved-action recovery exception may retain needed material.
Long-pending items cannot retain full body text forever: after expiry, future reliable handling may settle
the case but cannot teach a content lesson without the original comparison text.

## Mechanical invariants

- One active versioned configuration and mailbox per home.
- One live coordinator lease; linked-case holds cannot be bypassed through another thread.
- Immutable candidates bind exact body, recipients, attachments, evidence, trigger, sources,
  profile, authority, and writer.
- Checks bind the exact candidate and a different reviewer. Host isolation remains a host
  responsibility.
- Send intent and draft ownership are durable before provider writes.
- Unknown send/draft results hold work until provider reconciliation or owner handling.
- No blind send retry, BCC, self-reply, automatic backlog reply, or automated-message reply.
- Bot sends do not create positive learning. Ambiguous actor/linkage does not adapt behavior.
- Cleanup and undo history share the mailbox database; they cannot grant send authority.
- Reports reflect stored evidence and never turn accepted mail into recipient delivery.

## Approved chat presentation

`configure.config.chat_output` accepts `"full"` (legacy default) or `"profile"`.
Profile mode requires `chat-preferences.md` among the approved `profile_files`
SHA-256 hashes. It changes presentation only. It does not alter mail authority,
notification cadence, retention or pending obligations. For a scheduled home, pause and verify the disabled old task against its old exact
prompt before changing configuration or a hashed file. Then use authenticated configure,
rebind the already paused task to the newly generated exact prompt, reactivate and
verify readback. Legacy profiles retain their original prompt bytes. Modified
preference files require approval and reconfiguration before profile-mode runs.
