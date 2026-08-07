"""Wire models for user-owned LLM provider profiles.

Profiles deliberately model the Anthropic-compatible surface only.  The
provider protocol is not persisted: the runner talks to the endpoint through
the same Claude Code/Anthropic-compatible contract for every profile.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LlmProfile(BaseModel):
    id: str = Field(min_length=1, max_length=63)
    label: str = Field(min_length=1, max_length=120)
    base_url: str
    auth_token: str
    default_model: str | None = None
    opus_model: str | None = None
    sonnet_model: str | None = None
    haiku_model: str | None = None
    vision_model: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value or "://" not in value:
            raise ValueError("base_url must be an absolute http(s) URL")
        scheme = value.split("://", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        return value


class LlmProfileSummary(BaseModel):
    id: str
    label: str
    base_url: str
    auth_token: str
    auth_token_set: bool
    default_model: str | None = None
    opus_model: str | None = None
    sonnet_model: str | None = None
    haiku_model: str | None = None
    vision_model: str | None = None
    model_count: int | None = None


class LlmProfilesResponse(BaseModel):
    profiles: list[LlmProfileSummary] = Field(default_factory=list)
    default_profile_id: str | None = None


class LlmProfileCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=63)
    label: str = Field(min_length=1, max_length=120)
    base_url: str
    auth_token: str
    default_model: str | None = None
    opus_model: str | None = None
    sonnet_model: str | None = None
    haiku_model: str | None = None
    vision_model: str | None = None


class LlmProfileUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    auth_token: str | None = None
    default_model: str | None = None
    opus_model: str | None = None
    sonnet_model: str | None = None
    haiku_model: str | None = None
    vision_model: str | None = None

class LlmProfileDefaultResponse(BaseModel):
    default_profile_id: str
