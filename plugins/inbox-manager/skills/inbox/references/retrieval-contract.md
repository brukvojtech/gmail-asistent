# Draft retrieval

Use the bundled deterministic helper when the verified connector can list drafts only for the
whole mailbox:

```text
python3 <plugin-root>/scripts/inbox_retrieval.py <command> --home <absolute-home> --input <private-json-file>
```

It uses the engine's SQLite transaction and lease. It never calls the provider. `inventory-plan`
and `packet` are read-only; `inventory-page` and `invalidate` require the current engine
`run_id` and `token`.

Read `inventory-plan` with the mailbox alone to recover the current inventory ID and cursor.
Only when no inventory exists, choose a new batch ID. For a known batch:

```json
{
  "mailbox": "support@example.com",
  "inventory_id": "draft-inventory-run-2026-09-11T10:00Z"
}
```

The compact result has `inventory_id`, `generation`, `requested_cursor`, `complete`,
`observed_at`, `count`, and `pages`. A new inventory returns `requested_cursor:null`,
`complete:false`, and count zero. A completed current inventory keeps returning
`complete:true`; do not list it again. An interrupted inventory returns its exact next cursor,
even in a later process. A different inventory ID is rejected until the current one is
explicitly invalidated, so a retry cannot silently restart at page one.

For the connector's draft-list call, pass only its verified `link_id`, `max_results:100`, and
the plan's `requested_cursor` as `next_page_token`. Providers may return fewer rows than requested. Treat the returned token, not the requested size or row count,
as the pagination fact. Map the page to `id` and `thread_id` only; do not fetch or pass draft
bodies while building this inventory.

Commit each observed page with the exact cursor used:

```json
{
  "run_id": "current-run-id",
  "token": "current-lease-token",
  "mailbox": "support@example.com",
  "inventory_id": "draft-inventory-run-2026-09-11T10:00Z",
  "page_cursor": null,
  "next_page_token": "provider-next-token",
  "observed_at": "2026-09-11T10:00:03+00:00",
  "evidence_ref": "exact provider draft-list result reference",
  "drafts": [
    {"id": "provider-draft-id", "thread_id": "provider-thread-id"}
  ]
}
```

Use explicit `next_page_token:null` only when the provider result proves pagination finished.
The helper rejects missing tokens, cursor loops, replayed cursors, duplicate draft IDs, an ID
moving between threads, decreasing/future read times, bodies in inventory pages, and mailbox or
inventory changes. Continue `inventory-plan` then provider-read then `inventory-page` until the
plan reports complete. Empty rows prove no drafts only after that explicit final null.

## Canonical packet assembly

After the inventory is complete, read one complete decoded thread, then fetch bodies only for
the inventory IDs bound to that thread. Keep `packet` stdout in a private file and pass its
`result.packet` programmatically to cycle intake/candidate/prepare; never dump it into chat. Build the packet with the read-only `packet` command:

```json
{
  "mailbox": "support@example.com",
  "inventory_id": "draft-inventory-run-2026-09-11T10:00Z",
  "thread_observed_at": "2026-09-11T10:01:04+00:00",
  "completeness": {
    "complete": true,
    "evidence_ref": "exact provider evidence that the full thread was returned"
  },
  "thread": {
    "thread_id": "provider-thread-id",
    "trigger_id": "provider-message-id",
    "automated": false,
    "messages": [
      {
        "id": "provider-message-id",
        "thread_id": "provider-thread-id",
        "from": "customer@example.com",
        "to": ["support@example.com"],
        "cc": [],
        "subject": "Question",
        "body": "Complete decoded body",
        "headers": {"Message-ID": "<provider-message-id@example.com>"}
      }
    ]
  },
  "drafts": []
}
```

Messages and relevant drafts use the canonical fields in the cycle contract; drafts also need
`bcc`. Preserve their complete decoded bodies, headers, recipients, and provider fields. The
set of draft body IDs must exactly equal the completed inventory's IDs for this thread. Missing,
extra, duplicate, cross-thread, or cross-mailbox drafts are rejected.

A thread call made with `max_messages:100` is not complete merely because it returned fewer
than 100 messages. Supply a separate `completeness` object with both `complete:true` and a
nonempty provider `evidence_ref`. The helper rejects missing evidence, false completeness, an
empty message array, and any result that declares `has_more`, `truncated`, or a next cursor.

The result contains `packet` and the snapshot built by `inbox_snapshot.py`. The canonical
packet carries `thread_completeness` and a `draft_inventory` proof marker with inventory ID,
generation, explicit completion, earliest inventory read, page/count totals, and a digest of
the page evidence references. It also carries `thread_observed_at` and `drafts_observed_at`.
Its main `observed_at` is the earlier of those two actual reads, preserving stale evidence
rather than using packet-assembly time.

Refresh once at cycle start, or once for a new action batch when the cached observation is stale
or relevant provider state changed. The existing fresh pre-action thread/snapshot age check still
applies; this cache does not add provider calls inside the engine.

After an actual draft-create or send result (`confirm`, `unknown`, or `failed`), invalidate the
inventory before another packet can use it. Do this after the provider result, not when reserving
the action, so one fresh inventory can support the bounded reads prepared before the first write.
Unknown and failed results still invalidate because the provider may have changed. Label-only
writes do not change the ID-only draft inventory and need no invalidation.

```json
{
  "run_id": "current-run-id",
  "token": "current-lease-token",
  "mailbox": "support@example.com",
  "inventory_id": "draft-inventory-run-2026-09-11T10:00Z",
  "reason": "provider draft write completed"
}
```

Run that input with `invalidate`. It records the reason and time, advances the generation, and
makes the old inventory unusable. The engine/cycle integration may call
`inbox_retrieval.invalidate_state(state, mailbox, inventory_id, reason)` after its own lease
check and recorded draft/send result. Start the next batch with a new inventory ID.
Repeating invalidation for the same already-invalidated inventory is an idempotent no-op, which
lets every result in one bounded concurrent batch finish recording safely.
