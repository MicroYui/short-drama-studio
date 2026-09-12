"""Text quoting resolves once on the owning Shot/Beat, then stores ordinary spans."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from _routes.studio import router as studio_router
from short_drama.deps import get_short_drama_service
from short_drama.models import ShotPlan, ShotReferenceCue, ShotTimelineBeat
from short_drama.production_contracts import StoryboardPatchRequest
from short_drama.reference_text import ShotTextAnchor, locate_text_anchor, remap_field_references
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioShotSave
from short_drama.tools.contracts import ToolCallStatus
from tests.test_storyboard_local_edits import _fixture


CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures/shot_reference_anchor_cases.json").read_text())["cases"]


def _beat(text: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"start_seconds": 0, "end_seconds": 4, "visual": text,
        "framing": "close", "camera": "static", "action": "hold", "references": refs}


def _cue(exact: str, **fields: Any) -> dict[str, Any]:
    return {"subject_asset_id": "asset-a", "alias": "人物_v7", "purpose": "当前实际选中图",
        "field": "visual", "text_anchor": {"exact": exact}, **fields}


def _shot(text: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": "local-shot", "project_id": "local-project", "sequence": 1, "duration_seconds": 4,
        "narrative_purpose": "定位实际引用", "visual": text, "framing": "近景", "camera": "固定",
        "audio_intent": "原声", "dialogue_line_ids": [], "references": refs,
        "continuity": {key: "保持当前事实" for key in (
            "character_positions", "eyeline", "prop_state_in", "prop_state_out", "action_in", "action_out")},
        "transition_out": "cut", "merge_candidate": False, "local_rework_risk": "无",
        "approval_status": "DRAFT", "freshness": "CURRENT"}


@pytest.mark.parametrize("case", CASES, ids=[f"{c['shot_id']}-{c['owner']['beat_index']}-{c['after']['alias']}" for c in CASES])
def test_five_real_wrong_offsets_resolve_to_the_actual_autonomous_correction(case: dict[str, Any]) -> None:
    incoming = deepcopy(case["before"])
    incoming.pop("text_start")
    incoming.pop("text_end")
    incoming["text_anchor"] = {"exact": case["actual_after_slice"]}
    is_shot = case["owner"]["beat_index"] is None
    raw = (_shot if is_shot else _beat)(case["body"], [incoming])
    original = deepcopy(raw)
    owner = (ShotPlan if is_shot else ShotTimelineBeat).model_validate(raw)
    assert owner.references[0].model_dump(mode="json") == case["after"]
    assert owner.visual[owner.references[0].text_start:owner.references[0].text_end] == case["actual_after_slice"]
    assert raw == original
    assert "text_anchor" not in owner.model_dump_json()
    # Supplying conflicting manual coordinates never silently replaces them.
    raw["references"][0].update(text_start=case["before"]["text_start"], text_end=case["before"]["text_end"])
    with pytest.raises(ValidationError, match="REFERENCE_TEXT_ANCHOR_CONFLICT"):
        (ShotPlan if is_shot else ShotTimelineBeat).model_validate(raw)


@pytest.mark.parametrize("field", ["visual", "camera", "action", "performance"])
def test_field_scope_unicode_and_typed_cue_do_not_mutate_the_input(field: str) -> None:
    text = "🙂e\u0301先看@人物，再停。"
    cue = ShotReferenceCue.model_validate(_cue("@人物", field=field))
    raw = _beat("另一个字段的@人物不参与定位", [])
    raw[field] = text
    raw["references"] = [cue]
    beat = ShotTimelineBeat.model_validate(raw)
    assert (beat.references[0].text_start, beat.references[0].text_end) == (5, 8)
    assert cue.text_start is None and cue.text_anchor is not None
    assert beat.references[0].text_anchor is None and beat.references[0].alias == "人物_v7"


def test_repeated_mentions_require_unique_context_and_can_bind_different_occurrences() -> None:
    text = "左侧@人物转身；右侧@人物停住。"
    with pytest.raises(ValidationError, match="AMBIGUOUS: 2"):
        ShotTimelineBeat.model_validate(_beat(text, [_cue("@人物")]))
    left = _cue("@人物", text_anchor={"exact": "@人物", "prefix": "左侧"})
    right = _cue("@人物", text_anchor={"exact": "@人物", "suffix": "停住。"})
    saved = ShotTimelineBeat.model_validate(_beat(text, [left, right]))
    assert [(cue.text_start, cue.text_end) for cue in saved.references] == [(2, 5), (10, 13)]
    assert saved.references[0].subject_asset_id == saved.references[1].subject_asset_id
    with pytest.raises(ValueError, match="AMBIGUOUS: 2"):
        locate_text_anchor("aaa", ShotTextAnchor(exact="aa"))


@pytest.mark.parametrize("anchor", [
    {"exact": "@不存在"}, {"exact": "@人物", "prefix": "不相邻"},
    {"exact": "@人物", "suffix": "不相邻"}, {"exact": "é"},
])
def test_missing_or_normalized_quote_is_not_guessed(anchor: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="NOT_FOUND"):
        ShotTimelineBeat.model_validate(_beat("e\u0301 @人物", [_cue("", text_anchor=anchor)]))


def test_legacy_range_custom_alias_and_whole_shot_reference_are_unchanged() -> None:
    legacy = _cue("unused", id="human-range", text_start=2, text_end=5)
    legacy.pop("text_anchor")
    whole = {"subject_asset_id": "asset-b", "alias": "背景_v2", "purpose": "整镜参考"}
    raw = _beat("🙂看@人物站定", [legacy, whole])
    saved = ShotTimelineBeat.model_validate(raw)
    assert (saved.references[0].text_start, saved.references[0].text_end) == (2, 5)
    assert saved.references[0].alias == "人物_v7"
    assert saved.references[1].field is None and saved.references[1].text_start is None
    # A historical in-range value is not silently relabeled by new parsing.
    legacy["text_start"], legacy["text_end"] = 0, 2
    assert ShotTimelineBeat.model_validate(_beat(raw["visual"], [legacy])).references[0].text_start == 0


def test_partial_rename_does_not_rebind_to_an_unchanged_same_text_occurrence() -> None:
    first = ShotReferenceCue(subject_asset_id="asset-a", id="first", alias="人物_v1", purpose="首处",
        field="visual", text_start=1, text_end=4)
    second = first.model_copy(update={"id": "second", "text_start": 6, "text_end": 9})
    raw = [first, second]
    result = remap_field_references(raw, field="visual", before="前@人物，后@人物。", after="前@角色，后@人物。")
    assert [cue.id for cue in result] == ["second"]
    assert result[0].text_start == 6 and first.text_start == 1
    result = remap_field_references(raw, field="visual", before="前@人物，后@人物。", after="前@人物，后面再看@人物。")
    assert [(cue.id, cue.text_start) for cue in result] == [("first", 1), ("second", 9)]
    # A second punctuation edit enlarges the changed region. It does not make
    # the surviving other mention a proven replacement for the first one.
    with pytest.raises(ValueError, match="REFERENCE_TEXT_REMAP_AMBIGUOUS"):
        remap_field_references([first], field="visual", before="前@人物，后@人物。", after="前@角色，后@人物！")


def _anchored_shot(shot: ShotPlan) -> dict[str, Any]:
    payload = shot.model_dump(mode="json")
    cue = payload["references"][0]
    cue.update(id=None, field="visual", alias="人工自定义_v12", text_start=None, text_end=None,
        text_anchor={"exact": "@人物"}, usage="EDITORIAL")
    payload["references"] = [cue]
    payload["visual"] = "🙂先停，再看@人物。"
    payload["camera"] = "在@人物的视线高度固定。"
    camera_cue = deepcopy(cue)
    camera_cue.update(field="camera", id="camera-ref")
    payload["references"].append(camera_cue)
    payload["timeline_beats"] = [_beat("先看@人物。", [deepcopy(cue)])]
    payload["timeline_beats"][0].update(end_seconds=shot.duration_seconds, dialogue_line_ids=shot.dialogue_line_ids)
    return payload


def test_actual_tool_and_http_save_store_resolved_cues_and_return_saved_values(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    payload = _anchored_shot(shot)
    arguments = {"project_id": pid, "shot_id": shot.id, "request": {
        "shot": payload, "expected_revision": shot.revision}}
    original = deepcopy(arguments)
    tool = service.tool_registry.invoke("shot.save_current", arguments)
    assert tool.status == ToolCallStatus.SUCCEEDED, tool.error
    assert arguments == original
    assert tool.output is not None and tool.receipt is not None
    saved = service.get_storyboard(pid).storyboard.shots[0]
    assert [(cue.text_start, cue.text_end) for cue in saved.references] == [(6, 9), (1, 4)]
    assert (saved.timeline_beats[0].references[0].text_start, saved.timeline_beats[0].references[0].text_end) == (2, 5)
    summary = tool.output["saved_shot"]
    assert isinstance(summary, dict)
    assert summary == tool.receipt.current_state["saved_shot"]
    assert isinstance(summary["shot_cues"], list)
    assert all(isinstance(item, dict) and item["referenced_text"] == "@人物" for item in summary["shot_cues"])
    assert all(cue.asset_version_id == shot.references[0].asset_version_id for cue in saved.references)
    # Existing UI/manual offsets and alias pass through the same HTTP endpoint.
    app = FastAPI()
    app.include_router(studio_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    manual = saved.model_dump(mode="json")
    manual["camera"] = "人工机位；" + manual["camera"]
    manual["references"][1]["text_start"] += 5
    manual["references"][1]["text_end"] += 5
    response = TestClient(app).put(f"/studio/projects/{pid}/shots/{shot.id}",
        json={"expected_revision": saved.revision, "shot": manual})
    assert response.status_code == 200, response.text
    assert response.json()["project"]["document"]["shots"][0]["references"] == manual["references"]
    actual = service.get_storyboard(pid).storyboard.shots[0]
    assert actual.references[1].text_start == 6 and actual.references[1].alias == "人工自定义_v12"
    before = service.get_project(pid).document
    stale = TestClient(app).put(f"/studio/projects/{pid}/shots/{shot.id}", json=arguments["request"])
    assert stale.status_code == 409
    assert service.get_project(pid).document == before


def test_local_anchor_patch_merges_unrelated_human_edit_and_rejects_ambiguity_atomically(tmp_path: Path) -> None:
    service, board = _fixture(tmp_path)
    pid, shot = board.storyboard.project_id, board.storyboard.shots[0]
    request = {"base_version_id": board.version.id, "origin": "AGENT", "replacements": [{
        "shot_id": shot.id, "expected_revision": shot.revision, "replacement": _anchored_shot(shot)}]}
    other = board.storyboard.shots[-1]
    service.studio.save_shot(pid, other.id, StudioShotSave(
        shot=other.model_copy(update={"camera": "用户单独保留的机位"}), expected_revision=other.revision))
    typed = StoryboardPatchRequest.model_validate(request)
    result = service.patch_storyboard(pid, typed, current_content_edit=True)
    assert result.storyboard.shots[-1].camera == "用户单独保留的机位"
    assert result.storyboard.shots[0].references[0].text_start == 6
    before = service.get_project(pid).document
    invalid = _anchored_shot(result.storyboard.shots[0])
    invalid["visual"] += " 再看@人物。"
    failed = service.tool_registry.invoke("shot.save_current", {"project_id": pid, "shot_id": shot.id,
        "request": {"expected_revision": result.storyboard.shots[0].revision, "shot": invalid}})
    assert failed.status == ToolCallStatus.INVALID_ARGUMENTS
    assert service.get_project(pid).document == before
