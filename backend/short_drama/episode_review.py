"""Hierarchical, advisory episode review built from persisted production evidence."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from short_drama.models import (
    DomainModel,
    EpisodeReviewDimension,
    EpisodeReviewFinding,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    SceneEpisodeReview,
    ScriptReviewSeverity,
    ShotPlan,
    StructuredScene,
    Take,
    TimelineManifest,
    VideoInspectionReport,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient


class EpisodeReviewAgentFinding(DomainModel):
    dimension: EpisodeReviewDimension
    code: str = Field(min_length=1, max_length=120)
    severity: ScriptReviewSeverity
    message: str = Field(min_length=1, max_length=1600)
    category: QaFailureCategory = QaFailureCategory.OTHER
    evidence: str = Field(min_length=1, max_length=2400)
    scene_ids: list[str] = Field(default_factory=list[str], max_length=24)
    shot_ids: list[str] = Field(default_factory=list[str], max_length=64)
    recommended_action: str = Field(min_length=1, max_length=1600)
    repair_recommended: bool = False

    @model_validator(mode="after")
    def require_repair_target(self) -> EpisodeReviewAgentFinding:
        if self.repair_recommended and not self.shot_ids:
            raise ValueError("An automatic repair recommendation requires a linked shot")
        return self


class SceneReviewAgentOutput(DomainModel):
    observations: list[str] = Field(default_factory=list[str], max_length=32)
    findings: list[EpisodeReviewAgentFinding] = Field(
        default_factory=list[EpisodeReviewAgentFinding],
        max_length=32,
    )
    summary: str = Field(min_length=1, max_length=3000)


class EpisodeReviewAgentOutput(DomainModel):
    findings: list[EpisodeReviewAgentFinding] = Field(
        default_factory=list[EpisodeReviewAgentFinding],
        max_length=128,
    )
    summary: str = Field(min_length=1, max_length=5000)


def review_scene(
    *,
    document: ProjectDocument,
    manifest: TimelineManifest,
    scene: StructuredScene,
    scene_sequence: int,
    shots: list[ShotPlan],
    takes_by_id: dict[str, Take],
    latest_inspection_by_take: dict[str, VideoInspectionReport],
    client: LanguageProviderClient,
    editorial_project: dict[str, JsonValue] | None = None,
) -> SceneEpisodeReview:
    shot_ids = [shot.id for shot in shots]
    clip_by_shot = {item.shot_id: item for item in manifest.video_clips}
    line_by_id = {
        line.id: line
        for script in document.script_versions
        if script.id == manifest.script_version_id
        for line in script.lines
    }
    image_urls: list[str] = []
    image_map: list[dict[str, object]] = []
    shot_evidence: list[dict[str, object]] = []
    inspection_ids: list[str] = []
    for shot in shots:
        clip = clip_by_shot.get(shot.id)
        if clip is None:
            raise ValueError(f"Current manifest has no video clip for {shot.id}")
        take = takes_by_id.get(clip.take_id)
        if take is None:
            raise ValueError(f"Current manifest references missing Take {clip.take_id}")
        inspection = latest_inspection_by_take.get(take.id)
        if inspection is not None:
            inspection_ids.append(inspection.id)
            if len(image_urls) < 12:
                image_urls.append(_image_data_url(Path(inspection.contact_sheet_path)))
                image_map.append(
                    {
                        "index": len(image_urls),
                        "shot_id": shot.id,
                        "take_id": take.id,
                        "role": "CHRONOLOGICAL_TAKE_CONTACT_SHEET",
                    }
                )
        shot_evidence.append(
            {
                "shot_plan": shot.model_dump(mode="json"),
                "timeline_clip": clip.model_dump(mode="json"),
                "selected_take": {
                    "id": take.id,
                    "route": take.route.value,
                    "duration_seconds": take.duration_seconds,
                    "qa_status": take.qa_status.value,
                    "qa_score": take.qa_report.score,
                    "qa_issues": [
                        item.model_dump(mode="json") for item in take.qa_report.issues
                    ],
                },
                "dialogue": [
                    line_by_id[line_id].model_dump(mode="json")
                    for line_id in shot.dialogue_line_ids
                    if line_id in line_by_id
                ],
                "video_inspection": (
                    {
                        "id": inspection.id,
                        "semantic_status": inspection.semantic_status.value,
                        "observations": [
                            item.model_dump(mode="json")
                            for item in inspection.observations
                        ],
                        "issues": [
                            item.model_dump(mode="json") for item in inspection.issues
                        ],
                        "summary": inspection.summary,
                    }
                    if inspection is not None
                    else None
                ),
            }
        )

    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.EPISODE_REVIEWER,),
        include_constitution=False,
        stage=ProductionPromptStage.POST,
    )
    instructions = (
        "你是当前视频制作媒介中的场景级成片审校 Agent。"
        f"{skill_prompt.text} 只返回 JSON，并严格匹配 SceneReviewAgentOutput。请基于已经剪成的"
        "场景进行审校，使用当前采用的场景意图、按时间顺序排列的 ShotPlan、已选 Take、时间线片段"
        "以及已持久化的视频观察。先陈述证据，再作推断。不要给场景打分，不要要求固定的节拍"
        "数量，不要要求每隔固定时间出现新信息，也不要把润色偏好当作问题。只有出现具体矛盾、"
        "缺失的因果环节、无法读懂的叙事动作、状态不连续，或画面证据明显无法支撑当前时长时，"
        "才形成 finding。LOW 和 MEDIUM 级 finding 只作为建议。只有当关联镜头能够在不改写周边"
        "剧情的前提下合理地局部重生成时，才设置 repair_recommended。不要执行语音转写、音色身份"
        "或音频质量 QA。只有现有画面证据或当前声音意图表明发声主体错误时，才可使用 "
        "SOUND_SOURCE_SEMANTICS。所有 finding 都可以被忽略，且绝不阻塞导出。"
    )
    payload = {
        "output_schema": SceneReviewAgentOutput.model_json_schema(),
        "episode": {
            "name": document.project.name,
            "production_profile": document.project.production_profile.value,
            "target_duration_seconds": document.project.target_duration_seconds,
        },
        "scene": scene.model_dump(mode="json"),
        "scene_sequence": scene_sequence,
        "shot_evidence": shot_evidence,
        "image_map": image_map,
        "allowed_dimensions": [item.value for item in EpisodeReviewDimension],
        "allowed_issue_categories": [item.value for item in QaFailureCategory],
        "product_rule": (
            "只做开放式建议审校；不使用数字评分、审批门、固定节奏规则或场景专属 schema。"
            "只依据提供的场景与制作证据。"
        ),
    }
    if editorial_project is not None:
        from short_drama.studio_episode_review import editorial_scene_context
        payload["saved_editorial_scene"] = editorial_scene_context(editorial_project, shot_ids)
        payload["visual_evidence_scope"] = (
            "当前时间、取用范围、变速、倒放、图层和字幕以 saved_editorial_scene 为准。"
            "图片是源 Take 的观察联系表，不是当前剪辑合成画面；不能凭它断言剪辑图版缺失、"
            "合成排版正确或变速后画面已通过检查。完整合成需查看当前修订的实际导出。"
        )
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls or None,
        max_output_tokens=16_000,
    )
    parsed = SceneReviewAgentOutput.model_validate_json(_strip_json_fence(output))
    allowed_scenes = {scene.id}
    allowed_shots = set(shot_ids)
    findings = [
        _persist_finding(
            item,
            allowed_scene_ids=allowed_scenes,
            allowed_shot_ids=allowed_shots,
            default_scene_ids=[scene.id],
        )
        for item in parsed.findings
    ]
    return SceneEpisodeReview(
        scene_id=scene.id,
        scene_sequence=scene_sequence,
        shot_ids=shot_ids,
        inspection_ids=list(dict.fromkeys(inspection_ids)),
        observations=parsed.observations,
        findings=findings,
        summary=parsed.summary,
    )


def review_episode(
    *,
    document: ProjectDocument,
    manifest: TimelineManifest,
    scene_reviews: list[SceneEpisodeReview],
    client: LanguageProviderClient,
) -> tuple[list[EpisodeReviewFinding], str]:
    script = next(
        item
        for item in document.script_versions
        if item.id == manifest.script_version_id
    )
    allowed_scene_ids = {item.id for item in script.structured_scenes} | {
        item.scene_id for item in scene_reviews
    }
    allowed_shot_ids = {item.id for item in document.shots}
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.EPISODE_REVIEWER,),
        include_constitution=False,
        stage=ProductionPromptStage.POST,
    )
    instructions = (
        "你是当前视频制作媒介中的整集审校 Agent。"
        f"{skill_prompt.text} 只返回 JSON，并严格匹配 EpisodeReviewAgentOutput。请对照当前剧本、"
        "各场景的进入与退出状态、时间线顺序以及相互独立的场景审校结果。只识别这些产物能够支持的"
        "跨场景或整集问题，包括故事是否清楚、因果是否顺利交接、人物或道具状态是否连续、已有画面"
        "观察支持的声音来源语义，以及具体剪辑内容造成的节奏问题。除非合并多个场景 finding 会改变"
        "诊断，否则不要重新评分或重复它们。不要强制固定的节拍数、镜头数或信息出现间隔。只有具体"
        "关联镜头可以局部重生成时，才设置 repair_recommended；其他情况给出可忽略的建议。绝不阻塞"
        "导出，也不要求审批。"
    )
    payload = {
        "output_schema": EpisodeReviewAgentOutput.model_json_schema(),
        "episode": {
            "name": document.project.name,
            "production_profile": document.project.production_profile.value,
            "target_duration_seconds": document.project.target_duration_seconds,
            "manifest_duration_seconds": manifest.duration_seconds,
            "script_lines": [item.model_dump(mode="json") for item in script.lines],
            "scenes": [
                item.model_dump(mode="json") for item in script.structured_scenes
            ],
        },
        "timeline_order": [item.shot_id for item in manifest.video_clips],
        "scene_reviews": [item.model_dump(mode="json") for item in scene_reviews],
        "allowed_dimensions": [item.value for item in EpisodeReviewDimension],
        "allowed_issue_categories": [item.value for item in QaFailureCategory],
        "product_rule": (
            "只做建议性的证据综合；不使用数字评分、审批门、标准答案、固定节奏阈值、声音 QA "
            "或场景专属字段。"
        ),
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        max_output_tokens=16_000,
    )
    parsed = EpisodeReviewAgentOutput.model_validate_json(_strip_json_fence(output))
    findings = [
        _persist_finding(
            item,
            allowed_scene_ids=allowed_scene_ids,
            allowed_shot_ids=allowed_shot_ids,
            default_scene_ids=[],
        )
        for item in parsed.findings
    ]
    return findings, parsed.summary


def _persist_finding(
    finding: EpisodeReviewAgentFinding,
    *,
    allowed_scene_ids: set[str],
    allowed_shot_ids: set[str],
    default_scene_ids: list[str],
) -> EpisodeReviewFinding:
    scene_ids = finding.scene_ids or default_scene_ids
    unknown_scenes = sorted(set(scene_ids) - allowed_scene_ids)
    unknown_shots = sorted(set(finding.shot_ids) - allowed_shot_ids)
    if unknown_scenes:
        raise ValueError("Episode review referenced unknown scenes: " + ", ".join(unknown_scenes))
    if unknown_shots:
        raise ValueError("Episode review referenced unknown shots: " + ", ".join(unknown_shots))
    return EpisodeReviewFinding(
        id=f"episode-finding-{uuid4().hex}",
        dimension=finding.dimension,
        issue=QaIssue(
            code=finding.code,
            severity=finding.severity.value,
            message=finding.message,
            repair_layer="EPISODE_DIRECTOR",
            category=finding.category,
        ),
        evidence=finding.evidence,
        scene_ids=list(dict.fromkeys(scene_ids)),
        shot_ids=list(dict.fromkeys(finding.shot_ids)),
        recommended_action=finding.recommended_action,
        repair_recommended=finding.repair_recommended,
        dismissible=True,
    )


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _image_data_url(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"Episode review contact sheet is unavailable: {resolved}")
    mime_type = mimetypes.guess_type(resolved.name)[0] or "image/jpeg"
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
