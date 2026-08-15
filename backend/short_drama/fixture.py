"""Deterministic planning fixture used by the Phase 1 vertical slice."""

from __future__ import annotations

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetVersion,
    AudioKind,
    CapabilitySnapshot,
    CharacterProfile,
    ContinuityState,
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
    PropState,
    ReferenceBinding,
    ReferencePlanEntry,
    RunMode,
    ScriptLine,
    ScriptVersion,
    ShotPlan,
    SourceMode,
    StructuredScene,
    StyleProfile,
)


FIXTURE_PROJECT_ID = "project-dont-trust-me"
FIXTURE_NAME = "别相信我"
FIXTURE_IDEA = "雨夜便利店里，一支录音笔让女人开始怀疑自己的记忆。"

CANONICAL_LINES: tuple[tuple[str, str, AudioKind], ...] = (
    ("林夏", "你说他十点离开，可监控里，十点零三分他还在这里。", AudioKind.DIALOGUE),
    ("周衡", "监控会骗人。", AudioKind.DIALOGUE),
    ("林夏", "人也会。", AudioKind.DIALOGUE),
    ("录音笔中的林夏", "如果你听到这段话，别相信我。", AudioKind.VOICEOVER),
    ("周衡", "现在，你还信自己吗？", AudioKind.DIALOGUE),
)

CANONICAL_SCRIPT = "\n".join(
    f"{speaker}：{text}" for speaker, text, _audio_kind in CANONICAL_LINES
)


def _artifact_id(project_id: str, suffix: str) -> str:
    return f"{project_id}-{suffix}"


def _canonical_script_lines(project_id: str) -> list[ScriptLine]:
    return [
        ScriptLine(
            id=_artifact_id(project_id, f"line-{index:02d}"),
            speaker=speaker,
            original_text=text,
            spoken_text=text,
            subtitle_text=text,
            audio_kind=audio_kind,
        )
        for index, (speaker, text, audio_kind) in enumerate(CANONICAL_LINES, start=1)
    ]


def _locked_script_lines(project_id: str, input_text: str) -> list[ScriptLine]:
    non_empty = [line.strip() for line in input_text.splitlines() if line.strip()]
    source_lines = non_empty or [input_text.strip()]
    result: list[ScriptLine] = []
    for index, source_line in enumerate(source_lines, start=1):
        speaker, separator, text = source_line.partition("：")
        if not separator:
            speaker = "旁白"
            text = source_line
        result.append(
            ScriptLine(
                id=_artifact_id(project_id, f"line-{index:02d}"),
                speaker=speaker.strip() or "旁白",
                original_text=text.strip(),
                spoken_text=text.strip(),
                subtitle_text=text.strip(),
                audio_kind=AudioKind.VOICEOVER if speaker.strip() == "旁白" else AudioKind.DIALOGUE,
            )
        )
    return result


def _build_script(
    *,
    project_id: str,
    source_mode: SourceMode,
    input_text: str,
    created_at: str,
    fixed_fixture: bool,
) -> ScriptVersion:
    if fixed_fixture or source_mode == SourceMode.IDEA_EXPANSION:
        lines = _canonical_script_lines(project_id)
        original_text = CANONICAL_SCRIPT if fixed_fixture else input_text
    else:
        lines = _locked_script_lines(project_id, input_text)
        original_text = input_text

    spoken_text = "\n".join(line.spoken_text for line in lines)
    subtitle_text = "\n".join(line.subtitle_text for line in lines)
    return ScriptVersion(
        id=_artifact_id(project_id, "script-v1"),
        project_id=project_id,
        version=1,
        source_mode=source_mode,
        original_text=original_text,
        spoken_text=spoken_text,
        subtitle_text=subtitle_text,
        lines=lines,
        structured_scenes=[
            StructuredScene(
                id=_artifact_id(project_id, "scene-01"),
                heading="内景·雨夜便利店·深夜",
                summary="林夏用监控时间质问周衡，一支录音笔播放出她自己的警告。",
                line_ids=[line.id for line in lines],
            )
        ],
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )


