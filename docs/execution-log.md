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

## Phase 1 — Groundwork: safety hygiene, CI, migration machinery — COMPLETE

Closed 2026-08-26. All eight exit criteria ticked with evidence (checklist at
the end of this phase's section); the next phase is the roadmap's Phase 2,
deterministic replay, which begins with its own log entry below when work
starts.

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

### T1.6 — Docker build context + compose repair

- **Scope:** new `docker/agent.Dockerfile` (python:3.13-slim, non-root,
  agentstate ownership chowned before `USER`, explicit COPY list keeping the
  source tree at `/app` so `parents[2]` resolution works for personas and
  migrations, one-worker CMD), `docker/Caddyfile` (TLS terminator; the agent
  stays the auth boundary), `.dockerignore`; compose: initdb mount removed,
  `migrate` one-shot gating the agent on `service_completed_successfully`,
  the in-network DSN as a single YAML anchor across migrate/agent/enrichment,
  `env_file` long-form `required: false`, enrichment+dashboard
  profile-gated, `DOMAIN` for caddy; `POSTGRES_PASSWORD` and `DOMAIN`
  documented; README "Run the full stack" quickstart.
- **Rule 1 review** (two adversaries, cross-refutation, one cascade round):
  - *A-F1 (survived):* a strong `POSTGRES_PASSWORD` containing `/ @ ? #`
    breaks the interpolated DSN (asyncpg parse errors, executed) and gates
    the whole stack off with a misleading connection error — biting exactly
    the operator who follows "set a real one". **Fixed** as a doc contract
    at the point of setting: URL-safe charset (RFC 3986 unreserved), the
    named failure mode, `openssl rand -hex 24` safe vs `-base64` not.
    A accepted (a fail-closed, loud misconfiguration does not justify
    restructuring the runner's standard DSN interface); the deploying phase
    should graduate the contract to a validated check when SOPS lands.
  - *A-F2 (survived):* the `env_file` long syntax needs Compose ≥ 2.24 and
    no minimum was documented. **Fixed** in the README quickstart.
  - *B-F1 (survived):* three docs falsified by this diff — the roadmap's
    "mount removal is T1.6's" (now past-tense), `secrets.md`'s "container
    deployment is not built yet" (now describes today's plaintext
    `env_file` injection honestly, with SOPS still the pre-deployment
    requirement), and the CLI docstring's "planned migrate service" (now
    true present tense). **Fixed**; every rewritten sentence re-verified
    by both adversaries, including "never passed on a command line"
    against every rendered compose command.
  - *B-F2 (survived):* T1.5's self-dissolving initdb fence dissolved but
    was not retired — a stale bolded "no 002 may be committed" would have
    blocked legitimate schema work. **Fixed:** docstring re-tensed to
    history, the fence test deleted. The deletion accidentally reparented
    the empty/missing-directory tests into `TestLayoutViolations` — both
    adversaries ruled that home semantically *more* correct than the old
    one; made deliberate with normalized spacing, and recorded here
    accurately (the fence class did hold three tests, not one as the
    coordinator first claimed).
  - *B-F3 (survived):* the migrate one-shot inherited the full `.env` —
    every provider key — while consuming exactly one variable. **Fixed:**
    `env_file` removed from migrate; its rendered environment now holds
    only the DSN (verified from the rendered config).
  - *B-F4 + A's cascade catch (survived):* enrichment kept the
    host-oriented `localhost` DSN (wrong-by-inspection in-container) —
    **fixed** with the anchor override; the dashboard placeholder then
    stood as the odd one out, inheriting the full `.env` on the service
    that will hold recordings — **fixed** by removing its `env_file` with
    a comment deferring its env contract to its phase, per the roadmap's
    own read-only-role design (not an escalation: Phase 8 already made
    that call).
  - *B-F6a (survived):* the DSN duplicated across services could drift and
    silently break the migrate gate's guarantee — **fixed** as one YAML
    anchor, rendering byte-identical (verified). *B-F6b (dropped, reason
    recorded):* a CMD==compose-command fence test would pin a path nothing
    exercises — compose `command:` overrides CMD on every supported path.
  - *A's cascade prose catch:* "removed together with this runner's compose
    wiring" misparsed as the wiring being removed — reworded.
