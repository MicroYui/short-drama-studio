"""Actual Director read/patch shape through Registry, durable turns and Worker input."""
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image

from short_drama.agent_runtime import AgentRuntimeStore, SpecialistTurnRecord
from short_drama.agent_runtime.model_input import ModelInputCompiler, pointer_value
from short_drama.agent_runtime.token_budget import ContextBudget, count_text_tokens
from short_drama.models import ProjectDocument
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent, _specialist_working_set
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolCallStatus
from tests.test_harness_tool_context import ScriptedClient, runtime_work
from tests.test_scene_write_context import _other, _turn
from tests.test_storyboard_local_edits import _fixture, _request

REAL: dict[str, Any] = json.loads((Path(__file__).parent / 'fixtures/fresh2_storyboard_context.json').read_text())
READS = frozenset({'shot.get_storyboard', 'artifact.read_current', 'artifact.get_production_document'})
WRITES = frozenset({'shot.patch_storyboard', 'shot.replace_storyboard', 'shot.save_current'})


def _service(tmp_path):
    service = ShortDramaService(database_path=tmp_path / 'project.sqlite3', workspace_root=tmp_path / 'media')
    setup = deepcopy(REAL['setup'])
    read = REAL['turns'][0]['result']['output']
    board_doc = {k: v for k, v in read['document'].items() if k != 'current_version'}
    board_doc['versions'] = [{**read['version'], 'payload': read['storyboard']}]
    setup['production_documents'].append(board_doc)
    setup['shots'] = read['storyboard']['shots']
    setup.update(execution_plan=[], reference_plan=[], manifests=[])
    setup['storyboard_document_id'] = board_doc['id']
    for asset in setup['assets']:
        asset['current_version_id'] = asset['versions'][0]['id'] if asset['versions'] else ''
        for version in asset['versions']:
            path = tmp_path / (version['id'] + '.png')
            Image.new('RGB', (32, 48), 'navy').save(path)
            version['local_path'] = str(path)
    document = ProjectDocument.model_validate(setup)
    service.repository.save_project(document)
    return service, document.project.id


def _state(work):
    return _specialist_working_set(work, [], read_tool_ids=READS, write_tool_ids=WRITES).model_dump(mode='json')


def _entities(state, kind):
    return {e['id']: e['state'] for e in state['entities'] if e['kind'] == kind}