def _build_bible(project_id: str, created_at: str) -> ProjectBibleVersion:
    return ProjectBibleVersion(
        id=_artifact_id(project_id, "bible-v1"),
        project_id=project_id,
        version=1,
        characters=[
            CharacterProfile(
                id="character-linxia",
                name="林夏",
                age=28,
                appearance="短黑发，冷静锐利的眼神",
                wardrobe="米白色风衣，深色内搭",
                performance="克制但警觉，情绪从质疑递进到动摇",
                voice_identity="S1",
            ),
            CharacterProfile(
                id="character-zhouheng",
                name="周衡",
                age=32,
                appearance="左眉浅疤，神情平静",
                wardrobe="深灰色西装",
                performance="表面平静，始终掌握谈话节奏",
                voice_identity="S2",
            ),
        ],
        locations=[
            LocationProfile(
                id="location-convenience-store",
                name="雨夜便利店",
                layout="玻璃门在画面右侧，收银台在左后方，两人隔柜台对峙",
                lighting="冷白顶灯，门外蓝色霓虹反光",
                palette="冷青、灰白，以红色录音笔为唯一高饱和点",
                key_views=["门外建立镜头", "林夏方向", "周衡反打", "柜台道具特写"],
            )
        ],
        props=[
            PropProfile(
                id="prop-red-recorder",
                name="红色旧录音笔",
                appearance="红色磨损外壳，顶部指示灯",
                states=[
                    PropState(shot_id=_artifact_id(project_id, "shot-03"), holder="周衡右手", location="胸前"),
                    PropState(shot_id=_artifact_id(project_id, "shot-05"), holder="无人", location="柜台中央"),
                    PropState(shot_id=_artifact_id(project_id, "shot-08"), holder="林夏左手", location="离开便利店"),
                ],
            )
        ],
        style=StyleProfile(
            medium="真人都市短剧",
            genre="都市悬疑",
            cinematography="9:16，写实电影感，克制推镜，遵守对话轴线",
            color="冷白室内光与蓝色雨夜霓虹，红色道具作为视觉锚点",
            texture="真实皮肤、自然布料、湿玻璃反光",
            forbidden=["动漫化", "过度磨皮", "角色换脸", "左右手道具跳变", "无动机越轴"],
        ),
        approval_status=ApprovalStatus.APPROVED,
        freshness=Freshness.CURRENT,
        created_at=created_at,
    )


def _asset(
    *,
    project_id: str,
    suffix: str,
    kind: AssetKind,
    name: str,
    descriptor: str,
    created_at: str,
) -> Asset:
    version_id = _artifact_id(project_id, f"asset-{suffix}-v1")
    return Asset(
        id=_artifact_id(project_id, f"asset-{suffix}"),
        project_id=project_id,
        kind=kind,
        name=name,
        current_version_id=version_id,
        versions=[
            AssetVersion(
                id=version_id,
                version=1,
                origin_type=OriginType.FIXTURE,
                descriptor=descriptor,
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                created_at=created_at,
            )
        ],
    )


def _build_assets(project_id: str, created_at: str) -> list[Asset]:
    return [
        _asset(
            project_id=project_id,
            suffix="linxia",
            kind=AssetKind.CHARACTER,
            name="林夏定妆",
            descriptor="28 岁，短黑发，米白风衣，克制警觉，声线标识 S1",
            created_at=created_at,
        ),
        _asset(
            project_id=project_id,
            suffix="zhouheng",
            kind=AssetKind.CHARACTER,
            name="周衡定妆",
            descriptor="32 岁，深灰西装，左眉浅疤，平静，声线标识 S2",
            created_at=created_at,
        ),
        _asset(
            project_id=project_id,
            suffix="store",
            kind=AssetKind.LOCATION,
            name="雨夜便利店",
            descriptor="冷白顶灯，右侧玻璃门，左后收银台，蓝色霓虹雨夜反光",
            created_at=created_at,
        ),
        _asset(
            project_id=project_id,
            suffix="recorder",
            kind=AssetKind.PROP,
            name="红色旧录音笔",
            descriptor="磨损红壳，指示灯；周衡右手 → 柜台 → 林夏左手",
            created_at=created_at,
        ),
    ]


