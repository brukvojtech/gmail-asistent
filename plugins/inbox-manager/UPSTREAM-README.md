# Inbox Manager

Give the bot an inbox, a job, your knowledge, and your rules. It researches and prepares
supported replies, groups unresolved questions at the end of one report, revises affected
Gmail drafts when you answer in Codex, and learns from both routes.

Inbox Manager manages conversations toward a result. It does not chase inbox zero: a case is
successful when the request is answered, a commitment is protected, the right person owns
the next step, or the approved completion rule says it can close.

The current source is an unmerged local review candidate. Installation alone connects or changes no
mailbox. Automatic sending still requires separately proved provider transport and
host-enforced checker isolation.

## What it does

- **Give it a job.** Define who it represents, which requests it owns, and what “handled”
  means. Personal assistant and customer support are examples of the same engine.
- **Teach it where answers live.** Select permitted email, files, folders, links, and
  connected sources. A short research guide says what each can answer while existing access,
  freshness, authority, and disclosure rules remain the boundary.
- **Set its freedom.** Approve reply categories it may send, categories it leaves as drafts,
  decisions it brings back, recipients, commitments, and throughput limits.
- **Check every automatic reply independently.** A fresh read-only checker verifies the exact
  body, recipients, evidence, privacy, conversation state, and active authority. If the host
  cannot isolate that checker, the product stays draft-only.
- **Track each obligation.** A conversation can contain several asks, owners, and deadlines.
  Completing one preserves the others. New mail, decisions, and relevant source changes
  queue the affected work for another look. Archiving mail never erases the obligation.
- **Learn through Gmail or Codex.** Reliably attributed owner-sent replies and authenticated
  answers can become scoped facts, procedures, corrections, examples, and low-risk preferences.
  Unsent edits remain work in progress, and a one-case exception does not become policy.
- **Manage several inboxes.** Each registered mailbox has a verified account mapping, its own
  private profile and learning, and an explicitly bound schedule. Ask `/inbox status all`
  for combined status. Shared knowledge still needs approval in each inbox.
- **Keep knowledge current.** Missing facts become named gaps; repeated gaps are grouped for
  owner review. Approved source answers queue the affected conversations without granting
  new action authority.
- **Prepare clearly.** Each changed case records its supported answers, evidence and unresolved
  questions together. A checked summary describes the exact current draft; manual changes can
  make that summary stale. Owner feedback revises the current draft while preserving unrelated
  edits, or reports the conflict without overwriting it.
- **Answer once.** Reports separate completed actions, verified drafts, waiting conversations,
  failures, and uncertain sends, then end with one numbered question list. Answer several
  numbers in one Codex message and affected drafts are revised in the same invocation.

## Requirements

- Python 3. The bundled engine uses only the standard library and stores private transactional
  state in the selected inbox home.
- A connected Gmail provider with the exact read/write capabilities required by the approved
  job. Setup discovers and proves live verbs rather than assuming them.
- A host that can start a fresh worker with read-only tools for every automatic send. Without
  that isolation, Inbox Manager can still prepare Gmail drafts.
- Optional native scheduling. Setup records the actual cadence and whether the host must be
  running; local scheduling is not an always-on hosted service.
- Optional alert and knowledge connectors selected during setup. Access to a connector does
  not automatically put it inside the job.

The synthetic Codex trial is evidence for that tested configuration, not a guarantee for
live Gmail, every Codex version, or Claude Code. Setup must verify the actual provider and
checker capabilities before enabling an action.

## Start

```text
/inbox
```

Describe the job in one prompt:

> Manage support@example.com. Answer product questions from this help centre and these
> policies. Keep replies short and friendly. Bring refunds and anything you cannot verify
> to me. Check every weekday and give me a daily digest.

Setup confirms the connected mailbox before reading mail, proves only the capabilities the
job needs, samples authorized mail, and produces one operating brief. The brief shows the
job, knowledge, send/draft/decision boundaries, limits, schedule, learning behavior, and
representative examples. Correct it in ordinary language, then approve the exact version.

