"""MiniMax-H3 model skill for compiling provider-neutral shot facts to H3 Prompt IR.

The field names, section order, reference labels, dialogue markup, and keyframe
alignment follow MiniMax's public ``h3-prompt-writing`` skill.  Project-specific
facts remain owned by ShotPlan, ProjectBible, and ReferencePlan; this module is
only the model adapter at the final prompt boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from short_drama.h3_models import (
    H3MediaInput,
    H3MediaKind,
    H3VideoGuide,
    H3VideoGuideKind,
)
from short_drama.models import (
    AssetKind,
    AudioKind,
    ProjectDocument,
    ShotPlan,
    VideoGenerationRoute,
)
from short_drama.production_profiles import production_skill
from short_drama.style_context import declared_style_value


_BASE_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
_REFERENCE_FIELDS = (
    "subject_definitions:",
    "summary:",
    "retention_analysis:",
    "detailed_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)


@dataclass(frozen=True)
class _PromptReference:
    label: str
    media: H3MediaInput
    subject: str
    descriptor: str
    purpose: str
    video_guide: H3VideoGuide | None = None


def compile_h3_prompt_ir(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute,
    duration_seconds: int | None = None,
    media_inputs: list[H3MediaInput] | None = None,
    video_guides: list[H3VideoGuide] | None = None,
) -> str:
    """Compile one provider-neutral shot into the official H3 prompt shape."""

    duration = duration_seconds or math.ceil(shot.duration_seconds)
    inputs = list(media_inputs or [])
    if route == VideoGenerationRoute.REFERENCE:
        prompt = _compile_reference_prompt(
            document,
            shot,
            duration=duration,
            media_inputs=inputs,
            video_guides=video_guides or [],
        )
    else:
        prompt = _compile_base_prompt(
            document,
            shot,
            route=route,
            duration=duration,
        )
    if len(prompt) > 7000:
        raise ValueError("Compiled H3 prompt exceeds 7000 characters")
    return prompt


def validate_h3_prompt_ir(
    prompt: str,
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    media_inputs: list[H3MediaInput] | None = None,
) -> list[str]:
    """Validate official structure plus the project's locked dialogue facts."""

    stripped = prompt.strip()
    if not stripped:
        raise ValueError("H3 prompt is empty")
    if len(stripped) > 7000:
        raise ValueError("H3 prompt exceeds 7000 characters")
    if "```" in stripped:
        raise ValueError("H3 prompt contains Markdown fencing")
    if "9:16" not in stripped:
        raise ValueError("H3 prompt omitted the locked 9:16 ratio")

    fields = _REFERENCE_FIELDS if route == VideoGenerationRoute.REFERENCE else _BASE_FIELDS
    positions = [stripped.find(field) for field in fields]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError(
            "H3 prompt omitted or reordered the required fields: " + ", ".join(fields)
        )

    if route == VideoGenerationRoute.FRAME_FIRST:
        required = (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
        if not stripped.startswith(required):
            raise ValueError("H3 I2VA prompt omitted the official first-frame alignment line")
    elif route == VideoGenerationRoute.FRAME_FIRST_LAST:
        alignment = (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {duration_seconds:.2f}-second mark "
            "of the target video."
        )
        if not stripped.startswith(alignment):
            raise ValueError("H3 FL2VA prompt omitted the official first/last alignment line")

    duration_patterns = (
        rf"\b{duration_seconds}-second\b",
        rf"\b{duration_seconds}\s+seconds?\b",
        rf"\b{duration_seconds:.2f}-second\b",
        rf"{duration_seconds}\s*秒",
    )
    if not any(re.search(pattern, stripped) for pattern in duration_patterns):
        raise ValueError(f"H3 prompt omitted the locked {duration_seconds}-second duration")

    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    locked_lines = [
        line_by_id[line_id]
        for line_id in shot.dialogue_line_ids
        if line_id in line_by_id
    ]
    for line in locked_lines:
        if line.spoken_text not in stripped:
            raise ValueError(
                f"H3 prompt changed or omitted locked dialogue for {line.speaker}"
            )
        if line.speaker not in stripped:
            raise ValueError(f"H3 prompt changed or omitted locked speaker {line.speaker}")

    if route == VideoGenerationRoute.REFERENCE:
        for reference in _reference_labels(media_inputs or []):
            if reference not in stripped:
                raise ValueError(f"H3 Ref2VA prompt omitted reference label {reference}")

    return [
        "official MiniMax-H3 prompt field order preserved",
        f"locked 9:16 ratio and {duration_seconds}-second duration preserved",
        (
            f"{len(locked_lines)} locked dialogue line(s) preserved verbatim"
            if locked_lines
            else "shot contains no locked dialogue"
        ),
    ]


def prompt_schema_for_route(route: VideoGenerationRoute) -> dict[str, object]:
    """Small serializable contract for the optional GPT prompt compiler."""

    if route == VideoGenerationRoute.REFERENCE:
        return {
            "mode": "Ref2VA",
            "fields_in_order": list(_REFERENCE_FIELDS),
            "rewrite_language": "English, except original dialogue and visible text",
        }
    mode = {
        VideoGenerationRoute.DIRECT: "T2VA",
        VideoGenerationRoute.FRAME_FIRST: "I2VA",
        VideoGenerationRoute.FRAME_FIRST_LAST: "FL2VA",
    }[route]
    return {
        "mode": mode,
        "fields_in_order": list(_BASE_FIELDS),
        "rewrite_language": "English, except original dialogue and visible text",
    }


def _compile_base_prompt(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute,
    duration: int,
) -> str:
    instruction = ""
    if route == VideoGenerationRoute.FRAME_FIRST:
        instruction = (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        )
    elif route == VideoGenerationRoute.FRAME_FIRST_LAST:
        instruction = (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {duration:.2f}-second mark "
            "of the target video.\n\n"
        )

    route_anchor = {
        VideoGenerationRoute.DIRECT: "",
        VideoGenerationRoute.FRAME_FIRST: (
            "The opening identity, wardrobe, setting, lighting, subject placement, and "
            "composition fully match <Picture 1>. "
        ),
        VideoGenerationRoute.FRAME_FIRST_LAST: (
            "The shot begins in the exact visible state and composition of <Picture 1>, "
            "then follows the temporal behavior declared by the ShotPlan—including stillness, "
            "limited or discontinuous change, or continuous motion—and reaches the exact pose, "
            "object state, spacing, camera angle, lighting, and composition of <Picture 2> at "
            "the end. "
        ),
    }[route]
    description = _timeline_description(document, shot, duration=duration)
    return (
        f"{instruction}"
        f"integrated_multimodal_description: [Shot 1] {route_anchor}{description}\n\n"
        f"overall_soundscape: {_soundscape(shot)}\n\n"
        "non_diegetic_music: N/A"
    )


def _compile_reference_prompt(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    duration: int,
    media_inputs: list[H3MediaInput],
    video_guides: list[H3VideoGuide],
) -> str:
    skill = production_skill(document.project.production_profile)
    references = _resolve_references(document, media_inputs, video_guides)
    definitions = " ".join(
        f"{item.label} defines {item.subject}: {item.descriptor}."
        for item in references
    ) or "No external reference media is declared."
    retention = " ".join(_retention_sentence(item) for item in references) or "N/A"
    bindings = " ".join(
        f"{item.subject} follows {item.label} for {item.purpose}."
        for item in references
    )
    timeline = _timeline_description(document, shot, duration=duration)
    if bindings:
        timeline = f"{timeline} Reference use: {bindings}"
    return (
        f"subject_definitions: {definitions}\n\n"
        "summary: Ref2VA generation for a "
        f"{duration}-second 9:16 {skill.label} shot. Only the declared "
        "subjects and reference relationships are used.\n\n"
        f"retention_analysis: {retention}\n\n"
        f"detailed_description: [Shot 1] {timeline}\n\n"
        f"overall_soundscape: {_soundscape(shot)}\n\n"
        "non_diegetic_music: N/A"
    )


def _timeline_description(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    duration: int,
) -> str:
    hidden_props = _reference_plan_omits_visible_props(document, shot)
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        document.bible_versions[-1],
    )
    style = bible.style
    style_fields = [
        f"{label}={value}"
        for label, raw_value in (
            ("medium", style.medium),
            ("cinematography", style.cinematography),
            ("color", style.color),
            ("texture", style.texture),
        )
        if (value := declared_style_value(raw_value)) is not None
    ]
    opening = f"Compose the shot for a 9:16 vertical frame over {duration} seconds."
    if style_fields:
        opening = (
            "Follow the project's declared style: "
            + "; ".join(style_fields)
            + ". "
            + opening
        )
    parts = [
        opening,
        f"The {shot.framing} composition shows the following observable scene: {shot.visual}.",
        f"Narrative purpose: {shot.narrative_purpose}.",
        f"Camera direction: {shot.camera}.",
    ]
    if hidden_props:
        parts.append(
            "Show only the visible subjects declared by the shot reference plan; keep the "
            "foreground clean and do not reveal off-screen continuity props."
        )
        parts.append(
            f"Character staging: {shot.continuity.character_positions}; preserve the declared "
            "dialogue eyeline and screen axis."
        )
    else:
        parts.extend(
            [
                f"The visible state develops from {shot.continuity.action_in} "
                f"to {shot.continuity.action_out}, using only the form and amount of motion "
                "required by the ShotPlan and project style.",
                "Visible continuity: "
                f"the opening state is {shot.continuity.prop_state_in}; "
                f"the ending state is {shot.continuity.prop_state_out}.",
                f"Character staging and eyeline: {shot.continuity.character_positions}; "
                f"{shot.continuity.eyeline}.",
            ]
        )
    action_constraints = _structured_action_constraints(document, shot)
    if action_constraints:
        parts.append(action_constraints)
    dialogue = _dialogue_description(document, shot)
    if dialogue:
        parts.append(dialogue)
    else:
        parts.append("No one speaks; only the declared ambient and physical sounds are heard.")
    parts.extend(
        [
            "Do not introduce undeclared characters, props, actions, scene changes, or dialogue.",
            "No subtitles, watermark, or additional readable text appear in the image.",
        ]
    )
    return " ".join(parts)


