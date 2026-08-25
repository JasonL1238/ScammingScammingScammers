# One image for the agent and the migrate one-shot; compose picks the command.
#
# The source tree is COPY'd to /app and the process runs from there: `python -m`
# puts the working directory first on sys.path, so imports resolve from /app —
# which is what makes the package's repo-root-relative lookups work
# (persona.py finds personas/ and playbooks/, ssscammers/db/files.py finds
# db/migrations/, all via parents[2] of their own file). Discarding the source
# tree after `pip install` would silently break every one of those.

FROM python:3.13-slim

# libsndfile backs the ambient mixer (soundfile); certificates back every
# outbound TLS connection (Twilio recording start, model, speech, ntfy).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The agentstate named volume mounts over /var/lib/ssscammers and inherits this
# directory's ownership on first creation — chown BEFORE dropping root, or the
# daily ledger cannot write and every call fails closed at admission.
RUN useradd --create-home app \
    && mkdir -p /var/lib/ssscammers \
    && chown app:app /var/lib/ssscammers

WORKDIR /app

# Explicit COPY list, never `COPY . .`: the build context is the repo root, and
# a careless glob would bake .env into a layer. .dockerignore is the backstop;
# this list is the policy.
COPY pyproject.toml ./
COPY ssscammers/ ssscammers/
COPY personas/ personas/
COPY playbooks/ playbooks/
COPY data/ data/
COPY db/ db/

RUN pip install --no-cache-dir ".[media,db]"

USER app
EXPOSE 8080

# Byte-identical to the compose command: one process, one worker — the
# concurrency cap and live-call registry are in process memory.
CMD ["python", "-m", "ssscammers.agent", "--port", "8080"]