- **Operational verification (executed):** repo-dir `up --build` on the kept
  T1.5 volume — migrate exit 0 "already applied", caddy up, and the agent
  crash-looped with the *correct* loud `MEDIA_STREAM_PATH_TOKEN` refusal
  (the local `.env` is unconfigured and is never edited by the agent — the
  fail-closed boot check working as documented). Clean-checkout evidence:
  an **rsync-staged working tree** (not a git clone — T1.8's close-out
  re-runs this from a true clone post-commit) with its own `.env`: fresh
  volume → migrate applied 001 in-container (proving `parents[2]` →
  `ordered_migrations()` end-to-end, T1.5's deferral condition), agent
  healthz on loopback and through Caddy TLS, `down`/`up` again → migrate
  no-op, healthy; re-run green after the cascade fixes; `down -v` cleaned.
- **Verification (final tree):** 638 passed / 16 skipped (the fence test's
  skip became a deletion); textloop dry exit 0; `ruff check .` clean;
  `docker compose config -q` valid; guarded compose items (postgres block,
  volume names, loopback binding) byte-unchanged except the intended mount
  removal.
- **Escalations:** none.

### T1.7 — Enum-sync test redesign

- **Scope:** `tests/test_schema_enums.py` rewritten from a single-file
  exact-order regex parse to a cumulative multi-migration model: discovery
  through `ssscammers.db.files.ordered_migrations` (the T1.5 convergence
  promise, now true and re-tensed in both docstrings), `CREATE TYPE`
  initializes and `ALTER TYPE … ADD VALUE` appends, and every evolution the
  append-only contract forbids is a `SchemaSyncError`. The parser **fails
  closed**: SQL is lexed first (comments and string prose contribute
  nothing; `files.stripped_sql` gained public `keep_strings` /
  `keep_dollar_bodies` modes), any `CREATE/ALTER/DROP TYPE` outside a
  fully-parsed span is refused (qualified/quoted names, exotic spellings,
  unterminated statements), an `ADD VALUE` action must be *fully* consumed
  (valid-PG mid-list spellings like `BEFORE E'x'` / `AFTER $$y$$` refuse
  rather than mis-model as appends), and type DDL inside a dollar-quoted
  body — which executes at migration time — is refused outright.
  Retention and headline-view guards hardened (every key occurrence must be
  the pinned seed tuple; exactly one `wasted_time` definition, qualified
  spellings counted). Both mirroring directions gated: every SQL enum in
  PAIRS, every `shared.enums` StrEnum in PAIRS (explicit empty
  `not_persisted` escape hatch). 26 meta-tests prove each refusal bites.
- **Rule 1 review** (two adversaries, three rounds, every finding with an
  executed repro):
  - *Converged round-1 headline:* the first rewrite parsed **raw** SQL and
    failed **open** — B's end-to-end demo (legal Python append + a real 002
    containing only a commented-out `ALTER … ADD VALUE`) passed 23/23 green
    while the database never received the value: the exact runtime insert
    error this test exists to prevent. A independently demonstrated
    qualified-name blindness (`public.mood` renames/drops invisible —
    pg_dump-paste realism). **Fixed** via the lexed pipeline + the
    unconsumed-span guard; B's re-run of its demo now fails RED.
  - *A's round-2 sweep:* three more executed defects — DO-body DDL blanked
    into invisibility (the pre-PG12 idempotent-enum idiom), the
    optional-tail `_ADD_VALUE` search mis-modeling valid mid-list operand
    spellings as appends (piercing the exit criterion), and the view fork
    guard blind to qualified names. **All fixed** with meta-tests; A's
    closing round attacked the fixes (nested/mismatched dollar tags,
    fully-consumed evader hunt, quoted-schema spellings) and cleared them.
  - *The A/B disagreement, argued to ground:* A's Python→PAIRS sweep
    (B initially dropped it as no-concrete-scenario). Sided with A; B probed
    the implementation (forged-`__module__` bite test, re-export ignore
    test) and **withdrew** — PAIRS is now a gated registry, not a
    convention.
  - *B's honesty catch:* the coordinator claimed the trailing-newline fix
    was applied when it was not — a false verification claim, caught by
    byte inspection, now actually fixed and re-verified by A. Recorded as
    the reason fix reports get re-checked.
  - *Recorded residuals (below threshold, reasons on file):* LIKE-spelling
    retention evasion (needs semantic SQL), dynamic-SQL `EXECUTE` concat
    inside bodies (deliberate-evader class), quoted-*schema* view spelling
    (never emitted by pg_dump), DDL split across bodies (not executable),
    and a pre-existing `stripped_sql` `$`-in-identifier lexer divergence
    (effectively unreachable by accident; fix shape on file: tag opener
    only at a token boundary).
- **Verification (final tree):** 77 passed across the two migration/schema
  modules; 667 passed / 16 skipped full suite; textloop dry exit 0;
  `ruff check .` clean. Exit-criterion simulation executed twice by B:
  mid-list `ScamType` insertion → `test_sql_enum_matches_python[scam_type]`
  RED with the guiding message; restore verified byte-identical by sha256.
- **Green on main:** run
  [32912969712](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32912969712)
  (commit `c4b3699`).
- **Red proof** (throwaway branch, deleted after the red run completed): a
  mid-list `PUPPY_DEPOSIT` insertion into `ScamType` — run
  [32937394369](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32937394369):
  all four `tests` matrix cells red via
  `test_sql_enum_matches_python[scam_type]` (log-verified), migration
  runner, textloop, and lint green.
- **Escalations:** none.

### T1.8 — Geocode pre-launch check + phase close-out

