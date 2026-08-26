#!/usr/bin/env python3
"""Rewrite the golden call streams from their manifests.

Run this only when a change to the event stream is *intended*, and read the
diff before committing it: a golden is the record of what the system did, so
regenerating one to make a test pass is how a regression becomes the baseline.
The test names the alternative — fix the code — and this script exists for the
other case, where the payload really did change on purpose.

    python scripts/regenerate_goldens.py            # all of them
    python scripts/regenerate_goldens.py scam_bank_otp_baited

Needs no network and no API key: the model side is a recording.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssscammers.simscammer.golden import (  # noqa: E402
    GOLDEN_MANIFESTS,
    diff_events,
    events_to_json,
    load_golden,
    replay_call,
    write_golden,
)


async def regenerate(names: list[str]) -> int:
    wanted = [m for m in GOLDEN_MANIFESTS if not names or m.name in names]
    if names and len(wanted) != len(names):
        known = ", ".join(m.name for m in GOLDEN_MANIFESTS)
        print(f"unknown golden(s); available: {known}", file=sys.stderr)
        return 2

    changed = 0
    for manifest in wanted:
        before = load_golden(manifest) if manifest.path.exists() else None
        after = events_to_json(await replay_call(manifest))
        await write_golden(manifest)

        if before is None:
            print(f"created  {manifest.path.name}")
            changed += 1
        elif (diff := diff_events(before, after)) is not None:
            print(f"CHANGED  {manifest.path.name}")
            print(diff)
            changed += 1
        else:
            print(f"same     {manifest.path.name}")

    print(f"\n{changed} golden(s) written with changes; review the diff before committing")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(regenerate(list(argv if argv is not None else sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
