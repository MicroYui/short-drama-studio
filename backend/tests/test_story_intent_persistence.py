from __future__ import annotations

from short_drama.models import (
    ContentPlanningOutput,
    CreateProjectRequest,
    PlanningOutlineSnapshot,
    PlanningRunRecord,
    PlanningRunStatus,
    PlanningRunStep,
    ProjectDocument,
    SourceMode,
    StoryIntent,
    StoryIssueLedgerEntry,
    StorySourceContract,
)
from short_drama.production_planner import (
    _fallback_episode_outline,
    build_project_document_from_plan,
    create_production_plan,
)
from short_drama.script_documents import (
    PlanningScriptDocumentKind,
    build_episode_manifest,
    build_planning_script_documents,
)


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="数学退化短剧",
        source_mode=SourceMode.IDEA_EXPANSION,
        content_planning_output=ContentPlanningOutput.SCRIPT_ONLY,
        target_duration_seconds=180,
        input_text="所有人的数学水平倒退一百倍，只有主角不变。",
    )


def _story_intent() -> StoryIntent:
    return StoryIntent(
        audience_experience="让观众快速看懂主角的唯一优势，并持续获得公开翻盘的爽感。",
        dramatic_engine="把昨天普通的计算能力变成今天可见的稀缺能力，用不同场景放大它的后果。",
        progression_strategy="从身边人的惊讶扩大到权威人物争抢，每次放大都改变主角的地位或选择权。",
        tone_and_plausibility="校园荒诞爽剧；允许夸张反应和戏剧化压缩，但核心设定与行动结果要立即可读。",
        protected_elements=["日常能力瞬间变成神迹的反差", "主角松弛而围观者失态"],
        drift_risks=["不要漂移成程序审计剧", "不要只重复算术题"],
    )


def _source_contract() -> StorySourceContract:
    return StorySourceContract(
        explicit_commitments=["三分钟左右的 AI 爽文短剧", "所有人数学退化一百倍，只有主角不变"],
        explicit_prohibitions=["不写成过度理性的程序剧"],
        open_creative_choices=["角色、场景与具体升级方式由 Agent 决定"],
    )


def test_story_intent_and_source_contract_become_script_version_truth() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    intent = _story_intent()
    contract = _source_contract()
    planning.plan.story_intent = intent

    document = build_project_document_from_plan(
        project_id="project-story-intent",
        request=request,
        planning=planning,
        created_at="2026-09-03T00:00:00Z",
        source_contract=contract,
    )

    script = document.script_versions[0]
    assert script.story_intent == intent
    assert script.story_intent is not intent
    assert script.source_contract == contract
    assert script.source_contract is not contract

    restarted = ProjectDocument.model_validate_json(document.model_dump_json())
    assert restarted.script_versions[0].story_intent == intent
    assert restarted.script_versions[0].source_contract == contract


def test_old_project_documents_remain_readable_without_story_intent() -> None:
    request = _request()
    document = build_project_document_from_plan(
        project_id="project-old-story-intent",
        request=request,
        planning=create_production_plan(request, None),
        created_at="2026-09-03T00:00:00Z",
    )
    payload = document.model_dump(mode="json")
    script_payload = payload["script_versions"][0]
    assert isinstance(script_payload, dict)
    script_payload.pop("story_intent")
    script_payload.pop("source_contract")

    restored = ProjectDocument.model_validate(payload)

    assert restored.script_versions[0].story_intent is None
    assert restored.script_versions[0].source_contract is None


def test_story_intent_is_available_to_planning_retrieval_documents() -> None:
    outline = _fallback_episode_outline(_request())
    outline.story_intent = _story_intent()

    manifest = build_episode_manifest(outline)
    documents = build_planning_script_documents(
        run_id="planning-run-story-intent",
        revision_round=0,
        outline=outline,
        created_at="2026-09-03T00:00:00Z",
    )
    bible = next(
        document
        for document in documents
        if document.kind == PlanningScriptDocumentKind.STORY_BIBLE
    )

    assert manifest["story_intent"] == outline.story_intent.model_dump(mode="json")
    assert "观众体验：让观众快速看懂主角的唯一优势" in bible.body
    assert "戏剧引擎：把昨天普通的计算能力" in bible.body
    assert "程序审计剧" in bible.search_metadata


def test_planning_trace_persists_story_intent_and_narrative_skill_choices() -> None:
    request = _request()
    intent = _story_intent()
    contract = _source_contract()
    run = PlanningRunRecord(
        id="planning-run-story-intent",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.STORY_ARCHITECT,
        request=request,
        source_contract=contract,
        story_intent=intent,
        selected_narrative_skill_ids=[
            "narrative.payoff-escalation",
            "narrative.absurd-premise",
        ],
        skill_selection_reasons=[
            "用公开反应与地位变化兑现爽感。",
            "用荒诞扩大保留设定的喜剧性。",
        ],
        issue_ledger=[
            StoryIssueLedgerEntry(
                issue_id="story-focus",
                semantic_cluster="核心设定与观众回报",
                state="接受类型化压缩后交给最终冷读",
                observation="中段不应只剩下程序说明。",
                text_evidence=["主角在公开场景展示数学优势。"],
                fix_evidence=["修订后权威人物为争取主角当场改变待遇。"],
                evolution_notes=["从抽象节奏问题收敛为可见地位变化。"],
                accepted_tradeoff="接受校长快速到场的戏剧化压缩。",
                handoff="最终冷读检查这个压缩是否仍然清楚。",
            )
        ],
        project_id="project-story-intent",
        owner_agent_run_id="agent-run-story-intent",
        created_at="2026-09-03T00:00:00Z",
        updated_at="2026-09-03T00:00:00Z",
    )
    snapshot = PlanningOutlineSnapshot(
        revision_round=0,
        outline_json=_fallback_episode_outline(request).model_dump_json(),
        source="Story Architect",
        source_contract=contract,
        story_intent=intent,
        created_at="2026-09-03T00:00:00Z",
    )

    restored_run = PlanningRunRecord.model_validate_json(run.model_dump_json())
    restored_snapshot = PlanningOutlineSnapshot.model_validate_json(
        snapshot.model_dump_json()
    )

    assert restored_run.story_intent == intent
    assert restored_run.selected_narrative_skill_ids == [
        "narrative.payoff-escalation",
        "narrative.absurd-premise",
    ]
    assert restored_run.skill_selection_reasons[0].startswith("用公开反应")
    assert restored_run.issue_ledger[0].semantic_cluster == "核心设定与观众回报"
    assert restored_run.issue_ledger[0].accepted_tradeoff == (
        "接受校长快速到场的戏剧化压缩。"
    )
    assert restored_snapshot.story_intent == intent


def test_old_planning_records_default_new_story_fields() -> None:
    run_payload = PlanningRunRecord(
        id="planning-run-old",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.STORY_ARCHITECT,
        request=_request(),
        project_id="project-old",
        owner_agent_run_id="agent-run-old",
        created_at="2026-09-03T00:00:00Z",
        updated_at="2026-09-03T00:00:00Z",
    ).model_dump(mode="json")
    run_payload.pop("story_intent")
    run_payload.pop("selected_narrative_skill_ids")
    run_payload.pop("skill_selection_reasons")
    run_payload.pop("issue_ledger")

    restored = PlanningRunRecord.model_validate(run_payload)

    assert restored.story_intent is None
    assert restored.selected_narrative_skill_ids == []
    assert restored.skill_selection_reasons == []
    assert restored.issue_ledger == []