Processing defaults to Primary (`in:inbox category:primary`), including older and already-read
mail. The owner can explicitly select other categories or all Inbox. Visible Gmail tabs never
widen that scope, and urgency only changes order inside it. A cheap ID scan catches old mail
moved into Primary; complete bodies are read only for changed or actionable conversations.
Draft discovery resumes from its saved cursor and is shared across each batch of thread reads.

Automatic sending activates only when the provider transport is proved, the owner approves
bounded authority, and the host supplies an independent read-only checker. Brief approval
does not by itself authorize a provider consent prompt or prove a live test occurred.

## More than one inbox

Run `/inbox Set up sales@example.com`, then configure each additional inbox separately.
Use the mailbox or an approved alias in later requests. The registry never guesses between
accounts; a scheduled invocation is bound to its mailbox, account, home, and native task.
Existing unregistered homes still work by explicit home until setup registers them.

`/inbox status all` shows each inbox's mode, outstanding work, and observed schedule health.
Schedules report missed or unfinished runs and an unverified next run; a saved schedule
record alone is not proof that a host will wake up. Setup verifies the native task prompt
and its first actual run before calling scheduling proven.

## The normal cycle

On a manual or scheduled run, Inbox Manager:

1. reconciles what happened to pending Gmail drafts;
2. loads the next bounded scan lane, due conversations, and safe retries;
3. records a preparation assessment for each new or changed substantive request;
4. retrieves only scoped knowledge and approved sources that could change the answer;
5. drafts every independently supported part, or presents researched options when one owner
   decision controls the whole reply;
6. checks a possible automatic reply in a fresh read-only worker;
7. sends within standing authority, leaves an owner draft, or records a precise decision
   linked to the relevant obligations;
8. verifies provider actions, updates conversation ownership, and returns the complete report
   with drafts followed by consolidated questions.

External or ambiguously owned drafts are never overwritten. An authenticated Codex answer may
revise its affected bot-linked draft after a fresh three-version merge that preserves unrelated
manual edits; conflicts leave the draft untouched and become a precise question. The bot does
not auto-send another answer to the same unresolved ask. A timeout during
send becomes an uncertain hold until reconciliation; it is never blindly retried.

An unchanged waiting case is accounted for without repeating research or drafting. New or
changed mail, an owner answer, a confirmed draft/sent outcome, a deadline, or a relevant
source change reopens the work. “Processed” describes this scan accounting; “handled” means
the approved business completion rule is actually met.

Inbox Manager may label and reversibly archive mail. It never deletes mail. The current
workflow does not create tasks, issue refunds, book events, change a CRM, or run outbound
campaigns.

The operating brief can explicitly enable archive-on-resolution. It runs only after every
obligation is closed and no hold, takeover, owner decision, or pending draft remains. A case
can be handled even if reversible archiving fails; the digest reports those outcomes
separately. Existing profiles do not gain this authority without an amended brief approval.

## Worked examples

### Personal assistant

Job: protect the owner's attention and keep commitments from slipping.

An established contact asks how to book a call. The approved profile contains the owner's
public booking link and permits sending it to legitimate meeting requests. Research confirms
the current thread and link; the reply promises no availability; the independent checker
passes the exact body and recipient. Inbox Manager sends and records the case as waiting for
the correspondent.

Another contact asks the owner to join a project next month. That creates a new commitment,
so Inbox Manager prepares a concise Gmail draft or returns the decision: “Do you want to
commit to this?” It does not infer yes from the relationship.

### Customer support

Job: resolve routine product questions accurately and surface exceptions.

A customer asks where to change a setting. The current help article supports the steps and
the approved rule permits routine product answers. The exact reply passes the independent
check and sends.

A customer asks for a refund outside the published policy. Inbox Manager may explain the
policy, but it cannot claim a refund was issued. It brings the owner one recommendation and
keeps the case waiting. If the owner repeatedly approves similar exceptions, learning may
propose a standing rule; it cannot create one.

## Owner decisions and teaching

Answer one or several numbered questions in the authenticated owner task. Inbox Manager maps
the batch to the saved report, stores your words before provider writes, refreshes affected
conversations and drafts, and records whether you mean “this once,” “make a standing rule,”
or “I'll take over.” Ambiguous answers are clarified without blocking clear answers;
email from someone claiming to be the owner cannot grant permission. Standing rules still
need the amended operating brief. Delivery receipts track the notification, not whether
you read it.

