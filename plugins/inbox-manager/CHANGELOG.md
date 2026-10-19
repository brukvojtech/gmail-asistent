# Changelog — inbox-manager

## 2026-10-19 - inbox-manager v1.1.0

- Volitelná práce po jednom e-mailu v chatu; úplná evidence a původní mapování rozhodnutí zůstávají soukromě. Stávající nastavení se aktualizací nemění.
- Návrh provozu podle praktického používání: málo štítků, ochrana důležitých odpovědí, přesně schvalovaný opakovaný úklid, přímá zpětná vazba a volitelně tři denní běhy.
- Přenositelný Gmail dekodér a parser odpovědí CLI, trvalé projektové adaptéry bez závislosti na dočasných souborech. Zachována všechna omezení odesílání a ochrana konceptů.
- Žádné osobní profily, skutečná korespondence ani přístupy nejsou součástí aktualizace.

## 2026-10-19 - inbox-manager v1.0.1 (pracovní, nevydáno)

- Trvalý přenositelný Gmail adaptér bez závislosti na dočasných souborech; dekódování MIME a konceptů, prázdné odpovědi CLI a povinné dokončení neúspěšných běhů.

## 0.11.4 - 2026-10-02

### What changed

Provider label aliases and header-name casing are normalized at ingestion. Draft confirmation compares exact reply-threading values while accepting provider-added presentation headers. Research returns bounded evidence; the coordinator saves packets, preparation and source receipts. Owner waits without configured mailbox work no longer create cleanup debt. Fresh complete observations account for exact verified bot drafts, and verified scoped revisions appear as draft review rather than unanswered decisions.

New and changed cases can now submit one assessment containing business scope, checked evidence,
precise questions and an optional reply/summary. The runtime freezes the checker evidence and binds
candidate dependencies/threading. Authenticated draft revisions accept one desired target and freeze
the actual merged replacement before independent review; fresh pre-action drift requires a new check.
Legacy explicit revision inputs and in-flight recovery retain their original bindings.
Incomplete dashboards now list each unfinished bot obligation and unassessed email with
its conversation link, while keeping owner decisions separate.

### Why it matters

Fresh category movement no longer inherits an old Primary admission after native label evidence is lost. Correctly stored drafts no longer enter false verification recovery because the provider added headers. Current packet references can be reused without repeating state choreography. The normal path
avoids separately reauthoring case, preparation and candidate evidence. Checked summaries describe
current draft coverage and become stale after material manual edits; no summary implies fulfillment. Ordinary owner follow-ups can finish while the dashboard keeps their drafts and unrelated decisions visible.

Draft readiness now follows the verified obligation version, so a reopened ask cannot inherit an older draft's ready status. The saved draft stays visible when it needs refreshing. Fresh exact revision-backed reconciliation restores readiness after scoped batch answers while preserving unrelated decisions.

### What you need to do

For 0.11.3, update the plugin and reload its skills/scripts or start a fresh chat; keep your
existing inbox home. No permission change or workflow adoption is required for that update.
For older installs, ask `/inbox check my existing setup after the update` to verify effective
scope, capabilities and scheduling. Adopting changed defaults requires approval and schedule
rebinding. Sending still requires current authority and an independent isolated check.
This candidate is under review and has not been released.


## 0.11.3 - 2026-09-16

### What changed

The `/inbox` router now names the research, reply, independent-check, review, and learning stages
that complete a selected workflow. Owner feedback that should revise existing Gmail drafts routes
explicitly through review, and the router continues until the chosen workflow has produced its
required final report instead of stopping after the first internal handoff.

### Why it matters

Inbox Manager follows one visible path from the owner's request through evidence, draft work,
review, safe learning, and a complete report. Requests to update drafts are less likely to stall at
the front door or finish without returning the actionable result the owner expected.

### What you need to do

Update the staging plugin and start a fresh task so the concise router instructions reload. No
mailbox, database, schedule, permission, or authority migration is required. Installation itself
does not process mail, revise drafts, send replies, or change automation settings.

## 0.11.2 - 2026-09-16

### What changed

Scheduled prompts now explicitly request the complete periodic inbox report even when nothing
changed. When a host requires a heartbeat notification block, Inbox Manager puts the full report
first wherever the host permits and keeps the short notification as a supplement.

