"""Director repairs must reach current generation and the actual adopted edit."""
from pathlib import Path

import pytest

from short_drama.models import DirectorRepairDecision, DirectorRepairStrategy, ProductionDocumentOrigin
from short_drama.production_contracts import (
    ClipGroup, GenerationPlanCompileRequest, ProductionSpecCreateRequest,
    StoryboardContent, StoryboardCreateRequest,
)
from short_drama.reference_planner import DirectorRepairDecisionRequest, ReplacementShotDraft, ShotPlanPatch
from short_drama.service import ShortDramaService, WorkflowConflictError
from tests.test_storyboard_contracts import _spec_and_storyboard


def managed_project(tmp_path: Path) -> ShortDramaService:
    service = ShortDramaService(database_path=tmp_path / "project.sqlite3", workspace_root=tmp_path / "media")
    document = service.initialize_fixture().document
    spec, _ = _spec_and_storyboard(document)
    saved = service.create_production_spec(document.project.id, ProductionSpecCreateRequest(spec=spec, origin=ProductionDocumentOrigin.AGENT))
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    groups = [[*document.shots[:2]], *[[item] for item in document.shots[2:]]]
    storyboard = StoryboardContent(project_id=document.project.id, title="当前分镜",
        script_version_id=script.id, bible_version_id=document.project.current_bible_version_id,
        production_spec_document_id=saved.document.id, production_spec_version_id=saved.version.id,
        script_line_ids=[item.id for item in script.lines], shots=document.shots,
        clip_groups=[ClipGroup(id=f"group-{index}", title=f"组 {index}", shot_ids=[item.id for item in shots],
            duration_seconds=sum(item.duration_seconds for item in shots), generation_intent="保持动作与人物连续")
            for index, shots in enumerate(groups)], computed_duration_seconds=sum(item.duration_seconds for item in document.shots))
    service.create_storyboard(document.project.id, StoryboardCreateRequest(storyboard=storyboard, origin=ProductionDocumentOrigin.AGENT))
    return service


def test_repair_updates_managed_group_and_next_generation_windows(tmp_path: Path) -> None:
    service = managed_project(tmp_path)
    document = service.repository.list_projects()[0]
    pid = document.project.id
    before = service.get_project(pid).document
    shot = before.shots[0]
    service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(
        decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.CHANGE_CAMERA,
        reason="给动作留出时间并简化机位", expected_shot_revision=shot.revision,
        shot_patch=ShotPlanPatch(camera="固定侧机位", duration_seconds=shot.duration_seconds + 1)))
    current = service.get_storyboard(pid)
    assert current.storyboard.shots[0].camera == "固定侧机位"
    assert current.storyboard.shots[1:] == before.shots[1:]
    assert current.storyboard.clip_groups[0].duration_seconds == sum(item.duration_seconds for item in current.storyboard.shots[:2])
    spec = service.get_production_spec(pid)
    compiled = service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=current.document.id, storyboard_version_id=current.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        origin=ProductionDocumentOrigin.AGENT))
    assert compiled.generation_plan.units[0].shot_windows[1].source_in_seconds == shot.duration_seconds + 1
    assert service.get_project(pid).document.shots == current.storyboard.shots
    with pytest.raises(WorkflowConflictError, match="changed.*revision"):
        service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(
            decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.CHANGE_CAMERA,
            reason="过期建议", expected_shot_revision=shot.revision, shot_patch=ShotPlanPatch(camera="过期机位")))
    assert service.get_storyboard(pid).version.id == current.version.id


def test_split_replaces_managed_shot_and_preserves_following_content(tmp_path: Path) -> None:
    service = managed_project(tmp_path)
    pid = service.repository.list_projects()[0].project.id
    before = service.get_storyboard(pid)
    shot = before.storyboard.shots[0]
    def replacement(suffix: str, with_dialogue: bool) -> ReplacementShotDraft:
        return ReplacementShotDraft(id=f"{shot.id}-{suffix}", duration_seconds=shot.duration_seconds / 2,
            narrative_purpose=shot.narrative_purpose, visual=shot.visual, framing=shot.framing,
            camera="固定机位", audio_intent=shot.audio_intent, dialogue_line_ids=shot.dialogue_line_ids if with_dialogue else [],
            continuity=shot.continuity, transition_out=shot.transition_out, local_rework_risk=shot.local_rework_risk)
    updated = service.decide_director_repair(pid, shot.id, DirectorRepairDecisionRequest(
        decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.SPLIT_SHOT,
        reason="分开动作与反应", split_shots=[replacement("action", True), replacement("reaction", False)]))
    current = service.get_storyboard(pid)
    assert [item.id for item in current.storyboard.shots[:2]] == [f"{shot.id}-action", f"{shot.id}-reaction"]
    assert current.storyboard.clip_groups[0].shot_ids[:2] == [item.id for item in current.storyboard.shots[:2]]
    assert current.storyboard.clip_groups[0].duration_seconds == before.storyboard.clip_groups[0].duration_seconds
    assert current.storyboard.computed_duration_seconds == before.storyboard.computed_duration_seconds
    assert [item.model_dump(exclude={"sequence"}) for item in current.storyboard.shots[2:]] == [item.model_dump(exclude={"sequence"}) for item in before.storyboard.shots[1:]]
    assert updated.document.shots == current.storyboard.shots


def test_director_repair_updates_incremental_draft_not_only_its_projection(tmp_path: Path) -> None:
    from tests.test_storyboard_incremental_service import (
        _prepare_script_only_project, _draft, _begin_and_plan_draft, _append_all_groups,
    )
    from short_drama.studio_storyboard import current_draft
    service = ShortDramaService(database_path=tmp_path / "draft.sqlite3", workspace_root=tmp_path / "media")
    document, script, spec, shots = _prepare_script_only_project(service)
    draft = _draft(document, script, spec, dialogue_shots=shots)
    begun = _begin_and_plan_draft(service, project_id=document.project.id, draft=draft)
    _append_all_groups(service, document=document, script=script, dialogue_shots=shots,
        draft_document_id=begun.draft_ref.artifact_id, base_version_id=begun.draft_version_id)
    before = service.studio.current_project(document.project.id).document
    selected = before.shots[0]
    service.decide_director_repair(document.project.id, selected.id, DirectorRepairDecisionRequest(
        decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.CHANGE_CAMERA,
        reason="修改正在逐组写入的分镜", expected_shot_revision=selected.revision,
        shot_patch=ShotPlanPatch(camera="新固定机位", duration_seconds=selected.duration_seconds + 0.5)))
    current = service.studio.current_project(document.project.id).document
    saved = current_draft(current)
    assert saved is not None
    assert current.shots[0].camera == "新固定机位"
    assert saved[2].committed_groups[0].shots[0] == current.shots[0]
    assert saved[2].committed_groups[0].clip_group.duration_seconds == selected.duration_seconds + 0.5
    assert current.shots[1:] == before.shots[1:]
