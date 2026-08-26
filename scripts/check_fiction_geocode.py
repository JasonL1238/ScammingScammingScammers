#!/usr/bin/env python3
"""Pre-launch check: do the fiction pack's street addresses geocode to real streets?

The pack's street names are drawn from a curated invented list, but "invented" is a
claim about the generator, not the world — a name can collide with a real street in
the identity's own city, and an address that resolves is one a scammer could send a
courier to. For each identity this queries Nominatim (OpenStreetMap) twice: once
with the full street as written, and once with just the distinctive name, because a
structured search for "… Close" never returns the real "… Street" one suffix over —
couriers fuzzy-match suffixes, so a same-name road under any suffix is a hit. The
distinctive tokens come from ``STREET_NAMES`` itself (each entry minus its trailing
suffix word), so the checker cannot drift from the generator's vocabulary; a street
the vocabulary cannot explain fails rather than passes.

Empty results are only trusted after a per-city canary — common street names tried
in order until one fires — proves the search can see real roads at all: a degraded
street search still answers 200 with the city object, which would otherwise read as
"hit-free".

Deliberately NOT in CI: it needs the network, third parties rate-limit it, and its
verdict changes only when the pack regenerates. Run it before launch and after any
pack regeneration, and record the dated result in docs/guardrails.md:

    python scripts/check_fiction_geocode.py

Exit 0 only when every canary fires and every identity's street is checkable and
hit-free. Any hit, any canary miss, any network failure, and any unexplainable
street exits 1 — an address this script could not check is not an address proven
fictional.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssscammers.shared.fiction import (  # noqa: E402
    STREET_NAMES,
    FictionIdentity,
    load_pack,
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

USER_AGENT = "ScammingScammingScammers-geocode-check/1.0 (pre-launch fiction audit)"

#: Nominatim's usage policy: at most one request per second.
THROTTLE_SECONDS = 1.1

#: Positive-control streets, tried in order until one fires. No single name covers
#: every pack city (Bakersfield has no "Main" — probed 2026-08-26 — but plenty of
#: "Oak"); a city where none of these produces a road the matcher recognises is a
#: canary failure, because that pipeline cannot clear an invented street either.
CANARY_STREETS = ("Main", "Oak")


class UncheckableError(Exception):
    """A response this run cannot interpret — always a failure, never an ok."""


class NominatimSearch:
    """A throttled structured-search client.

    One instance per run: the politeness delay spans every request the run makes,
    whichever function makes it.
    """

    def __init__(self, client: httpx.Client, sleep=time.sleep) -> None:
        self._client = client
        self._sleep = sleep
        self._requested = False

    def roads(self, street: str, city: str, state: str) -> list[dict]:
        if self._requested:
            self._sleep(THROTTLE_SECONDS)
        self._requested = True
        response = self._client.get(
            NOMINATIM_URL,
            params={
                "format": "jsonv2",
                "street": street,
                "city": city,
                "state": state,
                "addressdetails": 1,
                "limit": 10,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise UncheckableError(
                f"Nominatim returned {type(payload).__name__} JSON, expected a list"
            )
        return payload


def distinctive_tokens(street: str) -> list[str] | None:
    """The invented name's distinctive words, derived from the generator's vocabulary.

    The matching ``STREET_NAMES`` entry minus its trailing suffix word, lowercased:
    "5644 Marlowe Pemberton Drive" → ["marlowe", "pemberton"]. Returns ``None`` for
    a street the vocabulary cannot explain — unverifiable, which the caller must
    treat as a failure, never a pass.
    """
    for name in STREET_NAMES:
        if street == name or street.endswith(f" {name}"):
            tokens = [word.lower() for word in name.split()[:-1]]
            return tokens or None
    return None


def matching_road(
    tokens: list[str], results: list[dict], *, require_road: bool = False
) -> str | None:
    """The first returned road containing every distinctive token, or ``None``.

    ``require_road`` restricts matching to results with an ``address.road``
    breakdown. That is the canary's mode: a degraded street search answers with
    the road-free city object, whose display_name can still carry a canary word
    as a substring ("Royal Oak, Oakland County, …") and must never satisfy the
    positive control. Identity matching keeps the display_name fallback — there
    it errs toward hits, the safe polarity.
    """
    for result in results:
        road = (result.get("address") or {}).get("road")
        if not road:
            if require_road:
                continue
            road = result.get("display_name", "")
        lowered = road.lower()
        if all(token in lowered for token in tokens):
            return road
    return None


def canary_ok(nominatim: NominatimSearch, city: str, state: str) -> bool:
    """Positive control: the search must see a street known to be real.

    Asserting that the *matcher* fires — not merely that results are non-empty —
    is deliberate: a street search whose ``street`` filter has stopped working
    still returns the city object itself, a non-empty answer that proves nothing.
    """
    for street in CANARY_STREETS:
        results = nominatim.roads(street, city, state)
        if matching_road([street.lower()], results, require_road=True) is not None:
            return True
    return False


def check_identity(nominatim: NominatimSearch, identity: FictionIdentity) -> str | None:
    """One identity's verdict: a failure reason, or ``None`` when provably hit-free."""
    tokens = distinctive_tokens(identity.street)
    if tokens is None:
        return (
            f"{identity.street!r} does not match the generator vocabulary "
            f"(fiction.STREET_NAMES) and cannot be checked"
        )
    for query in (identity.street, " ".join(tokens)):
        road = matching_road(
            tokens, nominatim.roads(query, identity.city, identity.state)
        )
        if road is not None:
            return (
                f"{identity.street!r} ({identity.city}, {identity.state}) "
                f"matches real road {road!r}"
            )
    return None


def run_check(nominatim: NominatimSearch) -> int:
    identities = load_pack()
    if not identities:
        print("no fiction pack found (load_pack returned nothing)", file=sys.stderr)
        return 1

    cities = sorted({(i.city, i.state) for i in identities.values()})
    for city, state in cities:
        try:
            passed = canary_ok(nominatim, city, state)
        except (httpx.HTTPError, UncheckableError) as exc:
            print(
                f"CANARY for {city}, {state} could not be checked "
                f"({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return 1
        if not passed:
            print(
                f"CANARY failed for {city}, {state}: a street search that sees "
                f"none of {CANARY_STREETS} cannot clear an invented street either",
                file=sys.stderr,
            )
            return 1
        print(f"canary  {city}, {state}: ok")

    failures: list[str] = []
    for persona_id, identity in identities.items():
        try:
            reason = check_identity(nominatim, identity)
        except (httpx.HTTPError, UncheckableError) as exc:
            # Fail closed: an address this script could not check is not an
            # address proven fictional.
            reason = f"could not be checked ({type(exc).__name__}: {exc})"
        label = f"{persona_id}: {identity.street}, {identity.city}"
        if reason is None:
            print(f"ok      {label}")
        else:
            print(f"FAIL    {label} — {reason}")
            failures.append(f"{persona_id}: {reason}")

    if failures:
        print(
            f"\n{len(failures)} identity(ies) failed; regenerate those identities "
            f"(scripts/generate_fiction_pack.py) and re-run",
            file=sys.stderr,
        )
        return 1
    print("\nall fiction-pack streets are hit-free on Nominatim")
    return 0


def main() -> int:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15.0) as client:
        return run_check(NominatimSearch(client))


if __name__ == "__main__":
    raise SystemExit(main())
