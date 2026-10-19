# Mailbox cleanup and legacy import

Cleanup uses the engine database, run ID and lease. `scripts/inbox_archive.py` holds
the state operations; `scripts/inbox_state.py` is only a pre-import compatibility
tool. Never initialize a second state rail for a new mailbox.

Business completion and mailbox presentation are independent. A handled case with
failed cleanup stays handled; `cleanup-work` exposes its pending action for recovery.
Do not reopen research merely to retry labels.

## Approved setup policy

Put this policy under `config.cleanup` in an owner-approved engine `configure` call.
Use existing names and verified provider IDs; these examples are placeholders.

```json
{
  "archive_on_resolution": true,
  "waiting_policy": "keep_inbox",
  "undo_label": {"name": "AI/Archived", "id": "Label_undo"},
  "workflow_labels": {
    "attention": {"name": "AI/Attention", "id": "Label_attention"},
    "waiting": {"name": "AI/Waiting", "id": "Label_waiting"}
  },
  "approval_ref": "owner-approved-cleanup-policy"
}
```

Missing cleanup policy grants no archive authority. `waiting_policy: "archive"`
needs a separately approved choice: waiting obligations remain open, and due mail
returns to Inbox with the configured attention label. Drafts are always preserved;
automatic draft deletion is not a setup choice or cleanup task. Native snooze is unsupported.
Open owner-action cases receive the approved attention label and lose the waiting
label; this label-only action preserves their current Inbox visibility and drafts.

## Reserve, perform, verify

Use `prepare-cleanup` with the current engine lease, case ID and the exact complete
provider packet. Every message needs its actual `label_ids`; the complete draft
inventory is required. Example input:

```json
{
  "run_id": "cycle-1",
  "token": "current-engine-lease-token",
  "case_id": "case-42",
  "packet": {
    "mailbox": "support@example.com",
    "complete": true,
    "thread_id": "thread-42",
    "trigger_id": "message-99",
    "observed_at": "2026-09-10T10:00:00+00:00",
    "automated": false,
    "messages": [{
      "id": "message-99",
      "thread_id": "thread-42",
      "from": "customer@example.com",
      "to": ["support@example.com"],
      "cc": [],
      "subject": "Resolved question",
      "body": "Thanks, the issue is resolved.",
      "headers": {},
      "label_ids": ["CATEGORY_PERSONAL", "INBOX", "Label_topic", "Label_attention"]
    }],
    "drafts": []
  }
}
```

The engine commits the intention before returning `action_id`, `attempt`, `stage`
and `mutations`. Apply only those message IDs and label additions/removals through
the already-proven provider connection. Never substitute a thread-wide write: it
could archive mail arriving after the read. First-stage archive mutations add the
undo label; they never remove `INBOX`.

Read the complete thread again. Call `cleanup-result` with the same lease,
`action_id`, current `attempt`, actual readback `packet` and `provider_ref`. A valid
undo readback commits the next intention before returning Inbox/workflow-label
removals. Apply them, then send another fresh complete readback. Only `verified`
means cleanup completed. Keep each provider packet in code; do not rewrite it from
a model summary or create a permanent per-case file.

After a timeout, use `cleanup-result` with `error` to retain the failed action. On
restart, `prepare-cleanup` returns `reconcile_required` for existing pending work.
Read actual provider state and reconcile that action before retrying. Recovery
returns only outstanding mutations; repeating a verified result creates no write.

New/unassessed mail, human drafts, unresolved owner/bot obligations, holds and
uncertain sends prevent automatic archive. User labels survive. A user-restored
verified archive stays visible for review. Label failures never close obligations.
Gmail label writes change message history IDs. Readback compares message content
without label/history metadata while retaining the complete provider snapshot.

## Import and undo evidence

Run `import-archive` under the engine lease while all legacy runs are stopped.
The engine transaction reads `state.json`, every `ledger.jsonl` row and every
`runs/*.json` manifest. Import refuses a live legacy lease, malformed evidence,
missing terminal proof or changes after import. It retains exact source hashes,
semantic equivalence proof, undo receipts and interrupted selected/deferred/retry
inventory. It does not grant cleanup authority or delete/repair the originals.

After import, the compatibility script rejects commands against that mailbox.
Its entire CLI operation shares the engine database lock, preventing a concurrent
updated legacy writer from racing import. Retired plugin versions cannot be patched
retroactively: stop old runs and use the updated installed plugin before migrating.

`cleanup-work` lists imported pending messages until normal case assessment admits
them. Discovery alone does not settle that work. `undo-window` returns both retained
legacy receipts and verified engine archive receipts; these are reversal candidates,
not proof that an undo occurred. Restore `INBOX` and verify before removing the undo
label under the applicable owner authority. Keep provider reversal evidence.

Runtime callers use `policy`, `prepare`, `result`, `work`, `undo_window`,
`read_legacy`, `import_legacy`, and `reconcile_legacy_pending` from `inbox_archive`.
Mutators run inside the engine transaction; provider writes happen only after commit.
The `mailbox_actions` collection shares the database with send actions while keeping
cleanup out of send quotas, send recovery and send-only status counts.
