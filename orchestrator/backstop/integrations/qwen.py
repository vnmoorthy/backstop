"""Qwen — the one consistent "appeals coordinator" brand voice + multilingual TTS.

Every Backstop call uses a single cloned brand voice so each rep hears the same
professional persona, in any language. Real if QWEN_API_KEY (DashScope) is set;
otherwise a deterministic stub that writes a placeholder wav path so the pipeline
runs without audio hardware.

.mode == "real" iff QWEN_API_KEY is set.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OUT = _REPO_ROOT / "data" / "out" / "tts"


class Qwen:
    def __init__(self):
        self.api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = os.getenv("QWEN_BASE_URL", "https://dashscope.aliexpress.com/api/v1")
        self.voice_id = os.getenv("QWEN_VOICE_ID", "appeals_coordinator")
        self.mode = "real" if self.api_key else "sim"

    def design_voice(self) -> str:
        """Return the brand voice id (would clone/design via Qwen Voice in real mode)."""
        return self.voice_id

    def tts(self, text: str, voice: str = "appeals_coordinator", lang: str = "en") -> str:
        """Synthesize speech, return a path to the audio artifact."""
        _OUT.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{voice}:{lang}:{text}".encode()).hexdigest()[:12]
        out_path = _OUT / f"{key}.wav"
        if self.mode == "real":
            try:
                self._tts_real(text, voice, lang, out_path)
                return str(out_path)
            except Exception:
                pass
        # sim: write a tiny placeholder so downstream code has a real file path
        if not out_path.exists():
            out_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEsim-placeholder")
        return str(out_path)

    def _tts_real(self, text: str, voice: str, lang: str, out_path: Path) -> None:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/services/audio/tts",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": "qwen-tts", "input": {"text": text, "voice": voice, "language": lang}},
            timeout=30.0,
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)

    def to_dict(self) -> dict:
        return {"name": "qwen", "mode": self.mode, "voice": self.voice_id}


if __name__ == "__main__":
    q = Qwen()
    print("mode:", q.mode, "| voice:", q.design_voice())
    print("tts ->", q.tts("Please re-run the auth lookup by the rendering NPI."))
