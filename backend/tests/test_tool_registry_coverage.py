"""Coverage checks for the agent-callable production tool catalog."""

from __future__ import annotations

from pathlib import Path

from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus, ToolCategory


REQUIRED_PRODUCTION_TOOLS = {
    "audio": {"audio.inspect_manifest", "audio.update_voice_reference"},
    "artifact": {
        "artifact.add_reference_entry",
        "artifact.create_asset",
        "artifact.import_catalog",
        "artifact.import_local_version",
        "artifact.import_stock",
        "artifact.import_url_version",
        "artifact.inspect_coverage",
        "artifact.review_version",
        "artifact.search_catalog",
        "artifact.search_stock",
    },
    "image": {
        "image.advance",
        "image.cancel",
        "image.enqueue",
        "image.list",
        "image.recover_output",
        "image.retry",
    },
    "planning": {"planning.advance"},
    "shot": {
        "shot.apply_director_repair",
        "shot.compile_keyframe_brief",
        "shot.director_advice",
        "shot.preview_reference_plan",
        "shot.run_director_loop",
        "shot.update_reference_plan",
    },
    "video": {
        "video.compare_takes",
        "video.enqueue",
        "video.extend",
        "video.gap_fill",
        "video.inspect_take",
        "video.retake",
        "video.select_take",
    },
    "timeline": {"timeline.export", "timeline.rebuild"},
    "task": {
        "task.advance",
        "task.cancel",
        "task.drain",
        "task.reconcile",
        "task.recover_output",
        "task.retry",
    },
    "qa": {
        "qa.check_input_compatibility",
        "qa.compare_keyframes",
        "qa.list_visual_input_reports",
        "qa.rerank_reference_visuals",
        "qa.review_episode",
    },
    "route": {
        "route.compile_h3_prompt",
        "route.enqueue_h3_probe",
        "route.execute_h3_plan",
        "route.h3_capability_profile",
        "route.list_h3_plans",
        "route.list_h3_reconciliation",
        "route.list_h3_runs",
        "route.plan_h3",
        "route.preview_h3_probe",
        "route.resolve_h3_not_submitted",
        "route.resolve_h3_submission",
        "route.review_h3_take",
    },
}


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def test_default_registry_covers_the_existing_production_loop(tmp_path: Path) -> None:
    service = _service(tmp_path)
    descriptors = {item.id: item for item in service.tool_registry.list()}

    for category, expected_ids in REQUIRED_PRODUCTION_TOOLS.items():
        assert expected_ids <= descriptors.keys()
        assert {
            descriptor.id
            for descriptor in service.tool_registry.list(
                category=ToolCategory(category)
            )
        } >= expected_ids

    assert "change.preview" in descriptors
    assert "change.apply" in descriptors
    assert "agent.delegate" not in descriptors

    for descriptor in descriptors.values():
        assert descriptor.input_schema["type"] == "object"
        assert descriptor.output_schema["type"] == "object"


def test_read_adapters_use_the_real_service_without_bypassing_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id

    coverage = service.tool_registry.invoke(
        "artifact.inspect_coverage",
        {"project_id": project_id},
    )
    assert coverage.status == ToolCallStatus.SUCCEEDED
    assert coverage.output is not None
    assert coverage.output["project_id"] == project_id

    image_tasks = service.tool_registry.invoke(
        "image.list",
        {"project_id": project_id},
    )
    assert image_tasks.status == ToolCallStatus.SUCCEEDED
    assert image_tasks.output == {"tasks": []}

    route_plans = service.tool_registry.invoke(
        "route.list_h3_plans",
        {"project_id": project_id},
    )
    assert route_plans.status == ToolCallStatus.SUCCEEDED
    assert route_plans.output == {"plans": []}

    visual_reports = service.tool_registry.invoke(
        "qa.list_visual_input_reports",
        {"project_id": project_id},
    )
    assert visual_reports.status == ToolCallStatus.SUCCEEDED
    assert visual_reports.output == {
        "reference_reranks": [],
        "keyframe_comparisons": [],
        "compatibility_checks": [],
    }
