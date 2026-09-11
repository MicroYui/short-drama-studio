"""Extract a full-resolution source frame and hand it to existing image/H3 tools."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
import tempfile
from threading import RLock
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image
from pydantic import Field, JsonValue, model_validator

from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource
from short_drama.image_models import ImageReferenceInput
from short_drama.models import (
    ApprovalStatus, Asset, AssetKind, AssetMediaType, AssetSourceRecord, AssetVersion,
    DomainModel, Freshness, ImageReferenceRole, OriginType, ProjectDocument, Take,
)
from short_drama.studio_editorial import EditorProject, read_editorial, require_unambiguous_editorial_ids
from short_drama.studio_trace import record_trace
from short_drama.video_inspection import VideoInspector

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class TakeFrameAssetRequest(DomainModel):
    take_id: str = Field(min_length=1)
    time_seconds: float = Field(ge=0, allow_inf_nan=False, description=(
        "Source-video seconds by default; with shot_id, seconds from that Take's shot window; "
        "with clip_id, seconds from that exact current film-use clip, including speed/reverse. "
        "The window end selects its last available frame."
    ))
    shot_id: str | None = None
    clip_id: str | None = None
    timeline_id: str | None = None
    asset_id: str | None = Field(default=None, description="Optional existing REFERENCE asset to receive the frame; omit to create one.")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500, description="Why this actual frame is useful; extraction itself does not inspect its identity or quality.")

    @model_validator(mode="after")
    def validate_coordinates(self) -> TakeFrameAssetRequest:
        if self.shot_id is not None and self.clip_id is not None:
            raise ValueError("Use shot_id or clip_id for local time, not both")
        if self.timeline_id is not None and self.clip_id is None:
            raise ValueError("timeline_id requires an exact clip_id")
        return self


class TakeFrameAssetResponse(DomainModel):
    project_id: str
    asset_id: str
    asset_version_id: str
    local_path: str
    width: int
    height: int
    source_take_id: str
    coordinate_space: Literal["SOURCE", "SHOT_LOCAL", "CLIP_LOCAL"]
    requested_time_seconds: float
    source_time_seconds: float
    source_window_start_seconds: float
    source_window_end_seconds: float
    clip_id: str | None = None
    timeline_id: str | None = None
    reused: bool = False
    review_status: Literal["NOT_REVIEWED"] = "NOT_REVIEWED"
    image_reference_input: ImageReferenceInput
    h3_media_input: H3MediaInput
    notes: str = (
        "Full-resolution underlying video frame, without editorial overlays, crop, flips or grading. "
        "Use image.enqueue EDIT/REFERENCE_GENERATE with image_reference_input to derive a target "
        "composition, or use h3_media_input as a soft REFERENCE composition anchor. "
        "For hard FRAME_FIRST, explicitly choose that route and only its first_frame input. "
        "A copied frame also retains source defects; it has not passed visual review."
    )


def _source_window(service: ShortDramaService, project_id: str, take: Take,
                   request: TakeFrameAssetRequest) -> tuple[float, float, float, str | None, dict[str, JsonValue]]:
    start, end = 0.0, take.duration_seconds
    scope: dict[str, JsonValue] = {}
    timeline_id: str | None = None
    if request.shot_id is not None:
        if request.shot_id not in take.covered_shot_ids():
            raise ValueError("shot_id is not covered by this Take")
        window = take.source_window_for_shot(request.shot_id)
        if window is not None:
            start, end = window.source_in_seconds, window.source_out_seconds
        scope = {"shot_id": request.shot_id, "start": start, "end": end}
    elif request.clip_id is not None:
        editorial = read_editorial(service, project_id)
        editor = EditorProject.model_validate(editorial.project)
        if request.timeline_id is None:
            require_unambiguous_editorial_ids(editor, clip_ids=[request.clip_id], subtitle_ids=[])
        timeline_id = request.timeline_id or editor.activeTimelineId
        timeline = next((item for item in editor.timelines if item.id == timeline_id), None)
        clip = next((item for item in timeline.clips if item.id == request.clip_id), None) if timeline else None
        if clip is None or clip.type != "video":
            raise ValueError("clip_id must name an existing video use in the specified timeline")
        identity = (clip.model_extra or {}).get("sourceTakeId") or clip.assetId
        asset = next((item for item in editor.assets if item.id == clip.assetId), clip.asset)
        if identity != take.id or asset is None or Path(asset.path).expanduser().resolve() != Path(take.media_path).expanduser().resolve():
            raise ValueError("The selected film use does not refer to this Take's current media")
        if request.time_seconds > clip.duration + 1e-8:
            raise ValueError("time_seconds is outside the selected clip's local window")
        start, end = clip.trimStart, clip.trimStart + clip.duration * clip.speed
        scope = {"timeline_id": timeline_id, "clip": clip.model_dump(mode="json")}
        source_time = end - request.time_seconds * clip.speed if clip.reversed else start + request.time_seconds * clip.speed
        return start, end, source_time, timeline_id, scope
    if request.time_seconds > end - start + 1e-8:
        raise ValueError("time_seconds is outside the selected Take/shot window")
    return start, end, start + request.time_seconds, timeline_id, scope


def _extract_png(path: Path, destination: Path, *, timestamp: float, end: float) -> float:
    # Decode the exact source timeline and preserve its pixels. showinfo supplies
    # the selected frame's actual PTS; requested time is not mislabeled as PTS.
    try:
        result = subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-y",
            "-i", str(path), "-an", "-vf", f"select=gte(t\\,{timestamp:.9f}),showinfo",
            "-frames:v", "1", "-fps_mode", "vfr", str(destination),
        ], capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Source frame decoding exceeded 60 seconds") from exc
    match = re.search(r"\bn:\s*0\s+pts:.*?\bpts_time:([\d.eE+-]+)", result.stderr)
    if result.returncode or not destination.is_file() or match is None:
        raise ValueError("Could not extract a decoded frame from the requested source interval")
    actual = float(match.group(1))
    if actual >= end - 1e-7:
        raise ValueError("The decoded frame falls outside the selected source interval")
    return actual


def _cached_frame(document: ProjectDocument, request: TakeFrameAssetRequest, recipe: str) -> tuple[Asset, AssetVersion, float] | None:
    for candidate in document.assets:
        if candidate.kind != AssetKind.REFERENCE or (request.asset_id is not None and candidate.id != request.asset_id):
            continue
        for version in candidate.versions:
            if (version.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
                or version.freshness != Freshness.CURRENT or not version.width or not version.height):
                continue
            if version.source and version.source.prompt == recipe and version.local_path and Path(version.local_path).is_file():
                uri = version.source.source_uri or ""
                if "#t=" in uri:
                    return candidate, version, float(uri.rsplit("#t=", 1)[1])
    return None


def extract_take_frame(service: ShortDramaService, project_id: str,
                       request: TakeFrameAssetRequest, *, mutation_lock: RLock) -> TakeFrameAssetResponse:
    take = service.repository.get_take(request.take_id)
    if take is None or take.project_id != project_id:
        raise ValueError("take_id is not in this project")
    path = Path(take.media_path).expanduser().resolve()
    probe = VideoInspector(service.workspace_root).probe_video(path)
    start, end, target, timeline_id, scope = _source_window(service, project_id, take, request)
    if end > probe.duration_seconds + 0.05 or start >= end:
        raise ValueError("The selected source window exceeds the actual readable video")
    end = min(end, probe.duration_seconds)
    # Endpoints are exclusive in media. Choose the last frame within the window.
    target = max(start, min(target, end - 1.0 / probe.fps))
    file_state = {"path": str(path), "size": path.stat().st_size, "modified_ns": path.stat().st_mtime_ns}
    recipe = json.dumps({"operation": "video.extract_frame", "take_id": take.id,
        "file": file_state, "target": target, "source_window": [start, end], "scope": scope,
        "description": request.description}, ensure_ascii=False, sort_keys=True)
    document = service.repository.get_project(project_id)
    if document is None:
        raise ValueError("Project does not exist")
    asset = next((item for item in document.assets if item.id == request.asset_id), None)
    if request.asset_id is not None and (asset is None or asset.kind != AssetKind.REFERENCE):
        raise ValueError("asset_id must refer to an existing REFERENCE asset")
    cached = _cached_frame(document, request, recipe)
    if cached is not None:
        return _response(project_id, cached[0], cached[1], request, start, end, cached[2], timeline_id, True)
    with tempfile.TemporaryDirectory(prefix="studio-take-frame-") as temporary:
        temporary_frame = Path(temporary) / "frame.png"
        actual = _extract_png(path, temporary_frame, timestamp=target, end=end)
        with Image.open(temporary_frame) as pixels:
            width, height = pixels.size
        with mutation_lock:
            latest_take = service.repository.get_take(take.id)
            if latest_take is None or latest_take.media_path != take.media_path or path.stat().st_size != file_state["size"] or path.stat().st_mtime_ns != file_state["modified_ns"]:
                raise ValueError("Source video changed during extraction; read its current frame again")
            if _source_window(service, project_id, latest_take, request)[4] != scope:
                raise ValueError("The selected shot/film use changed during extraction")
            document = service.repository.get_project(project_id)
            if document is None:
                raise ValueError("Project does not exist")
            cached = _cached_frame(document, request, recipe)
            if cached is not None:
                return _response(project_id, cached[0], cached[1], request, start, end, cached[2], timeline_id, True)
            asset = next((item for item in document.assets if item.id == request.asset_id), None)
            if request.asset_id is not None and (asset is None or asset.kind != AssetKind.REFERENCE):
                raise ValueError("Target reference asset is no longer available")
            asset_id = asset.id if asset else f"asset-{uuid4().hex[:12]}"
            version_id = f"asset-version-{uuid4().hex[:12]}"
            destination = service.workspace_root / project_id / "assets" / asset_id / f"{version_id}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(temporary_frame.read_bytes())
            now = datetime.now(UTC).isoformat()
            version = AssetVersion(id=version_id, version=service.asset_workbench.next_asset_version_number(asset) if asset else 1,
                origin_type=OriginType.LOCAL_DERIVED, descriptor=request.description,
                media_type=AssetMediaType.IMAGE, local_path=str(destination), mime_type="image/png",
                width=width, height=height, file_size_bytes=destination.stat().st_size,
                parent_version_id=asset.current_version_id if asset else None,
                source=AssetSourceRecord(source_type=OriginType.LOCAL_DERIVED, source_label="从视频实际帧提取",
                    source_uri=f"{path}#t={actual:.9f}", provider=take.provider, provider_asset_id=take.id,
                    original_filename=path.name, prompt=recipe, created_at=now),
                approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=now)
            if asset is None:
                asset = Asset(id=asset_id, project_id=project_id, kind=AssetKind.REFERENCE,
                    name=request.name or f"视频帧 {request.shot_id or take.shot_id} {actual:.3f}s",
                    current_version_id=version_id, versions=[version])
                document.assets.append(asset)
            else:
                asset.versions.append(version)
                asset.current_version_id = version_id
            service.repository.save_project(document)
    response = _response(project_id, asset, version, request, start, end, actual, timeline_id, False)
    record_trace("video.frame_asset", response.model_dump(mode="json"))
    return response


def _response(project_id: str, asset: Asset, version: AssetVersion, request: TakeFrameAssetRequest,
              start: float, end: float, actual: float, timeline_id: str | None, reused: bool) -> TakeFrameAssetResponse:
    assert version.local_path and version.width and version.height
    return TakeFrameAssetResponse(project_id=project_id, asset_id=asset.id, asset_version_id=version.id,
        local_path=version.local_path, width=version.width, height=version.height,
        source_take_id=request.take_id,
        coordinate_space="CLIP_LOCAL" if request.clip_id else "SHOT_LOCAL" if request.shot_id else "SOURCE",
        requested_time_seconds=request.time_seconds, source_time_seconds=actual,
        source_window_start_seconds=start, source_window_end_seconds=end,
        clip_id=request.clip_id, timeline_id=timeline_id, reused=reused,
        image_reference_input=ImageReferenceInput(asset_version_id=version.id,
            reference_role=ImageReferenceRole.COMPOSITION, purpose=request.description),
        h3_media_input=H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path=version.local_path), asset_version_id=version.id,
            subject_asset_id=asset.id, image_reference_role=ImageReferenceRole.COMPOSITION,
            binding_purpose=request.description))
