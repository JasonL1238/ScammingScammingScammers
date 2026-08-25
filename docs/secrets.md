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

`ANTHROPIC_API_KEY` may be left empty. The SDK resolves credentials in order — the env
var, then `ANTHROPIC_AUTH_TOKEN`, then an `ant auth login` profile — so a developer
who has logged in needs no key in their environment at all.

The trap worth knowing: **an exported `ANTHROPIC_API_KEY` silently overrides every
profile**, including an empty one. If requests are hitting an org you did not expect,
check `ant auth status` before debugging anything else, and unset the variable rather
than blanking it.

## Owner PII is a secret too

`OWNER_PII_DENYLIST` holds real values — the owner's name, email, family names — and
its whole purpose is that the agent must never say them. It is configuration, it is
sensitive, and it belongs in the same store as the API keys.

## Rotation

Rotate on any exposure, and on a schedule for Twilio. After rotating, restart the
media workers: the settings object is built once at boot and is not reloaded mid-call,
which is intentional — a credential changing underneath a live call is worse than a
call finishing on the old one.
