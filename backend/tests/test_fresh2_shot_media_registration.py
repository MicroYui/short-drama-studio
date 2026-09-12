"""Replay saved Fresh2 S04 relationship repairs without media generation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
from typing import Any, cast

from PIL import Image
import pytest

from short_drama.models import Asset, ProjectDocument, ReferencePack, ShotPlan, ShotReferenceCue
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolCallStatus
from tests.test_asset_plan_runtime import _service, _setup_story_truth

FIXTURE = Path(__file__).parent / "fixtures" / "fresh2_shot_media_registration.json"
SELECTED = "asset-version-2e42598d5807"
OWNER = "asset-2cb6093b0cad"
ROOM = "asset-758b439a6065"
BAG = "asset-ef29cf906440"
FOLDER = "asset-e104055d5990"


@pytest.fixture
def replay(tmp_path: Path) -> tuple[ShortDramaService, str, dict[str, Any]]:
    saved: dict[str, Any] = json.loads(FIXTURE.read_text())
    service = _service(tmp_path)
    pid, _spec, _board = _setup_story_truth(service)
    document = service.get_project(pid).document
    assets = [Asset.model_validate(item) for item in saved["assets"]]
    for asset in assets:
        asset.project_id = pid
        for version in asset.versions:
            if version.local_path:
                path = tmp_path / (version.id + ".png")
                Image.new("RGB", (32, 48), "white").save(path)
                version.local_path = str(path)
    document.assets = assets
    document.reference_packs = [ReferencePack.model_validate({**item, "project_id": pid}) for item in saved["reference_packs"]]
    document.shots = [ShotPlan.model_validate({**saved["before_shot"], "project_id": pid})]
    # Exact request/ownership replay. Existing suites cover formal/draft board
    # transactions; the unrelated setup board is not this actual Shot's source.
    document.storyboard_document_id = None
    document.output_slots = []
    document.reference_plan = []
    service.repository.save_project(document)
    return service, pid, saved


def arguments(saved: dict[str, Any], sequence: int, pid: str) -> dict[str, Any]:
    result = deepcopy(next(item["arguments"] for item in saved["turns"] if item["sequence"] == sequence))
    result["project_id"] = pid
    result["request"]["shot"]["project_id"] = pid
    return cast(dict[str, Any], result)


def before_state(service: ShortDramaService, pid: str) -> ProjectDocument:
    return service.get_project(pid).document


@pytest.mark.parametrize("sequence,status,required", [
    (31, ToolCallStatus.INVALID_ARGUMENTS, ["references[", "蓝色资料夹", SELECTED, "asset-version-a1233710c783"]),
    (32, ToolCallStatus.FAILED, ["reference_entries/0", "kind=REFERENCE", OWNER, "character, location, or prop", BAG, FOLDER]),
    (33, ToolCallStatus.FAILED, ["reference_entries/0", "lineage root", ROOM, "root=asset-version-c5b066d9f2a5", "owner=" + OWNER, BAG, FOLDER]),
    (34, ToolCallStatus.FAILED, ["/request/shot/references/", "/request/shot/timeline_beats/0/references/", BAG, FOLDER, SELECTED, "source_owner=" + OWNER]),
])
def test_actual_four_failed_requests_identify_relationships_and_write_nothing(
    replay: tuple[ShortDramaService, str, dict[str, Any]], sequence: int, status: ToolCallStatus, required: list[str],
) -> None:
    service, pid, saved = replay
    before = before_state(service, pid)
    events = service.repository.list_studio_events(pid)
    result = service.tool_registry.invoke("shot.save_current", arguments(saved, sequence, pid))
    assert result.status == status, result
    assert result.error
    for text in required:
        assert text in result.error.message, result.error.message
    assert result.output is None and result.receipt is None and not result.artifact_refs
    assert before_state(service, pid) == before
    assert service.repository.list_studio_events(pid) == events
    assert not service.repository.list_image_tasks(pid)
    assert not service.repository.list_tasks(pid)


def test_actual_three_subjects_share_image_and_replay_after_restart_without_new_writes(
    replay: tuple[ShortDramaService, str, dict[str, Any]], tmp_path: Path,
) -> None:
    service, pid, saved = replay
    before = before_state(service, pid)
    request = arguments(saved, 35, pid)
    result = service.tool_registry.invoke("shot.save_current", request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output and result.receipt
    after = before_state(service, pid)
    assert after.assets == before.assets
    assert after.shots[0].revision == 2
    entries = {pack.subject_asset_id: [entry for entry in pack.entries if entry.asset_version_id == SELECTED]
               for pack in after.reference_packs}
    assert all(len(entries[subject]) == 1 for subject in (ROOM, BAG, FOLDER))
    registration = cast(dict[str, Any], result.output["media_registration"])
    assert len(registration["created_reference_entry_ids"]) == 3
    assert registration["asset_id"] == OWNER
    assert not registration["plan_changed"]
    events = service.repository.list_studio_events(pid)
    restarted = _service(tmp_path)
    second = restarted.tool_registry.invoke("shot.save_current", request)
    assert second.status == ToolCallStatus.SUCCEEDED, second.error
    assert second.output
    assert cast(dict[str, Any], second.output["media_registration"])["reused"] is True
    assert not cast(dict[str, Any], second.output["media_registration"])["created_reference_entry_ids"]
    assert before_state(restarted, pid) == after
    assert restarted.repository.list_studio_events(pid) == events
    assert not restarted.repository.list_image_tasks(pid)
    assert not restarted.repository.list_tasks(pid)


def test_independent_entry_errors_and_cues_return_together_without_partial_registration(
    replay: tuple[ShortDramaService, str, dict[str, Any]],
) -> None:
    service, pid, saved = replay
    request = arguments(saved, 35, pid)
    entries = request["media_registration"]["reference_entries"]
    entries[0]["lineage_root_version_id"] = "asset-version-c5b066d9f2a5"
    entries[1]["subject_asset_id"] = OWNER
    before = before_state(service, pid)
    result = service.tool_registry.invoke("shot.save_current", request)
    assert result.status == ToolCallStatus.FAILED and result.error
    assert "reference_entries/0" in result.error.message
    assert "reference_entries/1" in result.error.message
    assert "lineage root" in result.error.message
    assert "kind=REFERENCE" in result.error.message
    assert ROOM in result.error.message and BAG in result.error.message
    assert "/request/shot/references/" in result.error.message
    assert before_state(service, pid) == before


def test_self_owned_draft_reference_composition_needs_no_pack(
    replay: tuple[ShortDramaService, str, dict[str, Any]],
) -> None:
    service, pid, saved = replay
    request = arguments(saved, 35, pid)
    request.pop("media_registration")
    request["request"]["shot"]["references"] = [ShotReferenceCue(
        subject_asset_id=OWNER, asset_version_id=SELECTED, alias="整镜构图", purpose="人物道具初态构图",
        image_reference_role="COMPOSITION").model_dump(mode="json")]
    for beat in request["request"]["shot"]["timeline_beats"]:
        beat["references"] = []
    before = before_state(service, pid)
    result = service.tool_registry.invoke("shot.save_current", request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    after = before_state(service, pid)
    assert after.reference_packs == before.reference_packs
    assert after.assets == before.assets
    assert after.shots[0].references[0].subject_asset_id == OWNER
    assert after.reference_plan[0].bindings[0].target_id == SELECTED
