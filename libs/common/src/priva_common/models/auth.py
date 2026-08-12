from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .mcp import McpServerSummary
from .llm_profiles import LlmProfileSummary
from .resource import QuickAction
from .user_env import UserEnvSettings


class UserRecord(BaseModel):
    username: str
    password_hash: str
    role: str = "user"
    api_key: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Phase-1 data-spine additions (additive/defaulted — file-backed callers ignore them).
    # account_id is the minted UUID PK; the request layer stays username-keyed in Phase 1.
    # api_key_lookup (HMAC) is an internal column and is NEVER surfaced on this DTO.
    account_id: str | None = None
    status: str = "active"
    agent_runner_type: str = "auto_scale"  # auto_scale | persistent
    feishu_user_id: str | None = None
    feishu_display_name: str | None = None
    # Digest of password_hash, computed by data-spine. Over gRPC password_hash is
    # always "" (never serialized), so this is the ONLY way the control-plane can
    # tell whether the credential behind a session has since changed.
    password_epoch: str | None = None


class UsageCounts(BaseModel):
    sessions: int = 0
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    active_days: int = 0


class UsageStats(BaseModel):
    all: UsageCounts = Field(default_factory=UsageCounts)
    last_30d: UsageCounts = Field(default_factory=UsageCounts)
    last_7d: UsageCounts = Field(default_factory=UsageCounts)


class HeatmapBucket(BaseModel):
    date: str
    count: int


class ModelUsage(BaseModel):
    model: str
    runs: int
    input_tokens: int
    output_tokens: int
    percentage: float


class SkillUsage(BaseModel):
    skill: str
    count: int


class DailyModelTokens(BaseModel):
    date: str
    by_model: dict[str, int]


class UserPublic(BaseModel):
    username: str
    role: str
    api_key: str | None = None
    workspace: str | None = None
    created_at: datetime
    updated_at: datetime
    # lifecycle (admin table STATUS column): active | disabled | offboarding | purged
    status: str = "active"
    # runner type + resource spec (admin table RUNNER column + edit drawer prefill)
    agent_runner_type: str = "auto_scale"
    cpu_cores: float | None = None
    memory_mb: int | None = None
    volume_gb: int | None = None
    stats: UsageStats | None = None
    heatmap: list[HeatmapBucket] | None = None
    model_usage: list[ModelUsage] | None = None
    daily_model_tokens: list[DailyModelTokens] | None = None
    favorite_model: str | None = None
    current_streak: int = 0
    longest_streak: int = 0
    peak_hour: int | None = None
    tagline: str | None = None
    skill_usage: list[SkillUsage] | None = None
    explored_skills: int = 0
    skill_invocations: int = 0


class UserOverviewBootstrap(BaseModel):
    quickactions: list[QuickAction] = Field(default_factory=list)
    llm_profiles: list[LlmProfileSummary] = Field(default_factory=list)
    default_profile_id: str | None = None
    mcp_servers: list[McpServerSummary] = Field(default_factory=list)
    active_cwd: str | None = None
    recent_activities: list[dict[str, Any]] = Field(default_factory=list)


class UserOverviewResponse(BaseModel):
    """Per-user usage overview — agent-runtime state served by the agent-runner
    (reads the per-account /workspace PVC). Formerly embedded in /api/auth/me on
    the control-panel, which could not see the PVC and returned zeros."""

    stats: UsageStats | None = None
    heatmap: list[HeatmapBucket] | None = None
    model_usage: list[ModelUsage] | None = None
    daily_model_tokens: list[DailyModelTokens] | None = None
    favorite_model: str | None = None
    current_streak: int = 0
    longest_streak: int = 0
    peak_hour: int | None = None
    tagline: str | None = None
    skill_usage: list[SkillUsage] = Field(default_factory=list)
    explored_skills: int = 0
    skill_invocations: int = 0
    bootstrap: UserOverviewBootstrap = Field(default_factory=UserOverviewBootstrap)


class UserCreate(BaseModel):
    username: str
    password: str | None = None
    role: str = "user"
    env: UserEnvSettings | None = None


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None
    api_key: str | None = None
    env: UserEnvSettings | None = None
    # runtime edit (admin edit drawer) — change runner type / resource spec live
    agent_runner_type: str | None = None
    cpu_cores: float | None = None
    memory_mb: int | None = None
    volume_gb: int | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class SetupRequest(BaseModel):
    username: str
    password: str
    env: UserEnvSettings | None = None


class SetupStatus(BaseModel):
    needs_setup: bool


class RegisterRequest(BaseModel):
    """Public self-registration (wizard submit). The user requests a runner type +
    resource spec; an admin approves. Bounds keep the request sane before approval."""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8)
    display_name: str | None = Field(default=None, max_length=128)
    runner_type: str = "auto_scale"
    cpu_cores: float = Field(default=1.0, ge=0.512, le=4)
    memory_mb: int = Field(default=2048, ge=1024, le=8192)
    volume_gb: int = Field(default=1, ge=1, le=1024)
    note: str | None = Field(default=None, max_length=1000)


class RegisterResponse(BaseModel):
    status: str = "pending_approval"
    request_id: str


def password_epoch(password_hash: str | None) -> str:
    """Opaque digest of a stored password hash, used to bind a session to the
    credential it was issued under.

    Lives here rather than in the control-panel because data-spine computes it —
    the bcrypt hash itself is deliberately never serialized over the wire, so the
    control-plane cannot derive this itself and must be handed the digest.
    A digest of an already-irreversible hash leaks nothing about the password.
    """
    import hashlib

    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


class TokenPayload(BaseModel):
    sub: str
    role: str
    exp: float
    # Hardening claims. `typ` stops a token minted for one purpose being replayed
    # as another; `pwd` is a digest of the stored password hash, so changing the
    # password invalidates every token issued before it (see services/auth.py).
    typ: str | None = None
    pwd: str | None = None
    jti: str | None = None
    iat: float | None = None


class ApiKeyResponse(BaseModel):
    has_key: bool
    api_key: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
