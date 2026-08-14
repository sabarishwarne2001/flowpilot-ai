"""Principal attribution abstraction for FlowPilot AI (ARCH-08 §B.1, §0.1).

Exactly one of user / api_key is set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.user import User


@dataclass(frozen=True)
class Principal:
    user: Optional[User] = None
    api_key: Optional[ApiKey] = None

    @property
    def audit_columns(self) -> dict[str, Optional[uuid.UUID]]:
        return {
            "actor_id": self.user.id if self.user is not None and self.api_key is None else None,
            "api_key_id": self.api_key.id if self.api_key is not None else None,
        }

    @property
    def issuer_id(self) -> Optional[uuid.UUID]:
        if self.api_key is not None:
            return self.api_key.user_id
        return self.user.id if self.user is not None else None