"""Structured autonomous planning for empty, image, idea, and locked-script inputs."""

from __future__ import annotations

import base64
from collections import Counter
import json
import math
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, model_validator

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetSourceRecord,
    AssetVersion,
    AudioKind,
    CapabilitySnapshot,
    CharacterProfile,
    ContinuityState,
    CreateProjectRequest,
    DomainModel,
    ExecutionPlanEntry,
    ExecutionStrategy,
    Freshness,
    LocationProfile,
    OriginType,
    ProjectBibleVersion,
    ProjectDocument,
    ProductionProfile,
    ProjectRecord,
    ProjectStage,
    PropProfile,
    ReferenceAngle,
    ReferenceBinding,
    ReferenceFraming,
    ReferencePlanEntry,
    ReferenceRequirement,
    ReferenceRole,
    ReviewRepairOwner,
    RevisionJudgeRecommendation,
    ScriptLine,
    ScriptReviewDisposition,
    ScriptReviewDispositionAction,
    ScriptReviewIssueRoute,
    ScriptReviewReport,
    StoryRepairAction,
    StoryRepairDiagnosis,
    StoryRepairPlan,
    StorySourceContract,
    StoryRevisionVerification,
    ScriptVersion,
    SceneContinuitySummary,
    ShotPlan,
    SourceMode,
    StructuredScene,
    StyleProfile,
)
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)


class PlannedDialogueLine(DomainModel):
    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    audio_kind: AudioKind = AudioKind.DIALOGUE


class PlannedCharacter(DomainModel):
    name: str = Field(min_length=1, max_length=80)
    age: int = Field(ge=0)
    appearance: str = Field(min_length=1, max_length=500)
    wardrobe: str = Field(min_length=1, max_length=500)
    performance: str = Field(min_length=1, max_length=500)
    voice_identity: str = Field(min_length=1, max_length=80)


class PlannedLocation(DomainModel):
    name: str = Field(min_length=1, max_length=120)
    layout: str = Field(min_length=1, max_length=800)
    lighting: str = Field(min_length=1, max_length=500)
    palette: str = Field(min_length=1, max_length=500)
    key_views: list[str] = Field(min_length=1, max_length=8)


class PlannedProp(DomainModel):
    name: str = Field(min_length=1, max_length=120)
    appearance: str = Field(min_length=1, max_length=500)


class PlannedStyle(DomainModel):
    genre: str = Field(min_length=1, max_length=120)
    cinematography: str = Field(min_length=1, max_length=800)
    color: str = Field(min_length=1, max_length=500)
    texture: str = Field(min_length=1, max_length=500)
    forbidden: list[str] = Field(default_factory=list[str], max_length=12)


class PlannedReferenceNeed(DomainModel):
    """Provider-neutral visual coverage requested by the planning Agent."""

    subject_name: str = Field(min_length=1, max_length=120)
    acceptable_roles: list[ReferenceRole] = Field(min_length=1, max_length=4)
    preferred_angles: list[ReferenceAngle] = Field(
        default_factory=list[ReferenceAngle],
        max_length=4,
    )
    preferred_framings: list[ReferenceFraming] = Field(
        default_factory=list[ReferenceFraming],
        max_length=4,
    )
    tags: list[str] = Field(default_factory=list[str], max_length=12)
    purpose: str = Field(min_length=1, max_length=500)
    required: bool = True


class PlannedShot(DomainModel):
    scene_sequence: int = Field(default=1, ge=1)
    duration_seconds: float = Field(gt=0.0)
    narrative_purpose: str = Field(min_length=1, max_length=500)
    visual: str = Field(min_length=1, max_length=1200)
    framing: str = Field(min_length=1, max_length=200)
    camera: str = Field(min_length=1, max_length=500)
    audio_intent: str = Field(min_length=1, max_length=800)
    dialogue_line_indices: list[int] = Field(default_factory=list[int], max_length=40)
    character_positions: str = Field(min_length=1, max_length=500)
    eyeline: str = Field(min_length=1, max_length=500)
    prop_state_in: str = Field(min_length=1, max_length=500)
    prop_state_out: str = Field(min_length=1, max_length=500)
    action_in: str = Field(min_length=1, max_length=500)
    action_out: str = Field(min_length=1, max_length=500)
    transition_out: str = Field(min_length=1, max_length=120)
    merge_candidate: bool = False
    local_rework_risk: str = Field(min_length=1, max_length=120)
    reference_needs: list[PlannedReferenceNeed] = Field(
        default_factory=list[PlannedReferenceNeed],
        max_length=12,
    )


class PlannedSceneContract(DomainModel):
    sequence: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1600)
    narrative_purpose: str = Field(min_length=1, max_length=800)
    playable_actions: list[str] = Field(default_factory=list[str], max_length=24)
    target_duration_seconds: float = Field(ge=4.0)
    dialogue_line_indices: list[int] = Field(default_factory=list[int], max_length=500)
    location_names: list[str] = Field(min_length=1, max_length=6)
    continuity_in: SceneContinuitySummary
    continuity_out: SceneContinuitySummary


class EpisodeOutline(DomainModel):
    title: str = Field(min_length=1, max_length=120)
    story_summary: str = Field(min_length=1, max_length=2400)
    target_duration_seconds: float = Field(ge=4.0)
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=5000,
    )
    characters: list[PlannedCharacter] = Field(min_length=1, max_length=12)
    locations: list[PlannedLocation] = Field(min_length=1, max_length=20)
    props: list[PlannedProp] = Field(default_factory=list[PlannedProp], max_length=20)
    style: PlannedStyle
    scenes: list[PlannedSceneContract] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_outline(self) -> EpisodeOutline:
        sequences = [scene.sequence for scene in self.scenes]
        if sequences != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scene sequences must be contiguous and 1-based")
        line_count = len(self.lines)
        assigned = [
            index
            for scene in self.scenes
            for index in scene.dialogue_line_indices
        ]
        invalid = sorted({index for index in assigned if index < 1 or index > line_count})
        if invalid:
            raise ValueError(f"Scene dialogue indices are out of range: {invalid}")
        duplicates = sorted(index for index, count in Counter(assigned).items() if count > 1)
        if duplicates:
            raise ValueError(f"Dialogue lines are assigned to multiple scenes: {duplicates}")
        missing = sorted(set(range(1, line_count + 1)) - set(assigned))
        if missing:
            raise ValueError(f"Dialogue lines are not assigned to a scene: {missing}")
        known_locations = {location.name for location in self.locations}
        unknown_locations = sorted(
            {
                name
                for scene in self.scenes
                for name in scene.location_names
                if name not in known_locations
            }
        )
        if unknown_locations:
            raise ValueError(
                "Scene contracts use unknown locations: " + ", ".join(unknown_locations)
            )
        planned_duration = sum(scene.target_duration_seconds for scene in self.scenes)
        tolerance = max(1.0, self.target_duration_seconds * 0.2)
        if abs(planned_duration - self.target_duration_seconds) > tolerance:
            raise ValueError(
                "Scene duration targets do not match the episode target: "
                f"target={self.target_duration_seconds}, scenes={planned_duration}"
            )
        return self


class SceneShotPlan(DomainModel):
    scene_sequence: int = Field(ge=1)
    shots: list[PlannedShot] = Field(min_length=1, max_length=80)
    continuity_out: SceneContinuitySummary


class ProductionPlan(DomainModel):
    title: str = Field(min_length=1, max_length=120)
    scene_heading: str = Field(min_length=1, max_length=200)
    scene_summary: str = Field(min_length=1, max_length=2400)
    target_duration_seconds: float = Field(default=50.0, ge=4.0)
    lines: list[PlannedDialogueLine] = Field(
        default_factory=list[PlannedDialogueLine],
        max_length=5000,
    )
    characters: list[PlannedCharacter] = Field(min_length=1, max_length=12)
    locations: list[PlannedLocation] = Field(min_length=1, max_length=20)
    props: list[PlannedProp] = Field(default_factory=list[PlannedProp], max_length=20)
    style: PlannedStyle
    scenes: list[PlannedSceneContract] = Field(
        default_factory=list[PlannedSceneContract],
        max_length=40,
    )
    shots: list[PlannedShot] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_timeline(self) -> ProductionPlan:
        duration = sum(shot.duration_seconds for shot in self.shots)
        tolerance = max(15.0, self.target_duration_seconds * 0.2)
        if abs(duration - self.target_duration_seconds) > tolerance:
            raise ValueError(
                "Planned episode duration does not match the requested target: "
                f"target={self.target_duration_seconds}, shots={duration}"
            )
        line_count = len(self.lines)
        invalid = sorted(
            {
                index
                for shot in self.shots
                for index in shot.dialogue_line_indices
                if index < 1 or index > line_count
            }
        )
        if invalid:
            raise ValueError(f"Shot dialogue indices are out of range: {invalid}")
        shot_assignments = [
            index
            for shot in self.shots
            for index in shot.dialogue_line_indices
        ]
        expected_line_indices = set(range(1, line_count + 1))
        duplicated_shot_lines = sorted(
            index for index, count in Counter(shot_assignments).items() if count > 1
        )
        missing_shot_lines = sorted(expected_line_indices - set(shot_assignments))
        if duplicated_shot_lines or missing_shot_lines:
            raise ValueError(
                "Shots must use every declared dialogue line exactly once: "
                f"duplicates={duplicated_shot_lines}, missing={missing_shot_lines}"
            )
        known_subjects = (
            {item.name for item in self.characters}
            | {item.name for item in self.locations}
            | {item.name for item in self.props}
        )
        unknown_subjects = sorted(
            {
                need.subject_name
                for shot in self.shots
                for need in shot.reference_needs
                if need.subject_name not in known_subjects
            }
        )
        if unknown_subjects:
            raise ValueError(
                "Reference needs use unknown production subjects: "
                + ", ".join(unknown_subjects)
            )
        if self.scenes:
            sequences = [scene.sequence for scene in self.scenes]
            if sequences != list(range(1, len(self.scenes) + 1)):
                raise ValueError("Scene sequences must be contiguous and 1-based")
            scene_assignments = [
                index
                for scene in self.scenes
                for index in scene.dialogue_line_indices
            ]
            invalid_scene_lines = sorted(
                {
                    index
                    for index in scene_assignments
                    if index < 1 or index > line_count
                }
            )
            duplicated_scene_lines = sorted(
                index
                for index, count in Counter(scene_assignments).items()
                if count > 1
            )
            missing_scene_lines = sorted(expected_line_indices - set(scene_assignments))
            if invalid_scene_lines or duplicated_scene_lines or missing_scene_lines:
                raise ValueError(
                    "Scenes must assign every declared dialogue line exactly once: "
                    f"invalid={invalid_scene_lines}, duplicates={duplicated_scene_lines}, "
                    f"missing={missing_scene_lines}"
                )
            known_sequences = {scene.sequence for scene in self.scenes}
            invalid_scene_refs = sorted(
                {
                    shot.scene_sequence
                    for shot in self.shots
                    if shot.scene_sequence not in known_sequences
                }
            )
            if invalid_scene_refs:
                raise ValueError(
                    f"Shots reference unknown scene sequences: {invalid_scene_refs}"
                )
        return self


@dataclass(frozen=True)
class ProductionPlanningResult:
    plan: ProductionPlan
    planner: str
    warnings: list[str]
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None


@dataclass(frozen=True)
class StoryArchitectureResult:
    outline: EpisodeOutline
    source: str
    warnings: list[str]


@dataclass(frozen=True)
class ScriptCritiqueResult:
    report: ScriptReviewReport | None
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class ScriptReviewRoutingResult:
    disposition: ScriptReviewDisposition
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class RevisionJudgeResult:
    recommendation: RevisionJudgeRecommendation | None
    warnings: list[str]


@dataclass(frozen=True)
class StorySourceContractResult:
    contract: StorySourceContract
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryRepairPlanningResult:
    plan: StoryRepairPlan
    source_contract: StorySourceContract
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryRevisionVerificationResult:
    verification: StoryRevisionVerification | None
    used: bool
    warnings: list[str]


@dataclass(frozen=True)
class StoryRevisionResult:
    outline: EpisodeOutline
    revised: bool
    warnings: list[str]
    repair_plan: StoryRepairPlan | None = None
    verification: StoryRevisionVerification | None = None
    source_contract: StorySourceContract | None = None


@dataclass(frozen=True)
class SceneDirectionResult:
    scene: SceneShotPlan
    repaired: bool
    used_fallback: bool
    warnings: list[str]


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


def _compact_error(error: BaseException, *, max_chars: int = 800) -> str:
    text = re.sub(r"\s+", " ", str(error)).strip()
    if "<!doctype html" in text.lower() or "<html" in text.lower():
        title = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE)
        detail = re.sub(r"<[^>]+>", " ", title.group(1)).strip() if title else ""
        text = "Provider returned an HTML error page"
        if detail:
            text += f": {detail}"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


_DIALOGUE_PUNCTUATION_PATTERN = re.compile(
    r"[\s，。！？；：、,.!?;:…—“”‘’\"'()（）《》【】\[\]{}]+"
)


def _spoken_character_count(text: str) -> int:
    """Return a rough speakable-character count for pacing evidence.

    This is deliberately descriptive rather than a validator: delivery, pauses,
    performance, and non-dialogue action still require semantic judgement.
    """

    return len(_DIALOGUE_PUNCTUATION_PATTERN.sub("", text))


def _scene_pacing_evidence(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
) -> dict[str, object]:
    lines = [outline.lines[index - 1] for index in scene.dialogue_line_indices]
    characters = sum(_spoken_character_count(line.text) for line in lines)
    deliberate_seconds = round(characters / 3.0, 1)
    brisk_seconds = round(characters / 4.0, 1)
    return {
        "scene_sequence": scene.sequence,
        "heading": scene.heading,
        "target_duration_seconds": scene.target_duration_seconds,
        "dialogue_lines": len(lines),
        "speakable_characters": characters,
        "rough_spoken_seconds": {
            "deliberate_3_chars_per_second": deliberate_seconds,
            "brisk_4_chars_per_second": brisk_seconds,
        },
        "rough_non_dialogue_budget_after_deliberate_delivery": round(
            scene.target_duration_seconds - deliberate_seconds,
            1,
        ),
    }


def _outline_pacing_evidence(outline: EpisodeOutline) -> dict[str, object]:
    scenes = [_scene_pacing_evidence(outline, scene) for scene in outline.scenes]
    characters = sum(_spoken_character_count(line.text) for line in outline.lines)
    deliberate_seconds = round(characters / 3.0, 1)
    brisk_seconds = round(characters / 4.0, 1)
    return {
        "purpose": (
            "Rough descriptive evidence for semantic pacing review; never a numerical "
            "pass/fail threshold. Inspect the actual dialogue, visible actions, and state changes."
        ),
        "target_duration_seconds": outline.target_duration_seconds,
        "dialogue_lines": len(outline.lines),
        "speakable_characters": characters,
        "rough_spoken_seconds": {
            "deliberate_3_chars_per_second": deliberate_seconds,
            "brisk_4_chars_per_second": brisk_seconds,
        },
        "rough_non_dialogue_budget_after_deliberate_delivery": round(
            outline.target_duration_seconds - deliberate_seconds,
            1,
        ),
        "scenes": scenes,
    }


_SCRIPT_METADATA_LABELS = {
    "标题",
    "片名",
    "类型",
    "题材",
    "角色",
    "人物",
    "角色设定",
    "人物设定",
    "主要角色",
    "登场人物",
    "场景",
    "核心场景",
    "地点",
    "时间",
    "核心道具",
    "道具",
    "要求",
    "制作要求",
    "镜头要求",
    "备注",
    "梗概",
    "简介",
    "结尾",
    "结尾明确",
    "结局",
}
_ACTION_PREFIX_PATTERN = re.compile(
    r"^([\u4e00-\u9fffA-Za-z·•_-]{2,12}?)(?="
    r"低声说|轻声说|冷冷地说|回答|回应|反问|追问|开口|"
    r"右手|左手|抬|看|说|问|答|握|拿|走|转|坐|站|把|将|听|笑|低声|"
    r"轻声|冷冷|突然|缓缓|盯|望|伸|推|拉|沉默|呼吸|皱眉|点头|摇头)"
)


def _split_label(source_line: str) -> tuple[str, str] | None:
    label, separator, value = source_line.partition("：")
    if not separator:
        label, separator, value = source_line.partition(":")
    if not separator:
        return None
    return label.strip(), value.strip()


def _is_metadata_label(value: str) -> bool:
    compact = re.sub(r"[\s.、()（）_-]", "", value)
    if compact in _SCRIPT_METADATA_LABELS:
        return True
    return re.fullmatch(
        r"(?:核心|主要|关键|整体|制作)?"
        r"(?:场景|地点|道具|设定|规则|冲突|悬念|反转|结尾|结局|主题|风格|时长|语言|说明|要求)",
        compact,
    ) is not None


def _is_voiceover_prefix(value: str) -> bool:
    if any(marker in value for marker in ("旁白", "画外", "内心", "传出", "声音响起")):
        return True
    return re.search(r"(?:录音(?:笔)?|电话|广播|设备).{0,8}(?:中|里|播放)", value) is not None


