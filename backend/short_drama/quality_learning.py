"""Provider capability profiles and reusable QA regression scenarios."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from short_drama.models import (
    DirectorRepairStrategy,
    DomainModel,
    QaFailureCategory,
    Take,
)


class CapabilityRisk(DomainModel):
    id: str
    label: str
    description: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    failure_categories: list[QaFailureCategory]
    recommended_strategies: list[DirectorRepairStrategy]
    observed_failure_count: int = 0
    evidence_take_ids: list[str] = Field(default_factory=list[str])


class RegressionCase(DomainModel):
    id: str
    title: str
    scenario: str
    learning_objective: str
    failure_categories: list[QaFailureCategory]
    expected_repair_strategies: list[DirectorRepairStrategy]
    status: Literal["ACTIVE"] = "ACTIVE"


class H3CapabilityProfileResponse(DomainModel):
    provider: str = "minimax"
    model: str = "MiniMax-H3"
    version: str = "2026-08-17.1"
    project_id: str | None = None
    risks: list[CapabilityRisk]
    regression_cases: list[RegressionCase]


_BASE_RISKS: tuple[CapabilityRisk, ...] = (
    CapabilityRisk(
        id="complex-contact-motion",
        label="复杂手物接触与物体状态变化",
        description=(
            "持续手物接触、身体转向和物体开合等状态变化出现在同一镜头时，"
            "接触点或空间拓扑更容易漂移。"
        ),
        risk_level="HIGH",
        failure_categories=[
            QaFailureCategory.CONTACT_CONTINUITY,
            QaFailureCategory.SPATIAL_CONTINUITY,
        ],
        recommended_strategies=[
            DirectorRepairStrategy.CHANGE_KEYFRAME,
            DirectorRepairStrategy.SPLIT_SHOT,
            DirectorRepairStrategy.CHANGE_CAMERA,
        ],
    ),
    CapabilityRisk(
        id="turn-and-crossing-orientation",
        label="转身与跨空间动作的人物朝向",
        description=(
            "人物转身、走离镜头或跨越空间边界时，头部、躯干和行进方向可能不一致。"
        ),
        risk_level="HIGH",
        failure_categories=[QaFailureCategory.CHARACTER_ORIENTATION],
        recommended_strategies=[
            DirectorRepairStrategy.CHANGE_KEYFRAME,
            DirectorRepairStrategy.SPLIT_SHOT,
            DirectorRepairStrategy.CHANGE_CAMERA,
        ],
    ),
    CapabilityRisk(
        id="dense-composition-quality",
        label="复杂构图与关键帧适配",
        description=(
            "关键帧同时承担身份、空间、接触和动作终态时，人物姿态与整体构图质量可能下降。"
        ),
        risk_level="MEDIUM",
        failure_categories=[QaFailureCategory.COMPOSITION_QUALITY],
        recommended_strategies=[
            DirectorRepairStrategy.CHANGE_KEYFRAME,
            DirectorRepairStrategy.CHANGE_CAMERA,
            DirectorRepairStrategy.PROMPT_RETRY,
        ],
    ),
)


REGRESSION_CASES: tuple[RegressionCase, ...] = (
    RegressionCase(
        id="complex-contact-turn-boundary-crossing-v1",
        title="持续接触、转身与跨空间复合动作",
        scenario=(
            "角色先与可交互物体保持接触，再完成身体转向并从一个空间覆盖范围走向另一个范围；"
            "具体道具、场景和开合方向由当前镜头意图决定。"
        ),
        learning_objective=(
            "验证编排系统能否识别空间、接触、人物朝向和构图失败，并在纯提示重试收益低时"
            "比较换关键帧、拆镜和换覆盖机位。"
        ),
        failure_categories=[
            QaFailureCategory.SPATIAL_CONTINUITY,
            QaFailureCategory.CONTACT_CONTINUITY,
            QaFailureCategory.CHARACTER_ORIENTATION,
            QaFailureCategory.COMPOSITION_QUALITY,
        ],
        expected_repair_strategies=[
            DirectorRepairStrategy.CHANGE_KEYFRAME,
            DirectorRepairStrategy.SPLIT_SHOT,
            DirectorRepairStrategy.CHANGE_CAMERA,
        ],
    ),
)


def build_h3_capability_profile(
    takes: Sequence[Take],
    *,
    project_id: str | None = None,
) -> H3CapabilityProfileResponse:
    h3_takes = [
        take
        for take in takes
        if take.provider == "minimax" and take.model == "MiniMax-H3"
    ]
    risks: list[CapabilityRisk] = []
    for base in _BASE_RISKS:
        categories = set(base.failure_categories)
        evidence = [
            take.id
            for take in h3_takes
            if any(issue.category in categories for issue in take.qa_report.issues)
        ]
        risks.append(
            base.model_copy(
                deep=True,
                update={
                    "observed_failure_count": len(evidence),
                    "evidence_take_ids": evidence[-20:],
                },
            )
        )
    return H3CapabilityProfileResponse(
        project_id=project_id,
        risks=risks,
        regression_cases=[item.model_copy(deep=True) for item in REGRESSION_CASES],
    )


def known_regression_case(case_id: str) -> RegressionCase | None:
    return next((item for item in REGRESSION_CASES if item.id == case_id), None)
