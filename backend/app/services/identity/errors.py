"""ARCH-16 domain exceptions."""

from __future__ import annotations


class IdentityError(Exception):
    status_code = 400

    def __init__(self, message: str, *, reason: str | None = None):
        super().__init__(message)
        self.message = message
        self.reason = reason or message


class DomainError(IdentityError):
    pass


class DomainPolicyRefused(DomainError):
    status_code = 422


class DomainVerificationFailed(DomainError):
    status_code = 409


class SsoBindingConflict(DomainError):
    status_code = 409


class IdpConfigError(IdentityError):
    pass


class AssertionRejected(IdentityError):
    status_code = 401
    public_detail = "Authentication failed."

    def __init__(self, outcome: str, reason: str):
        super().__init__(self.public_detail, reason=reason)
        self.outcome = outcome


class IdentityRefused(IdentityError):
    status_code = 403
    public_detail = "You do not have access to this organization."

    def __init__(self, reason: str, *, outcome: str = "REJECTED_UNKNOWN"):
        super().__init__(self.public_detail, reason=reason)
        self.outcome = outcome


class ScimError(IdentityError):
    def __init__(self, status_code: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.scim_type = scim_type

    def to_body(self) -> dict:
        body = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": str(self.status_code),
            "detail": self.detail,
        }
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


class ScimNotFound(ScimError):
    def __init__(self, detail: str = "Resource not found."):
        super().__init__(404, detail)


class ScimConflict(ScimError):
    def __init__(self, detail: str, scim_type: str = "uniqueness"):
        super().__init__(409, detail, scim_type)


class ScimInvalidFilter(ScimError):
    def __init__(self, detail: str):
        super().__init__(400, detail, "invalidFilter")


class ScimInvalidValue(ScimError):
    def __init__(self, detail: str):
        super().__init__(400, detail, "invalidValue")


class LastOwnerProtected(ScimConflict):
    def __init__(self):
        super().__init__(
            "Cannot deactivate the last remaining OWNER of this organization. "
            "Transfer ownership in FlowPilot first.",
            scim_type="mutability",
        )