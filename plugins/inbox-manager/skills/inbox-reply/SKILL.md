---
name: inbox-reply
description: "Chooses and writes the smallest useful reply for one Inbox Manager case from a bounded evidence packet, approved job, voice, and authority context. Use when inbox-triage has a completed inbox-research packet. Returns an exact candidate and intended conversation state; it performs no provider or state writes."
---

# Inbox reply

For the AI Kruh edition, read the [AI Kruh profile](../inbox/references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

Write one truthful response that moves the conversation toward the job's completion rule.
Read [`../inbox/references/reply-quality.md`](../inbox/references/reply-quality.md) and
[`../inbox/references/conversation-state.md`](../inbox/references/conversation-state.md).
For owner-question and existing-draft behavior, read
[`../inbox/references/feedback-loop.md`](../inbox/references/feedback-loop.md).

## Decide before writing

Use the current case, evidence packet, approved instructions, voice, and scoped lessons.
Identify the actual ask and the smallest useful next step. A reply may answer, request
missing information, propose an authorized next step, decline within policy, or state a
supported next event.

Do not invent a fact, commitment, deadline, availability, refund, booking, task, or business
action. Do not imply an external action happened because the email recommends it.

Identify every material ask. Draft every independently supported part by default. Return the
unresolved decision for the coordinator
to persist against the corresponding stable obligation IDs; a freeform candidate note is
not durable case state. Do not imply the omitted part was resolved or add a promise to
investigate. A partial answer cannot bypass a pending decision or become an automatic reply
while that decision remains unresolved. If one decision controls the whole response, return
researched options, a recommendation, and the precise question instead of a speculative body.
Unsupported content does not become acceptable by adding “probably”
or lowering confidence.

If no candidate is useful, return `no_draft_reason` as `already_resolved`, `no_reply_needed`,
`another_owner`, `decision_controls_whole_reply`, or `blocker` as applicable. Do not substitute
a generic acknowledgement, holding message, or promise to investigate.

## Write

Answer the ask early, preserve relevant commitments, and use only facts included in the
packet. Match explicit `voice.md` instructions before learned preferences; tone samples
never supply facts. Avoid filler, internal notes, hidden uncertainty, and fake holding copy.
Ask for missing information only when the correspondent can reasonably provide it and the
question advances the case.

Return the proposed exact body, subject, deliberate To/CC/BCC and attachments, supported
obligation selection, claim-to-source support, remaining uncertainty and intended next state.
Add a short factual summary of what the draft answers or proposes. A draft offering a time does
not mean a booking changed; a partial draft does not cover an unanswered sibling ask.

For the normal [assessed-case path](../inbox/references/cycle-contract.md#one-assessed-case-submission),
the coordinator supplies selected scope and exact source/knowledge evidence once in the assessment;
the runtime binds candidate versions and observed threading. Preserve exact learned-note
`note_id`, `version`, and `updated_at` when a note affected the reply. Do not copy those inherited
fields into the nested candidate. Recipients and attachments remain deliberate reviewed choices.

## Exit

Return either one complete or independently useful partial candidate bound to the
case/evidence/profile versions, one precise missing-fact result, one owner-decision result, or
no-reply with one of the explicit reasons above. A missing decision may legitimately withhold
the candidate when it controls the whole response. Never create a draft, send, mutate state, or
call a write-capable provider.
