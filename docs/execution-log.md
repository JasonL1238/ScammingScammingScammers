# Execution log

The task-by-task record of executing [`roadmap.md`](roadmap.md). One entry per
task: scope, the Rule 1 adversarial-review outcome, red-proof evidence for any
new CI gate, and the verification-loop results. Each phase closes with the
roadmap's exit criteria checked off with evidence. An entry lands in the same
commit as its task wherever possible; evidence that can only exist after a push
(CI run URLs) follows in a recorded follow-up.

## Standing verification loop

Run before every commit. CI runs these same checks, plus the install-shape
matrix legs that only CI exercises on every push — so local green predicts push
green for everything except a dependency-resolution difference on a shape not
run locally.

1. Full suite via the project venv (`pytest`). Baselines per install shape,
   recorded at T1.1 — collected count and pass/skip must not regress without a
   deliberate, cited contract change:
   - 557 collected in every shape;
   - py3.13 + `[dev,media]` (the project venv): 555 passed / 2 skipped;
   - `[dev]`-only (no media): 556 passed / 1 skipped.
2. `python -m ssscammers.simscammer.textloop --all-scripts --dry` — exit 0.
3. `ruff check .` — clean.
4. If docker/compose was touched: `docker compose config -q`, postgres up
   against the existing volume, and (for compose restructures) a clean-checkout
   `up --build`.
5. `git status` — no unexpected modifications.

## Red-proof procedure

For every new CI gate: push a throwaway branch seeding the named regression,
**wait for the red run to complete and record its URL**, and only then delete
the branch (or push the revert). Deleting or reverting while the seeded run is
in flight would leave no red evidence. Main never contains the regression.

## Phase 1 — Groundwork: safety hygiene, CI, migration machinery — IN PROGRESS

### T1.1 — CI bootstrap

- **Scope:** `.github/workflows/ci.yml` (jobs: `tests` as the full
  python × install-shape matrix — py3.11/py3.13 × `[dev]`/`[dev,media]` —
  plus the `textloop` release gate on a base-only install and `lint` via
  ruff); pin `--import-mode=prepend` in `pyproject.toml`; sharpen the
  `tests/conftest.py` docstring; record the direct-to-main workflow decision
  in `roadmap.md`; reference this loop from `CLAUDE.md`/`AGENTS.md`; this log.
- **Pre-push verification (all executed locally):**
  - py3.13 + `[dev,media]` (project venv): 557 collected before and after the
    import-mode pin; 555 passed / 2 skipped.
  - py3.13 + `[dev]` (scratch venv, fresh resolve): 556 passed / 1 skipped.
  - py3.11 + `[dev]` (fresh env and resolve, executed during the adversarial
    review): 556 passed / 1 skipped.
  - py3.11 + `[dev,media]` (fresh conda env and resolve — the first-ever
    execution of this documented-supported shape): 555 passed / 2 skipped.
  - textloop `--all-scripts --dry` on a base-only scratch venv: exit 0.
  - `ruff check .` on the project venv and on a fresh ruff 0.16.4 resolve:
    clean.
