"""ARCH-10 Steps 7-8 — unit tests for the state machine and worker profiles."""

from __future__ import annotations

import sys
import types
import pytest

from app.core.webhook_events import WEBHOOK_EVENT_TYPES
from app.services.pipeline_state import (
    EVENT_BY_STAGE,
    PUBLIC_STATUS_BY_STAGE,
    STAGE_TRANSITIONS,
    TERMINAL_STAGES,
    PipelineStage,
    can_transition,
)
from app.workers.profiles import (
    ALL,
    ENRICH,
    HEAVY_MODULES,
    LIGHT,
    OCR,
    ProfileError,
    assert_imports_match_profile,
    claimable_job_types,
    get_profile,
    uncovered_job_types,
)


def test_every_stage_has_a_public_status():
    missing = [s for s in PipelineStage if s not in PUBLIC_STATUS_BY_STAGE]
    assert not missing, f"stages with no public status: {missing}"


def test_public_statuses_stay_within_the_frontend_contract():
    assert set(PUBLIC_STATUS_BY_STAGE.values()) == {
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    }


def test_quota_blocked_reads_as_failed_publicly_but_is_its_own_stage():
    assert PUBLIC_STATUS_BY_STAGE[PipelineStage.QUOTA_BLOCKED] == "FAILED"
    assert PipelineStage.QUOTA_BLOCKED is not PipelineStage.FAILED
    assert PipelineStage.QUOTA_BLOCKED in TERMINAL_STAGES


def test_every_stage_has_a_transition_entry():
    missing = [s for s in PipelineStage if s not in STAGE_TRANSITIONS]
    assert not missing, f"stages absent from the transition table: {missing}"


def test_every_stage_is_reachable_from_queued():
    reachable = {PipelineStage.QUEUED}
    frontier = [PipelineStage.QUEUED]
    while frontier:
        current = frontier.pop()
        for target in STAGE_TRANSITIONS[current]:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    unreachable = set(PipelineStage) - reachable
    assert not unreachable, f"stages that can never be entered: {unreachable}"


def test_terminal_stages_only_return_to_queued():
    for stage in TERMINAL_STAGES:
        assert STAGE_TRANSITIONS[stage] == frozenset({PipelineStage.QUEUED})


@pytest.mark.parametrize("stage", list(PipelineStage))
def test_self_transition_is_always_legal(stage):
    assert can_transition(stage, stage)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (PipelineStage.QUEUED, PipelineStage.COMPLETED),
        (PipelineStage.QUEUED, PipelineStage.EXTRACTED),
        (PipelineStage.EXTRACTING, PipelineStage.COMPLETED),
        (PipelineStage.COMPLETED, PipelineStage.EXTRACTING),
        (PipelineStage.FAILED, PipelineStage.COMPLETED),
    ],
)
def test_illegal_transitions_are_rejected(source, target):
    assert not can_transition(source, target)


def test_extraction_cannot_shortcut_to_completed():
    assert PipelineStage.COMPLETED not in STAGE_TRANSITIONS[PipelineStage.EXTRACTING]
    assert PipelineStage.EXTRACTED in STAGE_TRANSITIONS[PipelineStage.EXTRACTING]


def test_published_events_are_in_the_webhook_vocabulary():
    published = {e for e in EVENT_BY_STAGE.values() if e}
    missing = sorted(published - set(WEBHOOK_EVENT_TYPES))
    assert not missing, f"{missing} are emitted by the state machine but not publishable."


def test_internal_stages_are_not_published():
    assert EVENT_BY_STAGE[PipelineStage.EXTRACTED] is None
    assert EVENT_BY_STAGE[PipelineStage.ENRICHING] is None


def test_quota_blocked_publishes_as_document_failed():
    assert EVENT_BY_STAGE[PipelineStage.QUOTA_BLOCKED] == "document.failed"


def test_every_stage_has_an_explicit_event_decision():
    missing = [s for s in PipelineStage if s not in EVENT_BY_STAGE]
    assert not missing, f"stages with no event decision: {missing}"


def test_production_profiles_are_disjoint():
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for profile in (LIGHT, OCR, ENRICH):
        for job_type in profile.job_types or ():
            if job_type in seen:
                clashes.append(f"{job_type}: {seen[job_type]} + {profile.name}")
            seen[job_type] = profile.name
    assert not clashes, f"a job type is claimable by two profiles: {clashes}"


def test_light_profile_permits_no_heavy_modules():
    assert LIGHT.allow_heavy == frozenset()


def test_ocr_profile_permits_paddle_only():
    assert OCR.allow_heavy == frozenset({"paddleocr", "paddle"})
    assert "chromadb" not in OCR.allow_heavy


def test_enrich_profile_does_not_permit_paddleocr():
    assert "paddleocr" not in ENRICH.allow_heavy
    assert "chromadb" in ENRICH.allow_heavy


def test_all_profile_claims_everything():
    assert ALL.job_types is None
    assert ALL.may_claim("anything.at.all")
    assert claimable_job_types(ALL) is None


def test_profile_routing():
    assert OCR.may_claim("document.extract")
    assert not OCR.may_claim("document.enrich")
    assert not LIGHT.may_claim("document.extract")
    assert ENRICH.may_claim("document.enrich")


def test_unknown_profile_raises():
    with pytest.raises(ProfileError):
        get_profile("heavy-ish")


def test_default_profile_is_light():
    assert get_profile(None) is LIGHT


def test_light_profile_refuses_a_process_with_paddleocr_loaded(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", types.ModuleType("paddleocr"))
    with pytest.raises(ProfileError) as exc:
        assert_imports_match_profile(LIGHT)
    assert "paddleocr" in str(exc.value)


def test_uncovered_job_types_ignores_test_handlers():
    registered = ["document.extract", "document.enrich", "storage.sample",
                  "test.noop", "test.always_fails"]
    assert uncovered_job_types(registered) == set()


def test_uncovered_job_types_catches_an_unrouted_handler():
    uncovered = uncovered_job_types(["document.extract", "reindex.workspace"])
    assert uncovered == {"reindex.workspace"}


def test_heavy_module_list_covers_the_ml_stack():
    for name in ("paddleocr", "paddle", "chromadb", "sentence_transformers", "torch"):
        assert name in HEAVY_MODULES