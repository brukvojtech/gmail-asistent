# Reply preparation

The reply workflow is split across
[`job-and-knowledge.md`](job-and-knowledge.md),
[`reply-quality.md`](reply-quality.md), and
[`action-policy.md`](action-policy.md), with owner answers and revisions in
[`feedback-loop.md`](feedback-loop.md). Research is relevance-driven; a fixed 90-day search,
task creation, and a separate humanizer pass are no longer part of the active workflow.

Start with the complete current thread and the smallest set of approved sources that could
change the answer. Inspect relevant available sources before asking the owner. An unavailable
source is a gap only for claims that depend on it; continue to another approved source when it
could answer the question.

For an explicitly selected expired local source, read its approved exact path. Submission of
the preparation renews that source from a bounded read and the SHA-256 of the bytes actually
read. If the hash changed, preparation returns the updated path and version for a new read and
does not create a draft-ready assessment from the old content. It does not renew unrelated
sources, and a source that is already current is not reread.
Expired connected sources require a fresh read with the approved model tool and registration
through the existing source command. A timestamp, connector listing, or remembered content is
not evidence of refresh. The saved `source_policy` and `context_access` limits still govern
every read.

Account for each stable obligation separately. Prepare a draft for every independently
supported bot obligation, even when another non-overlapping obligation needs an owner decision.
Bind the draft to the supported `obligation_ids` and persist the question against the other
IDs. If the decision controls the whole response, withhold the body and return the researched
options, recommendation, and precise question.

When preparation produces no draft, set `no_draft_reason` to `already_resolved`,
`no_reply_needed`, `another_owner`, `decision_controls_whole_reply`, or `blocker` as applicable.
Do not create filler, a fake status update, or a promise to investigate merely to ensure every
case has text.
