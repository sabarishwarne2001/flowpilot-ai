"""Integration tests for the V1 purge, automation notifications, and avatars."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestAutomationLogsEndpointIsExecutionBacked:
    @staticmethod
    def _execution(
        db_session,
        tenant,
        rule,
        work_item,
        *,
        status,
        started_offset_ms: int = 145,
        spent: int = 1_200,
        actions: int = 2,
    ):
        from app.models.automation_execution import AutomationExecution

        started = _utcnow() - timedelta(seconds=5)
        execution = AutomationExecution(
            organization_id=tenant.organization.id,
            workspace_id=tenant.workspace.id,
            rule_id=rule.id,
            work_item_id=work_item.id,
            correlation_id=uuid.uuid4(),
            depth=0,
            status=status,
            started_at=started,
            completed_at=started + timedelta(milliseconds=started_offset_ms),
            budget_cost_micros=1_000_000,
            spent_cost_micros=spent,
            node_count=actions,
            nodes_executed=actions,
            actions_executed=actions,
        )
        db_session.add(execution)
        db_session.flush()
        return execution

    def test_returns_execution_backed_rows(
        self, client, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus

        rule = rule_factory(name="purge-test-rule")
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()
        execution = self._execution(
            db_session,
            tenant,
            rule,
            work_item,
            status=AutomationExecutionStatus.COMPLETED,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.owner.headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload) >= 1
        row = [r for r in payload if r["id"] == str(execution.id)][0]
        assert row["rule_name"] == "purge-test-rule"
        assert row["document_name"] == work_item.original_filename

    def test_projects_execution_metrics(
        self, client, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus

        rule = rule_factory(name="metrics-rule")
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()
        self._execution(
            db_session,
            tenant,
            rule,
            work_item,
            status=AutomationExecutionStatus.COMPLETED,
            started_offset_ms=145,
            spent=1_200,
            actions=2,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200, response.text
        row = response.json()[0]

        assert row["execution_time_ms"] == pytest.approx(145, abs=5)
        assert row["spent_cost_micros"] == 1_200
        assert row["actions_executed"] == 2
        assert row["action_type"] == "2 actions"

    def test_no_application_code_writes_automation_logs(self) -> None:
        import ast
        from pathlib import Path

        backend = Path(__file__).resolve().parents[2]
        offenders: list[str] = []

        for path in (backend / "app").rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_automation_log"
                ):
                    offenders.append(
                        f"{path.relative_to(backend).as_posix()}:{node.lineno}"
                    )

        assert offenders == [], f"create_automation_log found at: {offenders}"


class TestAutomationEmitsInAppNotification:
    def test_successful_execution_notifies_the_rule_author(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import Notification, NotificationType
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(name="notify-rule", created_by_user_id=tenant.owner.user.id)
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()

        _notify_rule_owner(
            db_session,
            rule=rule,
            execution=_StubExecution(actions_executed=3),
            work_item=work_item,
            status=AutomationExecutionStatus.COMPLETED,
        )
        db_session.flush()

        notification = db_session.execute(
            select(Notification)
            .where(Notification.workspace_id == tenant.workspace.id)
            .where(Notification.notification_type == NotificationType.AUTOMATION)
        ).scalar_one_or_none()

        assert notification is not None
        assert notification.user_id == tenant.owner.user.id
        assert "notify-rule" in notification.title

    def test_long_rule_name_does_not_silently_drop_the_notification(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import Notification, NotificationType
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(name="R" * 100, created_by_user_id=tenant.owner.user.id)
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()

        _notify_rule_owner(
            db_session,
            rule=rule,
            execution=_StubExecution(actions_executed=1),
            work_item=work_item,
            status=AutomationExecutionStatus.COMPLETED,
        )
        db_session.flush()

        notification = db_session.execute(
            select(Notification).where(Notification.notification_type == NotificationType.AUTOMATION)
        ).scalar_one_or_none()

        assert notification is not None
        assert len(notification.title) <= 150


class _StubExecution:
    def __init__(self, *, actions_executed: int) -> None:
        self.id = uuid.uuid4()
        self.actions_executed = actions_executed


class TestAvatarDownscaling:
    @staticmethod
    def _png(size: tuple[int, int], mode: str = "RGB") -> bytes:
        buffer = io.BytesIO()
        Image.new(mode, size, (120, 140, 200)).save(buffer, format="PNG")
        return buffer.getvalue()

    def test_downscales_large_images(self) -> None:
        from app.services.avatar_service import MAX_DIMENSION, _validate_and_normalise

        normalised = _validate_and_normalise(self._png((2000, 2000)))
        with Image.open(io.BytesIO(normalised)) as result:
            assert result.format == "PNG"
            assert max(result.size) <= MAX_DIMENSION
            assert result.size == (MAX_DIMENSION, MAX_DIMENSION)

    def test_downscale_preserves_aspect_ratio(self) -> None:
        from app.services.avatar_service import MAX_DIMENSION, _validate_and_normalise

        normalised = _validate_and_normalise(self._png((2000, 1000)))
        with Image.open(io.BytesIO(normalised)) as result:
            assert max(result.size) == MAX_DIMENSION
            ratio = result.size[0] / result.size[1]
            assert ratio == pytest.approx(2.0, abs=0.02)

    def test_phone_photo_dimensions_are_accepted(self) -> None:
        from app.services.avatar_service import MAX_DIMENSION, _validate_and_normalise

        normalised = _validate_and_normalise(self._png((3024, 4032)))
        with Image.open(io.BytesIO(normalised)) as result:
            assert max(result.size) <= MAX_DIMENSION
            assert result.size[1] == MAX_DIMENSION

    def test_images_within_bounds_are_untouched(self) -> None:
        from app.services.avatar_service import _validate_and_normalise

        normalised = _validate_and_normalise(self._png((512, 512)))
        with Image.open(io.BytesIO(normalised)) as result:
            assert result.size == (512, 512)

    def test_too_small_is_still_rejected(self) -> None:
        from app.services.avatar_service import ImageTooLargeError, _validate_and_normalise

        with pytest.raises(ImageTooLargeError):
            _validate_and_normalise(self._png((16, 16)))

    def test_non_image_bytes_are_rejected(self) -> None:
        from app.services.avatar_service import InvalidImageError, _validate_and_normalise

        with pytest.raises(InvalidImageError):
            _validate_and_normalise(b"MZ\x90\x00 this is a PE header, not an image")
