# Secrets

Six providers hold keys here: Twilio, Anthropic, Deepgram, Cartesia, ElevenLabs, and
Cloudflare R2. Four of them matter today — **ElevenLabs and R2 are not wired to anything
yet** (no code path selects a second TTS provider, and recordings stay in Twilio), so
provisioning those two changes nothing about how the system runs. A leaked Twilio key is
the expensive one — it is why outbound is disabled at the subaccount level rather than
only in code.

## Rules

1. **Nothing but `.env.example` is committed.** `.gitignore` excludes `.env` and every
   `.env.*` variant. The example file carries names and shapes, never values.
2. **Secrets must be injected at container start**, from a SOPS-encrypted file or a
   secrets manager — never baked into images and never passed on a command line,
   where they land in shell history and process listings. (Today's dev compose
   injects them at container start from a local plaintext `.env` via `env_file` —
   never baked into images or passed on a command line, both of which still hold —
   and the SOPS/secrets-manager step remains the requirement before any real
   deployment. The venv workflow reads plain environment variables; nothing in the
   agent parses a `.env` file.)
3. **The media worker never gets an org-scoped credential it doesn't need.** Today
   there is one process and one `.env`; when the stack splits, anything with broad
   authority belongs on the control-plane side, and the process that runs
   scammer-influenced work gets the narrowest key that does the job.

## Anthropic specifically

`ANTHROPIC_API_KEY` may be left empty. The SDK resolves credentials in order, first
match winning — so a developer who has logged in needs no key in their environment at
all. The full order matters to anything trying to keep a process *off* the API, so it is
written out rather than summarised:

1. **Explicit constructor arguments** (`api_key=`, `auth_token=`, `credentials=`,
   `config=`, `profile=`). When any is passed, environment variables are not consulted
   for credentials at all.
2. `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`.
3. `ANTHROPIC_PROFILE`.
4. Workload-identity federation variables (`ANTHROPIC_IDENTITY_TOKEN[_FILE]`,
   `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`).
5. The active on-disk profile.

A set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` takes precedence over 3–5, which is
why poisoning those two is sufficient against a profile. It is *not* sufficient against
step 1 — a call site passing a literal never reads the environment. This agent's own
call site is safe on that count, because it sources the key from the environment and
passes it through, so a poisoned variable becomes the poisoned argument.

The Bedrock, Vertex, and other cloud client variants the SDK exports consult none of
these — they use the AWS and GCP credential chains and the cloud metadata endpoint at
`169.254.169.254`.

The trap worth knowing: **an exported `ANTHROPIC_API_KEY` silently overrides every
profile**, including an empty one. If requests are hitting an org you did not expect,
check `ant auth status` before debugging anything else, and unset the variable rather
than blanking it.

Several more variables reach the wire without touching a credential, and they are easy
to miss when trying to keep a process *off* the network — all of them matter to the
offline-test guard Phase 3 builds. **`ANTHROPIC_BASE_URL`** redirects every request;
note that *clearing* it does not pin the destination, because base-url precedence runs
constructor argument → this variable → the profile's own `resolved_base_url`, so an
on-disk profile decides once the variable is gone. **`HTTP_PROXY` / `HTTPS_PROXY` /
`ALL_PROXY`** (and their lowercase forms) are honoured, because the SDK's HTTP client
trusts the environment: with a proxy set, the only socket the SDK opens is to the proxy
— often on loopback — and the request still reaches the API. **`ANTHROPIC_CUSTOM_HEADERS`**
is read from the environment on every request. A network guard that allows loopback and
ignores these blocks nothing on a machine running mitmproxy or a corporate proxy, while
reporting itself satisfied.

## Owner PII is a secret too

`OWNER_PII_DENYLIST` holds real values — the owner's name, email, family names — and
its whole purpose is that the agent must never say them. It is configuration, it is
sensitive, and it belongs in the same store as the API keys.

## Rotation

Rotate on any exposure, and on a schedule for Twilio. After rotating, restart the
media workers: the settings object is built once at boot and is not reloaded mid-call,
which is intentional — a credential changing underneath a live call is worse than a
call finishing on the old one.
