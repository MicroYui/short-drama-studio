"""Scoped title/transition edits using the existing Studio renderer's basic contract."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from short_drama.studio_editorial import EditorClip, EditorTimeline, EditorTrack, editorial_clip_enabled
from short_drama.studio_models import EditorialClipChange, EditorialTitleCreate, EditorialTitleStyle


_STYLE_FIELDS = {"font_size": "fontSize", "font_weight": "fontWeight", "color": "color",
                 "position_x": "positionX", "position_y": "positionY", "opacity": "opacity"}
# Matches frontend/lib/studio-title-style.ts. The shared export runtime remains
# the authority for rendering and rejects advanced styles it cannot reproduce.
_BASIC_TITLE: dict[str, JsonValue] = {
    "text": "新的标题", "fontFamily": "Arial", "fontSize": 64, "fontWeight": "bold", "fontStyle": "normal",
    "color": "#FFFFFF", "backgroundColor": "transparent", "textAlign": "center", "positionX": 50, "positionY": 50,
    "strokeColor": "transparent", "strokeWidth": 0, "shadowColor": "transparent", "shadowBlur": 0,
    "shadowOffsetX": 0, "shadowOffsetY": 0, "letterSpacing": 0, "lineHeight": 1.2, "maxWidth": 0,
    "padding": 0, "borderRadius": 0, "opacity": 100,
}
_NONE: dict[str, JsonValue] = {"type": "none", "duration": 0}


def _style_patch(style: EditorialTitleStyle) -> dict[str, JsonValue]:
    return {_STYLE_FIELDS[name]: value for name, value in
            cast(dict[str, JsonValue], style.model_dump(mode="json", exclude_none=True)).items()}


def clip_visual_fields(clip: EditorClip) -> dict[str, JsonValue]:
    extra = clip.model_extra or {}
    result: dict[str, JsonValue] = {}
    style = extra.get("textStyle")
    if clip.type == "text" and isinstance(style, dict):
        current = cast(dict[str, JsonValue], style)
        result["text"] = current.get("text")
        result["title_style"] = {name: current[key] for name, key in _STYLE_FIELDS.items() if key in current}
    if clip.type in {"video", "image"}:
        for field, key in (("transition_in", "transitionIn"), ("transition_out", "transitionOut")):
            value = _transition(clip, key)
            result[field] = {"type": value.get("type", "none"), "duration_seconds": value.get("duration", 0)}
    return result


def change_title(clip: EditorClip, change: EditorialClipChange) -> None:
    if change.text is None and change.title_style is None:
        return
    if clip.type != "text":
        raise ValueError(f"/clips/{clip.id}: text/title_style can only edit a title clip; use add_titles to create one.")
    style = (clip.model_extra or {}).get("textStyle")
    if not isinstance(style, dict):
        raise ValueError(f"/clips/{clip.id}: the title has no saved style; re-read its current content.")
    merged = dict(cast(dict[str, JsonValue], style))
    if change.text is not None:
        merged["text"] = change.text
    if change.title_style is not None:
        merged.update(_style_patch(change.title_style))
    setattr(clip, "textStyle", merged)


def add_titles(timeline: EditorTimeline, titles: list[EditorialTitleCreate], *, shot_id: str | None) -> None:
    local_end = max((clip.startTime + clip.duration for clip in timeline.clips if clip.type in {"video", "image"}), default=0.0)
    has_solo = any(track.solo for track in timeline.tracks)
    for title in titles:
        if any(clip.id == title.id for clip in timeline.clips):
            raise ValueError(f"/add_titles: {title.id!r} already exists in timeline {timeline.id!r}; use clips to edit it.")
        if shot_id and title.start_seconds + title.duration_seconds > local_end + 1e-7:
            raise ValueError(f"/add_titles/{title.id}: title exceeds SHOT_LOCAL duration {local_end:g}s; use FILM scope for a crossing title.")

        def available(index: int) -> bool:
            track = timeline.tracks[index]
            return (track.kind == "video" and track.type != "subtitle" and track.enabled and not track.locked
                    and (not has_solo or track.solo) and not any(clip.trackIndex == index
                        and clip.startTime < title.start_seconds + title.duration_seconds
                        and clip.startTime + clip.duration > title.start_seconds for clip in timeline.clips))

        index = title.track_index
        if index is not None and (index >= len(timeline.tracks) or not available(index)):
            raise ValueError(f"/add_titles/{title.id}: track_index={index} is not an available visible visual track; omit it to add a title track.")
        if index is None:
            index = next((i for i in range(len(timeline.tracks)) if available(i)), None)
        if index is None:
            track_id, suffix = "titles", 1
            while any(track.id == track_id for track in timeline.tracks):
                track_id, suffix = f"titles-{suffix}", suffix + 1
            index = len(timeline.tracks)
            timeline.tracks.append(EditorTrack(id=track_id, name="标题", solo=has_solo))
        timeline.clips.append(EditorClip.model_validate({"id": title.id, "type": "text", "assetId": None, "asset": None,
            "startTime": title.start_seconds, "duration": title.duration_seconds, "trimStart": 0, "trackIndex": index,
            "textStyle": {**_BASIC_TITLE, **_style_patch(title.style), "text": title.text},
            **({"sourceShotId": shot_id} if shot_id else {})}))


def _transition(clip: EditorClip, edge: str) -> dict[str, JsonValue]:
    value = (clip.model_extra or {}).get(edge)
    return cast(dict[str, JsonValue], value) if isinstance(value, dict) else _NONE


def _peer(timeline: EditorTimeline, clip: EditorClip, edge: str) -> EditorClip | None:
    peers = [item for item in timeline.clips if item.id != clip.id and item.trackIndex == clip.trackIndex
             and item.type in {"video", "image"} and abs((item.startTime + item.duration - clip.startTime)
                 if edge == "transitionIn" else (item.startTime - clip.startTime - clip.duration)) < .001]
    if len(peers) > 1:
        raise ValueError(f"/clips/{clip.id}: the transition has multiple adjacent clips; resolve the overlap first.")
    return peers[0] if peers else None


def change_transitions(timeline: EditorTimeline, before: EditorTimeline,
                       changes: list[EditorialClipChange], removed: set[str]) -> None:
    clips, originals = {clip.id: clip for clip in timeline.clips}, {clip.id: clip for clip in before.clips}
    explicit: dict[tuple[str, str], dict[str, JsonValue]] = {}
    for change in changes:
        for field, edge in ((change.transition_in, "transitionIn"), (change.transition_out, "transitionOut")):
            if field is None or change.remove:
                continue
            clip = clips[change.id]
            if clip.type not in {"video", "image"}:
                raise ValueError(f"/clips/{clip.id}: basic transitions require a video/image clip, not {clip.type}.")
            explicit[(clip.id, edge)] = {"type": field.type, "duration": field.duration_seconds}
    updates = dict(explicit)
    for identity in {*removed, *(identity for identity, _edge in explicit)}:
        if identity not in originals:
            continue
        old = originals[identity]
        for edge, other in (("transitionIn", "transitionOut"), ("transitionOut", "transitionIn")):
            if identity not in removed and (identity, edge) not in explicit:
                continue
            value = _transition(old, edge)
            if value.get("type") != "dissolve":
                continue
            peer = _peer(before, old, edge)
            if peer and peer.id in clips and _transition(peer, other) == value and (peer.id, other) not in explicit:
                updates[(peer.id, other)] = dict(_NONE)
    for (identity, edge), value in explicit.items():
        if value["type"] != "dissolve":
            continue
        peer = _peer(timeline, clips[identity], edge)
        if peer is None:
            raise ValueError(f"/clips/{identity}: dissolve requires an adjacent same-track video/image; use FILM scope for a cross-shot cut.")
        key = (peer.id, "transitionOut" if edge == "transitionIn" else "transitionIn")
        if key in explicit and explicit[key] != value:
            raise ValueError(f"/clips/{identity}: both ends of a dissolve must have the same type and duration_seconds.")
        updates[key] = dict(value)
    for (identity, edge), value in updates.items():
        setattr(clips[identity], edge, value)
    # Match the shared exporter's temporal restrictions. Check only edited
    # objects/pairs so an unrelated saved legacy effect is never overwritten.
    affected = {identity for identity, _edge in updates} | {change.id for change in changes if not change.remove
        and any(value is not None for value in (change.start_seconds, change.duration_seconds, change.speed))}
    for identity in affected:
        clip = clips[identity]
        if clip.type not in {"video", "image"}:
            continue
        intro, outro = _transition(clip, "transitionIn"), _transition(clip, "transitionOut")
        for edge, value in (("transitionIn", intro), ("transitionOut", outro)):
            if value.get("type", "none") == "none":
                continue
            duration = float(cast(float, value.get("duration", 0)))
            if duration <= 0 or duration > clip.duration:
                raise ValueError(f"/clips/{identity}/{edge}: transition duration must fit the clip's {clip.duration:g}s duration.")
            if value["type"] == "dissolve":
                peer = _peer(timeline, clip, edge)
                other = "transitionOut" if edge == "transitionIn" else "transitionIn"
                if peer is None or _transition(peer, other) != value:
                    raise ValueError(f"/clips/{identity}/{edge}: dissolve has no matching adjacent partner; update or clear it in the same edit.")
                if editorial_clip_enabled(timeline, clip) and not editorial_clip_enabled(timeline, peer):
                    raise ValueError(f"/clips/{identity}/{edge}: dissolve partner is on a disabled track.")
                previous, following = (peer, clip) if edge == "transitionIn" else (clip, peer)
                if any(item.id not in {previous.id, following.id} and item.type in {"video", "image"}
                       and editorial_clip_enabled(timeline, item) and item.startTime < following.startTime
                       and item.startTime + item.duration > following.startTime - duration for item in timeline.clips):
                    raise ValueError(f"/clips/{identity}/{edge}: a third picture overlaps the basic dissolve; move it or clear the transition.")
        fade_in = float(cast(float, intro.get("duration", 0))) if intro.get("type") == "fade-to-black" else 0
        out = float(cast(float, outro.get("duration", 0))) if outro.get("type", "none") != "none" else 0
        if fade_in + out > clip.duration + 1e-7:
            raise ValueError(f"/clips/{identity}: incoming black fade and outgoing transition overlap; shorten their durations.")
