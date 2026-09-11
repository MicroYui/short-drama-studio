"""Planning and direct content work can proceed together without a global lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from short_drama.image_models import AssetCreateRequest
from short_drama.models import (
    AssetKind,
    EnqueueRequest,
    PlanningRunStatus,
    ProjectPlanningMetadata,
    TaskStatus,
)
from short_drama.service import ShortDramaService


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


@pytest.mark.parametrize("status", [PlanningRunStatus.RUNNING, PlanningRunStatus.NEEDS_ATTENTION, PlanningRunStatus.FAILED])
def test_unfinished_planning_does_not_freeze_independent_content(tmp_path: Path, status: PlanningRunStatus) -> None:
    service = _service(tmp_path)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    _set_planning_status(service, project_id, status)
    created = service.create_asset(project_id, AssetCreateRequest(
        kind=AssetKind.REFERENCE, name="并行准备的参考", descriptor="旧规划任务不阻止当前制作"))
    assert created.asset.name == "并行准备的参考"
    service.enqueue(project_id, EnqueueRequest(shot_ids=[project.document.shots[0].id], candidate_count=1))
    assert service.repository.list_tasks_for_shot(project_id, project.document.shots[0].id)
    planning = service.get_project(project_id).document.planning
    assert planning is not None and planning.status == status


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
