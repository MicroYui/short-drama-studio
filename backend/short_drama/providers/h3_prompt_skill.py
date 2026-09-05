"""MiniMax-H3 model skill for compiling provider-neutral shot facts to H3 Prompt IR.

The field names, section order, reference labels, dialogue markup, and keyframe
alignment follow MiniMax's public ``h3-prompt-writing`` skill.  Project-specific
facts remain owned by ShotPlan, ProjectBible, and ReferencePlan; this module is
only the model adapter at the final prompt boundary.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from short_drama.h3_models import (
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3VideoGuide,
    H3VideoGuideKind,
)
from short_drama.models import (
    AssetKind,
    AudioKind,
    ProjectDocument,
    ShotPlan,
    ShotTimelineBeat,
    VideoGenerationRoute,
)
from short_drama.production_profiles import production_skill
from short_drama.providers.h3_media_bindings import (
    ResolvedH3MediaBinding,
    resolve_h3_media_bindings,
)
from short_drama.style_context import declared_style_value

if TYPE_CHECKING:
    from short_drama.production_contracts import GenerationUnit


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


def compile_h3_generation_unit_prompt_ir(
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    route: VideoGenerationRoute,
    duration_seconds: int | None = None,
    media_inputs: list[H3MediaInput] | None = None,
    video_guides: list[H3VideoGuide] | None = None,
) -> str:
    """Compile an ordered multi-shot generation unit into one H3 request prompt.

    Editorial shots remain explicit in the prompt and in the generation-unit windows;
    combining them into one provider request never erases their downstream edit boundaries.
    """

    duration = duration_seconds or math.ceil(unit.requested_duration_seconds)
    if not shots:
        raise ValueError("An H3 generation unit requires at least one editorial shot")
    shot_by_id = {shot.id: shot for shot in shots}
    ordered_shots = [
        shot_by_id[window.shot_id]
        for window in unit.shot_windows
        if window.shot_id in shot_by_id
    ]
    if len(ordered_shots) != len(unit.shot_windows):
        raise ValueError("Generation-unit windows reference an unknown editorial shot")
    if len({shot.id for shot in ordered_shots}) != len(ordered_shots):
        raise ValueError("Generation-unit editorial shots must be unique")

    inputs = list(media_inputs or [])
    description = _generation_unit_timeline_description(
        document,
        unit,
        ordered_shots,
        duration=duration,
        references=(
            resolve_h3_media_bindings(document, inputs, video_guides or [])
            if route == VideoGenerationRoute.REFERENCE
            else []
        ),
    )
    instruction = _generation_unit_frame_alignment(route, duration)
    soundscape = " ".join(
        dict.fromkeys(_soundscape(shot) for shot in ordered_shots)
    )
    if route == VideoGenerationRoute.REFERENCE:
        references = resolve_h3_media_bindings(document, inputs, video_guides or [])
        definitions = " ".join(_reference_definitions(references)) or (
            "No external reference media is declared."
        )
        retention = " ".join(_retention_analysis(references)) or "N/A"
        prompt = (
            f"{instruction}"
            f"subject_definitions: {definitions}\n\n"
            "summary: Ref2VA generation for one "
            f"{duration}-second 9:16 video containing {len(ordered_shots)} ordered "
            "editorial shots.\n\n"
            f"retention_analysis: {retention}\n\n"
            f"detailed_description: {description}\n\n"
            f"overall_soundscape: {soundscape}\n\n"
            "non_diegetic_music: N/A"
        )
    else:
        prompt = (
            f"{instruction}"
            f"integrated_multimodal_description: {description}\n\n"
            f"overall_soundscape: {soundscape}\n\n"
            "non_diegetic_music: N/A"
        )
    if len(prompt) > 7000:
        raise ValueError("Compiled H3 generation-unit prompt exceeds 7000 characters")
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
    elif route == VideoGenerationRoute.FRAME_LAST:
        alignment = (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot 1]) aligns with the {duration_seconds:.2f}-second "
            "mark of the target video."
        )
        if not stripped.startswith(alignment):
            raise ValueError("H3 L2VA prompt omitted the official last-frame alignment line")
    elif route == VideoGenerationRoute.FRAME_FIRST_LAST:
        alignment = (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {duration_seconds:.2f}-second mark "
            "of the target video."
        )
        if not stripped.startswith(alignment):
            raise ValueError("H3 FL2VA prompt omitted the official first/last alignment line")

    duration_markers = (
        f"{duration_seconds}-second",
        f"{duration_seconds} second",
        f"{duration_seconds} seconds",
        f"{duration_seconds:.2f}-second",
        f"{duration_seconds}秒",
        f"{duration_seconds} 秒",
    )
    if not any(marker in stripped for marker in duration_markers):
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
        references = resolve_h3_media_bindings(document, media_inputs or [], [])
        for reference in references:
            if reference.provider_label not in stripped:
                raise ValueError(
                    "H3 Ref2VA prompt omitted reference label "
                    f"{reference.provider_label}"
                )
            if reference.prompt_label not in stripped:
                raise ValueError(
                    "H3 Ref2VA prompt omitted reusable content label "
                    f"{reference.prompt_label}"
                )
            binding = f"@{reference.alias} = {reference.prompt_label}"
            if reference.media.reference_alias is not None and binding not in stripped:
                raise ValueError(f"H3 Ref2VA prompt omitted semantic binding {binding}")

    if "[Shot 2]" in stripped:
        raise ValueError("One ShotPlan cannot compile undeclared additional editorial shots")
    beat_markers = [
        _beat_time_marker(beat.start_seconds, beat.end_seconds)
        for beat in shot.timeline_beats
    ]
    for index, (beat, marker) in enumerate(zip(shot.timeline_beats, beat_markers, strict=True)):
        marker = _beat_time_marker(beat.start_seconds, beat.end_seconds)
        marker_position = stripped.find(marker)
        if marker_position < 0:
            raise ValueError(f"H3 prompt omitted locked timeline range {marker}")
        next_position = (
            stripped.find(beat_markers[index + 1], marker_position + len(marker))
            if index + 1 < len(beat_markers)
            else len(stripped)
        )
        segment = stripped[marker_position:next_position]
        for line_id in beat.dialogue_line_ids:
            line = line_by_id.get(line_id)
            if line is not None and line.spoken_text not in segment:
                raise ValueError(
                    f"H3 prompt moved dialogue for {line.speaker} outside its locked time range"
                )

    return [
        "official MiniMax-H3 prompt field order preserved",
        f"locked 9:16 ratio and {duration_seconds}-second duration preserved",
        (
            f"{len(locked_lines)} locked dialogue line(s) preserved verbatim"
            if locked_lines
            else "shot contains no locked dialogue"
        ),
    ]


def validate_h3_generation_unit_prompt_ir(
    prompt: str,
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    media_inputs: list[H3MediaInput] | None = None,
) -> list[str]:
    """Validate a multi-shot prompt without applying creative-content gates."""

    stripped = prompt.strip()
    if not stripped:
        raise ValueError("H3 generation-unit prompt is empty")
    if len(stripped) > 7000:
        raise ValueError("H3 generation-unit prompt exceeds 7000 characters")
    if "```" in stripped:
        raise ValueError("H3 generation-unit prompt contains Markdown fencing")
    if "9:16" not in stripped:
        raise ValueError("H3 generation-unit prompt omitted the locked 9:16 ratio")
    fields = _REFERENCE_FIELDS if route == VideoGenerationRoute.REFERENCE else _BASE_FIELDS
    positions = [stripped.find(field) for field in fields]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError(
            "H3 generation-unit prompt omitted or reordered required fields: "
            + ", ".join(fields)
        )
    expected_alignment = _generation_unit_frame_alignment(route, duration_seconds)
    if expected_alignment and not stripped.startswith(expected_alignment):
        raise ValueError("H3 generation-unit prompt omitted its frame-alignment line")

    shot_by_id = {shot.id: shot for shot in shots}
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    for index, window in enumerate(unit.shot_windows, start=1):
        shot = shot_by_id.get(window.shot_id)
        if shot is None:
            raise ValueError(
                f"Generation-unit window references unknown shot {window.shot_id}"
            )
        marker = _generation_unit_window_marker(
            index,
            window.source_in_seconds,
            window.duration_seconds,
        )
        if marker not in stripped:
            raise ValueError(f"H3 generation-unit prompt omitted locked window {marker}")
        for line_id in shot.dialogue_line_ids:
            line = line_by_id.get(line_id)
            if line is not None and (
                line.spoken_text not in stripped or line.speaker not in stripped
            ):
                raise ValueError(
                    f"H3 generation-unit prompt changed or omitted dialogue for {line.speaker}"
                )

    if route == VideoGenerationRoute.REFERENCE:
        references = resolve_h3_media_bindings(document, media_inputs or [], [])
        for reference in references:
            if reference.provider_label not in stripped:
                raise ValueError(
                    "H3 Ref2VA generation-unit prompt omitted reference label "
                    f"{reference.provider_label}"
                )
            if reference.media.reference_alias is not None and (
                f"@{reference.alias} = {reference.prompt_label}" not in stripped
            ):
                raise ValueError(
                    "H3 Ref2VA generation-unit prompt omitted semantic binding "
                    f"@{reference.alias}"
                )
    return [
        "official MiniMax-H3 prompt field order preserved",
        f"{len(unit.shot_windows)} editorial shot window(s) preserved in order",
        f"locked 9:16 ratio and {duration_seconds}-second provider duration preserved",
    ]


def prompt_schema_for_route(route: VideoGenerationRoute) -> dict[str, object]:
    """Small serializable contract for the optional GPT prompt compiler."""

    if route == VideoGenerationRoute.REFERENCE:
        return {
            "mode": "Ref2VA",
            "fields_in_order": list(_REFERENCE_FIELDS),
            "rewrite_language": "English, except original dialogue and visible text",
            "reference_binding": (
                "For REFERENCE_IMAGE, preserve @semantic_alias = <Subject N> and define "
                "that reusable visible content as extracted from its uploaded <Picture N> "
                "source or sources. Bindings with the same <Subject N> are multiple assets "
                "for one subject, not separate subjects, and every source <Picture N> remains "
                "traceable. Concrete first, key, and last frames keep <Picture N>. Preserve "
                "supplied <Video N> and <Audio N> mappings."
            ),
        }
    mode = {
        VideoGenerationRoute.DIRECT: "T2VA",
        VideoGenerationRoute.FRAME_FIRST: "I2VA",
        VideoGenerationRoute.FRAME_LAST: "L2VA",
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
    elif route == VideoGenerationRoute.FRAME_LAST:
        instruction = (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot 1]) aligns with the {duration:.2f}-second mark "
            "of the target video.\n\n"
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
        VideoGenerationRoute.FRAME_LAST: (
            "Infer a plausible compatible opening state from the locked ShotPlan, then let "
            "the declared action, object state, camera, lighting, and composition gradually "
            "converge on the exact visible state of <Picture 1> at the end. <Picture 1> is "
            "only the final frame and must not be treated as the opening image. "
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
    references = resolve_h3_media_bindings(document, media_inputs, video_guides)
    definitions = " ".join(_reference_definitions(references)) or (
        "No external reference media is declared."
    )
    retention = " ".join(_retention_analysis(references)) or "N/A"
    bindings = " ".join(
        f"{item.subject} follows @{item.alias} ({item.prompt_label}) for {item.purpose}."
        for item in references
    )
    timeline = _timeline_description(
        document,
        shot,
        duration=duration,
        references=references,
    )
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


def _generation_unit_frame_alignment(
    route: VideoGenerationRoute,
    duration: int,
) -> str:
    if route == VideoGenerationRoute.FRAME_FIRST:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        )
    if route == VideoGenerationRoute.FRAME_LAST:
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from the final editorial shot) aligns with the {duration:.2f}-second "
            "mark of the target video.\n\n"
        )
    if route == VideoGenerationRoute.FRAME_FIRST_LAST:
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from [Shot 1]) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from the final editorial shot) aligns with the {duration:.2f}-second "
            "mark of the target video.\n\n"
        )
    return ""


def _generation_unit_window_marker(
    index: int,
    source_in_seconds: float,
    duration_seconds: float,
) -> str:
    return (
        f"[Shot {index}] [{source_in_seconds:.2f}-"
        f"{source_in_seconds + duration_seconds:.2f} seconds]"
    )


def _generation_unit_timeline_description(
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    duration: int,
    references: list[ResolvedH3MediaBinding],
) -> str:
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        document.bible_versions[-1],
    )
    style_fields = [
        f"{label}={value}"
        for label, raw_value in (
            ("medium", bible.style.medium),
            ("cinematography", bible.style.cinematography),
            ("color", bible.style.color),
            ("texture", bible.style.texture),
        )
        if (value := declared_style_value(raw_value)) is not None
    ]
    opening = (
        f"Generate one {duration}-second 9:16 vertical video containing "
        f"{len(shots)} editorial shots in exactly the declared order. "
        f"Generation intent: {unit.generation_intent}. "
        "Opening continuity: "
        + (
            "; ".join(unit.continuity_in)
            if unit.continuity_in
            else (
                f"{shots[0].continuity.action_in}; "
                f"{shots[0].continuity.prop_state_in}"
            )
        )
        + ". "
    )
    if style_fields:
        opening += "Project style: " + "; ".join(style_fields) + ". "

    parts = [opening]
    for index, (window, shot) in enumerate(
        zip(unit.shot_windows, shots, strict=True),
        start=1,
    ):
        marker = _generation_unit_window_marker(
            index,
            window.source_in_seconds,
            window.duration_seconds,
        )
        segment = [
            marker,
            f"Framing: {shot.framing}.",
            f"Observable image: {shot.visual}.",
            f"Narrative purpose: {shot.narrative_purpose}.",
            f"Camera direction: {shot.camera}.",
            (
                f"Visible state moves from {shot.continuity.action_in} to "
                f"{shot.continuity.action_out}."
            ),
            (
                f"Props move from {shot.continuity.prop_state_in} to "
                f"{shot.continuity.prop_state_out}."
            ),
            (
                f"Character staging and eyeline: {shot.continuity.character_positions}; "
                f"{shot.continuity.eyeline}."
            ),
        ]
        action_constraints = _structured_action_constraints(document, shot)
        if action_constraints:
            segment.append(action_constraints)
        if shot.timeline_beats:
            segment.extend(
                _generation_unit_beat_description(
                    document,
                    shot,
                    beat,
                    offset_seconds=window.source_in_seconds,
                    references=references,
                )
                for beat in shot.timeline_beats
                if beat.start_seconds < window.duration_seconds
            )
        else:
            dialogue = _dialogue_description(document, shot)
            segment.append(
                dialogue
                if dialogue
                else "No one speaks; only declared ambient and physical sounds are heard."
            )
        segment.append(f"Editorial transition out: {shot.transition_out}.")
        parts.append(" ".join(segment))
    parts.extend(
        [
            (
                "Closing continuity: "
                + (
                    "; ".join(unit.continuity_out)
                    if unit.continuity_out
                    else (
                        f"{shots[-1].continuity.action_out}; "
                        f"{shots[-1].continuity.prop_state_out}"
                    )
                )
                + "."
            ),
            *(
                ["Required visible changes: " + "; ".join(unit.visible_changes) + "."]
                if unit.visible_changes
                else []
            ),
            (
                "Audio intent across the generated unit: "
                + " ".join(dict.fromkeys(shot.audio_intent for shot in shots))
                + "."
            ),
            *(
                [f"Transition intent: {unit.transition_intent}."]
                if unit.transition_intent is not None
                else []
            ),
            *(
                ["Reference notes: " + "; ".join(unit.reference_notes) + "."]
                if unit.reference_notes
                else []
            ),
            (
                "Preserve every [Shot N] boundary and time window so the single generated "
                "file can be split back into independently editable editorial shots."
            ),
            "Do not introduce undeclared characters, props, actions, scenes, or dialogue.",
            "No subtitles, watermark, or additional readable text appear in the image.",
        ]
    )
    return " ".join(parts)


def _generation_unit_beat_description(
    document: ProjectDocument,
    shot: ShotPlan,
    beat: ShotTimelineBeat,
    *,
    offset_seconds: float,
    references: list[ResolvedH3MediaBinding],
) -> str:
    parts = [
        _beat_time_marker(
            offset_seconds + beat.start_seconds,
            offset_seconds + beat.end_seconds,
        ),
        f"Framing: {beat.framing}.",
        f"Camera and focus: {beat.camera}.",
        f"Observable image: {beat.visual}.",
        f"Action: {beat.action}.",
    ]
    if beat.performance is not None:
        parts.append(f"Performance: {beat.performance}.")
    if beat.emotion is not None:
        parts.append(f"Emotion or atmosphere: {beat.emotion}.")
    dialogue = _dialogue_description(document, shot, beat.dialogue_line_ids)
    if dialogue:
        parts.append(f"Dialogue in this range only: {dialogue}")
    if beat.sound is not None:
        parts.append(f"Sound in this range: {beat.sound}.")
    reference_text = _timeline_reference_cues(document, beat, references)
    if reference_text:
        parts.append(f"Reference cues: {reference_text}.")
    return " ".join(parts)


def _timeline_description(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    duration: int,
    references: list[ResolvedH3MediaBinding] | None = None,
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
    if shot.timeline_beats:
        parts.append(
            "The following ranges are continuous phases inside this single editorial shot; "
            "they do not authorize an undeclared cut or a second shot."
        )
        parts.extend(
            _timeline_beat_description(
                document,
                shot,
                beat_index=index,
                references=references or [],
            )
            for index in range(len(shot.timeline_beats))
        )
        if duration > shot.duration_seconds + 0.05:
            parts.append(
                f"From {shot.duration_seconds:.2f} to {duration:.2f} seconds, preserve the "
                "declared end state as a clean trimming handle without adding action or dialogue."
            )
    else:
        dialogue = _dialogue_description(document, shot)
        if dialogue:
            parts.append(dialogue)
        else:
            parts.append(
                "No one speaks; only the declared ambient and physical sounds are heard."
            )
    parts.extend(
        [
            "Do not introduce undeclared characters, props, actions, scene changes, or dialogue.",
            "No subtitles, watermark, or additional readable text appear in the image.",
        ]
    )
    return " ".join(parts)


def _beat_time_marker(start_seconds: float, end_seconds: float) -> str:
    return f"[{start_seconds:.2f}-{end_seconds:.2f} seconds]"


def _timeline_beat_description(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    beat_index: int,
    references: list[ResolvedH3MediaBinding],
) -> str:
    beat: ShotTimelineBeat = shot.timeline_beats[beat_index]
    parts = [
        _beat_time_marker(beat.start_seconds, beat.end_seconds),
        f"Framing: {beat.framing}.",
        f"Camera and focus: {beat.camera}.",
        f"Observable image: {beat.visual}.",
        f"Action: {beat.action}.",
    ]
    if beat.performance is not None:
        parts.append(f"Performance: {beat.performance}.")
    if beat.emotion is not None:
        parts.append(f"Emotion or atmosphere: {beat.emotion}.")
    dialogue = _dialogue_description(document, shot, beat.dialogue_line_ids)
    if dialogue:
        parts.append(f"Dialogue in this range only: {dialogue}")
    if beat.sound is not None:
        parts.append(f"Sound in this range: {beat.sound}.")
    reference_text = _timeline_reference_cues(document, beat, references)
    if reference_text:
        parts.append(f"Reference cues: {reference_text}")
    return " ".join(parts)


def _timeline_reference_cues(
    document: ProjectDocument,
    beat: ShotTimelineBeat,
    references: list[ResolvedH3MediaBinding],
) -> str:
    asset_names = {asset.id: asset.name for asset in document.assets}
    rendered: list[str] = []
    for cue in beat.references:
        matches = [
            item
            for item in references
            if item.subject_asset_id == cue.subject_asset_id
            and set(cue.required_tags).issubset(item.tags)
        ]
        if matches:
            selected = min(matches, key=lambda item: (len(item.tags), item.alias))
            rendered.append(
                f"@{selected.alias} ({selected.prompt_label}) for {cue.purpose}"
            )
        else:
            subject = asset_names.get(cue.subject_asset_id, cue.alias)
            rendered.append(f"{subject} for {cue.purpose}")
    return "; ".join(rendered)


def _dialogue_description(
    document: ProjectDocument,
    shot: ShotPlan,
    line_ids: list[str] | None = None,
) -> str:
    script = next(
        version
        for version in document.script_versions
        if version.id == document.project.current_script_version_id
    )
    line_by_id = {line.id: line for line in script.lines}
    lines = [
        line_by_id[line_id]
        for line_id in (shot.dialogue_line_ids if line_ids is None else line_ids)
        if line_id in line_by_id
    ]
    speaker_ids: dict[str, str] = {}
    rendered: list[str] = []
    for line in lines:
        # Display names remain presentation text. Only a typed character binding may
        # assert that separate lines share one speaker identity.
        speaker_key = line.speaker_character_id or line.id
        speaker_id = speaker_ids.setdefault(
            speaker_key,
            f"S{len(speaker_ids) + 1}",
        )
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


def _reference_definitions(
    references: list[ResolvedH3MediaBinding],
) -> list[str]:
    rendered: list[str] = []
    rendered_subjects: set[str] = set()
    for reference in references:
        if not _is_reusable_image_subject(reference):
            rendered.append(
                f"@{reference.alias} = {reference.prompt_label}; "
                f"{reference.prompt_label} defines {reference.subject}: "
                f"{reference.descriptor}."
            )
            continue
        if reference.prompt_label in rendered_subjects:
            continue
        group = [
            item
            for item in references
            if _is_reusable_image_subject(item)
            and item.prompt_label == reference.prompt_label
        ]
        rendered_subjects.add(reference.prompt_label)
        alias_bindings = "; ".join(
            f"@{alias} = {reference.prompt_label}"
            for alias in dict.fromkeys(item.alias for item in group)
        )
        source_descriptions = "; ".join(
            _reference_image_source_definition(item)
            for item in group
        )
        rendered.append(
            f"{alias_bindings}; {reference.prompt_label} is the reusable visible content for "
            f"{reference.subject} extracted from its reference images: "
            f"{source_descriptions}."
        )
    return rendered


def _reference_image_source_definition(reference: ResolvedH3MediaBinding) -> str:
    tags = (
        f" (reference tags: {', '.join(reference.tags)})"
        if reference.tags
        else ""
    )
    return (
        f"{reference.provider_label} provides {reference.descriptor} for "
        f"{reference.purpose}{tags}"
    )


def _retention_analysis(
    references: list[ResolvedH3MediaBinding],
) -> list[str]:
    rendered: list[str] = []
    rendered_subjects: set[str] = set()
    for reference in references:
        if not _is_reusable_image_subject(reference):
            rendered.append(_retention_sentence(reference))
            continue
        if reference.prompt_label in rendered_subjects:
            continue
        group = [
            item
            for item in references
            if _is_reusable_image_subject(item)
            and item.prompt_label == reference.prompt_label
        ]
        rendered_subjects.add(reference.prompt_label)
        aliases = ", ".join(
            f"@{alias}" for alias in dict.fromkeys(item.alias for item in group)
        )
        sources = ", ".join(item.provider_label for item in group)
        rendered.append(
            f"{reference.prompt_label} ({aliases}; extracted from {sources}): "
            "fully_preserved - the declared subject identity and stable visible appearance "
            "are retained while pose and framing change only as required by this shot; "
            "unrelated source-image content is not copied."
        )
    return rendered


def _is_reusable_image_subject(reference: ResolvedH3MediaBinding) -> bool:
    return (
        reference.media.kind == H3MediaKind.IMAGE
        and reference.media.role == H3MediaRole.REFERENCE_IMAGE
    )


def _retention_sentence(reference: ResolvedH3MediaBinding) -> str:
    if reference.video_guide is not None:
        if reference.video_guide.kind == H3VideoGuideKind.CAMERA_PREVIS:
            return (
                f"@{reference.alias} ({reference.provider_label}) transfers only the coarse "
                "camera path and pacing; its "
                "low-fidelity compression and interpolation artifacts are not retained. The "
                "project's declared style and intended amount of motion remain authoritative."
            )
        return (
            f"@{reference.alias} ({reference.provider_label}) transfers only the terminal "
            "motion state, screen direction, "
            "and compatible camera momentum; its dialogue, prior action, cuts, and accidental "
            "defects are not repeated."
        )
    if reference.media.kind == H3MediaKind.AUDIO:
        return (
            f"@{reference.alias} ({reference.provider_label}) preserves the referenced speaker's "
            "voice identity, pitch, "
            "timbre, and delivery character while all spoken words remain the locked dialogue."
        )
    if reference.media.kind == H3MediaKind.VIDEO:
        return (
            f"@{reference.alias} ({reference.provider_label}) transfers only the declared motion "
            "and camera relationship; "
            "unrequested subjects, dialogue, and edits are not copied."
        )
    return (
        f"@{reference.alias} ({reference.prompt_label}) fully preserves the declared subject "
        "identity and stable visible "
        "appearance while pose and framing change only as required by this shot."
    )


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
