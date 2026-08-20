# AGENTS.md

The working rules for this repository live in a single file: [CLAUDE.md](CLAUDE.md).

This file is a pointer so the two agent-facing filenames cannot drift apart. Edit
`CLAUDE.md`, not this file.

Two rules there are non-negotiable gates, restated here only because they change what
"done" means and must not be missed by an agent that opened this file first:

- **Rule 0 — Quality over cost.** Engineering cost is not a constraint. Never trade
  design, tests, or verification for effort.
- **Rule 1 — Adversarial review gate after every task.** Every task ends by running two
  adversary subagents concurrently against the change set; they argue their findings
  against each other, and every surviving finding must be fixed, refuted in writing with
  evidence, or escalated to the user before the task is reported done or any follow-up
  work begins.

Read [CLAUDE.md](CLAUDE.md) for the full text of both, and for everything else.