### Why it matters

The owner can act from the latest scheduled response alone instead of reconstructing outstanding
work from earlier runs. Unchanged attention items, waiting items, draft states, numbered questions,
and email links remain visible on every periodic report.

### What you need to do

No mailbox, database, schedule, or permission migration is required. After installing the update,
reload the plugin in a fresh task so future scheduled prompts use the complete-report contract.
Installation itself does not process mail, send replies, or change existing automation settings.

## 0.9.0 - 2026-09-08

### What changed

- Full Inbox discovery prioritizes confirmed Primary and Updates tabs, includes older and
  already-read mail, and resumes each category's pagination independently.
- Reports distinguish processed mail from resolved conversations. Owner-held work can appear
  in a daily digest at an approved local time, with case-specific snooze and mute controls.
- Owner-resolution handling checks whether an existing archive rule now applies. Complete
  provider pages, current owner-answer references, and file-based JSON input are explicit.

### Why it matters

Older Primary messages no longer disappear behind a recent-mail filter. Pending owner work
stays visible without repeatedly rewriting replies, and resolved mail can leave the Inbox
under the owner's existing archive policy.

### What you need to do

Update the staging plugin and use `/inbox setup` to approve visible Gmail tabs and reminder
preferences. Existing accounts, sending restrictions, and saved work are preserved. Reminders
do not send email or close cases. Scheduling still depends on the verified host connection.

## 0.8.0 - 2026-09-07

### What changed

- A cycle runner handles snapshots, exact review binding, action preparation, provider
  read-back checks, and completion records.
- Conversations retain separate obligations, owners, deadlines, and owner decisions.
- Full conversation snapshots and distinct review checks preserve current facts and sending
  boundaries across sales, client, vendor, and scheduling work.
- Approved knowledge updates queue affected cases for rechecking. A private registry binds
  each inbox to its account and scheduler task, with missed-run and completion status.

### Why it matters

A useful reply can move forward without losing another outstanding request. Owner answers
apply to the selected decision, and failures remain visible and recoverable.

### What you need to do

Update the staging plugin and continue using `/inbox`. Existing homes and sending restrictions
are preserved. Verify the account and scheduler binding during setup before enabling a real
schedule. Live mailbox, real scheduler wake-up, and Claude runtime remain unverified for this
staging candidate; this update does not grant new sending authority.

## 0.7.0 - 2026-09-06

Inbox Manager now works toward an owner-defined inbox job instead of stopping at sorting and
draft preparation. This is a source and deterministic-runtime candidate; intended stock
Claude Code and Codex compatibility has not been observed in host sessions.

### What changed

- **One job, any inbox role.** Setup captures identity, purpose, completion, owner
  instructions, voice, selected knowledge, and bounded authority. Personal assistant and
  customer support are worked examples of the same engine rather than baked-in modes.
- **Bounded automatic replies.** A reply may send only inside an approved category and
  explicit recipient, privacy, commitment, and throughput limits. A fresh read-only checker
  independently inspects the exact body, recipients, attachments, evidence, case state, and
  authority. Hosts without that isolation stay draft-only.
- **Conversation state replaces inbox-zero accounting.** Durable cases preserve unresolved
  asks, ownership, due work, takeover, and operational holds. Archiving changes Gmail
  presentation but never resolves a business obligation.
- **Discovery is durable before handling.** Paginated provider scans save a fixed cutoff,
  next cursor, and every discovered trigger in the SQLite queue before the watermark moves;
  admitting a trigger to a case is the only way to remove it.
- **Gmail-native passive learning.** Reliably linked sent/edited drafts become scoped,
  reversible examples and low-risk preferences. Pending or ambiguous outcomes do not teach;
  bot sends never reward themselves; rule and authority changes remain owner-approved.
- **Eight core skills.** `inbox` routes; `inbox-setup`, `inbox-triage`, `inbox-research`,
  `inbox-reply`, `inbox-check`, `inbox-learn`, and `inbox-review` each own one judgment or
  workflow. `inbox-cleanup` remains a thin compatibility door into backlog mode.
- **Relevance-driven research and direct voice.** The current thread and only sources that
  could change the answer replace mandatory 90-day history searches. The separate humanizer
  and optional task-creation loops are removed from the active workflow.
