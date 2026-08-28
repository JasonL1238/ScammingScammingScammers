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

Local red-proofs run on a clean tree: copy the target file aside, and clear
`__pycache__` **both** after seeding and after restoring. Stale bytecode can
fake either verdict — a same-length edit (`0.45` → `0.95`) leaves a `.pyc` that
outlives the restore. The false red costs a cycle; the false **green** is the
one that matters, because it retires a gate as proven when it never bit. Never
restore with `git checkout` on a file that carries uncommitted task work.

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

## Phase 2 — Deterministic replay foundation — FOUR OF FIVE CRITERIA MET

Eight tasks landed; four of the five exit criteria are met and verified fresh
against main (checklist at the end of this section). The fifth — the LLM
adversary's mean simulated time-on-call — is blocked on an authorized spend, and
stays this phase's work. The two design questions raised at T2.6 and T2.8 were
answered by the owner on 2026-08-26; a third, the adversary's budget, got a
disposition rather than an answer, and two new questions were raised. All of it
is at the end of this section.

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
- **Green on main:** run
  [32949642278](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32949642278)
  (commit `dadafbb`).

### T2.6 — The misroute FPR=0 gate

Roadmap exit criterion: "all misroute scripts × all personas × both entry
paths, release within two turns."

- **Scope:** `TestRealPeopleAreAlwaysReleased` rewritten from four partial
  tests (marjorie-only, plus a forwarded-only and a one-script persona test)
  into a derived cross-product — every misroute script × every shipped persona
  × both entry paths — asserting the **exact** exit each script declares, no
  engagement, and release within two turns. `BAITING_PHASES` promoted to
  `shared/enums.py` so the state machine's `baiting` property and the gate's
  absence-assertion cannot drift apart; `SHIPPED` moved to `tests/helpers.py`
  as the independent anchor (it had three copies). 51 → 120 tests in the
  module.
