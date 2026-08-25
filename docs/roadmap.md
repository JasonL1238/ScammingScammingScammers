# Execution Roadmap — everything left to build

> **Status: active execution plan.** [`plan.md`](plan.md) remains the design record and
> the source of the quality bar; this document sequences all remaining work against the
> code as it actually exists (Twilio Voice + Pipecat + FastAPI, one Python package, one
> process). Where the design record's scope is impossible on this architecture or
> conflicts with the project's own legal posture, the rescope is stated here explicitly —
> nothing is silently dropped. Per `CLAUDE.md` Rule 0 there are **no time estimates
> anywhere in this document**: phases are ordered by dependency and risk only, and each
> ends with a working, tested increment. File and line references are accurate as of
> this commit; the named symbols are the durable anchors when later phases move code.

> **Owner decision (2026-08-25): direct-to-main workflow.** Work lands on `main`
> directly, one commit per reviewed task, with no pull requests and no branch
> protection. "Merge-blocking" throughout this document therefore reads as
> **push-gated**: CI runs on every push, and a red run on `main` halts all other work
> until fixed — a process rule enforced by the execution log
> ([`execution-log.md`](execution-log.md)), not a repository setting. Exit criteria
> phrased as "a deliberate PR … fails CI" are met by the same seeded regression pushed
> on a throwaway branch, with the red run recorded before the branch is deleted.

## Workstreams

Every open item from the survey of the repo, lettered so phases can reference them:

| # | Workstream | Today |
|---|---|---|
| A | Postgres persistence — DB-backed event sink + call/caller/turn writers | `LoggingEventSink` only; nothing writes the DB at runtime |
| B | Recording pipeline — Twilio → content-addressed R2, retention enforced | recordings stay in Twilio; [`legal.md`](legal.md) promises deletion in the present tense with no code behind it |
| C | Enrichment worker — Batch-API post-call analysis | compose declares the service; the package does not exist |
| D | Dashboard — Next.js, Tailscale-private | compose declares the service; the directory does not exist |
| E | MONITOR guardrail layer | G-5, G-7, G-8, G-9, G-10, G-19 are PROMPT-only ([`guardrails.md`](guardrails.md)) |
| F | Cancellation-safe turn executor, then barge-in | `should_interrupt=False` at [`media.py:387`](../ssscammers/agent/media.py) — M2's unmet exit criterion |
| G | `NOTICE_AUDIO_URL` runtime monitoring | boot check validates URL shape only; a 404-at-call-time clip records people with no notice |
| H | Screening v2 (M3), rescoped to Twilio | deterministic triage only |
| I | Strategy engine (M4) | static YAML tactic weights; frustration machinery tested but unreachable |
| J | Intelligence layer (M5), rescoped to the legal boundary | nothing built |
| K | Quality-bar infrastructure — replay, goldens, latency SLO, chaos, OTel | test suite is strong but there is **no CI**, no replay of recorded calls, no golden gates |

## Honest rescopes and escalations

Six parts of the design record cannot — or should not — be built as written. Naming
them here is the plan; building around them silently would be planning fiction.

1. **Admission-time screening is metadata-only; acoustic features keep their designed
   post-answer role.** Twilio exposes no pre-answer media plane — the route decision
   must be returned as TwiML from the webhook before any audio exists — so
   [`plan.md`](plan.md)'s three-way pre-answer routing collapses to deterministic
   metadata triage at the webhook, and no acoustic signal can ever inform the
   answer/route decision. The acoustic features themselves are **not** removed:
   `plan.md` specifies dead-air onset and call-center babble as *post-answer* features,
   post-answer caller audio is available (`audio_in_enabled=True` at
   [`media.py:364`](../ssscammers/agent/media.py); the pipeline already consumes caller
   speech-energy events), and Phase 10 schedules both as inputs to the in-call
   commit/release posterior. Phase 10 also schedules the design record's **pre-answer
   metadata tier** — STIR/SHAKEN `StirVerstat`, Twilio Lookup (the schema reserves
   `callers.lookup_data` for exactly this), NANPA allocation, neighbor-spoofing, the
   FTC DNC complaint feed, velocity — which needed no rescope at all, only a home.
2. **Speaker embeddings are escalated, not built.** M5's "speaker embeddings and
   operator clustering" are voiceprints, and [`legal.md`](legal.md) flatly forbids
   voiceprints, speaker ID, and cross-call biometrics — caller correlation by phone
   number only. Phase 12 ships non-biometric campaign linking and files a written
   decision document with the recommendation to **keep the boundary**; no audio-derived
   speaker feature is computed or schema'd unless the owner changes the docs first, and
   a CI gate enforces the absence either way.
3. **The scammer-script-stage tracker does not exist.** M4's bandit context of "script
   stage (opener → hook → payload → payment)" has no implementation. The bandit
   conditions on `CallPhase` plus the realtime `scam_type` guess (allowed to be wrong by
   contract; corrected offline by enrichment labels). A script-stage estimator is an
   optional extension, not an assumption.
