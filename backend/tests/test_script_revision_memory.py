"""Regression coverage for versioned screenplay follow-up edits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from short_drama.models import (
    ApprovalStatus,
    AudioKind,
    ContentPlanningOutput,
    ContinuationPreference,
    CreateProjectRequest,
    DurationAuthority,
    DurationContract,
    DurationFidelityPolicy,
    DurationMismatchResolution,
    DurationTargetOrigin,
    EndingContract,
    Freshness,
    PlanningRunOperation,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanningRunStep,
    ResolvedEndingMode,
    ReviseScriptLinesRequest,
    ScriptLine,
    ScriptLineRevision,
    ScriptRevisionOrigin,
    SourceMode,
    StoryCharacterMetadataPatch,
    StoryIntent,
    StoryLockedDialogueLine,
    StoryMetadataRevisionPatch,
    StoryMetadataTarget,
    StoryRepairAction,
    StoryRepairDiagnosis,
    StoryRepairPlan,
    StoryRevisionVerification,
    StoryRevisionViolation,
    StoryVerificationResolutionDecision,
    StructuredScene,
)
from short_drama.hierarchical_script import compose_scene_screenplay
from short_drama.production_planner import EpisodeOutline, PlannedDialogueLine
from short_drama.script_documents import EpisodeScenePatch
from short_drama.script_text import synchronize_script_line_text
from short_drama.tools import ToolCallStatus
from tests.test_planning_runs import (
    CriticRecommendedFinalColdReadClient as _BaseCriticRecommendedFinalColdReadClient,
    StagedLanguageClient as _BaseStagedLanguageClient,
    _finish,
    _aligned_duration_fit_assessment,
    _request,
    _service,
    _start_owned_planning,
)


def _test_duration_contract(request: CreateProjectRequest) -> DurationContract:
    """Mirror the production fallback for legacy staged test responses."""

    target_origin = (
        DurationTargetOrigin.USER_DECLARED
        if request.target_duration_seconds is not None
        else DurationTargetOrigin.PROFILE_DEFAULT
    )
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return DurationContract(
            target_seconds=request.resolved_target_duration_seconds(),
            target_origin=target_origin,
            authority=DurationAuthority.SOURCE_LED,
            acceptable_range_seconds=None,
            source_fidelity_policy=DurationFidelityPolicy.PRESERVE_VERBATIM,
            mismatch_resolution=DurationMismatchResolution.REPORT_NATURAL_RUNTIME,
            decision_basis=(
                "测试客户端保留 SCRIPT_LOCKED 来源内容，"
                "并将请求时长作为对照。"
            ),
            rhythm_shape=[],
        )
    return DurationContract(
        target_seconds=request.resolved_target_duration_seconds(),
        target_origin=target_origin,
        authority=DurationAuthority.TARGET_LED,
        acceptable_range_seconds=None,
        source_fidelity_policy=(
            DurationFidelityPolicy.REWRITABLE
            if request.source_mode == SourceMode.AUTO_ORIGINAL
            else DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
        ),
        mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
        decision_basis="测试客户端沿用请求中的原创规模，不虚构可接受容差。",
        rhythm_shape=[],
    )


def _inject_duration_contract(
    output: str,
    request: CreateProjectRequest,
) -> str:
    """Upgrade only the old Story Orientation fixture payload."""

    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return output
    if not isinstance(payload, dict):
        return output
    story_intent = payload.get("story_intent")
    if not isinstance(story_intent, dict):
        return output
    if story_intent.get("duration_contract") is not None:
        return output
    story_intent["duration_contract"] = _test_duration_contract(request).model_dump(
        mode="json"
    )
    return json.dumps(payload, ensure_ascii=False)


class StagedLanguageClient(_BaseStagedLanguageClient):
    """Keep this regression file compatible with the current orientation schema."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        return _inject_duration_contract(output, self.request)


class CriticRecommendedFinalColdReadClient(
    _BaseCriticRecommendedFinalColdReadClient
):
    """Apply the same Story Orientation compatibility to this specialized fake."""

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        output = super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )
        return _inject_duration_contract(output, self.request)


