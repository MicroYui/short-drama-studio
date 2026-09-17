"""A pending observation keeps the real edit; reconfirming its source is idempotent."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from short_drama.h3_models import H3TakeReviewRequest
from short_drama.models import (
    EvidencePurpose, EvidenceStatus, GenerationUnitShotWindow, QaFailureCategory,
    QaIssue, QaStatus, RouteEvidence, Take, VideoGenerationRoute,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave
from tests.test_studio_shot_editorial import as_json, episode as episode


@pytest.fixture
def current_h3_edit(episode: tuple[ShortDramaService, str, list[str]]) -> tuple[ShortDramaService, str, Take]:
    service, pid, shot_ids = episode
    take = next(item for item in service.get_project(pid).takes if item.shot_id == shot_ids[1])
    take.provider, take.model = "minimax", "MiniMax-H3"
    take.qa_status = take.qa_report.status = QaStatus.PENDING
    take.qa_report.hard_gate_passed = False
    take.qa_report.issues = []
    take.generation_unit_id = "reviewed-generation-unit"
    take.shot_windows = [GenerationUnitShotWindow(shot_id=shot_ids[1], shot_revision=1,
        duration_seconds=3.5, source_in_seconds=4, source_out_seconds=7.5)]
    service.repository.save_take(take)
    service.repository.save_route_evidence(RouteEvidence(id="review-evidence", project_id=pid,
        shot_id=take.shot_id, task_id=take.task_id, take_id=take.id, provider=take.provider,
        model=take.model, route=VideoGenerationRoute.REFERENCE, resolution="320x180",
        purpose=EvidencePurpose.QUALITY_PROBE, status=EvidenceStatus.AWAITING_REVIEW,
        technical_passed=True, created_at=take.created_at))
    current = read_editorial(service, pid)
    editor = EditorProject.model_validate(current.project)
    for clip in editor.timelines[0].clips:
        if (clip.model_extra or {}).get("sourceShotId") == take.shot_id:
            clip.trimStart, clip.duration, clip.trimEnd = 3.875, 4.125, 0
            clip.volume, clip.audioFadeInSeconds = 0.65, 0.25
        elif clip.startTime >= 8:
            clip.startTime += 0.125
    for cue in editor.timelines[0].subtitles:
        if cue.startTime >= 8:
            cue.startTime += 0.125
            cue.endTime += 0.125
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={"project": as_json(editor)}))
    return service, pid, take


def issue(code: str, category: QaFailureCategory = QaFailureCategory.DIALOGUE_AUDIO) -> QaIssue:
    return QaIssue(code=code, severity="HIGH", message=code, repair_layer="POST_DIRECTOR", category=category)


def test_pending_review_and_same_source_reselection_preserve_actual_edit_and_export(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    before = service.get_project(pid)
    saved = read_editorial(service, pid)
    response = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": {
        "hard_gate_passed": False, "score": 3.5, "notes": "有条件可用；独立听审尚未完成，保留真实剪点与原声。",
        "issues": [issue("LISTENING_NOT_VERIFIED").model_dump(mode="json")],
    }})
    assert response.status.value == "SUCCEEDED", response.error
    reviewed = service.repository.get_take(take.id)
    assert reviewed and reviewed.qa_status == QaStatus.PENDING
    assert reviewed.selected_shot_ids == take.selected_shot_ids and reviewed.is_selected
    assert not reviewed.qa_report.hard_gate_passed
    assert read_editorial(service, pid) == saved
    after_review = service.get_project(pid).document
    service.select_take(take.id, shot_ids=[take.shot_id], expected_editorial_revision=saved.revision)
    after = service.get_project(pid)
    assert after.document == after_review
    assert after.document.editorial_timeline == before.document.editorial_timeline
    assert after.document.shots == before.document.shots
    assert [item for item in after.takes if item.id != take.id] == [item for item in before.takes if item.id != take.id]
    exported = start_export(service, pid, StudioExportRequest(expected_revision=saved.revision,
        width=480, height=270), background=False)
    assert exported.status == "completed", exported.error
    assert exported.duration_seconds == pytest.approx(12.125)
    assert exported.video_path and Path(exported.video_path).is_file()


def test_explicit_rejection_cannot_be_cleared_by_pending_review_or_reselection(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    saved = read_editorial(service, pid)
    rejected = service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.REJECTED,
        hard_gate_passed=False, score=1, notes="实际对白说错，需要修复。", issues=[issue("WRONG_DIALOGUE")]))
    assert not rejected.take.is_selected and rejected.take.qa_status == QaStatus.REJECTED
    assert read_editorial(service, pid) == saved
    pending = service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.PENDING,
        hard_gate_passed=False, score=2, notes="新增片段尚未观察，不能据此撤销之前实际失败。"))
    assert pending.take.qa_status == QaStatus.REJECTED and not pending.take.is_selected
    assert [item.code for item in pending.take.qa_report.issues] == ["WRONG_DIALOGUE"]
    with pytest.raises(WorkflowConflictError, match="rejected Take"):
        service.select_take(take.id, expected_editorial_revision=saved.revision)
    assert read_editorial(service, pid) == saved
    service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.PASSED,
        hard_gate_passed=True, score=4, notes="重新检查确认此前读音判断错误，实际对白通过。"))
    service.select_take(take.id, expected_editorial_revision=saved.revision)
    assert read_editorial(service, pid) == saved


def test_different_candidate_still_requires_coverage_and_same_source_requires_file(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    saved = read_editorial(service, pid)
    candidate = take.model_copy(deep=True, update={"id": "different-candidate", "candidate_index": 99,
        "is_selected": False, "selected_shot_ids": []})
    service.repository.save_take(candidate)
    with pytest.raises(WorkflowConflictError, match="已有裁切"):
        service.select_take(candidate.id, expected_editorial_revision=saved.revision)
    assert read_editorial(service, pid) == saved
    take.duration_seconds = 7.5
    service.repository.save_take(take)
    with pytest.raises(WorkflowConflictError, match="已有源区间"):
        service.select_take(take.id, expected_editorial_revision=saved.revision)
    assert read_editorial(service, pid) == saved
    Path(take.media_path).unlink()
    with pytest.raises(WorkflowConflictError, match="media is unavailable"):
        service.select_take(take.id, expected_editorial_revision=saved.revision)


def test_pending_review_does_not_override_known_technical_failure(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, _, take = current_h3_edit
    evidence = service.repository.get_route_evidence_for_take(take.id)
    assert evidence
    evidence.technical_passed = False
    service.repository.save_route_evidence(evidence)
    take.qa_report.issues = [issue("DECODE_FAILED", QaFailureCategory.TECHNICAL_MEDIA)]
    service.repository.save_take(take)
    result = service.review_h3_take(take.id, H3TakeReviewRequest(hard_gate_passed=False,
        score=0, notes="语义观察尚未完成，不能忽略已有媒体解码失败。"))
    assert result.take.qa_status == QaStatus.REJECTED and not result.take.is_selected
    assert [item.code for item in result.take.qa_report.issues] == ["DECODE_FAILED"]


@pytest.mark.parametrize(("status", "passed"), [(QaStatus.PASSED, False), (QaStatus.PENDING, True), (QaStatus.REJECTED, True)])
def test_review_status_and_passed_cannot_contradict(status: QaStatus, passed: bool) -> None:
    with pytest.raises(ValidationError, match="review_status PASSED requires"):
        H3TakeReviewRequest(review_status=status, hard_gate_passed=passed, score=3,
            notes="矛盾的检查合同不能保存。", issues=[issue("ISSUE")])


def test_explicit_rejection_needs_observed_classified_issue() -> None:
    with pytest.raises(ValidationError, match="classified QA issue"):
        H3TakeReviewRequest(review_status=QaStatus.REJECTED, hard_gate_passed=False,
            score=1, notes="只有状态，没有实际问题，不能否决。")


def test_retracting_a_misread_requirement_leaves_pending_checks_and_allows_explicit_selection(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    saved = read_editorial(service, pid)
    service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.REJECTED,
        hard_gate_passed=False, score=2, notes="Incorrectly treated solo ownership as single-hand-only.",
        issues=[issue("RIGHT_HAND_ASSISTS", QaFailureCategory.CONTACT_CONTINUITY)]))
    result = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": {
        "review_status": "PENDING", "hard_gate_passed": False, "score": 4,
        "notes": "Handover order observed; voice identity still requires listening.",
        "issues": [issue("LISTENING_NOT_VERIFIED").model_dump(mode="json")],
        "retracted_issue_codes": ["RIGHT_HAND_ASSISTS"],
        "retraction_reason": "User clarified that right-hand support after sole ownership is allowed.",
    }})
    assert result.status.value == "SUCCEEDED", result.error
    current = service.repository.get_take(take.id)
    assert current and current.qa_status == QaStatus.PENDING and not current.is_selected
    assert not current.qa_report.hard_gate_passed
    assert [item.code for item in current.qa_report.issues] == ["LISTENING_NOT_VERIFIED"]
    assert "RIGHT_HAND_ASSISTS" in (current.qa_report.reviewer_notes or "")
    assert "right-hand support after sole ownership is allowed" in (current.qa_report.reviewer_notes or "")
    evidence = service.repository.get_route_evidence_for_take(take.id)
    assert evidence and evidence.notes == current.qa_report.reviewer_notes
    assert read_editorial(service, pid) == saved
    service.select_take(take.id, expected_editorial_revision=saved.revision)
    assert read_editorial(service, pid) == saved


def test_withdrawing_one_rejection_keeps_other_observed_failures(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, _, take = current_h3_edit
    service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.REJECTED,
        hard_gate_passed=False, score=1, notes="Two recorded findings.",
        issues=[issue("HAND_RULE"), issue("WRONG_DIALOGUE")]))
    reviewed = service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.PENDING,
        hard_gate_passed=False, score=2, notes="Dialogue failure still applies.",
        retracted_issue_codes=["HAND_RULE"], retraction_reason="The hand requirement was misread."))
    assert reviewed.take.qa_status == QaStatus.REJECTED
    assert [item.code for item in reviewed.take.qa_report.issues] == ["WRONG_DIALOGUE"]


@pytest.mark.parametrize("code", ["UNKNOWN", "DECODE_FAILED"])
def test_withdrawal_does_not_ignore_unknown_or_technical_findings(
    current_h3_edit: tuple[ShortDramaService, str, Take], code: str,
) -> None:
    service, _, take = current_h3_edit
    take.qa_status = take.qa_report.status = QaStatus.REJECTED
    take.qa_report.issues = [issue("DECODE_FAILED", QaFailureCategory.TECHNICAL_MEDIA)]
    service.repository.save_take(take)
    before = service.repository.get_take(take.id)
    with pytest.raises(WorkflowConflictError):
        service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.PENDING,
            hard_gate_passed=False, score=2, notes="Still not passed.",
            retracted_issue_codes=[code], retraction_reason="Semantic reinterpretation only."))
    assert service.repository.get_take(take.id) == before


def test_withdrawal_needs_an_explicit_reason_and_does_not_mark_passed() -> None:
    with pytest.raises(ValidationError, match="specific retraction_reason"):
        H3TakeReviewRequest(review_status=QaStatus.PENDING, hard_gate_passed=False,
            score=3, notes="Still checking.", retracted_issue_codes=["OLD_FINDING"])
    with pytest.raises(ValidationError, match="requires PENDING"):
        H3TakeReviewRequest(review_status=QaStatus.PASSED, hard_gate_passed=True,
            score=3, notes="No complete check.", retracted_issue_codes=["OLD_FINDING"],
            retraction_reason="Changed interpretation is not a complete check.")


def test_pending_retraction_feedback_leads_to_a_normal_review_without_changing_the_edit(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.PENDING,
        hard_gate_passed=False, score=3.5, notes="The current shot does not yet cover the originally planned exit.",
        issues=[issue("ACTION_COMPLETION_MISMATCH", QaFailureCategory.CONTACT_CONTINUITY)]))
    before = service.get_project(pid)
    evidence_before = service.repository.get_route_evidence_for_take(take.id)
    request = H3TakeReviewRequest(review_status=QaStatus.PENDING, hard_gate_passed=False, score=3.5,
        notes="The user permits an implied exit; listening remains unknown.",
        issues=[issue("LISTENING_NOT_VERIFIED")], retracted_issue_codes=["ACTION_COMPLETION_MISMATCH"],
        retraction_reason="A complete visible exit was a self-imposed shot detail.").model_dump(mode="json")
    failed = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": request})
    assert failed.status.value == "FAILED" and failed.error is not None
    assert "qa_status=PENDING" in failed.error.message
    assert "remove both retracted_issue_codes and retraction_reason" in failed.error.message
    assert "submit review_status=PENDING with the current issues and notes" in failed.error.message
    assert service.get_project(pid) == before
    assert service.repository.get_route_evidence_for_take(take.id) == evidence_before

    # Follow the returned correction through the same registered Tool and persisted service.
    del request["retracted_issue_codes"]
    del request["retraction_reason"]
    updated = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": request})
    assert updated.status.value == "SUCCEEDED", updated.error
    reviewed = service.repository.get_take(take.id)
    assert reviewed and reviewed.qa_status == QaStatus.PENDING and not reviewed.qa_report.hard_gate_passed
    assert reviewed.is_selected and reviewed.selected_shot_ids == take.selected_shot_ids
    assert [item.code for item in reviewed.qa_report.issues] == ["LISTENING_NOT_VERIFIED"]
    assert reviewed.qa_report.reviewer_notes == request["notes"]
    after = service.get_project(pid)
    assert after.document.editorial_timeline == before.document.editorial_timeline
    assert after.document.shots == before.document.shots
    assert [item for item in after.takes if item.id != take.id] == [item for item in before.takes if item.id != take.id]


def test_unknown_retraction_feedback_names_current_codes_and_keeps_remaining_rejection(
    current_h3_edit: tuple[ShortDramaService, str, Take],
) -> None:
    service, pid, take = current_h3_edit
    service.review_h3_take(take.id, H3TakeReviewRequest(review_status=QaStatus.REJECTED,
        hard_gate_passed=False, score=1, notes="Two recorded findings.",
        issues=[issue("HAND_RULE"), issue("WRONG_DIALOGUE")]))
    before = service.get_project(pid)
    evidence_before = service.repository.get_route_evidence_for_take(take.id)
    request = H3TakeReviewRequest(review_status=QaStatus.PENDING, hard_gate_passed=False, score=2,
        notes="Dialogue failure still applies.", retracted_issue_codes=["ACTION_COMPLETION_MISMATCH"],
        retraction_reason="The hand rule was misread.").model_dump(mode="json")
    failed = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": request})
    assert failed.status.value == "FAILED" and failed.error is not None
    assert "qa_status=REJECTED" in failed.error.message
    assert "Unrecorded retracted_issue_codes: ACTION_COMPLETION_MISMATCH" in failed.error.message
    assert "Current recorded issue codes: HAND_RULE, WRONG_DIALOGUE" in failed.error.message
    assert service.get_project(pid) == before
    assert service.repository.get_route_evidence_for_take(take.id) == evidence_before

    request["retracted_issue_codes"] = ["HAND_RULE"]
    updated = service.tool_registry.invoke("route.review_h3_take", {"take_id": take.id, "request": request})
    assert updated.status.value == "SUCCEEDED", updated.error
    reviewed = service.repository.get_take(take.id)
    assert reviewed and reviewed.qa_status == QaStatus.REJECTED and not reviewed.is_selected
    assert [item.code for item in reviewed.qa_report.issues] == ["WRONG_DIALOGUE"]
    assert service.get_project(pid).document.editorial_timeline == before.document.editorial_timeline