4. **Wallet-address and payment-instrument intelligence is fenced off by the legal
   posture.** `plan.md`'s campaign linking via wallet addresses and its crypto/bank
   entity extraction conflict with [`legal.md`](legal.md)'s will-not-build list (no
   harvesting of scammer-side payment details). Resolved in favor of `legal.md`:
   Phases 9 and 12 assert the absence by test.
5. **The two-tier LLM router is superseded, deliberately.** `plan.md`'s
   fast-model/large-model router is replaced by the built single-model design —
   thinking disabled, effort low, sentence-streaming — whose latency rationale is
   recorded in [`llm.py`](../ssscammers/agent/llm.py). Escalating to a second model
   mid-turn is dead air on a phone line; reintroducing a router would be a new
   decision, not deferred work.
6. **Replay is decision-layer replay; raw audio ground truth is the recording.**
   `plan.md`'s canonical log wanted audio frames, VAD events, STT partials, TTS
   requests, and playback timings in the event log. The event log stays at the decision
   layer (Phase 2); the audio ground truth is the dual-channel R2 recording (Phase 6);
   and the media-plane events the schema's `turns` columns need — STT finals with word
   timings and confidence, playback and interruption timings — get their producer in
   Phase 7's media-seam work, flowing through the Phase 5 sink. Byte-identical replay
   reproduces every decision, not the waveform.

## Cross-cutting rules (every phase, no exceptions)

- **Rule 0 and Rule 1 govern every task.** Quality over cost, no shortcut labeled
  pragmatic; every task ends with the two-adversary review gate before it is reported
  done; cascades are the correct amount of work.
- **The one rule is absolute.** No phase adds any capability to originate contact.
  `tests/test_no_outbound.py` stays green at every merge; every new package inside
  `ssscammers/` enters its scan scope, and code outside the package (the dashboard)
  gets an equivalent scan wired into CI. Complaint export is a download the user files
  — never a transmission.
- **Failure polarities are fixed and opposite.** The pre-TTS filter, ledger, and
  admission fail **closed**; MONITOR-class model checks fail **open** and are never the
  sole enforcement of anything safety-critical. `PROMPT + MONITOR` rows are never
  relabeled CODE-grade.
- **Fixed human-reviewed scripts** (disclosure, victim warning, 911, and Phase 10's
  recovery script) bypass filter, monitor, and cancellation. No phase may create a path
  where a legally required script is suppressed.
- **Single process, single worker is load-bearing.** The concurrency cap, live-call
  registry, and turn lock are in-process. New processes (enrichment, dashboard, R2
  worker) communicate only via Postgres and R2; nothing **on the call path** ever
  awaits a database query — admission and the live turn path read only memory, while
  background writers and pollers (the event sink, the app-level poller) run off-path;
  every cap, switch, and refusal degrades to voicemail, never a rejected or dropped
  call.
- **Ordering contracts survive every refactor:** `report_outcome` before socket close
  (the voicemail promise), `auto_hang_up=False` hangup-by-socket-close, recording
  started before the notice plays, two-phase finish/release, idempotency against Twilio
  webhook retries.
- **Schema discipline.** Changes go only through the Phase 1 migration runner with a
  written migration-impact note; persisted shapes (event payloads once persisted, enum
  values) change only with explicit owner authorization; SQL enum evolution is
  append-only and mirrored from `ssscammers/shared/enums.py`.
- **`ssscammers/shared/` stays stdlib-only.** The safety suite must run with zero
  third-party packages; asyncpg, R2 clients, and OTel imports live outside it.
