"""Replay real Fresh3 selections through the Registry; pixels remain synthetic."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier
from typing import cast
from PIL import Image
from pydantic import JsonValue
import pytest
from short_drama.agent_runtime import ContextPack
from short_drama.agent_runtime.model_input import ModelInputCompiler
from short_drama.agent_runtime.models import DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.agent_runtime.media_handoffs import observed_media_handoffs
from short_drama.models import Asset, AssetKind, AssetVersion, DomainModel, ReferencePackEntry, ProductionProfile, ApprovalStatus, Freshness, AssetMediaType, ReferenceAngle, OriginType
from short_drama.production_manager import AgentWorkOrderView, LanguageSpecialistAgent
from short_drama.production_profiles import ProductionSkillRole, compile_production_prompt
from short_drama.reference_planner import ReferencePackEntriesCreateRequest, ReferencePackEntriesMutationResponse, ReferencePackEntryCreateRequest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioEpisodeCreate
from short_drama.tools.contracts import ToolResult
from tests.test_harness_tool_context import ScriptedClient

class SavedTurn(DomainModel):
    id: str
    sequence: int
    requested_at: str
    arguments: dict[str, JsonValue]
    saved_entry: ReferencePackEntry

class SavedAsset(DomainModel):
    id: str
    name: str
    kind: AssetKind
    versions: list[dict[str, JsonValue]]

class ReplayCase(DomainModel):
    role: str
    work_order_id: str
    source_project_id: str
    source: str
    assets: list[SavedAsset]
    turns: list[SavedTurn]

def prepare(tmp_path: Path, case_index: int = 0) -> tuple[ShortDramaService, str, ReplayCase, dict[str, JsonValue]]:
    cases = json.loads((Path(__file__).parent/'fixtures/fresh3_reference_entries.json').read_text())
    case = ReplayCase.model_validate(cases[case_index])
    service = ShortDramaService(database_path=tmp_path/'studio.sqlite3', workspace_root=tmp_path/'media')
    doc = service.initialize_fixture().document
    for asset in case.assets:
        versions: list[AssetVersion] = []
        for index, saved in enumerate(asset.versions):
            vid = str(saved['id']); path = tmp_path/(vid+'.png')
            with Image.new('RGB', (32, 48), 'white') as image:
                image.save(path)
            versions.append(AssetVersion(id=vid, version=index+1, origin_type=OriginType.FIXTURE, descriptor='Existing selected media',
                media_type=AssetMediaType.IMAGE, local_path=str(path), mime_type='image/png', width=32, height=48,
                approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at='2026-09-12T00:00:00Z'))
        doc.assets.append(Asset(id=asset.id, project_id=doc.project.id, name=asset.name, kind=asset.kind,
            current_version_id=versions[0].id, versions=versions))
    service.repository.save_project(doc)
    entries: list[JsonValue] = [ReferencePackEntryCreateRequest.model_validate(t.arguments['request']).model_dump(mode='json') for t in case.turns]
    return service, doc.project.id, case, {'project_id': doc.project.id, 'request': {'entries': entries}}

def invoke(service: ShortDramaService, args: dict[str, JsonValue]) -> tuple[ToolResult, ReferencePackEntriesMutationResponse]:
    result = service.tool_registry.invoke('artifact.add_reference_entries', args)
    assert result.error is None, result.model_dump_json()
    return result, ReferencePackEntriesMutationResponse.model_validate(result.output)

def events(service: ShortDramaService, pid: str) -> int:
    return sum(e.kind == 'content.updated' for e in service.repository.list_studio_events(pid))

@pytest.mark.parametrize('case_index', [0, 1, 2], ids=['lin-master-face', 'zhou-master-face', 'paper-four-states'])
def test_real_selections_save_once_keep_draft_and_replay_after_restart(tmp_path: Path, case_index: int) -> None:
    service, pid, case, args = prepare(tmp_path, case_index)
    before = service.get_project(pid).document; original = deepcopy(args); count = events(service, pid)
    result, saved = invoke(service, args)
    assert args == original and events(service, pid) == count+1
    assert saved.created_entry_ids == saved.entry_ids and saved.reused_entry_ids == []
    assert [e.id for e in saved.pack.entries] == saved.entry_ids
    assert [e.model_dump(exclude={'id'}) for e in saved.pack.entries] == [t.saved_entry.model_dump(exclude={'id'}) for t in case.turns]
    after = service.get_project(pid).document
    assert after.assets == before.assets and after.shots == before.shots
    assert after.script_versions == before.script_versions and after.production_documents == before.production_documents
    assert result.receipt and result.receipt.changed_ids == {'created_reference_entry_ids': saved.entry_ids}
    assert set(result.output or {}) == {'pack', 'entry_ids', 'created_entry_ids', 'reused_entry_ids'}
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    again, repeated = invoke(reopened, args)
    assert repeated.pack == saved.pack and repeated.entry_ids == saved.entry_ids
    assert repeated.created_entry_ids == [] and repeated.reused_entry_ids == saved.entry_ids
    assert again.receipt and again.receipt.changed_ids == {'created_reference_entry_ids': []}
    assert reopened.get_project(pid).document == after and events(reopened, pid) == count+1

def test_same_image_distinct_roles_and_shared_root_are_independent_uses(tmp_path: Path) -> None:
    service, _pid, _case, args = prepare(tmp_path)
    request = ReferencePackEntriesCreateRequest.model_validate(args['request'])
    request.entries[1].asset_version_id = request.entries[0].asset_version_id
    _, saved = invoke(service, {**args, 'request': request.model_dump(mode='json')})
    assert len(saved.entry_ids) == 2 and len({e.asset_version_id for e in saved.pack.entries}) == 1
    assert len({e.role for e in saved.pack.entries}) == 2
    assert {e.lineage_root_version_id for e in saved.pack.entries} == {request.entries[0].asset_version_id}

@pytest.mark.parametrize('failure', ['REJECTED', 'STALE', 'missing', 'empty', 'wrong-root', 'non-image', 'existing-edit'])
def test_invalid_second_entry_never_partially_saves_first(tmp_path: Path, failure: str) -> None:
    service, pid, _case, args = prepare(tmp_path)
    request = ReferencePackEntriesCreateRequest.model_validate(args['request']); second = request.entries[1]
    doc = service.get_project(pid).document
    source = next(v for a in doc.assets for v in a.versions if v.id == second.asset_version_id)
    if failure == 'REJECTED': source.approval_status = ApprovalStatus.REJECTED
    elif failure == 'STALE': source.freshness = Freshness.STALE
    elif failure in {'missing', 'empty'}:
        assert source.local_path
        Path(source.local_path).unlink()
        if failure == 'empty': Path(source.local_path).touch()
    elif failure == 'wrong-root':
        foreign = next(a for a in doc.assets if a.id != second.subject_asset_id)
        foreign.versions.append(source.model_copy(update={'id': 'foreign-root'}))
        second.lineage_root_version_id = 'foreign-root'
    elif failure == 'non-image': source.media_type = AssetMediaType.VIDEO
    service.repository.save_project(doc)
    if failure == 'existing-edit':
        single = second.model_dump(mode='json'); single['description'] = 'User chose a different use'
        assert service.tool_registry.invoke('artifact.add_reference_entry', {'project_id': pid, 'request': single}).error is None
    before = service.get_project(pid).document; count = events(service, pid)
    result = service.tool_registry.invoke('artifact.add_reference_entries', {**args, 'request': request.model_dump(mode='json')})
    assert result.error and '/request/entries/1' in result.error.message and 'nothing was saved' in result.error.message
    assert service.get_project(pid).document == before and events(service, pid) == count

def test_multiple_errors_and_duplicate_or_mixed_subjects_are_explicit(tmp_path: Path) -> None:
    service, pid, _case, args = prepare(tmp_path); before = service.get_project(pid).document
    request = ReferencePackEntriesCreateRequest.model_validate(args['request'])
    request.entries[0].asset_version_id = 'missing-first'; request.entries[1].asset_version_id = 'missing-second'
    result = service.tool_registry.invoke('artifact.add_reference_entries', {**args, 'request': request.model_dump(mode='json')})
    assert result.error and '/request/entries/0' in result.error.message and '/request/entries/1' in result.error.message
    raw = ReferencePackEntriesCreateRequest.model_validate(args['request']).model_dump(mode='json')
    entries = cast(list[dict[str, JsonValue]], raw['entries']); entries.append(deepcopy(entries[0]))
    result = service.tool_registry.invoke('artifact.add_reference_entries', {**args, 'request': raw})
    assert result.error and 'repeats the same' in result.error.message
    entries.pop(); entries[1]['subject_asset_id'] = 'different-subject'
    result = service.tool_registry.invoke('artifact.add_reference_entries', {**args, 'request': raw})
    assert result.error and 'different subject' in result.error.message
    assert service.get_project(pid).document == before

def test_single_entry_still_updates_existing_content_and_batch_protects_it(tmp_path: Path) -> None:
    service, pid, case, args = prepare(tmp_path); _, batch = invoke(service, args)
    one = deepcopy(case.turns[0].arguments); one['project_id'] = pid
    cast(dict[str, JsonValue], one['request'])['description'] = 'Explicitly revised single use'
    result = service.tool_registry.invoke('artifact.add_reference_entry', one)
    assert result.error is None and result.output and set(result.output) == {'pack', 'entry', 'project'}
    assert cast(dict[str, JsonValue], result.output['entry'])['id'] == batch.entry_ids[0]
    before = service.get_project(pid).document
    result = service.tool_registry.invoke('artifact.add_reference_entries', args)
    assert result.error and 'different saved content' in result.error.message
    assert service.get_project(pid).document == before

@pytest.mark.parametrize('same_entries', [True, False])
def test_two_service_instances_preserve_concurrent_entries_and_unique_keys(tmp_path: Path, same_entries: bool) -> None:
    service, pid, _case, args = prepare(tmp_path)
    other = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    second_args = deepcopy(args)
    if not same_entries:
        request = ReferencePackEntriesCreateRequest.model_validate(second_args['request'])
        request.entries[1].angle = ReferenceAngle.THREE_QUARTER_LEFT; second_args['request'] = request.model_dump(mode='json')
    barrier = Barrier(2)
    def save(owner: ShortDramaService, payload: dict[str, JsonValue]) -> ReferencePackEntriesMutationResponse:
        barrier.wait(timeout=5)
        return invoke(owner, payload)[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(save, service, args), pool.submit(save, other, second_args)
        first, second = a.result(timeout=10), b.result(timeout=10)
    pack = service.get_project(pid).document.reference_packs[-1]
    assert len(pack.entries) == (2 if same_entries else 3)
    assert len({(e.asset_version_id,e.role,e.angle,e.framing) for e in pack.entries}) == len(pack.entries)
    assert first.entry_ids[0] == second.entry_ids[0]
    assert sum(len(v.created_entry_ids) for v in (first,second)) == len(pack.entries)

def test_episode_batch_saves_shared_pack_without_changing_shots(tmp_path: Path) -> None:
    service, pid, _case, args = prepare(tmp_path)
    child = service.studio.create_episode(pid, StudioEpisodeCreate(title='Episode')).document.project.id
    root_before, child_before = service.get_project(pid).document, service.get_project(child).document
    _, saved = invoke(service, {**args, 'project_id': child})
    root_after, child_after = service.get_project(pid).document, service.get_project(child).document
    assert saved.pack.project_id == pid
    assert root_after.reference_packs[-1] == child_after.reference_packs[-1] == saved.pack
    assert root_after.assets == root_before.assets and child_after.assets == child_before.assets
    assert root_after.shots == root_before.shots and child_after.shots == child_before.shots

def test_batch_reaches_restarted_worker_and_partial_handoff(tmp_path: Path) -> None:
    service, pid, _case, args = prepare(tmp_path); result, saved = invoke(service, args)
    runtime = service.production_manager.runtime
    run = runtime.create_run(project_id=pid, objective='Finish selected references', root_agent='leader')
    work = runtime.create_work_order(run.id, sender='leader', recipient_role='builder', objective=run.objective,
        why_now='Selected uses', production_skill_role=ProductionSkillRole.ASSET_BUILDER)
    turn = SpecialistTurnRecord(id='actual-batch-turn', sequence=9, tool_name='artifact.add_reference_entries',
        arguments=args, reason='Register known uses', status=SpecialistTurnStatus.COMPLETED,
        result=cast(dict[str, JsonValue], result.model_dump(mode='json')))
    work.specialist_turns.append(turn); work.status = DelegationStatus.IN_PROGRESS; runtime.store.save_work_order(work)
    reopened = ShortDramaService(database_path=service.repository.database_path, workspace_root=service.workspace_root)
    store = reopened.production_manager.runtime.store; loaded = store.get_work_order(work.id); assert loaded
    assert loaded.specialist_turns[0] == turn
    original = loaded.model_dump(mode='json')
    rows, _ = observed_media_handoffs(store, run_id=run.id, project_id=pid, token_budget=0)
    assert len(rows) == 2
    for raw in rows:
        row = cast(dict[str, JsonValue], raw)
        assert row['tool_name'] == 'artifact.add_reference_entries' and row['attempt_id'] == turn.id
        marker = cast(dict[str, JsonValue], row['saved_observation'])
        assert store.get_context_snapshot(str(marker['context_ref']), run.id) == {'arguments': args, 'result': turn.result}
    unchanged = store.get_work_order(work.id); assert unchanged and unchanged.model_dump(mode='json') == original
    client = ScriptedClient([json.dumps({'tool_name':'artifact.read_current','arguments':{'project_id':pid},'reason':'Continue'})])
    worker = LanguageSpecialistAgent(client, reopened.tool_registry); worker.context_compiler = ModelInputCompiler(store)
    compiled = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(ProductionSkillRole.ASSET_BUILDER,))
    assert 'artifact.add_reference_entries' in compiled.text
    worker.execute(AgentWorkOrderView(work_order=loaded, context=ContextPack(run_id=run.id, project_id=pid, objective=work.objective), system_prompt=compiled.text))
    state = cast(dict[str, JsonValue], client.payloads[0]['working_set'])
    packs = [e for e in cast(list[dict[str, JsonValue]], state['entities']) if e['kind']=='reference_pack']
    assert len(packs)==1 and packs[0]['state']==saved.pack.model_dump(mode='json')
    catalog = cast(list[dict[str, JsonValue]], client.payloads[0]['available_tools'])
    assert any(t['id']=='artifact.add_reference_entries' for t in catalog)