- **Transactional runtime boundary.** New state belongs to one SQLite engine and an immutable
  approved profile projection. The legacy state rail remains only for archive/history
  compatibility and migration.
- **Provider evidence stays honest.** Sends use durable intent plus read-back and ambiguous-
  result reconciliation; accepted mail is not reported as delivered. Archive remains the
  verified undo-label-first rail, and mail is never deleted.

### Why it matters

Members can give their inbox a specific job, handle uncertain replies in Gmail, and improve
future decisions through normal use. Waiting conversations and owner decisions remain visible
instead of disappearing when a message is archived.

### What you need to do

Run `/inbox` to migrate or set up the job. Review the operating brief and authorize the
required provider capabilities before activation. Existing homes keep their data; prior
sorting or drafting approval never enables sending. Setup replaces the old scheduled loop
only after disabling it and verifying the new configuration. This staging candidate has no observed Claude Code or Codex host-session proof;
production release requires that proof and separate approval.

## 0.6.0 — 2026-07-27 (student V2 staging)

Escalation you actually receive, honest product framing, and the last overlap hazard in
the run protocol. The judgment layer — classification, the reply legitimacy gate, doctrine
reasoning, draft provenance — is **unchanged**. State schema 3 → 4, additive.

- **Escalation adapts to your connectors.** Setup enumerates the messaging connectors you
  actually have, you pick one or none, and it **sends a test message you have to confirm**
  before it will save the channel. The rail refuses a half-configured record, so a target
  nobody ever pinged can no longer sit in your config looking functional. Choose none and
  urgent mail gets the `Urgent` label — now genuinely created at setup, alongside the undo
  label — and leads your run report.
- **Reply drafting ships off by default, labelled beta.** It is built and reviewed but not
  yet proven on a reply-heavy live mailbox, so it is opt-in and the product copy leads with
  triage. Nothing is lost with it off: reply-worthy mail still lands in Needs attention with
  a direct link. The legitimacy gate is untouched.
- **Two passes can no longer collide.** A run takes a lease; a second `begin-run` inside a
  live lease is refused with `run_in_progress` instead of seizing the first run and
  stranding its Gmail writes with no ledger row. `record` renews the lease; only an expired
  one is recovered as interrupted.
- **Report counts come from the ledger.** `run-counts` returns the tallies and the report
  quotes them. Draft and task ledger rows now carry their run id so they can be counted.
- **Setup's exit gate is enforced, not decorative, and it now runs on fresh setups too.**
  `complete-upgrade` checks the choices setup cannot default — draft mode, scope, transport,
  the two label ids, and the escalation decision — in one atomic write, and names exactly
  what is missing. It used to check the draft choice alone, on upgrades only, which left a
  half-configured home running on a schedule for two weeks.
- **Parked questions surface and stay countable.** Every run parks them, headless included;
  they are markdown checkboxes; and the open count leads the report, the digest, and the
  router's next action.
- **Doctrine is reconciled against real capabilities.** `validate-doctrine` flags rules
  commanding unsubscribe, delete, auto-send, forward, or sender-blocking — things the
  product does not do — and setup or review walks the rewrite with you.
- **`/inbox stop`.** Disables and verifies the scheduled task, states plainly what stays on
  disk, and points at connector revocation.
- **Routing stays cheap at backlog scale.** `read` returns the retry set's size; triage
  fetches the rows with `retry-set`.
- Student bundles ship the product and nothing else — 14 files. The authoring trail
  (`brief.md`, `research.md`, `feasibility.md`, `rework/`, `fixtures/`, `spec-*.md`) joins
  the evidence and baseline directories that were already excluded.

## 0.5.0 — 2026-07-23 (student V2 staging)

Operational-resilience patch from the 17-day release-candidate field trial. The proven
classification, legitimacy, draft-provenance, and consent judgment stay intact.

- **No mail orphaned by the pass cap.** Every run now writes a full-window selection
  manifest, durably queues capped or failed work, recovers interrupted runs, and advances
  the watermark only after `complete-run` accounts for every selected message.
- **One-time orphan recovery.** Existing homes sweep older unlabeled inbox mail that a
  pre-v0.5 cap may have skipped, with progress visible until the sweep is empty.
