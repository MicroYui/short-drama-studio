"""Whole-request context budgets, durable overflow, and exact recovery."""

import json
from pathlib import Path
from typing import cast
import zipfile

import pytest
from pydantic import JsonValue

from short_drama.agent_runtime.model_input import ContextBudgetExceeded, ModelInputCompiler, generate_context_managed_text
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.production_manager import AgentRunCreateRequest
from short_drama.providers.openai_compatible_language_client import LanguageProviderContextWindowError
from short_drama.service import ShortDramaService
from short_drama.studio_trace import production_trace
from short_drama.studio_trace_export import export_studio_trace
from short_drama.tools.agent import AgentReadContextInput


def _service(root: Path) -> tuple[ShortDramaService, str, str]:
    service = ShortDramaService(database_path=root / 'studio.sqlite3', workspace_root=root / 'media')
    project = service.initialize_fixture().document.project
    run = service.create_agent_run(project.id, AgentRunCreateRequest(objective='只修改指定场次，保留用户的结尾。', auto_plan=False))
    return service, project.id, run.snapshot.run.id


def test_offline_tokenizer_matches_published_o200k_examples() -> None:
    assert count_text_tokens('tiktoken is great!') == 6
    assert count_text_tokens('antidisestablishmentarianism') == 6
    assert count_text_tokens('2 + 2 = 4') == 7
    assert count_text_tokens('お誕生日おめでとう') == 8
    assert ContextBudget().window_tokens == 258000
    assert ContextBudget().input_limit(20000) == 236976


