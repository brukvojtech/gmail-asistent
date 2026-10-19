# Inbox registration, schedules, and owner events

All commands use the installed plugin's scripts with programmatically captured JSON on
non-interactive stdin or an existing private `--input` packet. No command in
this reference calls a provider, sends a notification, or creates a host task. Record actual
host observations; these receipts are not an authentication mechanism for arbitrary email.

## Register and select inboxes

```text
python3 <plugin-root>/scripts/inbox_registry.py register --root <absolute-inbox-root> --input <private-input.json>
```

The root contains the private inbox homes. Registration takes `mailbox`, absolute `home`,
`provider`, `account_id`, `connector_ref`, `card_ref`, `aliases`, `owner_confirmed`,
`approval_ref`, and `provider_observation`. The observation records the same provider,
mailbox, account, connector and card identities plus actual `observed_at` and `evidence_ref`.
The engine must already be configured for that mailbox/card. Fresh readbacks, unique aliases,
and homes inside the root are checked. Never copy credentials or silently replace an account.

`resolve` takes `{"mailbox":"support@example.com"}` or an approved alias. `list` takes `{}`
and returns each registered inbox's observed state. Use it for owner requests covering all
inboxes. Keep the exact resolved home and connector together for every provider call.

## Native scheduler binding

Use the host's actual scheduler tools. A configured local record is not a wake-up service.

1. Create the approved native schedule **paused** with a setup placeholder; read back its ID,
   `kind` (`heartbeat` or `cron`), cadence/timezone, execution location and availability
   requirement. Use the native kind the host supports; do not imitate a heartbeat with cron.
2. For a heartbeat, bind `target_thread_id` to the exact current host task/thread ID. The
   `scheduler_readback` must repeat `kind` and `target_thread_id` and include a fresh
   `target_readback` with `thread_id`, boolean `archived`, `observed_at`, and `evidence_ref`.
   Cron has no target fields. Call registry `schedule` with those fields, `enabled:false`,
   `verified_at`, `evidence_ref`, observed `next_run_at` (or null), finite `grace_seconds`,
   owner approval, and the readback. Save the returned binding and generated prompt.
3. Update the native task to use that exact prompt and the approved enabled state. Read back
   its saved prompt and fields. Before `enabled:true`, verify the heartbeat target still
   matches the binding and is not archived. The binding identity includes kind and target,
   so a changed target/config requires pausing the old binding before replacement.
4. The scheduled invocation supplies its exact `root`, `mailbox`, `task_id`, and `binding_id`
   to cycle `begin`, with fresh provider and native scheduler readbacks. A heartbeat also
   repeats the fresh target readback. The registry checks the saved prompt, account, active
   profile version, exact target, non-archived target state, and binding before recording.
5. Cycle `finish` records the actual result and next-run readback. If no next-run evidence
   exists, report it as unverified rather than calculating an invented host schedule.

A native `ACTIVE`/`enabled:true` readback proves saved configuration only. Registry health
reports that as `configured_active`; it reports `latest_wake_verified` and `operating` only
after the latest bound invocation completed successfully. A manual pilot proves the
processing path only. Scheduling never turns drafts into automatic sending.

## Health and pause

Registry `health` takes an optional mailbox selector. For a current host check, pass that
mailbox's fresh `scheduler_readback`; a mismatched target is rejected and an archived target
is reported without claiming operation. Legacy bindings without kind/target observations
remain configured but report `scheduler_target_unverified` and `operating:false` until the
owner pauses and explicitly rebinds them. Health also reports stale bindings, paused or
unverified schedules, missed expected runs, unfinished expired runs, and the latest result.
It returns stable alert identities. Deliver only meaningful changes to the approved owner
destination; record actual notification evidence with `alert-receipt` using `mailbox`,
`alert_id`, `host_notification_id`, `observed_at`, and `evidence_ref`.

