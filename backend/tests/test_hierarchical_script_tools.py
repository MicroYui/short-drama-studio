"""Tests for composable hierarchical screenplay tools."""

from __future__ import annotations

import json
from pathlib import Path

from short_drama.hierarchical_script import (
    _scene_writer_prompt,
    assemble_written_scenes,
    recommend_script_revision_context,
    write_story_scene,
)
from short_drama.models import (
    CreateProjectRequest,
    RunMode,
    SourceMode,
)
from short_drama.production_planner import (
    EpisodeOutline,
    PlannedDialogueLine,
    create_story_architecture,
)
from short_drama.repository import ShortDramaRepository
from short_drama.script_documents import (
    EpisodeOutlinePatch,
    EpisodeScenePatch,
    PlanningScriptDocumentKind,
    apply_episode_outline_patch,
    build_planning_script_documents,
    scene_dialogue_lines,
)


def _request(seconds: int) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=f"{seconds} 秒分层剧本",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=seconds,
        run_mode=RunMode.GUIDED,
    )


def _scene_patch(outline, sequence: int, suffix: str) -> EpisodeScenePatch:
    scene = outline.scenes[sequence - 1]
    first = outline.characters[0].name
    return EpisodeScenePatch(
        scene_sequence=scene.sequence,
        heading=scene.heading,
        summary=scene.summary,
        narrative_purpose=scene.narrative_purpose,
        playable_actions=list(scene.playable_actions),
        target_duration_seconds=scene.target_duration_seconds,
        location_names=list(scene.location_names),
        continuity_in=scene.continuity_in.model_copy(deep=True),
        continuity_out=scene.continuity_out.model_copy(deep=True),
        lines=[PlannedDialogueLine(speaker=first, text=f"第{sequence}场{suffix}")],
    )


def _explicit_multi_scene_blueprint(
    request: CreateProjectRequest,
    *,
    scene_count: int,
) -> EpisodeOutline:
    """Build the explicit multi-scene model output required by these tests."""

    outline = create_story_architecture(request, None).outline
    template = outline.scenes[0]
    scene_duration = request.resolved_target_duration_seconds() / scene_count
    scenes = []
    for sequence in range(1, scene_count + 1):
        continuity_in = template.continuity_in.model_copy(
            deep=True,
            update={
                "known_facts": (
                    [] if sequence == 1 else [f"第 {sequence - 1} 场已完成"]
                ),
                "open_threads": [f"第 {sequence} 场待推进"],
            },
        )
        continuity_out = template.continuity_out.model_copy(
            deep=True,
            update={
                "known_facts": [f"第 {sequence} 场已完成"],
                "open_threads": (
                    [f"第 {sequence + 1} 场待推进"]
                    if sequence < scene_count
                    else []
                ),
            },
        )
        scenes.append(
            template.model_copy(
                deep=True,
                update={
                    "sequence": sequence,
                    "heading": f"第 {sequence} 场",
                    "summary": f"第 {sequence} 场因果链",
                    "narrative_purpose": f"推进第 {sequence} 场因果链",
                    "playable_actions": [f"完成第 {sequence} 场行动"],
                    "target_duration_seconds": scene_duration,
                    "dialogue_line_indices": [],
                    "continuity_in": continuity_in,
                    "continuity_out": continuity_out,
                },
            )
        )
    return EpisodeOutline.model_validate(
        {
            **outline.model_dump(mode="python"),
            "scenes": [scene.model_dump(mode="python") for scene in scenes],
        }
    )


