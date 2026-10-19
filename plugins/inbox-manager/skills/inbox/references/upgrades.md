# Updating an existing inbox

Installation updates plugin code. It does not select a mailbox, move the inbox home, adopt
new operating defaults, or grant authority. Keep the same plugin identity and data home.

For an existing owner, start a fresh chat or reload the updated skills and scripts, then
resolve the existing home through the router. Never initialize a replacement home to make
an upgrade work. Use only the loaded version's scripts for the whole run.

## Check before resuming

1. Verify the connected account matches the home's saved mailbox. Read status and the saved
   profile. Preserve sources, access boundaries, authority, retention, labels, history,
   pending decisions, action identities and uncertain outcomes.
2. Show the effective processing scope. Preserve explicitly approved `processing_tabs` and
   its `scope_ref`. Older `enabled_tabs` describe visible Gmail tabs and cannot substitute
   for approved processing scope. When that approval is absent, explain that current scope
   is Primary only, retain excluded work, and ask only if the owner wants broader coverage.
3. Refresh provider capabilities if the host, account, connector or mapped operation changed.
   A saved successful probe does not prove the new connector returns complete threads or
   preserves a draft's MIME parts. Hold only actions needing the missing capability.
4. Read pending work before acting. Refresh old drafts against current mail and evidence;
   keep manual drafts and edits protected. Reconcile creating, attempting and unknown
   actions before any retry. Keep their original IDs, checked hashes and receipts.
5. Inspect the existing scheduler binding and its observed prompt. An unchanged approved
   profile may continue with the same valid binding. A missing/stale binding or a prompt
   tied to an obsolete install path needs repair through setup before scheduled dispatch.
   Report native wake-up as unverified until actually observed.

SQLite storage upgrades are additive on the first write and preserve a private backup of
the older database. Read-only status does not migrate storage. This does not establish
compatibility for every historical format; an unsupported home needs explicit migration,
never reset or guessed state conversion.

## Adopting changed behavior

Adoption is separate from updating code. Preview the actual `adopt-workflow` bundle: it
enables draft-first behavior, sets copied-body retention to 90 days and seeds the research
guide from existing approved sources. Obtain approval for that whole bundle before calling
the authenticated command; it cannot selectively retain a shorter retention setting.
Separately approved scope or notification changes use `configure`. Installation alone
never authorizes longer retention or new mail actions.

Adoption changes the profile version. Complete setup's pause, rebind, generated-prompt
update, reactivation and actual scheduler readback in the same request. The old binding
must reject dispatch until the replacement is valid. Keep the existing native task when
the host supports safe rebinding; never create a second active scheduler as a shortcut.

For 0.11.3 to 0.11.4, the saved storage, registry and schedule-binding formats are unchanged.
Legacy separate case/preparation/candidate inputs and explicit revision patches remain
supported, including saved in-flight recovery. No home reset, reconnect, new permissions
or workflow adoption is required solely for that version update. Verify the actual host and
home rather than promising that every installation needs no action.
