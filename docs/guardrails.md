# Guardrails

Each guardrail names the layer that enforces it and where that enforcement actually
lives. The layers mean specific things:

- **CODE** — impossible by construction, and a test proves it.
- **MONITOR** — an out-of-band check that can override or stop the agent.
- **PROMPT** — an instruction in the system prompt.

**The design rule is that nothing safety-critical is PROMPT-only**, because a model asked
nicely not to do something is a mitigation, not a control, and the callers here are
adversarial humans who will spend twenty minutes trying to talk it out of the instruction.

**Today the rule is not fully met, and no row below should be read as if it were.** There
is no MONITOR mechanism in the running system: the only one designed is G-17's model-backed
watchdog, and G-17 is partial — the deterministic half is in the output filter, the
model-backed half is pending. So wherever MONITOR appears it is *planned*, and:

- Every guardrail tabled `PROMPT + MONITOR` — G-5, G-7, G-8, G-9, G-10, G-19 — is
  **PROMPT-only in the running system**.
- G-3 and G-4 are **CODE today**, which is the half that matters: the pre-TTS filter is
  deterministic and blocks unilaterally. Their MONITOR half is planned, not built, so there
  is no out-of-band second opinion on either.

The guardrails that protect a real caller are CODE: G-1, G-11, G-12, G-13, G-14, G-16 are
built and none depends on the watchdog. G-2 is CODE but **partial** — see its row for the
one hole (an unfetchable `NOTICE_AUDIO_URL` makes Twilio skip the `<Play>` and record with
no notice), which is a real gap, not a formality. Closing the MONITOR column is what
building G-17 means.

Status is honest: **built** means implemented and tested today; **pending** means the
design is settled but the code is not written.

| # | Guardrail | Layer | Status | Where |
|---|---|---|---|---|
| G-1 | No outbound calls or texts, ever | CODE | **built** | Twilio subaccount with outbound disabled; [`test_no_outbound.py`](../tests/test_no_outbound.py) greps the package for call/message creation and for `<Dial>`, and asserts the one REST endpoint used takes a call SID as a path segment |
| G-2 | Recorded-line notice plays before any agent speech | CODE | partial | [`twiml.py`](../ssscammers/agent/twiml.py) — `engage()` cannot be constructed without a notice and emits it as the first verb; recording starts *before* it plays, so the notice is inside the audio. The voicemail documents carry the notice in their own prompt, since those paths never call `engage()`. **Gap:** a `NOTICE_AUDIO_URL` that is well-formed but unfetchable makes Twilio log the failed `<Play>` and continue to the next verb — boot-time validation catches a malformed URL, nothing catches a 404 |
| G-3 | No real personal data is ever spoken | CODE (+ MONITOR planned) | **built** as CODE | [`output_filter.py`](../ssscammers/shared/output_filter.py) denylist; [`fiction.py`](../ssscammers/shared/fiction.py) is the only source of "personal" detail |
| G-4 | No usable financial instrument is ever spoken | CODE (+ MONITOR planned) | **built** as CODE | Luhn / ABA / SSN-range checks in [`validators.py`](../ssscammers/shared/validators.py), enforced pre-TTS |
| G-5 | Never completes a transaction or verification | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md); model-side eval pending |
| G-6 | No device access, no real OTP ever read | CODE | **built** | There is no such tool. Inbound SMS is unhandled, so no real code exists to read |
| G-7 | Never confirms an identity a caller offers | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md) |
| G-8 | Never claims to be a real company, agency, or person | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md); fiction identities only |
| G-9 | Never helps commit fraud against anyone else | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md) |
| G-10 | Tone ceiling: mildly annoying, never cruel | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md); toxicity check pending |
| G-11 | A real caller is released within one turn | CODE | **built** | [`state_machine.py`](../ssscammers/agent/state_machine.py) safety exits; fixed `DISCLOSURE_SCRIPT` |
| G-12 | Real emergencies get the 911 redirect, immediately | CODE | **built** | `EMERGENCY_EXIT`; fixed `EMERGENCY_SCRIPT`; scripted fake emergencies excluded |
| G-13 | Threats end the call without escalation | CODE | **built** | `TERMINATE` with no parting utterance |
| G-14 | Hard call cap, enforced by timer not by model | CODE | **built** | `hard_cap_seconds` in the state machine |
| G-15 | Concurrency, daily minutes, and spend caps | CODE | **built** | Concurrency enforced by [`registry.py`](../ssscammers/agent/registry.py); daily minutes, spend, and repeat-caller counters by [`daily_ledger.py`](../ssscammers/agent/daily_ledger.py), persisted to disk with atomic writes (the compose file maps its state directory to a named volume). Every one of them routes overflow to voicemail, never a rejection |
| G-16 | Dead-air hangup | CODE | **built** | `dead_air_seconds` |
| G-17 | Persona-break watchdog can stop the call | MONITOR | partial | Deterministic half built into the filter; the model-backed half is pending |
| G-18 | Truthful AI disclosure only on code-gated triggers | PROMPT + CODE | **built** | Persona-break patterns blocked except in the exit phases |
| G-19 | Caller speech is data, never instructions | PROMPT + MONITOR | partial | [`core_rules.md`](../playbooks/core_rules.md); injection scripts in the eval set |
| G-20 | Kill switch for any live call and the whole system | CODE | partial | `CallRegistry.enabled` stops new calls in-process (they get voicemail, never a dropped call); persistence via `settings.system.enabled` and dashboard control pending, as is killing a call already in flight |