def _dialogue_description(document: ProjectDocument, shot: ShotPlan) -> str:
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    lines = [
        line_by_id[line_id]
        for line_id in shot.dialogue_line_ids
        if line_id in line_by_id
    ]
    speaker_ids: dict[str, str] = {}
    rendered: list[str] = []
    for line in lines:
        speaker_id = speaker_ids.setdefault(line.speaker, f"S{len(speaker_ids) + 1}")
        if line.audio_kind == AudioKind.VOICEOVER:
            rendered.append(
                f"The voice identified as {line.speaker} ({speaker_id}) says in an off-screen "
                f"voiceover: <d>[Chinese] {line.spoken_text}</d> while every on-screen "
                "character's lips remain completely closed."
            )
        else:
            rendered.append(
                f"{line.speaker} ({speaker_id}) says in Mandarin Chinese: "
                f"<d>[Chinese] {line.spoken_text}</d>."
            )
    return " ".join(rendered)


def _structured_action_constraints(document: ProjectDocument, shot: ShotPlan) -> str:
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is None or (not plan.action_beats and not plan.interactions):
        return ""
    asset_names = {asset.id: asset.name for asset in document.assets}
    parts: list[str] = []
    if plan.action_beats:
        beats = "; ".join(
            (
                f"{beat.order}. {asset_names.get(beat.actor_asset_id, beat.actor_asset_id)} "
                f"{beat.action}"
                + (f", targeting {beat.target_anchor}" if beat.target_anchor else "")
                + (
                    f", ending in {beat.required_end_state}"
                    if beat.required_end_state
                    else ""
                )
            )
            for beat in sorted(plan.action_beats, key=lambda item: item.order)
        )
        parts.append(f"Perform the declared action beats in order: {beats}.")
    for interaction in plan.interactions:
        actor = asset_names.get(interaction.actor_asset_id, interaction.actor_asset_id)
        target = asset_names.get(interaction.target_asset_id, interaction.target_asset_id)
        detail = (
            f"Physical interaction: {actor} uses {interaction.effector} to "
            f"{interaction.action} {target} at {interaction.target_anchor}, changing it from "
            f"{interaction.start_state} to {interaction.end_state}."
        )
        if interaction.contact_anchor is not None:
            detail += f" The contact point is {interaction.contact_anchor}"
            if interaction.contact_surface is not None:
                detail += f" on {interaction.contact_surface}"
            detail += "."
            if interaction.contact_persists:
                detail += (
                    " Maintain that contact through the shot unless a later declared action "
                    "beat explicitly changes it."
                )
        if interaction.rigid_attachments:
            attachments = "; ".join(
                f"{item.anchor} remains rigidly attached to {item.surface}"
                for item in interaction.rigid_attachments
            )
            detail += f" {attachments}."
        if interaction.unchanged_anchors:
            detail += (
                " Keep these anchors fixed: " + ", ".join(interaction.unchanged_anchors) + "."
            )
        detail += " Parts never disappear, switch faces, duplicate, detach, or drift."
        parts.append(detail)
    return " ".join(parts)