def test_four_seconds_through_sixty_minutes_share_the_same_scene_node_tools() -> None:
    brief_request = _request(4)
    short_request = _request(180)
    long_request = _request(3600)
    brief_blueprint = _explicit_multi_scene_blueprint(brief_request, scene_count=1)
    short_blueprint = _explicit_multi_scene_blueprint(short_request, scene_count=3)
    long_blueprint = _explicit_multi_scene_blueprint(long_request, scene_count=6)

    assert brief_blueprint.lines == []
    assert short_blueprint.lines == []
    assert long_blueprint.lines == []
    assert all(not scene.dialogue_line_indices for scene in brief_blueprint.scenes)
    assert all(not scene.dialogue_line_indices for scene in short_blueprint.scenes)
    assert all(not scene.dialogue_line_indices for scene in long_blueprint.scenes)
    assert len(brief_blueprint.scenes) <= len(short_blueprint.scenes) < len(long_blueprint.scenes)

    brief_first = write_story_scene(
        brief_request,
        brief_blueprint,
        brief_blueprint.scenes[0],
        [],
        None,
    ).patch
    short_patches = [
        write_story_scene(short_request, short_blueprint, scene, [], None).patch
        for scene in short_blueprint.scenes
    ]
    long_first = write_story_scene(
        long_request,
        long_blueprint,
        long_blueprint.scenes[0],
        [],
        None,
    ).patch

    short_script = assemble_written_scenes(short_blueprint, short_patches)
    assert short_script.lines
    assert len(short_script.scenes) == len(short_blueprint.scenes)
    assert brief_first.scene_sequence == 1
    assert len(brief_first.lines) == 1
    assert long_first.scene_sequence == 1
    assert long_first.lines


def test_scene_writer_keeps_fast_pacing_out_of_telegraphic_dialogue() -> None:
    request = _request(180)
    blueprint = create_story_architecture(request, None).outline

    instructions, _ = _scene_writer_prompt(
        request,
        blueprint,
        blueprint.scenes[0],
        [],
    )

    assert "actions, reactions, and blocking carry state changes directly" in instructions
    assert "Never verbalize a scene contract" in instructions
    assert "must not be translated into compressed or slogan-like speech" in instructions
    assert "not every line needs to add a new fact" in instructions


def test_scope_recommendation_compacts_search_text_but_keeps_long_edit_valid() -> None:
    request = _request(180)
    outline = create_story_architecture(request, None).outline
    instruction = "局部修改指令" * 1000

    plan = recommend_script_revision_context(instruction, outline, [], None)

    assert len(instruction) > 4000
    assert len(plan.retrieval_query) == 4000
    assert plan.user_overridable is True


def test_scene_patch_merge_does_not_regenerate_unrelated_scenes() -> None:
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    initial = assemble_written_scenes(
        blueprint,
        [_scene_patch(blueprint, scene.sequence, "初稿") for scene in blueprint.scenes],
    )
    before_first = scene_dialogue_lines(initial, initial.scenes[0])
    replacement = _scene_patch(initial, 2, "局部修改")

    revised = apply_episode_outline_patch(
        initial,
        EpisodeOutlinePatch(
            base_revision_round=0,
            rationale="用户只修改第二场",
            scene_patches=[replacement],
        ),
    )

    assert scene_dialogue_lines(revised, revised.scenes[0]) == before_first
    assert scene_dialogue_lines(revised, revised.scenes[1])[0].text.endswith("局部修改")
    assert [scene.sequence for scene in revised.scenes] == [
        scene.sequence for scene in initial.scenes
    ]


def test_versioned_script_search_is_isolated_by_run_and_revision(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    request = _request(180)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    outline = assemble_written_scenes(
        blueprint,
        [_scene_patch(blueprint, scene.sequence, "搜索文本") for scene in blueprint.scenes],
    )
    run_one_docs = build_planning_script_documents(
        run_id="run-one",
        revision_round=0,
        outline=outline,
        created_at="2026-08-25T00:00:00Z",
    )
    run_two_docs = build_planning_script_documents(
        run_id="run-two",
        revision_round=0,
        outline=outline.model_copy(update={"title": "另一个剧本"}),
        created_at="2026-08-25T00:00:00Z",
    )
    repository.replace_planning_script_documents("run-one", 0, run_one_docs)
    repository.replace_planning_script_documents("run-two", 0, run_two_docs)

    hits = repository.search_planning_script_documents(
        "run-one",
        0,
        "第 2 场因果链搜索文本",
        limit=8,
        include_story_bible=False,
    )

    assert hits
    assert all(hit.document.run_id == "run-one" for hit in hits)
    assert all(
        hit.document.kind == PlanningScriptDocumentKind.SCENE for hit in hits
    )
    assert any(hit.document.scene_sequence == 2 for hit in hits)
    assert json.loads(hits[0].document.model_dump_json())["revision_round"] == 0
