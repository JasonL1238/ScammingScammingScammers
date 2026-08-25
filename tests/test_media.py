"""The media adapter's testable edges.

The Pipecat wiring itself is exercised by a real call, not by this file — mocking a whole
realtime pipeline would assert the mock. What is checked here is everything that can go
wrong *around* it: the optional dependency, the name-for-name consistency of the imported
stack, and the sound-pack loader, which runs on the live audio path and must never raise.
"""

from __future__ import annotations

import asyncio
import importlib.util
import wave
from pathlib import Path

import pytest
from helpers import reserve_call

from ssscammers.agent import media
from ssscammers.agent.persona import VoiceConfig
from ssscammers.agent.registry import CallRegistry
from ssscammers.shared.config import Settings
from ssscammers.shared.enums import EndReason


def write_wav(
    path: Path, *, channels: int = 1, width: int = 2, rate: int = media.CLIP_SAMPLE_RATE
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x01" * 400 * channels)


class FakeSocket:
    """The two methods `_serve_call` uses on the way to refusing a call."""

    def __init__(self) -> None:
        self.closed: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)

    async def accept(self) -> None:  # pragma: no cover - not reached by these tests
        raise AssertionError("_serve_call must not accept the socket itself")


class TestWhetherABundleCanBeVoicedAtAll:
    """`unservable_reason` is checked twice — at boot and at call setup — so it is pure."""

    def test_the_implemented_provider_is_servable(self) -> None:
        assert media.unservable_reason(VoiceConfig(tts="cartesia", voice_id="v1")) is None

    def test_an_unimplemented_provider_says_which_are_implemented(self) -> None:
        reason = media.unservable_reason(VoiceConfig(tts="elevenlabs", voice_id="v1"))
        assert reason is not None
        assert "cartesia" in reason

    def test_a_bundle_with_no_voice_is_unservable(self) -> None:
        # Refused rather than answered: `voice_id=""` reaches the synthesiser as a request
        # to speak with no voice, which is a call that connects and then says nothing.
        reason = media.unservable_reason(VoiceConfig(tts="cartesia", voice_id="   "))
        assert reason is not None
        assert "voice_id" in reason

    def test_a_placeholder_voice_is_flagged_but_not_refused(self) -> None:
        # Every shipped bundle still carries one, so refusing would refuse every call.
        voice = VoiceConfig(tts="cartesia", voice_id="PLACEHOLDER_CARTESIA_VOICE_ID")
        assert media.unservable_reason(voice) is None
        assert media.looks_unconfigured(voice)

    def test_a_real_voice_id_is_not_flagged(self) -> None:
        assert not media.looks_unconfigured(VoiceConfig(tts="cartesia", voice_id="a1b2c3"))


@pytest.fixture
def persona_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(media, "PERSONA_DIR", tmp_path)
    return tmp_path


class TestTheRealtimeStackIsOptional:
    def test_the_module_imports_without_it(self) -> None:
        # The safety suite — filter, fiction pack, state machine, conversation — has to
        # run with none of the media dependencies installed.
        assert media.CLIP_SAMPLE_RATE == 8000

    def test_using_it_without_it_installed_says_what_to_install(self) -> None:
        pytest.importorskip  # noqa: B018 - documents that this test assumes absence
        try:
            import pipecat  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("the media extra is installed in this environment")

        with pytest.raises(RuntimeError, match=r"\[media\]"):
            media._import_pipecat()


class TestTheImportedStackIsNamedConsistently:
    """`_import_pipecat` lists 17 names twice: once as imports, once as namespace keys.

    A mismatch (`CartesiaTTSService=DeepgramFluxSTTService`) is not a syntax error, not a
    ruff error, and invisible to every other test — it surfaces as a broken live call.
    This is the one check that can see it, and it only runs where the extra is installed.
    """

    def test_every_attribute_is_the_type_its_key_names(self) -> None:
        if importlib.util.find_spec("pipecat") is None:
            pytest.skip("the media extra is not installed")

        stack = media._import_pipecat()
        mismatched = {
            key: getattr(value, "__name__", f"<no __name__: {value!r}>")
            for key, value in vars(stack).items()
            # SoundfileMixer is the one optional entry and may legitimately be None.
            if not (key == "SoundfileMixer" and value is None)
            and getattr(value, "__name__", None) != key
        }
        assert not mismatched, f"namespace keys disagree with the types they name: {mismatched}"