- **Rule 1 review:** two adversaries (A: correctness/contracts, B:
  design/reuse/verification), then cross-refutation. Findings and outcomes:
  - *A-1 (survived):* the first draft of the pyproject pin comment claimed
    importlib mode fails the suite at collection — disproven by execution
    (suite passes under `--import-mode=importlib`; `tests/conftest.py`'s
    `sys.path.insert` is the mechanism in every mode). **Fixed:** comment
    rewritten to the true rationale (pin the proven default against upstream
    changes); the conftest docstring's ambiguous sentence sharpened.
  - *A-2 + B-F7 (merged, survived):* unconditional `cancel-in-progress`
    destroyed per-commit verification on main and would cancel seeded
    red-proof runs on throwaway branches. **Fixed:** the `concurrency` block
    removed entirely (its only benefit is compute, which Rule 0 does not
    optimize for); wait-for-red added to the red-proof procedure above.
  - *B-F1 (survived, strengthened by A):* this loop omitted ruff and the
    `[dev]`-only shape while claiming to be "the same checks" as CI.
    **Fixed:** ruff added to the loop; the prediction claim scoped honestly
    (matrix legs are CI-only); per-shape baselines recorded.
  - *B-F2 (survived):* `roadmap.md` still demanded merge-blocking CI while
    the owner had chosen a direct-to-main push gate, and the adaptation lived
    only in this log. **Fixed:** the decision recorded in `roadmap.md`'s
    header in the same commit.
  - *B-F3 (survived at reduced severity):* the loop was not referenced from
    `CLAUDE.md`/`AGENTS.md`. **Fixed:** one-line pointer added under
    "Honesty about verification".
  - *B-F4, B-F5 (survived):* pre-verification evidence covered two of three
    job configurations and the baseline ratchet conflated collected count
    with pass count. **Fixed:** the missing shapes executed and recorded;
    baselines restated per shape above.
  - *B-F6 (split):* "the diagonal matrix hides a broken cell today" —
    refuted by execution (py3.13+`[dev]` green). The design half survived:
    the matrix is now the full 2×2, including the never-before-executed
    py3.11+`[dev,media]` cell (pipecat declares `>=3.11`).
  - Dropped in cross-refutation: B's initial claim that the import-mode pin
    guards collection (retracted by B against A-1's evidence — the pin is a
    declaration; nothing behavioral changes if it is deleted today).
- **First green on main:** run
  [32897840253](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32897840253)
  (commit `ad91384`) — all six jobs passed.
- **Red proofs** (throwaway branches, deleted after their red runs completed;
  each failed exactly the targeted gate):
  - `dial()` call seeded in `twiml.py` — the SDK shape only the AST scanner
    catches: run
    [32898787431](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32898787431),
    all four `tests` matrix cells red via `test_no_outbound`, textloop and
    lint green. This is the "`<Dial>` verb fails CI" exit criterion.
  - Flipped `expect_phase` in `simscammer/scripts.py`: run
    [32898789782](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32898789782),
    the `textloop` gate red (the `tests` cells also red via
    `test_call_scripts`, as expected), lint green.
  - Unused `import os` appended to `shared/enums.py`: run
    [32899010924](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32899010924),
    only `ruff check` red. The first attempt at this seed (run
    [32898792333](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32898792333),
    green) was defective — the seeding script inserted the import inside the
    module docstring, so there was genuinely nothing to flag; the green run
    caught the bad seed, which is the red-proof rule working as intended.
- **Escalations:** none. (B-F2 was resolved by recording the owner's
  already-made workflow decision, not by making a new one.)

### T1.2 — PII purge + `_env_number` warning

- **Scope:** `.env.example` `OWNER_PII_DENYLIST` real values → placeholders,
  with the secret-store pointer and the git-history note; the real owner name
  renamed to fictional data ("Norbert"/"Quill"/`norbert@example.net`) across
  `tests/helpers.py`, `test_conversation.py`, `test_fiction_pack.py`,
  `test_persona_director.py`, `test_output_filter.py`, and the
  `output_filter.py` comment example; `_env_number` now warns on unparseable
  values (still falling back), with long values length-gated out of the log;
  new `tests/test_config.py` pinning the warnings, the silent paths, and the
  known parse-only limit.