## Two failure modes, deliberately opposite

The pre-TTS filter **fails closed**: if the scan itself raises, the utterance is
replaced with an in-character fumble rather than spoken unchecked. A crash must never
become a leak.

The model-backed watchdog is **designed to fail open** — if the classifier errors or times
out, the deterministic verdict stands alone, because a flaky API must never stall a live
call. It is not built yet (G-17), so today the deterministic verdict is the only verdict.

## Why over-blocking is cheap but not free

A blocked utterance is replaced with "hold on dear, I've lost my place" — which is a
perfectly good stalling turn, so a false positive costs nothing conversationally.

It is not free in one specific way, and it took a failing test to notice: the persona
has to be able to read *its own* fiction-pack card aloud, because fumbling digits is
the strongest tactic in the playbook. A filter that blocked that would quietly gut the
project. The filter is therefore told the active identity's numbers and strips them
before checking, and there is a test asserting each persona can recite its own fact
sheet without being gagged.

Two related corrections came out of the same work, and are worth recording because
both look reasonable until you check them:

- **Reserved test card numbers are banned from the fiction pack.** Numbers like
  `4111 1111 1111 1111` are Luhn-*valid* by design, so G-4 blocks them. Pack cards are
  generated to fail the checksum instead — unusable by a processor and speakable by
  the persona.
- **Nine-digit values are checked both ways round.** A never-issued SSN can also be a
  checksum-valid ABA routing number (`900-12-3456` is one). The generator now rejects
  any value that is dangerous under either reading.

## What the adversarial review changed

Both reviews of the ingress and pipeline work found real defects in it, and four are
worth recording because each looked correct until it was executed:

- **A hold longer than the dead-air window made the persona hang up on itself.** Holds
  are drawn from 10–90s and the dead-air window is 60s, so roughly a third of holds —
  and every `HOLD_ON` tactic — terminated the call mid-stall with `DEAD_AIR`. The test
  that claimed otherwise called `note_agent_audio_finished()` by hand at a point the
  transport never does. Dead air is now measured against a line-busy deadline set when
  the pause is *emitted*, and the test drives the tick loop instead of faking it.
- **The cumulative output filter was per-turn.** Checking a reply against itself closes
  the two-sentence card split; a scammer asking "and the rest?" splits the same number
  across two *turns*, which the check could not see. A bounded digit tail now carries
  across the turn boundary, so a run only continues if no ordinary word interrupted it.
- **Every model request would have been rejected.** The neutral greeting is an assistant
  turn, and the Messages API requires the first message to be `user`. Sent as-is, every
  turn 400s — and the fail-soft generation path converts that into a stalling line, so
  the persona would have spoken nothing but fumbles for an entire call while the logs
  showed a "handled" error. Leading assistant turns are now dropped from the request.
- **Three of the daily caps were declared and enforced nowhere.** `DAILY_MINUTES_CAP`,
  `DAILY_SPEND_CAP_USD` and `REPEAT_CALLER_DAILY_CAP` were read from the environment into
  `Settings` and never consulted. G-14 bounds one call and G-15's concurrency half bounds
  one moment; nothing bounded a day, and five slots recycling for twenty-four hours is
  about a hundred and twenty call-hours reachable by an ordinary redialling robocaller.
  Now enforced at admission by `daily_ledger.py`. Four things went wrong in the first
  attempt at it, all of them failing *open* on a cost control:
  - **Unwritable state failed open.** A read-only mount or a full disk leaves reads
    working and freezes the counters, so every cap switched off silently. Read failure and
    write failure are now latched separately — a successful read must not clear a failed
    write — and `cap_reason` probes a write while degraded so a recovered disk un-latches.
  - **Well-formed JSON with a wrong-typed value raised out of the webhook.** `{"seconds":
    null}` gave `TypeError` and a 500 on `/twilio/voice`, which Twilio does not retry, so
    the caller was *dropped* — the one outcome the voicemail rule forbids. Values are now
    type-checked on load (numeric strings coerced, because hand-editing this file is the
    expected repair) and a bad shape fails closed like a corrupt one.
  - **The documented repair did not work.** The error log says "repaired or removed"; the
    fail-closed flag was set and never cleared, so following those instructions left every
    caller going to voicemail until a restart.
  - **The write was not atomic.** `write_text` truncates in place, and the deployment
    SIGKILLs this process after a grace period while `release()` writes here. A torn write
    reads back as corrupt, which fails closed — turning an ordinary restart into an outage.
    Now a temp file plus `os.replace`.
