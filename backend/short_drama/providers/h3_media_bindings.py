"""Single resolver for human-readable aliases and provider media labels."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from short_drama.h3_models import (
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3VideoGuide,
    H3VideoGuideKind,
    derive_h3_reference_alias,
)
from short_drama.models import ProjectDocument


@dataclass(frozen=True)
class ResolvedH3MediaBinding:
    provider_label: str
    alias: str
    media: H3MediaInput
    subject: str
    subject_asset_id: str | None
    descriptor: str
    purpose: str
    tags: tuple[str, ...]
    video_guide: H3VideoGuide | None = None


def resolve_h3_media_bindings(
    document: ProjectDocument,
    media_inputs: list[H3MediaInput],
    video_guides: list[H3VideoGuide],
) -> list[ResolvedH3MediaBinding]:
    """Resolve one ordered media list into stable aliases plus H3's positional labels."""

    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    asset_index = {asset.id: asset for asset in document.assets}
    guide_index = {guide.id: guide for guide in video_guides}
    counters = {H3MediaKind.IMAGE: 0, H3MediaKind.VIDEO: 0, H3MediaKind.AUDIO: 0}
    aliases: dict[str, tuple[str, ...]] = {}
    result: list[ResolvedH3MediaBinding] = []

    for media in media_inputs:
        counters[media.kind] += 1
        prefix = {
            H3MediaKind.IMAGE: "Picture",
            H3MediaKind.VIDEO: "Video",
            H3MediaKind.AUDIO: "Audio",
        }[media.kind]
        provider_label = f"<{prefix} {counters[media.kind]}>"
        guide = guide_index.get(media.video_guide_id or "")
        resolved = version_index.get(media.asset_version_id or "")
        subject_asset = asset_index.get(media.subject_asset_id or "")

        if guide is not None:
            subject = "the Route Agent's motion and continuity guide"
            descriptor = guide.rationale
            purpose = media.binding_purpose or (
                "camera path and pacing only"
                if guide.kind == H3VideoGuideKind.CAMERA_PREVIS
                else "the immediately preceding motion state and screen direction only"
            )
            default_alias = (
                "camera_previs"
                if guide.kind == H3VideoGuideKind.CAMERA_PREVIS
                else "motion_continuity"
            )
        else:
            source_asset = resolved[0] if resolved is not None else None
            version = resolved[1] if resolved is not None else None
            semantic_asset = subject_asset or source_asset
            subject = (
                semantic_asset.name
                if semantic_asset is not None
                else {
                    H3MediaKind.IMAGE: "the declared visual reference",
                    H3MediaKind.VIDEO: "the declared motion reference",
                    H3MediaKind.AUDIO: "the declared voice or audio reference",
                }[media.kind]
            )
            descriptor = (
                version.descriptor
                if version is not None
                else f"A {media.role.value} input bound by the Route Agent"
            )
            purpose = media.binding_purpose or {
                H3MediaKind.IMAGE: "identity, wardrobe, object appearance, or location geometry",
                H3MediaKind.VIDEO: "declared motion, timing, camera behavior, or continuity",
                H3MediaKind.AUDIO: "voice identity and acoustic character, not copied words",
            }[media.kind]
            default_alias = _default_alias(media, subject)

        alias = media.reference_alias or _alias_token(default_alias)
        identity = _media_identity(media)
        prior = aliases.get(alias)
        if prior == identity:
            raise ValueError(
                f"H3 media input duplicates semantic binding @{alias}; reuse one input"
            )
        if prior is not None:
            if media.reference_alias is not None:
                raise ValueError(f"H3 reference alias @{alias} identifies multiple media inputs")
            stable_suffix = hashlib.sha256(
                "\0".join(identity).encode("utf-8")
            ).hexdigest()[:12]
            alias = _alias_with_stable_suffix(alias, stable_suffix)
            final_prior = aliases.get(alias)
            if final_prior == identity:
                raise ValueError(
                    f"H3 media input duplicates semantic binding @{alias}; reuse one input"
                )
            if final_prior is not None:
                raise ValueError(
                    "Could not derive a unique H3 reference alias for the media input"
                )
        aliases[alias] = identity
        result.append(
            ResolvedH3MediaBinding(
                provider_label=provider_label,
                alias=alias,
                media=media,
                subject=subject,
                subject_asset_id=(
                    media.subject_asset_id
                    or (resolved[0].id if resolved is not None else None)
                ),
                descriptor=descriptor,
                purpose=purpose,
                tags=tuple(media.reference_tags),
                video_guide=guide,
            )
        )
    return result


def _default_alias(media: H3MediaInput, subject: str) -> str:
    if media.role == H3MediaRole.FIRST_FRAME:
        return "shot_start"
    if media.role == H3MediaRole.LAST_FRAME:
        return "shot_end"
    tokens = [_alias_token(subject)]
    tokens.extend(_alias_token(tag) for tag in media.reference_tags if tag)
    if len(tokens) == 1 and media.kind == H3MediaKind.AUDIO:
        tokens.append("voice")
    return "_".join(token for token in tokens if token) or "reference"


def _alias_token(value: str) -> str:
    # H3MediaInput already normalizes explicit aliases. This small fallback handles
    # persisted legacy media that has no alias metadata.
    return derive_h3_reference_alias(value)


def _alias_with_stable_suffix(alias: str, stable_id: str) -> str:
    suffix = _alias_token(stable_id)[-16:]
    return _alias_token(f"{alias[: max(1, 79 - len(suffix))]}_{suffix}")


def _media_identity(media: H3MediaInput) -> tuple[str, ...]:
    return (
        media.kind.value,
        media.role.value,
        media.asset_version_id or "",
        media.video_guide_id or "",
        media.subject_asset_id or "",
        media.source.external_url or "",
        media.source.local_path or "",
        media.source.file_id or "",
    )


__all__ = ["ResolvedH3MediaBinding", "resolve_h3_media_bindings"]
