"""
Business orchestration services registry for FlowPilot AI.

Unifies and exports high-level service workflows (such as credential 
verifications and enrollment management) to decouple API routers.
"""

from app.services.auth_service import register_new_user, authenticate_user

__all__ = [
    "register_new_user",
    "authenticate_user",
]