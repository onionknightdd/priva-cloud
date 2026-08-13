from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from priva_common.runtime_settings import validate_extra_env


class ModelInfo(BaseModel):
    id: str


class ModelListResponse(BaseModel):
    models: list[ModelInfo] = Field(default_factory=list)


class QuickAction(BaseModel):
    name: str
    prompt: str
    icon: str | None = None


class QuickActionListResponse(BaseModel):
    quickactions: list[QuickAction] = Field(default_factory=list)


class QuickActionUpdateRequest(BaseModel):
    quickactions: list[QuickAction]


class VisionModelResponse(BaseModel):
    vision_model: str | None = None


class VisionModelUpdateRequest(BaseModel):
    vision_model: str | None = None


class RecapSettingResponse(BaseModel):
    recap_enabled: bool = True


class RecapSettingUpdateRequest(BaseModel):
    recap_enabled: bool


class RuntimeSettingsResponse(BaseModel):
    extra_env_enabled: bool = False
    extra_env: dict[str, str] = Field(default_factory=dict)
    prompt_suggestion_enabled: bool = False
    agent_teams_enabled: bool = False
    cross_session_interaction_enabled: bool = False


class RuntimeSettingsUpdateRequest(BaseModel):
    extra_env_enabled: bool | None = None
    extra_env: dict[str, str] | None = None
    prompt_suggestion_enabled: bool | None = None
    agent_teams_enabled: bool | None = None
    cross_session_interaction_enabled: bool | None = None

    @field_validator("extra_env")
    @classmethod
    def validate_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else validate_extra_env(value)