def _saved(service, pid):
    args = deepcopy(REAL['turns'][1]['arguments'])
    args['project_id'] = pid
    result = service.tool_registry.invoke('shot.patch_storyboard', args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    return _turn('shot.patch_storyboard', args, result, 11), result


@pytest.mark.parametrize('after_other_tool', [False, True])
def test_actual_t10_t11_reaches_restarted_worker_without_t13(tmp_path, after_other_tool):
    service, pid = _service(tmp_path)
    store, work, context = runtime_work(tmp_path / 'agent.sqlite3')
    read = SpecialistTurnRecord.model_validate(REAL['turns'][0])
    write, result = _saved(service, pid)
    work.specialist_turns = [read, write] + ([_other(12)] if after_other_tool else [])
    work.loaded_tool_schema_ids = ['project.get']
    store.save_work_order(work)
    original = deepcopy(work.specialist_turns)
    reopened = AgentRuntimeStore(store.database_path)
    resumed = reopened.get_work_order(work.id)
    assert resumed is not None
    client = ScriptedClient([json.dumps({'tool_name': 'project.get', 'arguments': {'project_id': pid}, 'reason': 'Continue with the saved groups.'})])
    worker = LanguageSpecialistAgent(client, service.tool_registry)
    worker.context_compiler = ModelInputCompiler(reopened)
    context.project_id = pid
    worker.execute(AgentWorkOrderView(work_order=resumed, context=context, system_prompt='Continue the saved Storyboard.'))
    payload = cast(dict[str, Any], client.payloads[0])
    state = payload['working_set']
    shots, groups = _entities(state, 'shot'), _entities(state, 'clip_group')
    assert len(shots) == 12 and len(groups) == 6
    assert {key: value['shot'] for key, value in shots.items()} == {s['id']: s for s in REAL['expected']['shots']}
    assert {key: value['clip_group'] for key, value in groups.items()} == {g['id']: g for g in REAL['expected']['clip_groups']}
    assert len(_entities(state, 'storyboard_scene_node')) == len(REAL['expected']['scene_nodes'])
    assert next(iter(_entities(state, 'storyboard_state').values()))['metadata'] == {k: v for k, v in REAL['expected'].items() if k not in ('shots', 'clip_groups', 'scene_nodes')}
    assert not any(item['tool_name'] == 'shot.get_storyboard' for item in state['read_observations'])
    delta = result.output['write_context']
    assert delta['objects']['shots']['changed_ids'] == ['shot-xkyx-08', 'shot-xkyx-09']
    assert not delta['objects']['clip_groups']['saved']
    assert delta['parent_version_id'] == read.result['output']['version']['id']
    assert shots['shot-xkyx-01']['storyboard_identity']['version_id'] == delta['parent_version_id']
    assert shots['shot-xkyx-01']['unchanged_through_storyboard_identity']['version_id'] == delta['version_id']
    assert shots['shot-xkyx-08']['storyboard_identity']['version_id'] == delta['version_id']
    assert reopened.get_work_order(work.id).specialist_turns == original
    assert len(client.payloads) == 1


def test_unmodified_visible_read_is_not_duplicated(tmp_path):
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    work.specialist_turns = [SpecialistTurnRecord.model_validate(REAL['turns'][0]), _other(11)]
    state = _state(work)
    assert not _entities(state, 'clip_group') and not _entities(state, 'storyboard_state')
    assert not _entities(state, 'shot')
    assert state['read_observations'][0]['result']['output']['storyboard'] == REAL['turns'][0]['result']['output']['storyboard']


def test_other_writer_parent_breaks_unproven_old_scope_then_fresh_read_restores(tmp_path):
    service, pid = _service(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    read = SpecialistTurnRecord.model_validate(REAL['turns'][0])
    args = deepcopy(REAL['turns'][1]['arguments'])
    target = deepcopy(read.result['output']['storyboard']['shots'][0])
    target['visual'] += '；用户保留的新构图。'
    other = service.tool_registry.invoke('shot.patch_storyboard', {'project_id': pid, 'request': {
        'origin': 'AGENT', 'base_version_id': args['request']['base_version_id'], 'replacements': [{'shot_id': target['id'], 'expected_revision': 1, 'replacement': target}]}})
    assert other.status == ToolCallStatus.SUCCEEDED, other.error
    write, result = _saved(service, pid)
    assert result.output['write_context']['parent_version_id'] == other.output['version']['id']
    work.specialist_turns = [read, write, _other(12)]
    state = _state(work)
    assert set(_entities(state, 'shot')) == {'shot-xkyx-08', 'shot-xkyx-09'}
    assert not _entities(state, 'clip_group')
    assert not any(o['tool_name'] == 'shot.get_storyboard' for o in state['read_observations'])
    fresh_args = {'project_id': pid}
    fresh = service.tool_registry.invoke('shot.get_storyboard', fresh_args)
    work.specialist_turns.extend([_turn('shot.get_storyboard', fresh_args, fresh, 13), _other(14)])
    state = _state(work)
    body = next(o['result']['output']['storyboard'] for o in state['read_observations'] if o['tool_name'] == 'shot.get_storyboard')
    assert body['shots'][0]['visual'].endswith('用户保留的新构图。')
    assert not _entities(state, 'shot')


def test_references_are_not_changed_shots_and_failed_patch_preserves_read(tmp_path):
    service, pid = _service(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    readargs = {'project_id': pid, 'shot_id': 'shot-xkyx-01'}
    read = service.tool_registry.invoke('artifact.read_current', readargs)
    work.specialist_turns = [_turn('artifact.read_current', readargs, read, 1)]
    write, result = _saved(service, pid)
    assert len([r for r in result.artifact_refs if r.artifact_type == 'shot']) == 12
    work.specialist_turns.extend([write, _other(12)])
    state = _state(work)
    assert any(o['request_identifiers'].get('shot_id') == 'shot-xkyx-01' for o in state['read_observations'])
    args = deepcopy(REAL['turns'][1]['arguments'])
    args['request']['replacements'][0]['replacement']['visual'] += ' conflicting stale edit'
    failed = service.tool_registry.invoke('shot.patch_storyboard', args)
    assert failed.status == ToolCallStatus.FAILED
    work.specialist_turns.extend([_turn('shot.patch_storyboard', args, failed, 13), _other(14)])
    assert _entities(_state(work), 'shot') == _entities(state, 'shot')


def test_split_order_groups_and_renumbered_saved_values_match_actual(tmp_path):
    service, board = _fixture(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    args = {'project_id': board.storyboard.project_id}
    read = service.tool_registry.invoke('shot.get_storyboard', args)
    request = {**args, 'request': _request(board).model_dump(mode='json')}
    result = service.tool_registry.invoke('shot.patch_storyboard', request)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    work.specialist_turns = [_turn('shot.get_storyboard', args, read, 1), _turn('shot.patch_storyboard', request, result, 2), _other(3)]
    state = _state(work)
    actual = service.get_storyboard(board.storyboard.project_id).storyboard.model_dump(mode='json')
    assert {s['id']: s for s in actual['shots']} == {key: item['shot'] for key, item in _entities(state, 'shot').items()}
    assert {g['id']: g for g in actual['clip_groups']} == {key: item['clip_group'] for key, item in _entities(state, 'clip_group').items()}
    assert next(iter(_entities(state, 'storyboard_state').values()))['order']['shots'] == [s['id'] for s in actual['shots']]


def test_group_and_shot_removal_retire_old_ids_across_two_actual_writes(tmp_path):
    from short_drama.production_contracts import StoryboardClipGroupSplice, StoryboardPatchRequest, StoryboardShotSplice
    from short_drama.models import ProductionDocumentOrigin

    service, board = _fixture(tmp_path)
    pid = board.storyboard.project_id
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    readargs = {'project_id': pid}
    read = service.tool_registry.invoke('shot.get_storyboard', readargs)
    request = _request(board)
    group = board.storyboard.clip_groups[0]
    children = request.shot_splices[0].shots
    request.clip_group_splices = [StoryboardClipGroupSplice(clip_group_ids=[group.id], clip_groups=[
        group.model_copy(update={'shot_ids': [children[0].id]}),
        group.model_copy(update={'id': group.id + '-insert', 'shot_ids': [children[1].id]}),
    ])]
    args = {**readargs, 'request': request.model_dump(mode='json')}
    split = service.tool_registry.invoke('shot.patch_storyboard', args)
    assert split.status == ToolCallStatus.SUCCEEDED, split.error
    # Collapse the local range back to the original Shot; both removed object IDs
    # must disappear, while unaffected objects carry their proven parent chain.
    current = service.get_storyboard(pid)
    old_first = board.storyboard.shots[0].model_copy(update={'revision': current.storyboard.shots[0].revision})
    remove = StoryboardPatchRequest(base_version_id=current.version.id, origin=ProductionDocumentOrigin.AGENT,
        shot_splices=[StoryboardShotSplice(shot_ids=[child.id for child in children], shots=[old_first])],
        clip_group_splices=[StoryboardClipGroupSplice(clip_group_ids=[group.id, group.id + '-insert'], clip_groups=[group])])
    remove_args = {**readargs, 'request': remove.model_dump(mode='json')}
    removed = service.tool_registry.invoke('shot.patch_storyboard', remove_args)
    assert removed.status == ToolCallStatus.SUCCEEDED, removed.error
    work.specialist_turns = [_turn('shot.get_storyboard', readargs, read, 1), _turn('shot.patch_storyboard', args, split, 2),
        _turn('shot.patch_storyboard', remove_args, removed, 3), _other(4)]
    state = _state(work)
    actual = service.get_storyboard(pid).storyboard.model_dump(mode='json')
    assert {key: value['shot'] for key, value in _entities(state, 'shot').items()} == {s['id']: s for s in actual['shots']}
    assert {key: value['clip_group'] for key, value in _entities(state, 'clip_group').items()} == {g['id']: g for g in actual['clip_groups']}
    assert children[1].id not in _entities(state, 'shot')
    assert group.id + '-insert' not in _entities(state, 'clip_group')
    assert removed.output['write_context']['objects']['shots']['removed_ids'] == [children[1].id]
    assert removed.output['write_context']['objects']['clip_groups']['removed_ids'] == [group.id + '-insert']


@pytest.mark.parametrize('legacy', [False, True])
def test_old_explicit_board_read_does_not_restore_stale_objects_or_rewrite_trace(tmp_path, legacy):
    service, pid = _service(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    original = SpecialistTurnRecord.model_validate(REAL['turns'][0])
    write, result = _saved(service, pid)
    if legacy:
        result.output.pop('write_context')
        write = _turn(write.tool_name, write.arguments, result, 11)
    old = original.model_copy(update={'id': 'old-pinned', 'sequence': 12,
        'arguments': {'project_id': pid, 'version_id': original.result['output']['version']['id']}}, deep=True)
    work.specialist_turns = [original, write, old, _other(13)]
    persisted = deepcopy(work.specialist_turns)
    state = _state(work)
    assert not any(o['tool_name'] == 'shot.get_storyboard' for o in state['read_observations'])
    assert len(_entities(state, 'shot')) == (0 if legacy else 12)
    assert len(_entities(state, 'clip_group')) == (0 if legacy else 6)
    assert work.specialist_turns == persisted


def test_changed_actual_typed_source_invalidates_unproven_parent_objects(tmp_path):
    service, pid = _service(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    read = SpecialistTurnRecord.model_validate(REAL['turns'][0])
    document = service.get_project(pid).document
    original = document.bible_versions[0]
    newer = original.model_copy(update={'id': 'new-current-bible', 'version': original.version + 1}, deep=True)
    document.bible_versions.append(newer)
    document.project.current_bible_version_id = newer.id
    service.repository.save_project(document)
    from short_drama.production_contracts import ProductionSpecReplaceRequest
    from short_drama.models import ProductionDocumentOrigin
    spec = service.get_production_spec(pid)
    updated_spec = service.replace_production_spec(pid, ProductionSpecReplaceRequest(
        base_version_id=spec.version.id, spec=spec.spec.model_copy(update={'bible_version_id': newer.id}),
        origin=ProductionDocumentOrigin.AGENT))
    args = {'project_id': pid, 'request': {'base_version_id': read.result['output']['version']['id'],
        'bible_version_id': newer.id, 'production_spec_version_id': updated_spec.version.id, 'origin': 'AGENT'}}
    result = service.tool_registry.invoke('shot.patch_storyboard', args)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output['write_context']['sources_changed'] is True
    work.specialist_turns = [read, _turn('shot.patch_storyboard', args, result, 11), _other(12)]
    state = _state(work)
    assert not _entities(state, 'clip_group')
    # Changed Shot values, if the actual save returns any, are real saved values;
    # unchanged objects cannot be carried across a changed production input.
    changed = result.output['write_context']['objects']['shots']['saved']
    assert {k: value['shot'] for k, value in _entities(state, 'shot').items()} == {s['id']: s for s in changed}
    assert not any(o['tool_name'] == 'shot.get_storyboard' for o in state['read_observations'])


def test_large_current_board_entities_remain_exactly_page_readable_after_restart(tmp_path):
    service, pid = _service(tmp_path)
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=pid, objective='Continue the actual saved storyboard.', root_agent='leader')
    work = runtime.create_work_order(run.id, sender='leader', recipient_role='director', objective=run.objective, why_now='Use saved facts.')
    write, _ = _saved(service, pid)
    work.specialist_turns = [SpecialistTurnRecord.model_validate(REAL['turns'][0]), write, _other(12)]
    runtime.store.save_work_order(work)
    state = _state(work)
    prepared = ModelInputCompiler(runtime.store, budget=ContextBudget(window_tokens=12000, compact_at_tokens=5000,
        target_tokens=3500, envelope_tokens=100)).prepare(run_id=run.id, project_id=pid,
        instructions='Use the saved storyboard.', payload={'working_set': state}, max_output_tokens=100)
    payload = json.loads(prepared.input_text)
    marker = payload['working_set']['entities']
    assert 'context_ref' in marker and 'context_read' in payload
    restarted = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    snapshot = restarted.agent_runtime.store.get_context_snapshot(marker['context_ref'], run.id)
    assert snapshot is not None and pointer_value(snapshot, marker['pointer']) == state['entities']
    chunks = []
    offset = 0
    while offset is not None:
        result = restarted.tool_registry.invoke('agent.read_context', {'project_id': pid, 'run_id': run.id,
            'snapshot_id': marker['context_ref'], 'pointer': marker['pointer'], 'offset': offset, 'limit': 12000})
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        chunks.append(result.output['content'])
        offset = result.output['next_offset']
    assert json.loads(''.join(chunks)) == state['entities'] and len(chunks) > 1
    assert prepared.input_tokens == count_text_tokens('Use the saved storyboard.') + count_text_tokens(prepared.input_text)
    saved = restarted.agent_runtime.store.get_work_order(work.id)
    assert saved is not None and saved.specialist_turns == work.specialist_turns


@pytest.mark.parametrize('from_saved_summary', [False, True])
def test_actual_newer_ref_retires_independent_shot_from_an_unseen_other_writer(tmp_path, from_saved_summary):
    service, pid = _service(tmp_path)
    _, work, _ = runtime_work(tmp_path / 'agent.sqlite3')
    args = {'project_id': pid, 'shot_id': 'shot-xkyx-01'}
    read = service.tool_registry.invoke('artifact.read_current', args)
    target = deepcopy(read.output['content']['shot'])
    observed = _turn('artifact.read_current', args, read, 1)
    if from_saved_summary:
        saveargs = {**args, 'request': {'shot': target, 'expected_revision': target['revision']}}
        save = service.tool_registry.invoke('shot.save_current', saveargs)
        assert save.status == ToolCallStatus.SUCCEEDED, save.error
        observed = _turn('shot.save_current', saveargs, save, 1)
    target = next(s for s in service.get_project(pid).document.shots if s.id == args['shot_id']).model_dump(mode='json')
    target['visual'] += '；别人先保存的不同画面。'
    other = service.tool_registry.invoke('shot.patch_storyboard', {'project_id': pid, 'request': {
        'base_version_id': service.get_storyboard(pid).version.id, 'origin': 'AGENT',
        'replacements': [{'shot_id': target['id'], 'expected_revision': target['revision'], 'replacement': target}]}})
    assert other.status == ToolCallStatus.SUCCEEDED, other.error
    own_args = deepcopy(REAL['turns'][1]['arguments'])
    own_args['request']['base_version_id'] = service.get_storyboard(pid).version.id
    own_result = service.tool_registry.invoke('shot.patch_storyboard', own_args)
    assert own_result.status == ToolCallStatus.SUCCEEDED, own_result.error
    write = _turn('shot.patch_storyboard', own_args, own_result, 11)
    work.specialist_turns = [observed, write, _other(12)]
    state = _state(work)
    assert 'shot-xkyx-01' not in _entities(state, 'shot')
    assert 'shot-xkyx-01' not in _entities(state, 'saved_shot')
    assert not any(o['request_identifiers'].get('shot_id') == 'shot-xkyx-01' for o in state['read_observations'])
    assert {key for key in _entities(state, 'shot')} == {'shot-xkyx-08', 'shot-xkyx-09'}
    # Nothing invents the other Writer's visual: only a new actual read supplies it.
    fresh = service.tool_registry.invoke('artifact.read_current', args)
    work.specialist_turns.extend([_turn('artifact.read_current', args, fresh, 13), _other(14)])
    fresh_state = _state(work)
    item = next(o for o in fresh_state['read_observations'] if o['request_identifiers'].get('shot_id') == 'shot-xkyx-01')
    assert item['result']['output']['content']['shot']['visual'].endswith('别人先保存的不同画面。')
