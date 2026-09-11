"""Product-runner behavior over real persistence, Tools and local fake inference."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from short_drama.agent_runtime import DelegationStatus, SpecialistTurnStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.production_manager import AgentPlanProposal, AgentRunAdvanceRequest, AgentRunCreateRequest, AgentRunReplanRequest
from short_drama.providers.openai_compatible_language_client import LanguageProviderTransportError
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioJob, StudioJobControl, StudioMessage, StudioScope, StudioProjectCreate
from short_drama.studio_runtime import StudioAgentRunner


class HarnessLanguageClient:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.handler = handler
        self.inputs: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        del instructions, image_urls, max_output_tokens
        payload = json.loads(input_text)
        with self.lock:
            self.inputs.append(payload)
        return json.dumps(self.handler(payload), ensure_ascii=False)


def _service(root: Path, client: HarnessLanguageClient, *, retries: tuple[float, ...] = ()) -> tuple[ShortDramaService, str]:
    service = ShortDramaService(database_path=root / 'studio.sqlite3', workspace_root=root / 'media',
                               language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url='http://127.0.0.1:4141/v1'))
    service.studio_runtime = StudioAgentRunner(service, retry_delays=retries)
    return service, project_id


def _wait_job(service: ShortDramaService, job_id: str, status: str, timeout: float = 12) -> StudioJob:
    until = time.monotonic() + timeout
    runner = service.studio_runtime
    while time.monotonic() < until:
        job = next(job for job in service.repository.list_studio_jobs() if job.id == job_id)
        thread = runner.threads.get(job_id)
        if job.status == status and (thread is None or not thread.is_alive()):
            return job
        if job.status == 'failed' and status != 'failed':
            pytest.fail(f'Unexpected job failure: {job.error}')
        threading.Event().wait(0.015)
    pytest.fail(f'Job never reached {status}: {job.model_dump()}')


def _plan(*names: str, complete: bool = True) -> dict[str, Any]:
    return {'summary': '已完成本次范围' if not names else '按当前目标安排分工',
            'goal_summary': '只完成用户要求的剧本或分镜，不追加视频生成。',
            'goal_complete': complete,
            'steps': [{'id': name, 'title': name, 'kind': 'DELEGATION', 'assigned_agent': name,
                       'production_skill_role': 'DIRECTOR', 'production_prompt_stage': 'STORYBOARD',
                       'instructions': f'完成 {name}，只处理当前范围。'} for name in names]}


def _done(summary: str = '当前分工完成') -> dict[str, Any]:
    return {'status': 'COMPLETED', 'concise_summary': summary}


def _scene(text: str) -> dict[str, Any]:
    return {'tool_name': 'script.save_scene', 'reason': '逐场保存剧本',
            'arguments': {'project_id': 'project-dont-trust-me', 'request': {'heading': 'HARNESS 测试场次', 'screenplay_text': text}},
            'working_notes': '本场结束后停止；用户指定的最后一句保留。'}


def _saved_scenes(service: ShortDramaService, project_id: str) -> list[str]:
    return [scene.screenplay_text or '' for scene in service.get_project(project_id).document.script_versions[-1].structured_scenes
            if scene.heading == 'HARNESS 测试场次']


@pytest.mark.parametrize('status', ['paused', 'waiting_user', 'completed', 'failed'])
def test_pause_does_not_restart_inactive_job(tmp_path: Path, status: str) -> None:
    client = HarnessLanguageClient(lambda _payload: _plan())
    service, project_id = _service(tmp_path, client)
    job = StudioJob.model_validate({
        'id': 'already-inactive', 'project_id': project_id,
        'objective': '保留当前任务，等待明确继续。', 'status': status, 'scope': {},
        'created_at': '2026-09-09T00:00:00+00:00',
        'updated_at': '2026-09-09T00:00:00+00:00',
    })
    service.repository.save_studio_job(job)
    returned = service.studio_runtime.control(job.id, StudioJobControl(action='pause'))
    assert returned.status == status
    assert job.id not in service.studio_runtime.threads
    assert client.inputs == []


def test_blank_workspace_retains_first_goal_for_later_script_revision(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    project_id = service.studio.create_project(StudioProjectCreate(name='空白作品')).document.project.id
    original = '做一个食堂里发生的短片，45–60秒，直接做到成片。'
    job = service.studio_runtime.send(project_id, StudioMessage(text=original))
    _wait_job(service, job.id, 'completed')
    document = service.get_project(project_id).document
    assert document.project.input_text == original
    request = service._script_revision_request(document, document.script_versions[-1])
    assert request.input_text == original
    followup = service.studio_runtime.send(project_id, StudioMessage(text='主角自然一点。'))
    _wait_job(service, followup.id, 'completed')
    assert service.get_project(project_id).document.project.input_text == original


def test_resume_repairs_legacy_blank_brief_with_original_job_goal(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    project_id = service.studio.create_project(StudioProjectCreate(name='已存在的空白作品')).document.project.id
    job = StudioJob(id='studio-job-legacy-brief', project_id=project_id,
                    objective='原始的完整短片要求。', scope=StudioScope(), status='paused',
                    created_at='2026-09-09T00:00:00+00:00', updated_at='2026-09-09T00:00:00+00:00')
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action='resume'))
    _wait_job(service, job.id, 'completed')
    assert service.get_project(project_id).document.project.input_text == job.objective


def test_single_prompt_continues_across_plans_and_stops_at_agent_completion(tmp_path: Path) -> None:
    leader_turns = 0
    writer_turns = 0

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal leader_turns, writer_turns
        if 'work_order' not in payload:
            leader_turns += 1
            return _plan('writer', complete=False) if leader_turns == 1 else _plan('storyboard')
        if payload['work_order']['recipient_role'] == 'writer':
            writer_turns += 1
            return _scene('完整的一场剧本。') if writer_turns == 1 else _done('剧本已经保存')
        return _done('分镜已核对；到此结束')

    client = HarnessLanguageClient(respond)
    service, project_id = _service(tmp_path, client)
    job = service.studio_runtime.send(project_id, StudioMessage(text='生成剧本，然后整理分镜，到此结束。'))
    finished = _wait_job(service, job.id, 'completed')
    assert leader_turns == 2
    assert writer_turns == 2
    assert _saved_scenes(service, project_id) == ['完整的一场剧本。']
    assert finished.run_id is not None
    workers = service.agent_runtime.store.list_work_orders(finished.run_id)
    assert len(workers) == 2 and all(work.status == DelegationStatus.COMPLETED for work in workers)
    activity = service.studio_runtime.activity(project_id)
    assert len(activity.workers) == 2 and activity.context_window_tokens == 258000
    assert all(event.data.get('job_id') == job.id for event in activity.events
               if event.kind in {'context.prepared', 'worker.updated', 'chat.user', 'chat.assistant'})
    assert any(event.kind == 'context.prepared' for event in activity.events)
    assert not service.get_project(project_id).tasks


def test_ready_siblings_reach_the_model_in_parallel(tmp_path: Path) -> None:
    rendezvous = threading.Barrier(2, timeout=5)
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if 'work_order' not in payload:
            return _plan('scene-a', 'scene-b')
        rendezvous.wait()
        return _done()
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text='并行核对两个独立场次，完成即停。'))
    _wait_job(service, job.id, 'completed')
    assert len(service.studio_runtime.activity(project_id).workers) == 2


def test_fast_worker_saves_and_finishes_while_sibling_is_still_busy(tmp_path: Path) -> None:
    slow_started, release_slow, writer_finished = threading.Event(), threading.Event(), threading.Event()
    writer_turns = 0

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal writer_turns
        if 'work_order' not in payload:
            return _plan('writer', 'slow-reviewer')
        if payload['work_order']['recipient_role'] == 'slow-reviewer':
            slow_started.set()
            assert release_slow.wait(30)
            return _done('较慢的观察结束')
        writer_turns += 1
        if writer_turns == 1:
            return _scene('其他分工仍在观察时，这一场已经保存。')
        writer_finished.set()
        return _done('写作完成')

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text='独立写作和观察同时进行，各自完成后交接。'))
    try:
        assert slow_started.wait(15)
        assert writer_finished.wait(10), 'A slow sibling blocked the next write and decision'
        assert _saved_scenes(service, project_id) == ['其他分工仍在观察时，这一场已经保存。']
        assert not release_slow.is_set()
    finally:
        release_slow.set()
    _wait_job(service, job.id, 'completed')


def test_ready_leader_work_prepares_parallel_post_while_retained_worker_waits(tmp_path: Path) -> None:
    slow_started, release_slow, post_finished = threading.Event(), threading.Event(), threading.Event()
    retained_id = ''
    leader_turns = 0

    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal leader_turns
        if 'work_order' not in payload:
            leader_turns += 1
            assert _saved_scenes(service, project_id) == ['独立准备已经保存，后期可以开始。']
            plan = _plan('parallel-post')
            plan['carried_work_order_ids'] = [retained_id]
            return plan
        if payload['work_order']['recipient_role'] == 'slow-video':
            slow_started.set()
            assert release_slow.wait(25)
            return _done('远端候选返回')
        post_finished.set()
        return _done('后期已利用现有素材完成')

    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    initial = service.create_agent_run(project_id, AgentRunCreateRequest(
        objective='候选继续等待，同时准备并完成现有素材后期。',
        initial_plan=AgentPlanProposal.model_validate(_plan('slow-video', complete=False)),
    ))
    run_id = initial.snapshot.run.id
    created = service.advance_agent_run(run_id, AgentRunAdvanceRequest(max_actions=1))
    retained_id = created.created_work_order_ids[0]
    service.replan_agent_run(run_id, AgentRunReplanRequest(
        reason='先完成独立准备，再安排后期，保留仍在等待的候选。',
        proposed_plan=AgentPlanProposal.model_validate({
            'summary': '先保存独立准备结果，再决定后续并行分工。',
            'goal_complete': False, 'carried_work_order_ids': [retained_id],
            'steps': [{'id': 'prepare-post', 'title': '保存独立准备', 'kind': 'TOOL_CALL',
                       'assigned_agent': 'LEADER', 'tool_name': 'script.save_scene',
                       'instructions': '保存当前独立准备，不等待远端候选。',
                       'arguments': {'project_id': project_id, 'request': {
                           'heading': 'HARNESS 测试场次',
                           'screenplay_text': '独立准备已经保存，后期可以开始。'}}}],
        }),
    ))
    job = StudioJob(id='retained-work-parallel-post', project_id=project_id, run_id=run_id,
                    objective='候选继续等待，同时准备并完成现有素材后期。', scope=StudioScope(),
                    status='paused', created_at='2026-09-11T00:00:00+00:00',
                    updated_at='2026-09-11T00:00:00+00:00')
    service.repository.save_studio_job(job)
    service.studio_runtime.control(job.id, StudioJobControl(action='resume'))
    try:
        assert post_finished.wait(12), 'Retained work starved independent preparation or the next Leader decision'
        assert slow_started.wait(3)
        assert not release_slow.is_set()
        assert leader_turns == 1
    finally:
        release_slow.set()
        _wait_job(service, job.id, 'completed')


@pytest.mark.parametrize('restart', [False, True])
def test_pause_preserves_requested_tool_and_resume_executes_it_once(tmp_path: Path, restart: bool) -> None:
    started, release = threading.Event(), threading.Event()
    worker_turns = 0
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal worker_turns
        if 'work_order' not in payload:
            return _plan('writer')
        worker_turns += 1
        if worker_turns == 1:
            started.set()
            assert release.wait(5)
            return _scene('暂停恢复后只写一次。')
        assert payload['work_order']['working_notes'] == '本场结束后停止；用户指定的最后一句保留。'
        return _done()
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    runner = service.studio_runtime
    job = runner.send(project_id, StudioMessage(text='只生成完整剧本，写完停下。'))
    try:
        assert started.wait(5)
        runner.control(job.id, StudioJobControl(action='pause'))
    finally:
        release.set()
    paused = _wait_job(service, job.id, 'paused')
    assert not _saved_scenes(service, project_id)
    assert paused.run_id is not None
    work = service.agent_runtime.store.list_work_orders(paused.run_id)[0]
    assert work.specialist_turns[0].status == SpecialistTurnStatus.REQUESTED
    if restart:
        client = HarnessLanguageClient(respond)
        service = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
                                   language_provider_factory=lambda _key, _url: client)
        runner = service.studio_runtime
    runner.control(job.id, StudioJobControl(action='resume'))
    finished = _wait_job(service, job.id, 'completed')
    assert finished.run_id == paused.run_id
    assert _saved_scenes(service, project_id) == ['暂停恢复后只写一次。']


def test_mid_decision_user_input_supersedes_stale_write_and_is_not_truncated(tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()
    new_instruction = '修改要求：' + '保留这一处人物关系。' * 600 + '最后只交付修改建议，不再写入。'
    seen: list[str] = []
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if 'work_order' in payload:
            started.set()
            assert release.wait(5)
            return _scene('这段旧计划不能写入。')
        current = payload['goal']['latest_user_instructions']
        if current:
            seen.extend(current)
            return _plan()
        return _plan('old-writer')
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    runner = service.studio_runtime
    job = runner.send(project_id, StudioMessage(text='生成剧本。'))
    try:
        assert started.wait(5)
        steered = runner.send(project_id, StudioMessage(text=new_instruction))
        assert steered.id == job.id
    finally:
        release.set()
    finished = _wait_job(service, job.id, 'completed')
    assert not finished.pending_messages
    assert any(new_instruction in item for item in seen)
    assert not _saved_scenes(service, project_id)


def test_failed_message_handoff_keeps_pending_input_and_same_run_after_restart(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get('goal', {}).get('latest_user_instructions'):
            raise LanguageProviderTransportError('fake disconnected replan')
        return _plan()
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective='原目标', auto_plan=False))
    original = StudioJob(id='persisted-job', project_id=project_id, objective='原目标', scope=StudioScope(), run_id=view.snapshot.run.id,
                         status='queued', pending_messages=[StudioMessage(text='新结尾必须保留。')],
                         created_at='2026-09-09T00:00:00+00:00', updated_at='2026-09-09T00:00:00+00:00')
    service.repository.save_studio_job(original)
    service.studio_runtime.resume_after_restart()
    failed = _wait_job(service, original.id, 'failed')
    assert [message.text for message in failed.pending_messages] == ['新结尾必须保留。']
    repaired_client = HarnessLanguageClient(lambda _payload: _plan())
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
                                 language_provider_factory=lambda _key, _url: repaired_client)
    resumed = restarted.studio_runtime.send(project_id, StudioMessage(text='继续，前面的要求仍然生效。'))
    assert resumed.id == original.id and resumed.run_id == original.run_id
    final = _wait_job(restarted, original.id, 'completed')
    assert not final.pending_messages
    joined = '\n'.join(repaired_client.inputs[0]['goal']['latest_user_instructions'])
    assert '新结尾必须保留。' in joined and '前面的要求仍然生效。' in joined


def test_transport_retry_keeps_completed_tool_result_without_repeating_write(tmp_path: Path) -> None:
    worker_turns = 0
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal worker_turns
        if 'work_order' not in payload:
            return _plan('writer')
        worker_turns += 1
        if worker_turns == 1:
            return _scene('断网前已经落盘。')
        if worker_turns == 2:
            raise LanguageProviderTransportError('fake network interruption')
        return _done()
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond), retries=(0.01,))
    job = service.studio_runtime.send(project_id, StudioMessage(text='只写剧本。'))
    completed = _wait_job(service, job.id, 'completed')
    assert _saved_scenes(service, project_id) == ['断网前已经落盘。']
    assert worker_turns == 3
    assert completed.run_id is not None
    work = service.agent_runtime.store.list_work_orders(completed.run_id)[0]
    assert len(work.specialist_turns) == 1
    assert any(event.kind == 'agent.recovering' for event in service.studio_runtime.activity(project_id).events)


def test_transport_failure_between_completed_stages_reaches_runner_retry(tmp_path: Path) -> None:
    leader_turns, worker_turns = 0, 0
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal leader_turns, worker_turns
        if 'work_order' not in payload:
            leader_turns += 1
            if leader_turns == 1:
                return _plan('writer', complete=False)
            if leader_turns == 2:
                raise LanguageProviderTransportError('fake disconnect during automatic replanning')
            return _plan()
        worker_turns += 1
        return _scene('前一阶段的成果必须保留。') if worker_turns == 1 else _done()
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond), retries=(0.01,))
    job = service.studio_runtime.send(project_id, StudioMessage(text='生成剧本并整理最后的交付说明。'))
    _wait_job(service, job.id, 'completed')
    assert leader_turns == 3 and worker_turns == 2
    assert _saved_scenes(service, project_id) == ['前一阶段的成果必须保留。']
    assert any(event.kind == 'agent.recovering' for event in service.studio_runtime.activity(project_id).events)


def test_unactionable_plan_failure_is_visible_instead_of_spinning(tmp_path: Path) -> None:
    leader_turns = 0
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal leader_turns
        if 'work_order' in payload:
            return _done()
        leader_turns += 1
        if leader_turns == 1:
            return _plan('inspect', complete=False)
        raise ValueError('后续计划缺少必要的结构')
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text='继续完成这个目标。'))
    failed = _wait_job(service, job.id, 'failed')
    assert failed.error is not None and '暂时无法推进' in failed.error
    assert failed.run_id is not None
    assert service.agent_runtime.store.list_work_orders(failed.run_id)[0].status == DelegationStatus.COMPLETED


def test_exhausted_retries_are_visible_and_pause_interrupts_long_backoff(tmp_path: Path) -> None:
    def disconnected(_payload: dict[str, Any]) -> dict[str, Any]:
        raise LanguageProviderTransportError('fake unavailable connection')
    client = HarnessLanguageClient(disconnected)
    service, project_id = _service(tmp_path, client, retries=(0.01, 0.01))
    job = service.studio_runtime.send(project_id, StudioMessage(text='生成剧本。'))
    failed = _wait_job(service, job.id, 'failed')
    assert failed.retry_attempt == 2 and len(client.inputs) == 3
    service.studio_runtime = StudioAgentRunner(service, retry_delays=(30,))
    service.studio_runtime.control(job.id, StudioJobControl(action='resume'))
    until = time.monotonic() + 5
    while time.monotonic() < until:
        if service.studio_runtime.activity(project_id).jobs[0].status == 'retrying':
            break
        threading.Event().wait(0.01)
    else:
        pytest.fail('Retrying state never became visible')
    service.studio_runtime.control(job.id, StudioJobControl(action='pause'))
    _wait_job(service, job.id, 'paused', timeout=2)


def test_new_goal_after_completion_does_not_revive_older_unfinished_job(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _plan()))
    for job_id, status, timestamp in [('older', 'paused', '2026-09-08T00:00:00+00:00'),
                                      ('newer', 'completed', '2026-09-09T00:00:00+00:00')]:
        service.repository.save_studio_job(StudioJob(id=job_id, project_id=project_id, objective=job_id, scope=StudioScope(),
            status=status, created_at=timestamp, updated_at=timestamp))
    job = service.studio_runtime.send(project_id, StudioMessage(text='一个新的要求'))
    assert job.id not in {'older', 'newer'}
    _wait_job(service, job.id, 'completed')


def test_waiting_user_ends_turn_and_reply_resumes_original_goal(tmp_path: Path) -> None:
    def respond(payload: dict[str, Any]) -> dict[str, Any]:
        if payload['goal']['latest_user_instructions']:
            return _plan()
        return {'summary': '需确定结局', 'steps': [{'id': 'ask', 'title': '结局', 'kind': 'USER_INPUT',
            'assigned_agent': 'user', 'instructions': '你希望采用什么结局？'}]}
    service, project_id = _service(tmp_path, HarnessLanguageClient(respond))
    job = service.studio_runtime.send(project_id, StudioMessage(text='先确认结局再继续。'))
    waiting = _wait_job(service, job.id, 'waiting_user')
    # One valid product message plus its scope already exceeds the former 12k
    # answer limit. Keep the complete answer through the handoff.
    answer = '开放式结局。' * 1995 + '完成剧本即停。'
    resumed = service.studio_runtime.send(project_id, StudioMessage(text=answer))
    assert resumed.id == waiting.id
    finished = _wait_job(service, job.id, 'completed')
    assert finished.run_id == waiting.run_id
    assert not finished.pending_messages


def test_startup_recovers_in_progress_worker_without_replaying_completed_tool(tmp_path: Path) -> None:
    service, project_id = _service(tmp_path, HarnessLanguageClient(lambda _payload: _scene('重启前已完成保存。')))
    view = service.create_agent_run(project_id, AgentRunCreateRequest(objective='只写这一场',
        initial_plan=AgentPlanProposal.model_validate(_plan('writer'))))
    run_id = view.snapshot.run.id
    advanced = service.advance_agent_run(run_id, AgentRunAdvanceRequest())
    work_id = advanced.created_work_order_ids[0]
    service.execute_agent_work_order(work_id)  # Persist the request.
    service.execute_agent_work_order(work_id)  # Persist the completed Tool.
    service.agent_runtime.begin_work_order(work_id)  # Crash during the next inference.
    service.repository.save_studio_job(StudioJob(id='recovered-job', project_id=project_id, scope=StudioScope(),
        objective='只写这一场', run_id=run_id, status='running',
        created_at='2026-09-09T00:00:00+00:00', updated_at='2026-09-09T00:00:00+00:00'))
    client = HarnessLanguageClient(lambda _payload: _done())
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root,
                                 language_provider_factory=lambda _key, _url: client)
    restarted.studio_runtime.resume_after_restart()
    completed = _wait_job(restarted, 'recovered-job', 'completed')
    assert completed.run_id == run_id
    assert len(client.inputs) == 1
    assert client.inputs[0]['work_order']['working_notes'] == '本场结束后停止；用户指定的最后一句保留。'
    assert _saved_scenes(restarted, project_id) == ['重启前已完成保存。']