For a missing business fact, it groups related cases into a knowledge gap. You can supply an
approved source, clarify its scope, and have those cases reconsidered. A source update or
owner answer never silently clears a takeover, pending draft, or uncertain send.

The daily digest includes all outstanding drafts, knowledge gaps, and owner-held cases, with
only Gmail links the provider returned or verified. Snoozing or muting a reminder suppresses
the nudge; it does not resolve the underlying work. Setup verifies that the host notification
policy can deliver the approved action alerts and digest, including the exact saved time and
time zone.

## Learning controls

Gmail and authenticated Codex answers are the feedback surfaces:

- sent substantially unchanged: a scoped positive example when actor/linkage is reliable;
- edited and sent: learn only supported wording, factual, or decision differences;
- edited but pending or untouched: no judgment yet;
- missing with no matched sent reply: ambiguous, no adaptation;
- bot-sent reply: execution evidence only, no self-reward;
- explicit Codex answer: scoped facts, procedures, corrections, and preferences can apply
  immediately; exceptions remain case-specific and policy changes remain proposals.

Copied email bodies default to 90-day retention. Useful scoped knowledge remains until
corrected, forgotten, or outdated; compact operational history remains for one year.

Use `/inbox` to inspect, search, forget knowledge, exclude a case, pause learning
while management continues, resume it, maintain the store, or decide a proposed standing rule. Learning can adjust
low-risk preferences inside current authority. It cannot add recipients, disclosures,
refunds, promises, actions, follow-ups, sources, sending categories, or limits.

## Private files

Member data stays outside the installed plugin in the chosen home:

```text
inbox-manager/<inbox-name>/
  job.md
  instructions.md
  voice.md
  knowledge/
    index.json
  inbox-engine.sqlite3
  connection.md
```

The editable profile explains the job. The database stores the approved immutable projection,
cases, candidates, cleanup/undo receipts, and bounded learning state. Compact lookup commands
find one case or action without reading the whole history. Reads do not rewrite state. Keep
the home private and out of Git. Legacy archive/history imports preserve their originals;
legacy `doctrine.md` is imported once and kept as history, not as a competing rule source.

## Skills

| Skill | Responsibility |
|---|---|
| `inbox` | One front door for routing and status. |
| `inbox-setup` | Job, knowledge, authority, provider proof, learning, and schedule. |
| `inbox-triage` | One complete cycle and the only external write owner. |
| `inbox-research` | Read-only evidence packet for one case. |
| `inbox-reply` | Exact useful reply candidate with no send access. |
| `inbox-check` | Fresh independent read-only pre-send judgment. |
| `inbox-learn` | Gmail/Codex feedback and reversible scoped knowledge. |
| `inbox-review` | Batch answers, draft revisions, takeover, rules, and learning controls. |
| `inbox-cleanup` | Thin compatibility door into resumable historical backlog mode. |

Members need to remember only `/inbox`. Research and writing may stay in the coordinator;
the specialist skills also support bounded assistance. Independent checking uses a fresh reviewer.

Tracked conversations are refreshed every run, including replies you sent directly in Gmail.
The model reassesses outstanding obligations; a sent reply alone does not prove completion.
Owner answers in Codex use the same verified cleanup path. Existing tasks can continue after
reloading the current skill and scripts; adopting the draft-first workflow is an explicit
migration and does not reset history or grant authority.

## Updating an existing installation

Update the plugin, then start a fresh chat or reload its current skills and scripts. Keep
the existing inbox home. The 0.11.3 to 0.11.4 update preserves saved work, permissions and
schedule bindings; adopting changed defaults is a separate approved setup change.

Ask `/inbox check my existing setup after the update` to verify the account, effective
category scope, provider capabilities, pending actions and schedule. Much older profiles
may need scope confirmation or migration. A changed profile requires schedule rebinding,
which setup completes before reporting automation active. See the
[upgrade procedure](skills/inbox/references/upgrades.md) for the exact boundaries.
