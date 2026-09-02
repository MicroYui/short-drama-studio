"""Prepare visual-only H3 reference videos from accepted production media.

The generated clips are optional Route Agent inputs. They do not decide whether a
shot can proceed and do not alter the provider-neutral ShotPlan.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

from short_drama.h3_models import (
    H3CameraMotionPreset,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3VideoGuide,
    H3VideoGuideBaseSource,
    H3VideoGuideKind,
)
from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.models import (
    Freshness,
    ProjectDocument,
    QaStatus,
    ShotPlan,
    Take,
    TakeAvailability,
)


_GUIDE_WIDTH = 768
_GUIDE_HEIGHT = 1344
_GUIDE_FPS = 24
_TAIL_SECONDS = 4.0
_PREVIS_SECONDS = 4.0


def camera_motion_preset(camera: str) -> H3CameraMotionPreset:
    """Map free-form Director language to a small generic camera-previs vocabulary."""

    value = camera.casefold()
    mappings: tuple[tuple[H3CameraMotionPreset, tuple[str, ...]], ...] = (
        (
            H3CameraMotionPreset.DOLLY_IN,
            (
                "推近",
                "推进",
                "轻推",
                "推镜",
                "靠近",
                "前移",
                "dolly in",
                "push in",
                "zoom in",
            ),
        ),
        (
            H3CameraMotionPreset.DOLLY_OUT,
            ("拉远", "后退", "后移", "dolly out", "pull out", "zoom out"),
        ),
        (
            H3CameraMotionPreset.JIB_UP,
            ("升起", "上升", "摇上", "抬升", "jib up", "crane up", "tilt up"),
        ),
        (
            H3CameraMotionPreset.JIB_DOWN,
            ("下降", "下沉", "摇下", "降低", "jib down", "crane down", "tilt down"),
        ),
        (
            H3CameraMotionPreset.TRACK_LEFT,
            ("左移", "向左", "左摇", "track left", "pan left", "dolly left"),
        ),
        (
            H3CameraMotionPreset.TRACK_RIGHT,
            ("右移", "向右", "右摇", "track right", "pan right", "dolly right"),
        ),
        (
            H3CameraMotionPreset.FOLLOW,
            ("跟随", "跟拍", "追随", "tracking", "follow"),
        ),
    )
    for preset, tokens in mappings:
        if any(token in value for token in tokens):
            return preset
    return H3CameraMotionPreset.STATIC


def append_h3_video_guide_guidance(prompt: str, guide: H3VideoGuide) -> str:
    """Tell H3 which part of an optional guide is authoritative."""

    if guide.kind == H3VideoGuideKind.PREVIOUS_TAKE:
        guidance = (
            "<Video 1>是紧邻前镜的无声完整 Take，只用于承接人物身份、场景布局、"
            "动作方向和摄影机动势；当前镜头必须从前镜结束之后继续，不得重演前镜、"
            "复制其对白、剪辑或其中的偶发错误。"
        )
    elif guide.kind == H3VideoGuideKind.TAIL_WINDOW:
        guidance = (
            "<Video 1>只包含紧邻前镜最后一段无声运动窗口，用于承接边界姿态、视线、"
            "道具状态、银幕方向和摄影机惯性；当前镜头从该终态之后自然继续，不得把"
            "前镜末段原样重复。"
        )
    else:
        motion = guide.camera_motion.value if guide.camera_motion is not None else "STATIC"
        if guide.previs_base_source == H3VideoGuideBaseSource.TARGET_START_FRAME:
            guidance = (
                f"<Video 1>是从本镜头已批准起始构图生成的低保真二维机位预演（{motion}）。"
                "保留其起始主体、场景、位置和构图关系，只参考 ShotPlan 明确需要的粗略"
                "摄影机路径与节奏；预演自身的低保真压缩和插值伪影不是风格要求。最终画面"
                "的媒介、静动关系、表演和空间表达全部服从本镜头与项目 Style。"
            )
        else:
            guidance = (
                f"<Video 1>是从前镜边界画面生成的低保真二维机位预演（{motion}），只参考"
                "ShotPlan 明确需要的粗略摄影机路径与节奏；预演自身的低保真压缩和插值"
                "伪影不是风格要求。最终画面的媒介、静动关系、表演和空间表达全部服从"
                "本镜头与项目 Style。"
            )
    return f"{prompt} 视频连续性参考：{guidance}"


class H3VideoGuideBuilder:
    """Create reusable, visual-only continuity guides without calling a provider."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def prepare(
        self,
        *,
        document: ProjectDocument,
        takes: list[Take],
        shot_ids: list[str],
        created_at: str,
        prepared_media_inputs: dict[str, list[H3MediaInput]] | None = None,
    ) -> tuple[dict[str, list[H3VideoGuide]], list[str]]:
        selected_ids = set(shot_ids)
        ordered = sorted(document.shots, key=lambda item: item.sequence)
        source_take_by_shot = self._selected_source_takes(takes)
        guides: dict[str, list[H3VideoGuide]] = {}
        warnings: list[str] = []

        for index, shot in enumerate(ordered):
            if index == 0 or (selected_ids and shot.id not in selected_ids):
                continue
            target_take = source_take_by_shot.get(shot.id)
            if target_take is not None and target_take.qa_status == QaStatus.PASSED:
                continue
            previous = ordered[index - 1]
            if previous.scene_id != shot.scene_id:
                continue
            source_take = source_take_by_shot.get(previous.id)
            if source_take is None:
                continue
            target_start_frame = self._target_start_frame(
                (prepared_media_inputs or {}).get(shot.id, [])
            )
            try:
                built = self._prepare_for_shot(
                    document=document,
                    shot=shot,
                    previous=previous,
                    source_take=source_take,
                    created_at=created_at,
                    target_start_frame=target_start_frame,
                )
            except MediaGenerationError as exc:
                warnings.append(
                    f"Optional H3 video guides for shot {shot.sequence} were unavailable: {exc}"
                )
                continue
            if built:
                guides[shot.id] = built
        return guides, warnings

    @staticmethod
    def _selected_source_takes(takes: list[Take]) -> dict[str, Take]:
        candidates: dict[str, Take] = {}
        for take in takes:
            path = Path(take.media_path).expanduser()
            if (
                not take.is_selected
                or take.freshness != Freshness.CURRENT
                or take.availability != TakeAvailability.AVAILABLE
                or take.qa_status == QaStatus.PENDING
                or not path.is_file()
                or path.stat().st_size == 0
            ):
                continue
            current = candidates.get(take.shot_id)
            if current is None or (take.created_at, take.id) > (
                current.created_at,
                current.id,
            ):
                candidates[take.shot_id] = take
        return candidates

    @staticmethod
    def _target_start_frame(media_inputs: list[H3MediaInput]) -> H3MediaInput | None:
        for item in media_inputs:
            path = item.source.local_path
            if (
                item.kind == H3MediaKind.IMAGE
                and item.role == H3MediaRole.FIRST_FRAME
                and path is not None
                and Path(path).expanduser().is_file()
            ):
                return item
        return None

    def _prepare_for_shot(
        self,
        *,
        document: ProjectDocument,
        shot: ShotPlan,
        previous: ShotPlan,
        source_take: Take,
        created_at: str,
        target_start_frame: H3MediaInput | None,
    ) -> list[H3VideoGuide]:
        source_path = Path(source_take.media_path).expanduser().resolve()
        metadata = probe_media_with_ffmpeg(source_path)
        try:
            actual_duration = float(metadata["duration"])
        except (KeyError, ValueError) as exc:
            raise MediaGenerationError(
                f"Could not read duration for source Take {source_take.id}"
            ) from exc
        if actual_duration <= 0.0:
            raise MediaGenerationError(f"Source Take {source_take.id} has no usable duration")
        full_duration = min(15.0, actual_duration)
        full_in = max(0.0, actual_duration - full_duration)

        guide_dir = (
            self.workspace_root
            / document.project.id
            / "h3-video-guides"
            / self._safe_component(shot.id)
        )
        guide_dir.mkdir(parents=True, exist_ok=True)
        source_token = self._safe_component(source_take.id)
        revision_token = f"r{shot.revision}"
        common_warnings = self._source_warnings(source_take)
        issue_codes = [item.code for item in source_take.qa_report.issues]

        full_path = guide_dir / f"previous-{source_token}-{revision_token}.mp4"
        self._ensure_clip(
            source_path=source_path,
            destination=full_path,
            source_in=full_in,
            duration=full_duration,
        )

        tail_duration = min(_TAIL_SECONDS, actual_duration)
        tail_in = max(0.0, actual_duration - tail_duration)
        tail_path = guide_dir / f"tail-{source_token}-{revision_token}.mp4"
        self._ensure_clip(
            source_path=source_path,
            destination=tail_path,
            source_in=tail_in,
            duration=tail_duration,
        )

        motion = camera_motion_preset(shot.camera)
        if target_start_frame is not None:
            assert target_start_frame.source.local_path is not None
            raw_frame_path = Path(target_start_frame.source.local_path).expanduser().resolve()
            base_token = self._safe_component(
                target_start_frame.asset_version_id or raw_frame_path.stem
            )
            previs_base_source = H3VideoGuideBaseSource.TARGET_START_FRAME
            base_asset_version_id = target_start_frame.asset_version_id
            frame_path = guide_dir / f"previs-base-target-{base_token}-{revision_token}.jpg"
            previs_path = guide_dir / f"previs-target-{base_token}-{revision_token}.mp4"
            self._ensure_image_frame(
                source_path=raw_frame_path,
                destination=frame_path,
            )
            previs_rationale = (
                "Preview the Director's coarse camera path from the current shot's approved "
                "start composition; H3 should retain that composition while synthesizing natural motion."
            )
            previs_warnings = [
                "A 2D camera previs does not prove feasible body blocking or three-dimensional occlusion.",
                "The approved start frame controls composition but does not guarantee action continuity.",
            ]
        else:
            base_token = source_token
            previs_base_source = H3VideoGuideBaseSource.PREVIOUS_BOUNDARY_FRAME
            base_asset_version_id = None
            frame_path = guide_dir / f"previs-base-previous-{base_token}-{revision_token}.jpg"
            previs_path = guide_dir / f"previs-previous-{base_token}-{revision_token}.mp4"
            self._ensure_last_frame(
                source_path=source_path,
                destination=frame_path,
                timestamp=max(0.0, actual_duration - 0.30),
            )
            previs_rationale = (
                "Preview the Director's coarse camera path from the preceding boundary frame; "
                "H3 should use only the requested motion and retain the project's declared style."
            )
            previs_warnings = [
                *common_warnings,
                "A 2D camera previs does not prove feasible body blocking or three-dimensional occlusion.",
                "A preceding boundary frame may preserve the wrong coverage for a new camera angle.",
            ]
        self._ensure_previs(
            image_path=frame_path,
            destination=previs_path,
            duration=_PREVIS_SECONDS,
            motion=motion,
        )
        full_output_duration = self._media_duration(full_path)
        tail_output_duration = self._media_duration(tail_path)
        previs_output_duration = self._media_duration(previs_path)

        return [
            H3VideoGuide(
                id=self._guide_id(H3VideoGuideKind.PREVIOUS_TAKE, shot, source_take),
                shot_id=shot.id,
                source_shot_id=previous.id,
                source_shot_revision=previous.revision,
                source_take_id=source_take.id,
                kind=H3VideoGuideKind.PREVIOUS_TAKE,
                media_path=str(full_path.resolve()),
                duration_seconds=full_output_duration,
                source_in_seconds=full_in,
                source_qa_status=source_take.qa_status,
                source_quality_score=source_take.qa_report.score,
                source_issue_codes=issue_codes,
                rationale=(
                    "Carry the preceding accepted Take's wider performance and camera context "
                    "into the current shot as an optional visual-only reference."
                ),
                warnings=[
                    *common_warnings,
                    "A full preceding Take can propagate unwanted motion, composition, or scene errors.",
                ],
                created_at=created_at,
            ),
            H3VideoGuide(
                id=self._guide_id(H3VideoGuideKind.TAIL_WINDOW, shot, source_take),
                shot_id=shot.id,
                source_shot_id=previous.id,
                source_shot_revision=previous.revision,
                source_take_id=source_take.id,
                kind=H3VideoGuideKind.TAIL_WINDOW,
                media_path=str(tail_path.resolve()),
                duration_seconds=tail_output_duration,
                source_in_seconds=tail_in,
                source_qa_status=source_take.qa_status,
                source_quality_score=source_take.qa_report.score,
                source_issue_codes=issue_codes,
                rationale=(
                    "Use only the preceding Take's final motion window to anchor boundary pose, "
                    "screen direction, prop state, and camera momentum with a smaller error surface."
                ),
                warnings=[
                    *common_warnings,
                    "The tail window preserves less of the location layout and earlier choreography.",
                ],
                created_at=created_at,
            ),
            H3VideoGuide(
                id=self._guide_id(
                    H3VideoGuideKind.CAMERA_PREVIS,
                    shot,
                    source_take,
                    base_token=base_token,
                ),
                shot_id=shot.id,
                source_shot_id=previous.id,
                source_shot_revision=previous.revision,
                source_take_id=source_take.id,
                kind=H3VideoGuideKind.CAMERA_PREVIS,
                media_path=str(previs_path.resolve()),
                duration_seconds=previs_output_duration,
                camera_motion=motion,
                previs_base_source=previs_base_source,
                base_asset_version_id=base_asset_version_id,
                source_qa_status=source_take.qa_status,
                source_quality_score=source_take.qa_report.score,
                source_issue_codes=issue_codes,
                rationale=previs_rationale,
                warnings=previs_warnings,
                created_at=created_at,
            ),
        ]

    @staticmethod
    def _source_warnings(source_take: Take) -> list[str]:
        if source_take.qa_status == QaStatus.PASSED:
            return []
        return [
            "The selected source Take still has visible QA issues; the Route Agent should weigh error inheritance."
        ]

    @staticmethod
    def _safe_component(value: str) -> str:
        result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
        return result or "artifact"

    @staticmethod
    def _guide_id(
        kind: H3VideoGuideKind,
        shot: ShotPlan,
        source_take: Take,
        *,
        base_token: str | None = None,
    ) -> str:
        suffix = f"-{base_token}" if base_token is not None else ""
        return (
            f"h3-video-guide-{kind.value.lower()}-{shot.id}-{source_take.id}"
            f"-r{shot.revision}{suffix}"
        )

    @staticmethod
    def _ensure_clip(
        *,
        source_path: Path,
        destination: Path,
        source_in: float,
        duration: float,
    ) -> None:
        if H3VideoGuideBuilder._usable_output(destination):
            return
        H3VideoGuideBuilder._run_ffmpeg(
            [
                "-y",
                "-ss",
                f"{source_in:.6f}",
                "-i",
                str(source_path),
                "-t",
                f"{duration:.6f}",
                "-an",
                "-vf",
                (
                    f"scale={_GUIDE_WIDTH}:{_GUIDE_HEIGHT}:"
                    "force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={_GUIDE_WIDTH}:{_GUIDE_HEIGHT},setsar=1,"
                    f"fps={_GUIDE_FPS},format=yuv420p"
                ),
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )

    @staticmethod
    def _ensure_last_frame(
        *,
        source_path: Path,
        destination: Path,
        timestamp: float,
    ) -> None:
        if H3VideoGuideBuilder._usable_output(destination):
            return
        H3VideoGuideBuilder._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source_path),
                "-ss",
                f"{timestamp:.6f}",
                "-frames:v",
                "1",
                "-vf",
                (
                    f"scale={_GUIDE_WIDTH}:{_GUIDE_HEIGHT}:"
                    "force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={_GUIDE_WIDTH}:{_GUIDE_HEIGHT},format=yuvj420p"
                ),
                "-q:v",
                "2",
                str(destination),
            ]
        )
        if not H3VideoGuideBuilder._usable_output(destination):
            raise MediaGenerationError(
                f"Could not extract a previs base frame from {source_path}"
            )

    @staticmethod
    def _ensure_image_frame(
        *,
        source_path: Path,
        destination: Path,
    ) -> None:
        if H3VideoGuideBuilder._usable_output(destination):
            return
        H3VideoGuideBuilder._run_ffmpeg(
            [
                "-y",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    f"scale={_GUIDE_WIDTH}:{_GUIDE_HEIGHT}:"
                    "force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={_GUIDE_WIDTH}:{_GUIDE_HEIGHT},format=yuvj420p"
                ),
                "-q:v",
                "2",
                str(destination),
            ]
        )
        if not H3VideoGuideBuilder._usable_output(destination):
            raise MediaGenerationError(
                f"Could not normalize the approved previs base frame {source_path}"
            )

    @staticmethod
    def _ensure_previs(
        *,
        image_path: Path,
        destination: Path,
        duration: float,
        motion: H3CameraMotionPreset,
    ) -> None:
        if H3VideoGuideBuilder._usable_output(destination):
            return
        frames = max(2, round(duration * _GUIDE_FPS))
        denominator = max(1, frames - 1)
        zoom, x, y = H3VideoGuideBuilder._zoompan_expressions(motion, denominator)
        H3VideoGuideBuilder._run_ffmpeg(
            [
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-frames:v",
                str(frames),
                "-vf",
                (
                    f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:"
                    f"s={_GUIDE_WIDTH}x{_GUIDE_HEIGHT}:fps={_GUIDE_FPS},"
                    "setsar=1,format=yuv420p"
                ),
                "-an",
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )

    @staticmethod
    def _zoompan_expressions(
        motion: H3CameraMotionPreset,
        denominator: int,
    ) -> tuple[str, str, str]:
        centered_x = "iw/2-(iw/zoom/2)"
        centered_y = "ih/2-(ih/zoom/2)"
        progress = f"on/{denominator}"
        if motion == H3CameraMotionPreset.DOLLY_IN:
            return f"1+0.12*{progress}", centered_x, centered_y
        if motion == H3CameraMotionPreset.DOLLY_OUT:
            return f"1.12-0.12*{progress}", centered_x, centered_y
        if motion == H3CameraMotionPreset.TRACK_LEFT:
            return "1.12", f"(iw-iw/zoom)*(1-{progress})", centered_y
        if motion == H3CameraMotionPreset.TRACK_RIGHT:
            return "1.12", f"(iw-iw/zoom)*{progress}", centered_y
        if motion == H3CameraMotionPreset.JIB_UP:
            return "1.12", centered_x, f"(ih-ih/zoom)*(1-{progress})"
        if motion == H3CameraMotionPreset.JIB_DOWN:
            return "1.12", centered_x, f"(ih-ih/zoom)*{progress}"
        if motion == H3CameraMotionPreset.FOLLOW:
            return "1.08", f"(iw-iw/zoom)*(0.15+0.7*{progress})", centered_y
        return "1", "0", "0"

    @staticmethod
    def _usable_output(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    @staticmethod
    def _media_duration(path: Path) -> float:
        try:
            duration = float(probe_media_with_ffmpeg(path)["duration"])
        except (KeyError, ValueError) as exc:
            raise MediaGenerationError(f"Could not read generated guide duration: {path}") from exc
        if duration <= 0.0 or duration > 15.0:
            raise MediaGenerationError(f"Generated guide has invalid duration: {path}")
        return duration

    @staticmethod
    def _run_ffmpeg(args: list[str]) -> None:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown ffmpeg failure"
            raise MediaGenerationError(detail)