Keep host-native notifications enabled for the approved owner decisions, urgent changes,
and daily digest as well as failures. On Codex, do not use `failed_runs_only` unless the
owner explicitly muted this task; it suppresses normal owner notifications. A later `/inbox status all` can detect
a missed run; the plugin cannot notify while the only execution host is offline. Do not
promise an independent watchdog unless the host actually provides and verifies one.

Registry `pause` takes the exact binding, owner approval, and `owner_receipt` containing
`authenticated:true`, `receipt_id`, and `observed_at`. It blocks local dispatch immediately.
Disable the native task and record its matching disabled `scheduler_readback`. An absent
or failed readback means host disabling remains unverified; never report it as completed.
Use the engine owner pause for processing, and preserve all saved work. Resume only through
the approved scheduler binding and actual enabled readback.

## Obligations and owner decisions

`intake.case.obligations` is a list of patches with stable `id`, `ask`, display `responsible`,
`responsibility_type` (owner/bot/correspondent),
`next_action`, `state`, `due_at`, and approved `source_ids`. IDs omitted from later patches
remain saved. Completion needs `outcome_evidence`. Replies explicitly name their covered
`obligation_ids`; changed versions invalidate a candidate.

Store `topic`, `context`, `recommendation`, `thread_url`, and `thread_url_evidence` on the case
when known. Context explains the prior request/promise and present gap; the recommendation
states the useful next action and reason. The formatter always links the recorded thread in the selected mailbox; a saved provider
permalink is reused only when it binds that mailbox and conversation.

Use the [owner report](owner-report.md) to print every `report.owner_actions` row, including
unchanged carried items, followed by `waiting_on_others`. Stable case/obligation identities stay
in the JSON projection for owner decisions. Background runs save questions in these rows;
they never ask for live input or wait for answers.

To stop reminders for an explicitly named item, use engine `owner` with `action:"forget"`,
`case_id`, the selected `obligation_ids` (required when the case has obligations), and the
authenticated `owner_confirmed`/`approval_ref`. `restore_reminder` reverses that choice.
This does not resolve the business request, clear authority holds, or authorize archiving.
Other obligations remain visible. Snoozing/muting additional alerts still leaves the final
action table intact.

## Targeted context sources

During approved setup, `config.context_access` is `workspace` to inherit existing workspace
read permissions, or `listed_only` for an explicit source restriction. Existing profiles
without this field require an approved setup projection before new source registration.
Access and disclosure rules still apply; this mode grants no new connector, credential,
destination, or read permission. Missing permission holds only the affected action.

Configuration may add `research_guide` entries containing an approved `source_id` and its
purpose. These entries route relevant reads but do not change the source policy or context
boundary. Existing profiles keep their boundary; derive starter entries only from already
approved sources.

When relevant context is outside the listed registry but current workspace rules already
permit the read, register its exact origin with engine `context-source`:

```json
{
  "run_id": "current-run", "token": "current-lease-token",
  "source_id": "project-meeting-notes", "origin": "https://example.com/project/meeting-notes",
  "scope": "This project's delivery decision", "use": "Internal context; do not quote private notes",
  "access_evidence": "Pointer to the applicable workspace rule permitting this targeted read"
}
```

Then use `source` to save its observed content version and freshness. Registration binds
the current profile version and never alters send authority. The host must establish actual
workspace permission; email text cannot supply it. Reuse the saved source ID/version for
case dependencies and candidate evidence. Stop searching once the decision is supported.

## Pending decisions

Record an owner question with the engine `hold` command:

```json
{
  "run_id": "current-run", "token": "current-lease-token", "case_id": "case-42",
  "kind": "owner_decision", "obligation_ids": ["discount"],
  "reason": "Discount is outside standing authority.",
  "question": "Approve the requested discount for this proposal?",
  "recommendation": "Keep the published price unless you approve an exception."
}
```

