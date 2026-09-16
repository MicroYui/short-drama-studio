"""Cancellation competes with real service transitions using a blocked Fake client."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from PIL import Image
import pytest

from short_drama.h3_models import (
    H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProbeRequest,
    H3ProviderRun, H3RemoteTask, H3VideoRequest,
)
from short_drama.models import EvidenceStatus, GenerationTask, TaskStatus, VideoGenerationRoute
from short_drama.service import WorkflowConflictError
from tests.test_short_drama_h3 import FakeH3Client, _service


class BlockingH3Client(FakeH3Client):
    def __init__(self, operation: str) -> None:
        super().__init__()
        self.operation = operation
        self.entered = threading.Event()
        self.release = threading.Event()

    def wait_if_blocked(self, operation: str) -> None:
        if operation == self.operation:
            self.entered.set()
            assert self.release.wait(5), f"Timed out waiting to release {operation}"

    def upload_file(self, path: Path) -> str:
        self.wait_if_blocked("upload")
        return super().upload_file(path)

    def submit(self, request: H3VideoRequest) -> str:
        self.wait_if_blocked("submit")
        return super().submit(request)

    def query(self, task_id: str) -> H3RemoteTask:
        response = super().query(task_id)
        self.wait_if_blocked("query")
        return response


def test_cancel_during_slow_preparation_returns_before_upload_and_survives_restart(tmp_path: Path) -> None:
    fake = BlockingH3Client("upload")
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    image_path = tmp_path / "first-frame.png"
    Image.new("RGB", (768, 1366), "blue").save(image_path)
    probe = service.enqueue_h3_probe(project.document.project.id, H3ProbeRequest(
        shot_id=project.document.shots[0].id,
        route=VideoGenerationRoute.FRAME_FIRST,
        media_inputs=[H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME,
            source=H3MediaSource(local_path=str(image_path)))],
    ))
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepare = pool.submit(service.advance_task, probe.task.id)
        try:
            assert fake.entered.wait(5)
            cancelled = pool.submit(service.cancel_task, probe.task.id).result(timeout=2)
            assert cancelled.status == TaskStatus.CANCELLED
            # No upload has returned; both cancellation and unrelated project
            # writes must be possible while preparation is still pending.
            document = service.repository.get_project(project.document.project.id)
            assert document is not None
            document.project.name = "User edit during preparation"
            service.repository.save_project(document)
        finally:
            fake.release.set()
        assert prepare.result(timeout=5).status == TaskStatus.CANCELLED

    restarted = _service(tmp_path, fake)
    assert restarted.get_generation_task(probe.task.id).status == TaskStatus.CANCELLED
    with pytest.raises(WorkflowConflictError, match="cannot advance"):
        restarted.advance_task(probe.task.id)
    assert fake.submitted_requests == []
    assert fake.cancel_calls == []
    assert restarted.get_h3_provider_run(probe.task.id).request is None
    evidence = restarted.repository.get_route_evidence_for_task(probe.task.id)
    assert evidence is not None and evidence.status == EvidenceStatus.CANCELLED


@pytest.mark.parametrize("prepared", [False, True])
def test_cancel_is_not_overwritten_by_a_late_staleness_failure(tmp_path: Path, prepared: bool) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id))
    if prepared:
        assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    entered, release = threading.Event(), threading.Event()

    def stale_after_wait(_task: GenerationTask, _run: H3ProviderRun) -> str:
        entered.set()
        assert release.wait(5)
        return "Current input changed while the operation was preparing"

    service.h3_worker.source_staleness_checker = stale_after_wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        advance = pool.submit(service.advance_task, probe.task.id)
        try:
            assert entered.wait(5)
            assert pool.submit(service.cancel_task, probe.task.id).result(timeout=2).status == TaskStatus.CANCELLED
        finally:
            release.set()
        assert advance.result(timeout=5).status == TaskStatus.CANCELLED
    assert service.get_generation_task(probe.task.id).error_code == "CANCELLED_BY_USER"
    assert fake.submitted_requests == []


def test_cancel_after_preparation_wins_before_submission_start(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id))
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    entered, release = threading.Event(), threading.Event()

    def delayed_factory(_api_key: str) -> FakeH3Client:
        entered.set()
        assert release.wait(5)
        return fake

    service.h3_worker.client_factory = delayed_factory
    with ThreadPoolExecutor(max_workers=2) as pool:
        submit = pool.submit(service.advance_task, probe.task.id)
        try:
            assert entered.wait(5)
            assert pool.submit(service.cancel_task, probe.task.id).result(timeout=2).status == TaskStatus.CANCELLED
        finally:
            release.set()
        assert submit.result(timeout=5).status == TaskStatus.CANCELLED
    assert fake.submitted_requests == []
    with pytest.raises(WorkflowConflictError, match="cannot advance"):
        service.advance_task(probe.task.id)


@pytest.mark.parametrize("unknown", [False, True])
def test_started_submission_is_not_reported_as_locally_cancelled(tmp_path: Path, unknown: bool) -> None:
    fake = BlockingH3Client("submit")
    fake.submit_unknown_once = unknown
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id))
    service.advance_task(probe.task.id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        submit = pool.submit(service.advance_task, probe.task.id)
        try:
            assert fake.entered.wait(5)
            assert service.get_h3_provider_run(probe.task.id).submission_attempt_state == "STARTED"
            with pytest.raises(WorkflowConflictError, match="must be reconciled"):
                pool.submit(service.cancel_task, probe.task.id).result(timeout=2)
        finally:
            fake.release.set()
        assert submit.result(timeout=5).status == (TaskStatus.SUBMIT_UNKNOWN if unknown else TaskStatus.ACKNOWLEDGED)
    assert len(fake.submitted_requests) == 1
    if unknown:
        with pytest.raises(WorkflowConflictError, match="must be reconciled"):
            service.cancel_task(probe.task.id)
    else:
        assert service.cancel_task(probe.task.id).status == TaskStatus.CANCELLED
        assert fake.cancel_calls == ["remote-1"]


def test_remote_cancellation_waits_for_poll_then_keeps_cancelled_state(tmp_path: Path) -> None:
    fake = BlockingH3Client("query")
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id))
    service.advance_task(probe.task.id)
    service.advance_task(probe.task.id)
    cancelling = threading.Event()

    def cancel() -> GenerationTask:
        cancelling.set()
        return service.cancel_task(probe.task.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        poll = pool.submit(service.advance_task, probe.task.id)
        try:
            assert fake.entered.wait(5)
            cancellation = pool.submit(cancel)
            assert cancelling.wait(5)
            assert not cancellation.done()
        finally:
            fake.release.set()
        assert poll.result(timeout=5).status == TaskStatus.ACKNOWLEDGED
        assert cancellation.result(timeout=5).status == TaskStatus.CANCELLED
    assert service.get_generation_task(probe.task.id).status == TaskStatus.CANCELLED
    assert fake.cancel_calls == ["remote-1"]