- **Verified undo before archive.** The fixed order is apply undo label → verify it is
  present → remove `INBOX` → record. Pinned ledger rows require run/message/thread/date/
  sender/subject identity, arrays for labels, and provider proof for claimed writes.
- **Level-3 ceiling.** Send/trash/delete are absent from the product; stored level-4
  configuration migrates safely to level 3.
- **Clearer operations.** Status answers flag stale scheduled success relative to cadence
  and distinguish a student-paused task from a task whose runs are failing. Connector
  repair now means reconnect → harmless search/read probe → reversible label round-trip,
  with explicit read-only degradation and outage escalation if repair fails.

## 0.4.0 (superseded before release)

Contextual reply preparation. Triage no longer stops at sorting: every legitimate email
that expects a reply gets a grounded draft or an honest blocked status. The level-3
write ceiling and read-only-worker boundary remain unchanged.

- **Legitimacy before research.** Cold pitches, bulk cadences, suppressed senders, and
  doctrine-matched spam never enter reply preparation just because they contain a question.
- **Adaptive context ladder.** Read the complete thread, search 90 days of sender/company
  email history, and expand to at most 12 months only when evidence points backward. Then,
  with individual setup consent, inspect matching calendar events, meeting notes, tasks,
  conditional Slack context, and linked artifacts needed to answer the request.
- **Substantive-or-blocked rule.** Missing evidence yields `draft_blocked` with the exact
  gap, never a polite holding reply that hands the prerequisite work back to the student.
- **Five auditable draft outcomes.** `draft_created`, `draft_updated`, `draft_preserved`,
  `draft_blocked`, or `no_draft`—one per candidate.
- **Draft and task idempotency.** Plugin draft IDs and body hashes protect human edits.
  Existing tasks are searched and linked before create; changes are proposed, never silently
  applied.
- **Default 10 reply candidates per run**, configurable during setup and later. Overflow
  is durable in `reply-queue.json`.
- **Linked action queues.** Reports and optional Slack digests lead with direct Gmail links
  for Drafts ready and actionable Needs attention items. Private context packets stay local.
- **Safe upgrade.** Schema-1 state migrates additively; setup offers a previewed 30-day
  backfill and never runs it silently. A persisted upgrade marker prevents the router from
  skipping required choices after migration.
- **Draft choice now means what it says.** `auto` performs legitimacy-gated preparation;
  `off` prevents admission and actuation while linking reply-worthy mail under Needs attention.
  The old undefined `flagged` choice is no longer offered.
- **Durable connection and schedule proof.** Optional sources cannot be newly enabled
  without a matching capability-card reference and probe time. Scheduled tasks are read
  back, saved by cadence/id, and carry the absolute inbox home; unverified schedules remain
  disabled instead of becoming ghost automation.

## 0.3.1 (superseded before release)

- **Drafts are humanized before Gmail.** Before creating Gmail drafts, the main thread now runs
  `content-humanizer` when available (or `humanizer` as fallback) over each worker candidate.
  If the skill is missing or fails, the draft is still created from the candidate text and the
  digest reports the fallback.

## 0.3.0 (superseded before release)

"Go deeper" round — four refinements from Gael's ~48h live-Gmail trial (spec:
`spec-v0.3.0.md`). Every new behavior is gated behind a setup-chosen depth posture; the
level-3 ceiling and the worker-can't-act injection boundary are unchanged.

- **Deep context before deciding.** For the emails that warrant it, the Sonnet workers now
  gather read-only context before bucketing/drafting: **Google Calendar** (is it scheduled?
  passed? did I RSVP? — feasibility-probed live), **fuller email history** (the prior thread),
  and a **local brain folder** (trusted markdown notes you point setup at). Sources are
  extensible and chosen at setup. Context-gathering stays inside the read-only workers, so the
  injection boundary holds.
- **Drafts by default.** `draft_mode: auto` pre-writes a reply for any reply-worthy Keep —
  candidate text comes from batched Sonnet workers and is created (never sent) by the main
  thread. `flagged`/`off` available.
- **Past-event default.** An event notification whose date has passed is archived + labeled
  (reversible), bounded to the notification itself — not follow-ups that still need action.