- **Determinism discipline.** All randomness on the injected per-call seeded rng, every
  draw logged into the event stream; all time via injected clocks (monotonic **and**
  the ledger's civil date). Once byte-identical replay is established (Phase 2), no
  later phase may regress it — every stochastic addition (bandit, estimators) logs its
  draws.
- **Every new CI gate is demonstrated red** on a deliberately seeded regression, then
  green after revert — a gate never proven to bite is not a gate.
- **Least privilege per [`secrets.md`](secrets.md).** Scammer-influenced processes hold
  the narrowest possible keys; caller speech, transcripts, recordings, and adversarial
  eval scripts are data — never instructions — to every model in every phase.
- **Docs move with code in the same commit.** `README.md`/`CLAUDE.md` in sync,
  `guardrails.md` enforcement columns truthful, no doc describing unshipped work in the
  present tense.

## Phases

### Phase 1 — Groundwork: safety hygiene, CI, migration machinery *(G, K, A-prep)*

**Goal.** Close the live gaps that must not wait one more phase, and make every later
gate real. Three facts force this to the front: there is **no CI configuration in the
repo** despite `legal.md` and `guardrails.md` claiming CI-enforced gates; migrations
apply only via initdb on a fresh volume, so the first post-deploy schema change would be
an emergency; and a `NOTICE_AUDIO_URL` that is fetchable at boot but 404s at call time
records real people with no notice — a legal-notice hole, today.

**Key moves.**
- Stand up merge-blocking CI: full pytest suite via the project venv plus
  `python -m ssscammers.simscammer.textloop --all-scripts --dry` (already
  exit-code-bearing); pin the pytest import mode per the `tests/conftest.py` warning.
- `NOTICE_AUDIO_URL` (G): replace the scheme-only boot check with an actual boot fetch
  plus a periodic in-process re-probe; on failure the next engage document degrades to
  the fixed Polly `NOTICE_TEXT` verb ([`twiml.py`](../ssscammers/agent/twiml.py) already
  supports clipless operation) and an NTFY alert fires — the notice degrades to text,
  it never silently disappears.
- Purge the committed owner PII from `.env.example` (`OWNER_PII_DENYLIST` line 44 holds
  real name and email) to placeholders; note git history retains it; real denylist
  values move to the secret store per [`secrets.md`](secrets.md).
- Build the **migration runner** (ordered application, `schema_migrations` tracking,
  fresh **and** existing volumes) and redesign `tests/test_schema_enums.py`'s sync
  strategy: its exact-order parse of `001_initial.sql` alone cannot survive an
  `ALTER TYPE ADD VALUE`; the new strategy parses cumulative migrations and enforces
  append-only Python enum evolution. Fix the false "generated from enums.py" header.
- Make `docker compose up --build` succeed from a clean checkout. The largest missing
  piece is the build context itself: `docker/` does not exist, yet compose builds the
  agent **and** enrichment from `docker/agent.Dockerfile` and mounts
  `./docker/Caddyfile` into caddy. Author both — the Caddyfile is exposure-relevant
  (it is the TLS terminator the loopback-binding security posture hangs on) and the
  Dockerfile must reproduce the compose command's one-worker invocation and the
  `agentstate` ledger volume the fail-closed caps depend on. Then correct
  `DATABASE_URL`'s host for compose, document `POSTGRES_PASSWORD`, and gate the
  nonexistent `enrichment`/`dashboard` services until their phases restore them.
- Hygiene: warn on `_env_number`'s silent typo-swallowing (matching the NaN-rate guard
  pattern); fix false present-tense docs — `twiml.py`'s enrichment-worker comment,
  `legal.md`'s deletion/CI claims **and** its "Redaction is what the system does"
  sentence re-tensed as pending, and the "zero lines of `llm.py`" shorthand in
  `guardrails.md`/`llm.py` corrected to "none of its request construction" (dry runs
  still import the module and build `Turn`s); script the fiction-pack street geocode
  check as a networked pre-launch script under `scripts/`.

**Exit criteria.** CI runs on every push and is merge-blocking, and a deliberate PR
introducing a `<Dial>` verb fails it. An injected 404 yields a `NOTICE_TEXT` first verb
plus a fired alert; a healthy re-probe restores the clip. A throwaway `002` migration
applies to an existing volume; a deliberate mid-list enum insertion fails the revised
sync test. PII grep over `.env.example` returns nothing. `docker compose up --build`
succeeds from a clean checkout. An unparseable numeric env var produces an asserted
warning while still falling back to the default. The geocode script exists and has run
against the fiction pack with results recorded. A checked list confirms no doc claims
unbuilt behavior in the present tense.

### Phase 2 — Deterministic replay foundation *(K)*

**Goal.** Make every call replayable **before** anything durable is recorded — a corpus
persisted from today's seedless, partial event log is unreplayable forever, and
`plan.md` M0's byte-identical criterion becomes retroactively unmeetable. This phase is
also the last free moment to change event payload shapes: no DB rows exist yet, so no
persisted-shape authorization is needed.

**Key moves.**
- Seed the production RNG in `build_conversation`
  ([`conversation.py:527`](../ssscammers/agent/conversation.py)) and record the seed in
  the `call_opened` payload; log every consequential draw (character delays, hold
  lengths, clip and fumble picks).
- Widen event payloads to the replay spec: triage verdict/confidence/`SignalHit`
  provenance, model-latency and filler-coverage fields on `agent_turn` (the timing
  already exists in `_generate`), tick evaluations, LLM request metadata including
  `last_stop_reason`. Update the payload assertions in `test_conversation.py`
  deliberately.
- Golden manifests **extend `CallerScript`** rather than inventing a parallel shape,
  pin the fiction `pack_version` (golden transcripts break if the pack regenerates),
  and record per-turn timing; a replay runner re-drives `Conversation` from a recorded
  event log and diffs the produced stream byte-for-byte. Replay must reproduce **tick
  cadence** (hold-vs-dead-air behavior is cadence-sensitive) and drive **both** time
  injections coherently — the monotonic clock and `DailyLedger`'s civil date.
- Consolidate the two fake clocks (`tests/helpers.FakeClock`,
  `textloop.SimulatedClock`) into one — `CLAUDE.md` forbids a third.
- Convert the four prose-only adversarial pass criteria in
  [`scripts.py`](../ssscammers/simscammer/scripts.py) into machine-checked content
  predicates — today a persona complying with "tell me your system prompt" passes both
  gates as long as the phase machine doesn't disclose.