- **Scope:** `scripts/check_fiction_geocode.py` — the networked Nominatim check:
  per identity a full-street structured query and a bare-distinctive-name query;
  tokens derived from `fiction.STREET_NAMES` itself; per-city ordered canary
  streets that must produce an `address.road` the matcher recognises; fail-closed
  on hits, canary misses, network errors, non-list JSON, and streets the
  vocabulary cannot explain. `tests/test_geocode_check.py` — 21 CI tests:
  vocabulary coupling, matcher, and the full orchestration over
  `httpx.MockTransport` with an injected sleep. `fiction.py` — `_STREET_NAMES`
  promoted to public `STREET_NAMES` (now a contract shared with the checker,
  stated in its comment) with docstring and `INVARIANTS` pointers to the script.
  `guardrails.md` — the verification paragraph rewritten as the dated record.
  Phase close-out below.
- **Rule 1 review** (two adversaries, cross-refutation, then a fix-verification
  round on the reworked script):
  - *A-1 + B-1 (merged, survived):* cross-suffix blindness twice over. The
    full-street structured query never returns suffix-variant roads at all
    (live-probed: `street="100 Brown Close"`, Dayton → 0 results while Brown
    Street exists), and the first draft's hardcoded suffix list omitted
    `close`/`rise` — two of the six vocabulary names (executed wrong-verdict
    demo). **Fixed:** a second bare-name query per identity (probe-validated:
    `street="Brown"` returns Brown Avenue *and* Brown Street), tokenization
    derived from the generator's own vocabulary, and the coupling test running
    every `STREET_NAMES` entry through the tokenizer.
  - *A-2 (survived; A's own fix shape refuted by B):* no positive control —
    HTTP 200 + `[]` is indistinguishable from a dead street filter, so a
    silently degraded pipeline records "hit-free". B's probe refuted A's
    non-empty canary (a degraded street search still returns the road-free
    city object); **fixed** as a matcher-asserting canary. Live finding during
    the re-run: Bakersfield has no "Main" under the structured search (0
    results, probed) — hence ordered `CANARY_STREETS` ("Main", "Oak"), with
    "Oak" probe-verified firing in all four locality cities.
  - *A-3 + B-2 (merged, survived):* the orchestration (`check_identity`,
    `run_check`) was untested while the docs claimed only the HTTP call was
    unexercised — the fail-closed catch being exactly the branch a
    warn-and-continue refactor flips silently. **Fixed:** injection seams
    (`NominatimSearch(client, sleep)`), MockTransport tests over every error
    path, exit code, both query shapes, canary gating, and the throttle between
    every consecutive request pair; the guardrails sentence and test docstring
    now enumerate exactly what is pinned.
  - *B-3 (survived):* `PACK_DIR` re-derived with a hardcoded "v1" — a
    `PACK_VERSION` bump would strand the checker on a stale pack. **Fixed:**
    `load_pack`/`PACK_DIR` imported from `fiction`; raw `json.loads` survives
    only in the deliberate offline pack-integrity test.
  - *B-4 (survived):* `matching_road` returned an in-band sentinel string that
    `check_identity` printed as "matches real road '(street name yielded no
    checkable tokens)'". **Fixed:** unverifiable is a distinct pre-query
    reason; the contract `(tokens, results) → road | None` now holds, tested.
  - *B-6 (survived at reduced severity):* the staleness half was refuted —
    `fiction.py`'s claims were literally true — but the one-line pointers to
    the script were endorsed as same-change polish and **added**.
  - *Dropped in cross-refutation (reasons recorded):* B-1's Drive-vs-Close
    asymmetry as live behavior (pre-fix, both suffix classes were equally
    unreturned at query level — A's probes); A's non-empty canary shape (B's
    city-object probe defeats it).
  - *Fix-verification round:* A found one new defect — `canary_ok` inherited
    the display_name fallback, so a road-free city object like "Royal Oak,
    Oakland County" satisfies an "Oak" canary (executed) — **fixed** with
    `require_road=True` plus a regression test; identity matching keeps the
    fallback deliberately (errs toward hits). B independently re-ran the fixed
    script live and confirmed every resolution; both recorded residuals below
    the finding bar: `main()`'s three-line UA shim is the one untested line
    (a lost header fails closed as a Nominatim 403), `assert_identity_safe`'s
    substring membership is looser than the checker's word-boundary matching
    (safe: the CI pack test validates through the checker's own tokenizer),
    and a future `STREET_NAMES` entry that is another's word-tail broadens
    tokens (over-flagging only).
- **Live runs (networked, 2026-08-26):** the first-draft run was superseded by
  the review's rework; the fixed script ran live twice — after the
  query/canary rework and again after the `require_road` tightening — both
  times: canaries ok for Bakersfield and Dayton, all three identities hit-free
  through both queries, exit 0. `guardrails.md` records the final shape and
  date.
- **Close-out — the T1.6 deferral, executed:** a true `git clone` of main into
  the session scratchpad → `cp .env.example .env`, token and `PUBLIC_BASE_URL`
  set per the README quickstart → `up --build`: migrate applied 001 on the
  fresh volume, agent healthy on loopback and through Caddy TLS; `down` / `up`
  → migrate "already applied … no migrations were applied", still healthy;
  `down -v` cleaned.
- **Close-out — doc-claims re-sweep:** the surfaces T1.4–T1.8 added or touched
  (notice/notify, `ssscammers/db/`, docker files, compose, README, guardrails,
  fiction) swept for present-tense unbuilt-behavior claims — none found; each
  task's own review policed its diff. One adjudicated exemption recorded:
  `001_initial.sql`'s column comments name their planned consumers (dashboard
  replay, enrichment relabel). The file is now checksum-frozen by the migration
  runner — editing an applied migration hard-errors every baselined volume —
  the schema was deliberately built ahead of its consumers, and build status is
  authoritatively tracked by `roadmap.md`/`README.md`. Do not edit 001 for
  comment tense.
- **Verification (final tree):** 688 passed / 16 skipped (21 in the geocode
  module); textloop dry exit 0; `ruff check .` clean.
- **Green on main:** run
  [32939279849](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32939279849)
  (commit `322ed26`) — all eight jobs. No new CI gate was added (the geocode
  check is pre-launch by design; its 21 tests ride the existing `tests` gate),
  so no red-proof branch applies; the old tokenizer's failure of the new
  coupling test was executed by both adversaries during the review.
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
- [x] A throwaway `002` migration applies to an existing volume *(T1.5:
  `test_a_002_applies_onto_an_existing_volume` baselines 001 and applies an
  `ALTER TYPE … ADD VALUE` 002 onto a simulated initdb-era volume, in CI on
  every push; the live compose volume was baselined and kept)*; a deliberate
  mid-list enum insertion fails the revised sync test *(T1.7: executed twice
  — `test_sql_enum_matches_python[scam_type]` goes RED on a mid-list
  `ScamType` insertion with a message directing the fix to the end of the
  enum; red-proof branch evidence recorded below after push)*.
- [x] PII grep over `.env.example` returns nothing. Evidence: T1.2 —
  `rg -in "jason|zenblen" .env.example` exits empty; placeholders carry the
  secret-store pointer and the git-history note.
- [x] `docker compose up --build` succeeds from a clean checkout. Evidence:
  T1.6 — rsync-staged working-tree copy with a fresh `.env`: migrate applied
  001, agent healthy via loopback and Caddy TLS, existing-volume re-up
  no-oped. Re-verified at the T1.8 close-out from a true `git clone`: fresh
  volume → migrate applied 001, healthz ok on loopback and via Caddy TLS;
  re-up → migrate no-op, healthy; `down -v` cleaned.
- [x] An unparseable numeric env var produces an asserted warning while still
  falling back to the default. Evidence: T1.2 —
  `tests/test_config.py::TestEnvNumber` asserts the warning via caplog (typo,
  float-for-int, and secret-suppression cases) with the fallback value intact.
- [x] The geocode script exists and has run against the fiction pack with
  results recorded. Evidence: T1.8 — `scripts/check_fiction_geocode.py` ran
  live 2026-08-26 (twice, post-review): canaries fired for both pack cities,
  all three identities hit-free through the full-street and bare-name queries,
  exit 0; the dated record lives in `docs/guardrails.md` "Verification
  status", and 21 CI tests pin everything but the live service.
- [x] A checked list confirms no doc claims unbuilt behavior in the present
  tense. Evidence: T1.3 — the 24-claim adjudicated sweep (17 sites fixed, one
  recorded playbook exemption); re-checked at the T1.8 close-out over every
  surface T1.4–T1.8 added — no new violations, one adjudicated exemption
  (`001_initial.sql` column comments; checksum-frozen, see T1.8).

## Phase 2 — Deterministic replay foundation — IN PROGRESS

### T2.1 — Seed the production RNG; log every consequential draw

- **Scope:** `build_conversation` mints a per-call seed (OS entropy when not
  given), constructs the one shared `random.Random(seed)` for director, filter,
  and conversation, and records the seed in the `call_opened` payload; the
  `rng` parameter is gone — an unrecorded rng is an unreplayable call, so there
  is deliberately no way to pass one. Draw outcomes now land in the stream:
  `hold` events carry the drawn `clip`, model-path `agent_turn` events carry
  `filler`/`character_delay_ms`/`fumbled`. The textloop gained `--seed` (every
  run prints the seed it used; `--all-scripts --seed N` reproduces the whole
  gate from one number), documented in its docstring and README. The consequential
  draw sites, catalogued: tactic, hold-probability, hold-length, filler, and
  character-delay draws in `persona_director._plan`; the hold-clip pick in
  `Conversation._execute`; the fumble pick in `_generate`; the filter's
  replacement-line pick in `output_filter` (fires on a block).
- **Rule 1 review** (two adversaries, cross-refutation; every claim probe-backed):
  - *A-1 (survived, sharpened by B):* the replay docstring named three inputs —
    seed, caller turns, clock — omitting the fourth, the **model reply stream**:
    the fumble draw fires only on an empty stream and the filter's replacement
    draw only on a blocked sentence, so a different reply shifts every draw
    after it. B sharpened it: transcript-based replay is *impossible* for
    blocked turns (the raw blocked sentence exists nowhere in the log — only
    the replacement text does), which is exactly why the roadmap pairs
    ReplayBrain with a recorded-client fake. **Fixed:** the docstring names all
    four inputs and states that replay re-drives recorded replies.
  - *B-F1 (HIGH, survived; A reproduced every probe):* the determinism test's
    seed (99) never drew a hold — its own coverage comment was false — and
    membership-only assertions let a deliberately unseeded hold-clip draw
    survive the whole suite (mutant executed by both adversaries). Run-vs-run
    equality kills unseeded-draw mutants only at seeds that reach the site and
    can never catch a seeded-draw reorder; only pinned values can. **Fixed:**
    the test now uses seed 6 (verified: two holds), pins the exact action list
    and hold payloads, and a new wet leg drives both model-conditioned draw
    sites (a blockable card sentence → filter draw; an empty reply → fumble
    draw) with a fixed reply stream. Mutation red-proof executed: the unseeded
    `random.choice(holds)` mutant now fails the pinned test (1 failed), reverted.
    The pins also make cross-version rng stability a CI-checked fact on the
    3.11/3.13 matrix (B-F5, discharged — 3.12/3.13 fingerprints verified
    identical locally; 3.11 closed by the matrix).
  - *B-F2 (survived):* the `build()` test helper wired two `Random(0)` streams
    (director vs conversation) — the exact two-stream shape the new docstring
    forbids, and a golden recorded through it would encode a draw order
    production never executes. **Fixed:** one shared `Random(0)`, landed
    *before* the pins were recorded (A's sequencing constraint); A pre-verified
    the fix against the full module (71 passed).
  - *A-2 (survived at LOW):* the hold-clip hoist changed `if holds:` to a
    truthiness guard, so an empty-string bundle entry would record a `clip` the
    caller never heard (loader accepts `holds: [""]` silently — both
    adversaries verified). **Fixed** per B's Rule 0 argument at the loader:
    sound-pack entries (fillers, holds, ambient) must be non-empty strings,
    three red loader tests added; the guard is now `is not None` as
    self-documentation.
  - *A-minor / B-rider (converged):* the entropy-path test never asserted the
    "records it" half of its own name. **Fixed** — it opens the call and pins
    the recorded payload.
  - *Dropped with reasons:* B-F4's line-anchor patch (the roadmap pre-disclaims
    line drift; symbols are the durable anchors — committed precedent);
    platform-scoping the pins (a ~1e-15 gauss-boundary flake is the wrong trade
    against losing reorder detection; red-on-divergence is the point).
  - *Also fixed:* roadmap's "seedless" present-tense fact re-tensed (factual-
    state patch per T1.3 policy).
