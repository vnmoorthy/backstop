"""Contract suite for ``SpeechSynthesisPort`` — sim vs Qwen/DashScope (M10).

Both adapters are instantiated and asserted to honour the same port. The real
DashScope adapter is driven through an ``httpx.MockTransport`` so NO network is
ever touched and a missing vendor SDK cannot block the gate. The headline
``test_port_contract__both_adapters_return_valid_playable_wav`` is the
anti-stub / anti-echo gate: a fixed request must yield a ``wave.open()``-parseable
mono/16-bit WAV of the requested sample rate, with frames and >1KB of audio —
identically for both adapters via the shared port.
"""

from __future__ import annotations

import base64
import io
import wave

import httpx
import pytest

from backstop.adapters.qwen.errors import SynthError, SynthErrorKind
from backstop.adapters.qwen.qwen_tts_adapter import QwenTtsAdapter
from backstop.adapters.qwen.sim_tts_adapter import SimTtsAdapter
from backstop.adapters.qwen.wav_codec import is_valid_pcm_wav, pcm_to_wav, wav_to_pcm
from backstop.domain.enums import IntegrationMode
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.speech_synthesis_port import (
    SpeechSynthesisPort,
    SynthRequest,
    SynthResult,
)

SAMPLE_RATE = 24000
SHORT_TEXT = "three words here"
LONG_TEXT = (
    "Hello, this is the appeals coordinator following up on a denied claim. "
    "The denial cites a missing prior authorization, but our records show the "
    "authorization on file with the rendering provider. I am calling to request "
    "a reprocessing of this claim under the policy provision that governs urgent "
    "specialist referrals for the member named on the account on the date noted."
)
AUDIO_URL = "https://dashscope-intl.aliyuncs.com/audio/canned-clip.wav"


def redacted(text: str) -> RedactedText:
    """Mint a RedactedText for the egress-safe synth input (test-only)."""
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN)


def make_request(text: str = SHORT_TEXT, sample_rate: int = SAMPLE_RATE) -> SynthRequest:
    """Build a SynthRequest for a redacted line."""
    return SynthRequest(text=redacted(text), voice_id="Cherry", sample_rate=sample_rate)


def canned_wav(sample_rate: int = SAMPLE_RATE, n_samples: int = 6000) -> bytes:
    """Build a real, non-trivial PCM WAV (>1KB) the mocked vendor can return."""
    import math
    import struct

    samples = [
        int(8000 * math.sin(2 * math.pi * 180.0 * (n / sample_rate)))
        for n in range(n_samples)
    ]
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    return pcm_to_wav(pcm, sample_rate)


def url_handler(sample_rate: int = SAMPLE_RATE) -> httpx.MockTransport:
    """MockTransport: generation returns an audio URL; the URL serves a WAV."""
    wav = canned_wav(sample_rate)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            assert request.headers["Authorization"] == "Bearer test-key"
            body = request.read().decode()
            assert "three words here" in body or "appeals coordinator" in body
            return httpx.Response(
                200,
                json={"output": {"audio": {"url": AUDIO_URL}}},
            )
        if str(request.url) == AUDIO_URL:
            return httpx.Response(200, content=wav)
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handle)


def real_adapter_with(transport: httpx.MockTransport) -> QwenTtsAdapter:
    """Build a real adapter wired to a mocked async HTTP client."""
    client = httpx.AsyncClient(transport=transport)
    return QwenTtsAdapter(api_key="test-key", http=client, region="intl")


# --------------------------------------------------------------------------- #
# Substitutability: both adapters satisfy the runtime-checkable port.
# --------------------------------------------------------------------------- #
def test_both_adapters_satisfy_port_protocol() -> None:
    sim = SimTtsAdapter()
    real = real_adapter_with(url_handler())
    assert isinstance(sim, SpeechSynthesisPort)
    assert isinstance(real, SpeechSynthesisPort)


# --------------------------------------------------------------------------- #
# THE anti-stub gate — parametrized identically over both adapters.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("which", ["sim", "real"])
async def test_port_contract__both_adapters_return_valid_playable_wav(which: str) -> None:
    if which == "sim":
        adapter: SpeechSynthesisPort = SimTtsAdapter()
    else:
        adapter = real_adapter_with(url_handler())

    req = make_request(LONG_TEXT)
    result = await adapter.synth(req)

    assert isinstance(result, SynthResult)
    assert result.sample_rate == req.sample_rate
    assert result.cost_chars == len(str(req.text))

    # Parses as a RIFF/WAVE PCM stream via stdlib wave.
    with wave.open(io.BytesIO(result.audio), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == req.sample_rate
        nframes = reader.getnframes()
        frames = reader.readframes(nframes)

    assert nframes > 0
    assert len(result.audio) > 1024, "anti-stub gate: WAV must be >1KB, not a fake"
    assert len(frames) > 1024

    # duration_ms matches frames/framerate within tolerance.
    expected_ms = nframes * 1000.0 / req.sample_rate
    assert abs(result.duration_ms - expected_ms) <= 5.0


# --------------------------------------------------------------------------- #
# Sim: genuine length-dependent DSP work.
# --------------------------------------------------------------------------- #
async def test_sim__duration_scales_with_text_length() -> None:
    sim = SimTtsAdapter()
    short = await sim.synth(make_request(SHORT_TEXT))
    long = await sim.synth(make_request(LONG_TEXT))

    assert short.duration_ms < long.duration_ms
    # Strictly more PCM frames for the longer line (not a constant blob).
    short_pcm, _ = wav_to_pcm(short.audio)
    long_pcm, _ = wav_to_pcm(long.audio)
    assert len(long_pcm) > len(short_pcm)


async def test_sim__deterministic_and_voice_dependent() -> None:
    sim = SimTtsAdapter()
    a = await sim.synth(make_request(SHORT_TEXT))
    b = await sim.synth(make_request(SHORT_TEXT))
    assert a.audio == b.audio  # byte-identical for identical inputs

    other_voice = SynthRequest(text=redacted(SHORT_TEXT), voice_id="Ethan", sample_rate=SAMPLE_RATE)
    c = await sim.synth(other_voice)
    assert c.audio != a.audio  # different voice => different audio


async def test_sim__audio_is_not_silence_and_not_constant() -> None:
    import struct

    sim = SimTtsAdapter()
    result = await sim.synth(make_request(LONG_TEXT))
    pcm, _ = wav_to_pcm(result.audio)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)

    n = len(samples)
    rms = (sum(s * s for s in samples) / n) ** 0.5
    assert rms > 200.0, "decoded audio must carry real energy (not silence)"

    # Windowed RMS varies across the clip (voice-like envelope, not flat noise).
    win = n // 8
    energies = []
    for i in range(0, n - win, win):
        chunk = samples[i : i + win]
        energies.append((sum(s * s for s in chunk) / len(chunk)) ** 0.5)
    assert max(energies) - min(energies) > 0.0


