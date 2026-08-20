#!/usr/bin/env python3
"""Regenerate the checked-in fiction pack.

Every identity is verified before it touches disk, so an unusable-number invariant can
never be broken by a pack that was generated and forgotten about. Run after changing
the generator, then commit the JSON.

    python scripts/generate_fiction_pack.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssscammers.shared.fiction import (  # noqa: E402
    assert_identity_safe,
    generate_identity,
    write_identity,
)
from ssscammers.shared.output_filter import OutputFilter  # noqa: E402

#: Personas that ship with the project. Adding one here and re-running is all it
#: takes to give it an identity.
PERSONAS: tuple[str, ...] = ("marjorie", "harold", "dot")


def main() -> int:
    for persona_id in PERSONAS:
        identity = generate_identity(persona_id)

        # Unusable by anyone...
        assert_identity_safe(identity)
        # ...and still speakable by the persona it belongs to.
        filt = OutputFilter.for_identity(identity)
        result = filt.check(identity.to_prompt_block())
        if not result.allowed:
            print(
                f"REFUSING to write {persona_id}: its own fact sheet would be blocked "
                f"pre-TTS ({[v.value for v in result.violations]})",
                file=sys.stderr,
            )
            return 1

        path = write_identity(identity)
        print(f"wrote {path.relative_to(Path.cwd())}  ({identity.full_name}, {identity.age})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