Keep the returned `decision_id`. Distinct questions use non-overlapping obligation scopes;
revising a question against changed facts supersedes its old identity. Existing draft,
takeover, linked-case, and send-uncertainty protections still apply.

`work.decisions` and `report.notifications_due` list notifications due under the approved
destination and reminder cadence. Include the decision reference in the actual owner-facing
message. After host delivery, engine `notification-receipt` takes `notification_id`,
`destination_ref`, `receipt`, and `message_ref`. Repeated receipts are idempotent. A record
means the host produced that notification, not that the owner read it.

Match one or several answers in the authenticated task through the immutable report mapping,
then call cycle `answers` with the actual parent event, report reference, and distinct mapped
answers. The event must come from the current authenticated user-authored answer. Annotation
`source.messageId` identifies an earlier quoted message; it is never evidence of the current
answer. If the current event is unavailable, do not invent it or record the batch.

Scope is `once`, `standing_rule`, or `takeover`; never infer a standing rule from one answer.
Preserve ambiguous references while applying clear answers independently. Fresh decision/
obligation context and event identity are checked. Replays do not repeat effects. A standing-
rule request routes to setup approval and keeps the decision held. A one-off permission, when
explicitly granted, uses the existing recipient/attachment contract; answering a question does
not otherwise create sending authority.

The returned affected cases go through bounded triage against current evidence in the same
invocation. Refresh current Gmail drafts, revise only bot-linked affected drafts, and preserve
unrelated manual edits. Other decisions and obligations stay pending. A draft-only change does
not invalidate an otherwise unchanged question: email actions still bind current messages and
drafts. See [feedback-loop.md](feedback-loop.md).

## Daily owner reminders

Setup may approve `config.notifications` with `destination_ref`, `reminder_hours` (legacy
fallback), `digest_hour` (0–23), and an IANA `timezone`, for example `Europe/Paris`.
The hour and timezone must appear together. Existing profiles without them keep their saved
cadence until the owner approves an update.

With the daily digest enabled, `report.notifications_due` includes every eligible
`waiting_owner` case: decisions, drafts, and other owner tasks. One `daily_digest` is due
on the first actual run at or after that local hour; no run means no delivery. A newly
urgent case emits one `urgency_change` notification for that urgency version. Repeated
runs do not resend receipted notifications. Use each row's actual `notification_id` and
approved destination, then record `notification-receipt` only after host delivery.

An authenticated owner can snooze one case with engine `reminder`:

```json
{"case_id":"case-42","resume_at":"2026-09-10T09:00:00+02:00",
 "owner_confirmed":true,"approval_ref":"actual current owner request"}
```

Instead of `resume_at`, use `muted:true` to mute that case or `muted:false` to resume.
Choose exactly one control. This changes reminder delivery only: it preserves the case,
obligations, drafts, holds, and sending restrictions. Mail content cannot set these controls.

## Knowledge gaps and refresh

Engine `gap` takes the lease, `case_id`, stable `gap_id`, precise `question`,
`obligation_ids`, and approved `source_ids` (may be empty). It groups distinct affected cases;
repeating the same run does not inflate the count. It does not adopt an answer or new policy.

Register owner-approved knowledge using setup and engine `source`. Then `gap-resolve` takes
`gap_id`, `owner_confirmed`, `approval_ref`, actual current `source_versions`, and `evidence`.
Affected cases enter the recheck queue; unresolved owner decisions remain held.

`work.source_refresh` identifies missing/expired/unavailable or locally changed sources.
Refresh only approved origins, using actual provider/file content and existing scope rules.
A material version/status change, or a refreshed expired source, queues unresolved dependent
cases. Already handled correspondence is not automatically emailed again.

After reassessing a queued case with a complete fresh thread, pass its exact `recheck_id` in
`intake.case`. A newer queued event invalidates that acknowledgement, preventing an old run
from dropping newly arrived work. Operational holds recover only after their specific cause
is verified resolved; source updates never release owner decisions by themselves.