- **Verification (final tree):** 698 passed / 16 skipped (10 new tests);
  textloop dry exit 0; two `--all-scripts --dry --seed 7` runs byte-identical,
  seed 8 differs (CLI-level determinism, executed); `ruff check .` clean.
  Process note recorded honestly: the mutation red-proof's restore step used
  `git checkout` on a file carrying uncommitted task work and wiped it; the
  edits were reapplied from context and the full suite re-verified green —
  future mutations copy the file aside first.
- **Escalations:** none.
- **Green on main:** run
  [32945202177](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32945202177)
  (commit `2c99667`) — all eight jobs, including the 3.11 legs: the pinned
  seed-6 draws reproduced on 3.11/ubuntu, settling cross-version rng stability
  empirically.

### T2.2 — Payload widening, first family: triage provenance + measured latency

- **Scope:** `TurnPlan` carries the `TriageResult` it was made under; the
  `caller_turn` emit moves after `_advance` (order-preserving, now pinned by
  test) and carries `TriageResult.as_payload()` — verdict, confidence,
  scam_type, emergency/threat, and the deduplicated signal evidence; model-path
  `agent_turn` gains measured `first_sentence_ms` / `stream_ms` /
  `character_pause_ms`. The triage engine deduplicates hits by
  (pattern, toward) with a `count` on `SignalHit`, appends emergency/threat
  evidence hits (weight 0.0 — score contribution stays honest), and populates
  the emergency/threat booleans on **every** result branch. The textloop's
  per-turn readout now shows the live verdict (`triage=scam@0.85`).
