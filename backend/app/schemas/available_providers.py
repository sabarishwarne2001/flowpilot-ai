from pydantic import BaseModel, Field


class AvailableProvidersResponse(BaseModel):
    providers: list[str] = Field(
        default_factory=list,
        description="Active AI providers available for selection",
    )
    configured_providers: list[str] = Field(
        default_factory=list,
        description="Providers with valid API keys configured in environment",
    )
    all_providers: list[str] = Field(
        default_factory=list,
        description="All providers supported by the platform",
    )
