"""Capability validation and request compilation for MiniMax-H3."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from short_drama.h3_models import (
    H3ContentItem,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3UrlValue,
    H3VideoRequest,
)
from short_drama.models import AssetKind, ProjectDocument, ShotPlan, VideoGenerationRoute


class H3CapabilityError(ValueError):
    """Raised before any provider call when the requested H3 mode is invalid."""


_ROLE_KIND: dict[H3MediaRole, H3MediaKind] = {
    H3MediaRole.FIRST_FRAME: H3MediaKind.IMAGE,
    H3MediaRole.LAST_FRAME: H3MediaKind.IMAGE,
    H3MediaRole.REFERENCE_IMAGE: H3MediaKind.IMAGE,
    H3MediaRole.REFERENCE_VIDEO: H3MediaKind.VIDEO,
    H3MediaRole.REFERENCE_AUDIO: H3MediaKind.AUDIO,
}

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov"}
_AUDIO_EXTENSIONS = {".wav", ".mp3"}
_SIZE_LIMITS = {
    H3MediaKind.IMAGE: 30 * 1024 * 1024,
    H3MediaKind.VIDEO: 50 * 1024 * 1024,
    H3MediaKind.AUDIO: 15 * 1024 * 1024,
}


def build_h3_prompt(document: ProjectDocument, shot: ShotPlan) -> str:
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    dialogue = [line_by_id[line_id] for line_id in shot.dialogue_line_ids if line_id in line_by_id]
    dialogue_text = "；".join(
        f'{line.speaker}用普通话说：“{line.spoken_text}”' for line in dialogue
    ) or "无对白，只保留计划中的环境声和动作声。"
    structured_constraints = _format_structured_action_constraints(document, shot)
    visible_continuity = _format_visible_continuity(document, shot)
    screen_direction = _format_visible_screen_direction(document, shot)
    prompt = (
        "真人都市悬疑短剧，电影级写实质感，9:16竖屏。"
        f"[Shot {shot.sequence}] 时长{math.ceil(shot.duration_seconds)}秒。"
        f"叙事目标：{shot.narrative_purpose}。"
        f"画面：{shot.visual}。景别与构图：{shot.framing}。"
        f"镜头运动：{shot.camera}。{visible_continuity}"
        f"{screen_direction}"
        f"声音：{shot.audio_intent}。{dialogue_text}"
        f"{structured_constraints}"
        "只保持本镜头明确出现的人物、服装、场景、道具及左右手方向连续；"
        "不要主动加入本镜头未提及的角色或道具；"
        "禁止错误字幕、水印、画面内可读文字和无关对白。"
    )
    if len(prompt) > 7000:
        raise H3CapabilityError("Compiled H3 prompt exceeds 7000 characters")
    return prompt


def _format_visible_continuity(document: ProjectDocument, shot: ShotPlan) -> str:
    if _reference_plan_omits_visible_props(document, shot):
        return (
            "表演保持克制并遵循本镜头画面描述。画面只呈现参考计划中的"
            "可见主体，前景保持空净；未列为可见主体的连续性状态不在本镜头中呈现。"
        )
    return (
        f"表演与动作从“{shot.continuity.action_in}”自然衔接到"
        f'“{shot.continuity.action_out}”。'
        f"连续性状态：开始时{shot.continuity.prop_state_in}；"
        f"结束时{shot.continuity.prop_state_out}。"
    )


def _format_visible_screen_direction(document: ProjectDocument, shot: ShotPlan) -> str:
    if _reference_plan_omits_visible_props(document, shot):
        return (
            f"人物位置：{shot.continuity.character_positions}；"
            "人物视线保持本镜头画面描述中的对话方向和银幕轴线。"
        )
    return (
        f"人物位置与视线：{shot.continuity.character_positions}；"
        f"{shot.continuity.eyeline}。"
    )


def _reference_plan_omits_visible_props(
    document: ProjectDocument,
    shot: ShotPlan,
) -> bool:
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is not None:
        visible_subject_ids = {
            requirement.subject_asset_id for requirement in plan.requirements
        }
        visible_subject_ids.update(beat.actor_asset_id for beat in plan.action_beats)
        for interaction in plan.interactions:
            visible_subject_ids.add(interaction.actor_asset_id)
            visible_subject_ids.add(interaction.target_asset_id)
        if visible_subject_ids:
            asset_kind_by_id = {asset.id: asset.kind for asset in document.assets}
            visible_prop = any(
                asset_kind_by_id.get(asset_id) == AssetKind.PROP
                for asset_id in visible_subject_ids
            )
            return not visible_prop
    return False


def _format_structured_action_constraints(
    document: ProjectDocument,
    shot: ShotPlan,
) -> str:
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is None or (not plan.action_beats and not plan.interactions):
        return ""
    asset_names = {asset.id: asset.name for asset in document.assets}
    parts: list[str] = []
    if plan.action_beats:
        beats = "；".join(
            (
                f"{beat.order}.{asset_names.get(beat.actor_asset_id, beat.actor_asset_id)}"
                f"{beat.action}"
                + (f"，目标锚点{beat.target_anchor}" if beat.target_anchor else "")
                + (
                    f"，完成状态{beat.required_end_state}"
                    if beat.required_end_state
                    else ""
                )
            )
            for beat in sorted(plan.action_beats, key=lambda item: item.order)
        )
        parts.append(f"动作节拍必须依序执行：{beats}。")
    for interaction in plan.interactions:
        attachments = "、".join(
            f"{item.anchor}固定在{item.surface}"
            for item in interaction.rigid_attachments
        )
        contact = ""
        if interaction.contact_anchor is not None:
            contact = f"接触点为{interaction.contact_anchor}"
            if interaction.contact_surface is not None:
                contact += f"，位于{interaction.contact_surface}"
            if interaction.contact_persists:
                contact += "，本镜头内保持接触，除非后续动作节拍明确改变该状态"
            contact += "；"
        unchanged = (
            f"{','.join(interaction.unchanged_anchors)}保持不动；"
            if interaction.unchanged_anchors
            else ""
        )
        rigid = f"刚性附件：{attachments}；" if attachments else ""
        parts.append(
            "物理交互："
            f"{asset_names.get(interaction.actor_asset_id, interaction.actor_asset_id)}"
            f"以{interaction.effector}{interaction.action}"
            f"{asset_names.get(interaction.target_asset_id, interaction.target_asset_id)}"
            f"的{interaction.target_anchor}，状态从{interaction.start_state}到"
            f"{interaction.end_state}；{contact}{rigid}{unchanged}"
            "部件不得消失、换面、复制、脱离或漂移。"
        )
    return "".join(parts)


def validate_h3_probe(
    *,
    route: VideoGenerationRoute,
    prompt: str,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
) -> None:
    if not prompt.strip():
        raise H3CapabilityError("H3 requires a non-empty text prompt")
    if len(prompt) > 7000:
        raise H3CapabilityError("H3 prompt exceeds 7000 characters")
    if duration_seconds < 4 or duration_seconds > 15:
        raise H3CapabilityError("H3 duration must be an integer from 4 to 15 seconds")
    if len(media_inputs) > 12:
        raise H3CapabilityError("H3 accepts at most 12 reference files")

    roles = Counter(item.role for item in media_inputs)
    for item in media_inputs:
        if _ROLE_KIND[item.role] != item.kind:
            raise H3CapabilityError(f"Role {item.role.value} requires {_ROLE_KIND[item.role].value}")
        _validate_local_file(item)

    frame_roles = {H3MediaRole.FIRST_FRAME, H3MediaRole.LAST_FRAME}
    reference_roles = {
        H3MediaRole.REFERENCE_IMAGE,
        H3MediaRole.REFERENCE_VIDEO,
        H3MediaRole.REFERENCE_AUDIO,
    }
    has_frames = any(role in roles for role in frame_roles)
    has_references = any(role in roles for role in reference_roles)
    if has_frames and has_references:
        raise H3CapabilityError("H3 frame roles and reference roles are mutually exclusive")
    if roles[H3MediaRole.FIRST_FRAME] > 1 or roles[H3MediaRole.LAST_FRAME] > 1:
        raise H3CapabilityError("H3 accepts at most one first frame and one last frame")
    if roles[H3MediaRole.LAST_FRAME] and not roles[H3MediaRole.FIRST_FRAME]:
        raise H3CapabilityError("H3 last_frame must be paired with first_frame")
    if roles[H3MediaRole.REFERENCE_IMAGE] > 9:
        raise H3CapabilityError("H3 accepts at most nine reference images")
    if roles[H3MediaRole.REFERENCE_VIDEO] > 3:
        raise H3CapabilityError("H3 accepts at most three reference videos")
    if roles[H3MediaRole.REFERENCE_AUDIO] > 3:
        raise H3CapabilityError("H3 accepts at most three reference audio clips")

    if route == VideoGenerationRoute.DIRECT and media_inputs:
        raise H3CapabilityError("DIRECT does not accept media inputs")
    if route == VideoGenerationRoute.FRAME_FIRST:
        if roles != Counter({H3MediaRole.FIRST_FRAME: 1}):
            raise H3CapabilityError("FRAME_FIRST requires exactly one first_frame image")
    if route == VideoGenerationRoute.FRAME_FIRST_LAST:
        expected = Counter({H3MediaRole.FIRST_FRAME: 1, H3MediaRole.LAST_FRAME: 1})
        if roles != expected:
            raise H3CapabilityError(
                "FRAME_FIRST_LAST requires exactly one first_frame and one last_frame image"
            )
    if route == VideoGenerationRoute.REFERENCE:
        if not media_inputs or not has_references or has_frames:
            raise H3CapabilityError("REFERENCE requires at least one reference media item")


def compile_h3_request(
    *,
    route: VideoGenerationRoute,
    prompt: str,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
    resolve_url: Callable[[H3MediaInput], str],
) -> H3VideoRequest:
    validate_h3_probe(
        route=route,
        prompt=prompt,
        duration_seconds=duration_seconds,
        media_inputs=media_inputs,
    )
    content = [H3ContentItem(type="text", text=prompt)]
    for media in media_inputs:
        url = H3UrlValue(url=resolve_url(media))
        if media.kind == H3MediaKind.IMAGE:
            content.append(H3ContentItem(type="image_url", image_url=url, role=media.role))
        elif media.kind == H3MediaKind.VIDEO:
            content.append(H3ContentItem(type="video_url", video_url=url, role=media.role))
        else:
            content.append(H3ContentItem(type="audio_url", audio_url=url, role=media.role))
    ratio = None if route in {
        VideoGenerationRoute.FRAME_FIRST,
        VideoGenerationRoute.FRAME_FIRST_LAST,
    } else "9:16"
    return H3VideoRequest(content=content, duration=duration_seconds, ratio=ratio)


def _validate_local_file(item: H3MediaInput) -> None:
    raw_path = item.source.local_path
    if raw_path is None:
        return
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise H3CapabilityError(f"Local H3 input does not exist: {path}")
    if path.stat().st_size > _SIZE_LIMITS[item.kind]:
        raise H3CapabilityError(f"Local H3 input exceeds the {item.kind.value} size limit")
    extension = path.suffix.lower()
    supported = {
        H3MediaKind.IMAGE: _IMAGE_EXTENSIONS,
        H3MediaKind.VIDEO: _VIDEO_EXTENSIONS,
        H3MediaKind.AUDIO: _AUDIO_EXTENSIONS,
    }[item.kind]
    if extension not in supported:
        raise H3CapabilityError(f"Unsupported H3 {item.kind.value} extension: {extension}")