def _declared_character_names(source_lines: list[str]) -> list[str]:
    names: list[str] = []
    for source_line in source_lines:
        split = _split_label(source_line)
        if split is None:
            continue
        label, value = split
        if label in {"角色", "人物", "角色设定", "人物设定", "主要角色", "登场人物"}:
            for match in re.finditer(
                r"(?:^|[。；;])\s*([\u4e00-\u9fffA-Za-z·•_-]{2,16})\s*[，,]\s*"
                r"(?:\d{1,2}\s*岁|男|女)",
                value,
            ):
                names.append(match.group(1).strip())
            if not names:
                for item in re.split(r"[、/，,；;]", value):
                    candidate = item.strip().split()[0] if item.strip() else ""
                    if 1 < len(candidate) <= 16 and not _is_metadata_label(candidate):
                        names.append(candidate)
        elif (
            1 < len(label) <= 16
            and not _is_metadata_label(label)
            and not any(character.isspace() for character in label)
            and not _is_voiceover_prefix(label)
        ):
            action = _ACTION_PREFIX_PATTERN.match(label)
            names.append(action.group(1) if action is not None else label)
    return list(dict.fromkeys(names))


def _quoted_dialogue(source_line: str) -> tuple[str, str] | None:
    for opening, closing in (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')):
        start = source_line.find(opening)
        end = source_line.rfind(closing)
        if start >= 0 and end > start:
            prefix = source_line[:start].rstrip("：:，, \t")
            return prefix, source_line[start + len(opening) : end].strip()
    return None


def _mentioned_character(
    value: str,
    character_names: list[str],
    *,
    prefer_last: bool,
) -> str | None:
    mentions = [
        ((value.rfind(name) if prefer_last else value.find(name)), name)
        for name in character_names
        if (value.rfind(name) if prefer_last else value.find(name)) >= 0
    ]
    if not mentions:
        return None
    return (max(mentions) if prefer_last else min(mentions))[1]


def _voiceover_speaker(prefix: str, character_names: list[str]) -> str:
    if "旁白" in prefix:
        return "旁白"
    name = _mentioned_character(prefix, character_names, prefer_last=True)
    if name is None:
        return "画外音"
    if "录音笔" in prefix:
        return f"录音笔中的{name}"
    if "录音" in prefix:
        return f"录音中的{name}"
    if "电话" in prefix:
        return f"电话中的{name}"
    if "广播" in prefix:
        return f"广播中的{name}"
    if "内心" in prefix:
        return f"{name}内心"
    return f"画外音·{name}"


def _dialogue_speaker(prefix: str, character_names: list[str]) -> str | None:
    normalized = prefix.strip().lstrip("-—•0123456789.、 ")
    split = _split_label(normalized)
    if split is not None:
        normalized = split[0]
    if _is_metadata_label(normalized):
        return None
    if _is_voiceover_prefix(normalized):
        return _voiceover_speaker(normalized, character_names)
    latest_clause = re.split(r"[。！？!?；;]", normalized)[-1].strip()
    known = _mentioned_character(
        latest_clause,
        character_names,
        prefer_last=False,
    )
    if known is not None:
        return known
    match = _ACTION_PREFIX_PATTERN.match(normalized)
    if match is not None:
        return match.group(1)
    if 1 < len(normalized) <= 16 and not any(character.isspace() for character in normalized):
        return normalized
    return None


def _locked_dialogue(input_text: str) -> list[PlannedDialogueLine]:
    source_lines = [line.strip() for line in input_text.splitlines() if line.strip()]
    character_names = _declared_character_names(source_lines)
    result: list[PlannedDialogueLine] = []
    for source_line in source_lines:
        quoted = _quoted_dialogue(source_line)
        if quoted is not None:
            prefix, text = quoted
            speaker = _dialogue_speaker(prefix, character_names)
            if speaker is None or not text:
                continue
            audio_kind = (
                AudioKind.VOICEOVER
                if _is_voiceover_prefix(prefix)
                else AudioKind.DIALOGUE
            )
            result.append(
                PlannedDialogueLine(
                    speaker=speaker,
                    text=text,
                    audio_kind=audio_kind,
                )
            )
            continue
        split = _split_label(source_line)
        if split is None:
            continue
        label, text = split
        speaker = _dialogue_speaker(label, character_names)
        if speaker is None or not text:
            continue
        is_known_speaker = label in character_names or speaker in character_names
        is_voiceover = _is_voiceover_prefix(label)
        if not is_known_speaker and not is_voiceover and character_names:
            continue
        result.append(
            PlannedDialogueLine(
                speaker=speaker,
                text=text.strip("“”\"' "),
                audio_kind=(AudioKind.VOICEOVER if is_voiceover else AudioKind.DIALOGUE),
            )
        )
    return result


def _character_name_covered(name: str, known_names: set[str]) -> bool:
    if name in known_names:
        return True
    group_suffixes = ("群", "们", "众", "人群", "团队", "一家")
    return any(
        known.startswith(name) and known[len(name) :] in group_suffixes
        for known in known_names
    )


def _planner_image_urls(request: CreateProjectRequest) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    warnings: list[str] = []
    for index, image in enumerate(request.input_images, start=1):
        if image.external_url is not None:
            urls.append(image.external_url)
            continue
        assert image.local_path is not None
        path = Path(image.local_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Input image does not exist: {path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            warnings.append(
                f"输入图片 {index} 超过 20 MB，已作为项目资产导入，但未发送给规划模型。"
            )
            continue
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            raise ValueError(f"Input seed is not an image: {path}")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        urls.append(f"data:{mime_type};base64,{encoded}")
    return urls, warnings


def _planner_prompt(request: CreateProjectRequest) -> tuple[str, str]:
    schema = ProductionPlan.model_json_schema()
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.PLANNER,
        ),
        context="\n".join(
            [
                request.name or "",
                request.input_text,
                *(item.descriptor or "" for item in request.input_images),
            ]
        ),
    )
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are the lead writer, production designer, production planner, and continuity planner "
        "for a Mandarin live-action vertical video. Return JSON only and match the supplied schema "
        f"exactly. {skill_prompt.text} Produce one coherent "
        f"video targeting {request.resolved_target_duration_seconds():g} seconds. Editorial shots "
        "may be shorter than 4 seconds or longer than 15 seconds when the material earns it. The "
        "current H3 provider accepts only integer 4-15 second generation requests, but that is a "
        "later execution-unit constraint: the execution planner may generate extra handles for a "
        "short edit or compile a long take into continuous provider segments. Never split an "
        "editorial shot merely to satisfy the provider boundary. Choose shot count and duration "
        "from the material rather than a fixed formula. Do not stretch a shot or combine several "
        "risky contacts merely to fill a provider request. Keep character, wardrobe, location "
        "geometry, props, screen direction, "
        "eyelines, and action states continuous across shots. Avoid difficult multi-contact actions "
        "when the same narrative beat can be covered with simpler shots. Do not add fields outside "
        "the schema. For SCRIPT_LOCKED, preserve every supplied speaker and dialogue line verbatim "
        "and in order; only plan production around them. Images are visual references, not proof of "
        "facts that are not actually visible. For every shot, populate reference_needs using only "
        "declared character, location, and prop names. Request the smallest useful coverage: a "
        "location Master Wide plus only the reverse, interaction zone, character angle, framing, or "
        "prop state that this shot actually needs. Use stable semantic tags for named location zones "
        "and states so an asset manager can retrieve or derive matching views. Character appearance "
        "must contain stable identity traits only: age impression, face, hair, body build, and other "
        "cross-shot visual anchors. Put sweat, dirt, injury, illness, distress, damaged makeup, and "
        "other temporary story states in the relevant shot or continuity fields, never in the base "
        "character appearance or wardrobe. Prop appearance must likewise describe only stable visible "
        "form, material, color, scale, controls, and construction. Put who carries or uses it, illegal "
        "or destructive actions, evidence meaning, and temporary opened, broken, dirty, wet, or damaged "
        "states in scene actions or continuity, never in the base prop appearance."
    )
    image_descriptions = [
        {
            "index": index,
            "descriptor": image.descriptor,
            "filename": Path(image.local_path).name if image.local_path else None,
            "url": image.external_url,
        }
        for index, image in enumerate(request.input_images, start=1)
    ]
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "project_name": request.name,
        "text_input": request.input_text.strip(),
        "locked_dialogue": [
            line.model_dump(mode="json") for line in locked
        ],
        "image_references": image_descriptions,
        "requirements": {
            "medium": production_skill(request.production_profile).label,
            "language": "普通话中文",
            "aspect_ratio": "9:16",
            "duration_seconds": request.resolved_target_duration_seconds(),
            "shot_duration_seconds": (
                "editorial duration follows the material; H3 later compiles each provider request "
                "to an integer 4-15 seconds"
            ),
            "dialogue_lines": "follow the material; silence is valid; preserve locked dialogue",
            "shots": "choose the smallest coherent executable set; no fixed count",
            "reference_needs": "minimal per-shot character/location/prop coverage",
        },
        "output_schema": schema,
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _planner_repair_prompt(
    request: CreateProjectRequest,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.PLANNER,
        ),
        context="\n".join((request.input_text, invalid_output)),
    )
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are repairing a previously invalid production-plan JSON for a Mandarin live-action "
        "vertical video. Return one complete corrected JSON object only and match the "
        "supplied ProductionPlan schema exactly. Preserve the story and shot intent where valid. "
        "Dialogue indices are strictly 1-based and may only reference the supplied lines. For "
        "SCRIPT_LOCKED, lines must exactly match locked_dialogue in speaker, text, audio_kind, and "
        "order. Characters must be actual story characters present in the source script; children, "
        "older people, and intentionally non-human characters are valid. Metadata labels, action "
        "descriptions, props, locations, devices, and incidental voiceover sources must never become "
        "character names. A recorded or off-screen voice is VOICEOVER unless the story intentionally "
        "treats its source as a character. Preserve the active creative policy while repairing structure. "
        f"{skill_prompt.text} Do not add "
        "fields outside the schema."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "validation_error": str(error),
        "invalid_plan_json": invalid_output,
        "output_schema": ProductionPlan.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _outline_prompt(request: CreateProjectRequest) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.PLANNER,
        ),
        context="\n".join(
            [
                request.name or "",
                request.input_text,
                *(item.descriptor or "" for item in request.input_images),
            ]
        ),
    )
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are the story-architect Agent for a Mandarin live-action vertical production. "
        "Return JSON only and match EpisodeOutline exactly. Produce a compact global blueprint, "
        "not the finished screenplay: define the causal story, global cast, reusable locations "
        "and props, then split it into coherent scene nodes. A downstream Scene Writer expands "
        "each node separately. Do not output shots, camera moves, keyframes, or provider prompts. "
        f"{skill_prompt.text} The user target is "
        f"{request.resolved_target_duration_seconds():g} seconds. Scene count and duration must "
        "follow the material; prefer scene-sized contexts that a downstream director can expand "
        "without receiving the whole prior screenplay. Each scene contract must state its story "
        "purpose, locations, a short list of playable_actions, and compact continuity input/output including "
        "character state, prop state, known facts, and open questions. Keep the causal question "
        "chain alive across scenes without adding beats mechanically. The final causal mechanism "
        "must retrospectively explain the strongest opening anomaly itself, not merely a nearby "
        "event. Establish any technical, institutional, or procedural capability before the plot "
        "depends on it; simplify the mechanism when a grounded explanation would otherwise require "
        "invented powers. Keep distinct outcomes causally separate unless the story establishes why one "
        "resolves another. Causality needed by the audience must appear in shootable scene actions, "
        "props, screens, or dialogue rather than only in hidden continuity notes. Rebalance scene "
        "duration when a scene contains too many distinct conclusions or too little playable action. "
        "Do not treat scene-duration arithmetic as proof that the episode contains enough story; "
        "the declared dialogue and shootable actions must plausibly earn the requested runtime under "
        "the active profile. "
        "Keep reusable asset briefs production-neutral: character appearance contains stable identity "
        "and wardrobe only, while prop appearance contains stable visible construction only. Put actors, "
        "uses, evidence meaning, and temporary opened, broken, dirty, wet, or damaged states in scene "
        "actions and continuity rather than the base asset description. "
        "For AUTO_ORIGINAL and IDEA_EXPANSION, return lines=[] and give every scene an empty "
        "dialogue_line_indices list; the Scene Writer owns final dialogue. For SCRIPT_LOCKED, copy "
        "every supplied speaker, text, audio_kind, and order verbatim, and assign each 1-based line "
        "index to exactly one scene. A recorded voice is VOICEOVER and need not "
        "become a visible character. Use only declared location names in scene contracts. Do not "
        "add fields outside the schema."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "project_name": request.name,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "text_input": request.input_text.strip(),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "image_references": [
            {
                "index": index,
                "descriptor": image.descriptor,
                "filename": Path(image.local_path).name if image.local_path else None,
                "url": image.external_url,
            }
            for index, image in enumerate(request.input_images, start=1)
        ],
        "handoff_contract": {
            "story_architect_owns": "story, assets, scenes, dialogue allocation, expected states",
            "scene_director_owns": "shot execution inside one scene",
            "continuity": "compact state summaries, not accumulated prior-shot prose",
        },
        "output_schema": EpisodeOutline.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _outline_repair_prompt(
    request: CreateProjectRequest,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    instructions, source = _outline_prompt(request)
    payload = json.loads(source)
    payload["validation_error"] = str(error)
    payload["invalid_outline_json"] = invalid_output
    return (
        instructions
        + " Repair the supplied invalid outline while preserving valid creative decisions. "
        "Return one complete corrected EpisodeOutline JSON object only.",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _story_critic_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
        context="\n".join((request.input_text, outline.model_dump_json())),
    )
    instructions = (
        "You are an independent script-critic Agent reviewing a Mandarin live-action vertical "
        "episode before shot planning. Return JSON only and match ScriptReviewReport exactly. "
        "Review the actual outline rather than checking it against a hidden answer key. Do not "
        "assume every story needs more twists, dialogue, scenes, or cuts. Identify only specific, "
        "evidence-backed problems in causal logic, stated world rules, character motivation, "
        "setup/payoff, unresolved threads, scene utility, information repetition, dialogue function, "
        "or pacing relative to the requested duration and production profile. A deliberate mystery "
        "is not a plot hole when the episode pays it off or clearly leaves it as an intentional "
        "continuing question. Recommendations are advisory and must not prescribe fixed beat counts. "
        "Report observations only: requires_revision is your advisory opinion for compatibility, "
        "not authority to control the production loop. A separate Review Router Agent under the "
        "active Production Skill decides whether an issue belongs to story revision or a downstream "
        "tool. Do not inflate the screenplay with explanation for a problem whose real remedy is "
        "staging, asset coverage, generation, or editing. "
        "For SCRIPT_LOCKED, never recommend rewriting supplied dialogue; recommend changes around it. "
        "Do not approve an episode merely because its scene durations add up to the target. Use the "
        "supplied pacing evidence as a rough audit aid, then inspect whether each scene contains "
        "explicit playable changes that earn its remaining time. The evidence is not a numerical "
        "gate and cannot prove a defect by itself. "
        f"{skill_prompt.text}"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "episode_outline": outline.model_dump(mode="json"),
        "pacing_evidence": _outline_pacing_evidence(outline),
        "output_schema": ScriptReviewReport.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _story_critic_repair_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    instructions, source = _story_critic_prompt(request, outline)
    payload = json.loads(source)
    payload["validation_error"] = str(error)
    payload["invalid_script_review_json"] = invalid_output
    return (
        instructions
        + " Repair only the invalid JSON or schema structure. Preserve the review's substantive "
        "observations, severities, strengths, and recommendations; do not add new findings while "
        "repairing serialization. Return one complete ScriptReviewReport JSON object only.",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _review_router_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.REVIEW_ROUTER,),
        context="\n".join((outline.model_dump_json(), review.model_dump_json())),
    )
    instructions = (
        "You are the Review Router Agent inside an AI-native production harness. The Script Critic "
        "has supplied observations, not a gate. Return JSON only and match ScriptReviewDisposition "
        "exactly. Inspect the actual outline and route every review issue, using its 1-based list "
        "index, to the smallest owner that can address it. Choose STORY_ARCHITECT only when the "
        "remedy must change episode intent, causality, motivation, setup/payoff, scene purpose, "
        "dialogue allocation, or playable duration allocation. Choose SCENE_DIRECTOR for shot design, "
        "blocking, performance direction, spatial presentation, or coverage; ASSET_AGENT for missing "
        "or inconsistent character, location, prop, or reference assets; VIDEO_DIRECTOR for model "
        "generation behavior or take-level motion; TIMELINE_AGENT for edit, subtitle, or timeline "
        "assembly; USER_OPTIONAL for taste or polish that need not be changed. Do not route by severity "
        "alone and do not repair downstream problems by asking the writer to add explanation. "
        "Set REVISE_STORY only when at least one routed STORY_ARCHITECT issue justifies another "
        "automatic story pass. Otherwise use CONTINUE_WITH_HANDOFF when any downstream or optional "
        "issues remain, or CONTINUE when there are no actionable issues. The recommendation is the "
        "default autonomous next action, but user_overridable must be true. "
        f"{skill_prompt.text}"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "episode_outline": outline.model_dump(mode="json"),
        "script_review": review.model_dump(mode="json"),
        "output_schema": ScriptReviewDisposition.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _revision_judge_prompt(
    request: CreateProjectRequest,
    *,
    older_revision_round: int,
    older_outline: EpisodeOutline,
    older_review: ScriptReviewReport,
    newer_revision_round: int,
    newer_outline: EpisodeOutline,
    newer_review: ScriptReviewReport,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
        context="\n".join(
            (
                request.input_text,
                older_outline.model_dump_json(),
                older_review.model_dump_json(),
                newer_outline.model_dump_json(),
                newer_review.model_dump_json(),
            )
        ),
    )
    instructions = (
        "You are an independent revision-judge Agent comparing two consecutive versions of a "
        "Mandarin live-action vertical episode after the latest Script Critic report showed a "
        "possible non-monotonic regression. Return JSON only and match "
        "RevisionJudgeRecommendation exactly. Recommend one supplied revision and name the other "
        "as the alternative. Do not rewrite either outline and do not assume the newer version is "
        "better. Issue counts and severity counts are evidence, not a score: inspect the actual "
        "outlines and each version's review. Compare causal closure, exact payoff of the opening "
        "hook, grounded technical or institutional rules, visible evidence, character motivation, "
        "scene utility, information density, and whether a repair introduced new contradictions. "
        "Explain concrete evidence and meaningful tradeoffs. Set advisory_only to true; the result "
        "must never block planning or silently replace the user's current outline. "
        f"{skill_prompt.text}"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "candidates": [
            {
                "revision_round": older_revision_round,
                "outline": older_outline.model_dump(mode="json"),
                "script_review": older_review.model_dump(mode="json"),
                "pacing_evidence": _outline_pacing_evidence(older_outline),
            },
            {
                "revision_round": newer_revision_round,
                "outline": newer_outline.model_dump(mode="json"),
                "script_review": newer_review.model_dump(mode="json"),
                "pacing_evidence": _outline_pacing_evidence(newer_outline),
            },
        ],
        "output_schema": RevisionJudgeRecommendation.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def story_revision_history_entry(
    *,
    revision_round: int,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
) -> dict[str, object]:
    """Build compact cross-round context without replaying every planning artifact."""

    return {
        "revision_round": revision_round,
        "title": outline.title,
        "story_summary": outline.story_summary,
        "scenes": [
            {
                "sequence": scene.sequence,
                "summary": scene.summary,
                "narrative_purpose": scene.narrative_purpose,
            }
            for scene in outline.scenes
        ],
        "critic_summary": review.summary,
        "critic_strengths": list(review.strengths),
        "critic_issues": [issue.model_dump(mode="json") for issue in review.issues],
    }


def _story_source_contract_prompt(
    request: CreateProjectRequest,
) -> tuple[str, str]:
    profile = production_skill(request.production_profile)
    instructions = (
        "You are the source-contract Agent for an AI-native video production system. Return JSON only "
        "and match StorySourceContract exactly. Extract what the user explicitly committed the story "
        "to, what the user explicitly prohibited, and which consequential choices remain open. Do not "
        "write or improve the story. Preserve plurality, named roles, required actions, causal order, "
        "ethical constraints, genre, duration, promised conflict scale, required outcome, and sequel hook "
        "when the source states them. Do not downgrade an explicit event into an optional suggestion. Do "
        "not invent commitments from genre conventions or from the active Production Skill. If two source "
        "clauses are genuinely incompatible, keep both commitments and name their reconciliation as an "
        "open creative choice. This is the initial compilation, so authoritative_user_overrides must be "
        "empty. The contract is advisory to the user but mandatory for autonomous Agents: "
        "user_overridable must remain true. "
        f"Active Production Skill: {profile.label}."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "input_image_count": len(request.input_images),
        "output_schema": StorySourceContract.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _fallback_story_source_contract(
    request: CreateProjectRequest,
) -> StorySourceContract:
    source = request.input_text.strip()
    if source:
        commitments = [source]
    else:
        commitments = [
            f"Create an original {request.production_profile.value} episode targeting "
            f"approximately {request.resolved_target_duration_seconds():g} seconds."
        ]
    return StorySourceContract(
        explicit_commitments=commitments,
        explicit_prohibitions=[],
        open_creative_choices=[
            "All story details not explicitly fixed by the supplied source remain editable."
        ],
    )


def compile_story_source_contract(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> StorySourceContractResult:
    """Extract explicit user promises independently from Writer and Critic."""

    fallback = _fallback_story_source_contract(request)
    if client is None:
        return StorySourceContractResult(
            contract=fallback,
            used=False,
            warnings=["Source Contract Agent 未配置，已把原始输入整体作为显式承诺。"],
        )
    instructions, input_text = _story_source_contract_prompt(request)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=5000,
        )
        return StorySourceContractResult(
            contract=StorySourceContract.model_validate_json(
                _strip_json_fence(output)
            ),
            used=True,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return StorySourceContractResult(
            contract=fallback,
            used=False,
            warnings=[
                "Source Contract Agent 不可用，已把原始输入整体作为显式承诺："
                + _compact_error(error)
            ],
        )


def _story_repair_plan_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    source_contract: StorySourceContract,
    revision_history: list[dict[str, object]] | None,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER, ProductionSkillRole.CRITIC),
        context="\n".join(
            (
                request.input_text,
                outline.model_dump_json(),
                review.model_dump_json(),
            )
        ),
    )
    instructions = (
        "You are the story-repair planner Agent working between Script Critic and Story Architect. "
        "Return JSON only and match StoryRepairPlan exactly. Do not rewrite the episode. Reassess "
        "every routed critic issue against the independent source contract and current outline instead of treating "
        "the critic as an answer key. Classify SOURCE_CONCEPT_GAP when the supplied premise itself "
        "leaves a pivotal audience-facing motive or rule unspecified and the outline had to invent a "
        "bridge; classify OUTLINE_DEFECT when the current outline creates the contradiction, weak "
        "motivation, overload, or regression; classify CRITIC_OVERREACH when the observation is not "
        "supported by the supplied text or would require camera, asset, generation, performance, or "
        "editing work rather than story repair. Critic overreach must set should_revise=false. Cover "
        "every supplied issue exactly once. Use prior rounds to preserve strengths and constraints that "
        "were already established, and explicitly guard against reintroducing issues that disappeared. "
        "Never delete, shrink, or silently replace an explicit source commitment merely to reduce production "
        "complexity; if a commitment is hard to stage, keep it and simplify its execution instead. A later "
        "authoritative_user_override takes precedence over any conflicting earlier commitment and the current "
        "user-edited outline must not be silently reverted. Recommend "
        "the smallest coherent causal repair. Prefer replacement, deletion, simplification, "
        "or time reallocation over accumulating explanations, evidence fragments, specialist rules, or "
        "new conveniences. A source-level gap still needs one concrete story choice, but do not pretend "
        "that choice came from the user. Keep the plan at premise, outline, scene-contract, and dialogue-"
        "economy level; do not prescribe shots. Recommendations are advisory and user_overridable must "
        "remain true. "
        f"{skill_prompt.text}"
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "current_outline": outline.model_dump(mode="json"),
        "current_pacing_evidence": _outline_pacing_evidence(outline),
        "routed_story_review": review.model_dump(mode="json"),
        "prior_revision_history": (revision_history or [])[-3:],
        "output_schema": StoryRepairPlan.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _fallback_story_repair_plan(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
) -> StoryRepairPlan:
    source_promise = request.input_text.strip() or outline.story_summary
    return StoryRepairPlan(
        summary=(
            "Repair Planner 不可用；按 Review Router 已确认的 Story Architect 范围形成最小修订计划。"
        ),
        source_promise=source_promise[:1200],
        invariants=list(review.strengths),
        actions=[
            StoryRepairAction(
                issue_index=index,
                diagnosis=StoryRepairDiagnosis.OUTLINE_DEFECT,
                should_revise=True,
                rationale="该问题已由 Review Router 路由到 Story Architect，但未完成独立来源诊断。",
                affected_scene_sequences=list(issue.scene_sequences),
                recommended_change=issue.recommendation,
                remove_or_simplify=[],
                preserve=list(review.strengths),
                validation_questions=[f"修订后是否直接消除了该观察：{issue.observation}"],
            )
            for index, issue in enumerate(review.issues, start=1)
        ],
    )


def _validate_story_repair_plan(
    review: ScriptReviewReport,
    outline: EpisodeOutline,
    plan: StoryRepairPlan,
) -> None:
    expected = set(range(1, len(review.issues) + 1))
    actual = {action.issue_index for action in plan.actions}
    if actual != expected:
        raise ValueError(
            "Story Repair Planner must diagnose every routed issue exactly once: "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    known_scenes = {scene.sequence for scene in outline.scenes}
    invalid_scenes = sorted(
        {
            sequence
            for action in plan.actions
            for sequence in action.affected_scene_sequences
            if sequence not in known_scenes
        }
    )
    if invalid_scenes:
        raise ValueError(
            "Story Repair Planner referenced unknown scenes: "
            + ", ".join(str(value) for value in invalid_scenes)
        )


def _story_revision_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    repair_plan: StoryRepairPlan | None = None,
    source_contract: StorySourceContract | None = None,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
        context="\n".join(
            (
                request.input_text,
                outline.model_dump_json(),
                review.model_dump_json(),
                repair_plan.model_dump_json() if repair_plan is not None else "",
            )
        ),
    )
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are the story-architect Agent revising an episode after an independent script review "
        "and a separate repair diagnosis. "
        "Return one complete EpisodeOutline JSON object only. Preserve working creative choices and "
        "apply only repair-plan actions with should_revise=true. Do not rewrite for actions diagnosed "
        "as CRITIC_OVERREACH. Treat the repair plan as an advisory change brief rather than permission "
        "to add every possible explanation. Every explicit source commitment is mandatory unless a later "
        "authoritative_user_override changed it; those overrides take precedence and the current user-edited "
        "outline must not be reverted. Otherwise, do not remove named or plural participants, shrink the promised conflict "
        "scale, omit a required event, reverse the required causal order, or replace the promised outcome "
        "merely to simplify production. Simplify how a commitment is staged, not whether it happens. "
        "Resolve causal contradictions, "
        "unmotivated choices, or missing setup/payoff at episode and scene-contract level; do not add "
        "shots, camera directions, fixed beat counts, or mechanical twists. Preserve production-neutral "
        "asset briefs: base character and "
        "prop appearance contain only stable visible identity or construction, while users, actions, "
        "evidence meaning, and temporary states remain in scenes and continuity. Treat HIGH findings as "
        "structural repair candidates: change the underlying setup, rule, motive, or consequence "
        "instead of only adding an explanation. A contradictory mechanism may be replaced or simplified "
        "rather than preserved because some surrounding scenes work. Prefer the smallest causal mechanism "
        "that makes the character's choice and human consequence immediately understandable. "
        "Confirm that the final mechanism exactly pays off the "
        "strongest opening anomaly, that pivotal choices have a visible benefit or constraint, and that "
        "technical or institutional actions use capabilities already established in the story. Do not "
        "move missing causality into continuity metadata; required evidence must remain visible in the "
        "scene contracts. Reduce or redistribute information when a scene cannot plausibly communicate "
        "its assigned conclusions within its duration. When the active profile's review identifies "
        "underfilled effective story duration, preserve the requested runtime by replacing "
        "empty atmosphere, transit, generic reactions, or repeated proof with causal events, active "
        "choices, setbacks, consequences, or payoffs that change story state. Do not repair density "
        "by merely lengthening pauses, explaining the same fact again, or mechanically adding cuts, "
        "dialogue, or unrelated twists. If a previous repair overfilled the runtime with explanatory detail, "
        "collapse it to the smallest plain-language mechanism the audience needs and dramatize the choice "
        "and human consequence instead. Keep distinct consequences separate unless the story establishes a "
        "causal link between them. "
        "Every dialogue line must "
        "still belong to exactly one scene. For SCRIPT_LOCKED, preserve every supplied speaker, text, "
        "audio_kind, and order verbatim. "
        f"{skill_prompt.text} Do not add fields outside the schema."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "current_outline": outline.model_dump(mode="json"),
        "current_pacing_evidence": _outline_pacing_evidence(outline),
        "script_review": review.model_dump(mode="json"),
        "story_repair_plan": (
            repair_plan.model_dump(mode="json") if repair_plan is not None else None
        ),
        "explicit_story_source_contract": (
            source_contract.model_dump(mode="json")
            if source_contract is not None
            else None
        ),
        "output_schema": EpisodeOutline.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _story_revision_repair_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    repair_plan: StoryRepairPlan | None,
    source_contract: StorySourceContract | None,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    instructions, source = _story_revision_prompt(
        request,
        outline,
        review,
        repair_plan,
        source_contract,
    )
    payload = json.loads(source)
    payload["validation_error"] = str(error)
    payload["invalid_revised_outline_json"] = invalid_output
    return (
        instructions
        + " Repair the supplied revised outline's JSON or schema structure while preserving its "
        "valid creative changes and the review-driven improvement. Return one complete corrected "
        "EpisodeOutline JSON object only.",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _story_revision_verifier_prompt(
    request: CreateProjectRequest,
    original_outline: EpisodeOutline,
    revised_outline: EpisodeOutline,
    review: ScriptReviewReport,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
) -> tuple[str, str]:
    profile = production_skill(request.production_profile)
    instructions = (
        "You are the story-revision verifier Agent. Return JSON only and match "
        "StoryRevisionVerification exactly. This is not another open-ended Script Critic and not a "
        "taste gate. Check only whether the revised outline actually satisfies each repair-plan action "
        "with should_revise=true, preserves the listed invariants and every explicit source commitment after "
        "applying any later authoritative_user_overrides, "
        "and avoids a new "
        "causal, state, timing, motivation, or information-density regression introduced by the edit. "
        "Trace important physical and resource states across scenes in ordinary audience terms. Do not "
        "request camera, asset, performance, generation, or editing changes. Do not reject for a merely "
        "different creative choice when it still solves the plan. Reject a revision that silently deletes "
        "or shrinks an explicit commitment that was not superseded by a user override, including plurality, required participants, conflict scale, "
        "required actions, causal order, or promised outcome. Every rejection must cite a concrete "
        "textual contradiction or an unmet plan action and give one minimal repair instruction. Mark all "
        "successfully handled repair issue indices in satisfied_issue_indices. Set accepted=true only "
        "when no concrete plan violation remains. The result is user-overridable. "
        f"Active Production Skill: {profile.label}."
    )
    payload = {
        "source_contract": {
            "source_mode": request.source_mode.value,
            "production_profile": request.production_profile.value,
            "target_duration_seconds": request.resolved_target_duration_seconds(),
            "source_text": request.input_text.strip(),
        },
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "original_outline": original_outline.model_dump(mode="json"),
        "revised_outline": revised_outline.model_dump(mode="json"),
        "original_pacing_evidence": _outline_pacing_evidence(original_outline),
        "revised_pacing_evidence": _outline_pacing_evidence(revised_outline),
        "routed_story_review": review.model_dump(mode="json"),
        "story_repair_plan": repair_plan.model_dump(mode="json"),
        "output_schema": StoryRevisionVerification.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _story_revision_targeted_repair_prompt(
    request: CreateProjectRequest,
    original_outline: EpisodeOutline,
    revised_outline: EpisodeOutline,
    review: ScriptReviewReport,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    verification: StoryRevisionVerification,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER,),
        context="\n".join(
            (
                request.input_text,
                revised_outline.model_dump_json(),
                repair_plan.model_dump_json(),
                verification.model_dump_json(),
            )
        ),
    )
    instructions = (
        "You are the Story Architect performing one bounded correction to a revised episode. Return one "
        "complete EpisodeOutline JSON object only. Change only what is needed to resolve the verifier's "
        "concrete violations. Preserve every repair-plan action already satisfied, every invariant, every "
        "explicit source commitment after applying authoritative user overrides, all "
        "unrelated creative choices, locked dialogue, and the target duration. Prefer deletion, replacement, "
        "or reallocation over adding explanations or mechanisms. Before returning, trace each affected "
        "character, prop, resource, and physical state across the cited scenes and answer every relevant "
        "validation question internally. Do not add shots or downstream production details. "
        f"{skill_prompt.text}"
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "original_outline": original_outline.model_dump(mode="json"),
        "current_revised_outline": revised_outline.model_dump(mode="json"),
        "routed_story_review": review.model_dump(mode="json"),
        "story_repair_plan": repair_plan.model_dump(mode="json"),
        "explicit_story_source_contract": source_contract.model_dump(mode="json"),
        "revision_verification": verification.model_dump(mode="json"),
        "output_schema": EpisodeOutline.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _user_story_revision_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    instruction: str,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.WRITER, ProductionSkillRole.PLANNER),
        context="\n".join((request.input_text, instruction, outline.model_dump_json())),
    )
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are the Story Architect editing the current episode at the user's or supervising "
        "Agent's request. Return one complete EpisodeOutline JSON object only. Apply the requested "
        "change at the smallest coherent story scope and preserve every unspecified premise, "
        "character identity, causal result, useful scene, asset contract, and duration target. "
        "Do not solve staging, camera, asset, generation, or edit problems by adding explanatory "
        "dialogue. When the instruction identifies causal or pacing defects, change the underlying "
        "action, motive, permission, consequence, scene allocation, or dialogue economy instead of "
        "adding prose. Keep base character and prop appearance production-neutral and limited to stable "
        "visible identity or construction; keep users, actions, evidence meaning, and temporary states "
        "inside scenes and continuity. Keep every dialogue line assigned to exactly one scene. For SCRIPT_LOCKED, "
        "preserve every supplied speaker, text, audio_kind, and order verbatim. This edit creates a "
        "new user-directed planning version; it is not a fixed approval gate. "
        f"{skill_prompt.text} Do not add fields outside the schema."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "production_profile": request.production_profile.value,
        "target_duration_seconds": request.resolved_target_duration_seconds(),
        "source_text": request.input_text.strip(),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "user_instruction": instruction,
        "current_outline": outline.model_dump(mode="json"),
        "current_pacing_evidence": _outline_pacing_evidence(outline),
        "output_schema": EpisodeOutline.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _user_story_revision_repair_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    instruction: str,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    instructions, source = _user_story_revision_prompt(
        request,
        outline,
        instruction,
    )
    payload = json.loads(source)
    payload["validation_error"] = str(error)
    payload["invalid_revised_outline_json"] = invalid_output
    return (
        instructions
        + " Repair only the invalid JSON or schema structure while preserving the requested "
        "creative edit. Return one complete corrected EpisodeOutline JSON object only.",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _scene_director_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff_in: SceneContinuitySummary,
    review_handoff: list[dict[str, object]] | None = None,
) -> tuple[str, str]:
    skill_prompt = compile_production_prompt(
        request.production_profile,
        roles=(ProductionSkillRole.PLANNER, ProductionSkillRole.DIRECTOR),
        context="\n".join(
            (
                request.input_text,
                scene.model_dump_json(),
                json.dumps(review_handoff or [], ensure_ascii=False),
            )
        ),
    )
    next_scene = next(
        (item for item in outline.scenes if item.sequence == scene.sequence + 1),
        None,
    )
    relevant_lines = [
        {
            "index": index,
            **outline.lines[index - 1].model_dump(mode="json"),
        }
        for index in scene.dialogue_line_indices
    ]
    instructions = (
        "You are a scene-director Agent. Return JSON only and match SceneShotPlan exactly. Expand "
        "only the supplied scene contract into editorial shots. A shot may be shorter than 4 "
        "seconds or longer than 15 seconds when its dramatic action and camera idea remain one "
        "continuous unit. H3's integer 4-15 second request boundary belongs to later execution "
        "planning and must not force a creative cut here. The story architect "
        "owns global facts, cast, locations, props, and dialogue; do not invent or rename them. "
        "Each assigned dialogue index must appear in exactly one shot and no other line index may "
        "appear. Keep every shot.scene_sequence and the top-level scene_sequence equal to the "
        "current scene. Use the previous scene's actual handoff as physical truth while still "
        "aiming for the architect's intended exit and the next scene's required entry. Do not copy "
        "all earlier shots into the plan; the compact handoff is the memory boundary. Long takes "
        "are allowed when they contain meaningful internal progression. Do not default to one "
        "dialogue line per shot: when consecutive lines share the same participants, axis, and "
        "simple action state, group them into one coherent dramatic beat and preserve "
        "room for visible reactions. Add a reaction insert only when it changes interpretation or "
        "continuity. This is a pacing recommendation, not a fixed shot-count rule. Do not stretch "
        "risky multi-contact action to fill duration. Do not turn the scene's rough non-dialogue "
        "budget into generic looking, waiting, walking, or repeated proof; use only actions already "
        "supported by the scene contract, and let each shot expose a change or necessary causal "
        "connection. Routed critic observations are advisory production notes: address only those "
        "owned by this or a later production layer, without changing story facts or adding explanatory "
        "dialogue. Populate minimal reference_needs using only names "
        "declared in the global assets and only for subjects actually visible in the shot. A "
        "character merely mentioned in dialogue, a prop explicitly left offscreen, or the next "
        "location named only by the outgoing transition must not become a visual reference need. "
        f"{skill_prompt.text} Do not add fields outside "
        "the schema."
    )
    payload = {
        "episode": {
            "title": outline.title,
            "story_summary": outline.story_summary,
            "target_duration_seconds": outline.target_duration_seconds,
            "style": outline.style.model_dump(mode="json"),
            "characters": [item.model_dump(mode="json") for item in outline.characters],
            "locations": [
                item.model_dump(mode="json")
                for item in outline.locations
                if item.name in scene.location_names
            ],
            "props": [item.model_dump(mode="json") for item in outline.props],
        },
        "scene_contract": scene.model_dump(mode="json"),
        "assigned_dialogue": relevant_lines,
        "scene_pacing_evidence": _scene_pacing_evidence(outline, scene),
        "previous_actual_handoff": handoff_in.model_dump(mode="json"),
        "routed_review_handoff": review_handoff or [],
        "next_scene": (
            {
                "heading": next_scene.heading,
                "narrative_purpose": next_scene.narrative_purpose,
                "expected_entry": next_scene.continuity_in.model_dump(mode="json"),
            }
            if next_scene is not None
            else None
        ),
        "output_schema": SceneShotPlan.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _scene_repair_prompt(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff_in: SceneContinuitySummary,
    review_handoff: list[dict[str, object]] | None,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    instructions, source = _scene_director_prompt(
        request,
        outline,
        scene,
        handoff_in,
        review_handoff,
    )
    payload = json.loads(source)
    payload["validation_error"] = str(error)
    payload["invalid_scene_plan_json"] = invalid_output
    return (
        instructions
        + " Repair the invalid scene plan without changing the scene contract. Return one complete "
        "corrected SceneShotPlan JSON object only.",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _validate_locked_outline(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
) -> None:
    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        return
    expected_lines = _locked_dialogue(request.input_text)
    expected = [(line.speaker, line.text, line.audio_kind) for line in expected_lines]
    actual = [(line.speaker, line.text, line.audio_kind) for line in outline.lines]
    if actual != expected:
        raise ValueError("Story architect changed or omitted locked dialogue")
    dialogue_speakers = {
        line.speaker for line in expected_lines if line.audio_kind == AudioKind.DIALOGUE
    }
    character_names = {character.name for character in outline.characters}
    missing = sorted(dialogue_speakers - character_names)
    if missing:
        raise ValueError("Story architect omitted speaking characters: " + ", ".join(missing))


def _validate_story_blueprint(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
) -> None:
    """Validate the common blueprint contract before scene-local writing begins."""

    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        _validate_locked_outline(request, outline)
        return
    if outline.lines:
        raise ValueError(
            "Story architect must leave unlocked dialogue to the scene-writer stage"
        )
    populated = [
        scene.sequence for scene in outline.scenes if scene.dialogue_line_indices
    ]
    if populated:
        raise ValueError(
            "Unlocked blueprint scenes must not pre-assign dialogue indices: "
            + ", ".join(str(sequence) for sequence in populated)
        )


def _validate_locked_dialogue(request: CreateProjectRequest, plan: ProductionPlan) -> None:
    if request.source_mode != SourceMode.SCRIPT_LOCKED:
        return
    expected_lines = _locked_dialogue(request.input_text)
    if not expected_lines:
        raise ValueError("Locked script did not contain recognizable dialogue")
    expected = [
        (line.speaker, line.text, line.audio_kind)
        for line in expected_lines
    ]
    actual = [
        (line.speaker, line.text, line.audio_kind)
        for line in plan.lines
    ]
    if actual != expected:
        raise ValueError("Planning model changed or omitted locked dialogue")
    dialogue_speakers = {
        line.speaker for line in expected_lines if line.audio_kind == AudioKind.DIALOGUE
    }
    character_names = {character.name for character in plan.characters}
    missing = sorted(dialogue_speakers - character_names)
    if missing:
        raise ValueError(
            "Planning model omitted speaking characters: " + ", ".join(missing)
        )
    invalid = sorted(
        name
        for name in character_names
        if _is_metadata_label(name) or name not in request.input_text
    )
    if invalid:
        raise ValueError(
            "Planning model invented invalid locked-script characters: "
            + ", ".join(invalid)
        )
    assigned = [
        index
        for shot in plan.shots
        for index in shot.dialogue_line_indices
    ]
    expected_indices = list(range(1, len(plan.lines) + 1))
    if sorted(assigned) != expected_indices or len(assigned) != len(set(assigned)):
        raise ValueError("Planning model duplicated or omitted locked dialogue in shots")


def _fallback_general_plan(request: CreateProjectRequest) -> ProductionPlan:
    actor = "林岚"
    premise = request.input_text.strip()
    if not premise and request.source_mode == SourceMode.IMAGE_SEEDED:
        premise = "人物在用户提供的视觉空间里度过一段安静但有方向变化的清晨。"
    if not premise:
        premise = "清晨，一名都市青年在窗边整理思绪，并在光线变化中作出一个平静决定。"
    shots = [
        PlannedShot(
            duration_seconds=12.0,
            narrative_purpose="建立人物、空间和清晨的观察节奏",
            visual=f"清晨城市室内大全景，{actor}在窗边安静停留，环境细节自然进入画面",
            framing="大全景",
            camera="稳定机位缓慢推近，不急于切换",
            audio_intent="城市远声、室内底噪和自然呼吸，无对白",
            character_positions=f"{actor}位于窗边画面右侧",
            eyeline="视线朝向窗外，不建立虚构对话关系",
            prop_state_in="桌面与窗边保持简洁，无叙事道具变化",
            prop_state_out="空间关系保持不变，人物注意力转向室内",
            action_in="人物静观窗外",
            action_out="人物缓慢收回视线",
            transition_out="自然切",
            merge_candidate=True,
            local_rework_risk="低",
        ),
        PlannedShot(
            duration_seconds=15.0,
            narrative_purpose="用连续表演呈现情绪从迟疑到松弛的变化",
            visual=f"中景连续跟随{actor}从窗边走到桌旁，停下整理衣袖并轻轻呼气",
            framing="中景",
            camera="克制侧向跟随，路径简单，保持人物完整动作",
            audio_intent="脚步、衣料摩擦、呼吸和持续环境声，无对白",
            character_positions=f"{actor}从画面右侧移动到中央桌旁",
            eyeline="视线由窗外转向桌面，方向连续",
            prop_state_in="桌面保持空净",
            prop_state_out="桌面保持空净",
            action_in="人物刚收回视线",
            action_out="人物在桌旁站稳并放松肩膀",
            transition_out="动作切",
            merge_candidate=False,
            local_rework_risk="低",
        ),
        PlannedShot(
            duration_seconds=12.0,
            narrative_purpose="让光线和人物微表情承担情绪信息",
            visual=f"{actor}近景，晨光逐渐变暖，人物看向室内后露出很轻的释然表情",
            framing="近景",
            camera="固定近景，仅做几乎不可察觉的靠近",
            audio_intent="环境声稍降，保留呼吸与远处车辆声，无对白",
            character_positions=f"{actor}保持在桌旁，背景窗线稳定",
            eyeline="视线保持向室内同一锚点",
            prop_state_in="无道具状态变化",
            prop_state_out="无道具状态变化",
            action_in="人物刚刚站稳",
            action_out="人物情绪由迟疑转为平静",
            transition_out="视线切",
            merge_candidate=True,
            local_rework_risk="低",
        ),
        PlannedShot(
            duration_seconds=11.0,
            narrative_purpose="以小幅决定动作结束观察段落",
            visual=f"回到稍宽中景，{actor}拉开椅子坐下，晨光留在空白墙面上，画面自然停留",
            framing="中景",
            camera="稳定机位，动作完成后留出短暂余韵",
            audio_intent="椅脚轻响、城市底噪和室内空气声，无对白",
            character_positions=f"{actor}从桌旁站姿转为坐姿，空间轴线不变",
            eyeline="视线落在桌面，不新增画外人物",
            prop_state_in="椅子位于桌边",
            prop_state_out="椅子被拉开后人物坐下",
            action_in="人物已恢复平静",
            action_out="人物坐定，段落完成",
            transition_out="淡出",
            merge_candidate=False,
            local_rework_risk="低",
        ),
    ]
    return ProductionPlan(
        title=request.name or "清晨留白",
        scene_heading="内景·城市窗边·清晨",
        scene_summary=premise,
        lines=[],
        characters=[
            PlannedCharacter(
                name=actor,
                age=28,
                appearance="真实自然的都市青年，面部特征清晰且易于跨镜保持",
                wardrobe="低饱和浅灰针织上衣与深色长裤，全片保持一致",
                performance="克制自然，以视线、呼吸、肩颈和微表情完成变化",
                voice_identity="S1",
            )
        ],
        locations=[
            PlannedLocation(
                name="清晨城市窗边",
                layout="单一室内空间，窗位于画面侧面，桌椅构成简单行动路径",
                lighting="柔和冷晨光逐渐转暖",
                palette="浅灰、米白与低饱和蓝色",
                key_views=["空间建立", f"{actor}窗边中景", f"{actor}近景"],
            )
        ],
        props=[],
        style=PlannedStyle(
            genre="都市生活观察",
            cinematography="9:16 写实电影感，允许留白和连续表演，运镜克制",
            color="低饱和清晨色调，光线缓慢变暖",
            texture="真实皮肤、自然布料和柔和窗光",
            forbidden=["动漫化", "过度磨皮", "无动机快切", "强造冲突", "无因果物体运动"],
        ),
        shots=shots,
    )


def _fallback_short_plan(request: CreateProjectRequest) -> ProductionPlan:
    if (
        request.production_profile == ProductionProfile.GENERAL_VIDEO
        and request.source_mode != SourceMode.SCRIPT_LOCKED
    ):
        return _fallback_general_plan(request)
    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        lines = _locked_dialogue(request.input_text)
        if not lines:
            raise ValueError("Locked script did not contain recognizable dialogue")
    else:
        lines = [
            PlannedDialogueLine(speaker="林岚", text="这件事，和你说的不一样。"),
            PlannedDialogueLine(speaker="顾川", text="你终于发现了。"),
            PlannedDialogueLine(speaker="林岚", text="那份记录是谁留下的？"),
            PlannedDialogueLine(speaker="顾川", text="是你自己。"),
            PlannedDialogueLine(speaker="林岚", text="可我从没来过这里。"),
        ]
    speakers = list(
        dict.fromkeys(
            line.speaker
            for line in lines
            if line.audio_kind == AudioKind.DIALOGUE and line.speaker != "旁白"
        )
    )
    while len(speakers) < 2:
        speakers.append("顾川" if speakers != ["顾川"] else "林岚")
    speakers = speakers[:3]
    characters = [
        PlannedCharacter(
            name=name,
            age=28 + index * 3,
            appearance="真实自然的都市青年，面部特征清晰且易于跨镜保持",
            wardrobe="低饱和都市通勤装，轮廓和颜色在全片保持一致",
            performance="克制、警觉，情绪随信息揭示逐步升级",
            voice_identity=f"S{index + 1}",
        )
        for index, name in enumerate(speakers)
    ]
    premise = request.input_text.strip()
    if not premise and request.source_mode == SourceMode.IMAGE_SEEDED:
        premise = "用户提供的视觉线索让两个人发现同一段记忆存在矛盾。"
    if not premise:
        premise = "深夜，一段不可能存在的记录让两个人开始怀疑彼此的记忆。"
    dialogue_slots = (2, 3, 4, 5, 7)
    dialogue_by_shot: dict[int, list[int]] = {number: [] for number in range(1, 9)}
    for index in range(1, len(lines) + 1):
        dialogue_by_shot[dialogue_slots[(index - 1) % len(dialogue_slots)]].append(index)
    first, second = speakers[0], speakers[1]
    shot_definitions = (
        (5.0, "建立异常空间与核心视觉线索", "夜色中的都市室内，镜头从环境线索推进到对峙的两个人", "大全景", "缓慢推近", "城市底噪与室内环境音", "空间与人物关系尚未揭示", "两人形成清晰对峙", "硬切", True, "低"),
        (7.0, "主角提出第一处矛盾", f"{first}近景，视线锁定{second}，背景保留核心视觉线索", "近景", "稳定机位，末段轻推", f"{first}对白", "主角压住情绪", "矛盾被明确说出", "反打", False, "中"),
        (6.0, "对手承认掌握更多信息", f"{second}反打，神情平静，手边出现关键记录", "中近景", "静止反打", f"{second}对白", "关键记录尚未被解释", "记录成为注意中心", "反打", False, "中"),
        (5.0, "把怀疑转向证据来源", f"{first}更近特写，短暂停顿后追问", "特写", "轻微推镜", f"{first}对白", "主角仍相信自己的记忆", "主角开始动摇", "动作切", False, "中"),
        (8.0, "关键记录揭示不可能的信息", "关键记录特写，信息出现时画面保持可读，人物不做复杂接触动作", "道具特写", "俯角微移", "对白与提示音形成声音桥", "记录处于未确认状态", "记录指向主角本人", "声音桥", False, "高"),
        (6.0, "给主角认知反应留出表演空间", f"{first}反应近景，呼吸和眼神变化清楚，不加入新动作", "近景反应", "缓慢靠近", "环境音、呼吸与短暂停顿", "主角刚看到反转证据", "主角作出决定", "视线切", True, "低"),
        (7.0, "对手完成最后的信息施压", f"{second}半侧近景，保持轴线并说出最后一句", "半侧近景", "轻微横移", f"{second}对白", "对手等待主角反应", "双方关系发生逆转", "动作切", False, "中"),
        (6.0, "以可拍摄的决定动作收束并留下悬念", f"{first}拿起关键记录离开画面，{second}留在原位，避免复杂转身接触", "中景", "跟随半步后停下", "脚步、门铃或城市环境音，无新增对白", "证据仍在两人之间", "证据由主角带走，关系悬置", "淡出", True, "中"),
    )
    shots = [
        PlannedShot(
            duration_seconds=definition[0],
            narrative_purpose=definition[1],
            visual=definition[2],
            framing=definition[3],
            camera=definition[4],
            audio_intent=definition[5],
            dialogue_line_indices=dialogue_by_shot[sequence],
            character_positions=f"{first}在轴线左侧，{second}在轴线右侧",
            eyeline="双方视线保持同一对话轴",
            prop_state_in=definition[6],
            prop_state_out=definition[7],
            action_in=definition[6],
            action_out=definition[7],
            transition_out=definition[8],
            merge_candidate=definition[9],
            local_rework_risk=definition[10],
        )
        for sequence, definition in enumerate(shot_definitions, start=1)
    ]
    return ProductionPlan(
        title=request.name or "记忆证词",
        scene_heading="内景·都市室内·深夜",
        scene_summary=premise,
        lines=lines,
        characters=characters,
        locations=[
            PlannedLocation(
                name="深夜都市室内",
                layout="单一主空间，两人隔着桌面或柜台对峙，出口位于画面侧后方",
                lighting="冷白主光与窗外蓝色城市反光",
                palette="冷灰、蓝黑，以关键记录为单一高饱和视觉锚点",
                key_views=["空间建立", f"{first}方向", f"{second}反打", "关键记录特写"],
            )
        ],
        props=[
            PlannedProp(name="关键记录", appearance="小型、轮廓清楚、跨镜容易保持的实体记录载体")
        ],
        style=PlannedStyle(
            genre="都市悬疑",
            cinematography="9:16 写实电影感，克制运镜，遵守对话轴线并优先自然表演",
            color="冷白室内光与蓝色夜景反光，高饱和色只用于叙事锚点",
            texture="真实皮肤、自然布料、可信的玻璃与金属反光",
            forbidden=["动漫化", "过度磨皮", "角色换脸", "无动机越轴", "无因果物体运动"],
        ),
        shots=shots,
    )


def _retime_shots(
    templates: list[PlannedShot],
    *,
    target_duration_seconds: float,
    scene_sequence: int,
    preferred_count: int | None = None,
) -> list[PlannedShot]:
    if not templates:
        raise ValueError("At least one shot template is required")
    requested_count = preferred_count if preferred_count is not None else len(templates)
    count = max(1, requested_count)
    if count == 1:
        selected = [templates[0]]
    elif count <= len(templates):
        selected = [
            templates[round(index * (len(templates) - 1) / (count - 1))]
            for index in range(count)
        ]
    else:
        selected = [templates[index % len(templates)] for index in range(count)]
    duration = round(target_duration_seconds / count, 3)
    shots = [
        template.model_copy(
            deep=True,
            update={
                "scene_sequence": scene_sequence,
                "duration_seconds": duration,
            },
        )
        for template in selected
    ]
    shots[-1].duration_seconds = round(
        target_duration_seconds - sum(shot.duration_seconds for shot in shots[:-1]),
        3,
    )
    return shots


def _fallback_scene_state(
    *,
    setting: str,
    scene_sequence: int,
    phase: str,
) -> SceneContinuitySummary:
    previous_fact = (
        []
        if scene_sequence == 1 and phase == "entry"
        else [f"第 {max(1, scene_sequence - (1 if phase == 'entry' else 0))} 场的发现已经发生"]
    )
    return SceneContinuitySummary(
        setting=setting,
        character_states=[f"主要人物进入第 {scene_sequence} 场的{phase}状态，身份与服装保持一致"],
        prop_states=["核心叙事道具沿用上一场已建立的持有者与可见状态"],
        known_facts=previous_fact,
        open_threads=(
            [f"第 {scene_sequence} 场需要推进的因果问题"]
            if phase == "entry"
            else [f"第 {scene_sequence + 1} 场需要回应的新问题"]
        ),
    )


def _distribute_line_indices(line_count: int, scene_count: int) -> list[list[int]]:
    buckets: list[list[int]] = [[] for _ in range(scene_count)]
    for offset, index in enumerate(range(1, line_count + 1)):
        bucket = min(scene_count - 1, offset * scene_count // max(1, line_count))
        buckets[bucket].append(index)
    return buckets


def _fallback_episode_outline(request: CreateProjectRequest) -> EpisodeOutline:
    base = _fallback_short_plan(request)
    target = request.resolved_target_duration_seconds()
    scene_count = 1 if target <= 90.0 else min(40, max(2, math.ceil(target / 75.0)))
    scene_duration = target / scene_count
    lines: list[PlannedDialogueLine] = list(base.lines)
    if scene_count > 1 and request.source_mode != SourceMode.SCRIPT_LOCKED:
        first = base.characters[0].name
        second = base.characters[min(1, len(base.characters) - 1)].name
        lines = []
        for sequence in range(1, scene_count + 1):
            lines.extend(
                [
                    PlannedDialogueLine(
                        speaker=first,
                        text=f"第{sequence}处线索，和我们最初知道的不一样。",
                    ),
                    PlannedDialogueLine(
                        speaker=second,
                        text=f"因为有人只让你看到第{sequence}层答案。",
                    ),
                    PlannedDialogueLine(
                        speaker=first,
                        text=f"那这一场留下的问题，究竟指向谁？",
                    ),
                    PlannedDialogueLine(
                        speaker=second,
                        text=f"先看完第{sequence}份记录，再决定相信谁。",
                    ),
                ]
            )
    line_buckets = _distribute_line_indices(len(lines), scene_count)
    arcs = (
        "用可辨认的异常建立本集核心问题",
        "让第一份证据改变人物对关系的判断",
        "用行动后果扩大冲突并暴露新的信息缺口",
        "让人物选择付出代价，推动关系重新站队",
        "揭示此前线索的另一层含义并形成反转",
        "让反转产生现实后果而不是停留在解释",
        "回收关键道具与人物承诺",
        "完成本集主要回答，同时留下下一集可追的问题",
    )
    setting = base.locations[0].name
    scenes = [
        PlannedSceneContract(
            sequence=sequence,
            heading=(
                base.scene_heading
                if scene_count == 1
                else f"{base.scene_heading}·段落 {sequence:02d}"
            ),
            summary=f"{base.scene_summary}；第 {sequence} 场沿因果链继续推进。",
            narrative_purpose=arcs[(sequence - 1) % len(arcs)],
            playable_actions=[
                f"人物因第 {sequence} 场的新信息改变当下选择",
                f"让这个选择立即产生可见后果并推向下一场",
            ],
            target_duration_seconds=(
                round(scene_duration, 3)
                if sequence < scene_count
                else round(target - round(scene_duration, 3) * (scene_count - 1), 3)
            ),
            dialogue_line_indices=line_buckets[sequence - 1],
            location_names=[setting],
            continuity_in=_fallback_scene_state(
                setting=setting,
                scene_sequence=sequence,
                phase="entry",
            ),
            continuity_out=_fallback_scene_state(
                setting=setting,
                scene_sequence=sequence,
                phase="exit",
            ),
        )
        for sequence in range(1, scene_count + 1)
    ]
    return EpisodeOutline(
        title=base.title,
        story_summary=base.scene_summary,
        target_duration_seconds=target,
        lines=lines,
        characters=base.characters,
        locations=base.locations,
        props=base.props,
        style=base.style,
        scenes=scenes,
    )


def _story_blueprint(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
) -> EpisodeOutline:
    """Project any valid outline into the shared scene-node writing contract."""

    if request.source_mode == SourceMode.SCRIPT_LOCKED:
        return outline.model_copy(deep=True)
    payload = outline.model_dump(mode="python")
    payload["lines"] = []
    for scene in payload["scenes"]:
        scene["dialogue_line_indices"] = []
    return EpisodeOutline.model_validate(payload)


def _fallback_directed_scene_plan(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff_in: SceneContinuitySummary,
) -> SceneShotPlan:
    base = _fallback_short_plan(request)
    preferred_count = 5 if request.production_profile == ProductionProfile.DOUYIN_SHORT_DRAMA else 4
    shots = _retime_shots(
        base.shots,
        target_duration_seconds=scene.target_duration_seconds,
        scene_sequence=scene.sequence,
        preferred_count=preferred_count,
    )
    for shot in shots:
        shot.dialogue_line_indices = []
        shot.narrative_purpose = f"{scene.narrative_purpose}；{shot.narrative_purpose}"
        shot.visual = f"{scene.heading}。{shot.visual}"
    for offset, line_index in enumerate(scene.dialogue_line_indices):
        shots[offset % len(shots)].dialogue_line_indices.append(line_index)
    shots[0].character_positions = "; ".join(handoff_in.character_states) or shots[0].character_positions
    shots[0].prop_state_in = "; ".join(handoff_in.prop_states) or shots[0].prop_state_in
    shots[-1].prop_state_out = "; ".join(scene.continuity_out.prop_states) or shots[-1].prop_state_out
    shots[-1].action_out = "; ".join(scene.continuity_out.open_threads) or shots[-1].action_out
    return SceneShotPlan(
        scene_sequence=scene.sequence,
        shots=shots,
        continuity_out=scene.continuity_out.model_copy(deep=True),
    )


def _validate_directed_scene_plan(
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    plan: SceneShotPlan,
) -> None:
    if plan.scene_sequence != scene.sequence:
        raise ValueError("Scene director returned the wrong scene sequence")
    if any(shot.scene_sequence != scene.sequence for shot in plan.shots):
        raise ValueError("Scene director shots reference another scene")
    assigned = [
        index
        for shot in plan.shots
        for index in shot.dialogue_line_indices
    ]
    expected = scene.dialogue_line_indices
    if sorted(assigned) != sorted(expected) or len(assigned) != len(set(assigned)):
        raise ValueError("Scene director changed, duplicated, or omitted assigned dialogue lines")
    duration = sum(shot.duration_seconds for shot in plan.shots)
    tolerance = max(10.0, scene.target_duration_seconds * 0.2)
    if abs(duration - scene.target_duration_seconds) > tolerance:
        raise ValueError(
            "Directed scene duration does not match its contract: "
            f"target={scene.target_duration_seconds}, shots={duration}"
        )
    known_subjects = (
        {item.name for item in outline.characters}
        | {item.name for item in outline.locations}
        | {item.name for item in outline.props}
    )
    unknown_subjects = sorted(
        {
            need.subject_name
            for shot in plan.shots
            for need in shot.reference_needs
            if need.subject_name not in known_subjects
        }
    )
    if unknown_subjects:
        raise ValueError(
            "Scene director reference needs use subjects outside the episode asset "
            "contract: " + ", ".join(unknown_subjects)
        )


def _assemble_production_plan(
    outline: EpisodeOutline,
    directed_scenes: list[SceneShotPlan],
) -> ProductionPlan:
    if [scene.scene_sequence for scene in directed_scenes] != [
        scene.sequence for scene in outline.scenes
    ]:
        raise ValueError("Directed scenes do not match the episode outline")
    shots = [shot for scene in directed_scenes for shot in scene.shots]
    return ProductionPlan(
        title=outline.title,
        scene_heading=outline.scenes[0].heading,
        scene_summary=outline.story_summary,
        target_duration_seconds=outline.target_duration_seconds,
        lines=outline.lines,
        characters=outline.characters,
        locations=outline.locations,
        props=outline.props,
        style=outline.style,
        scenes=outline.scenes,
        shots=shots,
    )


def _fallback_plan(request: CreateProjectRequest) -> ProductionPlan:
    outline = _fallback_episode_outline(request)
    directed: list[SceneShotPlan] = []
    handoff = outline.scenes[0].continuity_in.model_copy(deep=True)
    for scene in outline.scenes:
        plan = _fallback_directed_scene_plan(request, outline, scene, handoff)
        _validate_directed_scene_plan(outline, scene, plan)
        directed.append(plan)
        handoff = plan.continuity_out.model_copy(deep=True)
    return _assemble_production_plan(outline, directed)


def _normalize_single_pass_plan(
    request: CreateProjectRequest,
    plan: ProductionPlan,
) -> ProductionPlan:
    payload = plan.model_dump(mode="python")
    payload["target_duration_seconds"] = request.resolved_target_duration_seconds()
    shots = list(plan.shots)
    if not plan.scenes:
        for shot in shots:
            shot.scene_sequence = 1
        first = shots[0]
        last = shots[-1]
        location_name = plan.locations[0].name
        payload["scenes"] = [
            PlannedSceneContract(
                sequence=1,
                heading=plan.scene_heading,
                summary=plan.scene_summary,
                narrative_purpose="完成当前短片的完整叙事目的",
                target_duration_seconds=sum(shot.duration_seconds for shot in shots),
                dialogue_line_indices=list(range(1, len(plan.lines) + 1)),
                location_names=[location_name],
                continuity_in=SceneContinuitySummary(
                    setting=location_name,
                    character_states=[first.character_positions, first.action_in],
                    prop_states=[first.prop_state_in],
                    known_facts=[],
                    open_threads=[plan.scene_summary],
                ),
                continuity_out=SceneContinuitySummary(
                    setting=location_name,
                    character_states=[last.character_positions, last.action_out],
                    prop_states=[last.prop_state_out],
                    known_facts=[plan.scene_summary],
                    open_threads=[],
                ),
            ).model_dump(mode="python")
        ]
        payload["shots"] = [shot.model_dump(mode="python") for shot in shots]
    return ProductionPlan.model_validate(payload)


def create_story_architecture(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> StoryArchitectureResult:
    """Run only the long-form Story Architect stage, with a local fallback."""

    if client is None:
        return StoryArchitectureResult(
            outline=_story_blueprint(request, _fallback_episode_outline(request)),
            source="deterministic story architect fallback",
            warnings=["GPT-5.6-sol 未配置，Story Architect 已使用本地可复现大纲。"],
        )
    image_urls, image_warnings = _planner_image_urls(request)
    warnings = list(image_warnings)
    source = "gpt-5.6-sol story architect"
    instructions, input_text = _outline_prompt(request)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=14000,
        )
        try:
            outline = EpisodeOutline.model_validate_json(_strip_json_fence(output))
            _validate_story_blueprint(request, outline)
        except ValueError as first_error:
            repair_instructions, repair_input = _outline_repair_prompt(
                request,
                output,
                first_error,
            )
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=image_urls,
                max_output_tokens=14000,
            )
            outline = EpisodeOutline.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_story_blueprint(request, outline)
            source += " repaired"
            warnings.append(
                "故事统筹 Agent 首次输出未通过结构校验，已自动修复后采用："
                + _compact_error(first_error)
            )
    except (LanguageProviderError, ValueError) as error:
        outline = _story_blueprint(request, _fallback_episode_outline(request))
        source = "deterministic story architect fallback"
        warnings.append(
            "故事统筹 Agent 未通过，已使用本地可复现大纲并继续逐场规划："
            + _compact_error(error)
        )
    return StoryArchitectureResult(outline=outline, source=source, warnings=warnings)


def review_story_outline(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    client: LanguageProviderClient | None,
) -> ScriptCritiqueResult:
    """Run the independent script critic without blocking later planning."""

    if client is None:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=["GPT-5.6-sol 未配置，已跳过语义剧本审阅并保留结构校验。"],
        )
    instructions, input_text = _story_critic_prompt(request, outline)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=6000,
        )
        repair_warning: str | None = None
        try:
            report = ScriptReviewReport.model_validate_json(_strip_json_fence(output))
        except ValueError as first_error:
            repair_instructions, repair_input = _story_critic_repair_prompt(
                request,
                outline,
                output,
                first_error,
            )
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=[],
                max_output_tokens=6000,
            )
            report = ScriptReviewReport.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            repair_warning = (
                "剧本审阅 Agent 首次输出仅结构无效，已保留原审阅内容并修复 JSON："
                + _compact_error(first_error)
            )
        return ScriptCritiqueResult(
            report=report,
            used=True,
            warnings=[repair_warning] if repair_warning is not None else [],
        )
    except (LanguageProviderError, ValueError) as error:
        return ScriptCritiqueResult(
            report=None,
            used=False,
            warnings=[
                "剧本审阅 Agent 不可用，结构化大纲仍继续逐场规划："
                + _compact_error(error)
            ],
        )


def _fallback_review_disposition(
    review: ScriptReviewReport,
    *,
    rationale: str,
) -> ScriptReviewDisposition:
    if not review.issues:
        return ScriptReviewDisposition(
            action=ScriptReviewDispositionAction.CONTINUE,
            issue_routes=[],
            rationale=rationale,
        )
    return ScriptReviewDisposition(
        action=ScriptReviewDispositionAction.CONTINUE_WITH_HANDOFF,
        issue_routes=[
            ScriptReviewIssueRoute(
                issue_index=index,
                owner=ReviewRepairOwner.USER_OPTIONAL,
                rationale="路由 Agent 不可用时保留为可选建议，不自动扩大剧本修订范围。",
            )
            for index in range(1, len(review.issues) + 1)
        ],
        rationale=rationale,
    )


def _validate_review_disposition(
    review: ScriptReviewReport,
    disposition: ScriptReviewDisposition,
) -> None:
    expected = set(range(1, len(review.issues) + 1))
    actual = {route.issue_index for route in disposition.issue_routes}
    if actual != expected:
        raise ValueError(
            "Review Router must route every critic issue exactly once: "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    has_story_issue = any(
        route.owner == ReviewRepairOwner.STORY_ARCHITECT
        for route in disposition.issue_routes
    )
    if disposition.action == ScriptReviewDispositionAction.REVISE_STORY:
        if not has_story_issue:
            raise ValueError("REVISE_STORY has no Story Architect issue")
    elif has_story_issue:
        raise ValueError(
            "Story Architect issues require REVISE_STORY; use USER_OPTIONAL when revision is optional"
        )
    if not review.issues and disposition.action != ScriptReviewDispositionAction.CONTINUE:
        raise ValueError("A review without issues must continue")
    if review.issues and disposition.action == ScriptReviewDispositionAction.CONTINUE:
        raise ValueError("A review with routed issues must continue with handoff or revise story")


def route_story_review(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review: ScriptReviewReport,
    client: LanguageProviderClient | None,
) -> ScriptReviewRoutingResult:
    """Let the active Production Skill route critic observations to repair owners."""

    if not review.issues:
        return ScriptReviewRoutingResult(
            disposition=_fallback_review_disposition(
                review,
                rationale="Script Critic 未报告问题，继续进入逐场规划。",
            ),
            used=False,
            warnings=[],
        )
    if client is None:
        return ScriptReviewRoutingResult(
            disposition=_fallback_review_disposition(
                review,
                rationale="Review Router Agent 未配置，所有观察保留为可选下游建议。",
            ),
            used=False,
            warnings=[
                "GPT-5.6-sol 未配置，Review Router 未擅自触发剧本重写；"
                "Critic 观察已保留为可选建议。"
            ],
        )
    instructions, input_text = _review_router_prompt(request, outline, review)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=5000,
        )
        disposition = ScriptReviewDisposition.model_validate_json(
            _strip_json_fence(output)
        )
        _validate_review_disposition(review, disposition)
        return ScriptReviewRoutingResult(
            disposition=disposition,
            used=True,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return ScriptReviewRoutingResult(
            disposition=_fallback_review_disposition(
                review,
                rationale="Review Router Agent 输出不可用，观察保留为可选下游建议。",
            ),
            used=False,
            warnings=[
                "Review Router Agent 不可用，未自动扩大剧本修订范围："
                + _compact_error(error)
            ],
        )


def story_revision_review(
    review: ScriptReviewReport,
    disposition: ScriptReviewDisposition,
) -> ScriptReviewReport:
    """Return only findings explicitly owned by Story Architect."""

    indices = {
        route.issue_index
        for route in disposition.issue_routes
        if route.owner == ReviewRepairOwner.STORY_ARCHITECT
    }
    return review.model_copy(
        deep=True,
        update={
            "issues": [
                issue.model_copy(deep=True)
                for index, issue in enumerate(review.issues, start=1)
                if index in indices
            ],
            "requires_revision": bool(indices),
        },
    )


def scene_review_handoff(
    review: ScriptReviewReport | None,
    disposition: ScriptReviewDisposition | None,
    scene_sequence: int,
) -> list[dict[str, object]]:
    """Expose relevant routed observations to a scene Agent without rewriting the story."""

    if review is None or disposition is None:
        return []
    route_by_index = {
        route.issue_index: route for route in disposition.issue_routes
    }
    handoff: list[dict[str, object]] = []
    for index, issue in enumerate(review.issues, start=1):
        route = route_by_index.get(index)
        if route is None or route.owner in {
            ReviewRepairOwner.STORY_ARCHITECT,
            ReviewRepairOwner.USER_OPTIONAL,
        }:
            continue
        if issue.scene_sequences and scene_sequence not in issue.scene_sequences:
            continue
        handoff.append(
            {
                "issue_index": index,
                "owner": route.owner.value,
                "observation": issue.observation,
                "recommendation": issue.recommendation,
                "routing_rationale": route.rationale,
            }
        )
    return handoff


def judge_story_revisions(
    request: CreateProjectRequest,
    *,
    older_revision_round: int,
    older_outline: EpisodeOutline,
    older_review: ScriptReviewReport,
    newer_revision_round: int,
    newer_outline: EpisodeOutline,
    newer_review: ScriptReviewReport,
    client: LanguageProviderClient | None,
) -> RevisionJudgeResult:
    """Recommend between two reviewed outlines without mutating the current selection."""

    if older_revision_round >= newer_revision_round:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "Revision Judge 缺少按时间排序的两版大纲，已保留最新版继续规划。"
            ],
        )
    if client is None:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "GPT-5.6-sol 未配置，已跳过 Revision Judge；最新版大纲继续进入逐场规划。"
            ],
        )
    instructions, input_text = _revision_judge_prompt(
        request,
        older_revision_round=older_revision_round,
        older_outline=older_outline,
        older_review=older_review,
        newer_revision_round=newer_revision_round,
        newer_outline=newer_outline,
        newer_review=newer_review,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=4000,
        )
        recommendation = RevisionJudgeRecommendation.model_validate_json(
            _strip_json_fence(output)
        )
        candidate_rounds = {older_revision_round, newer_revision_round}
        returned_rounds = {
            recommendation.recommended_revision_round,
            recommendation.alternative_revision_round,
        }
        if returned_rounds != candidate_rounds:
            raise ValueError(
                "Revision Judge selected a revision outside the supplied candidates"
            )
        return RevisionJudgeResult(recommendation=recommendation, warnings=[])
    except (LanguageProviderError, ValueError) as error:
        return RevisionJudgeResult(
            recommendation=None,
            warnings=[
                "Revision Judge 不可用，未替用户改选版本；最新版大纲继续进入逐场规划："
                + _compact_error(error)
            ],
        )


def plan_story_repair(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review_report: ScriptReviewReport,
    client: LanguageProviderClient | None,
    *,
    source_contract: StorySourceContract | None = None,
    revision_history: list[dict[str, object]] | None = None,
) -> StoryRepairPlanningResult:
    """Diagnose Writer versus Critic responsibility before changing the outline."""

    contract_result = (
        compile_story_source_contract(request, client)
        if source_contract is None
        else StorySourceContractResult(
            contract=source_contract,
            used=True,
            warnings=[],
        )
    )
    resolved_source_contract = contract_result.contract
    fallback = _fallback_story_repair_plan(request, outline, review_report)
    if client is None:
        return StoryRepairPlanningResult(
            plan=fallback,
            source_contract=resolved_source_contract,
            used=False,
            warnings=[
                *contract_result.warnings,
                "Story Repair Planner 未配置，采用 routed review 的最小修订计划。",
            ],
        )
    instructions, input_text = _story_repair_plan_prompt(
        request,
        outline,
        review_report,
        resolved_source_contract,
        revision_history,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=7000,
        )
        plan = StoryRepairPlan.model_validate_json(_strip_json_fence(output))
        _validate_story_repair_plan(review_report, outline, plan)
        return StoryRepairPlanningResult(
            plan=plan,
            source_contract=resolved_source_contract,
            used=True,
            warnings=list(contract_result.warnings),
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRepairPlanningResult(
            plan=fallback,
            source_contract=resolved_source_contract,
            used=False,
            warnings=[
                *contract_result.warnings,
                "Story Repair Planner 不可用，采用 routed review 的最小修订计划："
                + _compact_error(error)
            ],
        )


def _validate_story_revision_verification(
    outline: EpisodeOutline,
    repair_plan: StoryRepairPlan,
    verification: StoryRevisionVerification,
) -> None:
    known_issue_indices = {action.issue_index for action in repair_plan.actions}
    satisfied = set(verification.satisfied_issue_indices)
    if not satisfied.issubset(known_issue_indices):
        raise ValueError("Story Revision Verifier returned an unknown repair issue index")
    required = {
        action.issue_index
        for action in repair_plan.actions
        if action.should_revise
    }
    if verification.accepted and not required.issubset(satisfied):
        raise ValueError(
            "Accepted story revision did not mark every required repair action satisfied"
        )
    known_scenes = {scene.sequence for scene in outline.scenes}
    invalid_scenes = sorted(
        {
            sequence
            for violation in verification.violations
            for sequence in violation.scene_sequences
            if sequence not in known_scenes
        }
    )
    if invalid_scenes:
        raise ValueError(
            "Story Revision Verifier referenced unknown scenes: "
            + ", ".join(str(value) for value in invalid_scenes)
        )


def verify_story_revision(
    request: CreateProjectRequest,
    original_outline: EpisodeOutline,
    revised_outline: EpisodeOutline,
    review_report: ScriptReviewReport,
    repair_plan: StoryRepairPlan,
    source_contract: StorySourceContract,
    client: LanguageProviderClient | None,
) -> StoryRevisionVerificationResult:
    """Verify Writer execution against its brief, not against a new creative rubric."""

    if client is None:
        return StoryRevisionVerificationResult(
            verification=None,
            used=False,
            warnings=["Story Revision Verifier 未配置，保留结构校验后的修订稿。"],
        )
    instructions, input_text = _story_revision_verifier_prompt(
        request,
        original_outline,
        revised_outline,
        review_report,
        repair_plan,
        source_contract,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=6000,
        )
        verification = StoryRevisionVerification.model_validate_json(
            _strip_json_fence(output)
        )
        _validate_story_revision_verification(
            revised_outline,
            repair_plan,
            verification,
        )
        return StoryRevisionVerificationResult(
            verification=verification,
            used=True,
            warnings=[],
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRevisionVerificationResult(
            verification=None,
            used=False,
            warnings=[
                "Story Revision Verifier 不可用，保留结构校验后的修订稿："
                + _compact_error(error)
            ],
        )


def revise_story_outline(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    review_report: ScriptReviewReport,
    client: LanguageProviderClient | None,
    *,
    revision_round: int,
    source_contract: StorySourceContract | None = None,
    revision_history: list[dict[str, object]] | None = None,
) -> StoryRevisionResult:
    """Apply one bounded Story Architect revision and preserve the prior outline on failure."""

    if client is None:
        return StoryRevisionResult(
            outline=outline,
            revised=False,
            warnings=[
                f"第 {revision_round} 轮剧本修订不可用，保留当前大纲与最近审阅建议。"
            ],
            source_contract=(
                source_contract or _fallback_story_source_contract(request)
            ),
        )
    repair_planning = plan_story_repair(
        request,
        outline,
        review_report,
        client,
        source_contract=source_contract,
        revision_history=revision_history,
    )
    repair_plan = repair_planning.plan
    resolved_source_contract = repair_planning.source_contract
    if not any(action.should_revise for action in repair_plan.actions):
        return StoryRevisionResult(
            outline=outline,
            revised=False,
            warnings=[
                *repair_planning.warnings,
                "Story Repair Planner 判定当前 routed findings 均不应触发剧本重写；"
                "已保留为可覆盖建议并继续下游规划。",
            ],
            repair_plan=repair_plan,
            source_contract=resolved_source_contract,
        )
    image_urls, image_warnings = _planner_image_urls(request)
    instructions, input_text = _story_revision_prompt(
        request,
        outline,
        review_report,
        repair_plan,
        resolved_source_contract,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=14000,
        )
        repair_warning: str | None = None
        try:
            revised = EpisodeOutline.model_validate_json(_strip_json_fence(output))
            _validate_locked_outline(request, revised)
        except ValueError as first_error:
            repair_instructions, repair_input = _story_revision_repair_prompt(
                request,
                outline,
                review_report,
                repair_plan,
                resolved_source_contract,
                output,
                first_error,
            )
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=image_urls,
                max_output_tokens=14000,
            )
            revised = EpisodeOutline.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_locked_outline(request, revised)
            repair_warning = (
                f"第 {revision_round} 轮剧本修订首次输出未通过结构校验，"
                "已自动修复后采用："
                + _compact_error(first_error)
            )
        verification_result = verify_story_revision(
            request,
            outline,
            revised,
            review_report,
            repair_plan,
            resolved_source_contract,
            client,
        )
        verification = verification_result.verification
        verification_warnings = list(verification_result.warnings)
        last_concrete_verification = verification
        targeted_repair_count = 0
        while (
            verification is not None
            and not verification.accepted
            and targeted_repair_count < 2
        ):
            targeted_instructions, targeted_input = (
                _story_revision_targeted_repair_prompt(
                    request,
                    outline,
                    revised,
                    review_report,
                    repair_plan,
                    resolved_source_contract,
                    verification,
                )
            )
            targeted_output = client.generate_text(
                instructions=targeted_instructions,
                input_text=targeted_input,
                image_urls=image_urls,
                max_output_tokens=14000,
            )
            targeted_revised = EpisodeOutline.model_validate_json(
                _strip_json_fence(targeted_output)
            )
            _validate_locked_outline(request, targeted_revised)
            targeted_repair_count += 1
            second_verification_result = verify_story_revision(
                request,
                outline,
                targeted_revised,
                review_report,
                repair_plan,
                resolved_source_contract,
                client,
            )
            verification_warnings.extend(second_verification_result.warnings)
            second_verification = second_verification_result.verification
            revised = targeted_revised
            verification = second_verification
            if second_verification is not None:
                last_concrete_verification = second_verification
        if (
            last_concrete_verification is not None
            and not last_concrete_verification.accepted
        ):
            return StoryRevisionResult(
                outline=outline,
                revised=False,
                warnings=[
                    *image_warnings,
                    *repair_planning.warnings,
                    *verification_warnings,
                    f"第 {revision_round} 轮修订在 {targeted_repair_count} 次有界定向修复后"
                    "仍未通过 Repair Plan 执行校验，已保留修订前大纲；"
                    "用户可覆盖或发起定向修改。",
                ],
                repair_plan=repair_plan,
                verification=last_concrete_verification,
                source_contract=resolved_source_contract,
            )
        targeted_repair_warning = (
            f"第 {revision_round} 轮 Writer 首稿偏离 Repair Plan，已按 Revision Verifier "
            f"的具体问题定向修复 {targeted_repair_count} 次。"
            if targeted_repair_count
            else None
        )
        return StoryRevisionResult(
            outline=revised,
            revised=True,
            warnings=[
                *image_warnings,
                *repair_planning.warnings,
                *verification_warnings,
                *([repair_warning] if repair_warning is not None else []),
                *(
                    [targeted_repair_warning]
                    if targeted_repair_warning is not None
                    else []
                ),
                f"剧本审阅 Agent 第 {revision_round} 轮提出 "
                f"{len(review_report.issues)} 项问题，Story Architect 已自动修订。",
            ],
            repair_plan=repair_plan,
            verification=verification,
            source_contract=resolved_source_contract,
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRevisionResult(
            outline=outline,
            revised=False,
            warnings=[
                *image_warnings,
                *repair_planning.warnings,
                f"第 {revision_round} 轮剧本修订不可用，"
                "保留当前大纲与最近审阅建议："
                + _compact_error(error),
            ],
            repair_plan=repair_plan,
            source_contract=resolved_source_contract,
        )


def revise_story_outline_from_instruction(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    instruction: str,
    client: LanguageProviderClient | None,
) -> StoryRevisionResult:
    """Create a user-directed outline version without entering a fixed workflow."""

    if client is None:
        return StoryRevisionResult(
            outline=outline,
            revised=False,
            warnings=["GPT-5.6-sol 未配置，无法执行用户要求的剧本 Agent 修改。"],
        )
    image_urls, image_warnings = _planner_image_urls(request)
    instructions, input_text = _user_story_revision_prompt(
        request,
        outline,
        instruction,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=14000,
        )
        repair_warning: str | None = None
        try:
            revised = EpisodeOutline.model_validate_json(_strip_json_fence(output))
            _validate_locked_outline(request, revised)
        except ValueError as first_error:
            repair_instructions, repair_input = _user_story_revision_repair_prompt(
                request,
                outline,
                instruction,
                output,
                first_error,
            )
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=image_urls,
                max_output_tokens=14000,
            )
            revised = EpisodeOutline.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_locked_outline(request, revised)
            repair_warning = (
                "用户定向剧本修改首次输出未通过结构校验，已自动修复后采用："
                + _compact_error(first_error)
            )
        return StoryRevisionResult(
            outline=revised,
            revised=True,
            warnings=[
                *image_warnings,
                *([repair_warning] if repair_warning is not None else []),
                "Story Architect 已按用户或监督 Agent 的指令创建新剧本版本。",
            ],
        )
    except (LanguageProviderError, ValueError) as error:
        return StoryRevisionResult(
            outline=outline,
            revised=False,
            warnings=[
                *image_warnings,
                "用户定向剧本修改不可用，保留当前版本：" + _compact_error(error),
            ],
        )


def direct_story_scene(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    scene: PlannedSceneContract,
    handoff: SceneContinuitySummary,
    client: LanguageProviderClient | None,
    review_handoff: list[dict[str, object]] | None = None,
) -> SceneDirectionResult:
    """Direct exactly one scene so callers can persist a recovery point after it."""

    if client is None:
        directed = _fallback_directed_scene_plan(request, outline, scene, handoff)
        _validate_directed_scene_plan(outline, scene, directed)
        return SceneDirectionResult(
            scene=directed,
            repaired=False,
            used_fallback=True,
            warnings=[
                f"场景 {scene.sequence:02d} 未调用语言模型，已使用本地可复现分镜。"
            ],
        )
    instructions, input_text = _scene_director_prompt(
        request,
        outline,
        scene,
        handoff,
        review_handoff,
    )
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=[],
            max_output_tokens=10000,
        )
        try:
            directed = SceneShotPlan.model_validate_json(_strip_json_fence(output))
            _validate_directed_scene_plan(outline, scene, directed)
            return SceneDirectionResult(
                scene=directed,
                repaired=False,
                used_fallback=False,
                warnings=[],
            )
        except ValueError as first_error:
            repair_instructions, repair_input = _scene_repair_prompt(
                request,
                outline,
                scene,
                handoff,
                review_handoff,
                output,
                first_error,
            )
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=[],
                max_output_tokens=10000,
            )
            directed = SceneShotPlan.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_directed_scene_plan(outline, scene, directed)
            return SceneDirectionResult(
                scene=directed,
                repaired=True,
                used_fallback=False,
                warnings=[
                    f"场景 {scene.sequence:02d} 导演 Agent 首次输出无效，"
                    "已局部修复后采用："
                    + _compact_error(first_error)
                ],
            )
    except (LanguageProviderError, ValueError) as error:
        directed = _fallback_directed_scene_plan(request, outline, scene, handoff)
        _validate_directed_scene_plan(outline, scene, directed)
        return SceneDirectionResult(
            scene=directed,
            repaired=False,
            used_fallback=True,
            warnings=[
                f"场景 {scene.sequence:02d} 导演 Agent 未通过，仅该场使用本地回退："
                + _compact_error(error)
            ],
        )


