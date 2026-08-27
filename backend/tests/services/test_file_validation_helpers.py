import pytest
import uuid
from types import SimpleNamespace

from app.core.principal import PrincipalKind
from app.services.usage_service import is_system_attributed

from app.services.file_validation_service import (
    FileValidationError,
    RejectionReason,
    spool_stream,
)


def test_spool_stream_rejects_empty():
    with pytest.raises(FileValidationError) as exc:
        spool_stream([], max_bytes=100)

    assert exc.value.reason is RejectionReason.EMPTY


def test_spool_stream_rejects_over_limit():
    with pytest.raises(FileValidationError) as exc:
        spool_stream([b"12345", b"67890"], max_bytes=5)

    assert exc.value.reason is RejectionReason.TOO_LARGE


def test_spool_stream_returns_file_and_size():
    handle, size = spool_stream([b"hello", b" world"], max_bytes=100)

    try:
        assert size == 11
        assert handle.read() == b"hello world"
    finally:
        handle.close()

def test_system_usage_event_is_detected():
    event = SimpleNamespace(
        actor_id=None,
        api_key_id=None,
        details={"principal": PrincipalKind.SYSTEM.value},
    )

    assert is_system_attributed(event) is True


def test_user_usage_event_is_not_system_attributed():
    event = SimpleNamespace(
        actor_id=uuid.uuid4(),
        api_key_id=None,
        details={"principal": "USER"},
    )

    assert is_system_attributed(event) is False