class AgentSelectedLocalRevisionClient(StagedLanguageClient):
    """Let the Leader choose one coherent scene, then reuse the scene Writer."""

    selected_scene_sequence = 2

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issue = payload["routed_story_review"]["issues"][0]
            return StoryRepairPlan(
                summary="这次意见只需改第二场，其他场次继续成立。",
                source_promise="保留用户原始设定与当前已成立的因果链。",
                invariants=["不改变其他场次的文本和顺序"],
                actions=[
                    StoryRepairAction(
                        issue_index=1,
                        issue_id=str(issue["issue_id"]),
                        diagnosis=StoryRepairDiagnosis.USER_REQUEST,
                        should_revise=True,
                        rationale="用户意见可以在第二场内完整落实。",
                        affected_scene_sequences=[self.selected_scene_sequence],
                        recommended_change="强化第二场的关键台词与现场反应。",
                        preserve=["其他场次", "原始用户承诺"],
                        validation_questions=["是否只有第二场发生变化？"],
                    )
                ],
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class AgentSelectedStructuralRevisionClient(StagedLanguageClient):
    """Let the Leader return a structural request to the Story Architect."""

    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request)
        self.structural_architect_inputs: list[dict[str, object]] = []

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issue = payload["routed_story_review"]["issues"][0]
            return StoryRepairPlan(
                summary="用户要求改变整集场次结构，需要返回 Story Architect。",
                source_promise="保留原始主角、核心设定与本集回报。",
                invariants=["不把本轮修订当成从零创作"],
                actions=[
                    StoryRepairAction(
                        issue_index=1,
                        issue_id=str(issue["issue_id"]),
                        diagnosis=StoryRepairDiagnosis.USER_REQUEST,
                        should_revise=True,
                        rationale="需要删除两场、新增一场并重排剩余场次。",
                        affected_scene_sequences=[],
                        recommended_change="重构场次蓝图后再逐场写作。",
                        preserve=["原始用户承诺", "已定的主角与地点资产"],
                        validation_questions=["新场次顺序是否形成完整因果？"],
                    )
                ],
            ).model_dump_json()
        if "story-architect Agent" in instructions:
            payload = json.loads(input_text)
            revision_instruction = payload.get("latest_user_revision_instruction")
            if revision_instruction is not None:
                self.story_architect_instructions.append(instructions)
                self.structural_architect_inputs.append(payload)
                current = EpisodeOutline.model_validate(
                    payload["current_complete_outline"]
                )
                first = current.scenes[1].model_copy(
                    deep=True,
                    update={
                        "sequence": 1,
                        "summary": "原第二场前移，直接承接修订后的开场。",
                        "narrative_purpose": "先展示关键结果，加快整集启动。",
                        "target_duration_seconds": 40.0,
                        "dialogue_line_indices": [1],
                        "screenplay_text": "",
                    },
                )
                inserted = current.scenes[2].model_copy(
                    deep=True,
                    update={
                        "sequence": 2,
                        "heading": "测试控制室·新增公开挑战",
                        "summary": "新的公开挑战迫使主角当场做出选择。",
                        "narrative_purpose": "用新场景事件连接前移的结果与结尾回收。",
                        "playable_actions": ["公开挑战", "主角做出选择"],
                        "target_duration_seconds": 40.0,
                        "dialogue_line_indices": [2],
                        "screenplay_text": "",
                    },
                )
                last = current.scenes[3].model_copy(
                    deep=True,
                    update={
                        "sequence": 3,
                        "summary": "原结尾场前移，直接回收新挑战的后果。",
                        "narrative_purpose": "完成本集回报。",
                        "target_duration_seconds": 40.0,
                        "dialogue_line_indices": [3],
                        "screenplay_text": "",
                    },
                )
                full_outline = current.model_copy(
                    deep=True,
                    update={
                        "title": "结构性修订后的故事",
                        "story_summary": "开场结果、新挑战与结尾回报组成新的三场结构。",
                        "lines": [
                            PlannedDialogueLine(
                                speaker="林夏",
                                text="我先公布第二段证据。",
                            ),
                            PlannedDialogueLine(
                                speaker="周衡",
                                text="那就现在公开验证。",
                            ),
                            PlannedDialogueLine(
                                speaker="林夏",
                                text="三段证据已经闭合。",
                            ),
                        ],
                        "scenes": [first, inserted, last],
                    },
                )
                blueprint_payload = full_outline.model_dump(mode="python")
                blueprint_payload["lines"] = []
                for scene in blueprint_payload["scenes"]:
                    scene["dialogue_line_indices"] = []
                    scene["screenplay_text"] = ""
                self.outline = full_outline
                self.blueprint = EpisodeOutline.model_validate(blueprint_payload)
                return self.blueprint.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class MetadataAwareRevisionClient(StagedLanguageClient):
    """Exercise typed global-metadata work with or without scene patches."""

    def __init__(
        self,
        request: CreateProjectRequest,
        *,
        selected_scene_sequences: list[int],
        revise_project_bible: bool = False,
    ) -> None:
        super().__init__(request)
        self.selected_scene_sequences = list(selected_scene_sequences)
        self.revise_project_bible = revise_project_bible
        self.revised_story_intent = StoryIntent(
            audience_experience=(
                "观众知道世界数学能力发生异常，剧中人物只看到林一"
                "能当场算对，并把他视为算术天才。"
            ),
            dramatic_engine=(
                "林一以可现场核验的计算结果不断放大众人震惊与学校回报。"
            ),
            progression_strategy=(
                "从食堂十位数加法的即时验证，递进到校长留人与高考兑现。"
            ),
            tone_and_plausibility=(
                "保持纯爽剧节奏；人物不得知全球数学能力下降的设定。"
            ),
            protected_elements=["高中生主角", "食堂开场", "校长留人", "高考回报"],
            drift_risks=[
                "不得让角色说出数学能力退化或林一未退化。"
            ],
        )
        self.revised_ending_contract = EndingContract(
            requested_preference=ContinuationPreference.AGENT_DECIDES,
            resolved_mode=ResolvedEndingMode.STANDALONE,
            episode_payoff=(
                "林一因能当场算对十位数加法被视为算术天才，"
                "得到学校支持并考入顶尖大学。"
            ),
            required_closures=[
                "食堂众人只通过可见计算结果确认林一的算术能力",
                "校长留人和高考录取完成回报",
            ],
            allowed_open_threads=[],
            continuation_hook=None,
            final_button="林一在食堂举起顶尖大学录取通知书。",
            decision_basis="用户要求完整纯爽故事，不保留续集钩子。",
        )
        self.revised_performance = "面对众人震惊保持平静，只展示计算能力"

    @property
    def metadata_targets(self) -> list[StoryMetadataTarget]:
        targets = [
            StoryMetadataTarget.STORY_INTENT,
            StoryMetadataTarget.ENDING_CONTRACT,
        ]
        if self.revise_project_bible:
            targets.append(StoryMetadataTarget.PROJECT_BIBLE)
        return targets

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "Leader 的返修分派步骤" in instructions:
            self.repair_plan_calls += 1
            payload = json.loads(input_text)
            self.repair_plan_inputs.append(payload)
            issue = payload["routed_story_review"]["issues"][0]
            return StoryRepairPlan(
                summary="对齐人物认知边界，只修改确有问题的场次与全局故事契约。",
                source_promise="保留食堂开场、校长留人和高考回报。",
                invariants=["未选中场次不改写", "角色只对林一算对本身震惊"],
                actions=[
                    StoryRepairAction(
                        issue_index=1,
                        issue_id=str(issue["issue_id"]),
                        diagnosis=StoryRepairDiagnosis.USER_REQUEST,
                        should_revise=True,
                        rationale=(
                            "用户修正了剧中人物的知识边界，需要同步可读文本"
                            "和后续会复用的故事契约。"
                        ),
                        affected_scene_sequences=list(self.selected_scene_sequences),
                        metadata_targets=self.metadata_targets,
                        recommended_change=(
                            "删除角色对退化设定的自知，改为他们只赞叹林一"
                            "会算十位数加法。"
                        ),
                        preserve=["未选中场次", "已成立的结尾回报"],
                        validation_questions=[
                            "角色是否只对可见计算结果做出反应？"
                        ],
                    )
                ],
            ).model_dump_json()
        if "story-metadata revision Agent" in instructions:
            self.metadata_revision_calls += 1
            payload = json.loads(input_text)
            self.metadata_revision_inputs.append(payload)
            character_patches: list[StoryCharacterMetadataPatch] = []
            if self.revise_project_bible:
                character_id = payload["current_global_metadata"]["characters"][0][
                    "character_id"
                ]
                character_patches.append(
                    StoryCharacterMetadataPatch(
                        character_id=character_id,
                        performance=self.revised_performance,
                    )
                )
            return StoryMetadataRevisionPatch(
                story_intent=self.revised_story_intent,
                ending_contract=self.revised_ending_contract,
                character_patches=character_patches,
            ).model_dump_json()
        if "scene-local screenplay revision Agent" in instructions:
            payload = json.loads(input_text)
            self.local_revision_inputs.append(payload)
            patch = EpisodeScenePatch.model_validate(payload["current_scene"])
            self.local_revision_calls.append(patch.scene_sequence)
            patch.lines[0].text = (
                "林一会算十位数加法！"
                if patch.scene_sequence == 1
                else "他是我们学校的算术天才！"
            )
            patch.screenplay_text = compose_scene_screenplay(
                heading=patch.heading,
                playable_actions=patch.playable_actions,
                lines=patch.lines,
            )
            return patch.model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class DurationContractMetadataRevisionClient(MetadataAwareRevisionClient):
    """Revise only StoryIntent while changing its authoritative target."""

    revised_target_seconds = 90.0

    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request, selected_scene_sequences=[])
        self.revised_story_intent = self.revised_story_intent.model_copy(
            deep=True,
            update={
                "duration_contract": DurationContract(
                    target_seconds=self.revised_target_seconds,
                    target_origin=DurationTargetOrigin.USER_DECLARED,
                    authority=DurationAuthority.TARGET_LED,
                    acceptable_range_seconds=None,
                    source_fidelity_policy=(
                        DurationFidelityPolicy.PRESERVE_STORY_COMMITMENTS
                    ),
                    mismatch_resolution=DurationMismatchResolution.REVISE_SCRIPT,
                    decision_basis=(
                        "The user explicitly revised the episode to 90 seconds."
                    ),
                )
            },
        )

    @property
    def metadata_targets(self) -> list[StoryMetadataTarget]:
        return [StoryMetadataTarget.STORY_INTENT]

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-metadata revision Agent" in instructions:
            self.metadata_revision_calls += 1
            self.metadata_revision_inputs.append(json.loads(input_text))
            return StoryMetadataRevisionPatch(
                story_intent=self.revised_story_intent,
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class MetadataOnlyVerifierRetryClient(MetadataAwareRevisionClient):
    """Return one typed metadata-only verifier violation before acceptance."""

    def __init__(self, request: CreateProjectRequest) -> None:
        super().__init__(request, selected_scene_sequences=[])
        self.intermediate_ending_contract = self.revised_ending_contract.model_copy(
            deep=True,
            update={
                "episode_payoff": (
                    "林一证明自己是唯一数学能力未退化的人。"
                ),
                "required_closures": ["全校确认林一的数学能力未退化"],
            },
        )

    @property
    def metadata_targets(self) -> list[StoryMetadataTarget]:
        return [StoryMetadataTarget.ENDING_CONTRACT]

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-metadata revision Agent" in instructions:
            self.metadata_revision_calls += 1
            payload = json.loads(input_text)
            self.metadata_revision_inputs.append(payload)
            ending_contract = (
                self.intermediate_ending_contract
                if self.metadata_revision_calls == 1
                else self.revised_ending_contract
            )
            return StoryMetadataRevisionPatch(
                ending_contract=ending_contract,
            ).model_dump_json()
        if "story-revision verifier Agent" in instructions:
            self.revision_verifier_calls += 1
            payload = json.loads(input_text)
            self.revision_verifier_inputs.append(payload)
            action = payload["story_repair_plan"]["actions"][0]
            if self.revision_verifier_calls == 1:
                return StoryRevisionVerification(
                    accepted=False,
                    summary="EndingContract 仍越过了剧中人物的知识边界。",
                    satisfied_issue_ids=[],
                    satisfied_issue_indices=[],
                    violations=[
                        StoryRevisionViolation(
                            issue_id=str(action["issue_id"]),
                            category="ENDING_CONTRACT_ALIGNMENT",
                            scene_sequences=[],
                            metadata_targets=[StoryMetadataTarget.ENDING_CONTRACT],
                            observation=(
                                "契约仍声称角色确认林一未退化，"
                                "与用户最新修改意见冲突。"
                            ),
                            repair_instruction=(
                                "只修正 EndingContract，改为众人根据可见结果"
                                "把林一视为算术天才。"
                            ),
                        )
                    ],
                    duration_fit_assessment=_aligned_duration_fit_assessment(
                        self.request
                    ),
                ).model_dump_json()
            return StoryRevisionVerification(
                accepted=True,
                summary="EndingContract 已与用户意见和现有场次对齐。",
                satisfied_issue_ids=[str(action["issue_id"])],
                satisfied_issue_indices=[int(action["issue_index"])],
                violations=[],
                duration_fit_assessment=_aligned_duration_fit_assessment(
                    self.request
                ),
            ).model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


def test_script_text_sync_respects_line_order_when_dialogue_is_repeated() -> None:
    previous = [
        ScriptLine(
            id="line-1",
            speaker="同学甲",
            original_text="不会吧",
            spoken_text="不会吧",
            subtitle_text="不会吧",
            audio_kind=AudioKind.DIALOGUE,
        ),
        ScriptLine(
            id="line-2",
            speaker="同学乙",
            original_text="不会吧",
            spoken_text="不会吧",
            subtitle_text="不会吧",
            audio_kind=AudioKind.DIALOGUE,
        ),
    ]
    revised = [
        previous[0].model_copy(deep=True),
        previous[1].model_copy(
            update={
                "spoken_text": "他居然心算出来了",
                "subtitle_text": "他居然心算出来了",
            }
        ),
    ]
    scenes, screenplay = synchronize_script_line_text(
        previous_lines=previous,
        revised_lines=revised,
        scenes=[
            StructuredScene(
                id="scene-1",
                heading="食堂",
                summary="两名同学先后震惊。",
                line_ids=["line-1", "line-2"],
                screenplay_text="同学甲：不会吧\n同学乙：不会吧",
            )
        ],
    )

    assert scenes[0].screenplay_text == "同学甲：不会吧\n同学乙：他居然心算出来了"
    assert screenplay == scenes[0].screenplay_text


def test_revise_script_lines_synchronizes_readable_text_and_clears_old_review(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "写一个发生在学校食堂的高中生爽剧。",
        }
    )
    language = CriticRecommendedFinalColdReadClient(request)
    service = _service(tmp_path, language)
    completed = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert completed.project is not None
    persisted = service.repository.get_project(
        completed.project.document.project.id
    )
    assert persisted is not None
    current = persisted.script_versions[-1]
    assert current.review_report is not None
    assert current.review_disposition is not None
    current.final_cold_read_report = current.review_report.model_copy(deep=True)
    service.repository.save_project(persisted)
    selected_line = current.lines[0]
    selected_scene = next(
        scene
        for scene in current.structured_scenes
        if selected_line.id in scene.line_ids
    )
    revised_spoken_text = "改稿后的台词只保留这一句，现场立刻安静下来。"
    expected_scene_text = selected_scene.screenplay_text.replace(
        selected_line.spoken_text,
        revised_spoken_text,
        1,
    )

    revised = service.revise_script_lines(
        current.project_id,
        ReviseScriptLinesRequest(
            revisions=[
                ScriptLineRevision(
                    line_id=selected_line.id,
                    spoken_text=revised_spoken_text,
                    subtitle_text=revised_spoken_text,
                )
            ],
            change_reason="根据用户反馈修改一句关键台词",
        ),
    )

    latest = revised.document.script_versions[-1]
    latest_scene = next(
        scene
        for scene in latest.structured_scenes
        if selected_line.id in scene.line_ids
    )
    assert latest_scene.screenplay_text == expected_scene_text
    assert latest.screenplay_text == "\n\n".join(
        scene.screenplay_text
        for scene in latest.structured_scenes
        if scene.screenplay_text.strip()
    )
    assert revised_spoken_text in latest.screenplay_text
    assert latest.review_report is None
    assert latest.review_disposition is None
    assert latest.final_cold_read_report is None