def assemble_multi_agent_planning_result(
    request: CreateProjectRequest,
    outline: EpisodeOutline,
    directed_scenes: list[SceneShotPlan],
    *,
    outline_source: str,
    critic_used: bool,
    revision_rounds: int,
    used_scene_repair: bool,
    used_scene_fallback: bool,
    warnings: list[str],
    review_report: ScriptReviewReport | None,
    review_disposition: ScriptReviewDisposition | None = None,
) -> ProductionPlanningResult:
    plan = _assemble_production_plan(outline, directed_scenes)
    _validate_locked_dialogue(request, plan)
    planner = f"multi-agent:{outline_source}"
    if critic_used:
        planner += " + script critic"
    if revision_rounds:
        planner += f" revised-{revision_rounds}x"
    planner += " + scene directors"
    if used_scene_repair:
        planner += " repaired"
    if used_scene_fallback:
        planner += " mixed-fallback"
    return ProductionPlanningResult(
        plan=plan,
        planner=planner,
        warnings=list(dict.fromkeys(warnings)),
        review_report=review_report,
        review_disposition=review_disposition,
    )


def _create_multi_agent_production_plan(
    request: CreateProjectRequest,
    client: LanguageProviderClient,
) -> ProductionPlanningResult:
    # Local import keeps the scene-writing Tool independent from the planner's
    # domain models while allowing this compatibility entrypoint to compose it.
    from short_drama.hierarchical_script import (
        assemble_written_scenes,
        write_story_scene,
    )
    from short_drama.script_documents import EpisodeScenePatch

    architecture = create_story_architecture(request, client)
    blueprint = architecture.outline
    warnings = list(architecture.warnings)
    written_scenes: list[EpisodeScenePatch] = []
    for scene in blueprint.scenes:
        writing = write_story_scene(
            request,
            blueprint,
            scene,
            written_scenes,
            client,
        )
        written_scenes.append(writing.patch)
        warnings.extend(writing.warnings)
    outline = assemble_written_scenes(blueprint, written_scenes)
    review_report: ScriptReviewReport | None = None
    review_disposition: ScriptReviewDisposition | None = None
    critic_used = False
    script_revision_rounds = 0
    revision_history: list[dict[str, object]] = []
    source_contract: StorySourceContract | None = None
    critique = review_story_outline(request, outline, client)
    review_report = critique.report
    critic_used = critique.used
    warnings.extend(critique.warnings)

    if review_report is not None:
        routing = route_story_review(request, outline, review_report, client)
        review_disposition = routing.disposition
        warnings.extend(routing.warnings)

    while (
        review_report is not None
        and review_disposition is not None
        and review_disposition.action == ScriptReviewDispositionAction.REVISE_STORY
        and script_revision_rounds < 3
    ):
        revision_review = story_revision_review(
            review_report,
            review_disposition,
        )
        revision = revise_story_outline(
            request,
            outline,
            revision_review,
            client,
            revision_round=script_revision_rounds + 1,
            source_contract=source_contract,
            revision_history=revision_history,
        )
        warnings.extend(revision.warnings)
        source_contract = revision.source_contract or source_contract
        if not revision.revised:
            break
        revision_history.append(
            story_revision_history_entry(
                revision_round=script_revision_rounds,
                outline=outline,
                review=revision_review,
            )
        )
        outline = revision.outline
        script_revision_rounds += 1
        critique = review_story_outline(request, outline, client)
        warnings.extend(critique.warnings)
        if critique.report is None:
            break
        review_report = critique.report
        critic_used = critic_used or critique.used
        routing = route_story_review(request, outline, review_report, client)
        review_disposition = routing.disposition
        warnings.extend(routing.warnings)
    if review_report is not None and review_report.issues:
        warnings.append(
            f"当前剧本审阅仍保留 {len(review_report.issues)} 项可选修改建议；"
            "已按当前 Production Skill 下放到对应制作层，不会阻断后续规划，"
            "用户可继续、编辑或忽略。"
        )

    directed_scenes: list[SceneShotPlan] = []
    handoff = outline.scenes[0].continuity_in.model_copy(deep=True)
    used_scene_fallback = False
    used_scene_repair = False
    for scene in outline.scenes:
        direction = direct_story_scene(
            request,
            outline,
            scene,
            handoff,
            client,
            scene_review_handoff(
                review_report,
                review_disposition,
                scene.sequence,
            ),
        )
        directed_scenes.append(direction.scene)
        handoff = direction.scene.continuity_out.model_copy(deep=True)
        warnings.extend(direction.warnings)
        used_scene_repair = used_scene_repair or direction.repaired
        used_scene_fallback = used_scene_fallback or direction.used_fallback

    return assemble_multi_agent_planning_result(
        request,
        outline,
        directed_scenes,
        outline_source=architecture.source,
        critic_used=critic_used,
        revision_rounds=script_revision_rounds,
        used_scene_repair=used_scene_repair,
        used_scene_fallback=used_scene_fallback,
        warnings=warnings,
        review_report=review_report,
        review_disposition=review_disposition,
    )


