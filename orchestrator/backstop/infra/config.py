"""Frozen application settings — the ONLY reader of ``os.environ``.

This module is the single source of truth for configuration. The architecture
guard (``tests/arch/test_layering.py``) statically asserts that no other file in
``orchestrator/backstop`` references ``os.environ`` / ``os.getenv``; all runtime
config flows through the immutable :class:`Settings` object built here. Every
sponsor key, per-port ``*_MODE`` flag, security secret, CORS allowlist, upload
cap and TTL lives on one frozen model that fails fast on invalid input.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Public placeholder secrets that must never reach a real deployment.
_DEV_AUTH_SECRET = "dev-insecure-change-me"
_DEV_LIVEKIT_SECRET = "dev-livekit-sim-secret"

# Per-integration mode literal. ``real`` uses the vendor SDK/HTTP adapter;
# ``sim`` uses the real-local-work adapter. Stored as a plain ``str`` constrained
# by a validator so Python 3.9 stays free of bare-union runtime types.
_MODES: Tuple[str, ...] = ("real", "sim")

# Names of the per-port mode fields on :class:`Settings`, keyed by the logical
# port name used by ``Settings.mode_for``.
_PORT_MODE_FIELDS: Dict[str, str] = {
    "routing": "pavo_mode",
    "retrieval": "moss_mode",
    "gateway": "tfy_mode",
    "redaction": "tfy_mode",
    "audit": "tfy_mode",
    "cost": "tfy_mode",
    "parser": "unsiloed_mode",
    "reasoning": "minimax_mode",
    "speech": "qwen_mode",
    "transport": "livekit_mode",
    "gate": "aws_mode",
}


class Settings(BaseSettings):
    """Immutable, validated application configuration.

    Frozen (``frozen=True``) so a built ``Settings`` cannot be mutated after the
    composition root reads it. Construction fails fast via pydantic validation
    on any malformed value (bad base URL, out-of-range cap, wildcard CORS).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ── Backstop service ────────────────────────────────────────────
    backstop_api_key: Optional[str] = Field(default=None, alias="BACKSTOP_API_KEY")
    backstop_mode: str = Field(default="sim", alias="BACKSTOP_MODE")

    # ── Auth / security ──────────────────────────────────────────
    backstop_auth_secret: str = Field(
        default="dev-insecure-change-me", alias="BACKSTOP_AUTH_SECRET"
    )
    backstop_auth_issuer: str = Field(default="backstop", alias="BACKSTOP_AUTH_ISSUER")
    cors_allow_origins: List[str] = Field(
        default_factory=lambda: ["https://localhost:8000"],
        alias="BACKSTOP_CORS_ALLOW_ORIGINS",
    )

    # ── LiveKit (VoiceTransportPort) ──────────────────────────────────
    livekit_url: Optional[str] = Field(default=None, alias="LIVEKIT_URL")
    livekit_api_key: Optional[str] = Field(default=None, alias="LIVEKIT_API_KEY")
    livekit_api_secret: Optional[str] = Field(default=None, alias="LIVEKIT_API_SECRET")
    livekit_sim_secret: str = Field(default="dev-livekit-sim-secret", alias="LIVEKIT_SIM_SECRET")
    livekit_mode: str = Field(default="sim", alias="LIVEKIT_MODE")

    # ── Moss (RetrievalPort) ───────────────────────────────────────
    moss_project_id: Optional[str] = Field(default=None, alias="MOSS_PROJECT_ID")
    moss_project_key: Optional[str] = Field(default=None, alias="MOSS_PROJECT_KEY")
    moss_base_url: str = Field(default="https://api.usemoss.dev", alias="MOSS_BASE_URL")
    moss_timeout_s: float = Field(default=2.0, gt=0.0, alias="MOSS_TIMEOUT_S")
    moss_mode: str = Field(default="sim", alias="MOSS_MODE")

    # ── TrueFoundry (LLMGatewayPort + Redaction/Audit/Cost) ──────────────────
    truefoundry_api_key: Optional[str] = Field(default=None, alias="TRUEFOUNDRY_API_KEY")
    truefoundry_base_url: str = Field(
        default="https://llm-gateway.truefoundry.com", alias="TRUEFOUNDRY_BASE_URL"
    )
    truefoundry_inference_path: str = Field(
        default="/openai/v1", alias="TRUEFOUNDRY_INFERENCE_PATH"
    )
    truefoundry_default_model: str = Field(
        default="openai-main/gpt-4o-mini", alias="TRUEFOUNDRY_DEFAULT_MODEL"
    )
    tfy_mode: str = Field(default="sim", alias="TFY_MODE")

    # ── Unsiloed (DenialParserPort) ────────────────────────────────────
    unsiloed_api_key: Optional[str] = Field(default=None, alias="UNSILOED_API_KEY")
    unsiloed_base_url: str = Field(
        default="https://prod.visionapi.unsiloed.ai", alias="UNSILOED_BASE_URL"
    )
    unsiloed_confidence_floor: float = Field(
        default=0.85, ge=0.0, le=1.0, alias="UNSILOED_CONFIDENCE_FLOOR"
    )
    unsiloed_mode: str = Field(default="sim", alias="UNSILOED_MODE")

    # ── MiniMax (ReasoningPort) ──────────────────────────────────────
    minimax_api_key: Optional[str] = Field(default=None, alias="MINIMAX_API_KEY")
    minimax_group_id: Optional[str] = Field(default=None, alias="MINIMAX_GROUP_ID")
    minimax_base_url: str = Field(default="https://api.minimax.io/v1", alias="MINIMAX_BASE_URL")
    minimax_model: str = Field(default="MiniMax-Text-01", alias="MINIMAX_MODEL")
    minimax_route: str = Field(default="native", alias="MINIMAX_ROUTE")
    minimax_mode: str = Field(default="sim", alias="MINIMAX_MODE")

    # ── Qwen / DashScope (SpeechSynthesisPort) ───────────────────────────
    qwen_api_key: Optional[str] = Field(default=None, alias="QWEN_API_KEY")
    dashscope_api_key: Optional[str] = Field(default=None, alias="DASHSCOPE_API_KEY")
    qwen_region: str = Field(default="intl", alias="QWEN_REGION")
    qwen_tts_model: str = Field(default="qwen-tts", alias="QWEN_TTS_MODEL")
    qwen_voice_id: Optional[str] = Field(default=None, alias="QWEN_VOICE_ID")
    qwen_mode: str = Field(default="sim", alias="QWEN_MODE")

    # ── AWS (ConcurrencyGatePort) ────────────────────────────────────
    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: Optional[str] = Field(default=None, alias="AWS_SESSION_TOKEN")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_mode: str = Field(default="sim", alias="BACKSTOP_AWS_MODE")

    # ── PAVO (RoutingPort — always real policy; backend selection only) ──────
    pavo_adapter_impl: str = Field(default="torch", alias="PAVO_ADAPTER_IMPL")
    pavo_weights_path: Optional[str] = Field(default=None, alias="PAVO_WEIGHTS_PATH")
    pavo_weights_npz: Optional[str] = Field(default=None, alias="PAVO_WEIGHTS_NPZ")
    pavo_bench_root: Optional[str] = Field(default=None, alias="PAVO_BENCH_ROOT")
    pavo_device: str = Field(default="cpu", alias="PAVO_DEVICE")
    pavo_mode: str = Field(default="sim", alias="PAVO_MODE")

    # ── Persistence / storage ───────────────────────────────────────
    database_url: str = Field(default="sqlite+aiosqlite:///./backstop.db", alias="DATABASE_URL")
    artifact_dir: str = Field(default="./artifacts", alias="BACKSTOP_ARTIFACT_DIR")
    price_table_path: Optional[str] = Field(default=None, alias="BACKSTOP_PRICE_TABLE_PATH")

    # ── Limits ───────────────────────────────────────────────────
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0, alias="BACKSTOP_MAX_UPLOAD_BYTES")
    max_concurrency: int = Field(default=200, gt=0, alias="BACKSTOP_MAX_CONCURRENCY")
    file_ttl_s: int = Field(default=3600, gt=0, alias="BACKSTOP_FILE_TTL_S")

    @field_validator(
        "backstop_mode",
        "livekit_mode",
        "moss_mode",
        "tfy_mode",
        "unsiloed_mode",
        "minimax_mode",
        "qwen_mode",
        "aws_mode",
        "pavo_mode",
    )
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        """Constrain every ``*_MODE`` flag to ``{real, sim}`` (lower-cased)."""
        lowered = value.strip().lower()
        if lowered not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, got {value!r}")
        return lowered

    @field_validator(
        "moss_base_url",
        "truefoundry_base_url",
        "unsiloed_base_url",
        "minimax_base_url",
        mode="after",
    )
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        """Reject malformed base URLs at load (fail-fast config)."""
        if not (value.startswith("https://") or value.startswith("http://")):
            raise ValueError(f"base URL must be http(s)://, got {value!r}")
        return value.rstrip("/")

    @field_validator("cors_allow_origins", mode="after")
    @classmethod
    def _reject_wildcard_cors(cls, value: List[str]) -> List[str]:
        """Reject a wildcard CORS allowlist (closes audit finding #6)."""
        if any(origin.strip() == "*" for origin in value):
            raise ValueError("CORS allowlist must not contain a wildcard '*'")
        return value

    @model_validator(mode="after")
    def _reject_dev_secrets_outside_sim(self) -> "Settings":
        """Fail fast: a non-``sim`` deployment must not run on the public dev
        secrets. The HS256 ``backstop_auth_secret`` gates every authenticated
        route and signs file-download tokens — shipping the known default would
        let anyone mint a valid JWT (full auth bypass + signed-URL forgery)."""
        if self.backstop_mode != "sim":
            if not self.backstop_auth_secret or self.backstop_auth_secret == _DEV_AUTH_SECRET:
                raise ValueError(
                    "BACKSTOP_AUTH_SECRET must be a strong non-default value when "
                    "BACKSTOP_MODE != 'sim'"
                )
            if self.livekit_sim_secret == _DEV_LIVEKIT_SECRET:
                raise ValueError(
                    "LIVEKIT_SIM_SECRET must be overridden when BACKSTOP_MODE != 'sim'"
                )
        return self

    def mode_for(self, port: str) -> str:
        """Return the configured mode (``real``/``sim``) for a logical port.

        Args:
            port: Logical port name, e.g. ``"retrieval"`` or ``"gateway"``.

        Returns:
            The resolved mode string for that port.

        Raises:
            KeyError: If ``port`` is not a known mode-bearing port name.
        """
        field_name = _PORT_MODE_FIELDS[port]
        return str(getattr(self, field_name))


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Build (once) and return the process-wide frozen :class:`Settings`.

    Cached so the single ``os.environ`` read happens exactly one time. Raises
    ``pydantic.ValidationError`` on invalid configuration (fail-fast).
    """
    return Settings()
