"""SpeechSynthesisPort — Qwen / DashScope text-to-speech (L2 port).

Synthesizes the composed spoken line into a valid RIFF/WAVE PCM clip (sim is real
stdlib DSP — a length-correct playable WAV, never a stub). Qwen has no BAA, so the
``text`` field on this egress port is typed ``RedactedText`` and the service
asserts PHI-clean before the port. ``synth_stream`` yields cancellable audio frames
so a nurse can barge in mid-utterance.

This module defines the Protocol plus its request/result DTOs only; concrete
adapters live in ``backstop.adapters.qwen``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from backstop.domain.enums import IntegrationMode
from backstop.domain.redacted import RedactedText

__all__ = [
    "SynthRequest",
    "SynthResult",
    "AudioFrame",
    "SynthHealth",
    "SpeechSynthesisPort",
]


@dataclass(frozen=True)
class SynthRequest:
    """A redacted line to synthesize into speech.

    Attributes:
        text: The redacted line to voice (egress-safe; PHI-clean).
        voice_id: Brand voice identifier.
        sample_rate: Target PCM sample rate in Hz.
        language: BCP-47-style language tag for the voice.
    """

    text: RedactedText
    voice_id: str
    sample_rate: int = 24000
    language: str = "en-US"


@dataclass(frozen=True)
class SynthResult:
    """A synthesized clip plus its duration and char-billing.

    Attributes:
        audio: Complete RIFF/WAVE PCM bytes (parseable by stdlib ``wave``).
        duration_ms: Clip duration in milliseconds (scales with text length).
        cost_chars: Number of billed characters for cost accounting.
        sample_rate: PCM sample rate of ``audio``, in Hz.
    """

    audio: bytes
    duration_ms: int
    cost_chars: int
    sample_rate: int


@dataclass(frozen=True)
class AudioFrame:
    """One streamed PCM frame for cancellable, barge-in-capable playback.

    Attributes:
        pcm: Raw PCM samples for this frame.
        seq: Monotonic frame sequence number.
        is_final: Whether this is the terminal frame of the utterance.
    """

    pcm: bytes
    seq: int
    is_final: bool = False


@dataclass(frozen=True)
class SynthHealth:
    """Liveness snapshot for the synthesis backend (never raises).

    Attributes:
        ok: Whether the backend is reachable and serving.
        mode: Whether the active adapter is real or sim.
        detail: Optional human-readable status detail.
    """

    ok: bool
    mode: IntegrationMode
    detail: Optional[str] = None


@runtime_checkable
class SpeechSynthesisPort(Protocol):
    """Async text-to-speech over redacted lines, with cancellable streaming."""

    async def synth(self, req: SynthRequest) -> SynthResult:
        """Synthesize ``req.text`` into a complete WAV clip.

        Raises:
            SynthError: On auth / rate_limit / expired_url / malformed_audio / timeout.
        """
        ...

    def synth_stream(self, req: SynthRequest) -> AsyncIterator[AudioFrame]:
        """Stream ``req.text`` as cancellable audio frames (nurse barge-in).

        Raises:
            SynthError: On auth / rate_limit / expired_url / malformed_audio / timeout.
        """
        ...

    async def health(self) -> SynthHealth:
        """Return a liveness snapshot; never raises."""
        ...