- **Closing doctrine interview.** A supervised pass ends with a short, self-skipping
  `AskUserQuestion` round to lock in what the session surfaced; `inbox-review` stays the
  deeper standalone walk.
- **Depth posture (`context_depth`) asked at setup:** `cheap` (deep only on supervised runs),
  `scheduled_capped` (deep + budgeted on schedule — recommended), or `full` (deep every run).
  Cleanup stays snippet-first regardless (bulk bucketing, no per-email deep lookups).
- **Suggested next actions + optional task capture.** Suggestions ride the digest; when a task
  system (Notion, etc.) is connected + enabled at setup, they become real tasks (level-3-class,
  capped, doctrine-grounded).
- State rail: `set-config` allowlist grows `context_depth` + `context_sources` + `draft_mode`
  (validated, `context_sources` merges key-by-key); ledger tolerates a `task` action with a
  `tasks_created` stat; pytest extended (24 pass).

## 0.2.0 (superseded before release)

Field-trial round — eight decisions from Gael's live run (spec: "V0.2.0 Delta").

- **Unsubscribe removed at every level** (was a level-4 verb); replaced by suppression —
  real Gmail filters via the GWS CLI when present, a confirmed-only `Suppress` doctrine
  bucket otherwise. Filter creation is always user-confirmed, never silent.
- **GWS CLI (`gws`) was added as an optional transport** when installed + authed: it replaces
  the Gmail MCP on the capability card, exposes batch ops and filter management (with a documented scope-bug
  fallback). MCP remains the student default.
- **Worker fan-out:** above ~30 emails (and always in cleanup), parallel read-only Sonnet
  subagents classify and return structured verdicts; the main thread merges and holds all
  write verbs — cost routing + injection isolation in one boundary.
- **Supervised mode on human-invoked runs:** novel situations get AskUserQuestion (capped
  ~5–8/pass), confirmed answers write to doctrine immediately; scheduled runs stay
  park-don't-ask.
- **Label recon is a hard setup checkpoint:** existing labels surveyed and reused first,
  taxonomy confirmed via non-skippable AskUserQuestion (fixes the field-trial "it just
  went" failure).
- **Outbound channel messages never solicit replies** — single CTA back to Claude Code.
- **Asymmetric doctrine learning on unattended runs:** rescues auto-loosen (stop archiving,
  stamped + digest-surfaced); tightening changes remain confirm-only.
- **Inbox scope asked at setup** (`scope: primary|all`): Primary processed first and fully;
  other tabs get an escalate-signals-only scan, never archived.
- State rail: `set-config` allowlist grows `scope` + `transport`, validated + tested.

## 0.1.0 (superseded before release)

First version. Gmail-first.

- Orchestrator (`inbox`) + four components (`inbox-setup`, `inbox-triage`,
  `inbox-review`, `inbox-cleanup`).
- Unattended scheduled triage with a trigger-agnostic pass: watermark, 100-email cap,
  per-message ledger, undo label, correction detection.
- Inference-first setup: rules learned from a 30–100 email sample, confirmed in batches.
- Learning loop: parked unknowns + detected corrections → confirmed doctrine rules via
  `inbox-review`. Headless runs never write doctrine.
- Optional Slack escalation + digest heartbeat; degrades to `Urgent` label + report
  when no channel is connected.
- Level-3 write ceiling (label/archive/draft); later v0.5 removed the unfinished
  send/delete configuration entirely.
- Backlog deep-clean (`inbox-cleanup`), chunked and resumable.
- Self-contained state rail (`scripts/inbox_state.py`, Python 3 stdlib), pytest-covered.
- Config writes go through a `set-config` rail (validates `write_level`, merges the label
  map) so the model never hand-edits `state.json`.

### Fixed pre-release (final review)

- Correction detection now feeds a current-inbox snapshot, so a rescued *old-dated* email
  is caught (was previously invisible behind the watermark window).
- Every triaged message is recorded, so Keeps/parked mail can't stall the watermark and
  re-trigger duplicate questions each pass.

### Not in this version

- Outlook / Microsoft 365 triage — blocked on a write-capable connector (the current
  official M365 connector is read-only for mail). Capability-card design makes it
  onboardable when one exists.
- Multi-inbox merged runs (layout is already multi-inbox-shaped).
- Unsubscribe automation; autonomous sending as a default.
