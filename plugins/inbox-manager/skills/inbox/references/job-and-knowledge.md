# Job and knowledge

This file owns the job profile, instruction precedence, source registry, retrieval
sufficiency, freshness, and disclosure scope.

## Profile

One inbox home has one active job and these owner-editable sources:

```text
job.md
instructions.md
voice.md
knowledge/index.json
```

`job.md` answers:

- Who does the inbox represent?
- What requests belong to the job?
- What outcome should the work produce?
- What counts as handled, waiting, transferred, or closed without response?

`instructions.md` holds explicit handling rules, exceptions, commitments, and restrictions.
`voice.md` holds tone, structure, signature, examples, and phrases to avoid. The knowledge
index records the selected sources and a short `research_guide`: each approved source ID and
the questions or purpose it serves. Source records retain precedence and refresh guidance.
Targeted context may also use reads already permitted by
current workspace rules when the approved profile inherits those rules. An explicit
listed-source restriction wins; availability alone never grants access. See the
[context-source contract](operations-contract.md#targeted-context-sources) for registering
one relevant source without changing action authority.

The research guide routes reads; it does not grant them. Existing `source_policy` and
`context_access` remain the access boundary. Connector availability alone grants neither
source scope nor disclosure.

The runtime stores an immutable projection of the approved profile with a version and
approval receipt. Editable files explain owner intent; they do not silently expand
executable authority. Reconfigure after a material edit.

## Precedence

1. Host/provider limits and active owner action authority constrain every action.
2. A verified owner's explicit decision applies to its stated case or approved scope.
3. Saved instructions govern handling; the job defines priorities and completion; bundled
   defaults fill genuine gaps.
4. Selected knowledge supplies facts according to its authority, disclosure scope, and
   freshness. Current approved policy normally beats an old reply.
5. Explicit scoped owner facts/procedures and current reliably attributed corrections can
   supply reusable knowledge. Learned examples, voice samples, and correspondence supply
   context and preferences. None can create permission, sources, recipients, or policy.

A material unresolved conflict pauses only the affected claim or case. Do not average
contradictory sources into a confident answer.

## Source record

For every source retain:

- stable identity and origin;
- allowed job/case scope;
- intended use and source authority;
- `public-answer`, `internal-guidance`, or `account-specific` disclosure class;
- last verified time/version and refresh rule;
- access boundary and any identity requirement.

Access to a document is not permission to disclose it. Account-specific information requires
the job's identity verification; an email display name is insufficient. Historical replies
are dated precedents, not policy sources.

## Retrieval

Start with the complete current thread and relevant scoped knowledge, then ask: **which
evidence would actually change the answer?** Follow the guide only through approved sources
that can resolve that question. A commercial enquiry
may need a service or pricing guide; a client request may need the
agreed scope and latest project record; a scheduling proposal may need owner-approved
availability; a product question may need one current help article. Do not search a fixed history window or every connector for every reply.

External web research is used only when the source policy allows it. It cannot replace
missing private business facts. Read a linked artifact when the sender asks for feedback or
the answer depends on it.

Stop when the actual ask, ownership, material facts, and commitments are supported. If a
required source is stale, refresh it within policy or return the precise gap. Record concise
facts and pointers, not raw source dumps.

The engine exposes expired, unavailable, and locally changed sources as refresh work.
Record actual approved-source refreshes through `source`; material changes queue unresolved
cases that depend on them. They do not automatically reopen resolved correspondence or
release an owner hold. Record recurring missing facts with `gap`; use `gap-resolve` only
after the owner-approved answer exists in a current registered source. Count distinct cases,
not repeated runs, when describing how often a gap occurs.

Freeze the relevant profile version, source versions/excerpts, and thread version for each
candidate. Before automatic sending, recheck live conversation state, active authority, and
material source freshness. A frozen snapshot supplies traceability, not permission to send
an obsolete answer.

Record the knowledge IDs used by a preparation. Correcting, forgetting, or expiring one queues
only unresolved dependent cases. A saved fact is useful only when the next applicable reply can
retrieve it; use bounded knowledge search/lookup rather than loading the full store.

## Example profiles

| | Personal assistant | Customer support |
|---|---|---|
| Job | Protect the owner's attention and keep commitments from slipping. | Resolve routine product questions accurately and surface exceptions. |
| Knowledge | Owner instructions, permitted correspondence, selected calendar, approved booking details. | Approved help centre and policies; scoped account context after identity verification. |
| Automatic example | Share the approved booking link when a legitimate contact requests a meeting; do not promise availability or book it. | Answer a setup question from the current help article and ask one needed diagnostic question. |
| Owner decision | New commitment, disputed ownership, unresolved deadline, or owner-only fact. | Policy exception, conflicting docs, identity-sensitive request, or action outside email authority. |
| Handled | Obligation resolved or explicitly transferred; waiting commitments retain owner and due time. | Evidence-backed answer sent with no remaining supported next action; requested diagnostics remain waiting. |

These examples calibrate the engine. They are not personal-inbox defaults or universal rules.
