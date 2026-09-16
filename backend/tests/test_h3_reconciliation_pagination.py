"""Bounded H3 history reads preserve unknown submissions and explicit ID recovery."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from _routes.short_drama import router
from short_drama.deps import get_short_drama_service
from short_drama.h3_models import H3ProbeRequest, H3ReconciliationCandidatesResponse, H3RemoteTask
from short_drama.models import GenerationTask, TaskStatus
from short_drama.providers.minimax_h3_client import H3TransientError
from short_drama.service import ProviderOperationError, ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_short_drama_h3 import FakeH3Client, _service


class PagedH3Client(FakeH3Client):
    def __init__(self) -> None:
        super().__init__()
        self.list_calls: list[tuple[int, int]] = []
        self.query_calls: list[str] = []
        self.fail_offset: int | None = None
        self.repeat_pages = False
        self.returned_page_size: int | None = None
        self.page_overrides: dict[int, list[H3RemoteTask]] = {}

    def list_tasks(self, *, page_size: int = 20, offset: int = 0) -> list[H3RemoteTask]:
        self.list_calls.append((offset, page_size))
        if offset == self.fail_offset:
            raise H3TransientError("simulated task-list timeout")
        if offset in self.page_overrides:
            return [item.model_copy(deep=True) for item in self.page_overrides[offset]]
        return super().list_tasks(page_size=min(page_size, self.returned_page_size or page_size),
                                  offset=0 if self.repeat_pages else offset)

    def query(self, task_id: str) -> H3RemoteTask:
        self.query_calls.append(task_id)
        return super().query(task_id)


def _unknown(tmp_path: Path, fake: PagedH3Client) -> tuple[ShortDramaService, GenerationTask]:
    fake.submit_unknown_once = True
    service = _service(tmp_path, fake)
    document = service.initialize_fixture().document
    queued = service.enqueue_h3_probe(document.project.id,
        H3ProbeRequest(shot_id=document.shots[0].id, duration_seconds=6))
    assert service.advance_task(queued.task.id).status == TaskStatus.SUBMITTING
    task = service.advance_task(queued.task.id)
    assert task.status == TaskStatus.SUBMIT_UNKNOWN
    return service, task


def _put_target_after(fake: PagedH3Client, count: int) -> None:
    target = fake.remote_tasks.pop("remote-1")
    for index in range(count):
        identity = f"unrelated-{index}"
        fake.remote_tasks[identity] = target.model_copy(update={"id": identity, "duration": 10})
    fake.remote_tasks[target.id] = target


def _unchanged(service: ShortDramaService, original: GenerationTask, fake: PagedH3Client) -> None:
    saved = service.repository.get_task(original.id)
    assert saved is not None and saved.model_dump() == original.model_dump()
    assert saved.status == TaskStatus.SUBMIT_UNKNOWN
    assert len(fake.submitted_requests) == 1
    assert fake.cancel_calls == []


@pytest.mark.parametrize("preceding", [50, 150])
def test_finds_compatible_unknown_beyond_first_fifty_without_binding(tmp_path: Path, preceding: int) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    _put_target_after(fake, preceding)
    result = service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id)
    assert [item.id for item in result.tasks] == ["remote-1"]
    assert result.query is not None
    assert result.query.tasks_scanned == preceding + 1
    assert result.query.unique_tasks_scanned == preceding + 1
    assert result.query.stop_reason == "empty_page" and result.query.next_offset is None
    assert [offset for offset, _ in fake.list_calls] == [*range(0, preceding + 1, 50), preceding + 1]
    _unchanged(service, task, fake)
    assert fake.query_calls == []

    calls_before = len(fake.list_calls)
    resolved = service.resolve_h3_submission(task.id, "remote-1")
    assert resolved.task.status == TaskStatus.ACKNOWLEDGED
    assert fake.query_calls == ["remote-1"] and len(fake.list_calls) == calls_before
    assert len(fake.submitted_requests) == 1


def test_default_window_stops_at_ten_pages_and_tool_can_continue(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    _put_target_after(fake, 550)
    first = service.tool_registry.invoke("route.list_h3_reconciliation",
        {"project_id": task.project_id, "task_id": task.id})
    assert first.status == ToolCallStatus.SUCCEEDED and first.output is not None
    page = H3ReconciliationCandidatesResponse.model_validate(first.output)
    assert page.tasks == [] and page.query is not None
    assert page.query.pages_scanned == 10 and page.query.tasks_scanned == 500
    assert page.query.stop_reason == "page_limit" and page.query.next_offset == 500
    assert len(fake.list_calls) == 10
    _unchanged(service, task, fake)

    second = service.tool_registry.invoke("route.list_h3_reconciliation",
        {"project_id": task.project_id, "task_id": task.id, "offset": page.query.next_offset})
    assert second.status == ToolCallStatus.SUCCEEDED and second.output is not None
    remaining = H3ReconciliationCandidatesResponse.model_validate(second.output)
    assert [item.id for item in remaining.tasks] == ["remote-1"]
    assert remaining.query is not None and remaining.query.start_offset == 500
    assert remaining.query.stop_reason == "empty_page"
    assert fake.list_calls[10:] == [(500, 50), (550, 50), (551, 50)]
    _unchanged(service, task, fake)


def test_short_pages_continue_by_actual_length_until_empty_page(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    _put_target_after(fake, 2)
    fake.returned_page_size = 1
    result = service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id)
    assert [item.id for item in result.tasks] == ["remote-1"]
    assert fake.list_calls == [(0, 50), (1, 50), (2, 50), (3, 50)]
    assert result.query is not None and result.query.stop_reason == "empty_page"
    _unchanged(service, task, fake)


def test_empty_history_does_not_resolve_or_resubmit_unknown(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    fake.remote_tasks.clear()
    result = service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id)
    assert result.tasks == [] and result.query is not None
    assert result.query.pages_scanned == 1 and result.query.tasks_scanned == 0
    assert result.query.stop_reason == "empty_page"
    assert "未找到候选不代表未提交" in result.query.note
    _unchanged(service, task, fake)


def test_repeated_page_stops_without_duplicate_candidates(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    fake.repeat_pages = True
    result = service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id)
    assert [item.id for item in result.tasks] == ["remote-1"]
    assert fake.list_calls == [(0, 50), (1, 50)]
    assert result.query is not None and result.query.stop_reason == "repeated_page"
    assert result.query.tasks_scanned == 2 and result.query.unique_tasks_scanned == 1
    assert result.query.next_offset is None
    _unchanged(service, task, fake)


def test_overlapping_pages_deduplicate_ids_and_keep_reading_new_tasks(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    first = fake.remote_tasks["remote-1"]
    second = first.model_copy(update={"id": "remote-2"})
    third = first.model_copy(update={"id": "remote-3"})
    fake.page_overrides = {0: [first, second], 2: [second, third], 4: []}
    result = service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id, page_size=2)
    assert [item.id for item in result.tasks] == ["remote-1", "remote-2", "remote-3"]
    assert result.query is not None and result.query.tasks_scanned == 4
    assert result.query.unique_tasks_scanned == 3 and result.query.stop_reason == "empty_page"
    _unchanged(service, task, fake)


@pytest.mark.parametrize("failure_offset", [0, 50])
def test_listing_error_reports_query_range_and_preserves_unknown(tmp_path: Path, failure_offset: int) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    _put_target_after(fake, 60)
    fake.fail_offset = failure_offset
    with pytest.raises(ProviderOperationError, match=f"offset {failure_offset} after {failure_offset // 50} completed pages"):
        service.list_h3_reconciliation_candidates_for_task(task.project_id, task.id)
    assert [offset for offset, _ in fake.list_calls] == list(range(0, failure_offset + 1, 50))
    _unchanged(service, task, fake)


def test_http_page_controls_bound_requests_and_return_continue_offset(tmp_path: Path) -> None:
    fake = PagedH3Client()
    service, task = _unknown(tmp_path, fake)
    _put_target_after(fake, 50)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        result = client.get("/api/short-drama/providers/minimax-h3/reconciliation-candidates",
            params={"offset": 50, "page_size": 1, "max_pages": 1})
        assert result.status_code == 200
        page = H3ReconciliationCandidatesResponse.model_validate(result.json())
        assert [item.id for item in page.tasks] == ["remote-1"]
        assert page.query is not None and page.query.next_offset == 51
        for params in ({"offset": -1}, {"page_size": 101}, {"max_pages": 11}):
            assert client.get("/api/short-drama/providers/minimax-h3/reconciliation-candidates", params=params).status_code == 422
    assert fake.list_calls == [(50, 1)]
    _unchanged(service, task, fake)
