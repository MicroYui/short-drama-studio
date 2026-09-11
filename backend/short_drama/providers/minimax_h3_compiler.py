"""Capability validation and request compilation for MiniMax-H3."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from short_drama.h3_models import (
    H3ContentItem,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3UrlValue,
    H3VideoGuide,
    H3VideoRequest,
)
from short_drama.models import ProjectDocument, ShotPlan, VideoGenerationRoute
from short_drama.providers.h3_output_format import h3_prompt_document, resolve_h3_output_format
from short_drama.providers.h3_prompt_skill import (
    compile_h3_generation_unit_prompt_ir,
    compile_h3_prompt_ir,
)

if TYPE_CHECKING:
    from short_drama.production_contracts import GenerationUnit


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


def build_h3_prompt(
    document: ProjectDocument,
    shot: ShotPlan,
    *,
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT,
    duration_seconds: int | None = None,
    media_inputs: list[H3MediaInput] | None = None,
    video_guides: list[H3VideoGuide] | None = None,
    aspect_ratio: str | None = None,
) -> str:
    try:
        return compile_h3_prompt_ir(
            h3_prompt_document(document, route=route, aspect_ratio=aspect_ratio),
            shot,
            route=route,
            duration_seconds=duration_seconds,
            media_inputs=media_inputs,
            video_guides=video_guides,
        )
    except ValueError as exc:
        raise H3CapabilityError(str(exc)) from exc


def build_h3_generation_unit_prompt(
    document: ProjectDocument,
    unit: GenerationUnit,
    shots: list[ShotPlan],
    *,
    route: VideoGenerationRoute = VideoGenerationRoute.DIRECT,
    duration_seconds: int | None = None,
    media_inputs: list[H3MediaInput] | None = None,
    video_guides: list[H3VideoGuide] | None = None,
    aspect_ratio: str | None = None,
) -> str:
    try:
        return compile_h3_generation_unit_prompt_ir(
            h3_prompt_document(document, route=route, aspect_ratio=aspect_ratio),
            unit,
            shots,
            route=route,
            duration_seconds=duration_seconds,
            media_inputs=media_inputs,
            video_guides=video_guides,
        )
    except ValueError as exc:
        raise H3CapabilityError(str(exc)) from exc


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
    validate_h3_inputs(
        route=route,
        duration_seconds=duration_seconds,
        media_inputs=media_inputs,
    )


def validate_h3_inputs(
    *,
    route: VideoGenerationRoute,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
) -> None:
    """Validate route/media before prompt authoring or any provider call."""

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
    if (
        route != VideoGenerationRoute.FRAME_LAST
        and roles[H3MediaRole.LAST_FRAME]
        and not roles[H3MediaRole.FIRST_FRAME]
    ):
        raise H3CapabilityError("H3 last_frame must be paired with first_frame")
    if roles[H3MediaRole.REFERENCE_IMAGE] > 9:
        raise H3CapabilityError("H3 accepts at most nine reference images")
    if roles[H3MediaRole.REFERENCE_VIDEO] > 3:
        raise H3CapabilityError("H3 accepts at most three reference videos")
    if roles[H3MediaRole.REFERENCE_AUDIO] > 3:
        raise H3CapabilityError("H3 accepts at most three reference audio clips")
    if roles[H3MediaRole.REFERENCE_AUDIO] and not (roles[H3MediaRole.REFERENCE_IMAGE] or roles[H3MediaRole.REFERENCE_VIDEO]):
        raise H3CapabilityError(
            "H3 reference audio requires at least one reference image or reference video; "
            "select the intended visual reference, or explicitly choose native audio without reference media"
        )

    if route == VideoGenerationRoute.DIRECT and media_inputs:
        raise H3CapabilityError("DIRECT does not accept media inputs")
    if route == VideoGenerationRoute.FRAME_FIRST:
        if roles != Counter({H3MediaRole.FIRST_FRAME: 1}):
            raise H3CapabilityError("FRAME_FIRST requires exactly one first_frame image")
    if route == VideoGenerationRoute.FRAME_LAST:
        if roles != Counter({H3MediaRole.LAST_FRAME: 1}):
            raise H3CapabilityError("FRAME_LAST requires exactly one last_frame image")
    if route == VideoGenerationRoute.FRAME_FIRST_LAST:
        expected = Counter({H3MediaRole.FIRST_FRAME: 1, H3MediaRole.LAST_FRAME: 1})
        if roles != expected:
            raise H3CapabilityError(
                "FRAME_FIRST_LAST requires exactly one first_frame and one last_frame image"
            )
    if route == VideoGenerationRoute.REFERENCE:
        if not media_inputs or not has_references or has_frames:
            raise H3CapabilityError("REFERENCE requires at least one reference media item")


def validate_h3_audio_durations(document: ProjectDocument, media_inputs: list[H3MediaInput]) -> None:
    """Check saved duration metadata without probing files or inventing unknown values."""
    versions = {version.id: (asset, version) for asset in document.assets for version in asset.versions}
    subjects = {asset.id: asset.name for asset in document.assets}
    known_seconds: list[float] = []
    for media in media_inputs:
        if media.kind != H3MediaKind.AUDIO:
            continue
        found = versions.get(media.asset_version_id or "")
        if found is None or found[1].duration_seconds is None:
            continue
        asset, version = found
        seconds = version.duration_seconds
        assert seconds is not None
        if not 2.0 <= seconds <= 15.0:
            subject = subjects.get(media.subject_asset_id or "") or asset.name
            raise H3CapabilityError(f"H3 reference audio {version.id} for {subject} ({asset.id}) is {seconds:g} seconds; "
                "each reference audio must be 2–15 seconds. Select a usable voice reference or explicitly revise the inputs.")
        known_seconds.append(seconds)
    if sum(known_seconds) > 15.0:
        raise H3CapabilityError(f"Selected reference audio totals at least {sum(known_seconds):g} seconds from saved metadata; "
            "H3 accepts at most 15 seconds combined. Select shorter current voice references or explicitly revise the input media; no voice was omitted.")


def compile_h3_request(
    *,
    route: VideoGenerationRoute,
    prompt: str,
    duration_seconds: int,
    media_inputs: list[H3MediaInput],
    resolve_url: Callable[[H3MediaInput], str],
    resolution: str = "768P",
    aspect_ratio: str | None = None,
) -> H3VideoRequest:
    try:
        resolution, ratio = resolve_h3_output_format(route=route, resolution=resolution, aspect_ratio=aspect_ratio)
    except ValueError as exc:
        raise H3CapabilityError(str(exc)) from exc
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
    return H3VideoRequest(content=content, duration=duration_seconds, ratio=ratio, resolution=resolution)


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
