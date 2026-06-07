"""Stdlib RIFF/WAVE PCM helpers shared by the Qwen TTS adapters.

One responsibility: turn raw little-endian 16-bit mono PCM into a valid
``wave``-parseable WAV container and back, and validate that arbitrary bytes are
a well-formed mono/16-bit PCM WAV (the anti-stub gate the contract test asserts).
Both the real DashScope adapter (which must validate fetched/decoded bytes) and
the sim DSP adapter (which must emit a real container) depend on this module, so
the codec lives in exactly one place.

Pure standard library: :mod:`io`, :mod:`wave`. No vendor SDK, no network, no PHI.
"""

from __future__ import annotations

import io
import wave
from typing import Tuple

__all__ = [
    "SAMPLE_WIDTH_BYTES",
    "NUM_CHANNELS",
    "pcm_to_wav",
    "wav_to_pcm",
    "is_valid_pcm_wav",
]

# The single audio shape this port speaks: mono, signed 16-bit little-endian PCM.
SAMPLE_WIDTH_BYTES: int = 2
NUM_CHANNELS: int = 1


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a RIFF/WAVE container.

    Args:
        pcm: Little-endian signed 16-bit mono PCM samples (frame count =
            ``len(pcm) // 2``).
        sample_rate: PCM sample rate in Hz to record in the WAV header.

    Returns:
        Complete WAV bytes parseable by the stdlib :mod:`wave` module.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(NUM_CHANNELS)
        writer.setsampwidth(SAMPLE_WIDTH_BYTES)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def wav_to_pcm(audio: bytes) -> Tuple[bytes, int]:
    """Decode a mono/16-bit PCM WAV back to raw PCM and its sample rate.

    Args:
        audio: Complete RIFF/WAVE bytes.

    Returns:
        A ``(pcm, sample_rate)`` pair.

    Raises:
        wave.Error: If ``audio`` is not a parseable WAV stream.
    """
    with wave.open(io.BytesIO(audio), "rb") as reader:
        sample_rate = reader.getframerate()
        pcm = reader.readframes(reader.getnframes())
    return pcm, sample_rate


def is_valid_pcm_wav(audio: bytes) -> bool:
    """Return ``True`` iff ``audio`` is a real mono/16-bit PCM WAV with frames.

    This is the structural gate that rejects expired-URL HTML bodies, empty
    responses and 24-byte fakes: the bytes must parse via :mod:`wave`, declare
    one channel and a 2-byte sample width, and carry at least one frame.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            return (
                reader.getnchannels() == NUM_CHANNELS
                and reader.getsampwidth() == SAMPLE_WIDTH_BYTES
                and reader.getnframes() > 0
            )
    except (wave.Error, EOFError, ValueError):
        return False