def test_planning_start_script_revision_inherits_current_project_truth(
    tmp_path: Path,
) -> None:
    original_brief = (
        "所有人的数学水平下降一百倍，只有男高中生主角不变；"
        "故事从学校食堂开始，场景集中，节奏要快。"
    )
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": original_brief,
        }
    )
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)
    completed = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert completed.project is not None
    persisted = service.repository.get_project(completed.project.document.project.id)
    assert persisted is not None
    current = persisted.script_versions[-1]
    assert current.source_contract is not None
    prior_overrides = [f"历史修改意见 {index}" for index in range(1, 26)]
    current.source_contract.authoritative_user_overrides = prior_overrides
    service.repository.save_project(persisted)
    feedback = "保留原设定，把第二场改得更荒诞，并让第三场紧接它的后果。"
    result = service.tool_registry.invoke(
        "planning.start_script_revision",
        {
            "project_id": current.project_id,
            "owner_agent_run_id": "agent-run-follow-up",
            "instruction": feedback,
            "base_script_version_id": current.id,
        },
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    response = PlanningRunResponse.model_validate(result.output)
    run = response.run
    assert run.operation == PlanningRunOperation.SCRIPT_REVISION
    assert run.request.input_text == original_brief
    assert run.revision_instruction == feedback
    assert run.revision_origin == ScriptRevisionOrigin.USER_FEEDBACK
    assert run.revision_evidence_refs == []
    assert run.base_script_version_id == current.id
    assert run.parent_planning_run_id == current.planning_run_id
    assert run.source_contract is not None
    assert (
        run.source_contract.explicit_commitments
        == current.source_contract.explicit_commitments
    )
    assert run.source_contract.authoritative_user_overrides[:-1] == prior_overrides
    assert run.source_contract.authoritative_user_overrides[-1] == feedback
    assert run.outline_json is not None
    inherited_outline = EpisodeOutline.model_validate_json(run.outline_json)
    assert [scene.screenplay_text for scene in inherited_outline.scenes] == [
        scene.screenplay_text for scene in current.structured_scenes
    ]
    assert [line.text for line in inherited_outline.lines] == [
        line.spoken_text for line in current.lines
    ]
    assert response.project is not None
    assert response.project.document.planning is not None
    assert response.project.document.planning.run_id == run.id


def test_script_revision_without_scene_duration_envelope_returns_to_architect(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={"content_planning_output": ContentPlanningOutput.SCRIPT_ONLY}
    )
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)
    completed = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )
    assert completed.project is not None
    persisted = service.repository.get_project(
        completed.project.document.project.id
    )
    assert persisted is not None
    current = next(
        item
        for item in persisted.script_versions
        if item.id == persisted.project.current_script_version_id
    )
    original_screenplay = current.screenplay_text
    current.planning_run_id = None
    current.structured_scenes = [
        scene.model_copy(
            deep=True,
            update={"target_duration_seconds": None},
        )
        for scene in current.structured_scenes
    ]
    service.repository.save_project(persisted)

    started = service.start_script_revision(
        current.project_id,
        owner_agent_run_id="agent-run-rebuild-duration-envelope",
        instruction="保留当前剧情，按完整表演语义重建场次时长包络。",
        base_script_version_id=current.id,
    )

    assert started.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert started.run.outline_json is None
    assert started.run.step_retry_feedback is not None
    handoff = json.loads(started.run.step_retry_feedback)
    assert handoff["requested_by"] == "SCRIPT_REVISION_DURATION_ENVELOPE"
    assert handoff["current_script"]["screenplay_text"] == original_screenplay
    assert all(
        scene["target_duration_seconds"] is None
        for scene in handoff["current_script"]["structured_scenes"]
    )
    assert "do not divide a target evenly across scenes" in handoff["reason"]

    rearchitected = service.advance_planning_run(started.run.id)
    assert rearchitected.run.current_step == PlanningRunStep.SCENE_WRITER
    assert rearchitected.run.outline_json is not None
    rebuilt = EpisodeOutline.model_validate_json(rearchitected.run.outline_json)
    assert all(scene.target_duration_seconds > 0 for scene in rebuilt.scenes)