class TestABundleThePipelineCannotVoiceIsRefused:
    """The guard that stops a call being voiced by the wrong provider.

    Driven directly rather than through a socket: `handle_media_socket` starts with
    `_import_pipecat()`, so with the media extra absent nothing downstream is reachable
    through the public entry point. `_serve_call` takes `pc` by injection and the guard runs
    before the first `pc.` attribute access, so `pc=None` is enough — and that is the point,
    because the invariant is that no transport, serializer, or model client is constructed.
    """

    @staticmethod
    def _serve(persona_id: str) -> tuple[FakeSocket, CallRegistry]:
        socket = FakeSocket()
        registry = CallRegistry(max_concurrent=1)
        admission = reserve_call(registry, "CA1", persona_id=persona_id)
        assert admission.call is not None
        registry.attach_stream("CA1")

        asyncio.run(
            media._serve_call(
                socket,
                pc=None,  # nothing may be constructed from it
                settings=Settings(deepgram_api_key="dg", cartesia_api_key="ct"),
                registry=registry,
                allowlist=None,
                call=admission.call,
                call_sid="CA1",
                stream_sid="MZ1",
                custom={},
            )
        )
        return socket, registry

    def test_a_persona_written_for_another_provider_gets_no_pipeline(
        self, unservable_persona: str
    ) -> None:
        socket, registry = self._serve(unservable_persona)

        assert socket.closed == [1011]
        call = registry.get("CA1")
        assert call is not None
        assert call.end_reason is EndReason.PIPELINE_ERROR
        # The whole point: nothing was built. `pc=None` would have raised on any use.
        assert not call.voicemail_promised, "nothing promised this caller a voicemail"

    def test_a_servable_persona_is_not_refused_by_the_guard(self) -> None:
        # Proves the guard discriminates rather than refusing everything: marjorie gets
        # past it and fails later, on `pc=None`, which is where construction begins.
        with pytest.raises(AttributeError):
            self._serve("marjorie")


class TestSoundPackLoading:
    def test_a_well_formed_clip_loads(self, persona_root: Path) -> None:
        write_wav(persona_root / "marjorie" / media.AUDIO_SUBDIR / "ok.wav")
        clip = media.load_clip_pcm("marjorie", "ok.wav")
        assert clip is not None
        assert clip.sample_rate == media.CLIP_SAMPLE_RATE
        assert clip.pcm

    def test_a_missing_clip_is_not_an_error(self, persona_root: Path) -> None:
        # The sound pack is rendered separately from the code; a call must survive an
        # asset that has not been uploaded yet.
        assert media.load_clip_pcm("marjorie", "not_there.wav") is None

    def test_a_clip_at_the_wrong_sample_rate_is_refused(self, persona_root: Path) -> None:
        # Accepting it would mean resampling on the live audio path, which is exactly
        # what the fixed rate exists to avoid.
        write_wav(persona_root / "marjorie" / media.AUDIO_SUBDIR / "wrong.wav", rate=16000)
        assert media.load_clip_pcm("marjorie", "wrong.wav") is None

    def test_a_stereo_clip_is_refused(self, persona_root: Path) -> None:
        write_wav(persona_root / "marjorie" / media.AUDIO_SUBDIR / "stereo.wav", channels=2)
        assert media.load_clip_pcm("marjorie", "stereo.wav") is None

    def test_an_eight_bit_clip_is_refused(self, persona_root: Path) -> None:
        write_wav(persona_root / "marjorie" / media.AUDIO_SUBDIR / "eight.wav", width=1)
        assert media.load_clip_pcm("marjorie", "eight.wav") is None

    def test_a_corrupt_file_is_not_an_error(self, persona_root: Path) -> None:
        path = persona_root / "marjorie" / media.AUDIO_SUBDIR / "junk.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"this is not a wav file at all")
        assert media.load_clip_pcm("marjorie", "junk.wav") is None

    def test_a_directory_where_a_clip_should_be_is_not_an_error(self, persona_root: Path) -> None:
        (persona_root / "marjorie" / media.AUDIO_SUBDIR / "adir.wav").mkdir(parents=True)
        assert media.load_clip_pcm("marjorie", "adir.wav") is None
