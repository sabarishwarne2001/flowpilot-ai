"""A hand that cannot act (a refused email, a task with nowhere to go) leaves its action FAILED with
the reason in words, on real Postgres; it is never left stuck EXECUTING with the job erroring."""

from __future__ import annotations

from app.services.helm import executor, hands
from app.services.helm import vocabulary as v
from tests.engines.test_helm_actions_live import _action, _book


def test_a_hand_that_cannot_act_leaves_the_action_failed_with_its_reason(db_session, tenant, monkeypatch):
    book = _book(db_session, tenant)
    action = _action(db_session, book, kind=v.ACTION_NOTIFY)

    def refuse(db, *, action, book, snapshot=None):
        raise hands.HandError("HELM_MAIL_FAILED", "The mail server did not accept the message: 550 refused")

    monkeypatch.setattr(hands, "notify", refuse)
    executor.execute(db_session, action_id=action.id)
    db_session.refresh(action)
    assert action.state == v.ACTION_FAILED
    assert action.result["error"] == "HELM_MAIL_FAILED"
    assert "550 refused" in action.result["message"]