def create_production_plan(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> ProductionPlanningResult:
    if client is None:
        return ProductionPlanningResult(
            plan=_fallback_plan(request),
            planner="deterministic-multi-agent-fallback-v2",
            warnings=["GPT-5.6-sol 未配置，已使用可复现的本地规划回退。"],
        )
    if request.resolved_target_duration_seconds() > 90.0:
        return _create_multi_agent_production_plan(request, client)
    image_urls, image_warnings = _planner_image_urls(request)
    instructions, input_text = _planner_prompt(request)
    try:
        output = client.generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=8000,
        )
    except LanguageProviderError as exc:
        return ProductionPlanningResult(
            plan=_fallback_plan(request),
            planner="deterministic-fallback-v1",
            warnings=[
                *image_warnings,
                f"GPT-5.6-sol 规划未通过，已自动回退：{exc}",
            ],
        )
    try:
        plan = _normalize_single_pass_plan(
            request,
            ProductionPlan.model_validate_json(_strip_json_fence(output)),
        )
        _validate_locked_dialogue(request, plan)
        return ProductionPlanningResult(
            plan=plan,
            planner="gpt-5.6-sol",
            warnings=image_warnings,
        )
    except ValueError as first_error:
        repair_instructions, repair_input = _planner_repair_prompt(
            request,
            output,
            first_error,
        )
        try:
            repaired_output = client.generate_text(
                instructions=repair_instructions,
                input_text=repair_input,
                image_urls=image_urls,
                max_output_tokens=8000,
            )
            repaired_plan = _normalize_single_pass_plan(
                request,
                ProductionPlan.model_validate_json(_strip_json_fence(repaired_output)),
            )
            _validate_locked_dialogue(request, repaired_plan)
            return ProductionPlanningResult(
                plan=repaired_plan,
                planner="gpt-5.6-sol-repaired",
                warnings=[
                    *image_warnings,
                    "GPT-5.6-sol 首次规划未通过结构校验，已自动修复后采用："
                    + _compact_error(first_error),
                ],
            )
        except (LanguageProviderError, ValueError) as repair_error:
            return ProductionPlanningResult(
                plan=_fallback_plan(request),
                planner="deterministic-fallback-v1",
                warnings=[
                    *image_warnings,
                    "GPT-5.6-sol 规划与一次自动修复均未通过，已使用本地回退："
                    f"first={first_error}; repair={repair_error}",
                ],
            )


