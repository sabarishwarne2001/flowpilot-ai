"""Principal attribution abstraction for FlowPilot AI (ARCH-08 §B.1, extended by ARCH-09 §B.10).

Represents the authenticated identity behind a unit of work (Human User, API Key, or System Background Worker).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Iterator, Optional

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.user import User


class PrincipalKind(str, PyEnum):
    USER = "USER"
    API_KEY = "API_KEY"
    SYSTEM = "SYSTEM"


class PrincipalError(ValueError):
    """A Principal was constructed in a shape the audit model cannot express."""


@dataclass(frozen=True)
class Principal:
    """The authenticated identity behind a unit of work."""

    kind: PrincipalKind = PrincipalKind.USER

    # Legacy object references (ARCH-08)
    user: Optional[Any] = None
    api_key: Optional[Any] = None

    # Identifiers (ARCH-09)
    user_id: Optional[uuid.UUID] = None
    api_key_id: Optional[uuid.UUID] = None

    # SYSTEM job metadata
    job_id: Optional[uuid.UUID] = None
    job_name: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 1. Resolve UUIDs from object instances if provided
        if self.user is not None and self.user_id is None:
            object.__setattr__(self, "user_id", getattr(self.user, "id", None))
        if self.api_key is not None:
            if self.api_key_id is None:
                object.__setattr__(self, "api_key_id", getattr(self.api_key, "id", None))
            if self.user_id is None:
                object.__setattr__(self, "user_id", getattr(self.api_key, "user_id", None))
            if self.kind == PrincipalKind.USER:
                object.__setattr__(self, "kind", PrincipalKind.API_KEY)

        kind = self.kind

        # 2. Strict audit model invariant assertions
        if kind is PrincipalKind.USER:
            if self.user_id is None and self.user is None:
                raise PrincipalError("USER principal requires user_id or user object.")
            if self.api_key_id is not None or self.api_key is not None:
                raise PrincipalError("USER principal must not carry api_key or api_key_id.")
            if self.job_id is not None:
                raise PrincipalError("USER principal must not carry job_id.")

        elif kind is PrincipalKind.API_KEY:
            if self.api_key_id is None and self.api_key is None:
                raise PrincipalError("API_KEY principal requires api_key_id or api_key object.")
            if self.user_id is None and self.user is None:
                raise PrincipalError("API_KEY principal requires issuer user_id or user object.")
            if self.job_id is not None:
                raise PrincipalError("API_KEY principal must not carry job_id.")

        elif kind is PrincipalKind.SYSTEM:
            if (
                self.user_id is not None
                or self.user is not None
                or self.api_key_id is not None
                or self.api_key is not None
            ):
                raise PrincipalError(
                    "SYSTEM principal must carry neither user_id/user nor api_key_id/api_key."
                )
            if self.job_name is None:
                raise PrincipalError("SYSTEM principal requires job_name.")

        else:
            raise PrincipalError(f"Unknown principal kind: {kind!r}")

    @property
    def actor_id(self) -> Optional[uuid.UUID]:
        return self.user_id if self.kind is PrincipalKind.USER else None

    @property
    def audit_api_key_id(self) -> Optional[uuid.UUID]:
        return self.api_key_id if self.kind is PrincipalKind.API_KEY else None

    @property
    def issuer_id(self) -> Optional[uuid.UUID]:
        if self.api_key is not None:
            return self.api_key.user_id
        return self.user_id

    def audit_columns(self) -> dict[str, Optional[uuid.UUID]]:
        return {
            "actor_id": self.actor_id,
            "api_key_id": self.audit_api_key_id,
        }

    def audit_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"principal": self.kind.value}

        if self.kind is PrincipalKind.API_KEY:
            details["key_owner_user_id"] = str(self.user_id)
        elif self.kind is PrincipalKind.SYSTEM:
            details["job_name"] = self.job_name
            if self.job_id is not None:
                details["job_id"] = str(self.job_id)

        if self.extra:
            details.update(self.extra)
        return details

    @classmethod
    def for_user(cls, user_id: uuid.UUID) -> Principal:
        return cls(kind=PrincipalKind.USER, user_id=user_id)

    @classmethod
    def for_api_key(
        cls, *, api_key_id: uuid.UUID, issuer_user_id: uuid.UUID
    ) -> Principal:
        return cls(
            kind=PrincipalKind.API_KEY,
            user_id=issuer_user_id,
            api_key_id=api_key_id,
        )

    @classmethod
    def for_system(
        cls,
        *,
        job_name: str,
        job_id: Optional[uuid.UUID] = None,
        **extra: Any,
    ) -> Principal:
        return cls(
            kind=PrincipalKind.SYSTEM,
            job_name=job_name,
            job_id=job_id,
            extra=dict(extra),
        )

    def __repr__(self) -> str:
        if self.kind is PrincipalKind.USER:
            return f"<Principal USER user={self.user_id}>"
        if self.kind is PrincipalKind.API_KEY:
            return (
                f"<Principal API_KEY key={self.api_key_id} "
                f"issuer={self.user_id}>"
            )
        return f"<Principal SYSTEM job={self.job_name}:{self.job_id}>"


_principal_var: ContextVar[Optional[Principal]] = ContextVar(
    "flowpilot_principal", default=None
)


def get_current_principal() -> Optional[Principal]:
    return _principal_var.get()


def set_current_principal(principal: Optional[Principal]) -> Token:
    return _principal_var.set(principal)


def reset_current_principal(token: Token) -> None:
    _principal_var.reset(token)


@contextmanager
def principal_scope(principal: Optional[Principal]) -> Iterator[Optional[Principal]]:
    token = _principal_var.set(principal)
    try:
        yield principal
    finally:
        _principal_var.reset(token)


@contextmanager
def system_principal(
    *,
    job_name: str,
    job_id: Optional[uuid.UUID] = None,
    **extra: Any,
) -> Iterator[Principal]:
    principal = Principal.for_system(job_name=job_name, job_id=job_id, **extra)
    with principal_scope(principal):
        yield principal