- **The reaper made the calls it exists for invisible to the caps.** `_reap` dropped stale
  calls with a bare `del`, so a call whose Twilio status callback never arrived — after up
  to ninety-five billable minutes — banked nothing. A run of callback failures would have
  blinded the daily caps permanently. Reaping and releasing now share one accounting path.
- **A 360-minute cap was really an 810-minute cap.** Minutes are only banked at release, so
  admission saw finished calls only: five calls admitted at minute 359 each run to the
  ninety-minute ceiling. In-flight calls are now charged pessimistically at that ceiling,
  which bounds the day at the cap plus one call rather than the cap plus `max_concurrent`.
- **The prompt-cache breakpoint was one block too late, making long calls dearer.** The
  state note is rebuilt every turn and only ever attaches to the newest turn, so marking a
  block that contained it stored a prefix no later request reproduces: the divergence
  landed on the *first* transcript message, nothing was ever read, and every turn still
  paid the 1.25x cache-write premium for a guaranteed miss — strictly worse than not
  caching. The marked block now holds only what the caller said, with the note in an
  unmarked block after it. The lesson generalises: a cache breakpoint must sit at the end
  of the *shared* prefix, never at the end of the prompt.
- **A cost measurement that bypassed the code it measured.** The 67%-saving figure quoted
  for the change above was produced by a script that hand-built its message list and
  hand-placed the breakpoint instead of calling `_build_messages`, so it exercised the
  no-state-note path — which production never takes — and reported a saving the shipped
  code could not deliver. A measurement that skips the interaction under test verifies
  nothing. Prefix stability across consecutive turns is now a test.
- **Moving to Sonnet 5 re-opened the same wound at the other end of the list.** An
  assistant turn in *last* position is an assistant prefill, which Sonnet 5 rejects and
  the previous model accepted — the identical silent-fumble failure as the greeting bug,
  reachable the moment anything upstream plans a model turn without new caller speech.
  Both ends are now stripped by one helper. Two guards currently make it unreachable
  (`respond()` is called only on non-empty transcribed text, and `tick()` cannot plan a
  model turn), so this is defence for the day one of them moves.
- **A model switch cannot be verified by the dry harness.** `textloop --dry` sets
  `brain=None`, so none of `agent/llm.py`'s request construction runs — no request is
  built or sent, though dry runs still import the module and build `Turn`s — and the
  harness's only assertions are state-machine properties that are model-independent by
  construction. A review claimed the switch was verified on that basis. Request-shape
  changes are invisible until they are live: run `textloop --script <name>` *without*
  `--dry`, against a real key.
- **A truncated reply was indistinguishable from a clean one.** On `stop_reason:
  "max_tokens"` the stream simply ends, the residual buffer is flushed with no sentence
  terminator, and the fragment is spoken, recorded as the persona's turn, and fed back to
  the model next turn — with `failure: null` in the event log. Truncation is now labelled
  `failure: "truncated"`. Measured headroom: the wordiest persona peaks near 273 output
  tokens against a 400 ceiling, so this is a margin being watched, not a live defect.
- **`DISCLOSE_EXIT` covers two scripts that promise opposite things.** The disclosure
  promises a voicemail; the victim warning says to hang up and ring your bank. Inferring
  the promise from the phase offered a recorded-message beep to precisely the caller who
  had just been told to put the phone down. The promise is now reported by the code that
  chose the script.

## Two ways the pre-TTS filter could have been fooled

Both were found by writing the streaming path, and both are worth recording because the
first looks like an optimisation and the second looks like a convenience.

**Sentence-at-a-time streaming almost opened a hole in G-4.** Audio starts on sentence
one while sentence two is still being generated, which is the largest perceived-latency
win available. Filtering each sentence *as it leaves* is the obvious implementation and
it is wrong: `"the number is 4539 1488"` and `"0343 6467"` each carry a harmless
eight-digit run, and together they read out a Luhn-valid card. The filter only blocks a
run that is exactly a card length, so neither half trips it. Sentences are therefore
checked against the whole reply so far, and a block ends the turn rather than moving on
to the next sentence — see [`conversation.py`](../ssscammers/agent/conversation.py) and
the test that asserts the evasion is real before asserting it is caught.

**Fixed scripts are not run through the filter.** They are constants a human reviewed,
and the filter fails closed: a scan that raised while checking the disclosure would
replace it with a fumble line, which is the exact failure G-11 exists to prevent. The
`EMERGENCY_SCRIPT` makes this concrete — it contains "9 1 1", a digit run, and it is the
one line that must never be swallowed.

## Verification status

Machine-checked in CI today: fiction-pack invariants across hundreds of generated
identities, the filter against spoken-word digit dictation, the state machine's safety
exits from every phase, every canned misroute script released within two turns (via
the disclosure or the emergency redirect, whichever the script demands), the
cross-sentence filter evasion above, and the ingress routing — signature
validation, blocklist, allowlist, overflow, kill switch, and the voicemail a released
caller is promised.

Not yet verified, and known: street names are drawn from a curated invented list but
are **not** geocode-checked against open street data. That check needs network access
and belongs in a pre-launch step, not in the unit suite. Until it runs, treat the
addresses as "almost certainly fictional" rather than "proven fictional".
