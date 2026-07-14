"""
Centralized notification dispatcher for FlowPilot AI.

Routes notification requests to registered providers (Email, Slack, Teams,
Webhooks, SMS, etc.) through a provider-agnostic registry.
"""

from __future__ import annotations

import logging

from app.services.notification.base import NotificationProvider
from app.services.notification.email import email_notification_provider
from app.models.email_settings import EmailSettings

logger = logging.getLogger("app.services.notification.dispatcher")


class NotificationDispatcher:
    """
    Central registry responsible for routing notifications to the
    appropriate provider implementation.
    """

    def __init__(self) -> None:
        self._providers: dict[str, NotificationProvider] = {}

        # Register default providers
        self.register_provider("email", email_notification_provider)
        self.register_provider("send_email", email_notification_provider)

    def register_provider(
        self,
        action_type: str,
        provider: NotificationProvider,
    ) -> None:
        """
        Register a notification provider.
        """
        key = action_type.lower().strip()

        if key in self._providers:
            logger.warning(
                "Replacing existing notification provider for '%s'.",
                key,
            )

        self._providers[key] = provider

        logger.info(
            "Registered provider '%s' for action '%s'.",
            provider.__class__.__name__,
            key,
        )

    def get_registered_providers(self) -> list[str]:
        """
        Return all registered provider keys.
        """
        return sorted(self._providers.keys())

    async def send(
        self,
        *,
        action_type: str,
        settings: EmailSettings,
        recipient: str,
        title: str,
        body: str,
    ) -> bool:
        """
        Dispatch a notification using the requested provider.
        """
        key = action_type.lower().strip()

        provider = self._providers.get(key)

        if provider is None:
            logger.error(
                "No notification provider registered for '%s'.",
                action_type,
            )
            raise ValueError(
                f"Unsupported notification provider: '{action_type}'."
            )

        logger.info(
            "Dispatching notification via '%s' to '%s'.",
            key,
            recipient,
        )

        try:
            success = await provider.send(
                settings=settings,
                recipient=recipient,
                title=title,
                body=body,
            )

            if success:
                logger.info(
                    "Notification delivered successfully via '%s'.",
                    key,
                )
            else:
                logger.warning(
                    "Notification provider '%s' reported delivery failure.",
                    key,
                )

            return success

        except Exception:
            logger.exception(
                "Notification dispatch failed for provider '%s'.",
                key,
            )
            return False


notification_dispatcher = NotificationDispatcher()