"""Focused worker coverage for multi-shot H3 generation units."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import pytest

from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.h3_models import (
    H3DeleteResponse,
    H3PricingSnapshot,
    H3ProviderConfigUpdate,
    H3ProviderRun,
    H3RemoteStatus,
    H3RemoteTask,
    H3TaskContent,
    H3TaskError,
    H3TaskUsage,
    H3VideoRequest,
)
from short_drama.h3_worker import PersistentH3Worker
from short_drama.models import (
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    GenerationTask,
    GenerationUnitShotWindow,
    OperationKind,
    ProductionDocument,
    ProductionDocumentOrigin,
    ProductionDocumentVersion,
    ProjectDocument,
    QAReport,
    QaStatus,
    RouteEvidence,
    Take,
    TakeAvailability,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.providers.h3_config import H3ProviderConfigStore
from short_drama.providers.minimax_h3_client import H3Client
from short_drama.repository import ShortDramaRepository


CREATED_AT = "2026-09-04T00:00:00.000+00:00"


class _FakeH3Client:
    def __init__(self) -> None:
        self.remote_tasks: dict[str, H3RemoteTask] = {}
        self.output_source: Path | None = None

    def submit(self, request: H3VideoRequest) -> str:
        del request
        raise AssertionError("These tests begin after provider acknowledgement")

    def query(self, task_id: str) -> H3RemoteTask:
        return self.remote_tasks[task_id].model_copy(deep=True)

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]:
        return [
            task.model_copy(deep=True)
            for task in list(self.remote_tasks.values())[:page_size]
        ]

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse:
        return H3DeleteResponse(
            task_id=task_id,
            action="cancelled",
            status="cancelled",
        )

    def upload_file(self, path: Path) -> str:
        del path
        raise AssertionError("These tests do not upload local references")

    def download(self, url: str, destination: Path) -> None:
        del url
        if self.output_source is None:
            raise AssertionError("A fake H3 output must be configured before download")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.output_source, destination)


@dataclass(frozen=True)
class _WorkerHarness:
    repository: ShortDramaRepository
    worker: PersistentH3Worker
    client: _FakeH3Client
    document: ProjectDocument
    task: GenerationTask
    run: H3ProviderRun
    windows: list[GenerationUnitShotWindow]


def _production_document(
    *,
    project_id: str,
    document_id: str,
    version_id: str,
    kind: str,
) -> ProductionDocument:
    return ProductionDocument(
        id=document_id,
        project_id=project_id,
        kind=kind,
        title=kind,
        current_version_id=version_id,
        versions=[
            ProductionDocumentVersion(
                id=version_id,
                version=1,
                payload={},
                origin=ProductionDocumentOrigin.AGENT,
                created_at=CREATED_AT,
            )
        ],
    )


def _task_bundle(
    document: ProjectDocument,
    *,
    task_id: str,
    provider_task_id: str,
    retry_of_task_id: str | None = None,
) -> tuple[GenerationTask, H3ProviderRun, RouteEvidence, list[GenerationUnitShotWindow]]:
    first_shot, second_shot = document.shots[:2]
    windows = [
        GenerationUnitShotWindow(
            shot_id=first_shot.id,
            shot_revision=first_shot.revision,
            source_in_seconds=0.0,
            source_out_seconds=5.0,
            duration_seconds=5.0,
        ),
        GenerationUnitShotWindow(
            shot_id=second_shot.id,
            shot_revision=second_shot.revision,
            source_in_seconds=5.0,
            source_out_seconds=13.0,
            duration_seconds=8.0,
        ),
    ]
    task = GenerationTask(
        id=task_id,
        project_id=document.project.id,
        shot_id=first_shot.id,
        operation=OperationKind.GENERATE,
        candidate_index=0,
        status=TaskStatus.ACKNOWLEDGED,
        provider="minimax",
        model="MiniMax-H3",
        route=VideoGenerationRoute.DIRECT,
        resolution="768P",
        provider_run_id=f"run-{task_id}",
        provider_task_id=provider_task_id,
        request_fingerprint=f"fingerprint-{task_id}",
        input_script_version_id=document.project.current_script_version_id,
        input_bible_version_id=document.project.current_bible_version_id,
        input_shot_revision=first_shot.revision,
        input_shot_revisions={
            first_shot.id: first_shot.revision,
            second_shot.id: second_shot.revision,
        },
        storyboard_document_id=document.storyboard_document_id,
        storyboard_version_id="storyboard-version-1",
        generation_plan_document_id=document.generation_plan_document_id,
        generation_plan_version_id="generation-plan-version-1",
        generation_unit_id="generation-unit-1",
        clip_group_id="clip-group-1",
        output_slot_id="video.generation-unit.generation-unit-1",
        shot_windows=windows,
        input_voice_reference_revision=document.project.voice_reference_revision,
        requested_duration_seconds=13.0,
        attempt=1 if retry_of_task_id is None else 2,
        retry_of_task_id=retry_of_task_id,
        submitted_at=CREATED_AT,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
    run = H3ProviderRun(
        id=f"run-{task_id}",
        task_id=task_id,
        project_id=document.project.id,
        shot_id=first_shot.id,
        generation_plan_document_id=task.generation_plan_document_id,
        generation_plan_version_id=task.generation_plan_version_id,
        storyboard_document_id=task.storyboard_document_id,
        storyboard_version_id=task.storyboard_version_id,
        generation_unit_id=task.generation_unit_id,
        clip_group_id=task.clip_group_id,
        output_slot_id=task.output_slot_id,
        shot_ids=task.covered_shot_ids(),
        shot_windows=windows,
        route=task.route,
        purpose=EvidencePurpose.PRODUCTION,
        prompt="[Shot 1] establish the space\n[Shot 2] deliver the dialogue",
        pricing=H3PricingSnapshot(captured_at=CREATED_AT),
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
    evidence = RouteEvidence(
        id=f"evidence-{task_id}",
        project_id=document.project.id,
        shot_id=first_shot.id,
        task_id=task_id,
        provider=task.provider,
        model=task.model,
        route=task.route,
        resolution=task.resolution,
        purpose=EvidencePurpose.PRODUCTION,
        status=EvidenceStatus.AWAITING_OUTPUT,
        created_at=CREATED_AT,
    )
    return task, run, evidence, windows


def _persist_bundle(
    repository: ShortDramaRepository,
    task: GenerationTask,
    run: H3ProviderRun,
    evidence: RouteEvidence,
) -> None:
    repository.save_task(task)
    repository.save_provider_run(run)
    repository.save_route_evidence(evidence)


def _harness(tmp_path: Path) -> _WorkerHarness:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture(CREATED_AT)
    storyboard = _production_document(
        project_id=document.project.id,
        document_id="storyboard-1",
        version_id="storyboard-version-1",
        kind="storyboard.plan",
    )
    generation_plan = _production_document(
        project_id=document.project.id,
        document_id="generation-plan-1",
        version_id="generation-plan-version-1",
        kind="generation.plan",
    )
    document.production_documents = [storyboard, generation_plan]
    document.storyboard_document_id = storyboard.id
    document.generation_plan_document_id = generation_plan.id
    repository.save_project(document)

    task, run, evidence, windows = _task_bundle(
        document,
        task_id="group-task-1",
        provider_task_id="remote-group-1",
    )
    _persist_bundle(repository, task, run, evidence)

    client = _FakeH3Client()
    config_store = H3ProviderConfigStore(tmp_path / "h3-provider.json")
    config_store.update(H3ProviderConfigUpdate(api_key="test-key"))

    def client_factory(api_key: str) -> H3Client:
        del api_key
        return client

    worker = PersistentH3Worker(
        repository=repository,
        workspace_root=tmp_path / "workspace",
        config_store=config_store,
        client_factory=client_factory,
    )
    return _WorkerHarness(
        repository=repository,
        worker=worker,
        client=client,
        document=document,
        task=task,
        run=run,
        windows=windows,
    )


def _remote_task(
    task_id: str,
    status: H3RemoteStatus,
) -> H3RemoteTask:
    return H3RemoteTask(
        id=task_id,
        status=status,
        error=(
            H3TaskError(code="REMOTE_FAILED", message="provider failed")
            if status == H3RemoteStatus.FAILED
            else None
        ),
        content=(
            H3TaskContent(url=f"fake://{task_id}.mp4")
            if status == H3RemoteStatus.SUCCEEDED
            else None
        ),
        resolution="768P",
        duration=13,
        usage=(
            H3TaskUsage(total_seconds=13, output_seconds=13)
            if status == H3RemoteStatus.SUCCEEDED
            else None
        ),
        ratio="9:16",
        task_type="generation",
        modality="video",
    )


def _render_output(path: Path, duration_seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x273447:s=768x1366:r=24:d={duration_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration_seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _advance_to_success(
    harness: _WorkerHarness,
    task: GenerationTask,
    *,
    duration_seconds: float,
) -> GenerationTask:
    source = harness.repository.database_path.parent / f"{task.id}.mp4"
    _render_output(source, duration_seconds)
    harness.client.output_source = source
    assert task.provider_task_id is not None
    harness.client.remote_tasks[task.provider_task_id] = _remote_task(
        task.provider_task_id,
        H3RemoteStatus.SUCCEEDED,
    )
    return harness.worker.advance(task.id)


def test_generation_unit_staleness_checks_every_shot_revision(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    assert harness.worker.input_staleness_reason(harness.task, harness.run) is None

    changed = harness.repository.get_project(harness.document.project.id)
    assert changed is not None
    second_shot = changed.shots[1]
    changed.shots[1] = second_shot.model_copy(
        update={"revision": second_shot.revision + 1}
    )
    harness.repository.save_project(changed)

    reason = harness.worker.input_staleness_reason(harness.task, harness.run)
    assert reason is not None
    assert second_shot.id in reason


@pytest.mark.parametrize(
    ("document_pointer", "expected_fragment"),
    [
        ("storyboard_document_id", "storyboard changed"),
        ("generation_plan_document_id", "generation plan changed"),
    ],
)
def test_generation_unit_staleness_checks_pinned_document_versions(
    tmp_path: Path,
    document_pointer: str,
    expected_fragment: str,
) -> None:
    harness = _harness(tmp_path)
    changed = harness.repository.get_project(harness.document.project.id)
    assert changed is not None
    document_id = getattr(changed, document_pointer)
    assert document_id is not None
    production_document = next(
        item for item in changed.production_documents if item.id == document_id
    )
    version_2 = ProductionDocumentVersion(
        id=f"{document_id}-version-2",
        version=2,
        parent_version_id=production_document.current_version_id,
        payload={"revision": 2},
        origin=ProductionDocumentOrigin.AGENT,
        created_at=CREATED_AT,
    )
    production_document.versions.append(version_2)
    production_document.current_version_id = version_2.id
    harness.repository.save_project(changed)

    reason = harness.worker.input_staleness_reason(harness.task, harness.run)
    assert reason is not None
    assert expected_fragment in reason


def test_generation_unit_success_preserves_metadata_and_unit_output_directory(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    completed = _advance_to_success(harness, harness.task, duration_seconds=13.0)

    assert completed.status == TaskStatus.SUCCEEDED
    take = harness.repository.get_take(f"take-{harness.task.id}")
    assert take is not None
    assert take.storyboard_document_id == harness.task.storyboard_document_id
    assert take.storyboard_version_id == harness.task.storyboard_version_id
    assert take.generation_plan_document_id == harness.task.generation_plan_document_id
    assert take.generation_plan_version_id == harness.task.generation_plan_version_id
    assert take.generation_unit_id == harness.task.generation_unit_id
    assert take.clip_group_id == harness.task.clip_group_id
    assert take.output_slot_id == harness.task.output_slot_id
    assert take.shot_windows == harness.windows
    assert Path(take.media_path).parent.name == harness.task.generation_unit_id
    assert take.is_selected is False
    assert take.selected_shot_ids == []

    evidence = harness.repository.get_route_evidence_for_task(harness.task.id)
    assert evidence is not None
    assert evidence.technical_passed is True


def test_generation_unit_marks_media_shorter_than_last_window_as_truncated(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    completed = _advance_to_success(harness, harness.task, duration_seconds=12.5)

    assert completed.status == TaskStatus.SUCCEEDED
    take = harness.repository.get_take(f"take-{harness.task.id}")
    assert take is not None
    assert abs(take.duration_seconds - 12.5) <= 0.1
    assert {issue.code for issue in take.qa_report.issues} == {
        "H3_SHOT_WINDOWS_TRUNCATED"
    }
    evidence = harness.repository.get_route_evidence_for_task(harness.task.id)
    assert evidence is not None
    assert evidence.technical_passed is False


def _save_selected_previous_take(harness: _WorkerHarness) -> Take:
    previous_task = harness.task.model_copy(
        update={
            "id": "previous-group-task",
            "provider_run_id": None,
            "provider_task_id": "remote-previous",
            "request_fingerprint": "fingerprint-previous",
            "status": TaskStatus.SUCCEEDED,
            "finished_at": CREATED_AT,
        }
    )
    harness.repository.save_task(previous_task)
    report = QAReport(
        id="qa-previous-group-take",
        take_id="previous-group-take",
        status=QaStatus.PASSED,
        hard_gate_passed=True,
        score=5.0,
        issues=[],
        checks=["previous take accepted"],
        reviewed_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    take = Take(
        id="previous-group-take",
        project_id=previous_task.project_id,
        shot_id=previous_task.shot_id,
        task_id=previous_task.id,
        candidate_index=previous_task.candidate_index,
        operation=previous_task.operation,
        provider=previous_task.provider,
        model=previous_task.model,
        route=previous_task.route,
        storyboard_document_id=previous_task.storyboard_document_id,
        storyboard_version_id=previous_task.storyboard_version_id,
        generation_plan_document_id=previous_task.generation_plan_document_id,
        generation_plan_version_id=previous_task.generation_plan_version_id,
        generation_unit_id=previous_task.generation_unit_id,
        clip_group_id=previous_task.clip_group_id,
        output_slot_id=previous_task.output_slot_id,
        shot_windows=harness.windows,
        media_path=str((harness.repository.database_path.parent / "previous.mp4").resolve()),
        thumbnail_path=str(
            (harness.repository.database_path.parent / "previous.jpg").resolve()
        ),
        duration_seconds=13.0,
        width=768,
        height=1366,
        availability=TakeAvailability.AVAILABLE,
        qa_status=QaStatus.PASSED,
        is_selected=True,
        freshness=Freshness.CURRENT,
        qa_report=report,
        created_at=CREATED_AT,
    )
    harness.repository.save_take(take)
    return take


def test_failed_generation_and_successful_retry_do_not_replace_selected_take(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    selected = _save_selected_previous_take(harness)
    assert harness.task.provider_task_id is not None
    harness.client.remote_tasks[harness.task.provider_task_id] = _remote_task(
        harness.task.provider_task_id,
        H3RemoteStatus.FAILED,
    )

    failed = harness.worker.advance(harness.task.id)
    assert failed.status == TaskStatus.FAILED
    preserved = harness.repository.get_take(selected.id)
    assert preserved is not None
    assert preserved.selected_shot_ids == harness.task.covered_shot_ids()

    retry_task, retry_run, retry_evidence, _windows = _task_bundle(
        harness.document,
        task_id="group-task-retry",
        provider_task_id="remote-group-retry",
        retry_of_task_id=harness.task.id,
    )
    _persist_bundle(harness.repository, retry_task, retry_run, retry_evidence)
    completed = _advance_to_success(harness, retry_task, duration_seconds=13.0)
    assert completed.status == TaskStatus.SUCCEEDED

    retry_take = harness.repository.get_take(f"take-{retry_task.id}")
    assert retry_take is not None
    assert retry_take.is_selected is False
    assert retry_take.selected_shot_ids == []
    preserved = harness.repository.get_take(selected.id)
    assert preserved is not None
    assert preserved.is_selected is True
    assert preserved.selected_shot_ids == harness.task.covered_shot_ids()
