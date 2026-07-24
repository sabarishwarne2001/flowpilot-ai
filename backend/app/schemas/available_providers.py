from pydantic import BaseModel


class AvailableProvidersResponse(BaseModel):
    providers: list[str]