- **Rule 1 review** (two adversaries; both ran mutation experiments, and
  between them killed most of the first draft's claims):
  - *A-F3 (HIGH, fixed):* the rewrite **lost strength**. Collapsing the
    deleted test's exact `is DISCLOSE_EXIT` into `in (DISCLOSE_EXIT,
    EMERGENCY_EXIT)` let two real regressions pass green: a fire emergency
    degraded to the ordinary disclosure ("your message will be seen" instead
    of "hang up and dial 9 1 1 right now"), and a pharmacist routed to the
    emergency exit — told to call 911, and silently stripped of the voicemail
    they were promised. **Fixed** by asserting `script.expect_phase`, which
    every misroute script already declares. Proven: a seeded
    emergency-signal broadening now fails 34 cases; under the first draft it
    failed none.
  - *A-F1 (HIGH, escalated — see below):* the matrix cannot reach the
    probation hard-commit boundary. Every misroute script is one or two lines,
    so elapsed time never passes 50s while commitment happens at 90s. A
    verified five-turn benign caller — *"Sorry, hello? I can't hear you very
    well"* — is baited into STALL on all three personas and both entry paths,
    and the gate declaring FPR=0 cannot see it.
  - *A-F2 (HIGH, same root cause):* `never_engages` was vacuous for 24 of 30
    cases — A inverted triage to return SCAM/0.98 for **every** caller and only
    6 cases failed, because HOOK is unreachable on a single turn. The corpus,
    not the assertion, is the limit.
  - *A-F4 + B-F1 (fixed):* the coverage meta-test was self-referential —
    B renamed a persona directory and the module silently dropped 60 of 90
    cases while the meta-test passed; A dropped the forwarded axis
    (`(False, False)`) and it also passed. **Fixed:** anchored on `SHIPPED`
    and asserting the entry-path axis explicitly; both mutants now die.
  - *B-F2 (recorded, not fixed):* the persona and entry-path axes have **zero**
    discriminating power today — B neutered the forwarded wiring entirely and
    the full suite stayed green, and all 15 script×persona pairs produce
    byte-identical transition histories. The axes are kept because the exit
    criterion names them and Phase 10 makes entry-path thresholds real, but
    "120 tests" is 5 distinct behaviors replayed 18×, and the log says so
    rather than letting a future reader mistake axis count for corpus breadth.
  - *A-F7 (fixed):* the engagement-phase tuple was duplicated between the gate
    and `CallStateMachine.baiting` — now one `BAITING_PHASES` constant.
  - *B-F8 (fixed, process):* the red-proof procedure now requires clearing
    `__pycache__`. During this task a same-length seeded edit (`0.45` → `0.95`)
    left a `.pyc` that outlived the restore and produced a convincing fake
    regression on a clean tree — twenty minutes chasing a bug that did not
    exist. The dangerous twin is the fake *green*, which retires a gate that
    never bit.
  - *Corrections to my own claims:* the pre-diff module count was 51, not the
    ~55 I reported; assertion 1's kill set is a subset of assertion 3's, so the
    three assertions are not three independent checks (kept anyway — each
    names a distinct property, and Rule 0 governs).
  - *Adjudicated out of scope:* growing the misroute corpus is Phase 10's, by
    the roadmap's own text; emitting a measured FPR *rate* would be a reporting
    harness, and a fail-closed gate should not double as a metrics object.
- **Escalation — open question for the owner.** *May probation expiry commit an
  UNCLEAR caller to baiting at all?* Today it does, at 90s. The rationale is
  sound (ninety seconds without one legitimacy signal is overwhelmingly a
  scammer) but it is the one shape where a real person is baited, and the
  population conditional forwarding delivers — slow, confused, hard-of-hearing
  — is exactly the population that trips it. **Recommendation:** keep the
  current behavior for now and close the gap in Phase 10, where the corpus
  grows and the posterior can revisit a commit mid-call; the behavior is pinned
  by `TestTheProbationBoundaryIsTheKnownGapInTheGate` so changing it is a
  visible decision. Escalated rather than fixed here because it is a product
  call about the FPR/engagement trade, not a defect in this diff.
  *(Answered 2026-08-26 — yes. See the owner reply at the end of this section;
  this entry stands as the record of what T2.6 escalated at the time.)*
- **Verification (final tree):** 848 passed / 16 skipped (51 → 120 in the gate
  module); textloop dry exit 0; `ruff check .` clean. Local red-proofs, run
  under the amended cache-clearing procedure: legit bar raised → 24 failures;
  emergency signals broadened → 34 failures; restored → 120 passed with the
  source tree clean.
- **Green on main:** run
  [32989606150](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/32989606150)
  (commit `9f6f98b`).

### T2.7 — The replay seam

- **Scope:** `ssscammers/simscammer/replay.py` — `CallRecording` /
  `RecordedTurn` (raw deltas, so replay re-runs the splitter rather than
  trusting a recording of its own output), `ReplayBrain` on the `stream_reply`
  seam, and `RecordedAnthropicClient` plugging into a real `ClaudeBrain` so
  that module's streaming path runs offline. `ClaudeBrain._client` became an
  injectable `client`; the sentence loop was extracted as public
  `stream_sentences`; `build_messages` made public because replay honours the
  same no-addressable-turn guard through it. New `tests/test_llm.py` gives
  `llm.py` its own home module.
- **Rule 1 review** (two adversaries; they converged independently on the same
  three headline defects, each with an executed repro):
  - *A-F1 ≡ B-F2 (HIGH):* `last_stop_reason` was assigned after the sentence
    loop instead of where production assigns it — inside the delta stream. A
    consumer that breaks early never reached it, and `_generate` breaks early on
    **every output-filter block**, so a filter-blocked truncated turn replayed
    as `failure=None` where the live call recorded `truncated`. **Fixed** by
    moving the assignment into the inner generator; both adversaries verified
    the depths then agree on break-after-1, break-after-tail, and no-break.
  - *B-F1 ≡ A-F6 (BLOCKER for T2.8):* `ReplayBrain` exposed no
    `model`/`effort`/`max_tokens`, which `call_opened` reads — so **the first
    event of every replay differed**, and the exit criterion ("replays
    byte-identically … payloads") failed before the caller spoke. The recording
    already carried the fields, write-only. **Fixed** with properties, and
    `check_environment` now refuses a replay whose request construction differs.
  - *A-F2 ≡ B-F4 (HIGH):* `exhausted` reported True after a *diverged* replay —
    the overrun check preceded the increment, and `DivergedError` subclasses
    `AssertionError`, which `_generate` swallows into a fumble. A runner
    following the docstring would pass a wholly diverged run. B caught the
    smoking gun: my own test asserted `brain.index == 1` rather than the public
    property, because the public property did not work. **Fixed:** one shared
    `_Cursor` counts divergences, and `complete` is false after any of them.
  - *A-F3 (MED-HIGH):* the *deeper* seam had no divergence checking at all,
    while the module docstring claimed both refused to paper over it. **Fixed:**
    `describe_request` reads the steering back out of the built request, so both
    depths run the identical check — and that function is also the half a
    recorder needs (B-F6).
  - *A-F4 (MED):* the depths disagreed on a history with no addressable caller
    turn — the real brain returns without touching the wire, `ReplayBrain`
    consumed a turn and desynced permanently. **Fixed** by running the
    production `build_messages` guard.
  - *A-F5 (MED):* the constructor failed closed while `from_json` failed open
    (`pack_version` → `""`, `stop_reason` → `None`), disabling the pack guard
    for every hand-written fixture. **Fixed**, defaults now mirror the
    dataclass.
  - *B-F3 + B-F9 (HIGH):* two of the three properties the task exists to cover
    had **zero** kills — deleting the tail flush (the truncation path) and the
    splitter's decimal and em-dash rules broke no test, and the `max_tokens`
    warning was unasserted. **Fixed** in `tests/test_llm.py`. One quirk found
    and pinned rather than changed under a test task: a closing quote after a
    terminator starts the next chunk.
  - *B-F5 (MED-HIGH):* `strict=True` was the default but had never run against
    a real `Conversation`, and `None` conflated "unsteered" with "unrecorded".
    **Fixed:** an `UNRECORDED` sentinel, and a test that records the steering
    off the deep seam and replays with every check armed.
  - *B-F7 (MED):* the hand-enumerated JSON dropped a new field silently (B
    simulated it: 0/873 failures). **Fixed:** the round-trip test is driven off
    `dataclasses.fields`. B's adjudication that the hand-written `to_json` earns
    its place for key order (metadata first, so a golden's diff opens on the
    fields a reviewer checks) is now stated in the code.
  - *B-F10 (LOW-MED):* the two depths carried duplicate cursors that had
    already drifted three ways. **Fixed:** one `_Cursor`.
  - *B-F11 (LOW):* `llm.py` had no home test module. **Fixed.**
  - *B-F8 / A-F7 (recorded):* `check_environment` still cannot see the caps or
    the entry path. Those belong to the manifest carrying the caller's side, and
    T2.8 checks them there — recorded so the boundary is deliberate.
- **Adjudicated for T2.8** (B's highest-value question): the model-recording /
  caller-script split is correct — the caller side is authored and
  human-reviewed, and capturing authored input would create a second source of
  truth for something a person edits. The recording is genuinely *not*
  redundant with the event log (proven on the blocked-sentence case: the
  recording holds the raw model output, the event log the post-filter speech).
  What T2.8 must add, on the manifest side: binding a recording to its script,
  per-turn timing, tick cadence (`Session` never calls `tick()` at all today),
  and the civil date.
- **Verification (final tree):** 908 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean. The round trip is now a test rather than a claim: a call
  driven through the deep seam and the same recording driven through the fast
  one emit **identical event streams**, including the truncation labels.
- **Green on main:** run
  [33004064111](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33004064111)
  (commit `f94c00a`).

### T2.8 — Golden manifests and the byte-identical replay gate

- **Scope:** `ssscammers/simscammer/golden.py` (manifest, runner, serializer,
  and a corpus of six), `session.py` (the *one* call driver, extracted),
  `scripts/regenerate_goldens.py`, `tests/goldens/*.json`, and
  `tests/test_goldens.py`. A golden pins the event stream, the transcript, and
  the steering the model was asked under.
- **Rule 1 review** (two adversaries, 28 mutations between them; **13 survived
  the first draft** — the gate was, in A's words, "partly real"). Every one of
  the following was found by executed mutation, not inspection:
  - *A-F1 (the gate's own red-proof was fake):* the mutation tests
    hand-rolled a second serializer without `ensure_ascii=False`, so an em dash
    in the disclosure produced a diff **whether or not the mutation was
    applied** — A proved it by deleting the mutations and watching the tests
    pass. **Fixed:** one `reserialize` helper, plus a control test asserting an
    unmutated replay produces *no* diff, so a serializer mismatch can never
    again masquerade as detection.
  - *A-F2 (the headline golden pinned nothing it claimed):* the card was split
    mid-*sentence*, and the splitter rejoins deltas before the filter sees
    them — so the whole 16 digits arrived in one sentence and a per-sentence
    check would have caught it identically. A proved it: reopening the G-4
    cumulative-filter hole left all 30 tests green. **Fixed** by splitting at a
    sentence boundary; the mutation now fails the gate.
  - *A-F3 ≡ B-F2 (G-16 was unobservable, and there were two drivers):* the
    runner advanced the whole pause, *then* marked audio finished, *then*
    ticked — so `_line_busy_until` was never the deciding term in 195
    evaluations, and deleting G-16 outright left the corpus green. The root
    cause was structural: the golden runner and `textloop.Session` were two
    drivers of one `Conversation` that already disagreed, and B measured the
    divergence (a `hold` at 111.6s vs 70.5s for the same manifest) — with the
    shipped harness being the one that was *wrong* relative to production,
    which drains lazily and sleeps per action. **Fixed by deletion:** one
    `Session`, used by both, advancing the clock inside the drain and running
    the timer *through* pauses at the production 1 Hz cadence. Removing G-16
    now fails four tests. B's prediction held exactly — the textloop gate stays
    exit 0, with one line changing (`phase=hook` → `phase=stall`), because the
    harness had been misreporting that turn.
  - *A-F4 ≡ B-F4 (steering pinned by nothing):* `state_note` appears in no
    event payload, and every golden left it `UNRECORDED`, so the state notes
    could be rewritten wholesale and the gate stayed green — `strict=True`
    would not have helped. **Fixed** by capturing the steering into the golden
    artifact (`ReplayBrain.seen_state_notes`) rather than into every event
    payload, where it would be bulky and, once persisted, permanent. Rewriting
    a note template now fails four tests.
  - *A-F5 (the transcript, which the exit criterion names, was discarded):*
    silencing the greeting entirely — the persona answering every call with
    nothing — left the corpus green. **Fixed:** the golden carries the
    transcript with provenance; that mutation now fails seven tests.
  - *B-F1 (the pack pin was vacuous):* every recording inherited
    `pack_version` from the live tree, so the guard compared a value to
    itself — B set `PACK_VERSION = "v2"` and all 30 tests passed. **Fixed** by
    pinning the literal on every manifest, with a monkeypatched regression test.
  - *A-F6 ≡ B-F3 (no golden walked the state machine):* three of five
    teleported to `STALL`. **Fixed** with a walk-from-greeting golden; the
    triage commit bar (0.6 → 0.95) now fails the gate.
  - *A-F9 (unpinned paths):* added a golden where the model returns nothing, so
    the fumble draw that covers silence is pinned.
  - *B-F5:* `sort_keys=True` bought no determinism (payloads are already
    insertion-ordered) and pushed `seq`/`type` *after* the payload they
    identify — the opposite of the reviewability rationale T2.7 adjudicated for
    the sibling serializer. **Removed.**
  - *Refuted in writing, with measurement:* no golden pins the probation
    *window*, and none can. No authored opener reaches the 0.6 commit bar alone
    (strongest is 0.50), so commitment always waits for a second turn at t=50s,
    already past the 30s window; probation binds only a caller who convicts
    himself in one breath, and inventing that opener to kill a mutation would
    be a fixture rather than a call. The probation → hard-commit behaviour is
    pinned directly by `tests/test_call_scripts.py`. The reasoning is recorded
    in the manifest itself so the next reader does not re-derive it.
  - *Adjudicated (B's headline question):* manifest-driven replay is correct,
    not a weaker substitute for log-driven replay — T2.7 proved the event log
    is not a sufficient replay source (it holds post-filter speech; the
    recording holds raw deltas), and T2.3 established that quiet ticks emit
    nothing by design. The roadmap's "from a recorded event log" is loose
    phrasing against a design this project converged on with reasons.
  - *Verified by B and worth recording:* the corpus is byte-identical under
    Python 3.11 and 3.13, hash-seed independent; the goldens contain no PII
    (the card is blocked and never spoken, and it is not either persona's
    fiction card); `tests/` is excluded from the Docker image, so goldens
    correctly do not ship.
- **Escalation — open question for the owner.** The exit criterion says *"a
  **recorded** call replays byte-identically"*. These six goldens are
  **authored**, not captured: there is no recorder, so the manifest expresses
  per-turn timing as a rule (`seconds_per_turn`) rather than as data, and a
  real call with uneven gaps — the shape that reaches dead air naturally —
  cannot be expressed. `DailyLedger`'s civil date is likewise not an input,
  refuted in writing because the ledger lives on the admission path and a
  conversation-level replay never touches it. **Recommendation:** amend Phase
  2's wording to "a synthesized call", and schedule the recorder against Phase
  5, where persisted rows make capture necessary and give it a real source.
  Escalated rather than decided here because it changes the roadmap's own exit
  criterion. *(Answered 2026-08-26 — synthesized. See the owner reply at the end
  of this section, which also records that "give it a real source" was only half
  right: the rows carry the timing but not the raw deltas. This entry stands as
  the record of what T2.8 escalated at the time.)*
- **Verification (final tree):** 950 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean. Mutation re-run under the cache-clearing procedure —
  of the five that survived the first draft and were retested: G-4 hole → 1
  failure, G-16 removed → 4, steering rewritten → 4, greeting silenced → 7,
  commit bar raised → 1; probation window → still green, refuted above.

### Phase 2 exit-criteria checklist

Copied from `roadmap.md` Phase 2 and re-executed against main at the close of
T2.8, not carried forward from the tasks that first satisfied them.

- [x] **A synthesized call replays byte-identically in CI (events, seq,
  payloads, transcript.)** Evidence: `tests/test_goldens.py`, 42 passed — six
  manifests re-driven through the production driver and diffed field for field,
  plus the transcript and the steering. The criterion read "a **recorded**
  call" until the owner amended it on 2026-08-26; the goldens are authored, and
  capture-and-replay is now Phase 5's. The mechanism is real and
  mutation-proven; the corpus is synthetic, and the criterion says so.
- [x] **Misroute FPR=0 becomes a merge-blocking gate (all misroute scripts ×
  all personas × both entry paths, release within two turns).** Evidence:
  `TestRealPeopleAreAlwaysReleased`, 91 passed over the full cross-product,
  asserting the exact exit each script declares. Red-proofs recorded at T2.6.
  *Known limit, now a settled decision:* no script is long enough to reach the
  probation hard-commit boundary, and the owner confirmed on 2026-08-26 that
  crossing it may commit an unclear caller. Corpus growth is Phase 10's.
- [x] **A deliberately-broken persona fails the new adversarial predicates
  (red-test proof).** Evidence: `tests/test_adversarial_predicates.py`, 58
  passed — every predicate carries a compliant shape that fails and an
  in-character shape that stays clean.
- [x] **Exactly one fake clock exists.** Evidence: a class-level grep finds one
  definition, `ssscammers/simscammer/clock.py`; T2.8 went further and
  collapsed the two *call drivers* onto one `Session` as well.
- [ ] **The LLM adversary reports mean simulated time-on-call, recording the
  baseline against which M2's ≥3-minute criterion and Phase 11's pre-registered
  margin are judged.** **Still blocked — on an authorized spend, not on a key.**
  The ask, the scale estimate, and the CI-secret caveat are `roadmap.md` open
  decision 5; they live there rather than here so a correction touches one
  passage. Nothing about this criterion can be verified offline — the whole
  point is a live model driving a real scam script — and it stays Phase 2's
  work rather than being moved in front of a later phase that has nothing to do
  with it.

### Owner reply of 2026-08-26 — what it decided, and what it did not

The reply, quoted exactly and in full, is the only primary source for what
follows:

> sysrehnsized nore recordsed. turn an unclear caller to batiing. api budget is
> not as bad for llm. less for other stuff though. proceed ot phase 3

It answers two of the four questions carried out of Phase 2 cleanly, gives a
disposition rather than an answer on the third, and does not touch the fourth.
Read it that way rather than as four answers: **an earlier draft of this entry
converted the third into an approval, a provisioned key, and an owner-supplied
schedule, none of which the reply says.** That over-reading was caught by the
Rule 1 review of this task and is corrected below.

1. **May probation expiry commit an UNCLEAR caller to baiting?** (T2.6.)
   **Answered: yes** — "turn an unclear caller to batiing". Today's behaviour
   stands: after `probation_hard_commit_seconds` (90s) an unclear caller is
   committed. No production code changed;
   `TestTheProbationBoundaryIsTheKnownGapInTheGate` still pins it, and its
   docstring now records the decision rather than describing an open question,
   so a future change remains a visible one. The FPR/engagement gap this leaves
   is real and unchanged: a slow, confused, hard-of-hearing real person is
   exactly the shape that reaches 90s without tripping a legitimacy signal.
   Phase 10 is where a mid-call posterior can revisit a commitment; that remains
   the plan, now as scheduled work rather than as a pending decision.
2. **Should the replay exit criterion say "recorded" or "synthesized"?**
   (T2.8.) **Answered: synthesized** — "sysrehnsized nore recordsed".
   `roadmap.md` Phase 2's first exit criterion is amended in place with a dated
   note saying what it used to say and why, and the Phase 2 checklist above is
   re-worded to match. Capture-and-replay moves to Phase 5 with a key move
   **and** an exit criterion that bites, because a relocated obligation with no
   criterion is an obligation that never gets built.

   The relocation is narrower than the first draft of this entry claimed — the
   caller side and the *event-level* gaps are capturable from persisted rows,
   the model's raw deltas are not, and the media-plane timing columns have no
   producer until Phase 7. `roadmap.md` Phase 5 carries the corrected shape and
   the reasoning; it is not restated here, so the two cannot drift.
3. **The LLM adversary's budget.** **Not an approval — a disposition.** The
   words are "api budget is not as bad for llm. less for other stuff though."
   That is cost tolerance. It names no figure and authorizes no run. Two things
   the first draft of this entry got wrong, both caught by this task's Rule 1
   review:
   - It said the criterion "stays blocked, on the key". A key was never the
     blocker — an adversary checked the host and found one already configured.
     (Stated that way on purpose: the check was of an untracked, machine-local
     file, so it is not a fact a committed document can assert. What a reader
     elsewhere can act on is that credentials resolve several ways, per
     [`secrets.md`](secrets.md).) The blocker is an authorized figure, and the
     concrete ask is now `roadmap.md` open decision 5, where an owner reading
     the active plan will actually find it.
   - It relocated the adversary to Phase 4 and made it a hard exit criterion
     there. That was wrong twice: the justification rested on the key claim
     above, and it would have stopped the G-11 open-line fix — a real safety
     defect — on an unrelated procurement. The owner's answer to this same
     blocker one phase earlier was "proceed ot phase 3", i.e. *don't stop for
     it*. Reverted; the adversary stays Phase 2's, and Phase 11's M4 exit
     ([`plan.md`](plan.md) M4) is the gate that already fails without the
     number.

   The ordering — adversary after Phase 3 — remains **this log's decision**,
   taken from "proceed ot phase 3". The reply is silent on the adversary's
   position in the sequence.

   What "less for other stuff though" constrains is genuinely ambiguous and is
   escalated below. Independent of how it resolves, Phase 3's monitor is
   designed cheap and offline-testable, because that is the right engineering
   answer under either reading. The rules for that phase — no sampling, a
   bounded excerpt, and a conftest guard that has to be *built* before "monitor
   tests run offline" is true of anything — live in `roadmap.md` Phase 3.
4. **The deictic persona-break gap in `output_filter.py`** (T2.5). **Not
   raised in the reply; still open.** The pre-TTS filter catches only
   first-person AI admissions, so "this is an automated assistant" reaches a
   scammer uncaught. It is queued as its own task. Phase 3 covers the
   persona-break half of G-17 at the MONITOR layer, which narrows the exposure
   but does not close it: the monitor is out-of-band and fail-open by design, so
   the sentence is still spoken before any verdict lands. The CODE-layer fix
   stays outstanding regardless of what Phase 3 builds. *(This paragraph is
   analysis, not an owner decision.)*

**One ambiguity resolved silently, recorded here because it should not have
been.** "turn an unclear caller to batiing" also has a broader reading — *route
UNCLEAR callers to baiting outright* — which the code genuinely distinguishes
from the narrow one: `_should_commit` commits on confidence, while
`probation_hard_commit_seconds` commits on elapsed time
([`state_machine.py`](../ssscammers/agent/state_machine.py)). The broad reading
would commit immediately rather than at 90s, a far larger FPR change, and it
would catch exactly the slow, confused caller decision 1 names. The narrow
reading is taken as the default because it answers the question that was asked;
if the broader one was meant, say so and the boundary comes out entirely.

### Open questions for the owner, raised 2026-08-26

Both are also listed in `roadmap.md`'s "Open decisions for the owner" (5 and 6),
which is where an owner working the active plan will find them.

1. **What does "less for other stuff though" constrain?** Two readings, and
   nothing in the reply separates them. (a) *Less for everything that is not the
   adversary*, which would bind Phase 3's monitor. (b) *Less for the non-LLM
   metered spend* — Deepgram STT, Cartesia TTS, Twilio minutes, all billed per
   minute and all maximised by design by an agent whose entire goal is wasting a
   caller's time. **Recommendation:** treat the monitor as budget-constrained
   either way (the cheap design is correct regardless), and answer only the part
   that is actually open. It is narrower than it looks: the media spend already
   has ceilings — `DAILY_MINUTES_CAP=360` and `DAILY_SPEND_CAP_USD=50`, enforced
   fail-closed at admission — so the question is not "does it need a cap" but
   "are 360 and $50 the intended numbers". The one genuine defect there is not a
   decision at all and is not escalated: `ESTIMATED_USD_PER_CALL_MINUTE=0.05`
   drives the dollar cap and [`config.py`](../ssscammers/shared/config.py) says
   plainly it is "**Not a measurement**", so the dollar cap is a guess wearing a
   number. Reconciling it against real invoices is scheduled work, not an owner
   question. Nothing is blocked on any of this; Phase 3 proceeds under the
   conservative reading.
2. **May raw, pre-filter model output be persisted at all?** `roadmap.md` open
   decision 6 and Phase 5's recorder key move carry the question and the full
   reasoning; it is not restated here. Two corrections belong in *this* log,
   because both were wrong in earlier drafts of this entry and both were caught
   by the Rule 1 review rather than by me:
   - "Raw model output is on disk today either way" — **false.** Nothing writes
     a `turns` row: the only `INSERT` in the package is into
     `schema_migrations`. Phase 5 does not add one string to an existing
     exposure, it begins the exposure, which changes what the owner is deciding.
   - "Every sentence that cleared the filter is committed as it streams" —
     **false**, and it cited the file that disproves it.
     [`conversation.py`](../ssscammers/agent/conversation.py) joins `spoken` and
     appends the turn **once**, after the stream loop; per-sentence commit is
     the thing Phase 4 exists to build. What is true is the *content*: the
     turn's recorded text is the surviving sentences with a fumble in place of
     the blocked one.

## Phase 2 close-out — recording the owner's reply

### T2.9 — Record the 2026-08-26 owner reply, and correct what it did not say

- **Scope:** documentation, plus one test class. `docs/execution-log.md` (the
  owner-reply section above, the Phase 2 heading and checklist, forward
  pointers on the T2.6 and T2.8 escalations), `docs/roadmap.md` (Phase 2's
  amended criterion and its open-adversary note, the replay-source correction,
  workstream row K, Phase 3's cost/testability constraints and offline-guard
  key move and exit criterion, Phase 5's recorder key move and both replay
  criteria, three new entries in "Open decisions for the owner"),
  `docs/plan.md` (the supersession note), `docs/secrets.md` (`ANTHROPIC_BASE_URL`
  and the proxy variables), `README.md` (the goldens are authored, not
  captured), `tests/test_call_scripts.py` (docstrings, the assertion message,
  and a new test). No production code.
- **Rule 1: four rounds, eight adversaries, four refutation rounds.** The gate
  re-ran three times because each round's fixes were a large enough change set
  to be reviewed in their own right, and each re-run found real defects in the
  previous fix. (Two round-4 adversary runs died on infrastructure — one
  stalled, one lost its machine to sleep — and were relaunched; a third attempt
  completed. No finding was lost to that.) Rounds 1 and 2 are summarised by
  what they changed rather than re-narrated; the drafts they corrected were
  never committed, so a future reader can check the outcome but not the
  intermediate text.
  - **Round 1** caught the substantive error of the task: the reply's "api
    budget is not as bad for llm" had been written up as an *approval*, a
    *provisioned key*, and an *owner-supplied schedule*. It is none of those.
    Also: a Phase 5 key move naming `CallRecording` (the model side, no timing
    field) for the caller's turns; a relocated obligation with no exit
    criterion; a present-tense claim about a monitor test suite that does not
    exist, which falsified a checked-off Phase 1 exit criterion; five stale
    "escalation" markers; the adversary having no roadmap home; `roadmap.md`'s
    "from a recorded event log", already adjudicated wrong at T2.8; row K still
    claiming "no golden gates"; and `plan.md`'s un-amended criterion and its
    now-wrong supersession count.
  - **Round 2** found six defects introduced by round 1's fix. The load-bearing
    one: "no provisioned key" was counterfactual, and it was the justification
    for having moved the adversary into Phase 4 — which would have stopped the
    G-11 open-line safety fix on a procurement. Reverted. Also a Phase 3 exit
    criterion that two adversaries independently *measured* as already green on
    the pre-phase tree (950/16 under a poisoned key with non-loopback sockets
    blocked, byte-identical to baseline, none of the 16 skips key-gated); a
    Phase 5 criterion depending on producers Rescope 6 assigns to Phase 7; a
    wrong claim about what a filtered turn persists; and `plan.md` widening
    owner authority a second time.
  - **Round 3** found six more, three of them substantive:
    - **The Phase 5 captured-golden criterion was still unmeetable**, now for a
      new reason — and round 4 showed the fix reasoned from a false measurement,
      so the corrected statement is given here rather than round 3's. Every
      `caller_turn` in the shipped goldens follows **the event before it** by
      exactly `seconds_per_turn`; the caller-to-caller spread (46.1, 26.0, 98.0,
      59.6, 26.0) is the agent's own speaking and hold time, which replay
      already reproduces. Round 3 compressed that into "consecutive
      `caller_turn` events sit exactly `seconds_per_turn` apart", which is
      plainly false against the corpus, and then inferred that nothing synthetic
      could produce non-uniform caller gaps — an inference round 4 also
      demolished independently, since `Session.idle` already advances an
      arbitrary float. Compounding it, `call_events` persists `ts timestamptz`
      (wall-clock, stamped by the sink) and **not** `CallEvent.at_seconds`, the
      monotonic elapsed replay
      actually drives on. Round 3 made the criterion **wet** on the strength of
      the false inference; round 4 made it dry and closable again — capture a
      call driven by an explicit per-turn gap schedule — and filed the real
      inbound call as open decision 8, a go-live acceptance item. A wet exit
      criterion would have been worse than the one Phase 2 already has: Phase 2
      waits on tooling that can be built, while "an event has not occurred" is a
      status no phase can engineer, and it carried no owner ask.
    - **The offline-test guard did not close the network.** An adversary
      demonstrated the bypass: the SDK's HTTP client trusts the environment, so
      with `HTTPS_PROXY` set the only socket it opens is to a loopback proxy —
      which the guard is *required* to allow for the migration-runner's local
      Postgres — and the request reaches the live API anyway. Round 3 closed the
      proxy instance; **round 4 showed that was patching an instance of a
      general defect.** `/etc/hosts`, a profile's `resolved_base_url`, an ssh
      tunnel and `socat` are the same hole, and enumerating them is unbounded:
      an allow-rule keyed on "loopback" cannot express "nothing reaches the
      vendor API". The spec now makes the allow-rule destination-specific and
      adds a second seam that does express it — an autouse fixture that refuses
      construction of a real client unless a test opts in — with the socket
      layer as defence in depth. Round 4 also found the credential half was
      derived from an incomplete resolution order in `secrets.md` (three steps
      documented, five in the installed SDK), that *clearing* `ANTHROPIC_BASE_URL`
      hands the destination to a profile rather than pinning it, and that an
      in-process socket patch does not inherit into the subprocesses the
      migration tests spawn.
    - **The rewritten assertion message claimed a pin the test did not have.**
      It said the boundary was pinned "in either direction". Measured: lowering
      `probation_hard_commit_seconds` from 90 to 5 — committing unclear callers
      *sooner*, the direction that baits more real people — left the suite
      green. That is the same behaviour this change set flags as the unresolved
      broad reading of the owner's reply, so the gap and the ambiguity were the
      same hole. Round 3's fix — a second test for the lower edge — was itself
      two-thirds wrong, and **round 4 found all three faults**:
      - Its authored failure message was **dead text under every input**.
        `UNCLEAR` is not in `BAITABLE_TRIAGE`, so from `ASSESSING` the only
        route to baiting is the boundary; reaching it at 75s means the setup
        guard above has already failed. The two assertions were mutually
        exclusive, and the whole test collapsed into its own threshold check.
        The behavioural assertion is now on the *phase* — `is ASSESSING`, not
        `not baiting`, which every terminal phase also satisfies — and the guard
        carries its own message.
      - The pair pinned a **default no shipped path reads**. Production takes
        `Settings.probation_hard_commit_seconds`, which `build_conversation`
        passes through; `make_director` builds a `PersonaDirector` directly and
        reads *its* default. Two independent literals, nothing tying them, so
        `PROBATION_HARD_COMMIT_SECONDS=5` left the suite green — measured.
      - The docstring claimed "the pair fails on either move", true only of the
        test-only literal.
      The fix deliberately does **not** pin `90.0`: the owner's decision was
      about the behaviour, not the number, and one adversary argued
      convincingly that adding literals to a change set whose material defects
      were all *copies drifting* is the wrong instinct. The boundary tests keep
      reading the boundary off the object — so a retune does not trip them — and
      one equivalence test in `test_config.py` pins that the two defaults have
      not diverged, with no new literal anywhere.
    - Plus: open decision 5 asserted the adversary was "bounded by nothing"
      (`HARD_CALL_CAP_SECONDS` and `max_tokens=400` bound it, and the estimate
      is now given so the figure is answerable); decision 6 listed one block
      cause where the filter has six, omitting `OWNER_PII` — the owner's real
      identity — and `SCANNER_ERROR`; and the timeout/error paths abandon the
      stream without flushing its buffer, so delta capture would newly persist
      raw *unvetted* text too, a worse category than the blocked sentence.
- **Red-proof (three mutations, over `test_call_scripts.py` + `test_config.py`,
  133 tests).** Run under the cache-clearing procedure above:
  - `PersonaDirector.probation_hard_commit_seconds` 90.0 → **5.0** (the unsafe
    direction): `..._still_on_probation_before_it_expires` fails **and** the
    drift guard fails — 2 failed / 131 passed. Before this task, that mutation
    was silent in both files.
  - 90.0 → **200.0** (the safe direction):
    `..._committed_once_probation_expires` fails and the drift guard fails —
    2 failed / 131 passed.
  - `Settings`' default 90 → **45**, drift only: **only** the drift guard fails
    — 1 failed / 132 passed. The boundary tests correctly do not see it, which
    is the separation the design intends: they pin behaviour at whatever the
    boundary is, and the config test pins that there is one boundary.
  - Restored: 133 passed, `git diff --stat ssscammers/` empty.
- **Refuted in writing, not silently dropped.**
  - *"The Phase 5 criterion pre-decides the raw-delta question."* It constrained
    the caller side and the timing source only, so an owner answer of "yes,
    persist deltas" left it satisfiable.
  - *"The escalation claims media spend is uncapped."* The sentence read
    "uncapped by anything **except** `daily_minutes_cap` and the estimated-rate
    spend cap" — it named both caps as the exceptions. The residue was real and
    adopted: the numbers were missing, and the estimate-vs-measurement gap is
    scheduled work, not an owner question.
  - *"The surviving model prefix on a blocked turn is replayable output, so the
    weak-gate recommendation understates what is available."* It is not:
    `turns.text` is post-splitter and post-join, and `RecordedTurn.deltas` are
    kept raw precisely so the splitter is re-run. A boundary bug is exactly what
    a recording of already-split output would hide.
  - *"`plan.md`'s `goldens/legit/` corpus is a third superseded site."* A
    different thing — consented real audio for false-positive measurement,
    already rescoped to decision-layer replay by Rescope 6, with its
    decision-layer equivalent shipped as T2.6's misroute gate.
  - *"`plan.md` should amend its milestone text in place, as `roadmap.md` does."*
    `plan.md` supersedes in its header preamble by its own convention — M2's
    barge-in is handled the same way and its milestone text is likewise
    untouched. Following the file's convention is not a defect.
  - *"The Phase 2 heading contradicts the checklist."* The "except the LLM
    adversary" clause reconciled them. Rewritten anyway: the heading is the
    greppable status token and "COMPLETE" should not appear on a phase with an
    open criterion. This entry is filed under Phase 2 close-out for the same
    reason — filing it under a "Phase 3 — IN PROGRESS" heading would have
    stamped monitor work as underway before a line of it exists.
  - *"This entry is too long for a task with no production code."* Measured
    against its neighbours the premise fails: T2.8 is 270 lines and this is
    under half of it. The related proposal — cut the per-round summaries because
    the drafts they describe were never committed — was declined for a stated
    reason: Rule 1 requires reporting what each adversary found and what
    survived, and those summaries are the only record of it. Describing
    uncommitted drafts *by outcome* is exactly the compromise the "no
    unverifiable quotes" rule already settled on.
  - *"Pin the boundary with literal `== 90.0` assertions in three places."*
    Declined, and the counter-argument is recorded because it is the better
    one: the material defects in this very change set were all copies of a fact
    drifting, so adding two more copies of `90` is the wrong instinct; and the
    owner decided the behaviour, not the number, so freezing it would encode
    authority never given. The equivalence assertion achieves the same
    protection with no new literal. The deeper fix — deleting
    `PersonaDirector`'s duplicate default so `Settings` is the single source —
    touches production code and is left for a task that owns it.
  - *"An explicit `api_key=` kwarg is a live bypass of the guard's credential
    half."* Not on the shipped path: `config.py` sources the key from the
    environment and `llm.py` passes that value through, so poisoning the
    variable poisons the argument. The narrower true case — a test hardcoding a
    literal — is closed by the client-construction seam, and the spec now says
    so rather than resting on the env half alone.
  - *"`GoldenManifest.idle_seconds` already injects a non-uniform caller gap."*
    It does not: it is applied once, after all lines, and only the dead-air
    golden uses it. The conclusion it was offered for still holds by a different
    route — `Session.idle` takes an arbitrary float — which is what the fix
    relies on.
- **Escalations (Rule 1 resolution 3).** Four owner questions, recorded in
  `roadmap.md`'s open-decisions list rather than only here: the adversary's
  spend ceiling (5), whether raw pre-filter model output may be persisted (6),
  which reading of "turn an unclear caller to batiing" was meant (7, listed
  because it was resolved by default rather than asked), and authorizing a first
  live inbound call plus the `at_seconds` column it needs (8).
- **One process failure worth recording.** Round 3's "six defects" miscount was
  reported by round 4 as having been *silently dropped* — present in neither the
  fix narration nor the refutation list. That is the one thing Rule 1 names
  explicitly as forbidden, and it happened here. Counts are corrected above and
  the finding is recorded rather than quietly fixed, because a dropped finding
  that gets fixed still means the gate leaked.
- **Where the review stopped, and why.** Four rounds. Round 4's findings were
  still substantive — a dead assertion message, a default no shipped path reads,
  a guard spec patching instances of a general defect — so this did not
  converge on trivia; it stopped because every surviving finding was resolved
  and the remaining work is Phase 3's own. Any later round should start from the
  Phase 3 guard spec, which is the newest and least-exercised text here.
- **Verification (final tree):** 952 passed / 16 skipped (950 + the boundary
  test + the default-drift test); textloop dry exit 0; `ruff check .` clean;
  `git diff --stat ssscammers/` empty — no production code changed.
- **Green on main:** run
  [33030385351](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33030385351)
  (commit `245b21e`).

## Phase 3 — MONITOR watchdog layer — IN PROGRESS

### T3.1 — The kill seam

- **Scope:** `ssscammers/agent/state_machine.py` (the `watchdog_killed` check
  moved below the real-person exits and below the already-exited guard),
  `ssscammers/agent/persona_director.py` (a one-way latch that records who
  asked), `ssscammers/agent/conversation.py` (`request_kill`, the event
  emitted at the next evaluation, and the sentence loop breaking on the
  latch), `tests/test_state_machine.py`, `tests/test_conversation.py`,
  `docs/roadmap.md`, and `docs/guardrails.md`'s G-20 row. No classifier: the
  verdict is injected directly, because the point of this task is that whatever
  produces a verdict, the call ends through the enforcement path that already
  exists.

  **What did *not* move, deliberately:** `guardrails.md`'s G-17 row still reads
  "the model-backed half is pending", and the preamble still says there is no
  MONITOR mechanism in the running system. Both remain exactly true — this task
  built the seam, not the classifier, and nothing produces a verdict yet. G-20's
  row *did* move, because half of "killing a call already in flight" now exists.
  Stated so a reader can tell deferral from oversight.
- **The design claim, and why the ordering is the whole of it.**
  `CallContext.watchdog_killed` has existed since the state machine was written
  and was set by nothing. Wiring a monitor into it *as positioned* would have
  made a classifier outrank a real person saying the safeword: they would get a
  bare hangup instead of the disclosure and the voicemail. It now ranks below
  the safeword, the DTMF escape, the allowlist and a positive real-person read —
  each of which also stops the persona, and does it while keeping a promise the
  watchdog cannot keep — and below the "once we have exited" guard, so a verdict
  cannot drag a call back out of an exit it already committed to. That last is
  the fixed-script carve-out, and taking it from the machine's own structure
  means the monitor cannot forget it.
- **Rule 1: two adversaries, one refutation round, nine findings, all resolved.**
  The review found more real defects than the change originally had lines of
  logic. In severity order:
  - **The kill did not stop the mouth.** A verdict landing at sentence 1 of a
    four-sentence reply left sentences 2-4 to be generated, vetted and spoken.
    On the live path it is worse than one turn: `tick()` runs outside
    `_turn_lock` while `perform` holds it, and `_end_call` drains queued audio,
    so the *whole* remaining reply is spoken before the hangup. "Ends within a
    second" was true of the state machine and false of the caller's ears — which
    is precisely the harm G-17 exists to prevent. Fixed by polling the latch at
    each sentence boundary, the same shape the cumulative filter already uses.
    My proposed "two-line reuse" was itself refuted: the fix needs four parts,
    and the reviewer who found the defect was wrong about its size while the
    other was right. The break must precede `spoken.append` (or the digit tail
    reflects words never said); the turn needs `failure="killed"` (or a
    kill-shortened turn is byte-identical in the log to a short clean one); the
    empty-reply fumble must be suppressed (or the persona speaks a stalling line
    on a call just decided unsafe — the fail-soft path defeating the guardrail);
    and an empty killed turn must not reach the transcript.
  - **A latched kill vanished if the caller hung up first** — the modal ending
    for a killed call, and `caller_hung_up` was the one evaluation path that
    never drained the pending verdict. Found independently by both adversaries.
  - **The design claim was untested.** Hoisting the check back above the guard
    left all 142 tests green. The test re-passed the releasing signal into the
    second evaluation, and `heard_safeword` / `emergency_suspected` are one-way
    latches in the triage engine — so in production they re-fire and are answered
    *above* the watchdog, making the guard unreachable by either vector. The
    test reproduced that unreachability instead of testing it. Now three vectors
    where the signal genuinely fades: DTMF (drained per evaluation), a cleared
    emergency, and — the production-realistic one — a caller released on a legit
    read who then talks scammy, since `_is_real_person` is recomputed each turn
    while the scam score only accumulates.
  - **The latch was a second, unguarded door.** `PersonaDirector.watchdog_kill()`
    was public and could end a call while bypassing every guard *and* emitting
    nothing. Whoever holds a director should not be able to stop a call
    anonymously, so latching and recording are now one operation.
  - **`findings: Sequence[str]` splatted a bare string into characters.** `str`
    satisfies `Sequence[str]`, ruff is the only static check in CI, and the
    result is valid JSON — so nothing downstream would ever have noticed. The
    monitor's verdict kinds are exactly single strings. Now rejected.
  - **A test that asserted nothing.** `test_a_clean_monitor_changes_the_event_stream_not_at_all`
    passed with the entire kill seam stubbed out: it compared two conversations
    with no tap anywhere, asserting that `build()` is deterministic — which
    `test_goldens.py` proves far more strongly. Rewritten with a real tap that
    wraps the sink, walks every turn event, and decides nothing. The reviewer
    who first said "delete it" withdrew that once the sentence-loop poll landed,
    because there is now a clean-path code path for it to protect.
  - **The carve-out's window.** The two adversaries flatly contradicted each
    other on whether one exists. Resolved by opening it: partially consuming
    `respond()`'s generator leaves the phase terminal while `_ended` is still
    False, for the whole span of the disclosure. Both were right about different
    things — the carve-out has no *escape*, and the `TERMINAL_PHASES` clause is
    what makes that true rather than redundant. The ten lines that opened it are
    now the test.
  - **A superseding kill vanished** — an operator hitting the switch on a call
    the monitor already killed left no trace, and the payload still named the
    monitor. Now recorded under `superseded`.
  - **A kill before `open()`** returned True and the persona still answered and
    greeted. Unreachable today; the `kill(call_sid)` lookup Phase 3 adds next is
    what makes it reachable.
- **Escalation — open question for the owner.** `end_reason` does not say the
  watchdog fired whenever a signal ranked *above* it answers first, and every
  exit above it is one. Two reachable shapes, and the second is the common one:
  - Killed *and* reading as a real person: exits `DISCLOSE_EXIT` /
    `DISCLOSED_EXIT`, voicemail promised.
  - Killed, then the scammer hangs up — the ending this entry itself calls the
    likeliest for a killed call: `caller_hung_up` is answered above the watchdog
    (correctly; they really did hang up), so the call ends `CALLER_HANGUP`.

  The phases are right in both cases and should not change: G-20's polarity is
  "take messages, never drop calls", and a real person must not lose their
  disclosure to a classifier. But `end_reason` is what the registry, the ledger
  and any dashboard read, and on both paths it is silent about the watchdog. The
  verdict survives in the event log, so it is recoverable there and absent from
  the outcome record — and `test_a_kill_is_still_logged_when_the_caller_hangs_up_first`
  asserts the event and deliberately not `end_reason`, because asserting today's
  value would pin the misattribution. **Recommendation:** record
  `watchdog_killed` alongside `end_reason` so a killed call is countable however
  it ends — resolved at the reporting layer, not the state machine. Deferred
  rather than done here because it adds a field the registry reads, which is a
  persisted-shape change and the owner's call (roadmap open decisions 2 and 3's
  class). Phase 5 is where it lands.
- **Rule 1, round 2 — the fixes were reviewed, and five more defects fell out.**
  Two were the same mistake in different clothes, and naming that mattered more
  than either instance: **the latch was read in one place while the turn carried
  on in others.**
  - **The kill stopped the mouth and not the hold.** Both adversaries found it
    independently. `_execute` falls straight into the hold branch after
    `_generate` returns, and the plan was built before the verdict, so it still
    carries one — roughly one baiting turn in four for marjorie. On the live
    path `perform` sleeps it out inside the turn lock the tick's `HangUp` is
    waiting on, and `_occupy_line` pushes the dead-air deadline past it, so
    neither the kill nor G-16 can fire. A killed call would sit playing a kettle
    for up to ninety seconds. I had written the sentence fix up as closing this;
    it closed the one-to-three-second half and left the ninety-second half.
  - **The fumble suppression read a loop-local instead of the latch.** `killed`
    records whether the sentence loop *observed* a verdict, which is weaker than
    whether the call was killed, and the two diverge exactly on the fail-soft
    paths: a verdict landing while sentence one is still forming, on a stream
    that then times out or raises, never re-enters the loop — so the persona
    speaks a stalling line on a killed call. A classifier racing a slow
    generation is the ordinary case. Neither the streaming test nor the
    fumble test could see it.
  - **The emitted payload aliased the director's lists.** `dict(request)` is
    shallow, and `CallEvent.payload` is that live mapping, so a later kill either
    retroactively rewrote an event already handed to the sink or vanished,
    depending on whether `superseded` existed at emit time. Now a deep copy.
  - **The director's latch was still a second public door** — the anonymity half
    was closed in round 1, the lifecycle half was not. Renamed to `_latch_kill`,
    with `Conversation.request_kill` documented as the only door and the owner of
    the guards.
  - **A recorded red-proof count was simply wrong** — see the corrected table
    below. An evidence number that does not reproduce is worse than no number.
  - Adjudicated and *not* changed, with the reasoning recorded because it cut
    against my own instinct: hoisting the poll out of `_generate` into `_execute`
    was proposed and rejected — the sentence boundary only exists inside that
    loop, so hoisting it would re-open the larger defect it fixed. The altitude
    error was that `_execute` had *no* guard for what follows `_generate`, which
    is now one condition carrying a stated invariant rather than three guards.
    A frozen `KillRequest` dataclass with an enum `source` was also proposed,
    matching `TurnPlan`/`Transition`/`FilterResult`, and deferred: it would make
    the aliasing bug impossible by construction, but `"kill_switch"` has no
    caller yet, and pinning a persisted payload shape before its only consumer
    exists is the wrong trade.
- **Red-proof (twelve mutations, `test_conversation.py` + `test_state_machine.py`,
  157 tests, `.venv/bin/python` — the system interpreter lacks `pytest-asyncio`
  and reds every async test).** Under the cache-clearing procedure. Six of these
  were measured *silent* before the review's fixes:
  - Watchdog hoisted above the terminal guard: **was 142 passed → 3 failed**.
  - Terminal guard deleted from `_evaluate` entirely: **3 failed**.
  - `request_kill` drops the terminal-phase half of its refusal:
    **was 142 passed → 2 failed**. *(An earlier version of this entry recorded
    "142 → 2 failed" against an ambiguous description of which guard was mutated;
    at the time the true figure was 1. Both readings are now listed separately
    with their own numbers.)*
  - `_report_pending_kill` dropped from `respond`: **was 100 passed → 1 failed**.
  - …from `tick`: **5 failed**. …from `caller_hung_up`: **1 failed**.
  - Sentence loop no longer polls the latch: **3 failed**.
  - Post-loop latch read removed: **2 failed** (round-2 fix).
  - Hold no longer gated on the latch: **was 156 passed → 1 failed**. The first
    version of this test planned no hold at all, because only a *baiting* phase
    plans one and a first caller turn lands in ASSESSING — it asserted an absence
    that was already absent. It now asserts the plan carries a hold before
    asserting the hold is skipped.
  - Fumble suppression removed: **3 failed**.
  - Emit uses a shallow copy: **1 failed** (round-2 fix).
  - Idempotence flag removed: **was 156 passed → 1 failed**. Also initially
    untested: every call sequence that reaches one drain site ends the call, so
    the contract is now pinned at the unit instead.
  - Restored: 157 passed.
- **Known limit, recorded rather than fixed:** a killed call is **not
  byte-replayable**. Skipping the fumble and the hold skips
  `rng.choice(FUMBLE_LINES)` and `rng.choice(holds)` on the one shared stream, so
  every later draw shifts. Nothing observes it today — no golden kills a call,
  and replay drives recorded *model* streams, not kill injections — but "replay
  determinism holds" would be false if written unqualified. Making a killed call
  replayable means recording the kill as a re-drivable input, which belongs with
  the monitor that produces one.
- **Verification (final tree):** 982 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean.
- **Green on main:** run
  [33032632910](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33032632910)
  (commit `a1a53f6`).

### T3.2 — The offline-test guard

- **Scope:** `tests/conftest.py` (the guard), `tests/test_offline_guard.py`
  (new — the guard's own tests), `README.md`, `docs/secrets.md`,
  `docs/roadmap.md`. Sequenced before `monitor.py` deliberately: the roadmap
  makes "monitor tests must run with no key and no network" a Phase 3
  requirement with an exit criterion, and a gate written after the code it
  guards is a gate nobody ever watched fail. Same reason CI landed at T1.1.
- **Four layers**, because the review demonstrated a live escape past each one
  alone: poisoned credentials for every variable the SDK resolves one from; a
  *pinned* base URL and cleared proxy variables; sockets refused except to
  declared local service ports; and the SDK's HTTP layer refusing to run.
- **Rule 1 — the review found a hole in the guard that let a test open real
  off-box TCP.** Both adversaries demonstrated rather than argued, which is what
  made this round worth the cost. In severity order:
  - **A `bytes` host walked straight through.** `_is_loopback` returned True for
    any non-`str` host, meaning to wave through `AF_UNIX` paths — but CPython
    accepts a `bytes` host for ordinary TCP, so `connect((b"1.1.1.1", 80))`
    **succeeded**, real off-box. The root cause was policy, not a typo: the
    helper guessed the address family from the argument's *shape* and failed
    **open** on anything it did not recognise. It now keys on the socket's own
    `family`, parses with `ipaddress`, and fails **closed**. The same rewrite
    fixed two silent errors in the same predicate — `127.evil.example.com` was
    *accepted* by a string prefix test, and `::ffff:127.0.0.1` and `LOCALHOST`
    were *rejected*, which would have failed the migration legs with the guard's
    error rather than a connection error.
  - **A zero-layer composite neither adversary reported alone.** Layer 4 patched
    `Anthropic`/`AsyncAnthropic`; `AnthropicBedrock` and `AnthropicVertex`
    inherit from neither, read their *own* base URLs, and ignore the poisoned
    credentials. With `ANTHROPIC_BEDROCK_BASE_URL` pointed at a loopback
    forwarder, layers 1, 2 and 4 all no-op and layer 3's all-loopback carve-out
    waves it through — no layer standing. Fixed by patching
    `SyncAPIClient`/`AsyncAPIClient`, the bases every variant inherits, which is
    *fewer* lines than the leaf-class loop it replaced, and by adding the variant
    base URLs to the cleared set.
  - **The socket rule was wider than the spec it implements.** The roadmap says
    "loopback permitted only on the declared Postgres port"; the implementation
    allowed all loopback, which put the pinned base URL inside the guard's own
    carve-out. Amending the spec to match a weaker implementation, inside the
    task that implements it, is exactly what this gate exists to catch — so the
    rule was narrowed instead, to a static port set. Static because sourcing it
    from `MIGRATIONS_TEST_DATABASE_URL` would let the environment being guarded
    against widen the guard. The migration test that dials `127.0.0.1:1` needed
    no rework: an undeclared *loopback* port now raises `ConnectionRefusedError`
    rather than `RuntimeError`, so `python -m ssscammers.db`'s `except OSError`
    still exits 1 — and the test got stronger, since it now proves the guard
    refuses the port rather than proving the OS does.
  - **The guard had no test, and its absence was invisible.** A reviewer removed
    `autouse=True` from both fixtures and the full suite stayed green with an
    identical tally — the false green the red-proof procedure above calls the
    dangerous one. Every escape found in this review was found by a probe that
    was then deleted; none was a standing regression. `tests/test_offline_guard.py`
    is the durable form, and seed 1 below is the proof it would have caught the
    `bytes` escape itself.
  - **Layer 4 refused `httpx.MockTransport`** — the SDK's own supported offline
    seam, which opens no socket, and the one a monitor test is most likely to
    reach for. Its error text asserted "a test tried to call the real Anthropic
    API", which in that case is simply false. The refusal stays unconditional and
    the text now says *HTTP layer* and names the sanctioned seam.
- **The one place the adversaries disagreed, and how it was resolved.** The
  roadmap specified an opt-in ("unless a test opts in"). A wanted it built:
  greppable, countable in review. B wanted it rejected: a marker is trivially
  reached for, and the case it serves is already served better by injecting
  `RecordedAnthropicClient` through `ClaudeBrain(client=...)`, which needs no
  HTTP stack. **B's position taken** — an escape hatch built before a
  demonstrated need is one that gets used before there is a demonstrated need.
  B's alternative, a `MockTransport` exemption, was *also* deferred on B's own
  reasoning: detecting it needs two private `httpx` attributes, so it would break
  silently on a dependency upgrade, which is the failure class this task exists
  to close. Both positions and the deferral condition are recorded in the
  fixture's docstring, and `roadmap.md`'s clause is amended with the reason.
- **Also fixed:** a dead `session_mocker` parameter that survived only because
  pytest strips defaulted parameters — dropping the default would have errored
  every test in the suite, and `pytest-mock` is not even a dependency; a
  docstring that stated the *opposite* of the implemented contract, left over
  from the reverted `__init__` design; an `ImportError` branch documented as the
  "`[dev]`-only install shape", which does not exist because `anthropic` is a
  base dependency; and a missing `ANTHROPIC_FOUNDRY_API_KEY` /
  `ANTHROPIC_WEBHOOK_SIGNING_KEY`.
- **`pytest-socket` was considered and rejected** (CLAUDE.md's search-before-writing
  rule): it implements layer 3 only, has no port-scoped allow-rule, and cannot
  express layers 1, 2 or 4. Recorded in the module docstring.
- **Red-proof (five seeded regressions, `.venv/bin/python`).** Under the
  cache-clearing procedure:
  - `autouse` dropped from `_offline` — **previously left the suite green**; now
    **8 failed / 1005 passed**.
  - `autouse` dropped from the SDK layer: **6 failed / 1007 passed**.
  - Layer 4 reverted to the two leaf classes: **2 failed** — both Bedrock and
    Vertex.
  - The fail-open host check restored: **1 failed** — the `bytes` escape.
  - The socket rule widened back to all-loopback: **1 failed**.
  - Restored: **1013 passed / 16 skipped**.
- **Pushed red-proof — and the first two attempts were false reds I nearly
  banked.** The exit criterion requires the seeded regression pushed, red, with
  the run URL recorded, and run *with `HTTPS_PROXY` set*. Recording what went
  wrong, because the failure mode is the one this procedure exists to catch and
  it is not obvious:
  - **Attempt 1** ([33033796344](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33033796344)):
    red, and worthless. Setting the proxy at *workflow* level killed every job at
    `actions/checkout`, before a single test ran. Eight red jobs and not one of
    them evidence.
  - **Attempt 2** ([33033885886](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33033885886)):
    also worthless. `git checkout` on the workflow reverted it to HEAD — which
    was *already* the seeded commit — so the step-scoped fix landed on top of the
    workflow-level proxy instead of replacing it.
  - **The tell, both times, was structural rather than in the logs:** `ruff` and
    `textloop` were failing. Disabling a pytest fixture cannot make a lint job
    fail. A red run whose failures exceed the causal reach of the seed is not
    evidence — and this is exactly where that is easy to miss, because a
    red-proof is the one procedure you enter *wanting* red.
  - **Attempt 3, the real one**
    ([33033946268](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33033946268)):
    all four `tests` legs red with **10 failed**, every failure in
    `tests/test_offline_guard.py`; `ruff`, `textloop` and both `migration runner`
    legs **green**. Two failures carry the weight: `test_no_proxy_survives`
    proves the proxy really was set on the step, so the guard was proven in the
    false-pass configuration; and `test_a_live_connection_off_box_actually_raises`
    means that without the guard the runner genuinely reached off-box. Branch
    deleted; main never contained the regression.
- **A process error worth recording.** Stashing the work onto the throwaway
  branch put the real change and the seed in the same commits, so deleting the
  branch deleted T3.2 as well. Recovered from the dangling commit and un-seeded.
  The right order is commit the work to main *first*, then branch for the seed —
  the red-proof procedure above says main must never contain the regression, and
  says nothing about the branch containing the only copy of the work.
- **Verification (final tree):** 1013 passed / 16 skipped; textloop dry exit 0;
  `ruff check .` clean; `.github/workflows/ci.yml` unmodified.
- **Green on main:** run
  [33034122366](https://github.com/JasonL1238/ScammingScammingScammers/actions/runs/33034122366)
  (commit `284bd7b`). Throwaway branch deleted from the remote.

### T3.3 — The out-of-band watchdog

- **Scope:** new `ssscammers/agent/monitor.py` (the tap, the excerpt, the worker, the
  pool) and `tests/test_monitor.py` (54 tests); `ssscammers/shared/enums.py` gains
  `MonitorFinding`; `tests/test_schema_enums.py` records the enum→SQL deferral in its
  `not_persisted` set; `tests/helpers.py` absorbs `build`, `drain`, `spoken`,
  `ScriptedBrain` and `RecordingSink` from `tests/test_conversation.py`, and
  `tests/test_replay.py` drops its duplicate `RecordingSink`; `docs/guardrails.md`,
  `docs/roadmap.md` and `README.md`.

  **What did *not* move, deliberately.** No classifier, and nothing constructs a
  `MonitorPool`, so **no live call is watched by anything**. `guardrails.md`'s preamble
  still says the `PROMPT + MONITOR` guardrails are PROMPT-only in the running system,
  because a mechanism with nothing behind it is not a control. What changed is that
  closing each of them is now a classifier and a wiring change rather than a component
  that does not exist.
- **The design claim.** The watchdog is out of band in the strongest sense available:
  it never runs inside a turn, never holds a lock a turn holds, and cannot delay a
  sentence of audio. The tap *is* the event sink — `CallMonitor` wraps whatever sink the
  conversation had — so nothing in `conversation.py` knows the module exists and a clean
  monitor leaves the stream byte-identical. Only a model-generated `agent_turn` starts a
  classification; caller speech is context, because the excerpt that judges a reply
  already contains the speech that provoked it.
- **Rule 1: two rounds, four adversary runs, two refutation rounds, 27 findings.**
  Round one produced 14 (A: 4 correctness, B: 10 design/verification); the fixes
  cascaded far enough to require the gate to re-run, and round two produced 13 more on
  the cascade alone, two of which were the same defect seen from both angles. Every one
  was fixed. Two adversary runs died on infrastructure (one stalled, one lost to the
  machine sleeping) and were resumed rather than substituted for.

  The defects that mattered, in severity order:
  - **The turn being judged could fall out of the excerpt that judged it.** The buffer
    was one bounded deque of "the last N turns", so a model turn waiting on a slow
    classifier could be pushed out by the caller's replies — and the request that turn
    *raised* was then spent judging caller speech alone. A real model call, a real
    permit, a clean verdict reached by reading nothing the persona said. The excerpt is
    now assembled by priority rather than recency: unjudged model turns first, then as
    much recent context as the remaining budget allows, since the caller's words cannot
    breach a guardrail and the persona's can.
  - **A miss was silently discarded whenever the monitor stopped.** `_missed` was read
    only at the next take, so a call ending first threw the count away unread — and
    `_missed` only grows while the classifier is behind, which makes the modal ending
    here (a scammer hanging up the moment the persona breaks) exactly the case that lost
    it. Quantified by an adversary at 155/200 simulated calls and 1,480 unreported
    turns. It now reports through `_stop()`, the one funnel every termination passes.
  - **`aclose()` swallowed a cancellation aimed at its own caller.**
    `suppress(CancelledError)` around `await self._task` cannot tell "the worker I just
    cancelled" from "somebody cancelled *me*", and cancelling a task then awaiting it
    always parks for a loop iteration — so every close of a live worker opened the
    window. A supervisor shutting a call down got a call reporting itself as finished
    normally. Now `asyncio.wait({task})`, which never re-raises the awaited task's
    cancellation and lets an outer one through.
  - **A failed `open()` poisoned the pool permanently.** `self._loop` was assigned
    before the steps that can raise, so an attempt that created no task and contended no
    semaphore still claimed the loop it failed on — and the next entirely correct
    `open()`, in a live loop, was refused with a message that was simply false. This is
    the round-one defect (`conversation.events` installed before `start()`) reinstated
    one field over, in the method whose docstring promises "a failure changes nothing".
  - **A classifier's own `TimeoutError` was reported as the monitor's deadline
    expiring.** `socket.timeout` *is* `TimeoutError` and `OSError(ETIMEDOUT)`
    instantiates as one, so any transport timeout inside a real classifier produced
    "classifier exceeded 4.0s" about a deadline that never fired, with the traceback
    dropped by `logger.warning` — and would send an operator to raise `timeout_seconds`
    against it. Now `asyncio.timeout(...) as deadline` and a branch on
    `deadline.expired()`. This is the same misattribution class as the permit hoist
    below, one clause later in the same function.
  - **The permit wait was inside the classifier's blame.** A cross-loop or otherwise
    failed acquire was logged as "classifier failed" about a classifier never entered.
    The acquire moved outside `_classify`'s `try` — a fix neither adversary proposed in
    round one and which one produced during the refutation round, closing two separate
    findings at once.
  - **The fail-*off* path left inconsistent state.** The `except Exception` around the
    worker loop retired the watchdog without setting `_stopped`, so `observe` kept
    recording, evicting and counting misses on the turn path for the rest of the call
    with nothing left to read any of it. It now routes through `_stop()`.
  - **Smaller, all real:** a truncated *fixed script* logged "the tail of the persona's
    own words was not judged" about a turn never judged by design — at any tuned-down
    cap that fires on every disclosed call, which is how the one line standing between
    an operator and a half-read persona becomes the line they ignore. Truncating a model
    turn logged nothing at all. `aclose()` nulled the task handle before cancelling,
    making `active` false by assignment. Two `except asyncio.CancelledError: raise`
    clauses were inert by construction (`CancelledError` derives from `BaseException`).
    The `while not self._stopped:` condition could never end the loop, and
    `_take_request`'s empty-queue guard plus its handler in the worker were the same
    unreachable check written twice — now one guard that raises loudly, because the
    alternative to skipping is the excerpt defect above.
- **Two claims I wrote that the review falsified.** Recorded because they are the
  reason the gate exists.
  - `guardrails.md`'s G-17 row said fixed scripts are never a trigger "*so* no verdict
    can suppress a disclosure". Non-sequitur: not classifying a script does nothing
    about a kill latched on an *earlier* turn. What actually stops that is
    `request_kill` refusing once the phase is terminal, plus the state machine ranking
    the watchdog below every real-person exit. The doc named the weakest of the three
    mechanisms as its proof — the sentence a refactor would preserve while deleting the
    one that holds.
  - `roadmap.md` said the scripted-trigger rule has "no observable effect in production
    today, because `call_ended` follows one event later". The premise is right and the
    inference was not: `_execute` *suspends* at `yield Say(...)` between the two emits,
    so a consumer that yields there hands the worker a slot to classify the disclosure
    in. One adversary demonstrated it with an inserted `await`; the other refuted the
    demonstration (production's `push_frame` completes without suspending on the default
    path) but not the correction. The honest claim is "not load-bearing *on today's
    stack*", which rests on third-party scheduling details under a `pipecat-ai>=1.7` pin
    with no ceiling.
- **A methodological finding worth more than any single defect.** Round one's design
  adversary ran a 46-mutant sweep; 18 survived green and became findings. It did not
  find the excerpt-eviction defect — and two of its own mutants were *inside that
  function*. Its explanation, unprompted: mutation testing perturbs code that exists and
  there is no mutant that **adds** a branch, so it is blind by construction to the whole
  omission class. It read the inert trigger accounting as dead defensive code; the other
  adversary asked what the accounting should have been doing and found the module
  tracked *how many* triggers were pending but never *which*.

  The follow-up sharpened it further. Round two's correctness adversary validated the
  new excerpt logic with a randomized property harness — 400 rounds, five invariants,
  non-vacuity shown by kill rates against three mutants. Asked whether that closes the
  gap, the design adversary pointed out that every invariant reads `monitor._missed`,
  the in-memory counter, and none reads the log — so a build that maintains the counter
  perfectly and never reports it passes all 400 rounds. That is the `_missed`-discarded
  defect exactly: invisible to the harness built to validate the fix beside it. Mutation
  is blind to omission; properties are blind to whichever boundary they decline to read.
  Neither replaces reading the contract.
- **Findings that were refuted rather than fixed**, recorded so the list is not read as
  unanimous: the excerpt-eviction repro as originally filed (an all-caller feed) is a
  test-only path, since every non-terminal `handle_caller_turn` sets
  `consult_model=True`; the chars-per-token argument for agent truncation being
  reachable (the project's own measurement puts the wordiest persona at ~273 output
  tokens); "the `isinstance` assertion contributes nothing" (circular — deleting it and
  showing the mutant survives proves it was the thing catching it); and the claim that
  the scripted-trigger rule provably fires in production.
- **Red-proof: 37 seeded mutants, every one red, none vacuous.** 26 after round one and
  11 after round two, each pointed at the test that must catch it. Four went green on
  first run and were themselves defects in the tests: the byte-identical-stream test
  could not see a tap that *awaited* the classifier (the conversation runs on a
  simulated clock, so real blocking moved no timestamp); the `open()` reorder was masked
  by the loop check added beside it; the context buffer's `maxlen` lost its behavioural
  signature once `_excerpt` capped its own output; and a fail-open test passed against a
  watchdog that died on its first bad turn, because it never checked the *next* turn was
  still watched.
- **One test was replaced for flakiness, not correctness.** The
  semaphore-outside-the-deadline property was pinned with real durations — a 50 ms
  classifier against a 120 ms deadline — and measured at 4 failures in 15 runs under
  load, with the classifier's true span reaching 211 ms. It now pins the *ordering* with
  no magnitudes: the only permit is held from outside, the deadline is short enough that
  any suspension exceeds it, and the classifier never suspends, so as shipped no
  deadline can expire however small. An assertion that the classifier has *not* answered
  while the permit is held proves the worker is genuinely queued — without it the mutant
  would wrap an uncontended acquire, never suspend, and survive.
- **Verification (final tree):** 1067 passed / 16 skipped; `ruff check .` clean;
  textloop `--all-scripts --dry` exit 0; 37/37 mutants red.
