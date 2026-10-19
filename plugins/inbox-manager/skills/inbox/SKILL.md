---
name: inbox
description: "Routes Inbox Manager setup, runs, status, owner decisions, learning controls, and backlog cleanup. Use for /inbox, manage my inbox, run my inbox, what did the inbox manager do, change my inbox rules, pause inbox automation, review inbox decisions, update inbox drafts, or clean up old mail. Do not use for outbound campaigns, newsletters, or composing one unrelated email."
---

# Inbox Manager

For the AI Kruh edition, read the [AI Kruh profile](references/ai-kruh-profile.md). Gmail operations use `gws` CLI; changing transport needs an explicit owner request. Proposed profile defaults require setup approval and never override an existing approved profile.

Route the owner's request to exactly one workflow. Do not triage, research, draft, check,
learn, or touch email here.

Use the selected workflow’s cycle or setup contract. Read
[`references/providers.md`](references/providers.md) when a provider capability is relevant;
other references belong to the component making that judgment.

## Route

Explicit intent wins over queue state:

| Request | Route |
|---|---|
| First setup, upgrade check, migration, job/profile/knowledge/authority/cadence change, pause, resume, or stop | `inbox-setup` |
| Run the inbox now, process new mail, process due work, or scheduled invocation | `inbox-triage` |
| Answer report questions, update drafts using owner feedback, resolve a named owner decision, approve a standing-rule proposal, take over, release a case, snooze/mute a nudge, or inspect/change learning | `inbox-review` |
| Apply the job to historical mail or clear a backlog | `inbox-cleanup` |
| Ask what happened, what is waiting, or whether automation is active | Answer from runtime status and the latest report |

Within a run, `inbox-triage` coordinates `inbox-research` for evidence, `inbox-reply` for
responses, and `inbox-check` for independent candidate review. `inbox-review` applies owner
answers to affected drafts; `inbox-learn` reconciles reliable sent outcomes and explicit
answers into scoped knowledge. These are internal stages, not extra commands to teach users.

## Select the inbox home

A scheduled invocation must supply its exact registry root, mailbox, task ID, and binding
ID. Resolve that registration and use the cycle runner's scheduled `begin`; never fall back
to another home when a binding fails.

For an owner request, use an explicitly supplied home, or resolve the named mailbox/alias
through `./inbox-manager/.inbox-manager-registry.sqlite3`. Use `inbox_registry.py list` for
`status all`; keep each mailbox's work, account, permissions, and learning distinct. An
ambiguous name requires owner selection. See
[`operations-contract.md`](references/operations-contract.md) for the registry commands.

If no registry exists, inspect only `./inbox-manager/<inbox-name>/` for legacy homes. One
unambiguous home may continue directly; multiple homes need selection. Register a legacy
home during setup after verifying its provider identity. Never roam the filesystem or infer
an inbox home from an installed plugin copy.

Use the loaded plugin's own runtime scripts. An absent, inactive, or unmigrated home routes
to setup. A live lease means another cycle owns that inbox; report it rather than starting
another coordinator.

## Status answer

Use the compact read-only status/report projection. Fetch full diagnostics only when explicitly
troubleshooting. For one named item, use `inbox_engine.py lookup --home <home>` with exactly one
direct selector such as `--case-id`, `--thread-id`, `--candidate-id`, `--action-id`, or `--gap-id`
instead of loading the whole mailbox record. Report facts from state and provider receipts:

- active job, mailbox, automation state, observed schedule health, and last successful run;
- completed actions, verified current Gmail drafts, conversations waiting on a correspondent,
  decisions waiting on the owner, operational holds, and uncertain sends;
- learning state and the latest adopted lesson or pending rule proposal;
- open obligations and knowledge gaps, with the responsible party and due date;
- category scan coverage and what remains durably queued;
- one useful next action with saved Gmail/case links and the report's numbered questions.

Do not equate an accepted send receipt with recipient delivery. Do not call a conversation
handled while a supported next step remains, or call a lane done before its scan and message
accounting are complete. Include only provider-returned or provider-verified Gmail links.
When scheduled activity is late relative to the saved cadence, say that it is stale and point
to the scheduler's actual run state.
Finish status and attention answers with the [owner report](references/owner-report.md).

## Handoff

Name the target and reason in one line, pass the selected home and explicit intent, then
invoke the skill. One route means one workflow; do not begin a second hidden path.

## Exit

Continue the selected workflow to its defined completion, including its required final report.
For status requests, finish after answering from durable state with the owner report.
No home routes to setup; an ambiguous home requires owner selection; a live lease stops the
run without stealing it.