def _asset(
    *,
    project_id: str,
    suffix: str,
    kind: AssetKind,
    name: str,
    descriptor: str,
    planner: str,
    created_at: str,
) -> Asset:
    version_id = f"{project_id}-asset-{suffix}-v1"
    return Asset(
        id=f"{project_id}-asset-{suffix}",
        project_id=project_id,
        kind=kind,
        name=name,
        current_version_id=version_id,
        versions=[
            AssetVersion(
                id=version_id,
                version=1,
                origin_type=OriginType.AI_GENERATED,
                descriptor=descriptor,
                media_type=AssetMediaType.TEXT_REFERENCE,
                source=AssetSourceRecord(
                    source_type=OriginType.AI_GENERATED,
                    source_label="自动制片规划",
                    provider=(
                        "openai-compatible"
                        if planner.startswith("gpt-5.6-sol")
                        else "local"
                    ),
                    model=planner,
                    created_at=created_at,
                ),
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                created_at=created_at,
            )
        ],
    )


def _default_reference_needs(
    *,
    plan: ProductionPlan,
    shot: PlannedShot,
    line_speakers: set[str],
) -> list[PlannedReferenceNeed]:
    """Build a small deterministic pack request when the planning Agent omits one."""

    context = "\n".join(
        (
            shot.visual,
            shot.framing,
            shot.camera,
            shot.character_positions,
            shot.eyeline,
        )
    )
    lowered = context.lower()
    close_up = any(token in lowered for token in ("特写", "近景", "close-up", "close up"))
    profile = any(token in lowered for token in ("侧面", "侧脸", "profile", "三分之四"))
    moving = shot.duration_seconds >= 12 or any(
        token in lowered
        for token in ("跟拍", "跟随", "行走", "走廊", "track", "dolly", "移动")
    )
    needs: list[PlannedReferenceNeed] = []
    for character in plan.characters:
        if (
            character.name not in line_speakers
            and not _character_name_is_visible(
                character.name,
                shot.character_positions,
            )
        ):
            continue
        if close_up:
            roles = [ReferenceRole.CHARACTER_FACE, ReferenceRole.CHARACTER_PROFILE]
            framings = [ReferenceFraming.FACE_CLOSE_UP, ReferenceFraming.CLOSE_UP]
        else:
            roles = [ReferenceRole.CHARACTER_BODY, ReferenceRole.CHARACTER_PROFILE]
            framings = [ReferenceFraming.FULL_BODY if moving else ReferenceFraming.MEDIUM]
        angles = (
            [ReferenceAngle.THREE_QUARTER_LEFT, ReferenceAngle.LEFT_PROFILE]
            if profile
            else [ReferenceAngle.FRONT, ReferenceAngle.THREE_QUARTER_LEFT]
        )
        needs.append(
            PlannedReferenceNeed(
                subject_name=character.name,
                acceptable_roles=roles,
                preferred_angles=angles,
                preferred_framings=framings,
                tags=["identity", "wardrobe"],
                purpose=(
                    "长时移动表演中的身份、服装和全身比例连续"
                    if moving
                    else "当前景别和机位中的角色身份与服装连续"
                ),
            )
        )

    for location in plan.locations:
        if len(plan.locations) > 1 and not _location_name_matches(
            location.name,
            context,
        ):
            continue
        matched_views = [
            view for view in location.key_views if view and view in context
        ]
        interaction_view = moving or any(
            token in lowered
            for token in ("入口", "出口", "过道", "门口", "进入", "离开", "揭晓", "反打")
        )
        needs.append(
            PlannedReferenceNeed(
                subject_name=location.name,
                acceptable_roles=(
                    [ReferenceRole.LOCATION_INTERACTION, ReferenceRole.LOCATION_MASTER]
                    if interaction_view
                    else [ReferenceRole.LOCATION_MASTER]
                ),
                preferred_framings=[ReferenceFraming.WIDE],
                tags=(matched_views[:3] or (["continuous-path"] if moving else ["master"])),
                purpose=(
                    "保持连续运动路径、空间轴线和镜头终点一致"
                    if moving
                    else "保持当前镜头的空间布局、轴线和灯光一致"
                ),
            )
        )

    for prop in plan.props:
        if not _canonical_name_matches(prop.name, context):
            continue
        stateful = any(
            token in lowered
            for token in ("手持", "拿", "放", "交", "佩戴", "取下", "状态", "亮", "打开")
        )
        needs.append(
            PlannedReferenceNeed(
                subject_name=prop.name,
                acceptable_roles=(
                    [ReferenceRole.PROP_STATE, ReferenceRole.PROP_IN_HAND]
                    if stateful
                    else [ReferenceRole.PROP_PRODUCT]
                ),
                preferred_framings=[ReferenceFraming.DETAIL],
                tags=["state"] if stateful else ["neutral"],
                purpose="保持道具外观、尺度以及镜头前后状态一致",
            )
        )
    return needs