def _soundscape(shot: ShotPlan) -> str:
    return (
        f"The diegetic sound follows this declared intent: {shot.audio_intent}. "
        "Physical action sounds remain synchronized with visible contact and movement."
    )


def _resolve_references(
    document: ProjectDocument,
    media_inputs: list[H3MediaInput],
    video_guides: list[H3VideoGuide],
) -> list[_PromptReference]:
    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    guide_index = {guide.id: guide for guide in video_guides}
    counters = {H3MediaKind.IMAGE: 0, H3MediaKind.VIDEO: 0, H3MediaKind.AUDIO: 0}
    result: list[_PromptReference] = []
    for media in media_inputs:
        counters[media.kind] += 1
        prefix = {
            H3MediaKind.IMAGE: "Picture",
            H3MediaKind.VIDEO: "Video",
            H3MediaKind.AUDIO: "Audio",
        }[media.kind]
        label = f"<{prefix} {counters[media.kind]}>"
        guide = guide_index.get(media.video_guide_id or "")
        if guide is not None:
            result.append(
                _PromptReference(
                    label=label,
                    media=media,
                    subject="the Route Agent's motion and continuity guide",
                    descriptor=guide.rationale,
                    purpose=(
                        "camera path and pacing only"
                        if guide.kind == H3VideoGuideKind.CAMERA_PREVIS
                        else "the immediately preceding motion state and screen direction only"
                    ),
                    video_guide=guide,
                )
            )
            continue
        resolved = version_index.get(media.asset_version_id or "")
        if resolved is None:
            subject = {
                H3MediaKind.IMAGE: "the declared visual reference",
                H3MediaKind.VIDEO: "the declared motion reference",
                H3MediaKind.AUDIO: "the declared voice or audio reference",
            }[media.kind]
            descriptor = f"A {media.role.value} input bound by the Route Agent"
        else:
            asset, version = resolved
            subject = asset.name
            descriptor = version.descriptor
        purpose = {
            H3MediaKind.IMAGE: "identity, wardrobe, object appearance, or location geometry",
            H3MediaKind.VIDEO: "declared motion, timing, camera behavior, or continuity",
            H3MediaKind.AUDIO: "voice identity and acoustic character, not copied words",
        }[media.kind]
        result.append(
            _PromptReference(
                label=label,
                media=media,
                subject=subject,
                descriptor=descriptor,
                purpose=purpose,
            )
        )
    return result