def test_agent_selected_local_revision_only_changes_one_scene_and_appends_version(
    tmp_path: Path,
) -> None:
    original_brief = "保留高中生主角和原有因果，整集节奏紧凑。"
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": original_brief,
        }
    )
    language = AgentSelectedLocalRevisionClient(request)
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1].model_copy(deep=True)
    original_bible_ids = [
        version.id for version in initial.project.document.bible_versions
    ]
    feedback = "只修改第二场：让主角的台词更有压迫感，其他场不动。"
    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-local-revision",
        instruction=feedback,
        base_script_version_id=original.id,
    )

    assert started.run.current_step == PlanningRunStep.STORY_REVISION
    completed = _finish(service, started.run.id, max_steps=20)

    assert completed.project is not None
    versions = completed.project.document.script_versions
    assert len(versions) == 2
    previous, latest = versions
    assert previous.id == original.id
    assert previous.approval_status == ApprovalStatus.SUPERSEDED
    assert previous.freshness == Freshness.STALE
    assert latest.version == previous.version + 1
    assert latest.parent_version_id == previous.id
    assert latest.planning_run_id == started.run.id
    assert latest.change_reason == feedback
    assert latest.original_text == previous.original_text
    assert latest.approval_status == ApprovalStatus.APPROVED
    assert latest.freshness == Freshness.CURRENT
    assert completed.project.document.project.current_script_version_id == latest.id
    assert [version.id for version in completed.project.document.bible_versions] == (
        original_bible_ids
    )

    selected_index = language.selected_scene_sequence - 1
    assert len(latest.structured_scenes) == len(previous.structured_scenes)
    for index, (old_scene, new_scene) in enumerate(
        zip(previous.structured_scenes, latest.structured_scenes, strict=True)
    ):
        assert new_scene.id == old_scene.id
        assert new_scene.line_ids == old_scene.line_ids
        if index == selected_index:
            assert new_scene.screenplay_text != old_scene.screenplay_text
            assert "（局部改稿）" in new_scene.screenplay_text
        else:
            assert new_scene == old_scene

    assert language.local_revision_calls == [language.selected_scene_sequence]
    assert language.revision_calls == 0
    assert language.revision_verifier_calls == 1
    assert language.repair_plan_inputs[0]["source_contract"]["source_text"] == (
        original_brief
    )
    explicit_contract = language.repair_plan_inputs[0][
        "explicit_story_source_contract"
    ]
    assert explicit_contract["authoritative_user_overrides"][-1] == feedback
    routed_issue = language.repair_plan_inputs[0]["routed_story_review"]["issues"][0]
    assert routed_issue["observation"] == feedback
    writer_instruction = json.loads(
        language.local_revision_inputs[0]["user_instruction"]
    )
    assert writer_instruction["latest_user_revision_instruction"] == feedback

    followup_feedback = "继续保留上一轮结果，只把结尾一句改得更克制。"
    followup = service.start_script_revision(
        latest.project_id,
        owner_agent_run_id="agent-run-second-follow-up",
        instruction=followup_feedback,
        base_script_version_id=latest.id,
    )
    assert followup.run.request.input_text == original_brief
    assert followup.run.parent_planning_run_id == started.run.id
    assert followup.run.source_contract is not None
    assert followup.run.source_contract.authoritative_user_overrides[-2:] == [
        feedback,
        followup_feedback,
    ]
    assert followup.run.outline_json is not None
    inherited_latest = EpisodeOutline.model_validate_json(followup.run.outline_json)
    assert [scene.screenplay_text for scene in inherited_latest.scenes] == [
        scene.screenplay_text for scene in latest.structured_scenes
    ]