def test_258k_budget_counts_whole_request_and_recovers_exact_overflow_after_restart(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    full_result = [{'id': f'shot-{i}', 'description': '摄影机从教室左侧缓慢移动到窗边，保留人物蓝白校服和桌椅位置。' * 12} for i in range(1200)]
    payload = cast(dict[str, JsonValue], {
        'goal': {'original_request': '只修改第三场，禁止继续生成视频。'},
        'work_order': {'objective': '核对第三场', 'working_notes': '保留用户结尾。下一步读取第三场镜头。'},
        'working_set': {'latest_handoff': {'result': {'status': 'SUCCEEDED', 'output': full_result}}},
        'tool_schemas': {'agent.read_context': {'type': 'object'}},
    })
    events: list[tuple[str, dict[str, JsonValue]]] = []
    instructions = '依据用户最新要求工作。' * 100
    with production_trace(tmp_path / 'trace', lambda kind, data: events.append((kind, data))):
        compiled = ModelInputCompiler(service.agent_runtime.store).prepare(
            run_id=run_id, project_id=project_id, instructions=instructions, payload=payload, max_output_tokens=20000)
    assert compiled.original_input_tokens > 258000
    assert compiled.input_tokens < 160000
    assert compiled.offloaded_blocks == 1
    actual = json.loads(compiled.input_text)
    assert actual['goal'] == payload['goal']
    assert actual['work_order'] == payload['work_order']
    marker = actual['working_set']['latest_handoff']['result']
    assert marker['context_ref']
    assert not any(kind == 'context.offloaded' for kind, _ in events)
    stats = next(data for kind, data in events if kind == 'context.prepared')
    assert stats['window_tokens'] == 258000
    assert stats['estimated_input_tokens'] == count_text_tokens(instructions) + count_text_tokens(compiled.input_text)
    assert stats['reserved_output_tokens'] == 20000
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    reader = restarted.production_manager
    pointer = marker['pointer'] + '/output/873/description'
    first = reader.read_context(AgentReadContextInput(project_id=project_id, run_id=run_id,
        snapshot_id=marker['context_ref'], pointer=pointer, limit=53))
    combined = first.content
    offset = first.next_offset
    while offset is not None:
        page = reader.read_context(AgentReadContextInput(project_id=project_id, run_id=run_id,
            snapshot_id=marker['context_ref'], pointer=pointer, offset=offset, limit=53))
        combined += page.content
        offset = page.next_offset
    assert combined == full_result[873]['description']
    archive_path = export_studio_trace(restarted, project_id)
    with zipfile.ZipFile(archive_path) as archive:
        saved = json.loads(archive.read(f"agent/{run_id}/context/{marker['context_ref']}.json"))
    assert saved == payload


def test_many_distinct_small_entities_are_recoverable_without_unbounded_accumulation(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    payload = cast(dict[str, JsonValue], {'working_set': {
        'entities': [{'id': f'asset-{i}', 'kind': 'asset', 'status': 'completed', 'name': f'道具{i}'} for i in range(700)],
        'latest_handoff': {'result': {'status': 'SUCCEEDED', 'output': {'id': 'current', 'keep': '当前镜头的精确事实'}}}},
        'goal': {'original_request': '保持当前角色设定'}, 'response_schemas': {'type': 'object'}})
    result = ModelInputCompiler(service.agent_runtime.store,
        budget=ContextBudget(window_tokens=9000, compact_at_tokens=7000, target_tokens=4500, envelope_tokens=200)).prepare(
            run_id=run_id, project_id=project_id, instructions='遵守当前目标', payload=payload, max_output_tokens=1000)
    assert result.input_tokens < 4500
    value = json.loads(result.input_text)
    assert value['working_set']['entities']['item_count'] == 700
    assert value['working_set']['latest_handoff']['result']['output']['id'] == 'current'


def test_protected_user_input_is_not_silently_truncated_or_sent_over_budget(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    goal = '新的完整用户约束。' * 2000 + '最后一句：写完剧本立即结束。'
    compiler = ModelInputCompiler(service.agent_runtime.store,
        budget=ContextBudget(window_tokens=8000, compact_at_tokens=6000, target_tokens=4000, envelope_tokens=200))
    with pytest.raises(ContextBudgetExceeded, match='未发送超限请求'):
        compiler.prepare(run_id=run_id, project_id=project_id, instructions='用户目标优先',
            payload={'goal': {'original_request': goal}}, max_output_tokens=1000)


def test_different_small_read_observations_use_recoverable_collection_fallback(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    payload = cast(dict[str, JsonValue], {'working_set': {
        'read_observations': [{'tool_name': 'asset.read', 'result': {'id': f'asset-{i}', 'value': '蓝白校服'}} for i in range(700)],
        'latest_handoff': {'result': {'id': 'latest-asset', 'value': '保留最新事实'}}}})
    result = ModelInputCompiler(service.agent_runtime.store,
        budget=ContextBudget(window_tokens=9000, compact_at_tokens=7000, target_tokens=4500, envelope_tokens=200)).prepare(
            run_id=run_id, project_id=project_id, instructions='保留当前人物', payload=payload, max_output_tokens=1000)
    actual = json.loads(result.input_text)
    assert result.input_tokens < 4500
    assert actual['working_set']['read_observations']['item_count'] == 700
    assert actual['working_set']['latest_handoff']['result']['id'] == 'latest-asset'


def test_leader_plan_body_is_budgeted_but_goal_and_schemas_are_preserved(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    payload = {'goal': {'original_request': '只改这个镜头，不继续视频'},
        'current_plan': {'summary': '当前方案', 'steps': [{'id': 'change-shot', 'depends_on': [],
            'instructions': '修改这个镜头', 'arguments': {'draft': '场景完整描述。' * 6000}}]},
        'tool_schemas': {'shot.save_current': {'type': 'object', 'required': ['request']}}}
    result = ModelInputCompiler(service.agent_runtime.store,
        budget=ContextBudget(window_tokens=9000, compact_at_tokens=7000, target_tokens=4500, envelope_tokens=200)).prepare(
            run_id=run_id, project_id=project_id, instructions='按用户要求收尾',
            payload=cast(dict[str, JsonValue], payload), max_output_tokens=1000)
    actual = json.loads(result.input_text)
    assert actual['goal'] == payload['goal'] and actual['tool_schemas'] == payload['tool_schemas']
    step = actual['current_plan']['steps'][0]
    assert step['id'] == 'change-shot' and step['instructions'] == '修改这个镜头'
    read = actual['context_read']
    marker = step['arguments']
    recovered = service.tool_registry.invoke(read['tool_name'], {
        'project_id': read['project_id'], 'run_id': read['run_id'],
        'snapshot_id': marker['context_ref'], 'pointer': marker['pointer'] + '/draft',
        'offset': 0, 'limit': 10,
    })
    assert recovered.status.value == 'SUCCEEDED'
    assert recovered.output is not None and recovered.output['content'] == ('场景完整描述。' * 2)[:10]


class OverflowOnceClient:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str, image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        del instructions, image_urls, max_output_tokens
        self.inputs.append(input_text)
        if len(self.inputs) == 1:
            raise LanguageProviderContextWindowError('context_length_exceeded')
        return '已继续'


def test_provider_context_rejection_rebuilds_before_one_retry(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    client = OverflowOnceClient()
    result = generate_context_managed_text(client, ModelInputCompiler(service.agent_runtime.store),
        run_id=run_id, project_id=project_id, instructions='保留用户要求',
        payload={'goal': {'original_request': '只写剧本'}, 'work_order': {'task_inputs': {'source': '独立场次的完整参考内容。' * 3000}}},
        max_output_tokens=20000)
    assert result == '已继续'
    assert len(client.inputs) == 2
    assert len(client.inputs[1]) < len(client.inputs[0]) // 2
    assert json.loads(client.inputs[1])['goal']['original_request'] == '只写剧本'


def test_provider_context_rejection_does_not_retry_identical_protected_input(tmp_path: Path) -> None:
    service, project_id, run_id = _service(tmp_path)
    client = OverflowOnceClient()
    with pytest.raises(ContextBudgetExceeded, match='未原样重发'):
        generate_context_managed_text(client, ModelInputCompiler(service.agent_runtime.store),
            run_id=run_id, project_id=project_id, instructions='用户目标优先',
            payload={'goal': {'original_request': '完整用户要求'}}, max_output_tokens=20000)
    assert len(client.inputs) == 1
