"""Structured autonomous planning for empty, image, idea, and locked-script inputs."""

from __future__ import annotations

import base64
import json
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
    ProjectRecord,
    ProjectStage,
    PropProfile,
    ReferenceBinding,
    ReferencePlanEntry,
    ScriptLine,
    ScriptVersion,
    ShotPlan,
    SourceMode,
    StructuredScene,
    StyleProfile,
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
    age: int = Field(ge=18, le=80)
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


class PlannedShot(DomainModel):
    duration_seconds: float = Field(ge=4.0, le=10.0)
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


class ProductionPlan(DomainModel):
    title: str = Field(min_length=1, max_length=120)
    scene_heading: str = Field(min_length=1, max_length=200)
    scene_summary: str = Field(min_length=1, max_length=1000)
    lines: list[PlannedDialogueLine] = Field(min_length=1, max_length=40)
    characters: list[PlannedCharacter] = Field(min_length=1, max_length=4)
    locations: list[PlannedLocation] = Field(min_length=1, max_length=3)
    props: list[PlannedProp] = Field(default_factory=list[PlannedProp], max_length=4)
    style: PlannedStyle
    shots: list[PlannedShot] = Field(min_length=6, max_length=10)

    @model_validator(mode="after")
    def validate_timeline(self) -> ProductionPlan:
        duration = sum(shot.duration_seconds for shot in self.shots)
        if duration < 45.0 or duration > 60.0:
            raise ValueError("Planned episode duration must be between 45 and 60 seconds")
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
        return self


