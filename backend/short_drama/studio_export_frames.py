"""Exact rendered-export pixels for the existing Worker visual-input path."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import JsonValue

from short_drama.image_observation import VisualAttachment
from short_drama.models import ApprovalStatus, Freshness
from short_drama.studio_models import StudioExport
from short_drama.tools.contracts import ArtifactRef
from short_drama.video_detail import extract_detail_frames

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def export_frame_attachments(
    service: ShortDramaService, content: dict[str, JsonValue], *, times: list[float],
    variant: Literal["subtitled", "clean"],
) -> tuple[VisualAttachment, ...]:
    """Add a bounded frame selection to an already inspected exact export.

    Only derived images are written. The export, timeline and source Takes are
    unchanged; observations are preserved by the ordinary Tool/Trace record.
    """
    if not times:
        return ()
    record = StudioExport.model_validate(content["export"])
    if record.status != "completed":
        raise ValueError("只能观察已完成的实际导出。")
    source_path = record.video_path if variant == "subtitled" else record.clean_video_path
    if not source_path or not Path(source_path).is_file():
        raise FileNotFoundError(f"导出记录中的 {variant} 视频未找到。")
    files = content.get("files")
    metadata = next((item for item in files if isinstance(item, dict)
        and item.get("kind") == variant and item.get("path") == source_path), None) if isinstance(files, list) else None
    file_duration = metadata.get("duration_seconds") if metadata else None
    file_fps = metadata.get("fps") if metadata else None
    if not isinstance(file_duration, (int, float)) or not isinstance(file_fps, (int, float)):
        raise ValueError("指定导出缺少实际文件的时长和帧率检查。")
    duration, fps = float(file_duration), float(file_fps)
    if fps <= 0 or len(times) > 4 or any(not 0 <= value < duration for value in times):
        raise ValueError("最多选择四帧，时间须位于实际文件的 [0, duration_seconds) 内。")
    source = Path(source_path)
    output_dir = service.workspace_root / record.project_id / "inspections" / f"export-frames-{uuid4().hex}"
    samples = extract_detail_frames(source=source, take_id=record.id, output_dir=output_dir,
        times=times, start=0, end=duration, source_fps=fps, max_dimension=2048)
    ref = ArtifactRef(project_id=record.project_id, artifact_type="studio_export",
        artifact_id=record.id, version=record.editorial_revision)
    matches = content.get("current_editorial_revision") == record.editorial_revision
    rows: list[JsonValue] = []
    attachments: list[VisualAttachment] = []
    for sample in samples:
        purpose = (f"Actual {variant} rendered export {record.id}, saved editorial revision {record.editorial_revision}; "
            f"EXPORT_FILM requested {sample.requested_timestamp_seconds}s, actual decoded PTS {sample.timestamp_seconds}s. "
            "Includes this file's rendered layers, titles, transitions and subtitle choice. "
            "A still frame does not establish intervening motion, spoken words or audio/video synchronisation.")
        rows.append({"requested_seconds": sample.requested_timestamp_seconds,
            "actual_pts_seconds": sample.timestamp_seconds, "timestamp_is_source_pts": True,
            "image_path": sample.image_path, "width": sample.width, "height": sample.height,
            "selection_reason": sample.reasons[0]})
        attachments.append(VisualAttachment(artifact_ref=ref, asset_id=record.id, role="SOURCE", purpose=purpose,
            local_path=sample.image_path, approval_status=ApprovalStatus.DRAFT,
            freshness=Freshness.CURRENT if matches else Freshness.STALE))
    content["rendered_frames"] = {"export_id": record.id, "editorial_revision": record.editorial_revision,
        "matches_editorial_revision_at_read": matches, "variant": variant, "subtitles_enabled": record.subtitles if variant == "subtitled" else False,
        "time_space": "EXPORT_FILM", "source_path": str(source), "source_bytes": source.stat().st_size,
        "file_duration_seconds": duration, "editorial_duration_seconds": record.duration_seconds,
        "frames": rows, "audio_semantics_observed": False,
        "scope": "所附图片直接来自本次指定导出，不是源 Take、当前实时预览或整段视听通过结论；旧修订导出仍可观察，但不能代替当前剪辑。"}
    return tuple(attachments)