@pytest.mark.parametrize(
    "revise_project_bible",
    [False, True],
    ids=["story-contracts-only", "with-character-metadata"],
)
def test_scene_and_metadata_revision_updates_only_selected_story_state(
    tmp_path: Path,
    revise_project_bible: bool,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "写一个从学校食堂开始的高中生纯爽短剧。",
        }
    )
    language = MetadataAwareRevisionClient(
        request,
        selected_scene_sequences=[1, 2],
        revise_project_bible=revise_project_bible,
    )
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1].model_copy(deep=True)
    original_bibles = [
        version.model_copy(deep=True)
        for version in initial.project.document.bible_versions
    ]
    initial_architect_calls = len(language.story_architect_instructions)
    initial_scene_writer_calls = len(language.script_scene_calls)
    feedback = (
        "剧中人物不知道数学能力下降，只会因林一算对而震惊；"
        "改成林一会算十位数加法。"
    )
    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-scene-and-metadata-revision",
        instruction=feedback,
        base_script_version_id=original.id,
    )

    routed = service.advance_planning_run(started.run.id)

    assert routed.run.current_step == PlanningRunStep.STORY_METADATA_REVISION
    assert routed.run.revision_scene_sequences == [1, 2]
    assert routed.run.latest_repair_plan is not None
    assert routed.run.latest_repair_plan.actions[0].metadata_targets == (
        language.metadata_targets
    )
    assert language.metadata_revision_calls == 0
    assert language.local_revision_calls == []

    aligned = service.advance_planning_run(started.run.id)

    assert aligned.run.current_step == PlanningRunStep.SCENE_REVISION
    assert aligned.run.story_intent == language.revised_story_intent
    assert language.metadata_revision_calls == 1
    assert language.revision_scope_calls == 0
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls
    metadata_input = language.metadata_revision_inputs[0]
    assert metadata_input["requested_metadata_targets"] == sorted(
        target.value for target in language.metadata_targets
    )
    assert metadata_input["current_global_metadata"]["ending_contract"] == (
        original.ending_contract.model_dump(mode="json")
        if original.ending_contract is not None
        else None
    )

    completed = _finish(service, started.run.id, max_steps=10)

    assert completed.project is not None
    previous, latest = completed.project.document.script_versions
    assert previous.id == original.id
    assert latest.parent_version_id == previous.id
    assert latest.story_intent == language.revised_story_intent
    assert latest.ending_contract == language.revised_ending_contract
    assert previous.story_intent != latest.story_intent
    assert previous.ending_contract != latest.ending_contract
    assert language.local_revision_calls == [1, 2]
    assert language.revision_calls == 0
    assert language.revision_verifier_calls == 1
    assert PlanningRunStep.STORY_ARCHITECT not in completed.run.completed_steps
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls
    assert "林一会算十位数加法" in latest.screenplay_text
    assert "数学没退化" not in latest.screenplay_text
    assert "数学未退化" not in latest.screenplay_text
    for index, (old_scene, new_scene) in enumerate(
        zip(previous.structured_scenes, latest.structured_scenes, strict=True),
        start=1,
    ):
        if index in {1, 2}:
            assert new_scene.screenplay_text != old_scene.screenplay_text
        else:
            assert new_scene == old_scene

    verifier_context = language.revision_verifier_inputs[0][
        "repair_verification_context"
    ]
    assert verifier_context["original_global_story_metadata"][
        "ending_contract"
    ] == (
        original.ending_contract.model_dump(mode="json")
        if original.ending_contract is not None
        else None
    )
    assert verifier_context["revised_global_story_metadata"][
        "story_intent"
    ] == language.revised_story_intent.model_dump(mode="json")
    assert verifier_context["revised_global_story_metadata"][
        "ending_contract"
    ] == language.revised_ending_contract.model_dump(mode="json")

    bible_versions = completed.project.document.bible_versions
    if revise_project_bible:
        assert len(bible_versions) == len(original_bibles) + 1
        old_bible = bible_versions[-2]
        new_bible = bible_versions[-1]
        assert old_bible.id == original_bibles[-1].id
        assert old_bible.freshness == Freshness.STALE
        assert new_bible.parent_version_id == old_bible.id
        assert new_bible.freshness == Freshness.CURRENT
        assert new_bible.characters[0].performance == language.revised_performance
        assert new_bible.locations == old_bible.locations
        assert new_bible.props == old_bible.props
        assert new_bible.style == old_bible.style
        assert completed.project.document.project.current_bible_version_id == (
            new_bible.id
        )
    else:
        assert [version.id for version in bible_versions] == [
            version.id for version in original_bibles
        ]
        assert completed.project.document.project.current_bible_version_id == (
            original_bibles[-1].id
        )


