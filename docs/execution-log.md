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

### T1.4 — `NOTICE_AUDIO_URL` runtime probe + ntfy alerting

- **Scope:** new `ssscammers/agent/notice.py` (`NoticeHealth`: boot fetch,
  interval re-probe, transition-only alerting, `current_url()` as the single
  in-memory call-path read) and `ssscammers/agent/notify.py` (`Notifier`
  protocol named `send` — the scanner bans `message` — `NtfyNotifier`,
  `NullNotifier`); Settings gained `NTFY_*` and
  `NOTICE_PROBE_INTERVAL_SECONDS`; `create_app` gained a lifespan (boot fetch
  before serving, supervised probe task, cancelled at shutdown) and an
  injectable `notice_health`; the engage document now reads
  `notice_health.current_url()`; `/healthz` reports `notice_clip`; ~40 new
  tests. Boot-fetch failure policy: degrade + alert, keep serving — refusing
  to boot over a transient fetch failure drops calls, and the spoken text is
  the designed degraded mode.
- **Rule 1 review** (two adversaries, cross-refutation, two cascade rounds):
  - *A-1/B-F1 (converged, survived):* `NOTICE_PROBE_INTERVAL_SECONDS=0`
    was a measured hot loop (~15k probes/s on the call-serving event loop).
    **Fixed:** a 5s floor clamped in `__post_init__` with a warning.
  - *A-2 (survived):* "< 400" called an unfollowable 302 healthy. **Fixed:**
    strict `is_success`.
  - *A-3/B-F2 (converged, survived):* a raising notifier killed the probe
    loop — a dead watchdog indistinguishable from a healthy clip. **Fixed:**
    alerts routed through a guarded `_alert`; proven by an
    exploding-notifier test.
  - *A-4 → B's overturn (the round's best catch):* the first fix
    blacklisted `text/*` but kept missing/octet-stream content types
    healthy, on A's "headerless CDNs serve playable clips" argument. B
    refuted it with Twilio's own documentation — `<Play>` enforces an
    explicit ten-type MIME allowlist and error 13325 fires on invalid *or
    missing* Content-Type; no sniffing — making the carve-out a false-green
    (an R2/S3 bucket with default `application/octet-stream` metadata would
    probe healthy while every caller was recorded with no notice, with
    monitoring asserting all-clear). Verified against the live Twilio docs
    by the coordinator and by A, who formally withdrew its ruling.
    **Fixed:** the probe now applies exactly Twilio's documented allowlist,
    so it can never false-alarm on a clip Twilio would play; tests
    inverted, plus a loop proving all ten documented types (with
    parameters, any case) stay healthy.
  - *A-5 (survived):* httpx timeouts bound socket operations, not requests —
    a trickling host held the boot fetch (which runs before serving) for
    unbounded wall clock, measured 8.1s+ per byte-rate. **Fixed:**
    `asyncio.wait_for(timeout×3)` inside `check_once`; tripping it is
    doubt, and doubt degrades to text.
  - *B-F3 (survived):* four docs falsified by this diff (README notice
    paragraph, `guardrails.md` G-2 preamble + row, roadmap workstream G row
    and Phase 1 goal). **Fixed** under the settled factual-state policy;
    G-2's status upgraded to **built** with the residual one-interval
    window named in bold in the row — B ruled the upgrade honest since the
    window is an inherent property of polling, disclosed where the status
    is read.
  - *B-F4 (survived):* the exit criterion is now one literal composition —
    the lifespan e2e asserts 404 → `<Say>` `NOTICE_TEXT` first verb +
    healthz `degraded` + exactly one fired alert, and no spurious alert at
    shutdown.
  - Dropped with reasons recorded: per-send-httpx-client duplication (below
    useful altitude at three divergent sites), probe-then-sleep ordering
    (boot fetch already probed once), `application/json` degradation
    (subsumed by the allowlist).
- **Live demonstration (final code, real process, real sockets):** boot with
  `NOTICE_PROBE_INTERVAL_SECONDS=0` and a 404ing clip URL → the clamp
  warning fires, healthz reports `degraded`, the transition alert is
  attempted once; creating the clip → the clamped 5s re-probe recovers it
  (real `http.server` `audio/x-wav` passing the allowlist) → healthz
  `healthy`, one recovery alert, no repeats.
- **Verification (final tree):** 603 passed / 2 skipped; textloop
  `--all-scripts --dry` exit 0; `ruff check .` clean.
- **Escalations:** none.

### T1.5 — Migration runner

- **Scope:** new `ssscammers/db/` — stdlib-only `files.py` (discovery,
  rigid `NNN_lower_snake.sql` names, contiguous-from-001 numbering, sha256
  checksums, a single-pass lexical ban on transaction-control statements)
  split from asyncpg-backed `runner.py` (advisory lock, self-created
  `schema_migrations`, three-state baseline for initdb-era volumes,
  per-migration transaction with the tracking row committed atomically,
  checksum-frozen applied migrations) and a `python -m ssscammers.db` CLI;
  `001_initial.sql` stripped of `BEGIN;`/`COMMIT;`; `db` extra (asyncpg);
  new CI `migrations` job (postgres service container, py3.11+3.13,
  skip-proof grep guard); 44 tests (20 file-contract, 24 PG-backed).
