# Durable Gmail adapter

Use the bundled `scripts/inbox_gmail.py` from the resolved plugin root. Import it
with `importlib.util.spec_from_file_location`; never rebuild runtime helpers in a
temporary directory. Persistent custom orchestration belongs in the owner's
project directory, outside the installed plugin/cache. Resolve the installed
plugin root anew after updates. Keep account selection and CLI configuration in
the owner's existing approved connection, never in the distributed scripts.

`message(raw)` accepts an actual Gmail `format=full` message and returns decoded
fields with MIME evidence. Apply it to every message in the complete thread and
use `draft(raw_draft)` for each complete Gmail Draft resource. It preserves both
the outer draft ID and message ID and builds the revision contract payload. Original MIME stays separately in `provider_mime`
as observation evidence; never replay its stale encoded body as a revised draft.
The bundled revision path supports only a single plain-text body without attachments.
HTML, multipart/alternative, attachments and inline parts remain observable, but carry
`revision_unsupported`; the runtime rejects their revisions before checking/writing.
Keep those existing drafts untouched and report the limitation. Do not strip the marker,
downgrade them to plain text or create a duplicate draft to bypass the hold.
Decoding errors or repeated identity/content headers block this packet; do not silently
substitute empty bodies or discard evidence. Fetch external text body parts when
Gmail supplies an attachment ID, before claiming complete decoded content.

Use `cli_result(stdout, returncode)` to parse CLI responses. Empty stdout with
exit zero is valid for mutations, but does not prove the intended change: obtain
fresh provider readback. A missing read response is incomplete evidence. Save
stderr privately and surface concrete errors without exposing credentials.

This helper does not discover drafts or establish completeness. Use
`inbox_retrieval.py` for a fresh, fully paginated draft inventory, fetch every
relevant draft body, and construct packets with honest provider read timestamps.
Do not replace existing inventory or lease contracts with this decoder.

For each run persist the begin result and lease in project-local evidence. Always
finish the same run with its actual outcome, including partial/failure, and fresh
scheduler readback when required. Missing helpers are a technical failure, not a
successful empty inbox scan. This repair grants no new send/archive authority.