def test_metadata_only_revision_skips_story_architect_and_scene_writer(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "写一个完整的高中生纯爽短剧。",
        }
    )
    language = MetadataAwareRevisionClient(
        request,
        selected_scene_sequences=[],
    )
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1].model_copy(deep=True)
    original_bible_ids = [
        version.id for version in initial.project.document.bible_versions
    ]
    initial_architect_calls = len(language.story_architect_instructions)
    initial_scene_writer_calls = len(language.script_scene_calls)
    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-metadata-only-revision",
        instruction="只更正全局故事契约中的人物认知边界，现有场次文字不动。",
        base_script_version_id=original.id,
    )

    routed = service.advance_planning_run(started.run.id)

    assert routed.run.current_step == PlanningRunStep.STORY_METADATA_REVISION
    assert routed.run.revision_scene_sequences == []
    assert language.metadata_revision_calls == 0

    aligned = service.advance_planning_run(started.run.id)

    assert aligned.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert aligned.run.revision_scene_sequences == []
    assert aligned.run.story_intent == language.revised_story_intent
    assert language.metadata_revision_calls == 1
    assert language.local_revision_calls == []
    assert language.revision_scope_calls == 0
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls

    verified = service.advance_planning_run(started.run.id)

    assert verified.run.current_step == PlanningRunStep.FINALIZE
    assert language.revision_verifier_calls == 1
    completed = _finish(service, started.run.id, max_steps=3)

    assert completed.project is not None
    previous, latest = completed.project.document.script_versions
    assert latest.parent_version_id == previous.id
    assert latest.story_intent == language.revised_story_intent
    assert latest.ending_contract == language.revised_ending_contract
    assert latest.structured_scenes == previous.structured_scenes
    assert latest.screenplay_text == previous.screenplay_text
    assert [
        version.id for version in completed.project.document.bible_versions
    ] == original_bible_ids
    assert language.local_revision_calls == []
    assert language.revision_calls == 0
    assert PlanningRunStep.STORY_ARCHITECT not in completed.run.completed_steps
    assert PlanningRunStep.SCENE_WRITER not in completed.run.completed_steps
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls


