"""Pairwise/multi-candidate Take comparison for the Director Agent."""

from __future__ import annotations

from short_drama.language_defaults import LANGUAGE_MODEL

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from short_drama.models import (
    DomainModel,
    ProjectDocument,
    SemanticReviewStatus,
    ShotPlan,
    Take,
    TakeComparisonCandidateReview,
    TakeComparisonDecision,
    TakeComparisonReport,
    VideoInspectionReport,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.video_inspection import image_data_url, semantic_reference_entries


class TakeComparisonJudgment(DomainModel):
    decision: TakeComparisonDecision
    selected_take_id: str | None = None
    candidate_reviews: list[TakeComparisonCandidateReview] = Field(min_length=2)
    summary: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_selected_take(self) -> TakeComparisonJudgment:
        if self.decision == TakeComparisonDecision.SELECT_TAKE:
            if self.selected_take_id is None:
                raise ValueError("SELECT_TAKE requires selected_take_id")
        elif self.selected_take_id is not None:
            raise ValueError("Only SELECT_TAKE may include selected_take_id")
        return self


def compare_take_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    takes: list[Take],
    inspections: list[VideoInspectionReport],
    client: LanguageProviderClient,
    review_focus: str | None,
    created_at: str,
    comparison_scope: Literal["GENERATION_UNIT", "SHOT"] = "GENERATION_UNIT",
) -> TakeComparisonReport:
    """Compare candidates in one model call and allow an explicit no-winner result."""

    if len(takes) < 2:
        raise ValueError("Take comparison requires at least two candidates")
    if len({take.id for take in takes}) != len(takes):
        raise ValueError("Take comparison candidates must be unique")
    if any(take.project_id != document.project.id or shot.id not in take.covered_shot_ids() for take in takes):
        raise ValueError("All compared Takes must belong to the same project and cover the requested shot")
    covered_ids = takes[0].covered_shot_ids() if comparison_scope == "GENERATION_UNIT" else [shot.id]
    if comparison_scope == "GENERATION_UNIT" and any(take.covered_shot_ids() != covered_ids for take in takes):
        raise ValueError("Full-group candidates must cover the same ordered shots")
    shots_by_id = {item.id: item for item in document.shots}
    if any(shot_id not in shots_by_id for shot_id in covered_ids):
        raise ValueError("A compared group is missing a current source shot")

    reports_by_take = {report.take_id: report for report in inspections}
    missing = [take.id for take in takes if take.id not in reports_by_take]
    if missing:
        raise ValueError("Missing video inspections for Takes: " + ", ".join(missing))

    reference_plans = [item for item in document.reference_plan if item.shot_id in covered_ids]
    image_urls: list[str] = []
    image_map: list[dict[str, str | int]] = []
    inspection_ids: list[str] = []
    for take in takes:
        report = reports_by_take[take.id]
        contact_sheet = Path(report.contact_sheet_path).expanduser().resolve()
        if not contact_sheet.is_file():
            raise ValueError(f"Inspection contact sheet is missing for Take {take.id}")
        image_urls.append(image_data_url(contact_sheet))
        image_map.append(
            {
                "index": len(image_urls),
                "role": "CANDIDATE_TIMELINE",
                "take_id": take.id,
            }
        )
        inspection_ids.append(report.id)
        if report.boundary_contact_sheet_path is not None:
            boundary = Path(report.boundary_contact_sheet_path).expanduser().resolve()
            if boundary.is_file():
                image_urls.append(image_data_url(boundary))
                image_map.append(
                    {
                        "index": len(image_urls),
                        "role": "CANDIDATE_PREVIOUS_BOUNDARY",
                        "take_id": take.id,
                    }
                )

    seen_references: set[str] = set()
    for plan in reference_plans:
        for version_id, label, url in semantic_reference_entries(document, plan):
            if version_id in seen_references:
                continue
            seen_references.add(version_id)
            image_urls.append(url)
            image_map.append({"index": len(image_urls), "role": "SELECTED_GENERATION_REFERENCE", "description": label})

    instructions = (
        "你是服务于电影导演 Agent 的同镜头 Take 对比工具。只返回 JSON，并严格匹配"
        "给定 schema。将所有候选时间线放在 comparison_target 指定的同一镜头组或源窗口、当前分镜、实际参考和前一镜边界下比较。"
        "阅读 QA 状态和问题作为证据，但不要把语义、连续性或审美标签当成不可覆盖的资格门禁。"
        "比较角色身份与服装、场景与道具连续性、动作与屏幕方向和摄影的相对差异。"
        "输入只有静帧与视觉观察，没有音频证据；对白、声线、声源语义与音画同步一律 UNKNOWN，不能据此判断声音错误。"
        "按每个候选的 shot_windows 定位相同叙事片段，合法的组内切镜不是漂移，不能用代表镜时长评价整组。"
        "报告仅适用于记录的 observed_source_range 和 review_focus，不外推未覆盖的时段。"
        "不要只因为某个候选‘没那么差’就选它；当所有候选都有会破坏当前剪辑意图的可见矛盾时，"
        "返回 NONE_ACCEPTABLE。若你选择一个带有 QA 警告的 Take，必须在该候选的 strengths/weaknesses "
        "和 summary 中说明为何这个创作取舍在当前目标下可接受。稀疏抽帧不能证明未被看到的事件。"
        "所有观察都必须有证据，本工具不做修复决定；报告只是建议，Agent 和用户可覆盖。"
        "relative_score 的 0–5 只用于解释本次相对比较，不要声称它能跨调用校准。"
    )
    payload = {
        "output_schema": TakeComparisonJudgment.model_json_schema(),
        "shot": shot.model_dump(mode="json"),
        "comparison_target": {"scope": comparison_scope, "requested_shot_id": shot.id, "covered_shot_ids": covered_ids},
        "current_shots": [shots_by_id[shot_id].model_dump(mode="json") for shot_id in covered_ids],
        "reference_plans": [plan.model_dump(mode="json") for plan in reference_plans],
        "candidates": [
            {
                "take": {
                    "id": take.id,
                    "candidate_index": take.candidate_index,
                    "route": take.route.value,
                    "generation_unit_id": take.generation_unit_id,
                    "shot_windows": [window.model_dump(mode="json") for window in take.shot_windows],
                    "qa_status": take.qa_status.value,
                    "qa_score": take.qa_report.score,
                    "qa_issues": [
                        issue.model_dump(mode="json") for issue in take.qa_report.issues
                    ],
                },
                "inspection": {
                    "id": reports_by_take[take.id].id,
                    "summary": reports_by_take[take.id].summary,
                    "observed_source_range": {
                        "start_seconds": reports_by_take[take.id].observed_source_in_seconds,
                        "end_seconds": reports_by_take[take.id].observed_source_out_seconds,
                    },
                    "review_focus": reports_by_take[take.id].review_focus,
                    "audio_semantics_observed": False,
                    "issues": [
                        issue.model_dump(mode="json")
                        for issue in reports_by_take[take.id].issues
                    ],
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in reports_by_take[take.id].observations
                    ],
                },
            }
            for take in takes
        ],
        "technically_available_take_ids": [take.id for take in takes],
        "review_focus": review_focus,
        "image_map": image_map,
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls,
        max_output_tokens=16000,
    )
    judgment = TakeComparisonJudgment.model_validate_json(_strip_json_fence(output))
    known_ids = {take.id for take in takes}
    reviewed_ids = {item.take_id for item in judgment.candidate_reviews}
    if reviewed_ids != known_ids or len(judgment.candidate_reviews) != len(known_ids):
        raise ValueError("Take comparison must review every supplied candidate exactly once")
    if judgment.selected_take_id is not None:
        selected = next(
            (take for take in takes if take.id == judgment.selected_take_id),
            None,
        )
        if selected is None:
            raise ValueError("Take comparison selected an unknown candidate")

    return TakeComparisonReport(
        id=f"take-comparison-{uuid4().hex}",
        project_id=document.project.id,
        shot_id=shot.id,
        candidate_take_ids=[take.id for take in takes],
        inspection_ids=inspection_ids,
        comparison_scope=comparison_scope,
        covered_shot_ids=covered_ids,
        review_focus=review_focus,
        decision=judgment.decision,
        selected_take_id=judgment.selected_take_id,
        semantic_status=SemanticReviewStatus.COMPLETE,
        semantic_model=LANGUAGE_MODEL,
        candidate_reviews=judgment.candidate_reviews,
        summary=judgment.summary,
        advisory_only=True,
        created_at=created_at,
    )


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
