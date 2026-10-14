"""F-353 — GROQ_MODEL_NAME chooses the model the platform's own Groq key serves.

    pytest tests/services/test_platform_model_setting.py -q

Both templates and the production compose file offered GROQ_MODEL_NAME ("Models used with the
platform keys. Change here when a provider retires one."), but nothing read it: the platform
model was fixed in code, so a deployment that changed it changed nothing. The production
template and compose file even set it to llama-3.3-70b-versatile, a model the platform does
not serve at all (N-054). It now decides the model a call on the platform's key falls back to
and the model a new workspace starts with, and only the models the platform serves on its own
key are accepted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.ai_models import PLATFORM_DEFAULT_MODEL, PLATFORM_SERVED_MODELS, platform_default_model, platform_safe
from app.core.config import Settings, settings
from app.schemas.ai_settings import AIProvider

pytestmark = pytest.mark.no_db

OTHER_SERVED = next(model for model in PLATFORM_SERVED_MODELS["groq"] if model != PLATFORM_DEFAULT_MODEL)


def test_the_default_is_the_registry_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_MODEL_NAME", raising=False)
    assert Settings(_env_file=None).GROQ_MODEL_NAME == PLATFORM_DEFAULT_MODEL


def test_setting_it_changes_the_model_the_platform_key_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROQ_MODEL_NAME", OTHER_SERVED)
    assert platform_default_model() == OTHER_SERVED
    needs_own_key = SimpleNamespace(provider=AIProvider.GEMINI, model="gemini-2.5-pro", temperature=0.2)
    safe = platform_safe(needs_own_key)
    assert (safe.provider, safe.model) == (AIProvider.GROQ, OTHER_SERVED)
    assert safe.temperature == 0.2


def test_a_new_workspace_starts_on_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """add_default_settings writes a new workspace's AI settings (into a recording session)."""
    import uuid

    from app.models.ai_settings import AISettings
    from app.services.workspace_service import add_default_settings

    class RecordingSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, row: object) -> None:
            self.added.append(row)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(settings, "GROQ_MODEL_NAME", OTHER_SERVED)
    db = RecordingSession()
    add_default_settings(db, workspace_id=uuid.uuid4(), actor_id=uuid.uuid4())  # type: ignore[arg-type]
    (ai,) = [row for row in db.added if isinstance(row, AISettings)]
    assert ai.model == OTHER_SERVED


@pytest.mark.parametrize("model", ["llama-3.3-70b-versatile", "gemini-3.5-flash", "qwen/qwen3.8-27b", ""])
def test_a_model_the_platform_does_not_serve_is_refused_at_start_up(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    monkeypatch.setenv("GROQ_MODEL_NAME", model)
    with pytest.raises(ValidationError, match="GROQ_MODEL_NAME"):
        Settings(_env_file=None)


def test_the_settings_import_first_in_a_fresh_process() -> None:
    """The check runs while the settings are being built at import time; it must not import
    anything that imports the settings back (alembic and the workers import config first)."""
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-c", "import app.core.config as c; print(c.settings.GROQ_MODEL_NAME)"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-800:]


@pytest.mark.parametrize("model", sorted(PLATFORM_SERVED_MODELS["groq"]))
def test_every_served_model_is_accepted(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    monkeypatch.setenv("GROQ_MODEL_NAME", f" {model} ")
    assert Settings(_env_file=None).GROQ_MODEL_NAME == model