def build_project_document_from_plan(
    *,
    project_id: str,
    request: CreateProjectRequest,
    planning: ProductionPlanningResult,
    created_at: str,
    planning_run_id: str | None = None,
) -> ProjectDocument:
    plan = planning.plan
    planned_lines = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else plan.lines
    )
    lines = [
        ScriptLine(
            id=f"{project_id}-line-{index:02d}",
            speaker=line.speaker,
            original_text=line.text,
            spoken_text=line.text,
            subtitle_text=line.text,
            audio_kind=line.audio_kind,
        )
        for index, line in enumerate(planned_lines, start=1)
    ]
    line_ids = {index: line.id for index, line in enumerate(lines, start=1)}
    planned_scenes = plan.scenes or [
        PlannedSceneContract(
            sequence=1,
            heading=plan.scene_heading,
            summary=plan.scene_summary,
            narrative_purpose="完成当前短片的完整叙事目的",
            target_duration_seconds=sum(shot.duration_seconds for shot in plan.shots),
            dialogue_line_indices=list(range(1, len(lines) + 1)),
            location_names=[plan.locations[0].name],
            continuity_in=SceneContinuitySummary(
                setting=plan.locations[0].name,
                open_threads=[plan.scene_summary],
            ),
            continuity_out=SceneContinuitySummary(
                setting=plan.locations[0].name,
                known_facts=[plan.scene_summary],
            ),
        )
    ]
    scene_id_by_sequence = {
        scene.sequence: f"{project_id}-scene-{scene.sequence:02d}"
        for scene in planned_scenes
    }
    original_text = request.input_text.strip() or "\n".join(
        f"{line.speaker}：{line.original_text}" for line in lines
    )
    script = ScriptVersion(
        id=f"{project_id}-script-v1",
        project_id=project_id,
        version=1,
        source_mode=request.source_mode,
        original_text=original_text,
        spoken_text="\n".join(line.spoken_text for line in lines),
        subtitle_text="\n".join(line.subtitle_text for line in lines),
        lines=lines,
        structured_scenes=[
            StructuredScene(
                id=scene_id_by_sequence[scene.sequence],
                heading=scene.heading,
                summary=scene.summary,
                line_ids=[
                    line_ids[index]
                    for index in scene.dialogue_line_indices
                    if index in line_ids
                ],
                narrative_purpose=scene.narrative_purpose,
                target_duration_seconds=scene.target_duration_seconds,
                entry_state=scene.continuity_in.model_copy(deep=True),
                exit_state=scene.continuity_out.model_copy(deep=True),
            )
            for scene in planned_scenes
        ],
        review_report=(
            planning.review_report.model_copy(deep=True)
            if planning.review_report is not None
            else None
        ),
        review_disposition=(
            planning.review_disposition.model_copy(deep=True)
            if planning.review_disposition is not None
            else None
        ),
        planning_run_id=planning_run_id,
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )

    planned_characters = list(plan.characters)
    known_names = {character.name for character in planned_characters}
    for line in planned_lines:
        if (
            line.audio_kind == AudioKind.DIALOGUE
            and line.speaker != "旁白"
            and not _character_name_covered(line.speaker, known_names)
        ):
            planned_characters.append(
                PlannedCharacter(
                    name=line.speaker,
                    age=30,
                    appearance="真实自然的都市人物，面部特征清晰",
                    wardrobe="低饱和都市通勤装",
                    performance="自然克制，服从当前对白节拍",
                    voice_identity=f"S{len(planned_characters) + 1}",
                )
            )
            known_names.add(line.speaker)
    character_profiles = [
        CharacterProfile(
            id=f"character-{index:02d}",
            name=character.name,
            age=character.age,
            appearance=character.appearance,
            wardrobe=character.wardrobe,
            performance=character.performance,
            voice_identity=character.voice_identity,
        )
        for index, character in enumerate(planned_characters, start=1)
    ]
    location_profiles = [
        LocationProfile(
            id=f"location-{index:02d}",
            name=location.name,
            layout=location.layout,
            lighting=location.lighting,
            palette=location.palette,
            key_views=location.key_views,
        )
        for index, location in enumerate(plan.locations, start=1)
    ]
    prop_profiles = [
        PropProfile(
            id=f"prop-{index:02d}",
            name=prop.name,
            appearance=prop.appearance,
            states=[],
        )
        for index, prop in enumerate(plan.props, start=1)
    ]
    bible = ProjectBibleVersion(
        id=f"{project_id}-bible-v1",
        project_id=project_id,
        version=1,
        characters=character_profiles,
        locations=location_profiles,
        props=prop_profiles,
        style=StyleProfile(
            medium=production_skill(request.production_profile).medium,
            genre=plan.style.genre,
            cinematography=plan.style.cinematography,
            color=plan.style.color,
            texture=plan.style.texture,
            forbidden=plan.style.forbidden,
        ),
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )

    assets: list[Asset] = []
    for index, character in enumerate(planned_characters, start=1):
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"character-{index:02d}",
                kind=AssetKind.CHARACTER,
                name=f"{character.name}定妆",
                descriptor=(
                    f"{character.age}岁；{character.appearance}；{character.wardrobe}；"
                    f"表演：{character.performance}；声线：{character.voice_identity}"
                ),
                planner=planning.planner,
                created_at=created_at,
            )
        )
    for index, location in enumerate(plan.locations, start=1):
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"location-{index:02d}",
                kind=AssetKind.LOCATION,
                name=location.name,
                descriptor=(
                    f"布局：{location.layout}；灯光：{location.lighting}；色彩：{location.palette}"
                ),
                planner=planning.planner,
                created_at=created_at,
            )
        )
    for index, prop in enumerate(plan.props, start=1):
        assets.append(
            _asset(
                project_id=project_id,
                suffix=f"prop-{index:02d}",
                kind=AssetKind.PROP,
                name=prop.name,
                descriptor=prop.appearance,
                planner=planning.planner,
                created_at=created_at,
            )
        )

    shots = [
        ShotPlan(
            id=f"{project_id}-shot-{sequence:02d}",
            project_id=project_id,
            sequence=sequence,
            scene_id=scene_id_by_sequence.get(shot.scene_sequence),
            duration_seconds=shot.duration_seconds,
            narrative_purpose=shot.narrative_purpose,
            visual=shot.visual,
            framing=shot.framing,
            camera=shot.camera,
            audio_intent=shot.audio_intent,
            dialogue_line_ids=[
                line_ids[index]
                for index in shot.dialogue_line_indices
                if index in line_ids
            ],
            continuity=ContinuityState(
                character_positions=shot.character_positions,
                eyeline=shot.eyeline,
                prop_state_in=shot.prop_state_in,
                prop_state_out=shot.prop_state_out,
                action_in=shot.action_in,
                action_out=shot.action_out,
            ),
            transition_out=shot.transition_out,
            merge_candidate=shot.merge_candidate,
            local_rework_risk=shot.local_rework_risk,
            approval_status=ApprovalStatus.APPROVED,
            freshness=Freshness.CURRENT,
        )
        for sequence, shot in enumerate(plan.shots, start=1)
    ]
    capability = CapabilitySnapshot(
        provider="mock",
        model="deterministic-media-v1",
        resolution="432x768",
        native_audio=True,
        supports_frame_reference=True,
        supports_asset_reference=True,
    )
    execution_plan = [
        ExecutionPlanEntry(
            shot_id=shot.id,
            strategy=(
                ExecutionStrategy.AUDIO_DRIVEN
                if shot.dialogue_line_ids
                else ExecutionStrategy.IMAGE_GUIDED
                if shot.local_rework_risk == "高"
                else ExecutionStrategy.DIRECT_AUDIOVISUAL
            ),
            rationale="自动规划先以供应商中立 Mock 验证完整编排；真实执行再按能力画像选路",
            skipped_optional_steps=["真实视频生成", "可选关键帧生成", "可选外部声音修复"],
            provider="mock",
            capability_snapshot=capability.model_copy(deep=True),
            candidate_count=2,
            fallback="普通失败自动重试当前镜头；质量失败进入通用 Director 修复循环",
        )
        for shot in shots
    ]
    asset_versions = {
        asset.name: asset.versions[0].id
        for asset in assets
    }
    subject_assets = {
        asset.name.removesuffix("定妆"): asset
        for asset in assets
        if asset.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
    }
    reference_plan: list[ReferencePlanEntry] = []
    location_count = sum(asset.kind == AssetKind.LOCATION for asset in assets)
    for shot, planned_shot in zip(shots, plan.shots, strict=True):
        dialogue = [
            line for line in lines if line.id in shot.dialogue_line_ids
        ]
        line_speakers = {
            line.speaker
            for line in dialogue
            if line.audio_kind == AudioKind.DIALOGUE
        }
        planned_needs = planned_shot.reference_needs or _default_reference_needs(
            plan=plan,
            shot=planned_shot,
            line_speakers=line_speakers,
        )
        requirements: list[ReferenceRequirement] = []
        for index, need in enumerate(planned_needs, start=1):
            subject = subject_assets.get(need.subject_name)
            if subject is None:
                continue
            requirements.append(
                ReferenceRequirement(
                    id=f"{shot.id}-reference-{index:02d}",
                    subject_asset_id=subject.id,
                    acceptable_roles=list(need.acceptable_roles),
                    preferred_angles=list(need.preferred_angles),
                    preferred_framings=list(need.preferred_framings),
                    required_tags=list(dict.fromkeys(need.tags)),
                    purpose=need.purpose,
                    required=need.required,
                )
            )
        requested_asset_ids = {
            requirement.subject_asset_id for requirement in requirements
        }
        context = _shot_reference_context(shot)
        bindings: list[ReferenceBinding] = []
        for asset in assets:
            should_bind = (
                asset.id in requested_asset_ids
                or _asset_relevant_to_shot(
                    asset,
                    context=context,
                    speakers=line_speakers,
                    location_count=location_count,
                    character_context=shot.continuity.character_positions,
                )
            )
            if should_bind:
                bindings.append(
                    ReferenceBinding(
                        kind=asset.kind.value,
                        target_id=asset_versions[asset.name],
                        purpose=_PLANNED_REFERENCE_BINDING_PURPOSE,
                        requirement="REQUIRED" if asset.kind != AssetKind.PROP else "OPTIONAL",
                    )
                )
        reference_plan.append(
            ReferencePlanEntry(
                shot_id=shot.id,
                bindings=bindings,
                capability_valid=True,
                validation_notes=[
                    "Mock 阶段保存供应商中立绑定；真实 Provider 提交前重新校验",
                    "逐镜资产需求由规划 Agent 给出；缺失时使用通用启发式回退",
                ],
                requirements=requirements,
                max_reference_images=min(9, max(3, len(requirements))),
                planning_notes=(
                    "asset-manager:gpt-planned"
                    if planned_shot.reference_needs
                    else "asset-manager:deterministic-fallback"
                ),
            )
        )

    project = ProjectRecord(
        id=project_id,
        name=(request.name or plan.title).strip(),
        source_mode=request.source_mode,
        run_mode=request.run_mode,
        production_profile=request.production_profile,
        target_duration_seconds=request.resolved_target_duration_seconds(),
        input_text=request.input_text,
        input_images=[image.model_copy(deep=True) for image in request.input_images],
        linked_asset_project_ids=list(request.linked_asset_project_ids),
        stage=ProjectStage.PLANNED,
        current_script_version_id=script.id,
        current_bible_version_id=bible.id,
        created_at=created_at,
        updated_at=created_at,
    )
    document = ProjectDocument(
        project=project,
        script_versions=[script],
        bible_versions=[bible],
        assets=assets,
        shots=shots,
        execution_plan=execution_plan,
        reference_plan=reference_plan,
        manifests=[],
    )
    refresh_lightweight_reference_plans(document)
    return document