def _line_id(project_id: str, number: int) -> str:
    return _artifact_id(project_id, f"line-{number:02d}")


def _build_shots(project_id: str, available_line_count: int) -> list[ShotPlan]:
    dialogue_map: dict[int, list[int]] = {2: [1], 3: [2], 4: [3], 5: [4], 7: [5]}
    definitions: tuple[
        tuple[float, str, str, str, str, str, str, str, str, bool, str], ...
    ] = (
        (5.0, "建立雨夜空间和压迫气氛", "雨夜便利店外，镜头靠近带雨痕的玻璃门", "大全景", "缓慢推近", "雨声与霓虹电流声", "门外", "门内两人形成对峙", "硬切", True, "低"),
        (8.0, "林夏给出决定性时间证据", "林夏近景，视线越过镜头锁定周衡", "近景", "稳定机位，末段轻推", "林夏台词 1", "林夏抬眼", "林夏保持警觉", "反打", False, "中"),
        (6.0, "周衡否定证据并首次露出道具", "周衡反打，右手握红色录音笔", "中近景", "静止反打", "周衡台词 2", "录音笔在右手", "录音笔仍在右手", "反打", False, "高"),
        (5.0, "林夏用短句把怀疑指向人", "林夏更近特写，表情收紧", "特写", "轻微推镜", "林夏台词 3", "林夏克制", "情绪升级", "动作切", False, "中"),
        (8.0, "录音笔播放不可能出现的信息", "周衡将红色录音笔放到柜台，指示灯亮", "道具特写", "俯角微移", "录音笔中的林夏台词 4，人物闭嘴", "道具从周衡右手进入", "道具停在柜台中央", "声音桥", False, "高"),
        (6.0, "让观众观察林夏的认知崩塌", "林夏听见自己的声音，呼吸变化", "近景反应", "缓慢靠近", "环境音与呼吸", "她看向柜台", "她准备伸手", "视线切", True, "中"),
        (7.0, "周衡完成心理施压", "周衡半侧脸，玻璃反光掠过眉间浅疤", "半侧近景", "轻微横移", "周衡台词 5", "周衡注视林夏", "周衡仍未移动", "动作切", False, "高"),
        (5.0, "以林夏带走证据但失去确定性收束", "林夏左手拿起录音笔，转身推门进入雨夜", "中景", "跟随半步后停下", "门铃与雨声，无对白", "道具在柜台", "道具转移到林夏左手", "淡出", True, "高"),
    )
    shots: list[ShotPlan] = []
    for sequence, definition in enumerate(definitions, start=1):
        (
            duration,
            purpose,
            visual,
            framing,
            camera,
            audio,
            action_in,
            action_out,
            transition,
            merge_candidate,
            risk,
        ) = definition
        line_numbers = [number for number in dialogue_map.get(sequence, []) if number <= available_line_count]
        shots.append(
            ShotPlan(
                id=_artifact_id(project_id, f"shot-{sequence:02d}"),
                project_id=project_id,
                sequence=sequence,
                duration_seconds=duration,
                narrative_purpose=purpose,
                visual=visual,
                framing=framing,
                camera=camera,
                audio_intent=audio if line_numbers or sequence not in dialogue_map else "环境音占位",
                dialogue_line_ids=[_line_id(project_id, number) for number in line_numbers],
                continuity=ContinuityState(
                    character_positions="林夏在镜头左侧轴线，周衡在右侧轴线",
                    eyeline="双方视线保持同一对话轴",
                    prop_state_in=action_in,
                    prop_state_out=action_out,
                    action_in=action_in,
                    action_out=action_out,
                ),
                transition_out=transition,
                merge_candidate=merge_candidate,
                local_rework_risk=risk,
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
            )
        )
    return shots


