"""A cut may continue one utterance without dropping its actual audible window."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from short_drama.dialogue_sequence import ordered_dialogue_line_ids
from short_drama.models import ContentPlanningOutput, DirectorRepairDecision, DirectorRepairStrategy, ProductionDocumentOrigin, ProductionProfile, ProjectStage, ShotPlan
from short_drama.production_contracts import (
    ClipGroup, ProductionSpecCreateRequest, StoryboardClipGroupDraft, StoryboardClipGroupSplice,
    StoryboardContent, StoryboardDraftAppendRequest, StoryboardDraftBeginRequest,
    StoryboardDraftContent, StoryboardDraftFinalizeRequest, StoryboardDraftPhase,
    StoryboardDraftSceneSeed, StoryboardDraftSeed, StoryboardPatchRequest, StoryboardSceneNode,
    StoryboardShotSplice, StoryboardSourceRange, StoryboardWorkUnitContract, assemble_storyboard_draft,
)
from short_drama.service import ShortDramaService
from short_drama.reference_planner import DirectorRepairDecisionRequest, ShotPlanPatch
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt
from short_drama.studio_models import StudioShotSave
from short_drama.tools import ToolCallStatus
from tests.test_storyboard_agent_interfaces import _production_spec
from tests.test_storyboard_incremental_contracts import _shot
from tests.test_storyboard_local_edits import _fixture


def _actual_shots() -> list[ShotPlan]:
    data = cast(dict[str, JsonValue], json.loads((Path(__file__).parent / "fixtures/storyboard_continued_dialogue_shots.json").read_text()))
    return [ShotPlan.model_validate(raw).model_copy(update={"sequence": index})
            for index, raw in enumerate(cast(list[JsonValue], data["shots"]), start=1)]


def _group(shots: list[ShotPlan], group_id: str = "continuous-group") -> ClipGroup:
    return ClipGroup(id=group_id, title="动作与证据插入", shot_ids=[shot.id for shot in shots],
        duration_seconds=sum(shot.duration_seconds for shot in shots),
        generation_intent="同一句普通话跨硬切延续，只说一次；画面从动作切到可读证据。")


def _board(shots: list[ShotPlan], source_ids: list[str], groups: list[ClipGroup] | None = None) -> StoryboardContent:
    groups = groups or [_group(shots)]
    scene_id = shots[0].scene_id
    assert scene_id is not None
    return StoryboardContent(project_id=shots[0].project_id, title="一次跨镜发声", script_version_id="script",
        bible_version_id="bible", production_spec_document_id="spec", production_spec_version_id="spec-v1",
        script_line_ids=source_ids, shots=shots, clip_groups=groups,
        scene_nodes=[StoryboardSceneNode(id="scene-node", scene_id=scene_id, sequence=1, heading="内景会议室",
            source_line_ids=source_ids, shot_ids=[shot.id for shot in shots], clip_group_ids=[g.id for g in groups])],
        computed_duration_seconds=sum(shot.duration_seconds for shot in shots))


def _small_shots(ids: list[list[str]]) -> list[ShotPlan]:
    return [_shot(shot_id=f"shot-{i}", scene_id="scene", line_id="unused", duration=1).model_copy(
        update={"sequence": i + 1, "dialogue_line_ids": lines}) for i, lines in enumerate(ids)]


@pytest.mark.parametrize("lines,expected", [
    ([["A"], ["A"]], ["A"]),
    ([["A", "B"], ["B", "C"]], ["A", "B", "C"]),
    ([["A"], [], ["A"]], ["A", "A"]),
    ([["A"], ["B"], ["A"]], ["A", "B", "A"]),
    ([["A", "B", "A"], ["A"]], ["A", "B", "A"]),
    ([["same-text-first-id"], ["same-text-second-id"]], ["same-text-first-id", "same-text-second-id"]),
])
def test_ordered_coverage_only_folds_adjacent_shot_boundaries(lines: list[list[str]], expected: list[str]) -> None:
    shots = _small_shots(lines)
    # The ClipGroup order is authoritative even if a caller's lookup list is reversed.
    assert ordered_dialogue_line_ids(list(reversed(shots)), clip_groups=[_group(shots)]) == expected


def test_actual_failed_split_preserves_all_four_seconds_in_storyboard_scene_and_draft() -> None:
    shots = _actual_shots()
    assert [shot.duration_seconds for shot in shots] == [2.5, 1.5]
    assert [[beat.dialogue_line_ids for beat in shot.timeline_beats] for shot in shots] == [
        [["line-yqq-02"], ["line-yqq-02"]], [["line-yqq-02"]]]
    board = _board(shots, ["line-yqq-02"])
    node = board.scene_nodes[0]
    source = StoryboardSourceRange(id="source", scene_ids=[node.scene_id], script_line_ids=board.script_line_ids,
        description="许岚出示报价单，画面切到准确可读证据，同一句话连续说完。")
    commit = StoryboardClipGroupDraft(work_unit_id="work", scene_node_id=node.id, scene_group_sequence=1,
        source_range=source, shots=shots, clip_group=board.clip_groups[0])
    draft = StoryboardDraftContent(project_id=board.project_id, title=board.title, phase=StoryboardDraftPhase.GROUP_AUTHORING,
        script_version_id=board.script_version_id, bible_version_id=board.bible_version_id,
        production_spec_document_id=board.production_spec_document_id, production_spec_version_id=board.production_spec_version_id,
        script_line_ids=board.script_line_ids,
        scene_nodes=[node.model_copy(update={"shot_ids": [], "clip_group_ids": []})], committed_groups=[commit])
    assembled = assemble_storyboard_draft(draft)
    assert assembled.shots == board.shots
    assert assembled.scene_nodes[0].source_line_ids == ["line-yqq-02"]
    audible_seconds = sum(beat.end_seconds - beat.start_seconds for shot in assembled.shots
        for beat in shot.timeline_beats if "line-yqq-02" in beat.dialogue_line_ids)
    assert audible_seconds == 4.0
    assert StoryboardContent.model_validate_json(assembled.model_dump_json()) == assembled


def test_director_actual_prompt_and_schema_explain_continuation_coverage() -> None:
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(ProductionSkillRole.DIRECTOR,),
        stage=ProductionPromptStage.STORYBOARD, method_skill_ids=("method.drama_series",))
    assert "两镜与有声时段都引用同一行" in prompt.text
    assert "把后镜 ID 留空" in prompt.text
    assert "Do not create a new ScriptLine" in (ShotPlan.model_fields["dialogue_line_ids"].description or "")


@pytest.mark.parametrize("lines,across_groups", [
    ([["A"], ["A"]], True),
    ([["A"], [], ["A"]], False),
    ([["A"], ["B"], ["A"]], False),
])
def test_separate_groups_and_noncontiguous_occurrences_are_not_hidden(lines: list[list[str]], across_groups: bool) -> None:
    shots = _small_shots(lines)
    groups = [_group([shot], f"group-{i}") for i, shot in enumerate(shots)] if across_groups else [_group(shots)]
    with pytest.raises(ValidationError, match="Cross-group or non-contiguous"):
        _board(shots, list(dict.fromkeys(line for value in lines for line in value)), groups)


def test_different_line_ids_keep_two_utterances_and_missing_lines_still_fail() -> None:
    shots = _small_shots([["first"], ["second"]])
    for shot in shots:
        shot.audio_intent = "完全相同的一句台词，作为两个明确不同来源的发声。"
    assert _board(shots, ["first", "second"]).script_line_ids == ["first", "second"]
    with pytest.raises(ValidationError, match="preserve every source script line"):
        _board(shots, ["first", "missing", "second"])


def test_incremental_draft_rejects_a_continuation_split_across_requests() -> None:
    shots = _actual_shots()
    board = _board(shots, ["line-yqq-02"])
    node = board.scene_nodes[0]
    groups = [StoryboardClipGroupDraft(work_unit_id=f"work-{i}", scene_node_id=node.id,
        scene_group_sequence=i, source_range=StoryboardSourceRange(id=f"source-{i}",
            scene_ids=[node.scene_id], script_line_ids=board.script_line_ids, description="同一句跨切镜"),
        shots=[shot], clip_group=_group([shot], f"request-{i}")) for i, shot in enumerate(shots, start=1)]
    with pytest.raises(ValidationError, match="cannot repeat across ClipGroups"):
        StoryboardDraftContent(project_id=board.project_id, title=board.title, phase=StoryboardDraftPhase.GROUP_AUTHORING,
            script_version_id=board.script_version_id, bible_version_id=board.bible_version_id,
            production_spec_document_id=board.production_spec_document_id, production_spec_version_id=board.production_spec_version_id,
            script_line_ids=board.script_line_ids, committed_groups=groups,
            scene_nodes=[node.model_copy(update={"shot_ids": [], "clip_group_ids": []})])


def _continued_split(source: ShotPlan) -> list[ShotPlan]:
    assert len(source.dialogue_line_ids) == 1
    actual = _actual_shots()
    return [ShotPlan.model_validate({**source.model_dump(mode="json"),
        "id": source.id if i == 0 else source.id + "-evidence", "duration_seconds": shot.duration_seconds,
        "timeline_beats": [{**beat.model_dump(mode="json"), "dialogue_line_ids": source.dialogue_line_ids,
            "references": []} for beat in shot.timeline_beats]}) for i, shot in enumerate(actual)]


def test_local_split_tool_saves_continuation_and_rejects_cross_group_change_atomically(tmp_path: Path) -> None:
    service, before = _fixture(tmp_path)
    target = next(shot for shot in before.storyboard.shots if len(shot.dialogue_line_ids) == 1)
    children = _continued_split(target)
    request = StoryboardPatchRequest(base_version_id=before.version.id,
        shot_splices=[StoryboardShotSplice(shot_ids=[target.id], shots=children)], origin=ProductionDocumentOrigin.AGENT)
    result = service.tool_registry.invoke("shot.patch_storyboard", {"project_id": before.storyboard.project_id,
        "request": request.model_dump(mode="json")})
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    saved = service.get_storyboard(before.storyboard.project_id)
    current = {shot.id: shot for shot in saved.storyboard.shots}
    assert [current[shot.id].dialogue_line_ids for shot in children] == [target.dialogue_line_ids] * 2
    assert sum(current[shot.id].duration_seconds for shot in children) == 4
    assert saved.storyboard.script_line_ids == before.storyboard.script_line_ids
    for old in before.storyboard.shots:
        if old.id != target.id:
            assert current[old.id].model_dump(exclude={"sequence"}) == old.model_dump(exclude={"sequence"})
    group = next(group for group in saved.storyboard.clip_groups if children[0].id in group.shot_ids)
    split_groups = [group.model_copy(update={"id": group.id + str(i), "shot_ids": [child.id],
        "duration_seconds": child.duration_seconds}) for i, child in enumerate(children)]
    invalid = StoryboardPatchRequest(base_version_id=saved.version.id,
        clip_group_splices=[StoryboardClipGroupSplice(clip_group_ids=[group.id], clip_groups=split_groups)],
        origin=ProductionDocumentOrigin.AGENT)
    rejected = service.tool_registry.invoke("shot.patch_storyboard", {"project_id": before.storyboard.project_id,
        "request": invalid.model_dump(mode="json")})
    assert rejected.status == ToolCallStatus.FAILED
    assert rejected.error is not None and "Cross-group" in rejected.error.message
    assert service.get_storyboard(before.storyboard.project_id) == saved
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=tmp_path / "reopened")
    assert reopened.get_storyboard(before.storyboard.project_id).storyboard == saved.storyboard


def test_draft_append_direct_edit_and_finalize_preserve_continued_source_once(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "db.sqlite3", workspace_root=tmp_path / "workspace")
    document = service.initialize_fixture().document
    original = list(document.shots)
    script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
    assert len(script.structured_scenes) == 1
    scene = script.structured_scenes[0]
    document.project.content_planning_output, document.project.stage = ContentPlanningOutput.SCRIPT_ONLY, ProjectStage.SCRIPT_READY
    document.shots, document.execution_plan, document.reference_plan = [], [], []
    service.repository.save_project(document)
    pid = document.project.id
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.AGENT))
    seed = StoryboardDraftSeed(project_id=pid, title="跨镜对白增量保存", script_version_id=script.id,
        bible_version_id=document.project.current_bible_version_id, production_spec_document_id=spec.document.id,
        production_spec_version_id=spec.version.id, scene_nodes=[StoryboardDraftSceneSeed(id="node", scene_id=scene.id, sequence=1)])
    progress = service.begin_storyboard_draft(pid, StoryboardDraftBeginRequest(seed=seed, origin=ProductionDocumentOrigin.AGENT)).progress
    split_done = False
    first_split_id = ""
    for i, original_shot in enumerate(original, start=1):
        source = original_shot.model_copy(update={"scene_id": scene.id})
        splitting = not split_done and len(source.dialogue_line_ids) == 1
        shots = _continued_split(source) if splitting else [source]
        source_range = StoryboardSourceRange(id=f"source-{i}", scene_ids=[scene.id],
            script_line_ids=source.dialogue_line_ids, description="保持当前动作及一次对白。")
        work = StoryboardWorkUnitContract(id=f"work-{i}", title="当前片段", source_range=source_range,
            objective="保存当前可执行片段", completion_criteria=["实际对白时段和动作对应"])
        group = StoryboardClipGroupDraft(work_unit_id=work.id, scene_node_id="node", scene_group_sequence=i,
            source_range=source_range, shots=shots, clip_group=_group(shots, f"group-{i}"))
        progress = service.append_storyboard_group(pid, StoryboardDraftAppendRequest(draft_document_id=progress.draft_ref.artifact_id,
            base_version_id=progress.draft_version_id, work_unit=work, group=group, origin=ProductionDocumentOrigin.AGENT)).progress
        if splitting:
            split_done, first_split_id = True, shots[0].id
            current = next(shot for shot in service.studio.current_project(pid).document.shots if shot.id == first_split_id)
            current.camera = "用户仅改平视固定机位，保持跨切镜的一句。"
            service.studio.save_shot(pid, current.id, StudioShotSave(shot=current, expected_revision=current.revision))
            current = next(shot for shot in service.studio.current_project(pid).document.shots if shot.id == first_split_id)
            service.decide_director_repair(pid, current.id, DirectorRepairDecisionRequest(
                decision=DirectorRepairDecision.APPLIED, strategy=DirectorRepairStrategy.CHANGE_CAMERA,
                reason="仅细化当前机位；一句对白继续覆盖原有四秒。",
                shot_patch=ShotPlanPatch(camera=current.camera + "保持人物轴线。", timeline_beats=current.timeline_beats),
                expected_shot_revision=current.revision))
            latest = service.get_production_document(pid, progress.draft_ref.artifact_id)
            draft = StoryboardDraftContent.model_validate(latest.version.payload)
            assert draft.committed_groups[-1].source_range.script_line_ids == source.dialogue_line_ids
            progress.draft_version_id = latest.version.id
    assert split_done
    final = service.finalize_storyboard(pid, StoryboardDraftFinalizeRequest(draft_document_id=progress.draft_ref.artifact_id,
        draft_version_id=progress.draft_version_id, origin=ProductionDocumentOrigin.AGENT))
    assert final.storyboard.script_line_ids == [line.id for line in script.lines]
    assert final.storyboard.scene_nodes[0].source_line_ids == script.structured_scenes[0].line_ids
    current_shot = next(shot for shot in final.storyboard.shots if shot.id == first_split_id)
    assert current_shot.camera.startswith("用户仅改")
    assert [shot.dialogue_line_ids for shot in final.storyboard.shots
        if shot.id in {first_split_id, first_split_id + "-evidence"}] == [current_shot.dialogue_line_ids] * 2
