"""Hierarchical, advisory episode review built from persisted production evidence."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator

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
    )
    instructions = (
        "You are a scene-level episode-review Agent for the active video-production medium. "
        f"{skill_prompt.text} Return JSON only and match SceneReviewAgentOutput. Review the supplied "
        "scene as edited, using "
        "the locked scene purpose, chronological ShotPlans, selected Takes, timeline clips, and "
        "persisted video observations. Report evidence before inference. Do not grade the scene, "
        "require a fixed beat count, require new information every N seconds, or turn polish "
        "preferences into failures. A finding needs a concrete contradiction, missing causal beat, "
        "unreadable story action, state discontinuity, or visibly unsupported duration. Keep LOW "
        "and MEDIUM findings advisory. Set repair_recommended only when a linked shot can plausibly "
        "be locally regenerated without rewriting the surrounding story. Do not perform STT, voice "
        "identity, or audio-quality QA. SOUND_SOURCE_SEMANTICS is allowed only when existing visual "
        "evidence or locked audio intent shows that the wrong on-screen source is speaking. Every "
        "finding is dismissible and never blocks export."
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
            "Open-ended advisory review only. No numeric score, approval gate, fixed pacing rule, "
            "or scene-specific schema. Use only supplied scene and production evidence."
        ),
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls or None,
        max_output_tokens=5000,
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
    )
    instructions = (
        "You are an episode-level reviewer Agent for the active video-production medium. "
        f"{skill_prompt.text} Return JSON only and match EpisodeReviewAgentOutput. Compare the locked "
        "script, scene entry/exit states, "
        "timeline order, and the independent scene reviews. Find only cross-scene or episode-level "
        "problems that are supported by those artifacts: story clarity, causal handoff, character "
        "or prop state, sound-source semantics already observed visually, and pacing caused by "
        "specific edited material. Do not rescore or repeat scene findings unless combining them "
        "changes the diagnosis. Do not enforce a fixed beat count, fixed shot count, or information "
        "interval. Set repair_recommended only when concrete linked shots can be locally regenerated; "
        "otherwise give a dismissible advisory. Never block export or require approval."
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
            "Advisory-only evidence synthesis. No numeric score, approval gate, answer key, fixed "
            "pacing threshold, voice QA, or scene-specific field."
        ),
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        max_output_tokens=6000,
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
