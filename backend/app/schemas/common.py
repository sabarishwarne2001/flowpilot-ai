from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """
    Generic success response used by endpoints that only need
    to return a confirmation message.
    """

    message: str = Field(
        ...,
        description="Human-readable success message.",
    )