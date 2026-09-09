"""Shot timing saves update authored groups while preserving existing media/editing."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.models import ProductionDocumentOrigin, ShotTimelineBeat
from short_drama.production_contracts import (
    GenerationPlanCompileRequest, ProductionSpecCreateRequest, StoryboardCreateRequest,
)
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioTimelineSave
from tests.test_storyboard_agent_interfaces import _production_spec, _storyboard


@pytest.mark.parametrize("seconds", [0.5, 4.1, 18.25])
def test_shot_timing_roundtrip_updates_canonical_groups_and_invalidates_generation(tmp_path: Path, seconds: float) -> None:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    spec = service.create_production_spec(pid, ProductionSpecCreateRequest(spec=_production_spec(service, pid), origin=ProductionDocumentOrigin.USER))
    storyboard = _storyboard(service, pid, production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id)
    shot = storyboard.shots[0]
    shot.timeline_beats = [
        ShotTimelineBeat(start_seconds=0, end_seconds=shot.duration_seconds / 2,
            visual=shot.visual, action="人物开口", camera=shot.camera, framing=shot.framing,
            dialogue_line_ids=shot.dialogue_line_ids, references=shot.references),
        ShotTimelineBeat(start_seconds=shot.duration_seconds / 2, end_seconds=shot.duration_seconds,
            visual="人物停顿", action="等待回应", camera=shot.camera, framing=shot.framing),
    ]
    created = service.create_storyboard(pid, StoryboardCreateRequest(storyboard=storyboard, origin=ProductionDocumentOrigin.USER))
    service.compile_generation_plan(pid, GenerationPlanCompileRequest(
        storyboard_document_id=created.document.id, storyboard_version_id=created.version.id,
        production_spec_document_id=spec.document.id, production_spec_version_id=spec.version.id,
        origin=ProductionDocumentOrigin.USER,
    ))
    service.studio.save_timeline(pid, StudioTimelineSave(timeline={"timelines": [{"id": "existing-edit", "clips": []}]}))
    before = service.studio.current_project(pid)
    original = before.document.shots[0]
    request = original.model_dump(mode="json")
    request["duration_seconds"] = seconds
    request["timeline_beats"] = [
        {**original.timeline_beats[0].model_dump(mode="json"), "end_seconds": seconds / 2},
        {**original.timeline_beats[1].model_dump(mode="json"), "start_seconds": seconds / 2, "end_seconds": seconds},
    ]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    endpoint = f"/api/short-drama/studio/projects/{pid}/shots/{shot.id}"
    with TestClient(app) as client:
        saved = client.put(endpoint, json={"shot": request, "expected_revision": original.revision})
        assert saved.status_code == 200, saved.text
        # A stale editor cannot overwrite the new timing.
        assert client.put(endpoint, json={"shot": request, "expected_revision": original.revision}).status_code == 409

    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    current = restarted.studio.current_project(pid)
    actual = current.document.shots[0]
    assert actual.duration_seconds == seconds and actual.revision == original.revision + 1
    assert [(beat.start_seconds, beat.end_seconds) for beat in actual.timeline_beats] == [(0, seconds / 2), (seconds / 2, seconds)]
    assert actual.timeline_beats[0].references == original.timeline_beats[0].references
    assert current.document.shots[1:] == before.document.shots[1:]
    assert current.document.editorial_timeline == before.document.editorial_timeline
    assert current.document.manifests == before.document.manifests
    assert current.document.assets == before.document.assets
    canonical = restarted.get_storyboard(pid).storyboard
    assert canonical.shots[0] == actual
    assert canonical.clip_groups[0].duration_seconds == seconds
    assert canonical.computed_duration_seconds == sum(item.duration_seconds for item in canonical.shots)
    generation = next(slot for slot in current.document.output_slots if slot.id == "video.generation-plan.current")
    assert generation.current_artifact_ref is None and generation.invalidation_reason


def test_invalid_timing_is_rejected_without_writing(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")
    before = service.initialize_fixture().document
    shot = before.shots[0]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        for seconds in (0, -1, "NaN", "Infinity"):
            request = {**shot.model_dump(mode="json"), "duration_seconds": seconds, "timeline_beats": []}
            response = client.put(f"/api/short-drama/studio/projects/{before.project.id}/shots/{shot.id}", json={"shot": request, "expected_revision": shot.revision})
            assert response.status_code == 422
    assert service.get_project(before.project.id).document.shots == before.shots
