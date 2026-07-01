"""
Dependencies Module for FlowPilot AI.

This module houses reusable dependency injection components for FastAPI routers.
Typical components registered here in later modules include:
- Database session context generators (e.g., get_db)
- Authenticated user extraction dependencies (e.g., get_current_user)
- Rule validations and authorization guard conditions
"""