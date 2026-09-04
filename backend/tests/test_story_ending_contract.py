"""Agent-native continuation decisions and final-ending review boundaries."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from short_drama.models import (
    ContinuationPreference,
    CreateProjectRequest,
    EndingContract,
    ResolvedEndingMode,
    ScriptReviewReport,
    SourceMode,
    StorySourceContract,
)
from short_drama.production_planner import (
    _fallback_episode_outline,
    _final_cold_read_prompt,
    _outline_prompt,
    _require_ending_contract,
)


def _request(input_text: str) -> CreateProjectRequest:
    return CreateProjectRequest(
        name="结局契约测试",
        source_mode=SourceMode.IDEA_EXPANSION,
        target_duration_seconds=90,
        input_text=input_text,
    )


def _ending_contract(
    *,
    preference: ContinuationPreference,
    mode: ResolvedEndingMode,
    open_threads: list[str] | None = None,
    hook: str | None = None,
) -> EndingContract:
    return EndingContract(
        requested_preference=preference,
        resolved_mode=mode,
        episode_payoff="主角在本集内完成了承诺的反击并看到结果。",
        required_closures=["本集主要冲突得到可见结果"],
        allowed_open_threads=open_threads or [],
        continuation_hook=hook,
        final_button="主角收起答卷，在全班的沉默中走出教室。",
        decision_basis="按照用户的续集偏好和本集故事容量决定。",
    )


def _source_contract(
    input_text: str,
    preference: ContinuationPreference,
) -> StorySourceContract:
    return StorySourceContract(
        explicit_commitments=[input_text],
        explicit_prohibitions=[],
        open_creative_choices=["未被用户限定的故事选择交由 Agent 判断"],
        continuation_preference=preference,
    )


def test_story_architect_receives_original_brief_and_semantically_decides_three_states() -> None:
    cases = [
        (
            "这个故事不要续集，必须在本集完整讲完。",
            ContinuationPreference.STANDALONE,
        ),
        (
            "做成系列，本集回报完成后可以保留下一集钩子。",
            ContinuationPreference.SERIES,
        ),
        (
            "有没有续集由 Agent 根据这个故事自行判断。",
            ContinuationPreference.AGENT_DECIDES,
        ),
    ]

    for original_brief, preference in cases:
        request = _request(original_brief)
        source_contract = _source_contract(original_brief, preference)

        instructions, input_text = _outline_prompt(request, source_contract)
        payload = json.loads(input_text)

        assert payload["user_brief"] == original_brief
        assert payload["story_source_contract"] == source_contract.model_dump(mode="json")
        assert "语义" in instructions
        assert ContinuationPreference.STANDALONE.value in instructions
        assert ContinuationPreference.SERIES.value in instructions
        assert ContinuationPreference.AGENT_DECIDES.value in instructions
        assert "由你根据这一个故事自主选择" in instructions


def test_agent_decides_preference_can_resolve_to_either_episode_shape() -> None:
    standalone = _ending_contract(
        preference=ContinuationPreference.AGENT_DECIDES,
        mode=ResolvedEndingMode.STANDALONE,
    )
    series = _ending_contract(
        preference=ContinuationPreference.AGENT_DECIDES,
        mode=ResolvedEndingMode.SERIES,
        open_threads=["主角将如何面对更大范围的数学危机"],
        hook="校长送来一张来自国际数学组织的求助函。",
    )

    assert standalone.resolved_mode == ResolvedEndingMode.STANDALONE
    assert series.resolved_mode == ResolvedEndingMode.SERIES


def test_story_architect_cannot_silently_omit_its_ending_decision() -> None:
    outline = _fallback_episode_outline(_request("续集与否交给 Agent 判断。")).model_copy(
        update={"ending_contract": None}
    )

    with pytest.raises(ValueError, match="must explicitly return ending_contract"):
        _require_ending_contract(outline)


def test_review_agent_must_explicitly_state_its_revision_decision() -> None:
    with pytest.raises(ValidationError):
        ScriptReviewReport.model_validate(
            {
                "summary": "剧本审校完成。",
                "strengths": [],
                "issues": [],
            }
        )

    with pytest.raises(ValidationError):
        ScriptReviewReport.model_validate(
            {
                "summary": "剧本需要定向修订。",
                "strengths": [],
                "issues": [
                    {
                        "category": "CAUSAL_LOGIC",
                        "severity": "HIGH",
                        "scene_sequences": [1],
                        "observation": "因果未闭合。",
                        "recommendation": "修复本场因果。",
                    }
                ],
                "requires_revision": True,
            }
        )


def test_final_cold_read_semantically_compares_brief_contract_and_screenplay() -> None:
    request = _request(
        "一名学生在全世界数学能力倒退后完成反击；"
        "不要续集，本集必须完整结束。"
    )
    outline = _fallback_episode_outline(request)
    screenplay = (
        "内景·教室·日\n"
        "老师写下一道加法题，全班陷入恐慌。\n"
        "林夏：答案是八。\n"
        "林夏：先发原始需求和事务清单。"
    )
    scene = outline.scenes[0].model_copy(
        deep=True,
        update={
            "summary": "INTERNAL_SCENE_SUMMARY",
            "narrative_purpose": "INTERNAL_NARRATIVE_PURPOSE",
            "playable_actions": ["INTERNAL_PLAYABLE_ACTION"],
            "screenplay_text": screenplay,
            "continuity_in": outline.scenes[0].continuity_in.model_copy(
                deep=True,
                update={"known_facts": ["INTERNAL_CONTINUITY_STATE"]},
            ),
        },
    )
    ending_contract = _ending_contract(
        preference=ContinuationPreference.STANDALONE,
        mode=ResolvedEndingMode.STANDALONE,
    )
    outline = outline.model_copy(
        deep=True,
        update={
            "story_summary": "INTERNAL_STORY_SUMMARY",
            "ending_contract": ending_contract,
            "scenes": [scene],
        },
    )

    instructions, input_text = _final_cold_read_prompt(request, outline)
    payload = json.loads(input_text)

    # Profile, duration and output_schema are transport constraints. The cold
    # reader also receives the explicit source promises and the intended viewer
    # experience, but no prior issue ledger, retry history or hidden scene notes.
    assert set(payload) == {
        "user_brief",
        "source_contract",
        "story_intent",
        "production_profile",
        "target_duration_seconds",
        "ending_contract",
        "character_names",
        "screenplay",
        "output_schema",
    }
    assert payload["user_brief"] == request.input_text
    assert payload["ending_contract"] == ending_contract.model_dump(mode="json")
    assert payload["character_names"] == [
        character.name for character in outline.characters
    ]
    assert payload["screenplay"] == [
        {
            "scene_sequence": scene.sequence,
            "heading": scene.heading,
            "screenplay_text": screenplay,
        }
    ]
    assert "selected_narrative_skill_ids" not in payload
    assert "issue_ledger" not in payload
    assert "previous_structural_feedback" not in payload

    for internal_value in (
        "INTERNAL_STORY_SUMMARY",
        "INTERNAL_SCENE_SUMMARY",
        "INTERNAL_NARRATIVE_PURPOSE",
        "INTERNAL_PLAYABLE_ACTION",
        "INTERNAL_CONTINUITY_STATE",
    ):
        assert internal_value not in input_text
    assert "最终冷读的通用职责" in instructions
    assert "只阅读用户原始需求" in instructions
    assert "ending_contract" in instructions
    assert "兑现目标观众体验、本集释放和最后一个可见戏剧动作" in instructions
    assert "行政指令、索要文件、事务清单" in instructions
    assert "STANDALONE" in instructions
    assert "SERIES" in instructions
    assert "Agent 已根据当前任务语义选择叙事技能" not in instructions
