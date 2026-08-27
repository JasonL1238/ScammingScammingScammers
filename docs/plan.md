# Scam-Baiting Voice Platform — Build Plan

> **Status: design record, partly superseded.** This is the plan the project was designed
> against, kept for its decisions, quality bar, and milestone exit criteria. Three sections
> no longer describe the code: **D3** (media stack), **Architecture** (the SIP/LiveKit
> diagram and the TypeScript control plane), and **Repo layout**. What was actually built is
> a single Python package on Twilio Voice + Pipecat + FastAPI, with no TypeScript control
> plane and no LiveKit — see [`../README.md`](../README.md) for the layout that exists and
> the design-name mapping.
>
> The remaining work is sequenced in [`roadmap.md`](roadmap.md), which is the active
> execution plan; this document stays the design record it draws on. The roadmap's
> "Honest rescopes and escalations" section supersedes more of this document than the
> notes below do — admission-time acoustic screening, M4's script-stage bandit context,
> M5's speaker embeddings, the two-tier LLM router, wallet-address intelligence, and
> the canonical log's scope (decision-layer replay; audio ground truth lives in the
> recording) are all rescoped or escalated there. Read it before working any M3–M5
> item or the quality-bar section.
>
> Milestones M0–M5 still name the shape of the work, with **two** criteria superseded in
> this document's own notes.
>
> **M0's replay exit** (`*Exit: the replay harness reproduces a recorded call
> byte-identically.*`) and the same wording in quality-bar item 2 ("a recorded call with
> byte-identical inputs") are **superseded**, and read here as a **synthesized** call.
> These two lines are the ancestor of the criterion the owner amended on 2026-08-26 —
> the amendment itself was to roadmap Phase 2's exit criterion, which is the only line
> the owner was asked about; carrying it back here is this document's own inference, not
> a decision made about `plan.md`. What shipped in roadmap Phase 2 is byte-identical replay of an
> *authored* corpus: six manifests whose model side is a canned recording and whose
> per-turn timing is a rule, not captured data. Capture-and-replay of a real call is
> roadmap Phase 5's, with its own exit criterion there. Read those two lines as
> "synthesized" until Phase 5 lands. (Quality-bar item 3's `goldens/legit/` corpus is a
> *different* thing and is not affected — it is consented real audio for false-positive
> measurement, already rescoped to decision-layer replay by the roadmap's Rescope 6, and
> its decision-layer equivalent shipped as the misroute FPR=0 gate.)
>
> **M2's barge-in**
> is deliberately disabled today (`should_interrupt=False` in
> [`media.py`](../ssscammers/agent/media.py)). Re-enabling it without first making the turn
> executor cancellation-safe reproduces a G-11 failure — Pipecat cancels the task running a
> turn, so a caller who talks over the disclosure has their hangup cancelled and is left on
> an open line, never released. The full reason is in `media.py` beside the flag; read it
> before working that criterion.

## Context

Goal: inbound phone numbers that scammers can reach. Calls are screened; if a caller is
judged to be a scammer, a realtime AI voice agent answers and keeps them talking as long
as possible. Everything learned from the call — transcript, extracted entities, voice
fingerprint — feeds back into the detector.

Per `CLAUDE.md` Rule 0, this plan optimizes for engineering quality and ignores build
cost. Where a cheap option and a correct option diverge, the correct option is specified.

## Decisions taken (override any of these and the plan changes)

| # | Decision | Default chosen | Why |
|---|---|---|---|
| D1 | How real numbers connect | **Conditional call-forwarding to platform DIDs, plus separate bait DIDs.** Real numbers stay with your carrier; `*61/*71`-style busy/no-answer/unreachable forwarding sends unanswered calls to us. Bait DIDs are never given to a human and get seeded onto lead lists. | No porting risk, no outage exposure on your primary line, and bait DIDs give a near-zero-false-positive training corpus for free. |
| D2 | False-positive posture | **Split policy.** Real lines: engage only on strong evidence, abstain to voicemail. Bait DIDs: engage immediately, no screening. | Trapping one real caller (doctor, school, delivery) costs far more than missing one scammer. |
| D3 | Media stack | **Self-hosted LiveKit SIP media plane, Python agent runtime, TypeScript control plane.** | Raw audio access is a hard requirement for the acoustic detector and speaker fingerprinting; SIP keeps carriers swappable. |
| D4 | Recording & consent | **Universal recording disclosure at answer.** | Caller ID is spoofable, so the caller's state is unknowable; all-party-consent states make silent recording a real liability. Universal disclosure is the only defensible default — and it's a free signal, since some dialers drop on it. |
| D5 | Jurisdiction | Assumed **US numbers**. | Signal sources (STIR/SHAKEN, NANPA, FTC complaint data) are US-specific. |

## Design constraints that shape the architecture

These are not commentary — each one removes options from the design.

- **Inbound only. The system never places a call.** No callback, no flooding, no
  retaliation. Outbound is where TCPA and harassment exposure lives, and it would break
  the honeypot's cover anyway.
- **Recording disclosure on every answered call** (D4).
- **Personas never impersonate a real person.** The synthetic identity vault holds only
  values validated as non-real (Luhn-invalid card numbers, non-issuable SSN ranges,
  non-resolving addresses), generated deterministically from a persona seed.
- **Nothing the agent says may touch a real system.** No real PII, no reading back OTPs,
  no installing software, no navigating to URLs, no DTMF to a bridged party.
- **Retention TTL and user-initiated deletion.** Recordings of identifiable people are
  never published.

## Architecture

```
 PSTN ──SIP──▶ Carrier trunk ──▶ LiveKit SIP ──▶ Media plane (raw PCM, dual channel)
                     │                                │
                     ▼                                ▼
              Pre-answer signals              Post-answer features
              (Lookup, SHAKEN, NANPA,        (dead-air, babble, opener
               complaints, allowlist)          embedding, LLM intent)
                     │                                │
                     └──────────▶ Fusion + calibration ◀───┘
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                   RING THROUGH     VOICEMAIL        ENGAGE
                                   (abstain band)       │
                                                        ▼
                                            Realtime agent (STT→LLM→TTS)
                                            + strategy engine + guardrails
                                                        │
                                                        ▼
                                            Intelligence (fingerprint,
                                            entities, campaign clustering)
```

Every stage writes to one **canonical event log** per call — see Quality Bar.

### 1. Ingress

- `CarrierAdapter` interface: provision, configure trunk, fetch lookup data, read SIP
  `Identity`/`verstat` headers, teardown. **Two implementations from day one**
  (Telnyx + Twilio) so the abstraction is exercised, not aspirational.
- LiveKit SIP terminates the trunk and exposes bidirectional raw audio. Dual-channel
  recording from the start: our audio and theirs on separate channels, which makes
  diarization exact rather than inferred.
- Line classes: `PERSONAL` (forwarded, D1) and `BAIT`. Line class sets the detector's
  prior — this is the single highest-value feature in the whole system.

### 2. Screening

**Pre-answer signals** (deterministic, <100 ms, free to legit callers):

- Allowlist match against synced contacts → immediate ring-through, no further scoring.
- Prior-verdict cache keyed by E.164 with TTL.
- Carrier lookup: line type (VoIP / mobile / landline), CNAM, SIM-swap recency.
- **STIR/SHAKEN attestation** from the SIP `Identity` header. Attestation C or a failed
  verification is a strong spoof indicator.
- **NANPA allocation check**: is the calling number in an unallocated or unassigned
  block? Near-conclusive spoof tell.
- **Neighbor-spoofing heuristic**: caller NPA-NXX matches the callee's with a random
  last four.
- **FTC DNC complaint feed** (public daily dataset): trailing-30-day complaint volume
  for the number and its block.
- Velocity: burst of calls from adjacent numbers in a short window.

**Post-answer features** — the real differentiator. After the disclosure, the agent asks
one unusual screening question ("who are you calling for?"). Then:

- **Dead-air onset**: time from end of our prompt to first caller speech energy.
  Strongly bimodal — humans land at 200–600 ms, predictive-dialer bridges at 1.5–3 s,
  often with an audible click.
- **Call-center babble**: background speech-like energy during non-speech segments.
- **Scripted-opener nearest-neighbor**: embed the caller's first utterance, ANN-search a
  growing pgvector index of confirmed scam openers. Precision compounds as the corpus
  grows — this is the system's main self-improving loop.
- **Prompt-response mismatch**: a scripted opener ignores the screening question and
  launches the pitch. Semantic non-sequitur detection.
- **LLM intent classification** over the first ~15 s of streaming transcript against a
  scam taxonomy (IRS/SSA, tech support, bank fraud dept, auto warranty, crypto recovery,
  grandparent, package delivery).

**Fusion.** Each signal emits a *calibrated* likelihood ratio, not an ad-hoc score.
Posterior log-odds = prior log-odds (set by line class) + Σ log LR. Naive independence is
wrong here — SHAKEN attestation, VoIP line type, and neighbor-spoofing are correlated —
so correlated signals are grouped into blocks, and a small logistic regression with
isotonic calibration is fit per block on the golden set. Calibration quality is itself a
CI-tracked metric (expected calibration error).

**Decision policy.** An explicit cost matrix with `C(false positive) >> C(false negative)`
yields two thresholds and three actions:

- below `τ_low` → **ring through**
- between → **voicemail** — an explicit *abstain* band, never the bot
- above `τ_high` → **engage**

`BAIT` lines skip straight to engage.

### 3. Engagement

Cascaded pipeline (streaming STT → LLM → streaming TTS) rather than speech-to-speech,
because the transcript must stay in the loop for the live posterior monitor, the strategy
engine, and the egress guardrail.

**Latency budget** — p50 < 500 ms, p95 < 900 ms from end-of-caller-speech to first byte
of our audio:

| Stage | Budget |
|---|---|
| Semantic turn detection | ~50 ms |
| STT final after endpoint | ~150 ms |
| LLM first token (two-tier router: fast model on the common path, larger model escalated only for complex turns) | 200–400 ms |
| TTS first chunk | 100–150 ms |

**Filler injector**: if no audio is ready by 350 ms, emit a persona-appropriate filler
("uh…", "hold on now, let me find my glasses"). This is what makes the agent read as
human, and it is cheap.

**Barge-in**: caller speech onset >300 ms during our playback stops TTS and flushes. The
partially-delivered utterance is truncated *in the LLM's context to what the caller
actually heard* — not what was generated. Getting this wrong is the most common way voice
agents become incoherent.

**Persona engine**: personas are data (YAML) — voice, speaking rate, disfluency profile,
backstory, synthetic identity vault reference. Persona state persists across calls, so a
callback from the same operator gets a consistent story.

**Strategy engine.** The call is modeled as maximizing time-on-call subject to not being
hung up on.

- State: elapsed time, script stage (opener → hook → payload → payment), estimated
  frustration, tactic history.
- Frustration estimator: interruption rate, speaking-rate delta, pitch/energy delta,
  explicit hangup threats.
- Tactic library: ask for repetition, mishear a digit, go get a pen, dog interruption,
  gentle premise question, side-story tangent, compliance-signaling ("okay okay, I'm
  doing it now").
- Controller: contextual bandit (Thompson sampling) over tactics conditioned on script
  stage × frustration bucket. Reward = seconds of call attributable to the tactic, fit
  offline from replayed logs.
- Key behavior: when frustration is high, switch to compliance-signaling to reset the
  clock rather than stalling harder. Stalling into a frustrated scammer ends calls.

**Guardrails, enforced outside the LLM** (a prompt is not a control):

- **Egress filter** on every string before TTS: NER + regex denylist covering real names,
  addresses, card/SSN/account patterns, and the user's own numbers and emails. **Fails
  closed** — a trip substitutes a stall phrase.
- Synthetic-identity-vault-only for any PII the persona emits.
- No outbound dial, no accepting a transfer to an outside number, no DTMF to a bridged
  party.
- Hard per-call duration cap, per-number daily minute cap, global concurrency cap.
- Kill-phrase detection (caller claims law enforcement, or a real emergency) → graceful
  exit plus operator alert.
- **Live posterior monitor**: the scam probability keeps updating during engagement. If
  it drops below `τ_high`, the agent apologizes, hands to voicemail, and flags the call
  for golden-set review. The false-positive recovery path is a first-class feature.

### 4. Intelligence

- Dual-channel recording → exact diarization for free.
- **Speaker embeddings** (ECAPA-TDNN class) per caller-channel segment → pgvector →
  agglomerative clustering into *operators*. Operators group into *campaigns* via shared
  script embeddings, callback numbers, and wallet addresses. This links one scammer
  across many spoofed numbers and is the most valuable derived artifact in the system.
- Entity extraction: callback numbers, URLs, crypto addresses (checksum-validated), bank
  account/routing numbers, named remote-access tools, claimed company names, dollar
  amounts.
- Metrics: minutes wasted total and per campaign, taxonomy distribution, engage rate.
- Export: FTC / FCC / IC3 complaint payloads — **user-triggered only**, never automatic.

## Data model

Postgres 16 + pgvector. Core tables: `numbers` (with `line_class`), `calls`,
`call_events` (append-only canonical log), `screening_features`, `verdicts`,
`transcripts`, `personas`, `persona_state`, `speaker_embeddings` (vector),
`opener_embeddings` (vector), `operators`, `campaigns`, `extracted_entities`.

Audio lives in object storage, content-addressed; rows hold digests. Redis holds live
per-call state and rate-limit counters only — never a source of truth.

Migrations are versioned and reviewed, each with an explicit migration-impact note per
`CLAUDE.md`.

## The quality bar

This section is the reason the project is worth building well. A voice agent that cannot
be replayed cannot be engineered — it can only be tinkered with.

1. **Canonical event log.** Every call appends: inbound audio frames with timestamps, VAD
   events, STT partials and finals, every LLM request/response, TTS requests, playback
   timings, and every policy decision *with its full input feature vector*.
2. **Deterministic replay harness.** Re-run the entire screening and policy stack against
   a recorded call with byte-identical inputs. Requires seeded RNG and an injected clock;
   **no wall-clock reads anywhere in decision code**. This turns the classifier into
   ordinary testable software.
3. **Golden sets.**
   - `goldens/legit/` — real non-scam calls recorded with consent (friends, businesses,
     delivery, medical). **CI asserts FPR = 0 at the shipping threshold. Any PR that
     raises FPR on this set fails, no exceptions.**
   - `goldens/scam/` — confirmed scam calls. Recall is tracked and may regress only with
     explicit review.
   - Manifests in git; audio in object storage, referenced by digest.
4. **Adversarial scammer simulator.** An LLM adversary running real scam scripts with a
   hangup model, driven through the full stack over loopback SIP. Hermetic — no real
   calls, no real money. Headline CI metric: mean simulated time-on-call per PR.
5. **Latency SLO tests.** Synthetic turn-latency benchmark over recorded audio; a p95
   regression fails the build.
6. **Chaos tests.** Carrier disconnect mid-call, STT stall, TTS 5xx, LLM timeout. Each
   must degrade to a graceful stall phrase — never dead air, never a crash that drops the
   call in a way that reveals the bot.
7. **Observability.** OpenTelemetry trace per call, span per turn, attributes carrying
   latency components and policy posterior. Dashboards for turn latency, engage rate,
   calibration error, and minutes wasted.

## Repo layout

```
apps/
  control-plane/     TS API + dashboard
  agent/             Python realtime voice agent
packages/
  carrier/           CarrierAdapter + Telnyx/Twilio implementations (TS)
  contracts/         shared schemas, codegen to zod + pydantic
services/
  screener/          feature extractors, fusion, calibration (Python)
  intelligence/      embeddings, clustering, entity extraction (Python)
harness/
  replay/            deterministic call replay
  simulator/         adversarial scammer agent + loopback SIP
  goldens/           labeled corpora (manifests in git, audio by digest)
infra/
  migrations/        Postgres
  deploy/            terraform + k8s
docs/
```

## Milestones

Each ends with a working, tested increment.

**M0 — Foundations.** Monorepo, `CarrierAdapter` with both carriers, one DID answers and
writes a canonical event log, Postgres schema and migrations, CI, OTel wiring.
*Exit: the replay harness reproduces a recorded call byte-identically.*

**M1 — Screening v1, no bot.** Pre-answer signals, calibrated fusion, three-way routing
with the abstain band, golden-set CI gate.
*Exit: FPR = 0 on `goldens/legit/`; recall reported as a baseline.*

**M2 — Engagement v1, bait DIDs only.** Realtime agent, one persona, barge-in with
correct context truncation, filler injector, egress guardrail, duration caps, graceful
bail-out.
*Exit: simulator holds a scam script ≥3 minutes; p95 turn latency <900 ms.*
*Barge-in is currently disabled — see the status note at the top of this file before
re-enabling it.*

**M3 — Screening v2.** Acoustic features (dead-air, babble, scripted-opener ANN), LLM
intent, joint calibration, live posterior monitor enabled on `PERSONAL` lines.
*Exit: measurable recall lift on `goldens/scam/` with FPR still 0.*

**M4 — Strategy engine.** Tactic library, frustration estimator, contextual bandit,
offline replay-based policy evaluation.
*Exit: simulator mean time-on-call beats the M2 baseline by a pre-registered margin.*

**M5 — Intelligence.** Speaker embeddings, operator clustering, campaign linking, entity
extraction, dashboard, complaint export.
*Exit: the same operator is linked across ≥2 spoofed numbers in the corpus.*

## Verification

- **Unit/scoped**: `pytest` per service, `vitest` for TS packages. Cheapest relevant test
  first per `CLAUDE.md`.
- **Replay**: `harness/replay` over the golden corpora — the primary regression gate.
- **End-to-end, hermetic**: `harness/simulator` drives a full call over loopback SIP in
  CI; asserts time-on-call, turn latency p95, and zero guardrail trips.
- **End-to-end, live**: call a `BAIT` DID from a test handset, confirm the disclosure
  plays, the agent engages, the canonical log is complete, and the replay of that live
  call reproduces the same verdict.
- **False-positive drill**: call a `PERSONAL` DID from an unknown number and behave like a
  human; confirm the abstain band routes to voicemail and the bot never answers.
