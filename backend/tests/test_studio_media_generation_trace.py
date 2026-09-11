"""Media progress belongs to the active product job, with retrievable requests."""

import json
from pathlib import Path
from zipfile import ZipFile

from pydantic import JsonValue

from short_drama.h3_models import H3ProbeRequest, H3RemoteStatus
from short_drama.models import TaskStatus
from short_drama.studio_trace import production_trace, trace_context
from short_drama.studio_trace_export import export_studio_trace
from tests.test_short_drama_h3 import FakeH3Client, _service


def test_h3_progress_correlates_job_without_repeating_unchanged_polls(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    pid = project.document.project.id
    probe = service.enqueue_h3_probe(pid, H3ProbeRequest(shot_id=project.document.shots[0].id))
    events: list[tuple[str, dict[str, JsonValue]]] = []
    with production_trace(tmp_path / 'trace', lambda kind, value: events.append((kind, value))), trace_context(job_id='job', work_order_id='worker'):
        assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
        assert service.advance_task(probe.task.id).provider_task_id == 'remote-1'
        assert service.advance_task(probe.task.id).status == TaskStatus.ACKNOWLEDGED
        assert service.advance_task(probe.task.id).status == TaskStatus.ACKNOWLEDGED
        fake.set_status('remote-1', H3RemoteStatus.RUNNING)
        assert service.advance_task(probe.task.id).status == TaskStatus.RUNNING
        assert service.advance_task(probe.task.id).status == TaskStatus.RUNNING
    media = [value for kind, value in events if kind == 'provider.video_generation']
    assert [(item['operation'], item['status']) for item in media] == [
        ('update', 'SUBMITTING'), ('submit', 'SUBMITTING'), ('update', 'ACKNOWLEDGED'), ('update', 'RUNNING')]
    assert all(item['job_id'] == 'job' and item['work_order_id'] == 'worker' for item in media)
    assert all(item['task_id'] == probe.task.id and item['model'] == 'MiniMax-H3' for item in media)
    assert media[-1]['provider_task_id'] == 'remote-1'
    assert media[-1]['resolution'] == probe.task.resolution
    assert media[-1]['aspect_ratio'] == '9:16'
    assert media[-1]['usage'] is None
    assert len(fake.submitted_requests) == 1
    with ZipFile(export_studio_trace(service, pid)) as archive:
        run = json.loads(archive.read(f'providers/video/{probe.task.id}/run.json'))
        task = json.loads(archive.read(f'providers/video/{probe.task.id}/task.json'))
    assert run['request']['model'] == 'MiniMax-H3'
    assert run['prompt'] == probe.provider_run.prompt
    assert task['provider_task_id'] == 'remote-1'


def test_unknown_submission_is_visible_as_unknown_not_success(tmp_path: Path) -> None:
    fake = FakeH3Client()
    fake.submit_unknown_once = True
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id, H3ProbeRequest(shot_id=project.document.shots[0].id))
    events: list[dict[str, JsonValue]] = []
    with production_trace(tmp_path / 'trace', lambda kind, value: events.append(value) if kind == 'provider.video_generation' else None):
        service.advance_task(probe.task.id)
        service.advance_task(probe.task.id)
    assert events[-1]['status'] == 'SUBMIT_UNKNOWN'
    assert events[-1]['provider_task_id'] is None
    assert events[-1]['error_code'] == 'H3_SUBMIT_UNKNOWN'
    assert len(fake.submitted_requests) == 1