def _build_execution_plan(project_id: str, shots: list[ShotPlan]) -> list[ExecutionPlanEntry]:
    strategies = (
        ExecutionStrategy.DIRECT_AUDIOVISUAL,
        ExecutionStrategy.IMAGE_GUIDED,
        ExecutionStrategy.DIRECT_AUDIOVISUAL,
        ExecutionStrategy.AUDIO_DRIVEN,
        ExecutionStrategy.HYBRID_REPAIR,
        ExecutionStrategy.DIRECT_AUDIOVISUAL,
        ExecutionStrategy.AUDIO_DRIVEN,
        ExecutionStrategy.IMAGE_GUIDED,
    )
    capability = CapabilitySnapshot(
        provider="mock",
        model="deterministic-media-v1",
        resolution="432x768",
        native_audio=True,
        supports_frame_reference=True,
        supports_asset_reference=True,
    )
    return [
        ExecutionPlanEntry(
            shot_id=shot.id,
            strategy=strategies[index],
            rationale="用确定性占位媒体验证该路线可被统一领域模型、任务和时间线表达",
            skipped_optional_steps=["真实图像生成", "真实视频生成", "外部 TTS/STT", "口型修复"],
            provider="mock",
            capability_snapshot=capability.model_copy(deep=True),
            candidate_count=2,
            fallback="失败后只重试当前镜头；不影响其他镜头和旧 Take",
        )
        for index, shot in enumerate(shots)
    ]


def _build_reference_plan(project_id: str, shots: list[ShotPlan]) -> list[ReferencePlanEntry]:
    bindings_by_sequence: dict[int, list[ReferenceBinding]] = {
        2: [ReferenceBinding(kind="CHARACTER", target_id=_artifact_id(project_id, "asset-linxia-v1"), purpose="身份与服装", requirement="REQUIRED")],
        3: [
            ReferenceBinding(kind="CHARACTER", target_id=_artifact_id(project_id, "asset-zhouheng-v1"), purpose="身份与眉间浅疤", requirement="REQUIRED"),
            ReferenceBinding(kind="PROP", target_id=_artifact_id(project_id, "asset-recorder-v1"), purpose="右手持有状态", requirement="REQUIRED"),
        ],
        5: [ReferenceBinding(kind="PROP", target_id=_artifact_id(project_id, "asset-recorder-v1"), purpose="柜台位置与指示灯", requirement="REQUIRED")],
        8: [ReferenceBinding(kind="PROP", target_id=_artifact_id(project_id, "asset-recorder-v1"), purpose="道具转移到林夏左手", requirement="REQUIRED")],
    }
    return [
        ReferencePlanEntry(
            shot_id=shot.id,
            bindings=bindings_by_sequence.get(shot.sequence, []),
            capability_valid=True,
            validation_notes=["Mock Adapter 接受供应商中立引用；真实 H3 role 约束在后续 Adapter 中校验"],
        )
        for shot in shots
    ]


def build_project_document(
    *,
    project_id: str,
    name: str,
    source_mode: SourceMode,
    input_text: str,
    run_mode: RunMode,
    created_at: str,
    fixed_fixture: bool = False,
) -> ProjectDocument:
    """Build the deterministic Phase 1 planning output for either input contract."""

    script = _build_script(
        project_id=project_id,
        source_mode=source_mode,
        input_text=input_text,
        created_at=created_at,
        fixed_fixture=fixed_fixture,
    )
    bible = _build_bible(project_id, created_at)
    shots = _build_shots(project_id, len(script.lines))
    project = ProjectRecord(
        id=project_id,
        name=name,
        source_mode=source_mode,
        run_mode=run_mode,
        input_text=input_text,
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
        assets=_build_assets(project_id, created_at),
        shots=shots,
        execution_plan=_build_execution_plan(project_id, shots),
        reference_plan=_build_reference_plan(project_id, shots),
        manifests=[],
    )


def build_dont_trust_me_fixture(created_at: str) -> ProjectDocument:
    return build_project_document(
        project_id=FIXTURE_PROJECT_ID,
        name=FIXTURE_NAME,
        source_mode=SourceMode.SCRIPT_LOCKED,
        input_text=CANONICAL_SCRIPT,
        run_mode=RunMode.AUTO_DRAFT,
        created_at=created_at,
        fixed_fixture=True,
    )