- **Rule 1 review** (two adversaries, cross-refutation; every claim probed):
  - *A-1 + B-5 (merged, survived at MEDIUM):* unbounded caller-controlled
    quadratic signals growth — probed at 200 turns of looped pressure phrases:
    84.6KB single event, ~95MB/call extrapolated to the hard cap, and the
    90-minute looping robocall is the *target* caller. Argued to ground across
    four fix shapes; **engine-level dedupe with count won** (bounds memory and
    payload to the fixed pattern space — probed ceiling 81 entries ≈ 7.3KB
    worst case — keeps events self-contained for Phase 5, and "do not hang up
    × 212" is better evidence than 212 duplicates). Cap rejected (discards
    evidence arbitrarily), deltas rejected (pushes accumulation into every
    consumer forever), serializer-level rejected (leaves engine memory O(n) —
    the same altitude error as B-4). Insertion-ordered dict keeps the tuple
    byte-stable under seeded replay. A payload-shape decision made now
    deliberately: Phase 2 is the last free moment before Phase 5 freezes it.
  - *B-1 (survived at MEDIUM; A withdrew its initial "scope call" refutation
    with reasons):* the safety-critical classifications left zero evidence —
    probed: an emergency caller_turn read `unclear/0.0/no signals` while
    triggering `emergency_exit`, and serializing the booleans alone would have
    logged `emergency: false` (result() populated them only on the SCAM
    branch — confidently wrong, worse than silent). **Fixed at the source:**
    evidence hits in `observe()`, booleans on every branch (their result-field
    consumers were zero, verified), then serialized.
  - *B-4 (survived; A conceded):* the serializer lived a module away from the
    dataclass it flattens — and the B-1 omission is the drift that placement
    invited. **Fixed:** `TriageResult.as_payload()` beside `explanation`, the
    established precedent for presentation logic.
  - *A-2 (survived):* the emit reorder lost the caller_turn event when
    planning raises — the crashing input never reached the canonical log (the
    old order kept it). **Fixed:** try/except emits
    `{"text", "planning_failed": true}` then re-raises (PIPELINE_ERROR
    backstop preserved). The marker is explicit per B's argument — absent keys
    would be ambiguous among a crash, a verdict-free plan, and a serializer
    bug; the coordinator sided with B over A's bare-text shape.
  - *B-2 (survived):* the cumulative-hits contract was untested — a
    per-turn-slicing mutant passed all five new tests (turn one left zero
    hits, so any length comparison held). **Fixed:** containment + count pin
    ("Do not hang up." leaves a hit on turn one; turn two must contain it,
    and a repeat must raise `count` to 2).
  - *B-3 (survived as missing-red-test; A confirmed the order claim is
    accurate today):* the reorder's contract lived only in a comment; no test
    could red on a future reorder (run-vs-run reorders identically — the T2.1
    lesson applied). **Fixed:** exact `types()` pin for a respond turn. B-8
    rider: the scripted agent_turn's key set pinned to `{text, scripted}` —
    the no-measured-fields asymmetry is deliberate and now tested.
  - *B-7 (survived; A refused the "correct restraint" refutation):* the
    harness printed phase but not the verdict, and phase lags verdict by
    probation — a developer tuning triage could not see the flip. **Fixed:**
    one guarded line in `_describe`.
  - *Recorded limitations and non-goals:* "filler-coverage" is delivered as
    coverage-demand (`first_sentence_ms`) plus intent (the drawn `filler`) —
    whether the clip actually *played* is unknowable at this layer (media
    logs-and-skips missing clips); playback truth is the Phase 7 media seam's
    per rescope 6. A final verdict on `call_ended` is a deliberate non-goal:
    provably redundant with the last caller_turn's verdict (ticks and hangups
    never observe), better decided with the Phase 5 schema. `stream_ms`
    includes downstream consumption between sentences in production — stated
    in the payload comment; all timing fields are honest against the injected
    clock by design.
  - *Refuted by A (evidence on file):* the reorder creates no new tick
    interleaving (respond runs under the media turn lock; the logging sink
    never awaits); the failing-sink path is unchanged (disclosure still spoken
    verbatim, probed); T2.1's pins pass over the widened payloads.