- **Rule 1 review** (two adversaries, cross-refutation, two cascade rounds;
  every finding carried an executed repro):
  - *A-F1 (survived, then strengthened twice):* the first txn ban covered
    two exact spellings; A smuggled `ROLLBACK;` through it producing a
    migration *recorded but never applied*, and `COMMIT;`-then-fail leaving
    a half-applied change. The fix broadened to statement-leading keywords
    over stripped SQL; A then blinded the sequential regex passes three
    ways (a `--` inside a string eating a following `COMMIT` being the
    least exotic), and B independently proved two more defects in the same
    regexes ($$-tag false positives, named-tag bracketing). **Final form:**
    a single positional lexer (`_strip_non_sql`) that both adversaries
    re-attacked — 17 cases, no surviving evasion; `PREPARE TRANSACTION`
    banned as a two-token check; unterminated constructs documented as
    failing safe at execute time.
  - *A-F2/B-F1 (converged, survived):* the baseline sentinel (`personas`,
    the FIRST table 001 creates) certified half-built schemas — and this
    diff itself opened the window by de-atomizing the still-mounted initdb
    apply. **Fixed:** three-state check on `wasted_time` (the LAST object);
    half-applied volumes are refused with a hard error in both real and dry
    paths. The foreign-database guard was dropped with recorded reason
    (false-positives on legitimately shared databases; misdirection costs
    clutter, not destruction) — B accepted the drop.
  - *A-F3 (survived → mechanical):* until T1.6 removes the initdb mount, a
    committed 002 wedges fresh-volume bring-up. First fixed as a docstring
    fence; A pushed for mechanics — now a **self-dissolving test**: while
    compose contains the mount, `ordered_migrations()` must have length 1;
    the guard skips itself when the mount line disappears.
  - *A-F4/B-F3 (converged, survived):* `pytest -m migrations` exits 0 when
    everything skips — a vacuous gate. **Fixed:** the job greps its own
    output (requires "N passed", forbids "N skipped"), verified
    mechanically by both adversaries; matrixed over py3.11/3.13 (B-F5).
  - *A-F5..F8 (survived):* CLI tracebacks for routine failures → clean
    messages (PostgresError/InterfaceError/MigrationFileError); non-UTF-8
    files → contract error; enum add-then-use and
    no-CONCURRENTLY/VACUUM caveats documented; false "the enum-sync test
    imports this" claim re-tensed.
  - *B-F2 (survived):* the advisory lock had zero coverage — a no-lock
    mutant passed the suite. **Fixed:** a racing-runners test that kills
    the mutant deterministically (verified 3/3 by A, 6/6 by B; 001's
    non-idempotent CREATE TYPEs make the collision reliable).
  - *B-F4 (survived):* docs falsified by the diff — roadmap's
    initdb-emergency goal fact "(closed at T1.5)", README's CI list and
    layout row, `config.py`'s database_url docstring. **Fixed.**
  - *B's cascade catch:* the new CLI docstring claimed the compose
    `migrate` service exists (T1.6 work) — the T1.3 defect class,
    introduced by T1.5's own fix prose. **Re-tensed.**
  - *B-F6 (deferred with recorded constraint):* the `parents[2]` repo-root
    assumption breaks under a non-editable install that discards the source
    tree; deferral to T1.6 accepted by both adversaries **on the condition**
    that the T1.6 image keeps the source at `/app` and its clean-checkout
    test actually reaches `ordered_migrations()` (the compose migrate
    one-shot does exactly that).
- **Live demonstration:** the compose initdb volume (fresh `pgdata`, 001
  applied raw by the entrypoint) → first runner run baselined 001 without
  re-execution, second run reported up-to-date; tracking row `baselined=t`
  with the post-edit checksum. Volume kept for T1.6's existing-volume test.
- **Verification (final tree):** 653 passed / 2 skipped with
  `MIGRATIONS_TEST_DATABASE_URL` against a real postgres:16 container
  (639 / 16 without — PG tests skip locally); textloop dry exit 0;
  `ruff check .` clean.
- **Green on main:** run
  [32909325807](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32909325807)
  — all eight jobs, including both new migration-runner legs.
- **Red proof** (throwaway branch, deleted after the red run): a seeded
  broken `002_broken.sql` — run
  [32909343434](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32909343434):
  both `migration runner` legs red (the invalid SQL fails the apply tests)
  **and** all four `tests` cells red (the self-dissolving initdb fence
  refuses a second migration while the mount exists), textloop and lint
  green. The vacuous-skip failure mode has no branch seed — the grep guard
  was verified mechanically by both adversaries (env unset → step exit 1),
  recorded here as the per-failure-mode evidence.
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
- [x] An injected 404 yields a `NOTICE_TEXT` first verb plus a fired alert; a
  healthy re-probe restores the clip. Evidence: T1.4 — the lifespan e2e test
  composes all three assertions
  (`test_the_lifespan_probes_at_boot_and_degrades_on_a_404`), and the live
  process demonstration reproduced degrade-then-recover over real sockets.
- [~] A throwaway `002` migration applies to an existing volume *(done at
  T1.5: `test_a_002_applies_onto_an_existing_volume` baselines 001 and
  applies an `ALTER TYPE … ADD VALUE` 002 onto a simulated initdb-era volume,
  in CI on every push; the live compose volume was baselined and kept)*; a
  deliberate mid-list enum insertion fails the revised sync test *(T1.7 — the
  sync-test redesign)*.
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
