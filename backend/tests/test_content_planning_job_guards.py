"""Direct mutations must not race an Agent-owned Content Planning Job."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from short_drama.image_models import AssetCreateRequest
from short_drama.models import (
    AssetKind,
    EnqueueRequest,
    OperationKind,
    PlanningRunStatus,
    ProjectPlanningMetadata,
    TaskStatus,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )


def _set_planning_status(
    service: ShortDramaService,
    project_id: str,
    status: PlanningRunStatus,
) -> None:
    document = service.repository.get_project(project_id)
    assert document is not None
    document.planning = ProjectPlanningMetadata(
        run_id="planning-run-active-guard",
        status=status,
        owner_agent_run_id="agent-run-active-guard",
        planning_job_kind="CONTENT_PLANNING",
    )
    service.repository.save_project(document)


def test_unfinished_content_planning_job_blocks_direct_root_mutations(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot_id = project.document.shots[0].id
    asset_id = project.document.assets[0].id

    service.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[shot_id], candidate_count=1),
    )
    service.drain(project_id, max_steps=10)
    take = service.repository.list_takes(project_id)[0]
    _set_planning_status(service, project_id, PlanningRunStatus.RUNNING)

    invalid_request = cast(Any, object())
    guarded_calls: dict[str, Callable[[], object]] = {
        "apply semantic change": lambda: service.apply_semantic_change(
            project_id,
            invalid_request,
        ),
        "revise script": lambda: service.revise_script_lines(
            project_id,
            invalid_request,
        ),
        "create asset": lambda: service.create_asset(project_id, invalid_request),
        "update asset": lambda: service.update_asset_brief(
            project_id,
            asset_id,
            invalid_request,
        ),
        "import catalog asset": lambda: service.import_catalog_asset(
            project_id,
            invalid_request,
        ),
        "import local asset": lambda: service.import_local_asset_version(
            project_id,
            asset_id,
            invalid_request,
        ),
        "import URL asset": lambda: service.import_url_asset_version(
            project_id,
            asset_id,
            invalid_request,
        ),
        "compose reference sheet": lambda: service.compose_asset_reference_sheet(
            project_id,
            asset_id,
            invalid_request,
        ),
        "import stock asset": lambda: service.import_stock_asset(
            project_id,
            invalid_request,
        ),
        "review asset": lambda: service.review_asset_version(
            project_id,
            asset_id,
            "asset-version-unused",
            invalid_request,
        ),
        "update voice reference": lambda: service.update_character_voice_reference(
            project_id,
            "character-unused",
            invalid_request,
        ),
        "upload voice reference": lambda: service.upload_character_voice_reference(
            project_id,
            "character-unused",
            filename="voice.wav",
            descriptor="voice",
            data=b"unused",
        ),
        "enqueue image": lambda: service.enqueue_image_task(
            project_id,
            asset_id,
            invalid_request,
        ),
        "add reference pack entry": lambda: service.add_reference_pack_entry(
            project_id,
            invalid_request,
        ),
        "update shot reference plan": lambda: service.update_shot_reference_plan(
            project_id,
            shot_id,
            invalid_request,
        ),
        "decide director repair": lambda: service.decide_director_repair(
            project_id,
            shot_id,
            invalid_request,
        ),
        "run director loop": lambda: service.run_director_loop(
            project_id,
            shot_id,
            invalid_request,
        ),
        "compile H3 route": lambda: service.compile_h3_production_route_plan(
            project_id,
            invalid_request,
        ),
        "execute H3 route": lambda: service.execute_h3_production_route_plan(
            project_id,
            "route-plan-unused",
            invalid_request,
        ),
        "enqueue H3 probe": lambda: service.enqueue_h3_probe(
            project_id,
            invalid_request,
        ),
        "enqueue generation": lambda: service.enqueue(project_id, invalid_request),
        "enqueue local rework": lambda: service.enqueue_local_rework(
            project_id,
            shot_id,
            OperationKind.EXTEND,
            invalid_request,
        ),
        "select take": lambda: service.select_take(take.id),
        "review H3 take": lambda: service.review_h3_take(take.id, invalid_request),
        "rebuild manifest": lambda: service.rebuild_manifest(project_id),
        "export project": lambda: service.export_project(project_id),
    }

    for name, call in guarded_calls.items():
        try:
            with pytest.raises(
                WorkflowConflictError,
                match="unfinished Content Planning Job",
            ):
                call()
        except BaseException as exc:
            exc.add_note(f"guarded call: {name}")
            raise


@pytest.mark.parametrize(
    "status",
    [PlanningRunStatus.NEEDS_ATTENTION, PlanningRunStatus.FAILED],
)
def test_any_unfinished_owned_planning_status_blocks_mutation(
    tmp_path: Path,
    status: PlanningRunStatus,
) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    _set_planning_status(service, project_id, status)

    with pytest.raises(WorkflowConflictError, match=status.value):
        service.create_asset(
            project_id,
            AssetCreateRequest(
                kind=AssetKind.REFERENCE,
                name="blocked",
                descriptor="blocked while planning is unfinished",
            ),
        )


def test_complete_planning_projection_allows_direct_mutation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id = service.initialize_fixture().document.project.id
    _set_planning_status(service, project_id, PlanningRunStatus.COMPLETE)

    created = service.create_asset(
        project_id,
        AssetCreateRequest(
            kind=AssetKind.REFERENCE,
            name="post-planning reference",
            descriptor="created after the content planning job completed",
        ),
    )

    assert created.asset.name == "post-planning reference"


def test_reads_and_task_cleanup_remain_available_while_planning_is_active(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot_id = project.document.shots[0].id
    service.enqueue(
        project_id,
        EnqueueRequest(shot_ids=[shot_id], candidate_count=1),
    )
    task = service.repository.list_tasks_for_shot(project_id, shot_id)[0]
    _set_planning_status(service, project_id, PlanningRunStatus.RUNNING)

    assert service.get_project(project_id).document.planning is not None
    cancelled = service.cancel_task(task.id)

    assert cancelled.status == TaskStatus.CANCELLED
    planning = service.get_project(project_id).document.planning
    assert planning is not None
    assert planning.status == PlanningRunStatus.RUNNING
