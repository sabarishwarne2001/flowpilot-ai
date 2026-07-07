"""
Abstract notification provider interface for FlowPilot AI.

Defines the provider-agnostic contract implemented by all notification
channels (SMTP, Slack, Teams, Discord, SMS, Webhooks, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    """
    Base interface for all notification providers.

    Every notification channel must implement this interface so the
    automation engine can dispatch notifications without depending on
    a specific provider implementation.
    """

    @abstractmethod
    async def send(
        self,
        recipient: str,
        title: str,
        body: str,
    ) -> bool:
        """
        Send a notification.

        Args:
            recipient: Destination identifier (email, webhook URL, phone number, etc.).
            title: Notification title or subject.
            body: Notification body.

        Returns:
            True if the notification was successfully delivered,
            otherwise False.
        """
        raise NotImplementedError