def _retention_sentence(reference: _PromptReference) -> str:
    if reference.video_guide is not None:
        if reference.video_guide.kind == H3VideoGuideKind.CAMERA_PREVIS:
            return (
                f"{reference.label} transfers only the coarse camera path and pacing; its "
                "low-fidelity compression and interpolation artifacts are not retained. The "
                "project's declared style and intended amount of motion remain authoritative."
            )
        return (
            f"{reference.label} transfers only the terminal motion state, screen direction, "
            "and compatible camera momentum; its dialogue, prior action, cuts, and accidental "
            "defects are not repeated."
        )
    if reference.media.kind == H3MediaKind.AUDIO:
        return (
            f"{reference.label} preserves the referenced speaker's voice identity, pitch, "
            "timbre, and delivery character while all spoken words remain the locked dialogue."
        )
    if reference.media.kind == H3MediaKind.VIDEO:
        return (
            f"{reference.label} transfers only the declared motion and camera relationship; "
            "unrequested subjects, dialogue, and edits are not copied."
        )
    return (
        f"{reference.label} fully preserves the declared subject identity and stable visible "
        "appearance while pose and framing change only as required by this shot."
    )


def _reference_labels(media_inputs: list[H3MediaInput]) -> list[str]:
    counters = {H3MediaKind.IMAGE: 0, H3MediaKind.VIDEO: 0, H3MediaKind.AUDIO: 0}
    labels: list[str] = []
    for media in media_inputs:
        counters[media.kind] += 1
        prefix = {
            H3MediaKind.IMAGE: "Picture",
            H3MediaKind.VIDEO: "Video",
            H3MediaKind.AUDIO: "Audio",
        }[media.kind]
        labels.append(f"<{prefix} {counters[media.kind]}>")
    return labels


def _reference_plan_omits_visible_props(
    document: ProjectDocument,
    shot: ShotPlan,
) -> bool:
    plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    if plan is None:
        return False
    visible_subject_ids = {
        requirement.subject_asset_id for requirement in plan.requirements
    }
    visible_subject_ids.update(beat.actor_asset_id for beat in plan.action_beats)
    for interaction in plan.interactions:
        visible_subject_ids.add(interaction.actor_asset_id)
        visible_subject_ids.add(interaction.target_asset_id)
    if not visible_subject_ids:
        return False
    kind_by_id = {asset.id: asset.kind for asset in document.assets}
    return not any(
        kind_by_id.get(asset_id) == AssetKind.PROP for asset_id in visible_subject_ids
    )