- **Rule 1 review:** two adversaries, cross-refutation. Outcomes:
  - *A-1/B-1 (converged, survived):* the renamed substring test was strictly
    weaker than the "Li"-in-"Listen" test it replaced (interior embedding is
    rejected by either regex anchor alone). **Fixed:** parametrized
    interior/prefix/suffix probes, mutation-verified so each anchor is
    individually pinned — strictly stronger than the original.
  - *A-2 (dropped):* "the completed purge bullet in `roadmap.md` is now
    stale" — refuted by B: the roadmap pre-disclaims line-number drift, T1.1
    set the committed precedent (completion lives in this log, the plan stays
    as written), and re-tensing bullets per task would duplicate this log
    inside the plan document.
  - *A-3 (survived, refined by B):* the first warning draft echoed the raw
    env value verbatim — the branch fires exactly on operator mangling, and
    one mangling is pasting a secret onto a caps line. **Fixed:** values over
    16 characters are logged as length-only; companion test asserts a fake
    key never reaches the log stream.
  - *B-2 (survived):* the first `helpers.py` comment claimed the tracked
    tree carries no real owner PII, which the run URLs in this log falsify
    (the GitHub handle embeds the first name). **Fixed:** claim narrowed to
    the test data; the URLs stay — they are load-bearing evidence.
  - *B-3 (survived):* blank/whitespace values were unpinned. **Fixed:**
    parametrized unset/empty/whitespace silence tests.
  - *B-4 (dropped as a gate finding, acted on):* `_env_number`'s parse-only
    semantics predate this diff. A's fail-direction sweep of all 11 call
    sites found the one lax-direction cell — a zero/negative
    `PROBATION_HARD_COMMIT_SECONDS` commits to baiting with no triage
    window. **Resolved:** a `TestKnownLimits` test documents the parse-only
    contract; range-guarding the probation pair is filed as a named
    follow-up task.
  - *B-5 (dropped):* asserting `'5O'` pins `repr` rendering — both agreed
    the quoting is load-bearing (whitespace mangling stays visible) and the
    pin survived the A-3 message restructure unchanged.
- **Verification (executed on the final tree):** 570 collected;
  py3.13 + `[dev,media]` 568 passed / 2 skipped; `[dev]`-only 569 passed /
  1 skipped (+13 over T1.1: 11 config tests, 2 anchor probes); textloop
  `--all-scripts --dry` exit 0; `ruff check .` clean; `rg -in
  "jason|zenblen" .env.example` empty; repo-wide grep clean outside this
  log's run URLs.
- **Escalations:** none. Follow-up filed: range-guard the probation caps.

### T1.3 — Docs re-tensing sweep

- **Scope:** every doc, docstring, comment, and loaded-description claim stating
  unbuilt behavior as present fact, found by a 5-agent parallel sweep over
  `docs/`, `README.md`, module docstrings, SQL headers, persona YAMLs, and
  playbooks, each claim verified against code before flagging. 24 unique
  claims adjudicated; 17 sites edited across 18 files.
- **Claims fixed (claim → action):** `legal.md` seven-day deletion, R2
  migration, and "Redaction is what the system does" → re-tensed as designed,
  with today's reality stated; `legal.md` notice paragraph → rewrote the
  false "clip in the persona's voice" (contradicted `twiml.py`'s own
  "never persona-flavoured" design) to the real clip-or-fixed-text guarantee;
  `legal.md` no-outbound CI clause → tightened to the scan's package scope;
  `twiml.py` "enrichment worker already transcribes" → planned; `llm.py` +
  `guardrails.md` "no line of llm.py executes" under `--dry` → "none of its
  request construction runs" (dry runs import the module and build `Turn`s);
  `triage.py` loader-refresh claim → the seam is unwired, cache empty in
  production; `conversation.py` EventSink docstrings → LoggingEventSink is
  the only production implementation; `persona.py` stale system-message
  mechanism → the reminder-in-newest-caller-turn mechanism (stale since the
  Sonnet 5 switch); `shared/__init__.py` + four `enums.py` docstrings →
  enrichment/dashboard marked planned; `001_initial.sql` "generated from
  enums.py" → "mirror by hand" (the drift test claim, now true, kept);
  `secrets.md` SOPS injection and control-plane split → prescriptive,
  today's single-process reality stated; `.env.example` header → future-
  tensed; `webhooks.py` retention comment → "declared… not yet enforced";
  `guardrails.md` G-15 named-volume clause → atomic-writes-to-disk with the
  compose mapping named as declaration; `guardrails.md` misroute sentence →
  "released within two turns (disclosure or emergency redirect)" matching
  the test exactly (the old wording was false for `real_emergency`);
  `scripts.py` "These gate releases" → scoped to declared expectations;
  marjorie sound-pack comment → clips not in repo, degrade to silence;
  dot description → no longer claims the ElevenLabs engine her config moved
  off. Verified-true claims kept unchanged: `README.md` CI paragraph,
  `fiction.py` CI-verified invariants, `001_initial.sql` drift-test line.
