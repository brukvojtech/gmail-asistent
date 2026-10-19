# Handling rules

This file owns intent, job fit, priority, next action, and completion. The unit of work is a
conversation with a next step, not an unread message or an empty inbox.

## Understand the case

Read the complete current thread and establish:

- what the correspondent wants now;
- whether the job owns the next move;
- which asks, promises, deadlines, and linked work remain open;
- whether a reply, no reply, owner decision, wait, or reversible archive would move the
  conversation forward;
- what evidence will prove completion.

Classify by the active job. A new commercial approach may be noise for a personal assistant
and core work for a partnerships inbox. Sender category, keywords, and confidence scores do
not replace this judgment.

## Choose the next step

Before assigning the final business owner, complete the [shared assessment](cycle-contract.md#one-assessed-case-submission) for every new
or changed substantive request. Preparation belongs to the bot: read the current thread, check
applicable knowledge and likely sources, draft supported content, and record the remaining
question or blocker. `waiting_owner` cannot hide unattempted preparation.

- **Needs action:** the bot owns a supported next move within the current job and authority.
  Perform it in the current pass when the provider supports it; do not park a draftable reply as
  a description of future work.
- **Reply:** a supported answer or necessary question advances the request.
- **Wait for correspondent:** the last supported next move belongs to them; keep a wake
  condition or due time when needed.
- **Owner decision:** a fact, commitment, exception, identity, or authority belongs to the
  owner. Use `waiting_owner` for this or any owner-only next step, including work outside email,
  and surface one precise action with a recommendation.
- **No reply:** the message is informational, already answered, duplicate, outside the job,
  or the approved completion rule permits closure without response.
- **Operational hold:** required provider, source, lock, or send evidence is unavailable.
- **Archive:** change Gmail presentation only, through the reversible provider rail.

For a multi-part request, track supported bot obligations and owner questions separately. A
partial owner draft answers every independently supported part while the final report names
the missing fact or decision. If the gap controls the whole response, present researched
options and a recommendation rather than a speculative draft.

Do not create a polite placeholder just to produce a draft. A truthful necessary question is
useful; “I'll look into this” without authority or a real next step is not.

Group repeated notifications only when they evidence the same underlying action. Preserve
separate obligations and partial completion. Never close an obligation because its email was
archived, the owner did not respond, or a previous report mentioned it.

## Priority

Prioritize material deadlines, legal/financial exposure, promises already made, blocked
customers or colleagues, and consequences of delay under the job. Bulk origin does not make
a real obligation harmless. Repeated notices about the same unresolved deadline inherit the
known obligation.

A known urgent obligation takes priority within the approved processing scope. Urgency never
opens another category. Use the exact discovery plans from the runtime; enabled Gmail tabs
and authorized processing categories are separate choices.

Use the approved alert channel only for time-sensitive exceptions. Additional notification
cadence is separate from the complete owner action table printed after every run. An unanswered
owner question pauses that case, not the whole inbox.

## Completion

A case is handled when its approved completion rule is met and provider/state evidence
supports the outcome. A sent reply may move a case to waiting; it does not necessarily
finish it. Archiving never resolves an obligation.

Processing is scan accounting: a message can be processed because its unchanged waiting case
was preserved or it was left durably queued. Do not report that as handled.

Close without response only when the saved rule allows it. Record `closed without response`
separately from successfully handled work. When follow-up attempts reach their approved
limit, stop sending and bring a material unresolved obligation to the owner.

Every live case retains the unresolved ask, responsible party, next action, due time or wake
event, and evidence for its latest outcome.

## Expired requests

Judge expiry against the current request, latest activity, and the owner's completion rule.
A past invitation, expired offer, or superseded link can be closed without response when no
promise, material consequence, or continuing ask remains; retain the dated evidence and reason.
An old thread with a new reply or an outstanding commitment stays live.

There is no universal age cutoff. If the owner supplies one, record its scope and important
exceptions in `instructions.md`, then apply it using the latest substantive activity. Otherwise
age alone never resolves work. “Discard” under this workflow means a supported no-response
outcome followed by authorized reversible archive, never deletion or trash. Check each open
obligation before closing a case; stale draft presentation is separate from business completion.
