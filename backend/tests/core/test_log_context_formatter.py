"""FINAL RELEASE — log lines carry their `extra=` context, with secrets masked.

Before, the formatter printed only the event name: a worker warning read
"enrich.llm_unavailable" with no error and no document, so an operator could
not tell why a document had no extraction.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging_config import ContextFormatter

pytestmark = pytest.mark.no_db


def _record(**extra) -> logging.LogRecord:
    logger = logging.getLogger("tests.log_context")
    return logger.makeRecord(logger.name, logging.WARNING, __file__, 1, "enrich.llm_unavailable", (), None,
                             extra=extra)


def test_text_lines_carry_the_context():
    line = ContextFormatter("%(levelname)s %(message)s").format(
        _record(work_item_id="abc", operation="classify", error="Connection refused (port 11434)")
    )
    assert line.startswith("WARNING enrich.llm_unavailable | ")
    assert 'work_item_id=abc' in line and 'operation=classify' in line
    assert 'error="Connection refused (port 11434)"' in line


def test_secret_looking_fields_are_masked_and_long_values_cut():
    line = ContextFormatter("%(message)s").format(
        _record(api_key="sk-live-123", smtp_password="hunter2", detail="x" * 900)
    )
    assert "sk-live-123" not in line and "hunter2" not in line
    assert "api_key=[redacted]" in line and "smtp_password=[redacted]" in line
    assert "x" * 501 not in line


def test_public_path_tokens_are_redacted_in_context_too():
    line = ContextFormatter("%(message)s").format(_record(path="/api/v1/public/calendar-feeds/secrettoken123.ics"))
    assert "secrettoken123" not in line


def test_json_mode_writes_one_parseable_object():
    record = _record(work_item_id="abc", counts={"pages": 3})
    payload = json.loads(ContextFormatter(json_output=True).format(record))
    assert payload["message"] == "enrich.llm_unavailable"
    assert payload["level"] == "WARNING"
    assert payload["work_item_id"] == "abc"
    assert json.loads(payload["counts"]) == {"pages": 3}


def test_a_line_without_context_is_unchanged():
    assert ContextFormatter("%(message)s").format(_record()) == "enrich.llm_unavailable"