- **Recorded exemption — `playbooks/core_rules.md:4`.** Its enforcement
  overstatement ("text that breaks them is caught before it is spoken") is
  deliberately NOT edited: playbooks are inlined byte-for-byte into the
  system prompt in front of the cache breakpoint, so any edit is a live
  behavior change requiring a wet textloop run; the claim is true for the
  safety-critical subset (the pre-TTS filter) and the PROMPT-only gap is
  already disclosed to human readers in `guardrails.md`'s table; the
  deterrent framing is prompt engineering, and Phase 3's MONITOR makes it
  fully true. Do not "fix" the playbook in a future sweep without reading
  this rationale.
- **Rule 1 review:** two adversaries, cross-refutation. Outcomes:
  - *A-F1/B-F1 (converged, survived):* the sweep's own `triage.py` rewrite
    introduced a fresh false claim ("populates it via `set`" — nothing
    populates the cache in production; it stays empty for the process
    lifetime). **Fixed** and re-verified clause-by-clause.
  - *A-F2/B-F2 (survived):* `secrets.md`'s first fix claimed the agent
    "reads a local `.env` directly" (nothing parses `.env`), and
    `.env.example` still carried the same present-tense SOPS claim.
    **Both fixed.**
  - *A-F3/A-F4 (survived, policy-checked):* this diff falsified
    `roadmap.md`'s workstream B "Today" column, and the K row / Phase 1
    goal "no CI" facts were stale since T1.1. **Fixed** as factual-state
    patches; imperative bullets untouched, per the T1.2-settled
    distinction — confirmed compliant by both adversaries.
  - *B-F3/B-F4 (survived):* compose's two present-tense dashboard comments
    and `triage.py:218`'s dashboard reference. **Fixed** ("planned").
  - *A-F5 (dropped — no failure scenario; fix retained):* "only
    implementation" → "only production implementation" kept as a one-word
    precision improvement, recorded as such rather than a surviving finding.
  - *B-F5 (resolved):* the playbook exemption above, recorded here as the
    condition of the adversaries' sign-off.
- **Verification (final tree):** 570 collected, 568 passed / 2 skipped;
  textloop `--all-scripts --dry` exit 0; `ruff check .` clean;
  `docker compose config -q` valid (compose comments were touched). No
  functional code changed — every `.py` edit is docstring/comment-only, and
  dot's `description` field is consumed only by the textloop banner.
- **Escalations:** none.

### Phase 1 exit-criteria checklist

From `roadmap.md` Phase 1, read under the recorded direct-to-main decision
(roadmap header): "merge-blocking" = push-gated, PR-phrased proofs run on
throwaway branches.

- [x] CI runs on every push and gates all work, and a deliberate throwaway
  branch introducing a `<Dial>` verb fails it. Evidence: green main run
  [32897840253](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32897840253);
  seeded `dial()` red run
  [32898787431](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32898787431).
- [ ] An injected 404 yields a `NOTICE_TEXT` first verb plus a fired alert; a
  healthy re-probe restores the clip.
- [ ] A throwaway `002` migration applies to an existing volume; a deliberate
  mid-list enum insertion fails the revised sync test.
- [x] PII grep over `.env.example` returns nothing. Evidence: T1.2 —
  `rg -in "jason|zenblen" .env.example` exits empty; placeholders carry the
  secret-store pointer and the git-history note.
- [ ] `docker compose up --build` succeeds from a clean checkout.
- [x] An unparseable numeric env var produces an asserted warning while still
  falling back to the default. Evidence: T1.2 —
  `tests/test_config.py::TestEnvNumber` asserts the warning via caplog (typo,
  float-for-int, and secret-suppression cases) with the fallback value intact.
- [ ] The geocode script exists and has run against the fiction pack with
  results recorded.
- [ ] A checked list confirms no doc claims unbuilt behavior in the present
  tense.
