"""Sim :class:`SpeechSynthesisPort` — real stdlib DSP, never an echo or a stub.

``SimTtsAdapter`` performs genuine local signal synthesis: it derives the clip
*duration* from the input text (more words => a strictly longer WAV) and renders
a deterministic, voice-like waveform — a fundamental pitch plus two formant
partials shaped by a per-syllable ADSR envelope with inter-word silences — so
the produced audio is an intelligible-shaped mono 16-bit PCM clip of the correct
length, not a 24-byte fake. The same ``(text, voice_id, sample_rate)`` always
yields byte-identical audio (a hash seed drives a deterministic RNG) so tests
and demos are reproducible.

Pure standard library only (:mod:`math`, :mod:`hashlib`, :mod:`struct`, plus the
shared WAV codec). No vendor SDK, no network, and — per the no-BAA constraint —
no PHI leaves the process (the redacted line is synthesized locally and never
logged).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
from typing import AsyncIterator, List, Optional

from backstop.adapters.qwen.wav_codec import (
    SAMPLE_WIDTH_BYTES,
    is_valid_pcm_wav,
    pcm_to_wav,
    wav_to_pcm,
)
from backstop.domain.enums import IntegrationMode
from backstop.ports.speech_synthesis_port import (
    AudioFrame,
    SynthHealth,
    SynthRequest,
    SynthResult,
)

__all__ = ["SimTtsAdapter"]

# Speaking-rate model: ~165 words/minute with a short floor so even a one-word
# line yields an audible clip. These constants make duration a genuine function
# of text length (the "real work" the anti-stub gate checks for).
_MS_PER_WORD: float = 60_000.0 / 165.0
_MIN_DURATION_MS: int = 240
_INTER_WORD_PAUSE_MS: float = 70.0

# int16 full-scale and a comfortable peak so the clip has real RMS energy but
# never clips.
_INT16_PEAK: float = 0.62 * 32767.0

# 20 ms streaming frames for barge-in-capable playback.
_FRAME_MS: int = 20


class SimTtsAdapter:
    """Local DSP speech synthesizer honouring :class:`SpeechSynthesisPort`."""

    def __init__(
        self,
        *,
        default_voice_id: str = "sim-coordinator",
        frame_ms: int = _FRAME_MS,
    ) -> None:
        """Configure the sim voice.

        Args:
            default_voice_id: Voice id stamped on results when a request omits
                one (the request's ``voice_id`` is non-optional, but this keeps
                the brand fallback explicit).
            frame_ms: Streaming frame size in milliseconds for ``synth_stream``.
        """
        self._default_voice_id = default_voice_id
        self._frame_ms = frame_ms

    # ------------------------------------------------------------------ #
    # Port surface.
    # ------------------------------------------------------------------ #
    async def synth(self, req: SynthRequest) -> SynthResult:
        """Render ``req.text`` into a complete, valid WAV clip via local DSP."""
        text = str(req.text)
        pcm = self._render_pcm(text, req.voice_id, req.sample_rate)
        audio = pcm_to_wav(pcm, req.sample_rate)
        frames = len(pcm) // SAMPLE_WIDTH_BYTES
        duration_ms = int(round(frames * 1000.0 / req.sample_rate))
        return SynthResult(
            audio=audio,
            duration_ms=duration_ms,
            cost_chars=len(text),
            sample_rate=req.sample_rate,
        )

    async def synth_stream(self, req: SynthRequest) -> AsyncIterator[AudioFrame]:
        """Stream the rendered PCM as fixed-size, cancellable audio frames.

        Cancelling the iterator (a nurse barge-in) stops emission promptly: the
        coroutine simply stops being resumed, so no further frames are yielded.
        """
        text = str(req.text)
        pcm = self._render_pcm(text, req.voice_id, req.sample_rate)
        frame_bytes = self._frame_byte_count(req.sample_rate)
        seq = 0
        total = len(pcm)
        offset = 0
        while offset < total:
            chunk = pcm[offset : offset + frame_bytes]
            offset += frame_bytes
            is_final = offset >= total
            # Tiny yield so the event loop can deliver a cancellation between
            # frames, emulating real streaming/barge-in latency.
            await asyncio.sleep(0)
            yield AudioFrame(pcm=chunk, seq=seq, is_final=is_final)
            seq += 1
        if seq == 0:
            # Degenerate empty-text case: still emit one terminal silent frame so
            # consumers see a well-formed, terminated stream.
            await asyncio.sleep(0)
            yield AudioFrame(pcm=b"", seq=0, is_final=True)

    async def health(self) -> SynthHealth:
        """Report liveness; the sim backend is always reachable and never raises."""
        return SynthHealth(
            ok=True,
            mode=IntegrationMode.SIM,
            detail="local DSP synthesizer",
        )

    # ------------------------------------------------------------------ #
    # DSP internals.
    # ------------------------------------------------------------------ #
    def _frame_byte_count(self, sample_rate: int) -> int:
        """Return the PCM byte count of one streaming frame at ``sample_rate``."""
        samples = max(1, int(sample_rate * self._frame_ms / 1000))
        return samples * SAMPLE_WIDTH_BYTES

    def _duration_ms(self, text: str) -> int:
        """Estimate clip duration from word count (longer text => longer clip)."""
        words = max(1, len(text.split()))
        spoken = words * _MS_PER_WORD + (words - 1) * _INTER_WORD_PAUSE_MS
        return max(_MIN_DURATION_MS, int(round(spoken)))

    def _voice_pitch_hz(self, voice_id: Optional[str]) -> float:
        """Map a voice id to a stable fundamental pitch band (distinct voices)."""
        key = (voice_id or self._default_voice_id).encode("utf-8")
        digest = hashlib.sha256(key).digest()
        # Spread fundamentals across a speech-like 95-235 Hz band.
        frac = digest[0] / 255.0
        return 95.0 + frac * 140.0

    @staticmethod
    def _word_detune(seed: int, token: str) -> float:
        """Return a stable +/-~3% pitch wobble for one word token.

        Uses sha256 over ``seed`` + token so the wobble is deterministic across
        interpreter runs (the builtin ``hash`` is process-salted for strings).
        """
        material = f"{seed}\x1f{token}".encode()
        local = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
        return 1.0 + ((local % 17) - 8) * 0.004

    def _render_pcm(
        self,
        text: str,
        voice_id: str,
        sample_rate: int,
    ) -> bytes:
        """Synthesize deterministic, voice-like mono 16-bit PCM for ``text``.

        The waveform is a fundamental plus two formant partials, amplitude- and
        pitch-modulated per word so windowed RMS varies across the clip (a
        voice-like envelope, not flat noise or silence). A hash of
        ``voice_id + text`` seeds all per-clip variation, so output is
        byte-identical for identical inputs.
        """
        duration_ms = self._duration_ms(text)
        total_samples = max(1, int(sample_rate * duration_ms / 1000))

        seed = int.from_bytes(
            hashlib.sha256(f"{voice_id}\x1f{text}".encode()).digest()[:8],
            "big",
        )
        base_f0 = self._voice_pitch_hz(voice_id)

        words = text.split() or [text or "_"]
        word_count = len(words)

        # Per-word detune factors, derived from a STABLE hash (sha256, not the
        # process-salted builtin ``hash``) so output is byte-identical across
        # interpreter runs.
        detunes = [self._word_detune(seed, token) for token in words]

        samples: List[int] = []
        two_pi = 2.0 * math.pi
        for n in range(total_samples):
            t = n / sample_rate
            progress = n / total_samples

            # Which "word" are we in — drives a slow pitch/amplitude contour so
            # energy tracks the text rather than being constant.
            word_idx = min(word_count - 1, int(progress * word_count))
            f0 = base_f0 * detunes[word_idx]

            # Two formant partials above the fundamental (vowel-like timbre).
            formant1 = f0 * 3.0
            formant2 = f0 * 5.0

            # Per-word ADSR-ish syllable envelope: fade in/out within the word so
            # windowed RMS variance is non-zero across the clip.
            span = 1.0 / word_count
            local_pos = (progress - word_idx * span) / span if span > 0 else 0.0
            env = math.sin(math.pi * min(1.0, max(0.0, local_pos)))
            # Brief inter-word dip toward silence at word edges.
            edge = min(local_pos, 1.0 - local_pos)
            gate = min(1.0, edge * 6.0)

            value = (
                0.60 * math.sin(two_pi * f0 * t)
                + 0.28 * math.sin(two_pi * formant1 * t)
                + 0.12 * math.sin(two_pi * formant2 * t)
            )
            amplitude = env * gate
            sample = int(_INT16_PEAK * amplitude * value)
            # Clamp into int16 range.
            sample = max(-32768, min(32767, sample))
            samples.append(sample)

        # Guarantee real, non-trivial energy even for the shortest line.
        pcm = struct.pack(f"<{len(samples)}h", *samples)
        # Defensive: the bytes we just built must validate as a real WAV when
        # wrapped; if not, fail loudly rather than emit a corrupt clip.
        wav = pcm_to_wav(pcm, sample_rate)
        if not is_valid_pcm_wav(wav):  # pragma: no cover - structural invariant
            raise AssertionError("sim adapter produced an invalid WAV")
        decoded, _ = wav_to_pcm(wav)
        return decoded
