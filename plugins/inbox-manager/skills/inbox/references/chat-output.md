# Chat output preferences

Read the selected inbox home's approved `chat-preferences.md`, when present.
An explicit current owner request wins. Otherwise retain the existing approved
reporting preference; a code update alone never changes it. New setup offers
focused mode and full dashboard mode, and records the owner's choice outside
the plugin. Before adoption or any approved preference edit, pause the existing task
and verify its disabled readback against its current exact prompt. Then use authenticated
configure with `chat_output: "profile"` and
`chat-preferences.md` in the approved `profile_files` hashes. The default `chat_output:
"full"` preserves legacy scheduled prompts byte for byte. Rebind that already paused task after
the approved mode change using the newly generated prompt, reactivate and verify readback.
Never mutate configuration or a hashed file before pausing the old task. Never use an ad-hoc
edited prompt.

## Focused mode

For a named email or decision, show only that item: what it asks, what has been
verified or prepared, the actual next step, and its account-bound Gmail link.
For an ordinary run, select one actionable item, prioritizing a time-sensitive
important reply or a concrete operational blocker. Ask one specific question
with a recommendation when a decision is needed. Wait for the owner's answer
before presenting another email. Do not append the rest of the inbox or repeat
unchanged obligations. If no item needs attention, give a brief truthful result;
use the host's quiet completion mechanism only when the approved notification
policy permits it. An explicit request for the full overview uses full dashboard
mode for that response without changing the saved preference.

Still obtain the runtime's complete report on every run. Save the exact rendered
report privately in that run's durable evidence outside the plugin/cache, retaining
the runtime report ID and its immutable question mapping. Do not make a second
report snapshot just to choose a chat item. Preserve the actual run completion,
coverage, pending work and failures in that evidence. Never call a partial scan
complete or a proposed reply sent. Mention partial coverage briefly in chat when
reporting the run, even though other email rows stay private. A failure that blocks
scheduled processing must be disclosed, not hidden by a quiet-mode preference.

When showing a numbered question, retain its original saved number and report
mapping; never renumber a filtered subset. Bind a short answer to the exact
question/case presented and the authenticated owner event. If multiple reports
or topics make the answer ambiguous, clarify before any write. Never display
unrelated private mail merely to reproduce a dashboard.

## Full dashboard mode

Follow the complete-report format in [owner-report.md](owner-report.md).
All open work remains in that report until resolved. Choosing focused mode only
changes what appears in chat; it never dismisses an obligation, skips assessment,
changes retention, or grants permission to send, label, archive or forward mail.
