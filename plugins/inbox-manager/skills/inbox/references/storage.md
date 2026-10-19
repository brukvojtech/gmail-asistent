# Storage and recovery

The mailbox-local SQLite database is the durable state across fresh Codex tasks. Small
configuration lives in its header; growing cases, actions, observations, revisions, and
reports are indexed records. Search learned knowledge with `knowledge-search`, then fetch
only useful evidence with `knowledge-lookup`. Do not dump the database to process a run.

An existing schema-1 database migrates transactionally on its first write. The runtime
creates a consistent `migration-<hash>.sqlite3` backup first. Older binaries must not write a
schema-2 database. Migration preserves configuration and authority. Use owner-authorized
`adopt-workflow` to enable researched drafts and the 90-day body policy for an existing job;
this does not enable sending or claim owner attribution that the provider cannot prove.

Cycle finish runs a bounded maintenance batch and saves its progress. `maintain` can also
run a batch explicitly with optional `limit` (1–500). `knowledge-maintain` separately
maintains knowledge evidence. Useful notes survive body expiry; unresolved action recovery
material is retained until the result is known. Explicit full diagnostic reads remain
available; they are not the normal run path.

Use `inbox_engine.py COMMAND --home HOME --input FILE` for offline recovery:

- `storage-health`: `{ "mailbox": "owner@example.com" }` checks database integrity and returns
  table counts without loading historical records into the model context.
- `storage-backup`: the same mailbox plus `target_path` creates a consistent database backup,
  including uncheckpointed WAL data and knowledge tables. The target must not exist.
- `storage-restore`: the same mailbox plus `backup_path` and optional `sha256` restores into a
  new, empty HOME only. It never overwrites a running inbox. Restore the job's profile files
  separately before resuming; referenced local knowledge files and provider connections are
  not contained in a database backup. Verify the profile and fresh provider state first.

Keep backups private. These commands do not send mail, update drafts, change schedules, or
install a plugin. A successful database integrity check is not proof that external sources,
provider access, or scheduling are currently working.
