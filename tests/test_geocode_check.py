"""The geocode check's logic — everything except live Nominatim behavior.

The script is a networked pre-launch step (docs/guardrails.md says why it stays out
of CI); these tests pin the vocabulary-derived tokenizer, the matcher, and the run
orchestration — both queries per identity, canary gating, fail-closed error paths,
exit codes, and throttling — against canned Nominatim shapes, so the only thing CI
does not exercise is the live service itself.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path

import httpx

from ssscammers.shared import fiction
from ssscammers.shared.fiction import STREET_NAMES, load_pack

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_fiction_geocode.py"
_spec = importlib.util.spec_from_file_location("check_fiction_geocode", _SCRIPT)
geocode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geocode)


def road(name: str) -> dict:
    """A canned Nominatim jsonv2 place with an address breakdown."""
    return {"display_name": f"{name}, Somewhere, USA", "address": {"road": name}}


CITY_OBJECT = {
    # What a street search degraded to a city search returns: non-empty, road-free.
    "display_name": "Dayton, Montgomery County, Ohio, United States",
    "address": {},
}

OAK_CITY_OBJECT = {
    # The nastier degraded shape: a road-free city object whose display_name
    # carries a canary word as a substring. Must never satisfy the canary.
    "display_name": "Royal Oak, Oakland County, Michigan, United States",
    "address": {},
}


class RecordingSearch:
    """A NominatimSearch over a MockTransport, recording sleeps and queries."""

    def __init__(self, responder) -> None:
        self.sleeps: list[float] = []
        self.queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            street = request.url.params["street"]
            self.queries.append(street)
            result = responder(street)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, json=result)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.search = geocode.NominatimSearch(client, sleep=self.sleeps.append)


def canary_only(street: str):
    """The all-clear responder: canary streets exist, everything else is empty."""
    if street in geocode.CANARY_STREETS:
        return [road(f"{street} Street")]
    return []


class TestDistinctiveTokens:
    def test_every_vocabulary_entry_strips_its_own_suffix(self) -> None:
        # The coupling contract with fiction.STREET_NAMES: tokens are the entry's
        # words minus the trailing suffix, whatever suffix the entry uses — the
        # hardcoded-suffix-list design this replaced silently kept "Close" and
        # "Rise" as distinctive words.
        for name in STREET_NAMES:
            tokens = geocode.distinctive_tokens(f"1234 {name}")
            assert tokens == [word.lower() for word in name.split()[:-1]]
            assert tokens, f"{name!r} yielded no distinctive tokens"

    def test_a_street_outside_the_vocabulary_is_unverifiable(self) -> None:
        assert geocode.distinctive_tokens("5644 Elm Street") is None

    def test_a_bare_number_is_unverifiable(self) -> None:
        assert geocode.distinctive_tokens("5644") is None

    def test_a_vocabulary_name_inside_another_street_does_not_match(self) -> None:
        # endswith on a word boundary: "Old Marlowe Pemberton Drive" matches the
        # "Marlowe Pemberton Drive" entry (same distinctive tail), but a fused
        # word must not.
        assert geocode.distinctive_tokens("12 NotMarlowe Pemberton Drive") is None


class TestMatchingRoad:
    RESULTS = [road("Marlowe Pemberton Dr"), {"display_name": "Pemberton Park", "address": {}}]

    def test_a_road_with_every_token_is_a_hit(self) -> None:
        assert (
            geocode.matching_road(["marlowe", "pemberton"], self.RESULTS)
            == "Marlowe Pemberton Dr"
        )

    def test_a_partial_match_is_not_a_hit(self) -> None:
        # "Pemberton Park" shares one token, not all — sharing a word with some
        # real place is unavoidable; sharing the whole street name is the hit.
        assert geocode.matching_road(["quill", "pemberton"], self.RESULTS) is None

    def test_no_results_is_hit_free(self) -> None:
        assert geocode.matching_road(["marlowe", "pemberton"], []) is None

    def test_display_name_is_the_fallback_when_address_lacks_a_road(self) -> None:
        results = [{"display_name": "Marlowe Pemberton Alley, Somewhere"}]
        assert geocode.matching_road(["marlowe", "pemberton"], results) is not None

    def test_matching_is_case_insensitive(self) -> None:
        results = [{"display_name": "x", "address": {"road": "MARLOWE PEMBERTON DR"}}]
        assert geocode.matching_road(["marlowe", "pemberton"], results) is not None


class TestCheckIdentity:
    def test_an_unexplainable_street_is_a_failure_not_a_road_hit(self) -> None:
        identity = dataclasses.replace(
            load_pack()["marjorie"], street="5644 Elm Street"
        )
        recorder = RecordingSearch(canary_only)
        reason = geocode.check_identity(recorder.search, identity)
        assert reason is not None and "cannot be checked" in reason
        assert "matches real road" not in reason
        assert recorder.queries == []  # unverifiable is decided before any request


class TestRunCheck:
    def test_all_clear_exits_zero_with_the_throttle_between_requests(self) -> None:
        identities = load_pack()
        cities = {(i.city, i.state) for i in identities.values()}
        recorder = RecordingSearch(canary_only)
        assert geocode.run_check(recorder.search) == 0
        # One canary per city, then a full-street and a bare-name query per
        # identity; the politeness delay sits between every consecutive pair.
        assert len(recorder.queries) == len(cities) + 2 * len(identities)
        assert recorder.sleeps == [geocode.THROTTLE_SECONDS] * (len(recorder.queries) - 1)
        for identity in identities.values():
            assert identity.street in recorder.queries
            tokens = geocode.distinctive_tokens(identity.street)
            assert " ".join(tokens) in recorder.queries

    def test_a_cross_suffix_road_from_the_bare_query_is_a_hit(self) -> None:
        # The reason the bare-name query exists: the full-street query for
        # "… Drive" never returns the real "… Street" one suffix over.
        marjorie = load_pack()["marjorie"].street
        bare = " ".join(geocode.distinctive_tokens(marjorie))

        def responder(street: str):
            if street in geocode.CANARY_STREETS:
                return [road(f"{street} Street")]
            if street == bare:
                return [road("Marlowe Pemberton Street")]
            return []

        assert geocode.run_check(RecordingSearch(responder).search) == 1

    def test_a_canary_miss_fails_before_any_identity_is_queried(self) -> None:
        # A degraded street search answers every query with the road-free city
        # object; every canary street must be tried (throttled between tries),
        # then the run fails without a single identity query.
        recorder = RecordingSearch(lambda street: [CITY_OBJECT])
        assert geocode.run_check(recorder.search) == 1
        assert recorder.queries == list(geocode.CANARY_STREETS)
        assert recorder.sleeps == [geocode.THROTTLE_SECONDS] * (
            len(geocode.CANARY_STREETS) - 1
        )

    def test_a_canary_word_inside_a_city_display_name_is_not_a_canary(self) -> None:
        # "Royal Oak, Oakland County" contains "oak", but a road-free city
        # object must never satisfy the positive control — that is exactly the
        # degraded pipeline the canary exists to catch.
        recorder = RecordingSearch(lambda street: [OAK_CITY_OBJECT])
        assert geocode.run_check(recorder.search) == 1
        assert recorder.queries == list(geocode.CANARY_STREETS)

    def test_a_later_canary_street_can_carry_a_city_without_the_first(self) -> None:
        # The Bakersfield case: no "Main" under the structured search, but "Oak"
        # exists — one firing canary street is proof enough of a working search.
        def responder(street: str):
            if street == "Oak":
                return [road("Oak Street")]
            return []

        assert geocode.run_check(RecordingSearch(responder).search) == 0

    def test_a_network_error_on_one_identity_fails_the_run(self) -> None:
        # The fail-closed catch: turning this into a warn-and-continue would let
        # a run that checked nothing record itself as hit-free.
        marjorie = load_pack()["marjorie"].street

        def responder(street: str):
            if street == marjorie:
                return httpx.ConnectError("boom")
            return canary_only(street)

        assert geocode.run_check(RecordingSearch(responder).search) == 1

    def test_an_http_error_status_fails_the_run(self) -> None:
        marjorie = load_pack()["marjorie"].street

        def responder(street: str):
            if street == marjorie:
                return httpx.Response(500)
            return canary_only(street)

        assert geocode.run_check(RecordingSearch(responder).search) == 1

    def test_non_list_json_fails_the_run(self) -> None:
        marjorie = load_pack()["marjorie"].street

        def responder(street: str):
            if street == marjorie:
                return httpx.Response(200, json={"error": "shape change"})
            return canary_only(street)

        assert geocode.run_check(RecordingSearch(responder).search) == 1

    def test_a_canary_network_error_fails_the_run(self) -> None:
        recorder = RecordingSearch(lambda street: httpx.ConnectError("boom"))
        assert geocode.run_check(recorder.search) == 1

    def test_an_empty_pack_fails_the_run(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(fiction, "PACK_DIR", tmp_path)
        recorder = RecordingSearch(canary_only)
        assert geocode.run_check(recorder.search) == 1
        assert recorder.queries == []

    def test_the_real_pack_parses_and_every_street_is_explainable(self) -> None:
        # The offline half of the pre-launch contract: whatever the network says,
        # the checked-in pack must be checkable at all.
        for path in sorted(fiction.PACK_DIR.glob("*.json")):
            street = json.loads(path.read_text(encoding="utf-8"))["street"]
            assert geocode.distinctive_tokens(street), path.stem
