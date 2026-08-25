# ScammingScammingScammers

An **inbound-only** AI honeypot for scam calls. A dedicated phone number answers in a
believable persona (a slow, confused, hard-of-hearing retiree) and wastes the caller's
time for as long as possible — mishearing, rambling, fumbling — while never being
abusive and never emitting a single piece of real or usable data.

The metric is **scammer-minutes wasted**.

## The one rule everything else hangs off

**The system never places a call, never calls back, never sends a text.** Every
conversation is one a scammer chose to start by dialing our number. This keeps the
project clear of the TCPA, clear of harassment exposure, and clear of Twilio's AUP —
and it is enforced in code (guardrail G-1), not by convention.

## How a call flows

```
scammer dials ─► Twilio ─► POST /twilio/voice
                             │  triage: blocklist / allowlist / capacity / reputation
                             ▼
        recording started via REST (dual channel) before the notice plays
        TwiML: <Play> recorded-line notice  +  <Connect><Stream wss://…>
               +  <Redirect> /twilio/after-stream
                             │
        Pipecat worker: Deepgram Flux (STT+turn) ─► PersonaDirector (FSM, tactics,
        in-character latency, pre-TTS output filter) ─► Claude Sonnet 5 ─► Cartesia TTS
        ─► ambient mixer ─► back to Twilio
                             │
        events ─► Postgres ─► live dashboard;  recording ─► R2 ─► batch enrichment
                             └── planned; not in the repo. Today events go to the log
                                 (`LoggingEventSink`) and recordings stay in Twilio.
```

## Layout

The design doc names components by role; on disk they are Python-importable packages.
Rows marked *planned* are designed in [`docs/plan.md`](docs/plan.md) and not in the repo
yet — nothing imports them. The build order for everything still to come is
[`docs/roadmap.md`](docs/roadmap.md).

| Design name | On disk |
|---|---|
| `/pipecat-agent` | [`ssscammers/agent/`](ssscammers/agent) — [webhooks](ssscammers/agent/webhooks.py), [TwiML](ssscammers/agent/twiml.py), [call registry](ssscammers/agent/registry.py), [conversation driver](ssscammers/agent/conversation.py), [media pipeline](ssscammers/agent/media.py), persona director, triage |
| `/shared` | [`ssscammers/shared/`](ssscammers/shared) — classification enums, config, output filter |
| `/enrichment-worker` | `ssscammers/enrichment/` — Batch-API post-call analysis (*planned*) |
| `/sim-scammer` | [`ssscammers/simscammer/`](ssscammers/simscammer) — simulated-scammer test harness |
| persona bundles | [`personas/`](personas) — one directory per persona (prompt + config + sound pack) |
| shared playbooks | [`playbooks/`](playbooks) — cached prompt fragments: stalling tactics, scam types |
| fiction data pack | [`data/fiction_pack/`](data/fiction_pack) — the only "personal data" the agent may speak |
| DB schema | [`db/migrations/`](db/migrations) |
| dashboard | `dashboard/` — Next.js, private (Tailscale) (*planned*) |

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The default test suite needs **no API keys and no network** — it covers the
safety-critical layer (fiction-pack invariants, the pre-TTS output filter, the call
state machine, triage, the conversation driver, and the webhook routing). Install the
realtime stack with `pip install -e ".[media,dev]"` when you're ready to run an actual
call.

Every push runs CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): the full
suite in both install shapes (with and without the media extra), the simscammer
release gate (`--all-scripts --dry`), and ruff. A red run on `main` blocks all other
work until fixed.

### Judge a persona without a phone line

```bash
python -m ssscammers.simscammer.textloop --persona marjorie
```

This runs the same conversation driver a real call runs; only the clock and the speakers
are fake. `--dry` skips the model entirely, `--script <name>` plays a canned caller, and
`--all-scripts` is the release gate.

### Run the agent

```bash
python -m ssscammers.agent --port 8080
```

One process, one worker, deliberately: the concurrency cap and the live-call registry
live in memory, so a second worker would answer calls the first one cannot see. Point
Twilio's voice webhook at `POST /twilio/voice` and its status callback at
`POST /twilio/status`, both on `PUBLIC_BASE_URL`.

Every webhook requires a valid `X-Twilio-Signature`, and a missing `TWILIO_AUTH_TOKEN`
refuses service rather than skipping the check. `--allow-unsigned` exists for local
development and must never be used with a live number.

### How a call ends

The pipeline hangs up by closing its media socket, and Twilio then resumes the TwiML
document at `POST /twilio/after-stream`. That endpoint is what decides between a hangup
and a voicemail — which is how the disclosure script's promise ("I'm going to put you
through to voicemail now") is actually kept for a real person who reached this line by
mistake.

### Audio assets

The sound packs referenced in `personas/*/persona.yaml` are not in the repo. Render them
into `personas/<id>/audio/` as **8 kHz mono 16-bit WAV** — that is Twilio's stream rate,
so matching it keeps resampling off the live audio path. A missing clip is logged and
skipped rather than failing a call.

`NOTICE_AUDIO_URL` is the asset the recording notice depends on. Leaving it empty is
fine — a fixed synthesised line is spoken instead. A URL Twilio cannot fetch would be
worse: Twilio logs the failed `<Play>` and continues to the next verb, so the caller
would be recorded and connected with no notice at all. Three layers close that: the app
refuses to start on a malformed value; a configured clip is fetched at boot and
re-probed on an interval (`NOTICE_PROBE_INTERVAL_SECONDS`), and while it is unreachable
the engage document opens with the spoken line instead — degraded, never absent — with
an ntfy alert on each transition (when `NTFY_TOPIC` is configured) and the state
visible at `/healthz` as `notice_clip`.
The residual window is one probe interval: a clip that dies between probes can still
reach Twilio until the next probe notices.

## Safety

Guardrails are numbered G-1…G-20 and each is enforced at a layer: `CODE` (impossible
by construction), `MONITOR` (out-of-band classifier that can override the agent), or
`PROMPT`. The design rule is that nothing safety-critical is `PROMPT`-only; the MONITOR
half is not built yet, so the guardrails tabled `PROMPT + MONITOR` are PROMPT-only today —
[`docs/guardrails.md`](docs/guardrails.md) says which, and the CODE guardrails that protect
a real caller do not depend on it. The enforcement points live in
[`ssscammers/shared/output_filter.py`](ssscammers/shared/output_filter.py) and
[`ssscammers/agent/persona_director.py`](ssscammers/agent/persona_director.py).

A real person *will* eventually land here — conditional call forwarding guarantees it.
That case is a first-class path, not an afterthought: the agent drops the persona
within one turn, says plainly that it is an automated assistant, and hands off to a
normal voicemail.

This is a personal project for one person's own phone line, not a product or a service
offered to others. It is not legal advice; see [`docs/legal.md`](docs/legal.md) for the
posture and the short list of questions worth asking an actual lawyer.
