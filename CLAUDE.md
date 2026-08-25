# CLAUDE.md

Agent-facing working rules for this repository. Human-facing docs live in `README.md`;
when one changes, change the other in the same commit.

## Rule 0 — Quality over cost

Prioritize code and engineering quality over all else. Ignore engineering cost.

Time, token spend, and implementation effort are not constraints here. When there is a
tradeoff between "cheaper/faster to build" and "better designed, better tested, better
understood," always take the latter. Do not propose a shortcut and label it pragmatic.
Do not skip a test, an abstraction, or a verification step to save effort.

This rule outranks every other rule in this file. Where another rule appears to counsel
less rigor, Rule 0 wins.

Rule 0 and Rule 1 apply to every task in this repository, including this one.

## Rule 1 — Adversarial review gate after every task

Every task ends with an adversarial review. No task is finished, and no follow-up work
begins, until that review has run and every finding it produced has been resolved.

**When it fires.** After the last edit of any task that touched code, config, schema,
or docs — and before reporting the task done, before starting the next task, and before
any commit. It fires for one-line changes too. "Too small to review" is not a category
that exists here.

**What to run.** Exactly two adversary subagents, launched concurrently in a single
message, each pointed at the same change set (`git diff`, plus the untracked files the
task added). Give them the diff scope explicitly — never let them guess what changed.

Brief them as adversaries, not reviewers. Each gets a distinct attack angle so they do
not converge on the same easy findings:

- **Adversary A — correctness and contracts.** Hunt for logic that is wrong, not just
  ugly: broken edge cases, wrong error paths, unhandled failure modes, async/ordering
  bugs, state that can desync, schema or API shapes that changed under callers, and
  guardrail bypasses (a change that lets the system originate contact violates the
  project's one rule and is an automatic finding).
- **Adversary B — design, reuse, and verification.** Hunt for the thing that should not
  have been built this way: duplicated logic that an existing type/utility/service
  already covers, abstractions at the wrong altitude, dead or unreachable code, tests
  that assert the mock rather than the behavior, missing coverage for the branch just
  added, and any verification claim in the task's own summary that the diff does not
  support.

Require each adversary to return concrete findings — `file:line`, the specific input or
state that breaks, and the consequence. A finding with no failure scenario is not a
finding; instruct them to drop it rather than pad the list. Instruct them equally that
returning "looks good" without having tried to break the change is a failed review.

**Then they argue.** Send each adversary the other's findings and have them attempt to
refute them. A finding that survives refutation is real. A finding both agree is wrong
is dropped, with the reason recorded. Disagreement is the useful output — do not
average the two positions to make the conflict go away.

**Resolving before continuing.** For every surviving finding, exactly one of:

1. **Fix it**, then re-run the relevant test/lint/typecheck and report the actual result.
2. **Refute it in writing** — cite the code or test that proves it cannot happen. "I
   don't think that's an issue" is not a refutation.
3. **Escalate it** to the user as an explicit, named open question, with your
   recommendation — only when the resolution is genuinely the user's call (a product
   decision, a schema migration, a change to persisted shapes).

Never silently drop a finding, defer one to "later", or downgrade one to a TODO comment
in order to close out the task. If a fix cascades into more work than the original
change, that is the correct amount of work — Rule 0 governs, and the review re-runs on
the cascaded change.

Report the review honestly: what each adversary found, what survived the argument, what
was fixed, and what was refuted. Per the honesty rule, never describe a review you did
not actually run.

## Git and attribution

- Never commit, push, or deploy unless explicitly asked.
- Never attribute Git work to an AI, agent, or assistant in commit metadata.

## Honesty about verification

- Never claim a check (test, lint, typecheck) that you did not actually run.
- Run the cheapest relevant test first, then widen — file, scoped lint/types, full suite
  only when warranted.

## Changing code safely

- Search for an existing type, utility, or service before writing a new one. Shared test
  helpers live in `tests/helpers.py` (`FakeClock`, `make_director`, `reserve_call`,
  `UNSERVABLE_BUNDLE`)
  and shared pytest *fixtures* in `tests/conftest.py` (`unservable_persona`) — reuse them rather
  than rolling a third clock or a fourth director.
- Remove code only after a caller search plus a test or clear static proof it's dead.
- Preserve behavior when extracting duplicates; don't rewrite unrelated code while you're
  in there.
- Don't change database schemas without explaining migration impact, and don't change
  persisted API/response shapes without explicit authorization.

## Navigation discipline

- Start from the smallest named module or symbol; search before opening files. Prefer
  `git ls-files` and scoped `rg`, and never scan large data/artifact/cache directories.
- Read docs in layers — only pull in the map/architecture/testing docs when the change
  actually needs them.

## Docs hygiene

- When entrypoints, commands, or layout change, update the corresponding doc; keep
  agent-facing docs (`AGENTS.md`/`CLAUDE.md`) and human docs (`README.md`) in sync.
