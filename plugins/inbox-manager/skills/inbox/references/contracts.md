# Compatibility pointer: runtime contracts

The runtime owner is [`runtime-contract.md`](runtime-contract.md); cycle orchestration is
[`cycle-contract.md`](cycle-contract.md), and the owner feedback/revision behavior is
[`feedback-loop.md`](feedback-loop.md). New state, cleanup, knowledge, and undo history use
`scripts/inbox_engine.py`; the legacy script is fenced after import.
