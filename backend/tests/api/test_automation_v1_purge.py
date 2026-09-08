"""Integration tests for the V1 purge, automation notifications, and avatars.

Covers the three changes made when `automation_logs` was retired as a read
and write path:

  1. GET /automation/logs projects automation_executions, not automation_logs
  2. A completed execution writes a NotificationType.AUTOMATION row
  3. Avatar uploads downscale instead of rejecting

Uses `tenant`, `client`, `db_session`, `rule_factory` and `work_item_factory`
from tests/conftest.py.

    pytest tests/services/test_automation_v1_purge.py -v
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# 1. /automation/logs is execution-backed
# ===========================================================================


class TestAutomationLogsEndpointIsExecutionBacked:
    """The panel must read automation_executions.

    The seam: automation_logs was written only by automation_service.py (V1),
    while the live path is handlers/automation.py -> executor.run_execution,
    which writes automation_executions. A working automation that delivered
    email left this panel showing zero.
    """

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
            headers=tenant.ws_admin.headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload) == 1, (
            "The endpoint returned no rows for a workspace with one execution. "
            "It is probably still reading automation_logs."
        )

        row = payload[0]
        assert row["id"] == str(execution.id)
        assert row["rule_name"] == "purge-test-rule", "join to automation_rules failed"
        assert row["document_name"] == work_item.original_filename, (
            "join to work_items failed"
        )

    def test_projects_execution_metrics(
        self, client, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        """The fields Automation.tsx now renders instead of a hardcoded
        'Duration: < 15ms'."""
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

        row = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.ws_admin.headers,
        ).json()[0]

        assert row["execution_time_ms"] == pytest.approx(145, abs=2)
        assert row["spent_cost_micros"] == 1_200
        assert row["actions_executed"] == 2
        assert row["action_type"] == "2 actions"
        assert row["execution_status"] == "COMPLETED"

    def test_status_stays_within_the_frontend_union(
        self, client, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        """types/automation.ts declares status as "SUCCESS" | "FAILED".

        AutomationExecutionStatus has seven members. Leaking a raw value into
        `status` puts a string into that field the union does not admit, which
        TypeScript cannot catch at runtime. BUDGET_EXHAUSTED must arrive as
        "FAILED" with the real value in execution_status.
        """
        from app.models.automation_execution import AutomationExecutionStatus

        rule = rule_factory(name="budget-rule")
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()
        self._execution(
            db_session,
            tenant,
            rule,
            work_item,
            status=AutomationExecutionStatus.BUDGET_EXHAUSTED,
        )
        db_session.commit()

        row = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.ws_admin.headers,
        ).json()[0]

        assert row["status"] in {"SUCCESS", "FAILED"}
        assert row["status"] == "FAILED"
        assert row["execution_status"] == "BUDGET_EXHAUSTED"

    def test_executions_without_a_work_item_are_excluded(
        self, client, db_session, tenant, rule_factory
    ) -> None:
        """The frontend type declares work_item_id and document_name non-null.

        A manually triggered execution has neither. It belongs in the
        Execution Timeline, not this document audit feed — and emitting it
        here would put nulls into required fields.
        """
        from app.models.automation_execution import (
            AutomationExecution,
            AutomationExecutionStatus,
        )

        rule = rule_factory(name="manual-rule")
        db_session.flush()
        db_session.add(
            AutomationExecution(
                organization_id=tenant.organization.id,
                workspace_id=tenant.workspace.id,
                rule_id=rule.id,
                work_item_id=None,
                correlation_id=uuid.uuid4(),
                depth=0,
                status=AutomationExecutionStatus.COMPLETED,
                budget_cost_micros=1_000_000,
                spent_cost_micros=0,
            )
        )
        db_session.commit()

        payload = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.ws_admin.headers,
        ).json()

        assert payload == [], (
            "An execution with no work item leaked into the document audit "
            "feed; work_item_id and document_name would be null."
        )

    def test_legacy_automation_logs_rows_are_not_returned(
        self, client, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        """The purge assertion. A legacy row must not appear.

        If this fails, the endpoint is still reading the V1 table.
        """
        from app import crud

        rule = rule_factory(name="legacy-rule")
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()
        crud.create_automation_log(
            db_session,
            workspace_id=tenant.workspace.id,
            rule_id=rule.id,
            work_item_id=work_item.id,
            status="SUCCESS",
            log_message="written directly by the test, not by the app",
        )
        db_session.commit()

        payload = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/automation/logs",
            headers=tenant.ws_admin.headers,
        ).json()

        assert payload == [], "endpoint is still projecting automation_logs"

    def test_no_application_code_writes_automation_logs(self) -> None:
        """Static guard on the write path.

        crud.create_automation_log still exists — dropping it needs an Alembic
        revision and would break the ARCH-28 head pin at 118. What must stay
        true is that nothing under app/ calls it.
        """
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

        assert offenders == [], (
            f"V1 automation_logs write path reintroduced at: {offenders}"
        )


# ===========================================================================
# 2. In-app notification on execution
# ===========================================================================


class TestAutomationEmitsInAppNotification:
    """A completed execution must reach the workspace alert center."""

    def test_successful_execution_notifies_the_rule_author(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import Notification, NotificationType
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(
            name="notify-rule", created_by_user_id=tenant.ws_admin.user.id
        )
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

        assert notification is not None, "no AUTOMATION notification was written"
        assert notification.user_id == tenant.ws_admin.user.id, (
            "notification went to someone other than the rule author"
        )
        assert "notify-rule" in notification.title
        assert notification.work_item_id == work_item.id

    def test_failed_execution_notifies_with_warning_priority(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import (
            Notification,
            NotificationPriority,
            NotificationType,
        )
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(
            name="failing-rule", created_by_user_id=tenant.ws_admin.user.id
        )
        work_item = work_item_factory(classification="Invoice")
        db_session.flush()

        _notify_rule_owner(
            db_session,
            rule=rule,
            execution=_StubExecution(actions_executed=0),
            work_item=work_item,
            status=AutomationExecutionStatus.BUDGET_EXHAUSTED,
        )
        db_session.flush()

        notification = db_session.execute(
            select(Notification)
            .where(Notification.notification_type == NotificationType.AUTOMATION)
        ).scalar_one()

        assert notification.priority == NotificationPriority.WARNING
        assert "BUDGET_EXHAUSTED" in notification.message

    def test_rule_without_an_author_is_skipped(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        """Notification.user_id is non-nullable. A rule with no
        created_by_user_id must produce no notification rather than an
        arbitrary recipient."""
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import Notification, NotificationType
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(name="ownerless-rule", created_by_user_id=None)
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

        assert (
            db_session.execute(
                select(Notification).where(
                    Notification.notification_type == NotificationType.AUTOMATION
                )
            ).scalar_one_or_none()
            is None
        )

    def test_long_rule_name_does_not_silently_drop_the_notification(
        self, db_session, tenant, rule_factory, work_item_factory
    ) -> None:
        """NotificationBase.title caps at 150 chars; AutomationRule.name is
        String(255).

        Before truncation, a long name raised ValidationError inside
        _notify_rule_owner's own except block and the notification vanished
        with only a warning in the log.
        """
        from app.models.automation_execution import AutomationExecutionStatus
        from app.models.notification import Notification, NotificationType
        from app.workers.handlers.automation import _notify_rule_owner

        rule = rule_factory(
            name="R" * 200, created_by_user_id=tenant.ws_admin.user.id
        )
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
            select(Notification).where(
                Notification.notification_type == NotificationType.AUTOMATION
            )
        ).scalar_one_or_none()

        assert notification is not None, (
            "a 200-character rule name silently dropped the notification"
        )
        assert len(notification.title) <= 150


class _StubExecution:
    """Minimal stand-in. _notify_rule_owner reads only `id` and
    `actions_executed`, so a real row would add setup without adding coverage."""

    def __init__(self, *, actions_executed: int) -> None:
        self.id = uuid.uuid4()
        self.actions_executed = actions_executed


# ===========================================================================
# 3. Avatar downscaling
# ===========================================================================


class TestAvatarDownscaling:
    """Oversized images are resized, not rejected.

    MAX_DIMENSION was a hard ceiling, so every phone photo (3024x4032) failed
    with "It needs to be between 32px and 1024px on every side."
    """

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
        """2000x1000 must become 1024x512, not 1024x1024.

        thumbnail() preserves ratio; resize() would not. This pins that choice.
        """
        from app.services.avatar_service import MAX_DIMENSION, _validate_and_normalise

        normalised = _validate_and_normalise(self._png((2000, 1000)))

        with Image.open(io.BytesIO(normalised)) as result:
            assert max(result.size) == MAX_DIMENSION
            ratio = result.size[0] / result.size[1]
            assert ratio == pytest.approx(2.0, abs=0.02), (
                f"aspect ratio became {ratio:.3f}; expected 2.0"
            )

    def test_phone_photo_dimensions_are_accepted(self) -> None:
        """The reported case. 3024x4032 is a standard iPhone capture."""
        from app.services.avatar_service import MAX_DIMENSION, _validate_and_normalise

        normalised = _validate_and_normalise(self._png((3024, 4032)))

        with Image.open(io.BytesIO(normalised)) as result:
            assert max(result.size) <= MAX_DIMENSION
            assert result.size[1] == MAX_DIMENSION  # portrait: height is the long edge

    def test_images_within_bounds_are_untouched(self) -> None:
        from app.services.avatar_service import _validate_and_normalise

        normalised = _validate_and_normalise(self._png((512, 512)))

        with Image.open(io.BytesIO(normalised)) as result:
            assert result.size == (512, 512), "an in-bounds image was resized"

    def test_too_small_is_still_rejected(self) -> None:
        """MIN_DIMENSION stays a hard reject. Upscaling cannot be fixed server
        side and the user needs to pick a different file."""
        from app.services.avatar_service import (
            ImageTooLargeError,
            _validate_and_normalise,
        )

        with pytest.raises(ImageTooLargeError):
            _validate_and_normalise(self._png((16, 16)))

    def test_non_image_bytes_are_rejected(self) -> None:
        from app.services.avatar_service import (
            InvalidImageError,
            _validate_and_normalise,
        )

        with pytest.raises(InvalidImageError):
            _validate_and_normalise(b"MZ\x90\x00 this is a PE header, not an image")

    def test_output_is_always_png_regardless_of_input_format(self) -> None:
        from app.services.avatar_service import _validate_and_normalise

        buffer = io.BytesIO()
        Image.new("RGB", (1500, 1500), (10, 20, 30)).save(buffer, format="JPEG")

        normalised = _validate_and_normalise(buffer.getvalue())

        with Image.open(io.BytesIO(normalised)) as result:
            assert result.format == "PNG"
            assert max(result.size) <= 1024