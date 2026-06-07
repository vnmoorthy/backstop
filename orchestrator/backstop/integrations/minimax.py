"""MiniMax — the mid-tier reasoning brain + multilingual completion.

This is the model PAVO routes the moderate-complexity turns to (interpreting an
ambiguous denial reason, composing the agent's spoken line). Real if MINIMAX_API_KEY
is set; otherwise a deterministic grounded-template completion so the pipeline runs.

.mode == "real" iff MINIMAX_API_KEY is set.
"""
from __future__ import annotations

import os


class MiniMax:
    def __init__(self):
        self.api_key = os.getenv("MINIMAX_API_KEY")
        self.group_id = os.getenv("MINIMAX_GROUP_ID")
        self.base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
        self.model = os.getenv("MINIMAX_MODEL", "MiniMax-Text-01")
        self.mode = "real" if self.api_key else "sim"

    def complete(self, prompt: str, lang: str = "en") -> str:
        if self.mode == "real":
            try:
                return self._complete_real(prompt, lang)
            except Exception:
                pass
        # deterministic grounded line: echo the actionable instruction, never invent facts
        head = prompt.strip().splitlines()[0][:160] if prompt.strip() else ""
        if lang != "en":
            return f"[minimax:sim:{lang}] {head}"
        return f"[minimax:sim] {head}"

    def _complete_real(self, prompt: str, lang: str) -> str:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/text/chatcompletion_v2",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def to_dict(self) -> dict:
        return {"name": "minimax", "mode": self.mode, "model": self.model}


if __name__ == "__main__":
    mm = MiniMax()
    print("mode:", mm.mode)
    print(mm.complete("Compose the rebuttal: ask the rep to re-run the lookup by rendering NPI."))
