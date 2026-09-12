"""ARCH-30 Tranche 3 — billing refusals that speak the ARCH-01 error envelope.

THE DEFECT THIS CLOSES
======================

Tranche 2 refused add-on requests with `HTTPException(402, detail={...})`.
The console's API client parses exactly one structured shape, the ARCH-01
domain envelope `{code, message, details}`, and routes EVERY 402 to the
quota-refusal banner. So a Free owner clicking "Claim domain" saw "you are out
of quota" and a generic "Something went wrong", and the lock-card data in the
body was never read.

These classes subclass `FlowPilotError`, so `domain_exception_handler` renders
them. `resolve_exception_mapping` reads `status_code` and `code` from the class,
and the handler now forwards `details`, so the console receives:

    {"code": "ADDON_REQUIRED", "message": "...", "details": {"addon_key": ...}}

and `client.ts` holds these two codes back from the quota banner.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.core.exceptions import FlowPilotError


class BillingAccessError(FlowPilotError):
    """Base for refusals caused by what the organization pays for."""

    status_code = 402
    code = "BILLING_ACCESS"

    def __init__(self, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})


class AddonRequiredError(BillingAccessError):
    """The operation needs an add-on the organization does not currently have."""

    code = "ADDON_REQUIRED"


class BillingReadOnlyError(BillingAccessError):
    """The organization is read-only until an unpaid subscription is settled (D-11)."""

    code = "BILLING_READ_ONLY"


__all__ = ["AddonRequiredError", "BillingAccessError", "BillingReadOnlyError"]
