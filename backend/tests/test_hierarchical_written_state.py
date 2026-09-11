"""The reachable compatibility writer carries written facts, not fulfilled plans."""

import json
from pathlib import Path

import pytest

from short_drama.hierarchical_script import _scene_writer_prompt, _validate_scene_patch, write_story_scene
from short_drama.models import SceneContinuitySummary
from short_drama.script_documents import EpisodeScenePatch
from short_drama.service import ShortDramaService
from tests.test_hierarchical_script_tools import (
    _CapturingLanguageClient, _explicit_multi_scene_blueprint, _request, _scene_patch,
)


def test_planning_advance_remains_discoverable_from_current_registry(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "planning.sqlite", workspace_root=tmp_path / "media")
    ids = {item.id for item in service.tool_registry.list(tags=frozenset({"planning"}))}
    assert {"planning.advance", "planning.start_script_revision"} <= ids
    assert service.tool_registry.describe("planning.advance").input_schema


def test_written_state_survives_serialization_and_drives_next_scene_instead_of_outline() -> None:
    request = _request(60)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    first = blueprint.scenes[0]
    first.continuity_out.known_facts = ["计划：她已经答应归还雨伞。"]
    blueprint.scenes[1].continuity_in.known_facts = ["计划：雨伞已经归还。"]
    output = {
        "scene_sequence": 1,
        "blocks": [{"kind": "ACTION", "text": "雨已经停了。她捏着伞柄，没有递出去。"}],
        "written_scene": {
            "summary": "她借着还伞的机会停留，却仍没把伞递出去。",
            "entry_state": {"setting": first.continuity_in.setting, "prop_states": ["她拿着借来的伞"]},
            "exit_state": {
                "setting": first.continuity_out.setting, "prop_states": ["雨伞仍在她手中"],
                "known_facts": ["雨已停，她仍未归还雨伞"], "open_threads": ["对方尚未回应她的停留"],
            },
        },
    }
    client = _CapturingLanguageClient(json.dumps(output, ensure_ascii=False))
    result = write_story_scene(request, blueprint, first, [], client)
    assert not result.retry_required and not result.used_fallback, result.retry_feedback
    assert result.patch is not None
    # This is the JSON persisted in ContentPlanningJob.scene_script_json and
    # restored before the next planning.advance call.
    restored = EpisodeScenePatch.model_validate_json(result.patch.model_dump_json())
    assert restored.state_source == "SCREENPLAY"
    assert restored.summary == output["written_scene"]["summary"]
    assert restored.continuity_out.known_facts == ["雨已停，她仍未归还雨伞"]
    assert restored.continuity_in.prop_states == ["她拿着借来的伞"]
    _, next_input = _scene_writer_prompt(request, blueprint, blueprint.scenes[1], [restored])
    payload = json.loads(next_input)
    assert payload["previous_scene_boundary"]["state_source"] == "SCREENPLAY"
    assert payload["previous_scene_boundary"]["outcome"] == restored.summary
    assert payload["story_state_before_current_scene"]["prop_states"] == ["雨伞仍在她手中"]
    assert payload["current_scene_node"]["continuity_in"]["known_facts"] == ["计划：雨伞已经归还。"]
    assert "不要把蓝图要求但没有演出的事件算作已完成" in client.instructions[0]
    assert "written_scene" in client.inputs[0]["output_schema"]["properties"]


@pytest.mark.parametrize("legacy_shape", ["wide_patch", "blocks"])
def test_legacy_response_stays_compatible_but_its_outline_is_not_reported_as_written_fact(legacy_shape: str) -> None:
    request = _request(60)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    first = blueprint.scenes[0]
    first.continuity_out.known_facts = ["计划里有人获奖，但正文尚未发生。"]
    legacy = _scene_patch(blueprint, 1, "没有领奖，仍在等待。")
    if legacy_shape == "wide_patch":
        output = legacy.model_dump(mode="json")
        output.pop("state_source")
    else:
        output = {"scene_sequence": 1, "blocks": [{"kind": "ACTION", "text": "他望着门口，仍在等待。"}]}
    result = write_story_scene(request, blueprint, first, [], _CapturingLanguageClient(json.dumps(output)))
    assert result.patch is not None and not result.retry_required
    assert result.patch.state_source == "OUTLINE"
    _, next_input = _scene_writer_prompt(request, blueprint, blueprint.scenes[1], [result.patch])
    payload = json.loads(next_input)
    boundary = payload["previous_scene_boundary"]
    assert boundary["state_source"] == "OUTLINE"
    assert boundary["outcome"] is None and boundary["continuity_out"] is None
    assert boundary["outline_summary"] == first.summary
    assert boundary["final_rendered_line"]
    assert payload["story_state_before_current_scene"] is None


def test_older_outline_state_is_not_promoted_to_fulfilled_rollup() -> None:
    request = _request(60)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=3)
    older = _scene_patch(blueprint, 1, "旧格式写作。")
    recent = _scene_patch(blueprint, 2, "新格式写作。")
    recent.state_source = "SCREENPLAY"
    recent.continuity_out = SceneContinuitySummary(setting="走廊", known_facts=["门还没打开"])
    _, next_input = _scene_writer_prompt(request, blueprint, blueprint.scenes[2], [older, recent])
    payload = json.loads(next_input)
    assert payload["older_scene_rollup"] is None
    assert payload["story_state_before_current_scene"]["known_facts"] == ["门还没打开"]


def test_authored_state_does_not_allow_changing_unrelated_blueprint_fields() -> None:
    request = _request(60)
    blueprint = _explicit_multi_scene_blueprint(request, scene_count=2)
    patch = _scene_patch(blueprint, 1, "实际写出的场次。")
    patch.state_source = "SCREENPLAY"
    patch.summary = "实际正文可以形成比蓝图更精确的摘要。"
    patch.continuity_out.known_facts = ["一个明确发生的事实"]
    _validate_scene_patch(request, blueprint, blueprint.scenes[0], patch)
    patch.heading = "未经授权换了地点"
    with pytest.raises(ValueError, match="blueprint-owned fields: heading"):
        _validate_scene_patch(request, blueprint, blueprint.scenes[0], patch)