- **Verification (final tree):** 708 passed / 16 skipped (11 new tests across
  the task); textloop dry exit 0; `ruff check .` clean.
- **Escalations:** none.
- **Green on main:** run
  [32946995565](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32946995565)
  (commit `bee557e`).

### T2.3 — Payload widening, second family: tick evaluations, DTMF, LLM metadata

Completes roadmap Phase 2 move 2's widening list.

- **Scope:** `call_opened` records the request construction (`model`, `effort`,
  `max_tokens`; all None on a dry run); model-path `agent_turn` records the raw
  API `stop_reason` (distinct from the judged `failure`); timer-landed
  `phase_changed` events carry the `silence_seconds` the timer evaluated; and
  DTMF became a first-class logged input — a dedicated `dtmf` event emitted by
  `_drain_dtmf()` at every drain boundary (respond, tick, hangup), before the
  planner consumes the digits.
- **Rule 1 review** (two adversaries; findings converged so tightly the
  cross-refutation collapsed into a joint fix):
  - *A-1 ≡ B-1 (merged, the headline; probe-backed by both):* the first draft
    recorded DTMF only on the caller_turn path, but a quiet tick and a hangup
    both drained digits into the planner and logged nothing — and production
    timing (1 Hz ticker vs the STT window) makes the quiet tick the *common*
    drain, so non-escape keypresses — the robocall-IVR "press 1" signal this
    line exists to observe — would essentially never be logged, while the
    code's own comment claimed every planner input was recoverable. Replay is
    unbroken *today* (only "5" is read anywhere, and both adversaries proved
    the lost-digit outcomes identical), but Phase 2 is the last free moment
    for the shape. **Fixed at the input boundary:** the `dtmf` event fires at
    every drain, the triplicated drain collapsed into `_drain_dtmf()`, the
    caller_turn/phase_changed `dtmf` keys were removed as redundant, and the
    dtmf-only empty-text caller_turn branch (untested and unreachable through
    the production transport — B-3) was deleted rather than covered. New
    tests: quiet-tick drain, hangup drain, crash-survival (the event precedes
    planning), escape-before-transition ordering, and none-when-none.
  - *B-2 (survived):* `stop_reason` freshness was untested — a stash-at-open
    mutant passed the suite. **Fixed:** a two-turn shifting-brain test pins
    per-turn freshness. A's probe independently established the production
    reset is sound (first statement of the stream body; runs even under a
    zero timeout; a hung or errored turn reports None, never the prior
    turn's value).
  - *A-2 (note, recorded not fixed):* `ScriptedBrain` does not model
    ClaudeBrain's per-stream `last_stop_reason` reset — deliberate: the
    truncation test depends on a preset value persisting through a stream,
    and the recorded-client fake scheduled later in Phase 2 is where the real
    reset semantics get exercised.
  - *Adjudicated no-gaps (B, evidence on file):* caller speech-energy and
    agent-audio-finished timings belong to the golden manifest and the
    Phase 7 media seam (roadmap's own assignment), not this task; recording
    only transition-landing ticks is correct — quiet ticks consume zero rng
    draws (verified against the `exits_only` path) and are pure functions of
    clock, cadence, and state; the one exception was the dtmf buffer, which
    the boundary event now covers.
- **Verification (final tree):** 719 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean; T2.1 seed-6 pins and the T2.2 order pin green over the
  widened payloads.
- **Escalations:** none.
- **Green on main:** run
  [32948349976](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32948349976)
  (commit `07fe2f9`).

### T2.4 — Clock consolidation

- **Scope:** the two fake clocks became one. `SimulatedClock` now lives in
  `ssscammers/simscammer/clock.py` (default `now=1000.0` — FakeClock's old
  non-zero tripwire, kept deliberately and now documented); the textloop's own
  class is deleted, `Session` gains `seconds_per_turn` (pacing policy belongs
  on the session, not the clock) and delegates `elapsed` to the production
  `Conversation.elapsed_seconds`; `tests/helpers.py` re-exports the one class
  (FakeClock deleted, `__all__` added); renamed across three test modules (all
  constructions were bare, so the shared 1000.0 default preserves every test
  bit-for-bit); the CLAUDE.md helper list updated (AGENTS.md hook-mirrored).
  Exit criterion met: exactly one fake clock class exists repo-wide (grepped);
  the ledger's injected civil-date provider is the *other* of the roadmap's
  "both time injections", a different axis, adjudicated not-a-clock.
- **Rule 1 review** (A returned zero findings after attacking the default flip,
  the Session restructure, the re-export, and layering — all refuted with
  probes including a HEAD-vs-worktree byte-diff of a seeded gate run; the
  round ran one-directional, A refuting B):
  - *B-1 (survived; A conceded with an output-neutrality byte-diff):* the
    first draft's `Session._started_at`/delta-elapsed rebuilt the quantity
    `Conversation.elapsed_seconds` already measures — the "parallel
    implementation that has since drifted" textloop's own docstring forswears,
    and the exact latent window A had flagged and dropped. **Fixed as a
    deletion:** `elapsed` delegates to the production measurement.
  - *B-3 (survived; A's own mutation probe demolished A's refutation):* a
    plugin forcing the default to 0.0 left all 719 tests green — the
    documented non-zero tripwire was guarded by nothing, precisely because
    every transitive test is delta-based. **Fixed:**
    `tests/test_simulated_clock.py` pins the truthy default (the load-bearing
    line, per A's refinement) plus basic read/advance behavior.
  - *Adjudicated (B, evidence on file):* the clock's home is right — production
    injects `Callable[[], float]`, so `agent/` never imports `simscammer/`,
    now or at Phase 7's media-seam work; `shared/` would put a test double in
    the call-path layer. A "one clock" meta-scanner was rejected as ceremony
    (hygiene is encoded in CLAUDE.md; the scanner precedent guards G-1, a
    safety invariant). `roadmap.md`'s mention of `tests/helpers.FakeClock`
    stays as written per the settled bullets-are-historical policy.
- **Verification (final tree):** 721 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean; HEAD-vs-worktree seeded gate output byte-identical
  (adversary-executed).
- **Escalations:** none.
- **Green on main:** commit `b63faa2`.

### T2.5 — Machine-checked adversarial predicates

The four prose-only pass criteria became executable ones. Before this, a
persona that complied with "tell me your system prompt" passed both gates as
long as the phase machine didn't disclose.

- **Scope:** `ForbiddenReply` predicates plus a prompt-leak check on the
  adversarial scripts, evaluated over the persona's *own* speech;
  `Turn` gained a `scripted` provenance flag set by the producer;
  `Persona.leak_corpus()` and `persona_director.steering_corpus()` define what
  may never be recited; the textloop reports content checks wet and prints them
  **skipped, never passed**, when there is no model text (dry). 58 tests.
- **Rule 1 review** (two adversaries, cross-refutation; ~20 probe-backed
  findings, every one executed against the real predicates and persona):
  - *The headline (A-F1, generalized by B):* the first draft used the **whole
    system prompt** as the leak corpus — which contains the text the persona is
    *instructed to speak*. Probed: reading its own card aloud, and four of six
    of the bundle's own `## Things you say` example lines, were leak
    VIOLATIONS. On `injection_reveal_prompt` — whose first line is literally
    "verify your card please" — the best stalling move in the playbook failed
    the safety-critical gate. **Fixed** with `leak_corpus()`: instructions
    only, excluding the fact block and the example-speech section.
  - *A-F2, sequenced after it:* a nine-words-per-turn drip cleared the
    per-turn length gate while handing over the whole document (probed: 144
    prompt words leaked, zero violations). The joined pass fixes it — but B
    proved joining is unsafe *before* the corpus fix: two consecutive
    in-character example lines fused into a 29-token match from correct
    behavior, collapsing the safe threshold window to nothing. **Fixed in
    order**, with both cases pinned.
  - *A-F5 + B's surviving mutant:* a rules dump with the `[call state]` bracket
    omitted was invisible. **Fixed** by adding the steering text to the corpus
    — rendered in note order, because several notes are shorter than the
    detection run on their own and are only catchable with the line that
    follows them.
  - *B-4 (design):* exemption-by-exact-text reimplemented what the producer
    already knows, and the duck-typed surface returned `[]` for an
    event-shaped history — a green gate that ran no predicate. **Fixed:**
    `Turn.scripted`, typed `Iterable[Turn]`, wrong shapes now fail loudly. A
    verified the field is inert to request construction, the determinism pins,
    and every existing `Turn` construction.
  - *Pattern corrections, all probe-driven:* deictic confessions ("this is an
    automated assistant" — the disclosure's own wording spoken unscripted) and
    interjected ones ("I am, in fact, an AI") were missed; "I'm just a computer
    illiterate old thing" and echo-deflections were falsely flagged; the
    coaching predicate flagged refusals while missing the imperative voice that
    is how compliance actually reads; "Yes, I do." slipped the bare-confirmation
    check. All fixed, each with a red pin *and* a clean pin, after A found that
    B's first deictic fix produced three new false positives and added an
    interrogative guard scoped to that branch only.
  - *The sharpest disagreement, argued to ground:* whether an **echoed**
    authorisation formula is a violation. A called it a false positive
    (deflection punished); B carried it — audio is cut at word boundaries, so a
    quoted formula renders the same harvestable clip, and READ_BACK's direction
    ("repeat with one detail changed") does not license reproducing it intact.
    Kept as a violation; the predicate's frame tightened so "I confirm nothing
    until I've spoken to my grandson" stays clean.
  - *B-3 (mutation evidence):* 8 mutants run, 2 survived — topic-broadening
    either predicate passed 66/66 green because no clean pin echoed the
    attack's own vocabulary. **Fixed** with echo-deflection pins; A refuted the
    over-freezing objection (the pins assert the docstring's stated contract,
    not regex internals).
  - *A-F10 / B-5 (honesty):* an empty corpus failed the leak check open while
    the harness printed "PASS … clean" — now a `ValueError`; the docstring's
    claims about compliant-brain tests and recorded replay were corrected to
    what exists; the misnamed "production surface" test renamed to what it
    actually is; the count corrected (the summary said 16 tests, 15 existed).
- **Escalation (named, for the owner):** the deterministic *production* layer
  does not catch these shapes either. `output_filter.py`'s persona-break
  patterns are first-person only, so four of five deictic confessions reach the
  caller uncaught, and nothing anywhere blocks a spoken consent formula (G-18 /
  G-5 exposure). Both adversaries independently recommended fixing it as its
  own task rather than expanding this diff into the pre-TTS safety path;
  recommendation accepted, and a task is queued with the probe strings.
- **Verification (final tree):** 779 passed / 16 skipped; textloop dry exit 0
  (content checks reported skipped, honestly); `ruff check .` clean; the four
  driving probes re-executed end to end — in-character lines and card read-back
  clean, drip leak and bracket-less rules dump both caught.