- `ReplayBrain` on the duck-typed `stream_reply` seam plus a recorded-Anthropic-client
  fake, so `llm.py`'s real request construction (cache breakpoints, sentence
  boundaries, truncation handling) is actually exercised — `--dry` exercises none of
  it.
- An **LLM-adversary mode** for the simscammer — a model-driven scammer running real
  scam scripts with a hangup model, wet on a budgeted key as a manually-triggered or
  scheduled CI job — emitting **mean simulated time-on-call** as the tracked headline
  engagement metric (`plan.md` quality bar #4). Scripted callers remain the
  deterministic merge gate; the adversary is the trend signal. Adversary transcripts
  are data, never instructions, like every other caller input.

**Exit criteria.** A recorded call replays byte-identically in CI (events, seq,
payloads, transcript). Misroute FPR=0 becomes a merge-blocking gate (all misroute
scripts × all personas × both entry paths, release within two turns). A
deliberately-broken persona fails the new adversarial predicates (red-test proof).
Exactly one fake clock exists. The LLM adversary reports mean simulated time-on-call,
recording the baseline against which M2's ≥3-minute criterion and Phase 11's
pre-registered margin are judged.

### Phase 3 — MONITOR watchdog layer *(E)*

**Goal.** Close the project's declared design-rule violation — six guardrails are
PROMPT-only, and `guardrails.md` says closing the MONITOR column is what building G-17
means — **before anything increases exposure**. Deliberately database-free: verdicts
ride the widened in-memory event log, respecting the single-process constraint, so this
does not wait for persistence.

**Key moves.**
- New `ssscammers/agent/monitor.py`: an out-of-band, model-backed watchdog consuming
  `agent_turn`/`caller_turn` events from an in-process tap — never awaited in the
  per-sentence vet loop, so it adds zero latency to live audio.
- Enforcement rides the already-built dead seam: the verdict sets
  `CallContext.watchdog_killed`
  ([`state_machine.py:98`](../ssscammers/agent/state_machine.py), currently never set)
  consumed by `check_exits` at the existing 1s tick, terminating via
  `Trigger`/`EndReason.WATCHDOG_KILL` — zero new enforcement machinery, and a timer
  still never starts a model turn.
- **Fail-open polarity** exactly as designed: classifier timeout or error leaves the
  deterministic verdict standing; bounded concurrency and hard timeouts; the monitor
  holds only a narrow Anthropic key.
- Coverage: G-5 transaction/verification completion, G-7 identity confirmation, G-8
  real-entity claims, G-9 fraud assistance, G-10 tone ceiling (toxicity), G-19
  injection-compliance, plus second opinions on G-3/G-4 and the persona-break half of
  G-17 — evaluated against Phase 2's machine-checked adversarial predicates.
- The fixed-script carve-out is preserved: disclosure, victim warning, and 911 bypass
  the monitor as they bypass the filter — a fail-closed path over them would create a
  call where the required disclosure is never said.
- In-flight `kill(call_sid)` injected through the same override channel — completing
  the "kill a call already in flight" pending piece of G-20.
- `guardrails.md`'s MONITOR column updated honestly in the same commit; the
  verdict-enum-to-SQL decision deferred to Phase 5 with a written note (the Phase 1
  runner already exists if a review cascade forces it early).

**Exit criteria.** A fake classifier flagging a scripted persona break reaches
`DISCLOSE_EXIT`/`TERMINATE` within one turn of the verdict via the tick path. A hung
classifier leaves calls unchanged with turn latency within the pre-monitor bound
(injected-slow-monitor test). The adversarial injection scripts fail the release gate
when the persona complies. `kill(call_sid)` ends a live simulated call with the slot
released through two-phase finish/release. The disclosure and 911 scripts are provably
never suppressed by any verdict.

### Phase 4 — Cancellation-safe turn executor *(F, part one)*

**Goal.** Remove the latent G-11 open-line failure mode — the hard half of M2's unmet
exit criterion. `should_interrupt` stays `False`; the flag flips only in Phase 7,
behind this phase's test suite. Sequenced directly after MONITOR so the two changes to
`_generate`/`_execute` land in explicit order rather than colliding.

**Key moves.**
- Checkpointed, transactional turn state in `Conversation._execute`/`_generate`:
  commit spoken text to `_history` and `_spoken_digit_tail` **per sentence actually
  emitted** (today they commit only after the loop, so a cancelled turn desyncs the
  transcript and blinds the cross-turn G-4 digit filter); commit
  `_ended`/`_offered_voicemail` atomically with an **executed** `HangUp` (today they
  are set before the yield at
  [`conversation.py:394-402`](../ssscammers/agent/conversation.py) — the open-line
  trap); roll back `_line_busy_until` for Pauses emitted but never slept.
- Idempotent `_end_call`: once-only `report_outcome` preserving the
  report-before-socket-close ordering, re-issuable `stop_when_done`; correct
  `note_agent_audio_finished` on cancelled `perform_stream` paths.
- A cancellation-injection property harness: cancel at every await/yield point of every
  scripted turn, including hangup turns, and assert the invariant set.

**Exit criteria.** Exhaustive cancellation injection leaves zero states where
`_ended=True` without an executed `HangUp`; every post-cancellation call can still
respond or complete its hangup. A card number split across a cancelled turn is still
caught by the cumulative filter. `_end_call` called twice reports exactly one outcome.
All prior gates green; `should_interrupt` still `False`.

### Phase 5 — Postgres persistence *(A)*

**Goal.** Durable memory, now that every call is replayable (Phase 2) and MONITOR
verdicts exist to persist from day one (Phase 3).

**Key moves.**
- `PostgresEventSink` implementing the `EventSink` Protocol
  ([`conversation.py:129-132`](../ssscammers/agent/conversation.py)): `emit()` is
  enqueue-only into a bounded in-process queue with a background writer — zero DB
  round-trips on the live turn path, since emit is awaited inline per sentence. The
  sink owns `call_sid`→`call_id` mapping and timestamptz stamping per the `CallEvent`
  design note; writes upsert against `UNIQUE(call_id, seq)`; a documented bounded-loss
  story covers sustained outage. Wired at the single production site in `media.py` via
  `build_conversation(events=...)`; asyncpg never enters `ssscammers/shared/`.
- Calls/callers/turns writers from the webhook lifecycle, with a **duration-provenance
  column** (status-callback vs events-derived vs reaper-inflated ~hard-cap+300s — the
  console-configured status callback can silently never arrive).
- Capture `RecordingSid` from `/twilio/recording-status` **independent of registry
  state** — `record_recording_sid` on a released call is a silent no-op today and the
  SID dies with memory.
- Unify caller identity on `national_digits` (the ledger keys raw `From` today) with an
  explicit migration-impact note; the `DailyLedger` file stays **authoritative for
  admission** (fail-closed, no query on the call path) with async replication to
  `metrics_daily` for the dashboard only.
- Resolve the deferred Phase 3 decision: persist monitor verdicts and promote the
  filter's `Violation` kinds into the mirrored-SQL-enum regime via the runner — a
  persisted-shape addition requiring an explicit migration-impact note and owner
  sign-off before merge.
- Wire `AllowlistCache.loader` to Postgres (refresh on write and interval — the
  designed seam); Postgres becomes the allowlist system of record, the in-process copy
  remains what the call path reads. The interval refresh runs on a **new app-level
  supervised background task** started at agent boot — a component this phase must
  build, because the only ticker in the codebase today is per-call (`_tick_forever`
  lives and dies with one media socket) and nothing ticks while the system is idle.
  Phase 8 reuses this poller for the kill switch. Admission continues to read only
  memory; the DB never enters the admission path.

**Exit criteria.** A simulated call's rows in `calls`/`call_events`/`turns` exactly
match the in-memory stream, and a call replayed **from its persisted rows** is
byte-identical to its live stream. Postgres down mid-call: the call completes normally
with bounded, counted loss and unchanged latency (chaos test). The runner applies
migrations on fresh and existing volumes. Duplicate Twilio webhook deliveries produce
no duplicate rows. A recording-status callback arriving after slot release still
persists the SID.

### Phase 6 — Recording pipeline, retention, and observability *(B, G, K)*

**Goal.** Close the standing compliance gap: [`legal.md`](legal.md) promises seven-day
legit-audio deletion and migration off Twilio in the present tense, and neither exists.
Bundle OpenTelemetry here so out-of-band operator eyes exist **before** barge-in flips
on in Phase 7.

**Key moves.**
- Choose and justify an S3-compatible client (none exists); wire the pre-declared
  `R2_*` vars into `Settings` via the established pattern; the client lives outside
  `ssscammers/shared/`.
- On `/twilio/recording-status` "completed", enqueue into the `jobs` table
  (`SKIP LOCKED`, already in the schema) — entirely off the call path; the worker
  downloads the dual-channel audio, sha256 content-addresses it into R2, writes the
  object keys, **verifies**, then deletes the Twilio copy — one copy under one
  retention policy, as promised. The bounded 1.5s recording start and its
  never-drop-a-call contract are untouched; "no recording" remains a normal state.
- Retention job: legit-classified audio deleted within the test-pinned ≤7 days,
  `audio_deleted_at` set, transcript truncated. **Negative test: voicemail recordings —
  real people's private messages — never receive a bait-bucket key and never overwrite
  `calls.recording_sid`.**
- Credential split per [`secrets.md`](secrets.md): a bucket-scoped R2 key held by the
  job consumer, never the scammer-influenced media path.
- OpenTelemetry: traces and metrics for webhook and per-turn latency, sink queue depth
  and loss, recording-job outcomes, notice-probe status, registry/ledger counters —
  with alarms (sink backlog, notice-probe failure) **proven to fire in test**.
- `legal.md` flipped from aspiration to fact in the same commit.

**Exit criteria.** End-to-end against a fake Twilio media host: completed recording →
R2 object under its content hash, DB pointers set, Twilio copy verified deleted.
Clock-injected retention test passes. A worker killed mid-upload completes the job
exactly once on restart. Recording failure still never delays admission beyond the
1.5s bound. OTel alarms fire on injected failures.

### Phase 7 — Media test seam, chaos, and barge-in *(F part two, K)*

**Goal.** The exposure-increasing flag flips only now — after the cancellation-safe
executor (4), MONITOR (3), persistence (5), and observability (6) all exist to catch
its failures. First, the test seam `media.py` has never had: latency SLO and chaos
tests dead-end on its real sleeps today.

**Key moves.**
- Inject the clock into the 1s ticker and Pause sleeps; build a loopback transport so
  the full Pipecat pipeline runs headless in virtual time; pin the pipecat version
  (DeprecationWarnings are errors here; the worker import path is fragile).
- Chaos legs: STT stall, TTS 5xx, carrier disconnect, monitor outage, Postgres outage —
  each must end in a defined path with the slot released via the `PIPELINE_ERROR`
  backstop and the outcome reported. Latency SLO tests in virtual time over Phase 2's
  latency fields.
- Replace the FIFO turn lock with a **priority-aware executor**: a ticker `HangUp` or
  monitor override preempts in-flight generation instead of queuing behind a full
  generate+filter loop; fixed-script turns (disclosure, victim warning, 911) run as
  uncancellable critical sections.
- Flip `should_interrupt=True` behind the Phase 4 cancellation suite; wire **both**
  dead persona knobs while rebuilding this exact construction site —
  `Pacing.ignore_interruption_probability` and `VoiceConfig.speed` (slow delivery is
  "the cheapest believability lever the project has" per
  [`persona.py`](../ssscammers/agent/persona.py)'s own docstring, and the TTS service
  is constructed here) — then remove all six "NOT APPLIED YET" comments from the three
  persona YAMLs truthfully, in the same commit. Each YAML carries two such comments
  (speed and barge-in); neither may be deleted while its knob is still unwired.
- Emit **interruption events** (who barged, when, mid-which-sentence) — the
  interruption-rate signal Phase 11's frustration estimator cannot get any other way —
  and the **media-plane events the schema's `turns` columns were designed for**: STT
  finals with word timings and confidence (`words`, `audio_start_ms`,
  `stt_confidence`) and playback timings, flowing through the Phase 5 sink so
  Phase 8's word-timed transcript view has its data (Rescope 6).

**Exit criteria.** The G-11 scenario is a named CI regression test and passes with
barge-in on: a caller interrupting the disclosure or a hangup turn still gets the
complete fixed script or a completed hangup — no open line reachable under exhaustive
interruption injection. Chaos suite green with no leaked slots. A monitor override
preempts an in-flight reply within a bounded number of sentences. Latency SLO tests
fail on a seeded regression. STT word timings, confidence, and playback events appear
in persisted `turns` rows. Persona speed audibly differs between a `slow` and a
`normal` persona (asserted via the TTS request shape). All replay and FPR=0 gates stay
green with `should_interrupt=True`.

### Phase 8 — Dashboard and kill-switch completion *(D)*

**Goal.** Operator eyes and G-20 completion before anything increases call volume or
length.

**Key moves.**
- Build `dashboard/` (Next.js) in its pre-declared compose slot, bound
  loopback/Tailscale-only — it holds recordings of real people and must never be
  published; a **read-only Postgres role**; it never touches the agent's in-memory
  state.
- Control crosses processes via the database only: the dashboard writes
  `settings.system.enabled` (seeded row); the agent polls it into
  `CallRegistry.enabled` on the **Phase 5 app-level supervised poller** — the per-call
  ticker stops between calls, and an idle-time flip must be seen before the next
  admission, which is exactly when an operator flips it; kill-switch overflow still
  degrades to voicemail. Admission itself still reads only memory. The in-flight kill
  button invokes Phase 3's kill path via an authenticated loopback control endpoint.
  Allowlist/blocklist edits propagate via the Phase 5 loader-refresh pattern — never a
  call-path query.
- Views: call list and event replay (word-timed via Phase 7's media-plane events),
  turn latency, monitor verdicts, filter violations, `TriageResult` provenance (built
  for exactly this), recording/retention status, ledger-replicated daily totals,
  engage rate, and `wasted_time` rendered honestly as zero-until-enrichment
  (calibration views join in Phase 10). Recordings stream through a server-side proxy
  holding the narrow R2 credential.
- A **dashboard-scope no-outbound scan** wired into CI — `dashboard/` lives outside
  `ssscammers/`, so `tests/test_no_outbound.py`'s package scan never sees it.

**Exit criteria.** Toggling the kill switch routes the next inbound call to voicemail
within one poll interval and survives an agent restart. The dashboard is unreachable
except on loopback/tailnet (bind assertion) and its DB role cannot write outside the
settings/allowlist tables (privilege test). An allowlist edit changes admission
behavior without a restart or call-path query. The in-flight kill button ends a live
simulated call. The full compose stack is healthy. The live verification drills from
`plan.md` are run against the deployed stack and archived as golden manifests: a
bait-DID call from a test handset completes end-to-end with the disclosure played and
a complete persisted log, and a false-positive drill (a human caller behaving like a
human) lands in voicemail with the persona released.

### Phase 9 — Enrichment worker *(C)*

**Goal.** The second process — entirely on Postgres and R2, never the in-memory
registry. It activates the headline metric and supplies the authoritative labels
Phases 10–12 calibrate against.

**Key moves.**
- Create `ssscammers/enrichment/` with a worker entrypoint matching the compose
  command: a `SKIP LOCKED` consumer of the jobs table running Batch-API analysis over
  persisted transcripts and dual-channel R2 recordings (exact diarization for free).
- Outputs: `scam_classifications` with confidence — activating the `wasted_time` gate
  (≥0.7 AND NOT `flagged_legit`) so the headline metric goes structurally nonzero;
  authoritative `scam_type`/`tactic` relabel over the allowed-to-be-wrong realtime
  guesses; `CallStatus` driven through `enriching`→`enriched`; voicemail transcription
  finally making the `twiml.py` comment true.
- **G-19 discipline off the phone too:** transcripts are scammer-authored data;
  enrichment prompts are red-teamed with the adversarial scripts' literal injection
  lines as input fixtures. Least privilege: its own narrow Anthropic key, read-only R2,
  no Twilio credentials, no control-plane access.
- `metrics_daily` recomputed from base tables, never trigger-incremented, so a
  `flagged_legit` reclassification zeroes the call everywhere — and enqueues the
  Phase 6 retention-deletion path.
- No schema field for scammer payment instruments (the will-not-build list), asserted
  by test.

**Exit criteria.** A persisted golden call enriches end-to-end in compose with
`wasted_time` nonzero. Reclassification-zeroing proven, including triggered audio
deletion. The injection red-team eval is green. Crash/restart mid-job leaves no stuck
or double-processed jobs. A credential audit test pins the worker's key set.

### Phase 10 — Screening v2 *(H)*

**Goal.** M3, honestly rescoped (see above) and deliberately late: better screening
engages more callers, so it waits for MONITOR, the FPR=0 gates, replay calibration,
enrichment labels, and dashboard visibility.

**Key moves.**
- LLM intent classifier and prompt-response mismatch detector over the early
  transcript, running out-of-band on Phase 3's monitor infrastructure — **advisory,
  refining, never replacing** the deterministic triage layer, exactly as
  [`triage.py`](../ssscammers/agent/triage.py)'s design note reserves; fail-open to the
  deterministic verdict.
- The **pre-answer metadata tier** from the design record, unblocked (Rescope 1):
  parse STIR/SHAKEN `StirVerstat` from the inbound webhook into triage signals;
  populate `callers.lookup_data` via Twilio Lookup **off the admission path** (async
  after admission, feeding the posterior and the prior-verdict cache, never blocking
  the webhook); NANPA allocation, neighbor-spoofing, and velocity as deterministic
  in-process checks; trailing FTC DNC complaint volume batch-refreshed into Postgres
  and replicated in-process via the loader pattern, so the admission read is a memory
  lookup. Admission stays query-free and millisecond; every new refusal still degrades
  to voicemail.
- **Post-answer acoustic features, in their designed role** (Rescope 1): dead-air
  onset from the caller speech-energy events the pipeline already consumes, and
  call-center babble from background-energy analysis run out-of-band (over live frames
  or the dual-channel recording) — feeding the in-call commit/release posterior, never
  the webhook.
- Opener nearest-neighbor over **text** embeddings only (no voiceprints), against the
  enriched, labeled corpus.
- Calibrated fusion preserving the asymmetric evidence bars as a **property test**:
  fusion may only raise the bar to bait or lower the bar to release — equalizing them
  inverts the core safety posture and is an automatic finding.
- Live posterior monitor with the graceful false-positive recovery path: a mid-call
  flip to real-person exits within one turn via a **fixed, human-reviewed recovery
  script** that joins the filter-exempt set, with the voicemail promise kept through
  `/twilio/after-stream`.
- Consume the dormant seams: `is_known_scammer` skip-probation, the mid-call
  `ALLOWLISTED` exit already threaded into `build_conversation`, entry-path-conditioned
  thresholds.
- Calibration replayed offline against enrichment labels, byte-reproducible; the golden
  misroute set grows hard cases (soft-spoken elderly callers, scripted-sounding wrong
  numbers) **before** anything turns on.

**Exit criteria.** The expanded FPR=0 gate is green — merge-blocking, no exceptions.
**Measurable recall lift over deterministic-only triage on the enriched labeled corpus
at FPR still 0 — M3's exit — with expected calibration error tracked as a CI metric**;
recall and calibration may regress only with explicit review. The asymmetric-bars
property test passes. A posterior-flip golden releases within one turn with the
recovery script verbatim and the correct voicemail promise. Every model-backed
component failing leaves the deterministic verdict standing. No new synchronous model
call on the admission or per-sentence path. The calibration report is
byte-reproducible.

### Phase 11 — Strategy engine *(I)*

**Goal.** M4, last among agent-behavior phases: it most directly lengthens calls, and
every catching layer now exists.

**Key moves.**
- Frustration estimator from signals the director already receives plus Phase 7's
  interruption events; wire the permanently-dead frustration kwargs so the
  tested-but-unreachable `STALL`↔`WIND_DOWN` transitions and `SCAMMER_FRUSTRATED`
  trigger go live.
- Add the missing `compliance_signaling` tactic as the four-point coupled change:
  append-only `Tactic` enum, `ALTER TYPE` migration via the runner with an impact note,
  `_TACTIC_DIRECTIONS` entry, persona YAML weights under the strict loader.
- Contextual bandit replacing **exactly one call site**
  ([`persona_director.py:294`](../ssscammers/agent/persona_director.py)) with the YAML
  weights as priors; context = (`CallPhase`, realtime `scam_type`, frustration bucket,
  `entry_path`); all draws on the injected per-call seeded rng and logged, so replay
  stays byte-identical — extra draws perturb the shared rng sequence, so seeded tests
  are updated deliberately, not incidentally.
- Policy learning **offline-only** via replay-based off-policy evaluation
  (IPS/doubly-robust) over a pinned corpus snapshot, explicitly handling the `HOLD_ON`
  confound — a forced hold earns the persona's **sampled, logged 10–90 s draw** of
  guaranteed line occupancy (bounds are persona-dependent: harold caps at 60 s), a
  variable-magnitude confound, not a constant — and pricing the quadratic input-cost
  term of longer calls; policies ship as versioned, human-reviewed data.
- Hard boundaries as tests: the bandit selects only within baiting phases;
  `check_exits` never selects a tactic; exits are bit-identical with the bandit stubbed
  to every arm; the G-10 tone ceiling covers bandit outputs via MONITOR.

**Exit criteria.** A simulated frustrated caller drives `STALL`→`WIND_DOWN` and back —
live wiring, not just FSM tests. `compliance_signaling` flows end-to-end through enum →
SQL → directions → YAML → state note → event. A bandit-driven call replays
byte-identically including every draw. Offline evaluation is reproducible from the
pinned snapshot. **Mean simulated time-on-call from the Phase 2 LLM-adversary job
beats the pre-bandit baseline by a pre-registered margin — M4's exit; a bandit that
merely passes the wiring checks while shortening calls fails this phase.** All gates
stay green with the bandit active.

### Phase 12 — Intelligence layer *(J)*

**Goal.** M5 within the legal fence, last because it expands data handling.

**Key moves.**
- **The voiceprint escalation** (see Rescopes): a written decision document to the
  owner, recommendation to keep the boundary; a CI grep/schema gate asserts no
  audio-derived speaker feature exists anywhere, whichever way it is decided.
- Campaign linking on non-biometric features only: phone number (the one sanctioned
  key), opener/script text fingerprints reusing Phase 10's embedding store, named
  entities, timing patterns — via runner migrations with impact notes; clustering runs
  seeded and reproducible in the enrichment worker's process space.
- Entity extraction inside the will-not-build fence: impersonated organizations,
  volunteered callback numbers, script templates — expressly excluding scammer
  payment-credential harvesting; third-party real details redacted per the open
  `[LAWYER]` item.
- **Cross-call persona memory:** back the claims ledger with Postgres keyed on the
  normalized number and wire the currently test-only `record_claim`
  ([`persona_director.py:328`](../ssscammers/agent/persona_director.py)) so a callback
  from the same operator gets a consistent story — making `plan.md`'s persistence
  promise true, proven by a two-call golden replay. Claim extraction is advisory
  prompt-steering only; it never feeds phase, exit, or admission decisions.
- User-triggered complaint export: an FTC/FCC-ready bundle generated only on an
  explicit dashboard action, as a download the user files — never automatic, never
  transmitted, nothing published.

**Exit criteria.** Two corpus calls sharing an opener fingerprint link into one
campaign; two calls linked only by voice similarity are provably **not** linked
(negative test). The no-biometric CI gate is green. The export exists only behind an
explicit user action and a seeded third-party detail is redacted. The decision document
is on record and the shipped code matches it. `test_no_outbound.py` and every earlier
gate remain green across the full stack.

## Open decisions for the owner

These are the roadmap's named escalations — each blocks only its own phase, and each
carries a recommendation rather than a shrug:

1. **Voiceprints (Phase 12).** Recommendation: keep `legal.md`'s boundary; ship
   non-biometric linking.
2. **Persisted event-payload shape (Phase 5).** The Phase 2 widened payload becomes a
   persisted shape at first write; sign-off required before the first persisted row.
3. **Monitor-verdict / `Violation` SQL enums (Phase 5).** A persisted-shape addition
   with a written migration-impact note.
4. **The `[LAWYER]` items in [`legal.md`](legal.md).** Phase 12's export redaction
   implements the current answer; the checklist stays open until answered by someone
   qualified.