async def test_sim__health_reports_sim_mode() -> None:
    health = await SimTtsAdapter().health()
    assert health.ok is True
    assert health.mode is IntegrationMode.SIM


async def test_sim__stream_frames_reassemble_to_synth_pcm() -> None:
    sim = SimTtsAdapter()
    req = make_request(SHORT_TEXT)
    whole = await sim.synth(req)
    whole_pcm, _ = wav_to_pcm(whole.audio)

    frames = [frame async for frame in sim.synth_stream(req)]
    assert [f.seq for f in frames] == list(range(len(frames)))
    assert frames[-1].is_final is True
    assert b"".join(f.pcm for f in frames) == whole_pcm


async def test_sim__stream_cancellation_stops_early() -> None:
    sim = SimTtsAdapter()
    agen = sim.synth_stream(make_request(LONG_TEXT))
    first = await agen.__anext__()
    assert first.seq == 0
    # Nurse barge-in: close the iterator; it must stop without error.
    await agen.aclose()


# --------------------------------------------------------------------------- #
# Real adapter: vendor translation, mocked httpx only (no network).
# --------------------------------------------------------------------------- #
async def test_real__synth_posts_correct_payload_and_returns_wav() -> None:
    captured = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            captured["auth"] = request.headers["Authorization"]
            captured["body"] = request.read().decode()
            captured["host"] = request.url.host
            return httpx.Response(200, json={"output": {"audio": {"url": AUDIO_URL}}})
        return httpx.Response(200, content=canned_wav())

    real = real_adapter_with(httpx.MockTransport(handle))
    result = await real.synth(make_request(SHORT_TEXT))

    import json

    assert captured["auth"] == "Bearer test-key"
    payload = json.loads(captured["body"])
    assert payload["input"]["text"] == SHORT_TEXT
    assert payload["input"]["voice"] == "Cherry"
    assert payload["input"]["language_type"] == "English"
    assert captured["host"] == "dashscope-intl.aliyuncs.com"
    assert is_valid_pcm_wav(result.audio)


async def test_real__region_cn_selects_beijing_host() -> None:
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            seen["host"] = request.url.host
            return httpx.Response(200, json={"output": {"audio": {"url": AUDIO_URL}}})
        return httpx.Response(200, content=canned_wav())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    real = QwenTtsAdapter(api_key="test-key", http=client, region="cn")
    await real.synth(make_request(SHORT_TEXT))
    assert seen["host"] == "dashscope.aliyuncs.com"


async def test_real__inline_base64_branch() -> None:
    wav = canned_wav()
    b64 = base64.b64encode(wav).decode()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"output": {"preview_audio": {"data": b64}}}
        )

    real = real_adapter_with(httpx.MockTransport(handle))
    result = await real.synth(make_request(SHORT_TEXT))
    assert is_valid_pcm_wav(result.audio)
    assert result.audio == wav


async def test_real__expired_url_body_raises_typed_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            return httpx.Response(200, json={"output": {"audio": {"url": AUDIO_URL}}})
        return httpx.Response(200, content=b"<html>Access Denied</html>")

    real = real_adapter_with(httpx.MockTransport(handle))
    with pytest.raises(SynthError) as exc:
        await real.synth(make_request(SHORT_TEXT))
    assert exc.value.kind is SynthErrorKind.MALFORMED_AUDIO


async def test_real__403_url_maps_to_expired_url() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/generation"):
            return httpx.Response(200, json={"output": {"audio": {"url": AUDIO_URL}}})
        return httpx.Response(403, content=b"")

    real = real_adapter_with(httpx.MockTransport(handle))
    with pytest.raises(SynthError) as exc:
        await real.synth(make_request(SHORT_TEXT))
    assert exc.value.kind is SynthErrorKind.EXPIRED_URL


async def test_real__429_maps_to_rate_limit_with_retry_after() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, json={})

    real = real_adapter_with(httpx.MockTransport(handle))
    with pytest.raises(SynthError) as exc:
        await real.synth(make_request(SHORT_TEXT))
    assert exc.value.kind is SynthErrorKind.RATE_LIMIT
    assert exc.value.retry_after == 7.0


async def test_real__401_maps_to_auth_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    real = real_adapter_with(httpx.MockTransport(handle))
    with pytest.raises(SynthError) as exc:
        await real.synth(make_request(SHORT_TEXT))
    assert exc.value.kind is SynthErrorKind.AUTH


async def test_real__health_reports_real_mode() -> None:
    real = real_adapter_with(url_handler())
    health = await real.health()
    assert health.mode is IntegrationMode.REAL
    assert health.ok is True