@dataclass(frozen=True)
class ProductionPlanningResult:
    plan: ProductionPlan
    planner: str
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
}
_ACTION_PREFIX_PATTERN = re.compile(
    r"^([\u4e00-\u9fffA-Za-z·•_-]{2,12}?)(?="
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
    return compact in _SCRIPT_METADATA_LABELS


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
            names.append(label)
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
        (value.find(name), name)
        for name in character_names
        if value.find(name) >= 0
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
    known = _mentioned_character(normalized, character_names, prefer_last=False)
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
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are the lead writer, production designer, director, and continuity planner for a "
        "Mandarin live-action urban vertical short drama. Return JSON only and match the supplied "
        "schema exactly. Produce one coherent 45-60 second episode with 6-10 executable shots. "
        "Use strong cause-and-effect, a clear hook, escalating information, natural performance, "
        "and a final turn. Keep character, wardrobe, location geometry, props, screen direction, "
        "eyelines, and action states continuous across shots. Avoid difficult multi-contact actions "
        "when the same narrative beat can be covered with simpler shots. Do not add fields outside "
        "the schema. For SCRIPT_LOCKED, preserve every supplied speaker and dialogue line verbatim "
        "and in order; only plan production around them. Images are visual references, not proof of "
        "facts that are not actually visible."
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
        "project_name": request.name,
        "text_input": request.input_text.strip(),
        "locked_dialogue": [
            line.model_dump(mode="json") for line in locked
        ],
        "image_references": image_descriptions,
        "requirements": {
            "medium": "真人都市短剧",
            "language": "普通话中文",
            "aspect_ratio": "9:16",
            "duration_seconds": "45-60",
            "dialogue_lines": "prefer 3-6 unless the locked script requires otherwise",
            "shots": "6-10",
        },
        "output_schema": schema,
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


def _planner_repair_prompt(
    request: CreateProjectRequest,
    invalid_output: str,
    error: ValueError,
) -> tuple[str, str]:
    locked = (
        _locked_dialogue(request.input_text)
        if request.source_mode == SourceMode.SCRIPT_LOCKED
        else []
    )
    instructions = (
        "You are repairing a previously invalid production-plan JSON for a Mandarin live-action "
        "vertical short drama. Return one complete corrected JSON object only and match the "
        "supplied ProductionPlan schema exactly. Preserve the story and shot intent where valid. "
        "Dialogue indices are strictly 1-based and may only reference the supplied lines. For "
        "SCRIPT_LOCKED, lines must exactly match locked_dialogue in speaker, text, audio_kind, and "
        "order. Characters must be actual human story characters present in the source script; "
        "metadata labels, action descriptions, props, locations, devices, and voiceover sources "
        "must never become character names. A recorded or off-screen voice is VOICEOVER, not an "
        "extra visual character. Do not add fields outside the schema."
    )
    payload = {
        "source_mode": request.source_mode.value,
        "source_text": request.input_text.strip(),
        "locked_dialogue": [line.model_dump(mode="json") for line in locked],
        "validation_error": str(error),
        "invalid_plan_json": invalid_output,
        "output_schema": ProductionPlan.model_json_schema(),
    }
    return instructions, json.dumps(payload, ensure_ascii=False, indent=2)


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


def _fallback_plan(request: CreateProjectRequest) -> ProductionPlan:
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


def create_production_plan(
    request: CreateProjectRequest,
    client: LanguageProviderClient | None,
) -> ProductionPlanningResult:
    if client is None:
        return ProductionPlanningResult(
            plan=_fallback_plan(request),
            planner="deterministic-fallback-v1",
            warnings=["GPT-5.6-sol 未配置，已使用可复现的本地规划回退。"],
        )
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
        plan = ProductionPlan.model_validate_json(_strip_json_fence(output))
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
            repaired_plan = ProductionPlan.model_validate_json(
                _strip_json_fence(repaired_output)
            )
            _validate_locked_dialogue(request, repaired_plan)
            return ProductionPlanningResult(
                plan=repaired_plan,
                planner="gpt-5.6-sol-repaired",
                warnings=[
                    *image_warnings,
                    "GPT-5.6-sol 首次规划未通过结构校验，已自动修复后采用："
                    + str(first_error),
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


def build_project_document_from_plan(
    *,
    project_id: str,
    request: CreateProjectRequest,
    planning: ProductionPlanningResult,
    created_at: str,
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
                id=f"{project_id}-scene-01",
                heading=plan.scene_heading,
                summary=plan.scene_summary,
                line_ids=[line.id for line in lines],
            )
        ],
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
            and line.speaker not in known_names
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
            medium="真人都市短剧",
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

    line_ids = {index: line.id for index, line in enumerate(lines, start=1)}
    shots = [
        ShotPlan(
            id=f"{project_id}-shot-{sequence:02d}",
            project_id=project_id,
            sequence=sequence,
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
    reference_plan: list[ReferencePlanEntry] = []
    for shot in shots:
        line_speakers = {
            line.speaker for line in lines if line.id in shot.dialogue_line_ids
        }
        bindings: list[ReferenceBinding] = []
        for asset in assets:
            subject_name = asset.name.removesuffix("定妆")
            should_bind = (
                asset.kind == AssetKind.LOCATION
                or subject_name in shot.visual
                or subject_name in line_speakers
            )
            if should_bind:
                bindings.append(
                    ReferenceBinding(
                        kind=asset.kind.value,
                        target_id=asset_versions[asset.name],
                        purpose="自动规划的身份、场景或道具连续性参考",
                        requirement="REQUIRED" if asset.kind != AssetKind.PROP else "OPTIONAL",
                    )
                )
        reference_plan.append(
            ReferencePlanEntry(
                shot_id=shot.id,
                bindings=bindings,
                capability_valid=True,
                validation_notes=["Mock 阶段保存供应商中立绑定；真实 Provider 提交前重新校验"],
            )
        )

    project = ProjectRecord(
        id=project_id,
        name=(request.name or plan.title).strip(),
        source_mode=request.source_mode,
        run_mode=request.run_mode,
        target_duration_seconds=sum(shot.duration_seconds for shot in shots),
        input_text=request.input_text,
        input_images=[image.model_copy(deep=True) for image in request.input_images],
        stage=ProjectStage.PLANNED,
        current_script_version_id=script.id,
        current_bible_version_id=bible.id,
        created_at=created_at,
        updated_at=created_at,
    )
    return ProjectDocument(
        project=project,
        script_versions=[script],
        bible_versions=[bible],
        assets=assets,
        shots=shots,
        execution_plan=execution_plan,
        reference_plan=reference_plan,
        manifests=[],
    )