def test_story_intent_duration_revision_updates_project_truth_and_survives_followup(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "写一个完整的高中生纯爽短剧。",
        }
    )
    language = DurationContractMetadataRevisionClient(request)
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1]
    assert original.story_intent is not None
    assert original.story_intent.duration_contract is not None
    assert original.story_intent.duration_contract.target_seconds == 120.0
    assert initial.project.document.project.target_duration_seconds == 120.0

    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-duration-contract-revision",
        instruction="把本集改成约90秒，保留当前场次文字，更新全局时长契约。",
        base_script_version_id=original.id,
    )
    routed = service.advance_planning_run(started.run.id)
    assert routed.run.current_step == PlanningRunStep.STORY_METADATA_REVISION

    aligned = service.advance_planning_run(started.run.id)
    assert aligned.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert aligned.run.outline_json is not None
    in_flight_outline = EpisodeOutline.model_validate_json(
        aligned.run.outline_json
    )
    assert in_flight_outline.target_duration_seconds == 90.0
    assert in_flight_outline.story_intent is not None
    assert in_flight_outline.story_intent.duration_contract is not None
    assert in_flight_outline.story_intent.duration_contract.target_seconds == 90.0

    # The ProjectDocument still describes the current (old) ScriptVersion until
    # the accepted revision is atomically published.
    before_publish = service.repository.get_project(original.project_id)
    assert before_publish is not None
    assert before_publish.project.current_script_version_id == original.id
    assert before_publish.project.target_duration_seconds == 120.0

    completed = _finish(service, started.run.id, max_steps=5)
    assert completed.project is not None
    revised_document = completed.project.document
    revised_script = next(
        item
        for item in revised_document.script_versions
        if item.id == revised_document.project.current_script_version_id
    )
    assert revised_script.story_intent is not None
    assert revised_script.story_intent.duration_contract is not None
    assert revised_script.story_intent.duration_contract.target_seconds == 90.0
    assert revised_document.project.target_duration_seconds == 90.0
    assert revised_script.structured_scenes == original.structured_scenes

    selected_line = revised_script.lines[0]
    followup = service.revise_script_lines(
        revised_document.project.id,
        ReviseScriptLinesRequest(
            revisions=[
                ScriptLineRevision(
                    line_id=selected_line.id,
                    spoken_text=f"{selected_line.spoken_text}。",
                    subtitle_text=f"{selected_line.subtitle_text}。",
                )
            ],
            change_reason="后续只修改一句台词",
        ),
    )
    followup_script = next(
        item
        for item in followup.document.script_versions
        if item.id == followup.document.project.current_script_version_id
    )
    assert followup_script.story_intent is not None
    assert followup_script.story_intent.duration_contract is not None
    assert followup_script.story_intent.duration_contract.target_seconds == 90.0
    assert followup.document.project.target_duration_seconds == 90.0


def test_verifier_metadata_only_violation_returns_to_metadata_agent(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "写一个完整的高中生纯爽短剧。",
        }
    )
    language = MetadataOnlyVerifierRetryClient(request)
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1].model_copy(deep=True)
    original_bible_ids = [
        version.id for version in initial.project.document.bible_versions
    ]
    initial_architect_calls = len(language.story_architect_instructions)
    initial_scene_writer_calls = len(language.script_scene_calls)
    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-verifier-metadata-retry",
        instruction="更正 EndingContract 中的人物认知边界，场次文字不动。",
        base_script_version_id=original.id,
    )

    routed = service.advance_planning_run(started.run.id)
    assert routed.run.current_step == PlanningRunStep.STORY_METADATA_REVISION

    first_metadata = service.advance_planning_run(started.run.id)
    assert first_metadata.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert language.metadata_revision_calls == 1

    rejected = service.advance_planning_run(started.run.id)

    assert rejected.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert rejected.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert rejected.run.revision_scene_sequences == []
    assert rejected.run.latest_revision_verification is not None
    assert rejected.run.latest_revision_verification.accepted is False
    assert rejected.run.latest_revision_verification.violations[0].metadata_targets == [
        StoryMetadataTarget.ENDING_CONTRACT
    ]
    violation_id = rejected.run.latest_revision_verification.violations[0].issue_id
    assert violation_id is not None
    assert language.local_revision_calls == []
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls

    resolved = service.resolve_story_verification(
        rejected.run.project_id,
        run_id=started.run.id,
        owner_agent_run_id=started.run.owner_agent_run_id,
        decision=(
            StoryVerificationResolutionDecision.CONTINUE_FOCUSED_REVISION
        ),
        rationale="Verifier 定位的是 EndingContract 元数据越界，交回 Repair Leader 决定范围。",
    )
    assert resolved.run.current_step == PlanningRunStep.STORY_REVISION

    repair_planned = service.advance_planning_run(started.run.id)
    assert repair_planned.run.current_step == PlanningRunStep.STORY_METADATA_REVISION

    second_metadata = service.advance_planning_run(started.run.id)

    assert second_metadata.run.current_step == PlanningRunStep.REVISION_VERIFIER
    assert language.metadata_revision_calls == 2
    assert language.metadata_revision_inputs[1]["requested_metadata_targets"] == [
        StoryMetadataTarget.ENDING_CONTRACT.value
    ]
    previous_verification = language.metadata_revision_inputs[1][
        "revision_verification"
    ]
    assert previous_verification is None
    leader_handoff = json.loads(
        language.repair_plan_inputs[-1]["previous_structural_feedback"]
    )
    assert leader_handoff["story_verification_resolution"][
        "revision_verification"
    ]["violations"][0]["issue_id"] == violation_id

    accepted = service.advance_planning_run(started.run.id)

    assert accepted.run.current_step == PlanningRunStep.FINALIZE
    assert language.revision_verifier_calls == 2
    assert language.revision_verifier_inputs[1][
        "previous_revision_verification"
    ] is None
    completed = _finish(service, started.run.id, max_steps=3)

    assert completed.project is not None
    previous, latest = completed.project.document.script_versions
    assert latest.parent_version_id == previous.id
    assert latest.ending_contract == language.revised_ending_contract
    assert latest.structured_scenes == previous.structured_scenes
    assert latest.screenplay_text == previous.screenplay_text
    assert [
        version.id for version in completed.project.document.bible_versions
    ] == original_bible_ids
    assert language.local_revision_calls == []
    assert language.revision_scope_calls == 0
    assert PlanningRunStep.STORY_ARCHITECT not in completed.run.completed_steps
    assert PlanningRunStep.SCENE_WRITER not in completed.run.completed_steps
    assert len(language.story_architect_instructions) == initial_architect_calls
    assert len(language.script_scene_calls) == initial_scene_writer_calls