def refresh_lightweight_reference_plans(document: ProjectDocument) -> bool:
    """Keep simple AUTO_DRAFT bindings useful without overwriting advanced plans.

    The first autonomous planning pass intentionally emits a lightweight, provider-neutral
    ReferenceBinding list.  Later route planning can enrich that list from the already-declared
    shot intent, while structured requirements/action beats/interactions remain authoritative.
    """

    lines_by_id = {
        line.id: line
        for script in document.script_versions
        if script.id == document.project.current_script_version_id
        for line in script.lines
    }
    assets_by_version = {
        version.id: asset
        for asset in document.assets
        for version in asset.versions
    }
    location_count = sum(asset.kind == AssetKind.LOCATION for asset in document.assets)
    plans_by_shot = {item.shot_id: item for item in document.reference_plan}
    inferred_location_ids = _infer_shot_location_asset_ids(document)
    changed = False

    for shot in document.shots:
        plan = plans_by_shot.get(shot.id)
        if plan is None:
            continue

        dialogue = [
            lines_by_id[line_id]
            for line_id in shot.dialogue_line_ids
            if line_id in lines_by_id
        ]
        context = _shot_reference_context(shot)
        speakers = {
            line.speaker.removeprefix("录音笔中的")
            for line in dialogue
            if line.audio_kind == AudioKind.DIALOGUE
        }
        if plan.planning_notes == "asset-manager:deterministic-fallback":
            refreshed_requirements = _fallback_reference_requirements_for_document(
                document,
                shot,
                speakers=speakers,
                inferred_location_ids=inferred_location_ids.get(shot.id, set()),
            )
            before_requirements = [
                item.model_dump(mode="json") for item in plan.requirements
            ]
            after_requirements = [
                item.model_dump(mode="json") for item in refreshed_requirements
            ]
            if before_requirements != after_requirements:
                plan.requirements = refreshed_requirements
                changed = True
        existing_by_asset: dict[str, ReferenceBinding] = {}
        unresolved: list[ReferenceBinding] = []
        requested_asset_ids = {
            requirement.subject_asset_id for requirement in plan.requirements
        }
        relevant_asset_ids = {
            asset.id
            for asset in document.assets
            if asset.id in requested_asset_ids
            or asset.id in inferred_location_ids.get(shot.id, set())
            or (
                asset.kind != AssetKind.LOCATION
                and _asset_relevant_to_shot(
                    asset,
                    context=context,
                    speakers=speakers,
                    location_count=location_count,
                    character_context=shot.continuity.character_positions,
                )
            )
        }
        for binding in plan.bindings:
            asset = assets_by_version.get(binding.target_id)
            if asset is None:
                unresolved.append(binding.model_copy(deep=True))
                continue
            if (
                binding.purpose in _AUTOMATIC_REFERENCE_BINDING_PURPOSES
                and asset.id not in relevant_asset_ids
            ):
                continue
            existing_by_asset.setdefault(asset.id, binding)

        selected_assets = [
            asset
            for asset in document.assets
            if asset.id in existing_by_asset or asset.id in relevant_asset_ids
        ]

        bindings: list[ReferenceBinding] = []
        for asset in selected_assets:
            existing = existing_by_asset.get(asset.id)
            target_id = asset.approved_version_id or asset.current_version_id
            bindings.append(
                ReferenceBinding(
                    kind=asset.kind.value,
                    target_id=target_id,
                    purpose=(
                        existing.purpose
                        if existing is not None
                        else _ENRICHED_REFERENCE_BINDING_PURPOSE
                    ),
                    requirement=(
                        existing.requirement
                        if existing is not None
                        else "REQUIRED"
                    ),
                )
            )
        bindings.extend(unresolved)
        max_images = min(9, max(plan.max_reference_images, len(bindings), 1))
        before = [item.model_dump(mode="json") for item in plan.bindings]
        after = [item.model_dump(mode="json") for item in bindings]
        if before != after or plan.max_reference_images != max_images:
            plan.bindings = bindings
            plan.max_reference_images = max_images
            changed = True
    return changed


_PLANNED_REFERENCE_BINDING_PURPOSE = "自动规划的身份、场景或道具连续性参考"
_ENRICHED_REFERENCE_BINDING_PURPOSE = "自动补全的当前镜头身份、空间或道具连续性参考"
_AUTOMATIC_REFERENCE_BINDING_PURPOSES = {
    _PLANNED_REFERENCE_BINDING_PURPOSE,
    _ENRICHED_REFERENCE_BINDING_PURPOSE,
}


def _shot_reference_context(shot: ShotPlan) -> str:
    return "\n".join(
        [
            shot.visual,
            shot.framing,
            shot.camera,
            shot.continuity.character_positions,
            shot.continuity.eyeline,
        ]
    )


def _fallback_reference_requirements_for_document(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    speakers: set[str],
    inferred_location_ids: set[str],
) -> list[ReferenceRequirement]:
    """Rebuild omitted Agent coverage from subjects actually visible in one shot."""

    context = _shot_reference_context(shot)
    lowered = context.lower()
    close_up = any(
        token in lowered for token in ("特写", "近景", "close-up", "close up")
    )
    profile = any(
        token in lowered for token in ("侧面", "侧脸", "profile", "三分之四")
    )
    moving = shot.duration_seconds >= 12 or any(
        token in lowered
        for token in ("跟拍", "跟随", "行走", "走廊", "track", "dolly", "移动")
    )
    interaction_view = moving or any(
        token in lowered
        for token in ("入口", "出口", "过道", "门口", "进入", "离开", "揭晓", "反打")
    )
    stateful_prop = any(
        token in lowered
        for token in ("手持", "拿", "放", "交", "佩戴", "取下", "状态", "亮", "打开")
    )
    location_count = sum(
        asset.kind == AssetKind.LOCATION for asset in document.assets
    )
    visible_assets = [
        asset
        for asset in document.assets
        if asset.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
        and (
            asset.id in inferred_location_ids
            or (
                asset.kind != AssetKind.LOCATION
                and _asset_relevant_to_shot(
                    asset,
                    context=context,
                    speakers=speakers,
                    location_count=location_count,
                    character_context=shot.continuity.character_positions,
                )
            )
        )
    ]
    requirements: list[ReferenceRequirement] = []
    for index, asset in enumerate(visible_assets, start=1):
        if asset.kind == AssetKind.CHARACTER:
            roles = (
                [ReferenceRole.CHARACTER_FACE, ReferenceRole.CHARACTER_PROFILE]
                if close_up
                else [ReferenceRole.CHARACTER_BODY, ReferenceRole.CHARACTER_PROFILE]
            )
            angles = (
                [ReferenceAngle.THREE_QUARTER_LEFT, ReferenceAngle.LEFT_PROFILE]
                if profile
                else [ReferenceAngle.FRONT, ReferenceAngle.THREE_QUARTER_LEFT]
            )
            framings = (
                [ReferenceFraming.FACE_CLOSE_UP, ReferenceFraming.CLOSE_UP]
                if close_up
                else [ReferenceFraming.FULL_BODY if moving else ReferenceFraming.MEDIUM]
            )
            tags = ["identity", "wardrobe"]
            purpose = (
                "长时移动表演中的身份、服装和全身比例连续"
                if moving
                else "当前景别和机位中的角色身份与服装连续"
            )
        elif asset.kind == AssetKind.LOCATION:
            roles = (
                [ReferenceRole.LOCATION_INTERACTION, ReferenceRole.LOCATION_MASTER]
                if interaction_view
                else [ReferenceRole.LOCATION_MASTER]
            )
            angles = [ReferenceAngle.NOT_APPLICABLE]
            framings = [ReferenceFraming.WIDE]
            tags = ["continuous-path" if moving else "master"]
            purpose = (
                "保持连续运动路径、空间轴线和镜头终点一致"
                if moving
                else "保持当前镜头的空间布局、轴线和灯光一致"
            )
        else:
            roles = (
                [ReferenceRole.PROP_STATE, ReferenceRole.PROP_IN_HAND]
                if stateful_prop
                else [ReferenceRole.PROP_PRODUCT]
            )
            angles = [ReferenceAngle.NOT_APPLICABLE]
            framings = [ReferenceFraming.DETAIL]
            tags = ["state" if stateful_prop else "neutral"]
            purpose = "保持当前镜头可见道具的外观、尺度和状态一致"
        requirements.append(
            ReferenceRequirement(
                id=f"{shot.id}-reference-{index:02d}",
                subject_asset_id=asset.id,
                acceptable_roles=roles,
                preferred_angles=angles,
                preferred_framings=framings,
                required_tags=tags,
                purpose=purpose,
                required=True,
            )
        )
    return requirements


def _infer_shot_location_asset_ids(
    document: ProjectDocument,
) -> dict[str, set[str]]:
    """Carry the last explicit location anchor across independently generated shots."""

    locations = [
        asset for asset in document.assets if asset.kind == AssetKind.LOCATION
    ]
    if not locations:
        return {}
    plans = {item.shot_id: item for item in document.reference_plan}
    explicit: dict[str, set[str]] = {}
    for shot in sorted(document.shots, key=lambda item: item.sequence):
        plan = plans.get(shot.id)
        requested: set[str] = (
            {
                requirement.subject_asset_id
                for requirement in plan.requirements
                if any(asset.id == requirement.subject_asset_id for asset in locations)
            }
            if plan is not None
            and plan.planning_notes != "asset-manager:deterministic-fallback"
            else set()
        )
        if requested:
            explicit[shot.id] = requested
            continue
        strong_context = "\n".join(
            (
                shot.camera,
                shot.audio_intent,
                shot.continuity.character_positions,
                shot.continuity.eyeline,
            )
        )
        strong = {
            asset.id
            for asset in locations
            if _location_name_matches(asset.name, strong_context)
        }
        if strong:
            explicit[shot.id] = strong
            continue
        visual = {
            asset.id
            for asset in locations
            if _location_name_matches(asset.name, shot.visual)
        }
        if visual:
            explicit[shot.id] = visual

    ordered = sorted(document.shots, key=lambda item: item.sequence)
    first_anchor = next(
        (explicit[shot.id] for shot in ordered if shot.id in explicit),
        {locations[0].id} if len(locations) == 1 else set(),
    )
    inferred: dict[str, set[str]] = {}
    current = set(first_anchor)
    for shot in ordered:
        if shot.id in explicit:
            current = set(explicit[shot.id])
        if current:
            inferred[shot.id] = set(current)
    return inferred


def _asset_relevant_to_shot(
    asset: Asset,
    *,
    context: str,
    speakers: set[str],
    location_count: int,
    character_context: str | None = None,
) -> bool:
    if asset.kind == AssetKind.REFERENCE:
        return False
    subject = asset.name.removesuffix("定妆").strip()
    if asset.kind == AssetKind.CHARACTER:
        return subject in speakers or _character_name_is_visible(
            subject,
            character_context if character_context is not None else context,
        )
    if asset.kind == AssetKind.LOCATION and location_count == 1:
        return True
    if asset.kind == AssetKind.LOCATION:
        return _location_name_matches(subject, context)
    return _canonical_name_matches(subject, context)


def _character_name_is_visible(name: str, context: str) -> bool:
    offscreen_markers = (
        "不在现场",
        "未在现场",
        "尚未到场",
        "没有到场",
        "不在画面",
        "未入画",
        "不入镜",
        "不出现",
        "画外",
    )
    for clause in re.split(r"[；;。.!！?？，,]", context):
        if not _canonical_name_matches(name, clause):
            continue
        if any(marker in clause for marker in offscreen_markers):
            continue
        return True
    return False


def _location_name_matches(name: str, context: str) -> bool:
    haystack = "".join(character.lower() for character in context if character.isalnum())
    if not haystack:
        return False
    for raw_part in re.split(r"[与和及、/|]+", name):
        part = "".join(
            character.lower() for character in raw_part if character.isalnum()
        )
        if not part:
            continue
        for start in range(max(1, len(part) - 1)):
            suffix = part[start:]
            if len(suffix) >= 2 and suffix in haystack:
                return True
    return False


def _canonical_name_matches(name: str, context: str) -> bool:
    canonical = "".join(character.lower() for character in name if character.isalnum())
    haystack = "".join(character.lower() for character in context if character.isalnum())
    if not canonical:
        return False
    if canonical in haystack:
        return True
    if len(canonical) <= 2:
        return False
    threshold = min(4, max(3, (len(canonical) + 1) // 2))
    previous = [0] * (len(haystack) + 1)
    longest = 0
    for left in canonical:
        current = [0]
        for index, right in enumerate(haystack, start=1):
            length = previous[index - 1] + 1 if left == right else 0
            current.append(length)
            longest = max(longest, length)
        previous = current
    return longest >= threshold