def test_explicit_user_revision_can_update_a_previously_locked_script(
    tmp_path: Path,
) -> None:
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": "用户最初提交并锁定了一份完整剧本。",
        }
    )
    language = AgentSelectedLocalRevisionClient(request)
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    persisted = service.repository.get_project(initial.project.document.project.id)
    assert persisted is not None
    current = persisted.script_versions[-1]
    assert current.source_contract is not None
    locked_line = current.lines[1]
    persisted.project.source_mode = SourceMode.SCRIPT_LOCKED
    current.source_mode = SourceMode.SCRIPT_LOCKED
    current.source_contract.locked_dialogue = [
        StoryLockedDialogueLine(
            speaker=locked_line.speaker,
            text=locked_line.spoken_text,
            audio_kind=locked_line.audio_kind,
        )
    ]
    service.repository.save_project(persisted)
    feedback = "用户现在明确要求改写第二场的锁定台词。"

    started = service.start_script_revision(
        persisted.project.id,
        owner_agent_run_id="agent-run-locked-revision",
        instruction=feedback,
        base_script_version_id=current.id,
    )

    assert started.run.request.source_mode == SourceMode.IDEA_EXPANSION
    completed = _finish(service, started.run.id, max_steps=20)
    assert completed.project is not None
    latest = completed.project.document.script_versions[-1]
    assert latest.source_mode == SourceMode.SCRIPT_LOCKED
    assert "（局部改稿）" in latest.structured_scenes[1].screenplay_text
    assert latest.source_contract is not None
    assert latest.source_contract.authoritative_user_overrides[-1] == feedback


def test_agent_selected_structural_revision_rearchitects_and_appends_version(
    tmp_path: Path,
) -> None:
    original_brief = "保留主角和核心真相，但允许 Agent 按后续意见重组场次。"
    request = _request(120).model_copy(
        update={
            "content_planning_output": ContentPlanningOutput.SCRIPT_ONLY,
            "input_text": original_brief,
        }
    )
    language = AgentSelectedStructuralRevisionClient(request)
    service = _service(tmp_path, language)
    initial = _finish(
        service,
        _start_owned_planning(service, request).run.id,
    )

    assert initial.project is not None
    original = initial.project.document.script_versions[-1].model_copy(deep=True)
    original_scene_ids = [scene.id for scene in original.structured_scenes]
    original_headings = [scene.heading for scene in original.structured_scenes]
    original_bible_ids = [
        version.id for version in initial.project.document.bible_versions
    ]
    initial_writer_call_count = len(language.script_scene_calls)
    feedback = "把第二场提到开头，删掉原第一、三场，在中间加一场公开挑战。"
    started = service.start_script_revision(
        original.project_id,
        owner_agent_run_id="agent-run-structural-revision",
        instruction=feedback,
        base_script_version_id=original.id,
    )

    routed = service.advance_planning_run(started.run.id)
    assert routed.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert routed.run.latest_repair_plan is not None
    assert routed.run.latest_repair_plan.actions[0].affected_scene_sequences == []

    completed = _finish(service, started.run.id, max_steps=20)

    assert completed.project is not None
    assert PlanningRunStep.STORY_ARCHITECT in completed.run.completed_steps
    assert language.local_revision_calls == []
    assert language.revision_calls == 0
    assert language.script_scene_calls[initial_writer_call_count:] == [1, 2, 3]
    assert len(language.structural_architect_inputs) == 1
    architect_input = language.structural_architect_inputs[0]
    assert architect_input["user_brief"] == original_brief
    assert architect_input["latest_user_revision_instruction"] == feedback
    assert architect_input["current_complete_outline"] is not None
    assert architect_input["story_source_contract"][
        "authoritative_user_overrides"
    ][-1] == feedback

    versions = completed.project.document.script_versions
    assert len(versions) == 2
    previous, latest = versions
    assert previous.id == original.id
    assert latest.version == previous.version + 1
    assert latest.parent_version_id == previous.id
    assert latest.planning_run_id == started.run.id
    assert latest.change_reason == feedback
    assert latest.original_text == previous.original_text
    assert completed.project.document.project.current_script_version_id == latest.id
    assert [version.id for version in completed.project.document.bible_versions] == (
        original_bible_ids
    )

    latest_headings = [scene.heading for scene in latest.structured_scenes]
    assert latest_headings == [
        original_headings[1],
        "测试控制室·新增公开挑战",
        original_headings[3],
    ]
    latest_scene_ids = [scene.id for scene in latest.structured_scenes]
    assert latest_scene_ids[0] == original_scene_ids[1]
    assert latest_scene_ids[2] == original_scene_ids[3]
    assert latest_scene_ids[1] not in original_scene_ids
    assert original_scene_ids[0] not in latest_scene_ids
    assert original_scene_ids[2] not in latest_scene_ids
    assert previous.approval_status == ApprovalStatus.SUPERSEDED
    assert previous.freshness == Freshness.STALE
    assert latest.approval_status == ApprovalStatus.APPROVED
    assert latest.freshness == Freshness.CURRENT
