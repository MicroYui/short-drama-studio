"""Asset versioning, imports, approval, and persistent image-provider tasks."""

from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)

from short_drama.image_models import (
    AssetBriefUpdateRequest,
    AssetBatchCreateRequest,
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImageCropRequest,
    AssetReferenceSheetComposeRequest,
    AssetTextPlateComposeRequest,
    AssetImportUrlRequest,
    AssetReviewDecision,
    AssetReviewPurpose,
    AssetReviewRequest,
    ImageBackground,
    ImageCostRecord,
    ImageGenerationTask,
    ImageOperation,
    ImageNotSubmittedResolutionRequest,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
    ImageProviderRequest,
    ImageReferenceInput,
    ImageRetryDisposition,
    ImageTaskCreateRequest,
    ImageTaskStatus,
    ImageUsage,
)
from short_drama.image_edit_guides import (
    AssetImageEditGuideRequest, AssetImageEditGuideResponse, render_edit_guide,
)
from short_drama.image_plane_composite import AssetImagePlaneCompositeRequest, render_plane_composite
from short_drama.models import (
    ImageReferenceRole,
    ApprovalStatus,
    Asset,
    AssetDerivationReference,
    AssetKind,
    AssetImageCrop,
    AssetLayoutProfile,
    AssetMediaType,
    AssetPresentationMode,
    AssetReferenceSheet,
    AssetReferenceSheetCell,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetSourceRecord,
    AssetStandardView,
    AssetTextPlate,
    AssetVersion,
    CoreAssetBrief,
    Freshness,
    ImageReferenceAuthority,
    OriginType,
    ProjectDocument,
    TextPlateAppendedTextBlock,
)
from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.image_regions import read_image_region
from short_drama.providers.image_config import ImageProviderConfigStore
from short_drama.providers.image_provider import (
    ImageProvider,
    ImageProviderDefiniteError,
    ImageProviderInput,
    ImageProviderResult,
    ImageProviderSubmissionUnknown,
)
from short_drama.providers.openai_image_client import OpenAIImageClient
from short_drama.providers.openai_image_endpoint import resolve_image_endpoint
from short_drama.repository import ShortDramaRepository
from short_drama.stock_assets import StockAssetImportRequest
from short_drama.worker import utc_now


class AssetWorkbenchError(RuntimeError):
    """Base class for asset workbench failures."""


class AssetWorkbenchNotFound(AssetWorkbenchError):
    """Requested project, asset, version, or image task does not exist."""


class AssetWorkbenchConflict(AssetWorkbenchError):
    """Requested action is incompatible with current persisted state."""


class AssetProviderConfigurationError(AssetWorkbenchError):
    """A paid image action needs a configured provider credential."""


class ImageOutputDimensionMismatch(ValueError):
    """Provider output violates the explicit requested image dimensions."""


ImageProviderFactory = Callable[[str, str], ImageProvider]


def default_image_provider_factory(api_key: str, base_url: str) -> ImageProvider:
    return OpenAIImageClient(api_key=api_key, base_url=base_url)


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}
_VOICE_UPLOAD_EXTENSIONS = {*_AUDIO_EXTENSIONS, ".ogg", ".webm"}
_MAX_LOCAL_ASSET_BYTES = 256 * 1024 * 1024
_MAX_VOICE_UPLOAD_BYTES = 32 * 1024 * 1024
_MAX_H3_AUDIO_BYTES = 15 * 1024 * 1024
_MAX_EDIT_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_EDIT_MASK_BYTES = 4 * 1024 * 1024
_REFERENCE_SHEET_CELL_WIDTH = 512
_REFERENCE_SHEET_IMAGE_HEIGHT = 512
_REFERENCE_SHEET_LABEL_HEIGHT = 40
_REFERENCE_SHEET_PADDING = 16
_REFERENCE_SHEET_WIDE_SIZE = (1536, 864)
_REFERENCE_SHEET_MIN_CELL_IMAGE_DIMENSION = 64
_TEXT_PLATE_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/STHeiti Medium.ttc"), "STHeiti Medium"),
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), "Arial Unicode"),
    (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), "Songti"),
    (Path("C:/Windows/Fonts/msyh.ttc"), "Microsoft YaHei"),
    (Path("C:/Windows/Fonts/simhei.ttf"), "SimHei"),
    (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        "Noto Sans CJK",
    ),
    (
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        "WenQuanYi Zen Hei",
    ),
)
_CHARACTER_STANDARD_VIEWS = frozenset(
    {
        AssetStandardView.CANONICAL_MASTER,
        AssetStandardView.CHARACTER_FRONT,
        AssetStandardView.CHARACTER_LEFT_PROFILE,
        AssetStandardView.CHARACTER_RIGHT_PROFILE,
        AssetStandardView.CHARACTER_BACK,
        AssetStandardView.CHARACTER_WARDROBE_DETAIL,
        AssetStandardView.CHARACTER_SHEET,
    }
)
_SCENE_COVERAGE_VIEWS = frozenset(
    {
        AssetStandardView.CANONICAL_MASTER,
        AssetStandardView.SCENE_MASTER,
        AssetStandardView.SCENE_FRONT,
        AssetStandardView.SCENE_OVERHEAD,
        AssetStandardView.SCENE_REVERSE,
        AssetStandardView.SCENE_LEFT,
        AssetStandardView.SCENE_RIGHT,
        AssetStandardView.SCENE_INTERACTION_ZONE,
    }
)


class AssetWorkbench:
    """Owns provider-neutral asset mutations and synchronous image task execution."""

    def __init__(
        self,
        *,
        repository: ShortDramaRepository,
        workspace_root: Path,
        config_store: ImageProviderConfigStore,
        provider_factory: ImageProviderFactory = default_image_provider_factory,
        lock: threading.RLock | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root
        self.config_store = config_store
        self.provider_factory = provider_factory
        self._lock = lock if lock is not None else threading.RLock()
        self._image_advance_locks: dict[str, threading.Lock] = {}
        self.recovered_submissions = self.repository.recover_interrupted_image_tasks()

    def get_config(self) -> ImageProviderConfigResponse:
        return self.config_store.response()

    def update_config(
        self,
        request: ImageProviderConfigUpdate,
    ) -> ImageProviderConfigResponse:
        return self.config_store.update(request)

    def validate_credentials(self) -> ImageProviderConfigResponse:
        self._provider().validate_credentials()
        return self.config_store.response()

    def validate_planned_asset_contract(
        self,
        kind: AssetKind,
        brief: CoreAssetBrief | None,
        outputs: Sequence[tuple[AssetLayoutProfile, str]],
    ) -> None:
        """Check that an AssetPlan can be executed by the same workbench rules."""

        presentation_mode = None
        if brief is not None:
            presentation_mode = brief.presentation_mode
            self._validate_asset_strategy(kind, brief.reference_strategy)
            self._validate_asset_layout(
                kind,
                brief.layout_profile,
                brief.presentation_mode,
            )
            self._validate_standard_views(
                brief.reference_strategy,
                brief.requested_views,
            )
        for layout_profile, size in outputs:
            self._validate_asset_layout(kind, layout_profile, presentation_mode)
            self._validate_layout_output_size(layout_profile, size)

    def create_asset(self, project_id: str, request: AssetCreateRequest) -> AssetVersion:
        assets = self.create_assets(project_id, AssetBatchCreateRequest(assets=[request]))
        return assets[0].versions[0]

    def _validate_asset_create(self, request: AssetCreateRequest) -> None:
        self._validate_asset_strategy(
            request.kind,
            request.brief.reference_strategy if request.brief is not None else None,
        )
        if request.brief is not None:
            self._validate_asset_layout(
                request.kind,
                request.brief.layout_profile,
                request.brief.presentation_mode,
            )
            self._validate_standard_views(
                request.brief.reference_strategy,
                request.brief.requested_views,
            )

    def create_assets(self, project_id: str, request: AssetBatchCreateRequest) -> list[Asset]:
        # Validate the entire batch before constructing or saving any object.
        # The same rules serve both the single and multiple-object interfaces.
        for item in request.assets:
            self._validate_asset_create(item)
        with self._lock:
            document = self._require_project(project_id)
            now = utc_now()
            assets: list[Asset] = []
            for item in request.assets:
                version = AssetVersion(
                    id=f"asset-version-{uuid4().hex[:12]}",
                    version=1,
                    origin_type=OriginType.MANUAL,
                    descriptor=item.descriptor.strip(),
                    media_type=AssetMediaType.TEXT_REFERENCE,
                    approval_status=ApprovalStatus.DRAFT,
                    freshness=Freshness.CURRENT,
                    source=AssetSourceRecord(
                        source_type=OriginType.MANUAL,
                        source_label="用户创建的资产说明",
                        created_at=now,
                    ),
                    created_at=now,
                )
                assets.append(Asset(
                    id=f"asset-{uuid4().hex[:12]}", project_id=project_id,
                    kind=item.kind, name=item.name.strip(),
                    brief=item.brief.model_copy(deep=True) if item.brief is not None else None,
                    current_version_id=version.id, versions=[version],
                ))
            document.assets.extend(assets)
            document.project.updated_at = now
            self.repository.save_project(document)
            # save_project merges current edits and normalizes shared episode
            # assets to their root project. Return those actually saved values.
            saved = {asset.id: asset for asset in document.assets}
            return [saved[asset.id] for asset in assets]

    def update_asset_brief(
        self,
        project_id: str,
        asset_id: str,
        request: AssetBriefUpdateRequest,
    ) -> AssetVersion:
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id)
            self._validate_asset_strategy(asset.kind, request.brief.reference_strategy)
            self._validate_asset_layout(
                asset.kind,
                request.brief.layout_profile,
                request.brief.presentation_mode,
            )
            self._validate_standard_views(
                request.brief.reference_strategy,
                request.brief.requested_views,
            )
            asset.brief = request.brief.model_copy(deep=True)
            current = self._require_asset_version(asset, asset.current_version_id)
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return current

    def import_local(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportLocalRequest,
    ) -> AssetVersion:
        source_path = Path(request.local_path).expanduser().resolve()
        if not source_path.is_file():
            raise AssetWorkbenchConflict(f"Local asset does not exist: {source_path}")
        size_bytes = source_path.stat().st_size
        if size_bytes <= 0 or size_bytes > _MAX_LOCAL_ASSET_BYTES:
            raise AssetWorkbenchConflict("Local asset must be between 1 byte and 256 MB")
        media_type = self._media_type_for_path(source_path)
        metadata = probe_media_with_ffmpeg(source_path) if media_type in {AssetMediaType.VIDEO, AssetMediaType.AUDIO} else None
        if metadata is not None and float(metadata["duration"]) <= 0:
            raise AssetWorkbenchConflict("导入的音视频文件没有可读取的时长。")
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id)
            self._validate_asset_media_kind(asset, media_type)
            parent_id = self._resolve_parent(asset, request.parent_version_id)
            number = self._next_version_number(asset)
            version_id = f"asset-version-{uuid4().hex[:12]}"
            destination = self._asset_directory(project_id, asset_id) / (
                f"v{number}-{version_id}{source_path.suffix.lower()}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            mime_type = mimetypes.guess_type(destination.name)[0]
            width, height = self._image_dimensions(destination, media_type)
            if media_type == AssetMediaType.VIDEO and metadata is not None:
                dimensions = metadata["size"].split("x")
                if len(dimensions) != 2:
                    raise AssetWorkbenchConflict("导入文件没有可读取的视频画面。")
                width, height = int(dimensions[0]), int(dimensions[1])
            now = utc_now()
            version = AssetVersion(
                id=version_id,
                version=number,
                origin_type=OriginType.UPLOAD,
                descriptor=request.descriptor.strip(),
                media_type=media_type,
                local_path=str(destination),
                mime_type=mime_type,
                width=width,
                height=height,
                duration_seconds=float(metadata["duration"]) if metadata else None,
                file_size_bytes=destination.stat().st_size,
                parent_version_id=parent_id,
                source=AssetSourceRecord(
                    source_type=OriginType.UPLOAD,
                    source_label="本地文件导入",
                    source_uri=str(source_path),
                    original_filename=source_path.name,
                    created_at=now,
                ),
                approval_status=ApprovalStatus.DRAFT,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            self._append_version(document, asset, version)
            return version

    def import_url(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportUrlRequest,
    ) -> AssetVersion:
        external_url = request.external_url.strip()
        if not external_url.startswith(("https://", "http://")):
            raise AssetWorkbenchConflict("External asset URL must use HTTP or HTTPS")
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id)
            self._validate_asset_media_kind(asset, request.media_type)
            parent_id = self._resolve_parent(asset, request.parent_version_id)
            now = utc_now()
            version = AssetVersion(
                id=f"asset-version-{uuid4().hex[:12]}",
                version=self._next_version_number(asset),
                origin_type=OriginType.LIBRARY_IMPORT,
                descriptor=request.descriptor.strip(),
                media_type=request.media_type,
                external_url=external_url,
                parent_version_id=parent_id,
                source=AssetSourceRecord(
                    source_type=OriginType.LIBRARY_IMPORT,
                    source_label="外部链接导入",
                    source_uri=external_url,
                    created_at=now,
                ),
                approval_status=ApprovalStatus.DRAFT,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            self._append_version(document, asset, version)
            return version

    def crop_image(
        self, project_id: str, asset_id: str, request: AssetImageCropRequest,
    ) -> AssetVersion:
        """Register a lossless local crop without replacing its source or canonical master."""

        with self._lock:
            document = self._require_project(project_id)
            target = self._require_asset(document, asset_id)
            source_asset, source = self._find_version(document, request.source_asset_version_id)
            if source.media_type != AssetMediaType.IMAGE or source.local_path is None:
                raise AssetWorkbenchConflict("image.crop requires a locally cached image AssetVersion")
            if source.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED} or source.freshness != Freshness.CURRENT:
                raise AssetWorkbenchConflict(f"Crop source is not current usable media: {source.id}")
            source_path = Path(source.local_path).expanduser().resolve()
            if not source_path.is_file() or source_path.stat().st_size == 0:
                raise AssetWorkbenchConflict(f"Crop source file is missing or empty: {source.id}")
            source_stat = source_path.stat()
            expected_current_id = target.current_version_id
            output_id = f"asset-version-{uuid4().hex[:12]}"
            parent_id = source.id if source_asset.id == target.id else None

        directory = self._asset_directory(project_id, asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{output_id}-crop.png"
        destination = temporary
        try:
            try:
                cropped, source_size, box = read_image_region(source_path, request.region)
                with cropped:
                    if cropped.width < 2 or cropped.height < 2:
                        raise ValueError("Crop region must cover at least 2 pixels in each dimension")
                    if cropped.mode not in {"RGB", "RGBA", "L", "LA"}:
                        cropped = cropped.convert("RGBA" if "transparency" in cropped.info else "RGB")
                    cropped.save(temporary, format="PNG")
                    width, height = cropped.size
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise AssetWorkbenchConflict(f"Cannot crop {source.id}: {exc}") from exc
            with self._lock:
                document = self._require_project(project_id)
                target = self._require_asset(document, asset_id)
                _source_asset, current_source = self._find_version(document, source.id)
                current_stat = source_path.stat() if source_path.is_file() else None
                if (
                    target.current_version_id != expected_current_id
                    or current_source.local_path != source.local_path
                    or current_source.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
                    or current_source.freshness != Freshness.CURRENT
                    or current_stat is None
                    or (current_stat.st_size, current_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns)
                ):
                    raise AssetWorkbenchConflict("Asset or crop source changed during local processing; re-read that asset")
                output_number = self._next_version_number(target)
                destination = directory / f"v{output_number}-{output_id}-crop.png"
                temporary.replace(destination)
                now = utc_now()
                version = AssetVersion(
                    id=output_id, version=output_number, origin_type=OriginType.LOCAL_DERIVED,
                    descriptor=request.descriptor.strip(), media_type=AssetMediaType.IMAGE,
                    local_path=str(destination), mime_type="image/png", width=width, height=height,
                    file_size_bytes=destination.stat().st_size, parent_version_id=parent_id,
                    image_crop=AssetImageCrop(
                        source_asset_version_id=source.id, source_width=source_size[0],
                        source_height=source_size[1], box_pixels=box,
                    ),
                    derivation_references=[AssetDerivationReference(
                        asset_version_id=source.id, source_project_id=project_id,
                        reference_role=request.reference_role,
                        reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
                        purpose="原图中的精确局部像素；仅承担本次声明的参考职责。",
                        subject_alias=source_asset.name,
                    )],
                    source=AssetSourceRecord(
                        source_type=OriginType.LOCAL_DERIVED, source_label="本地图像裁切",
                        provider="local", model="pillow-crop-v1", created_at=now,
                    ),
                    approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=now,
                )
                self._append_version(document, target, version)
                return version
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

    def create_edit_guide(
        self, project_id: str, request: AssetImageEditGuideRequest,
    ) -> AssetImageEditGuideResponse:
        """Save local edit instructions and mask without selecting either as current media."""
        with self._lock:
            document = self._require_project(project_id)
            source_asset, source = self._find_version(document, request.source_asset_version_id)
            if source.media_type != AssetMediaType.IMAGE or not source.local_path:
                raise AssetWorkbenchConflict("Edit guide requires a local image AssetVersion")
            if source.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED} or source.freshness != Freshness.CURRENT:
                raise AssetWorkbenchConflict("Edit guide source is not current usable media")
            source_path = Path(source.local_path).expanduser().resolve()
            if not source_path.is_file() or source_path.stat().st_size == 0:
                raise AssetWorkbenchConflict("Edit guide source file is missing or empty")
            source_stat = source_path.stat()
            asset_id = source_asset.id
        directory = self._asset_directory(project_id, asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        guide_id, mask_id = (f"asset-version-{uuid4().hex[:12]}" for _ in range(2))
        guide_path = directory / f"{guide_id}-edit-guide.png"
        mask_path = directory / f"{mask_id}-edit-mask.png"
        try:
            font_source, _ = self._text_plate_font([region.label for region in request.regions] + [arrow.label for arrow in request.arrows])
            try:
                width, height = render_edit_guide(source_path, request, guide_path=guide_path,
                    mask_path=mask_path, font_source=font_source)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise AssetWorkbenchConflict(f"Cannot create edit guide: {exc}") from exc
            with self._lock:
                document = self._require_project(project_id)
                asset, current_source = self._find_version(document, source.id)
                current_stat = source_path.stat() if source_path.is_file() else None
                if (current_source.local_path != source.local_path
                    or current_source.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
                    or current_source.freshness != Freshness.CURRENT or current_stat is None
                    or (current_stat.st_size, current_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns)):
                    raise AssetWorkbenchConflict("Edit source changed during local processing; read that exact source again")
                now = utc_now()
                versions: list[AssetVersion] = []
                for identifier, path, kind, title in (
                    (guide_id, guide_path, "guide", "局部编辑图示"),
                    (mask_id, mask_path, "mask", "局部编辑遮罩"),
                ):
                    version = AssetVersion(id=identifier, version=self._next_version_number(asset),
                        origin_type=OriginType.LOCAL_DERIVED, descriptor=f"{title}：{request.descriptor}",
                        media_type=AssetMediaType.IMAGE, local_path=str(path), mime_type="image/png",
                        width=width, height=height, file_size_bytes=path.stat().st_size,
                        parent_version_id=source.id,
                        derivation_references=[AssetDerivationReference(asset_version_id=source.id,
                            source_project_id=project_id, reference_role=ImageReferenceRole.COMPOSITION,
                            reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
                            purpose="本次局部编辑的实际原图；图示和遮罩不代表修复完成。", subject_alias=asset.name)],
                        source=AssetSourceRecord(source_type=OriginType.LOCAL_DERIVED, source_label=title,
                            provider="local", model=f"pillow-edit-{kind}-v1", prompt=request.model_dump_json(), created_at=now),
                        approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=now)
                    asset.versions.append(version)
                    versions.append(version)
                # Auxiliary images are available by exact ID; current and canonical stay selected.
                document.project.updated_at = now
                self.repository.save_project(document)
                return AssetImageEditGuideResponse(project_id=project_id, asset_id=asset_id,
                    source_asset_version_id=source.id, guide_version=versions[0], mask_version=versions[1])
        except Exception:
            guide_path.unlink(missing_ok=True)
            mask_path.unlink(missing_ok=True)
            raise

    def composite_plane(
        self, project_id: str, request: AssetImagePlaneCompositeRequest,
    ) -> AssetVersion:
        """Save an exact local surface composite as a candidate, preserving selected media."""
        with self._lock:
            document = self._require_project(project_id)
            target, base = self._find_version(document, request.base_asset_version_id)
            asset_id = target.id
            identifiers = list(dict.fromkeys([base.id, *(layer.source_asset_version_id for layer in request.layers)]))
            sources: dict[str, tuple[AssetVersion, Path, tuple[int, int], str]] = {}
            for identifier in identifiers:
                owner, version = self._find_version(document, identifier)
                if version.media_type != AssetMediaType.IMAGE or not version.local_path:
                    raise AssetWorkbenchConflict(f"Plane composite requires a local image: {identifier}")
                if version.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED} or version.freshness != Freshness.CURRENT:
                    raise AssetWorkbenchConflict(f"Plane composite source is not current usable media: {identifier}")
                path = Path(version.local_path).expanduser().resolve()
                if not path.is_file() or path.stat().st_size == 0:
                    raise AssetWorkbenchConflict(f"Plane composite source file is missing or empty: {identifier}")
                stat = path.stat()
                sources[identifier] = (version, path, (stat.st_size, stat.st_mtime_ns), owner.name)

        directory = self._asset_directory(project_id, asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        output_id = f"asset-version-{uuid4().hex[:12]}"
        temporary = directory / f".{output_id}-plane.png"
        destination = temporary
        try:
            try:
                width, height = render_plane_composite(sources[base.id][1],
                    {identifier: entry[1] for identifier, entry in sources.items()},
                    request, output_path=temporary)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise AssetWorkbenchConflict(f"Cannot composite image plane: {exc}") from exc
            with self._lock:
                document = self._require_project(project_id)
                target = self._require_asset(document, asset_id)
                for identifier, (original, path, expected_stat, _name) in sources.items():
                    _owner, current = self._find_version(document, identifier)
                    stat = path.stat() if path.is_file() else None
                    if (current.local_path != original.local_path
                            or current.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
                            or current.freshness != Freshness.CURRENT or stat is None
                            or (stat.st_size, stat.st_mtime_ns) != expected_stat):
                        raise AssetWorkbenchConflict(f"Plane composite source changed during local processing: {identifier}; read that exact source again")
                number = self._next_version_number(target)
                destination = directory / f"v{number}-{output_id}-plane.png"
                temporary.replace(destination)
                now = utc_now()
                version = AssetVersion(id=output_id, version=number,
                    origin_type=OriginType.LOCAL_DERIVED, media_type=AssetMediaType.IMAGE,
                    descriptor=request.descriptor.strip(), local_path=str(destination), mime_type="image/png",
                    width=width, height=height, file_size_bytes=destination.stat().st_size,
                    parent_version_id=base.id,
                    derivation_references=[AssetDerivationReference(
                        asset_version_id=identifier, source_project_id=project_id,
                        reference_role=ImageReferenceRole.COMPOSITION if identifier == base.id else ImageReferenceRole.SUPPORTING_VISUAL,
                        reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
                        purpose="保留平面外及明确遮挡区的原图像素。" if identifier == base.id else "按记录的四角对应变换此来源的实际像素，不重新生成文字或图形。",
                        subject_alias=sources[identifier][3],
                    ) for identifier in identifiers],
                    source=AssetSourceRecord(source_type=OriginType.LOCAL_DERIVED, source_label="本地图像透视合成",
                        provider="local", model="pillow-plane-composite-v1", prompt=request.model_dump_json(), created_at=now),
                    approval_status=ApprovalStatus.DRAFT, freshness=Freshness.CURRENT, created_at=now)
                # A composite still needs visual inspection; preserve concurrent/user selections.
                target.versions.append(version)
                document.project.updated_at = now
                self.repository.save_project(document)
                return version
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

    def compose_reference_sheet(
        self,
        project_id: str,
        asset_id: str,
        request: AssetReferenceSheetComposeRequest,
    ) -> AssetVersion:
        """Compose explicitly selected local images into one deterministic draft version."""

        with self._lock:
            document = self._require_project(project_id)
            target = self._require_asset(document, asset_id)
            self._validate_asset_strategy(target.kind, request.strategy)
            self._validate_asset_layout(target.kind, request.layout_profile, None)
            self._validate_sheet_views(request)
            parent_id = self._resolve_parent(target, request.parent_version_id)
            expected_current_version_id = target.current_version_id
            output_version_id = f"asset-version-{uuid4().hex[:12]}"
            source_rows: list[tuple[Path, AssetReferenceSheetCell]] = []
            for requested_cell in request.cells:
                source_asset, source_version = self._find_version(
                    document,
                    requested_cell.asset_version_id,
                )
                if source_version.media_type != AssetMediaType.IMAGE:
                    raise AssetWorkbenchConflict(
                        "Reference sheet inputs must be image AssetVersions"
                    )
                if source_version.local_path is None:
                    raise AssetWorkbenchConflict(
                        "Reference sheet inputs must be locally cached images"
                    )
                source_path = Path(source_version.local_path).expanduser().resolve()
                if not source_path.is_file():
                    raise AssetWorkbenchConflict(
                        f"Reference sheet input file is missing: {source_version.id}"
                    )
                if (
                    request.input_policy == AssetSheetInputPolicy.APPROVED_ONLY
                    and (
                        source_asset.approved_version_id != source_version.id
                        or source_version.approval_status != ApprovalStatus.APPROVED
                    )
                ):
                    raise AssetWorkbenchConflict(
                        "APPROVED_ONLY reference sheets require each source AssetVersion "
                        "to be its asset's current approved version"
                    )
                if (
                    request.input_policy == AssetSheetInputPolicy.EXPLICIT_VERSIONS
                    and source_version.approval_status == ApprovalStatus.REJECTED
                ):
                    raise AssetWorkbenchConflict(
                        "Rejected AssetVersions cannot be explicitly composed into a "
                        "reference sheet"
                    )
                source_rows.append(
                    (
                        source_path,
                        AssetReferenceSheetCell(
                            view=requested_cell.view,
                            label=(requested_cell.label or requested_cell.view.value).strip(),
                            source_asset_id=source_asset.id,
                            source_version_id=source_version.id,
                        ),
                    )
                )

        try:
            ImageColor.getrgb(request.background_color.strip())
        except ValueError as exc:
            raise AssetWorkbenchConflict(
                "Reference sheet background_color is not a recognized image color"
            ) from exc
        render_columns = self._reference_sheet_columns(request)

        output_directory = self._asset_directory(project_id, asset_id)
        output_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = output_directory / f".{output_version_id}-reference-sheet.png"
        output_path = temporary_path
        try:
            width, height = self._render_reference_sheet(
                source_rows,
                columns=render_columns,
                layout_profile=request.layout_profile,
                show_labels=request.show_labels,
                background_color=request.background_color.strip(),
                target_size=request.target_size,
                destination=temporary_path,
            )
            with self._lock:
                document = self._require_project(project_id)
                target = self._require_asset(document, asset_id)
                if target.current_version_id != expected_current_version_id:
                    raise AssetWorkbenchConflict(
                        "Target asset changed while the reference sheet was being composed; retry"
                    )
                for source_path, cell in source_rows:
                    source_asset, source_version = self._find_version(
                        document,
                        cell.source_version_id,
                    )
                    if (
                        source_version.local_path is None
                        or Path(source_version.local_path).expanduser().resolve()
                        != source_path
                        or not source_path.is_file()
                    ):
                        raise AssetWorkbenchConflict(
                            "A reference sheet source changed while it was being composed; retry"
                        )
                    if (
                        request.input_policy == AssetSheetInputPolicy.APPROVED_ONLY
                        and (
                            source_asset.approved_version_id != source_version.id
                            or source_version.approval_status
                            != ApprovalStatus.APPROVED
                        )
                    ):
                        raise AssetWorkbenchConflict(
                            "A reference sheet source is no longer its asset's current "
                            "approved version; retry"
                        )
                    if (
                        request.input_policy == AssetSheetInputPolicy.EXPLICIT_VERSIONS
                        and source_version.approval_status == ApprovalStatus.REJECTED
                    ):
                        raise AssetWorkbenchConflict(
                            "A reference sheet source was rejected while it was being "
                            "composed; retry"
                        )
                authoritative_lineage: list[AssetDerivationReference] = []
                seen_lineage_version_ids: set[str] = set()
                for _source_path, cell in source_rows:
                    _source_asset, source_version = self._find_version(
                        document,
                        cell.source_version_id,
                    )
                    for reference in source_version.derivation_references:
                        if (
                            reference.reference_authority
                            != ImageReferenceAuthority.AUTHORITATIVE
                            or reference.asset_version_id
                            in seen_lineage_version_ids
                        ):
                            continue
                        authoritative_lineage.append(reference.model_copy(deep=True))
                        seen_lineage_version_ids.add(reference.asset_version_id)
                if len(authoritative_lineage) > 16:
                    raise AssetWorkbenchConflict(
                        "Reference sheet authoritative derivation lineage exceeds "
                        "16 unique source versions"
                    )
                output_number = self._next_version_number(target)
                output_path = output_directory / (
                    f"v{output_number}-{output_version_id}-reference-sheet.png"
                )
                temporary_path.replace(output_path)
                now = utc_now()
                version = AssetVersion(
                    id=output_version_id,
                    version=output_number,
                    origin_type=OriginType.LOCAL_DERIVED,
                    descriptor=request.descriptor.strip(),
                    media_type=AssetMediaType.IMAGE,
                    local_path=str(output_path),
                    mime_type="image/png",
                    width=width,
                    height=height,
                    file_size_bytes=output_path.stat().st_size,
                    parent_version_id=parent_id,
                    derivation_references=authoritative_lineage,
                    reference_sheet=AssetReferenceSheet(
                        strategy=request.strategy,
                        input_policy=request.input_policy,
                        columns=render_columns,
                        layout_profile=request.layout_profile,
                        show_labels=request.show_labels,
                        background_color=request.background_color.strip(),
                        target_size=(
                            f"{width}x{height}"
                            if request.layout_profile is not None
                            else request.target_size
                        ),
                        cells=[cell for _source_path, cell in source_rows],
                    ),
                    source=AssetSourceRecord(
                        source_type=OriginType.LOCAL_DERIVED,
                        source_label="本地确定性标准视图拼图",
                        provider="local",
                        model="pillow-reference-sheet-v1",
                        created_at=now,
                    ),
                    approval_status=ApprovalStatus.DRAFT,
                    freshness=Freshness.CURRENT,
                    created_at=now,
                )
                self._append_version(document, target, version)
                return version
        except Exception:
            temporary_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise

    def compose_text_plate(
        self,
        project_id: str,
        asset_id: str,
        request: AssetTextPlateComposeRequest,
    ) -> AssetVersion:
        """Render exact visible copy into a deterministic local PNG draft."""

        with self._lock:
            document = self._require_project(project_id)
            target = self._require_asset(document, asset_id)
            plate, base = self.resolve_text_plate_content(document, request, asset_id=asset_id)
            self.validate_text_plate_contract(target.kind, target.brief, plate, asset_id=target.id)
            parent_id = self._resolve_parent(target, request.parent_version_id)
            base_state = self._text_plate_file_state(base) if base is not None else None
            expected_current_version_id = target.current_version_id
            output_version_id = f"asset-version-{uuid4().hex[:12]}"

        try:
            background = ImageColor.getrgb(plate.background_color)
            foreground = ImageColor.getrgb(plate.foreground_color)
            ImageColor.getrgb(plate.stroke_color)
            for element in [*plate.text_blocks, *plate.lines, *plate.append_text_blocks]:
                if element.color is not None:
                    ImageColor.getrgb(element.color)
        except ValueError as exc:
            raise AssetWorkbenchConflict(
                "Text plate colors must be recognized image colors"
            ) from exc
        if background == foreground:
            raise AssetWorkbenchConflict(
                "Text plate foreground and background colors must differ"
            )

        font_source, font_family = self._text_plate_font(
            [block.text for block in request.append_text_blocks] if base is not None else request.required_visible_text()
        )
        if base is None:
            plate.font_family = font_family
        else:
            for block in plate.append_text_blocks[-len(request.append_text_blocks):]:
                block.font_family = font_family
        output_directory = self._asset_directory(project_id, asset_id)
        output_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = output_directory / f".{output_version_id}-text-plate.png"
        output_path = temporary_path
        try:
            if base is None:
                width, height = self._render_text_plate(request, font_source=font_source, destination=temporary_path)
            else:
                width, height = self._append_text_plate(base, plate, request, font_source=font_source, destination=temporary_path)
            with self._lock:
                document = self._require_project(project_id)
                target = self._require_asset(document, asset_id)
                if target.current_version_id != expected_current_version_id:
                    raise AssetWorkbenchConflict(
                        "Target asset changed while the text plate was being composed; retry"
                    )
                self.validate_text_plate_contract(target.kind, target.brief, plate, asset_id=target.id)
                if base is not None:
                    _current_plate, current_base = self.resolve_text_plate_content(document, request, asset_id=asset_id)
                    if (current_base is None or current_base.text_plate != base.text_plate
                            or self._text_plate_file_state(current_base) != base_state):
                        raise AssetWorkbenchConflict(f"base_version_id={base.id} changed during local composition; re-read this source")
                if parent_id is not None:
                    self._require_asset_version(target, parent_id)
                output_number = self._next_version_number(target)
                output_path = output_directory / (
                    f"v{output_number}-{output_version_id}-text-plate.png"
                )
                temporary_path.replace(output_path)
                now = utc_now()
                version = AssetVersion(
                    id=output_version_id,
                    version=output_number,
                    origin_type=OriginType.LOCAL_DERIVED,
                    descriptor=request.descriptor,
                    media_type=AssetMediaType.IMAGE,
                    local_path=str(output_path),
                    mime_type="image/png",
                    width=width,
                    height=height,
                    file_size_bytes=output_path.stat().st_size,
                    parent_version_id=parent_id,
                    text_plate=plate,
                    derivation_references=[] if base is None else [AssetDerivationReference(
                        asset_version_id=base.id, source_project_id=project_id,
                        reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
                        purpose="实际复制此字版 PNG 的全部既有像素，仅追加本次明确文字。",
                    )],
                    source=AssetSourceRecord(
                        source_type=OriginType.LOCAL_DERIVED,
                        source_label="本地确定性精确文字图版" if base is None else "本地字版复制与文字追加",
                        provider_asset_id=None if base is None else base.id,
                        provider="local",
                        model="pillow-text-plate-v1",
                        prompt=request.layout_description,
                        created_at=now,
                    ),
                    approval_status=ApprovalStatus.DRAFT,
                    freshness=Freshness.CURRENT,
                    created_at=now,
                )
                if base is not None or (version.text_plate and version.text_plate.purpose == "PHYSICAL_SURFACE_CONTENT"):
                    # A flat surface candidate must not replace the physical carrier's
                    # current/canonical image or rewrite its material/space brief.
                    target.versions.append(version)
                    document.project.updated_at = now
                    self.repository.save_project(document)
                else:
                    self._append_version(document, target, version)
                return version
        except Exception:
            temporary_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise

    def import_stock_candidate(
        self,
        project_id: str,
        request: StockAssetImportRequest,
    ) -> AssetVersion:
        """Register one user- or Agent-selected stock result with source metadata."""

        candidate = request.candidate
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, request.target_asset_id)
            if (
                asset.kind != AssetKind.REFERENCE
                and candidate.asset_kind != asset.kind
            ):
                raise AssetWorkbenchConflict(
                    "Stock candidate kind does not match the target asset"
                )
            parent_id = self._resolve_parent(asset, request.parent_version_id)
            now = utc_now()
            version = AssetVersion(
                id=f"asset-version-{uuid4().hex[:12]}",
                version=self._next_version_number(asset),
                origin_type=OriginType.LIBRARY_IMPORT,
                descriptor=request.descriptor.strip(),
                media_type=AssetMediaType.IMAGE,
                external_url=candidate.image_url,
                width=candidate.width,
                height=candidate.height,
                parent_version_id=parent_id,
                source=AssetSourceRecord(
                    source_type=OriginType.LIBRARY_IMPORT,
                    source_label=f"外部素材库：{candidate.provider.value}",
                    source_uri=candidate.source_page_url,
                    provider=candidate.provider.value,
                    provider_asset_id=candidate.provider_asset_id,
                    creator=candidate.creator,
                    creator_uri=candidate.creator_url,
                    license_name=candidate.license_name,
                    license_uri=candidate.license_url,
                    attribution=candidate.attribution,
                    stock_import_mode=request.import_mode.value,
                    created_at=now,
                ),
                approval_status=ApprovalStatus.DRAFT,
                review_notes="；".join(candidate.advisory_notes) or None,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            self._append_version(document, asset, version)
            return version

    def clone_catalog_image(
        self,
        project_id: str,
        target_asset_id: str,
        *,
        source_project_id: str,
        source_asset_id: str,
        source_asset_name: str,
        source_version: AssetVersion,
        descriptor: str,
        derivation_references: list[AssetDerivationReference] | None = None,
    ) -> AssetVersion:
        """Copy one approved catalog image into a target asset as a draft version."""

        if source_version.media_type != AssetMediaType.IMAGE:
            raise AssetWorkbenchConflict("Only image assets can be cloned from the catalog")
        if source_version.local_path is None and source_version.external_url is None:
            raise AssetWorkbenchConflict("Catalog image has no usable media")
        source_uri = (
            f"short-drama://projects/{source_project_id}/assets/{source_asset_id}/"
            f"versions/{source_version.id}"
        )
        with self._lock:
            document = self._require_project(project_id)
            target = self._require_asset(document, target_asset_id)
            number = self._next_version_number(target)
            version_id = f"asset-version-{uuid4().hex[:12]}"
            local_path: str | None = None
            external_url = source_version.external_url
            mime_type = source_version.mime_type
            width = source_version.width
            height = source_version.height
            file_size_bytes = source_version.file_size_bytes
            original_filename: str | None = None
            if source_version.local_path is not None:
                source_path = Path(source_version.local_path).expanduser().resolve()
                if not source_path.is_file():
                    raise AssetWorkbenchConflict(
                        f"Catalog source file is missing: {source_path}"
                    )
                suffix = source_path.suffix.lower() or ".png"
                destination = self._asset_directory(project_id, target_asset_id) / (
                    f"v{number}-{version_id}{suffix}"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
                local_path = str(destination)
                external_url = None
                mime_type = mime_type or mimetypes.guess_type(destination.name)[0]
                width, height = self._image_dimensions(
                    destination,
                    AssetMediaType.IMAGE,
                )
                file_size_bytes = destination.stat().st_size
                original_filename = source_path.name
            now = utc_now()
            version = AssetVersion(
                id=version_id,
                version=number,
                origin_type=OriginType.LIBRARY_IMPORT,
                descriptor=descriptor.strip(),
                media_type=AssetMediaType.IMAGE,
                local_path=local_path,
                external_url=external_url,
                mime_type=mime_type,
                width=width,
                height=height,
                file_size_bytes=file_size_bytes,
                derivation_references=[
                    reference.model_copy(deep=True)
                    for reference in (derivation_references or [])
                ],
                source=AssetSourceRecord(
                    source_type=OriginType.LIBRARY_IMPORT,
                    source_label=f"资产库复用：{source_asset_name}",
                    source_uri=source_uri,
                    original_filename=original_filename,
                    created_at=now,
                ),
                approval_status=ApprovalStatus.DRAFT,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            self._append_version(document, target, version)
            return version

    def import_voice_reference_bytes(
        self,
        project_id: str,
        *,
        asset_id: str | None,
        asset_name: str,
        descriptor: str,
        filename: str,
        data: bytes,
    ) -> AssetVersion:
        """Normalize one uploaded/recorded clip into an approved H3 WAV asset."""

        if not data or len(data) > _MAX_VOICE_UPLOAD_BYTES:
            raise AssetWorkbenchConflict(
                "Voice reference upload must be between 1 byte and 32 MB"
            )
        suffix = Path(filename).suffix.lower()
        if suffix not in _VOICE_UPLOAD_EXTENSIONS:
            raise AssetWorkbenchConflict(
                "Voice reference must be WAV, MP3, M4A, AAC, FLAC, OGG, or WEBM"
            )
        normalized_descriptor = descriptor.strip()
        if not normalized_descriptor or len(normalized_descriptor) > 4000:
            raise AssetWorkbenchConflict(
                "Voice reference descriptor must be between 1 and 4000 characters"
            )

        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id) if asset_id else None
            if asset is not None and asset.kind != AssetKind.REFERENCE:
                raise AssetWorkbenchConflict(
                    "A character voice reference must use a REFERENCE asset"
                )
            resolved_asset_id = asset.id if asset is not None else f"asset-{uuid4().hex[:12]}"
            expected_current_version_id = (
                asset.current_version_id if asset is not None else None
            )
        version_id = f"asset-version-{uuid4().hex[:12]}"
        directory = self._asset_directory(project_id, resolved_asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        source_path = directory / f".{version_id}-upload{suffix}"
        output_path = directory / f".{version_id}-normalized.wav"
        try:
            source_path.write_bytes(data)
            duration = 0.0
            result = subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or "uploaded audio could not be decoded"
                raise AssetWorkbenchConflict(detail)
            metadata = probe_media_with_ffmpeg(output_path)
            duration = float(metadata["duration"])
            if metadata["audio"] != "present":
                raise AssetWorkbenchConflict("Voice reference contains no audio stream")
            if duration < 2.0 or duration > 15.0:
                raise AssetWorkbenchConflict(
                    "MiniMax-H3 voice reference must be between 2 and 15 seconds"
                )
            if output_path.stat().st_size > _MAX_H3_AUDIO_BYTES:
                raise AssetWorkbenchConflict(
                    "Normalized MiniMax-H3 voice reference exceeds 15 MB"
                )
        except AssetWorkbenchConflict:
            output_path.unlink(missing_ok=True)
            raise
        except (MediaGenerationError, OSError, ValueError) as exc:
            output_path.unlink(missing_ok=True)
            raise AssetWorkbenchConflict(str(exc)) from exc
        finally:
            source_path.unlink(missing_ok=True)

        now = utc_now()
        version = AssetVersion(
            id=version_id,
            version=1,
            origin_type=OriginType.UPLOAD,
            descriptor=normalized_descriptor,
            media_type=AssetMediaType.AUDIO,
            local_path=str(output_path),
            mime_type="audio/wav",
            duration_seconds=duration,
            file_size_bytes=output_path.stat().st_size,
            source=AssetSourceRecord(
                source_type=OriginType.UPLOAD,
                source_label="用户上传或录制的角色声线参考",
                original_filename=Path(filename).name,
                created_at=now,
            ),
            approval_status=ApprovalStatus.APPROVED,
            freshness=Freshness.CURRENT,
            created_at=now,
        )
        try:
            with self._lock:
                document = self._require_project(project_id)
                asset = self._require_asset(document, asset_id) if asset_id else None
                if asset is not None and (
                    asset.kind != AssetKind.REFERENCE
                    or asset.current_version_id != expected_current_version_id
                ):
                    raise AssetWorkbenchConflict(
                        "Voice reference asset changed while the upload was being normalized; retry"
                    )
                if asset is None and any(
                    item.id == resolved_asset_id for item in document.assets
                ):
                    raise AssetWorkbenchConflict(
                        "Voice reference asset was created concurrently; retry"
                    )
                version.version = self._next_version_number(asset) if asset is not None else 1
                destination = directory / f"v{version.version}-{version_id}.wav"
                output_path.replace(destination)
                output_path = destination
                version.local_path = str(output_path)
                if asset is None:
                    asset = Asset(
                        id=resolved_asset_id,
                        project_id=project_id,
                        kind=AssetKind.REFERENCE,
                        name=asset_name.strip(),
                        current_version_id=version.id,
                        approved_version_id=version.id,
                        versions=[version],
                    )
                    document.assets.append(asset)
                else:
                    for existing in asset.versions:
                        if existing.approval_status == ApprovalStatus.APPROVED:
                            existing.approval_status = ApprovalStatus.SUPERSEDED
                            existing.freshness = Freshness.STALE
                    asset.versions.append(version)
                    asset.current_version_id = version.id
                    asset.approved_version_id = version.id
                document.project.updated_at = now
                self.repository.save_project(document)
                return version
        except Exception:
            output_path.unlink(missing_ok=True)
            raise

    def review_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetReviewRequest,
    ) -> AssetVersion:
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id)
            version = self._require_asset_version(asset, version_id)
            if request.decision == AssetReviewDecision.APPROVED:
                if version.local_path is None and version.external_url is None:
                    raise AssetWorkbenchConflict(
                        "Only an AssetVersion with usable media can be approved"
                    )
                if request.purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE:
                    if version.media_type != AssetMediaType.IMAGE:
                        raise AssetWorkbenchConflict(
                            "A supplemental reference must be an image AssetVersion"
                        )
                else:
                    previous_canonical_id = asset.approved_version_id
                    if previous_canonical_id is not None and previous_canonical_id != version.id:
                        previous_canonical = self._require_asset_version(
                            asset,
                            previous_canonical_id,
                        )
                        previous_canonical.approval_status = ApprovalStatus.SUPERSEDED
                        previous_canonical.freshness = Freshness.STALE
                version.approval_status = ApprovalStatus.APPROVED
                version.freshness = Freshness.CURRENT
                if request.purpose == AssetReviewPurpose.CANONICAL_MASTER:
                    asset.approved_version_id = version.id
                asset.current_version_id = version.id
            else:
                version.approval_status = ApprovalStatus.REJECTED
                if asset.approved_version_id == version.id:
                    asset.approved_version_id = None
            version.review_notes = request.notes.strip()
            document.project.updated_at = utc_now()
            self.repository.save_project(document)
            return version

    def enqueue_image_task(
        self,
        project_id: str,
        asset_id: str,
        request: ImageTaskCreateRequest,
        *,
        attempt: int = 1,
        retry_of_task_id: str | None = None,
    ) -> ImageGenerationTask:
        if not self.config_store.api_key():
            raise AssetProviderConfigurationError(
                "OpenAI API key is required before enqueueing a paid GPT Image task"
            )
        provider_request = ImageProviderRequest(
            operation=request.operation,
            prompt=request.prompt.strip(),
            size=request.size,
            quality=request.quality,
            background=request.background,
            output_format=request.output_format,
            layout_profile=request.layout_profile,
            input_version_ids=request.input_version_ids,
            reference_inputs=request.reference_inputs,
            primary_input_version_id=request.primary_input_version_id,
            mask_version_id=request.mask_version_id,
            mask_feather_pixels=request.mask_feather_pixels,
        )
        if (
            provider_request.background == ImageBackground.TRANSPARENT
            and provider_request.output_format.value not in {"png", "webp"}
        ):
            raise AssetWorkbenchConflict("Transparent output requires PNG or WebP")
        if (
            provider_request.background == ImageBackground.TRANSPARENT
            and resolve_image_endpoint(self.config_store.base_url()).is_azure
        ):
            raise AssetWorkbenchConflict(
                "This Azure GPT Image deployment does not support transparent background; "
                "choose opaque"
            )
        with self._lock:
            document = self._require_project(project_id)
            asset = self._require_asset(document, asset_id)
            self._validate_asset_output(
                asset,
                provider_request.layout_profile,
                provider_request.size,
            )
            parent_id = (
                self._resolve_parent(asset, request.parent_version_id)
                if request.operation == ImageOperation.EDIT
                or request.parent_version_id is not None
                else None
            )
            self._validate_image_inputs(document, asset, provider_request, parent_id)
            now = utc_now()
            task = ImageGenerationTask(
                id=f"image-task-{uuid4().hex[:12]}",
                project_id=project_id,
                asset_id=asset_id,
                output_version_id=f"asset-version-{uuid4().hex[:12]}",
                output_version_number=self._next_version_number(asset),
                descriptor=request.descriptor.strip(),
                parent_version_id=parent_id,
                operation=request.operation,
                status=ImageTaskStatus.QUEUED,
                request=provider_request,
                attempt=attempt,
                retry_of_task_id=retry_of_task_id,
                created_at=now,
                updated_at=now,
            )
            self.repository.save_image_task(task)
            return task

    def list_image_tasks(
        self,
        project_id: str,
        asset_id: str | None = None,
    ) -> list[ImageGenerationTask]:
        self._require_project(project_id)
        return self.repository.list_image_tasks(project_id, asset_id)

    def advance_image_task(self, task_id: str) -> ImageGenerationTask:
        initial = self._require_image_task(task_id)
        with self._lock:
            task_lock = self._image_advance_locks.setdefault(task_id, threading.Lock())
        with task_lock:
            task = self._require_image_task(task_id)
            if task.status != initial.status:
                return task
            if task.status == ImageTaskStatus.QUEUED:
                task.status = ImageTaskStatus.SUBMITTING
                task.updated_at = utc_now()
                self.repository.save_image_task(task)
                return task
            if task.status != ImageTaskStatus.SUBMITTING:
                raise AssetWorkbenchConflict(
                    f"Image task {task.id} cannot advance from {task.status.value}"
                )
            return self._submit_image_task(task)

    def cancel_image_task(self, task_id: str) -> ImageGenerationTask:
        task = self._require_image_task(task_id)
        if task.status != ImageTaskStatus.QUEUED:
            raise AssetWorkbenchConflict("Only a queued image task can be cancelled")
        task.status = ImageTaskStatus.CANCELLED
        task.error_code = "CANCELLED_BY_USER"
        task.error_message = "尚未提交到 OpenAI 的图像任务已取消。"
        task.retry_disposition = ImageRetryDisposition.NEW_ENQUEUE
        task.finished_at = utc_now()
        task.updated_at = task.finished_at
        self.repository.save_image_task(task)
        return task

    def retry_image_task(self, task_id: str) -> ImageGenerationTask:
        task = self._require_image_task(task_id)
        if task.status == ImageTaskStatus.SUBMIT_UNKNOWN:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} may already have been accepted by the provider; "
                "direct retry is disabled. Verify the provider outcome, then create a "
                "new image task explicitly if another submission is required"
            )
        if task.status == ImageTaskStatus.OUTPUT_MISSING:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} has provider output to recover; use "
                "image.recover_output instead of submitting the same paid request again"
            )
        if task.status == ImageTaskStatus.CANCELLED:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} was cancelled; create a new image.enqueue task "
                "if generation is still required"
            )
        if task.status != ImageTaskStatus.FAILED:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} cannot retry from {task.status.value}"
            )
        if task.retry_disposition != ImageRetryDisposition.RETRY_SAME_REQUEST:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} failed with a non-transient or legacy "
                "classification; identical paid resubmission is disabled. Inspect the "
                "structured error, revise the prompt, references, operation, or request "
                "semantics, then create a new image.enqueue task"
            )
        request = ImageTaskCreateRequest(
            operation=task.operation,
            prompt=task.request.prompt,
            descriptor=task.descriptor,
            parent_version_id=task.parent_version_id,
            input_version_ids=task.request.input_version_ids,
            reference_inputs=task.request.reference_inputs,
            primary_input_version_id=task.request.primary_input_version_id,
            mask_version_id=task.request.mask_version_id,
            mask_feather_pixels=task.request.mask_feather_pixels,
            size=task.request.size,
            quality=task.request.quality,
            background=task.request.background,
            output_format=task.request.output_format,
            layout_profile=task.request.layout_profile,
        )
        return self.enqueue_image_task(
            task.project_id,
            task.asset_id,
            request,
            attempt=task.attempt + 1,
            retry_of_task_id=task.id,
        )

    def resolve_image_not_submitted(
        self,
        task_id: str,
        request: ImageNotSubmittedResolutionRequest,
    ) -> ImageGenerationTask:
        task = self._require_image_task(task_id)
        if task.status != ImageTaskStatus.SUBMIT_UNKNOWN:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} is not waiting for submission reconciliation"
            )
        reason = request.reason.strip()
        if not reason:
            raise AssetWorkbenchConflict("A reconciliation reason is required")
        now = utc_now()
        task.status = ImageTaskStatus.FAILED
        task.error_code = "IMAGE_SUBMISSION_NOT_CREATED"
        task.error_message = reason
        task.retry_disposition = ImageRetryDisposition.RETRY_SAME_REQUEST
        task.finished_at = now
        task.updated_at = now
        self.repository.save_image_task(task)
        return task

    def recover_image_output(self, task_id: str) -> ImageGenerationTask:
        task = self._require_image_task(task_id)
        if task.status != ImageTaskStatus.OUTPUT_MISSING:
            raise AssetWorkbenchConflict("Image task has no recoverable output")
        candidate = Path(task.output_path) if task.output_path else None
        if candidate is None or not candidate.is_file():
            staged = Path(task.staged_path) if task.staged_path else None
            if staged is None or not staged.is_file():
                raise AssetWorkbenchConflict(
                    "No staged image remains; create an explicit retry task"
                )
            width, height = self._validate_output_image(staged)
            try:
                self._validate_requested_output_dimensions(task, width, height)
            except ImageOutputDimensionMismatch as exc:
                return self._mark_task_failed(
                    task,
                    "IMAGE_OUTPUT_DIMENSION_MISMATCH",
                    str(exc),
                )
            candidate = self._final_output_path(task)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(candidate)
            task.output_path = str(candidate)
        else:
            width, height = self._validate_output_image(candidate)
            try:
                self._validate_requested_output_dimensions(task, width, height)
            except ImageOutputDimensionMismatch as exc:
                return self._mark_task_failed(
                    task,
                    "IMAGE_OUTPUT_DIMENSION_MISMATCH",
                    str(exc),
                )
        return self._finalize_image_version(task, candidate, width, height)

    def _submit_image_task(self, task: ImageGenerationTask) -> ImageGenerationTask:
        try:
            inputs = self._resolve_provider_inputs(task)
            provider = self._provider()
        except (AssetWorkbenchError, OSError, ValueError) as exc:
            return self._mark_task_failed(
                task,
                "IMAGE_INPUT_PREPARATION_FAILED",
                str(exc),
            )
        task.submitted_at = utc_now()
        task.updated_at = task.submitted_at
        task.error_code = None
        task.error_message = None
        task.retry_disposition = None
        self.repository.save_image_task(task)
        try:
            result = provider.create(task.request, inputs)
        except ImageProviderDefiniteError as exc:
            return self._mark_task_failed(
                task,
                exc.code,
                str(exc),
                retry_disposition=(
                    ImageRetryDisposition.RETRY_SAME_REQUEST
                    if exc.same_request_retryable
                    else ImageRetryDisposition.REVISE_AND_ENQUEUE
                ),
            )
        except ImageProviderSubmissionUnknown as exc:
            task.status = ImageTaskStatus.SUBMIT_UNKNOWN
            task.error_code = "IMAGE_SUBMIT_UNKNOWN"
            task.error_message = str(exc)
            task.retry_disposition = ImageRetryDisposition.RECONCILE_SUBMISSION
            task.updated_at = utc_now()
            self.repository.save_image_task(task)
            return task
        except (OSError, ValueError) as exc:
            return self._mark_task_failed(
                task,
                "IMAGE_INPUT_PREPARATION_FAILED",
                str(exc),
            )

        task.provider_request_id = result.provider_request_id
        task.revised_prompt = result.revised_prompt
        task.usage = result.usage
        task.cost = self._cost_record(result.usage)
        try:
            output_path, width, height = self._land_provider_output(task, result)
            return self._finalize_image_version(task, output_path, width, height)
        except ImageOutputDimensionMismatch as exc:
            return self._mark_task_failed(
                task,
                "IMAGE_OUTPUT_DIMENSION_MISMATCH",
                str(exc),
            )
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            task.status = ImageTaskStatus.OUTPUT_MISSING
            task.error_code = "IMAGE_OUTPUT_MISSING"
            task.error_message = str(exc)
            task.retry_disposition = ImageRetryDisposition.RECOVER_OUTPUT
            task.updated_at = utc_now()
            self.repository.save_image_task(task)
            return task

    def _land_provider_output(
        self,
        task: ImageGenerationTask,
        result: ImageProviderResult,
    ) -> tuple[Path, int, int]:
        try:
            payload = base64.b64decode(result.image_base64, validate=True)
        except ValueError as exc:
            raise ValueError("Provider returned invalid image base64") from exc
        suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }.get(result.mime_type)
        if suffix is None:
            raise ValueError(f"Unsupported image MIME type: {result.mime_type}")
        staging_dir = self.workspace_root / task.project_id / "assets" / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / f"{task.id}{suffix}.part"
        staged.write_bytes(payload)
        task.staged_path = str(staged)
        task.output_mime_type = result.mime_type
        task.updated_at = utc_now()
        self.repository.save_image_task(task)
        width, height = self._validate_output_image(staged)
        self._validate_requested_output_dimensions(task, width, height)
        if task.request.mask_version_id is not None:
            self._apply_masked_composite(task, staged, width, height)
            width, height = self._validate_output_image(staged)
            self._validate_requested_output_dimensions(task, width, height)
        output_path = self._final_output_path(task, suffix)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(output_path)
        task.output_path = str(output_path)
        task.updated_at = utc_now()
        self.repository.save_image_task(task)
        return output_path, width, height

    @staticmethod
    def _validate_requested_output_dimensions(
        task: ImageGenerationTask,
        width: int,
        height: int,
    ) -> None:
        raw_width, raw_height = task.request.size.split("x", maxsplit=1)
        requested = (int(raw_width), int(raw_height))
        actual = (width, height)
        if actual != requested:
            raise ImageOutputDimensionMismatch(
                "Provider output dimensions "
                f"{width}x{height} do not match requested {task.request.size}"
            )

    def _apply_masked_composite(
        self,
        task: ImageGenerationTask,
        generated_path: Path,
        width: int,
        height: int,
    ) -> None:
        document = self._require_project(task.project_id)
        primary_id = (
            task.request.primary_input_version_id
            or task.request.ordered_input_version_ids()[0]
        )
        _primary_asset, primary = self._find_version(document, primary_id)
        _mask_asset, mask = self._find_version(document, task.request.mask_version_id or "")
        if primary.local_path is None or mask.local_path is None:
            raise ValueError("Masked composite inputs are not locally available")
        with Image.open(primary.local_path) as source_image:
            source = source_image.convert("RGBA")
        with Image.open(mask.local_path) as mask_image:
            mask_alpha = mask_image.convert("RGBA").getchannel("A")
        with Image.open(generated_path) as generated_image:
            generated = generated_image.convert("RGBA")
        if source.size != (width, height) or mask_alpha.size != (width, height):
            raise ValueError("Masked composite dimensions do not match provider output")

        editable = Image.eval(mask_alpha, lambda value: 255 - value)
        if task.request.mask_feather_pixels:
            softened = editable.filter(
                ImageFilter.GaussianBlur(radius=task.request.mask_feather_pixels)
            )
            editable = ImageChops.multiply(editable, softened)
        composite = Image.composite(generated, source, editable)
        output_format = task.request.output_format.value
        if output_format == "jpeg":
            composite.convert("RGB").save(generated_path, format="JPEG", quality=95)
        elif output_format == "webp":
            composite.save(generated_path, format="WEBP", quality=95)
        else:
            composite.save(generated_path, format="PNG")

    def _finalize_image_version(
        self,
        task: ImageGenerationTask,
        output_path: Path,
        width: int,
        height: int,
    ) -> ImageGenerationTask:
        with self._lock:
            document = self._require_project(task.project_id)
            asset = self._require_asset(document, task.asset_id)
            existing = next(
                (item for item in asset.versions if item.id == task.output_version_id),
                None,
            )
            now = utc_now()
            if existing is None:
                # New tasks reserve unique numbers at enqueue. Older persisted tasks
                # may share a reservation; repair only this not-yet-registered output.
                if any(item.version == task.output_version_number for item in asset.versions):
                    task.output_version_number = self._next_version_number(asset)
                    destination = self._final_output_path(task, output_path.suffix)
                    output_path.replace(destination)
                    output_path = destination
                    task.output_path = str(output_path)
                    self.repository.save_image_task(task)
                origin = (
                    OriginType.AI_EDITED
                    if task.operation == ImageOperation.EDIT
                    else OriginType.AI_GENERATED
                )
                source_label = {
                    ImageOperation.GENERATE: "GPT Image 2 生成",
                    ImageOperation.REFERENCE_GENERATE: "GPT Image 2 参考生成",
                    ImageOperation.EDIT: "GPT Image 2 编辑",
                }[task.operation]
                version = AssetVersion(
                    id=task.output_version_id,
                    version=task.output_version_number,
                    origin_type=origin,
                    descriptor=task.descriptor,
                    media_type=AssetMediaType.IMAGE,
                    local_path=str(output_path),
                    mime_type=task.output_mime_type,
                    width=width,
                    height=height,
                    file_size_bytes=output_path.stat().st_size,
                    parent_version_id=task.parent_version_id,
                    image_task_id=task.id,
                    derivation_references=[
                        AssetDerivationReference(
                            asset_version_id=reference.asset_version_id,
                            source_project_id=task.project_id,
                            reference_role=reference.reference_role,
                            reference_authority=reference.reference_authority,
                            purpose=reference.purpose,
                            subject_alias=reference.subject_alias,
                        )
                        for reference in task.request.ordered_reference_inputs()
                    ],
                    source=AssetSourceRecord(
                        source_type=origin,
                        source_label=source_label,
                        provider=task.provider,
                        model=task.model,
                        prompt=task.request.prompt,
                        created_at=now,
                    ),
                    approval_status=ApprovalStatus.DRAFT,
                    freshness=Freshness.CURRENT,
                    created_at=now,
                )
                asset.versions.append(version)
            else:
                task.output_version_number = existing.version
                existing.local_path = str(output_path)
                existing.width = width
                existing.height = height
                existing.file_size_bytes = output_path.stat().st_size
            asset.current_version_id = task.output_version_id
            document.project.updated_at = now
            self.repository.save_project(document)
            task.status = ImageTaskStatus.SUCCEEDED
            task.error_code = None
            task.error_message = None
            task.retry_disposition = None
            task.finished_at = now
            task.updated_at = now
            self.repository.save_image_task(task)
            return task

    def _resolve_provider_inputs(
        self,
        task: ImageGenerationTask,
    ) -> list[ImageProviderInput]:
        document = self._require_project(task.project_id)
        result: list[ImageProviderInput] = []
        ordered_references = task.request.ordered_reference_inputs()
        primary_id = task.request.primary_input_version_id
        if primary_id is not None and not task.request.reference_inputs:
            ordered_references = [
                next(
                    item
                    for item in ordered_references
                    if item.asset_version_id == primary_id
                ),
                *(
                    item
                    for item in ordered_references
                    if item.asset_version_id != primary_id
                ),
            ]
        if task.request.mask_version_id is not None:
            ordered_references.append(
                ImageReferenceInput(
                    asset_version_id=task.request.mask_version_id,
                    purpose="Edit mask",
                )
            )
        for reference in ordered_references:
            version_id = reference.asset_version_id
            _asset, version = self._find_version(document, version_id)
            # A mask and the final local composite use saved local pixels. Upload
            # those same pixels rather than a potentially different remote image.
            if version.external_url is not None and task.request.mask_version_id is None:
                image_url = version.external_url
            elif version.local_path is not None:
                path = Path(version.local_path)
                if not path.is_file():
                    raise AssetWorkbenchConflict(
                        f"Input AssetVersion file is missing: {version.id}"
                    )
                if path.stat().st_size > _MAX_EDIT_IMAGE_BYTES:
                    raise AssetWorkbenchConflict(
                        f"Edit input exceeds 20 MB: {path.name}"
                    )
                mime_type = version.mime_type or mimetypes.guess_type(path.name)[0]
                if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise AssetWorkbenchConflict(
                        f"Unsupported GPT Image input type: {mime_type or path.suffix}"
                    )
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                image_url = f"data:{mime_type};base64,{encoded}"
            else:
                raise AssetWorkbenchConflict(
                    f"Input AssetVersion has no usable media: {version.id}"
                )
            result.append(
                ImageProviderInput(
                    asset_version_id=version.id,
                    image_url=image_url,
                    purpose=reference.purpose,
                    subject_alias=reference.subject_alias,
                    reference_role=reference.reference_role,
                    reference_authority=reference.reference_authority,
                    text_plate=version.text_plate.model_copy(deep=True) if version.text_plate is not None else None,
                )
            )
        return result

    def _validate_image_inputs(
        self,
        document: ProjectDocument,
        target_asset: Asset,
        request: ImageProviderRequest,
        parent_version_id: str | None,
    ) -> None:
        input_version_ids = request.ordered_input_version_ids()
        if request.operation == ImageOperation.GENERATE:
            if input_version_ids:
                raise AssetWorkbenchConflict("Generate does not accept input AssetVersions")
            if parent_version_id is not None:
                raise AssetWorkbenchConflict("Use EDIT to derive from an existing AssetVersion")
            return
        if not input_version_ids:
            raise AssetWorkbenchConflict(
                f"{request.operation.value} requires at least one input AssetVersion"
            )
        for version_id in input_version_ids:
            _asset, version = self._find_version(document, version_id)
            if version.media_type != AssetMediaType.IMAGE:
                raise AssetWorkbenchConflict("GPT Image reference inputs must be images")
            if version.local_path is None and version.external_url is None:
                raise AssetWorkbenchConflict("GPT Image reference input has no usable media")
        if request.operation == ImageOperation.REFERENCE_GENERATE:
            if parent_version_id is not None:
                raise AssetWorkbenchConflict(
                    "REFERENCE_GENERATE must not declare a target-asset parent"
                )
            if request.mask_version_id is not None:
                raise AssetWorkbenchConflict("REFERENCE_GENERATE does not accept an edit mask")
            return
        if request.mask_version_id is not None:
            _mask_asset, mask = self._find_version(document, request.mask_version_id)
            if mask.media_type != AssetMediaType.IMAGE or mask.local_path is None:
                raise AssetWorkbenchConflict(
                    "Masked edits require a locally cached PNG mask AssetVersion"
                )
            mask_path = Path(mask.local_path)
            if not mask_path.is_file():
                raise AssetWorkbenchConflict("Edit mask file is missing")
            mask_mime = mask.mime_type or mimetypes.guess_type(mask_path.name)[0]
            if mask_mime != "image/png":
                raise AssetWorkbenchConflict("Edit mask must be a PNG image")
            if mask_path.stat().st_size >= _MAX_EDIT_MASK_BYTES:
                raise AssetWorkbenchConflict("Edit mask must be smaller than 4 MB")
            primary_id = request.primary_input_version_id or input_version_ids[0]
            _primary_asset, primary = self._find_version(document, primary_id)
            if primary.local_path is None:
                raise AssetWorkbenchConflict(
                    "Masked edits require the primary input to be locally cached"
                )
            if mask.width != primary.width or mask.height != primary.height:
                raise AssetWorkbenchConflict(
                    "Edit mask dimensions must match the primary input image"
                )
            for version_id in input_version_ids:
                _input_asset, input_version = self._find_version(document, version_id)
                if input_version.local_path is None:
                    raise AssetWorkbenchConflict(
                        "Masked edits require every visual input to be locally cached"
                    )
        if parent_version_id is None:
            raise AssetWorkbenchConflict("Edit requires a parent version from the target asset")
        self._require_asset_version(target_asset, parent_version_id)

    def _append_version(
        self,
        document: ProjectDocument,
        asset: Asset,
        version: AssetVersion,
    ) -> None:
        asset.versions.append(version)
        asset.current_version_id = version.id
        document.project.updated_at = utc_now()
        self.repository.save_project(document)

    @staticmethod
    def _validate_asset_strategy(
        kind: AssetKind,
        strategy: AssetReferenceStrategy | None,
    ) -> None:
        if strategy in {None, AssetReferenceStrategy.LIGHTWEIGHT}:
            return
        if (
            strategy == AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS
            and kind != AssetKind.CHARACTER
        ):
            raise AssetWorkbenchConflict(
                "CHARACTER_STANDARD_VIEWS can only be used for a character asset"
            )
        if (
            strategy == AssetReferenceStrategy.SCENE_COVERAGE
            and kind != AssetKind.LOCATION
        ):
            raise AssetWorkbenchConflict(
                "SCENE_COVERAGE can only be used for a location asset"
            )

    @staticmethod
    def _validate_asset_layout(
        kind: AssetKind,
        layout_profile: AssetLayoutProfile | None,
        presentation_mode: AssetPresentationMode | None,
    ) -> None:
        if layout_profile is None:
            return
        if layout_profile == AssetLayoutProfile.LIGHTWEIGHT_SINGLE:
            return
        if layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2,
            AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1,
        } and kind != AssetKind.CHARACTER:
            raise AssetWorkbenchConflict(
                f"{layout_profile.value} can only be used for a character asset"
            )
        if (
            layout_profile == AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2
            and kind != AssetKind.LOCATION
        ):
            raise AssetWorkbenchConflict(
                "SCENE_FOUR_DIRECTION_2X2 can only be used for a location asset"
            )
        if layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE:
            if kind != AssetKind.PROP:
                raise AssetWorkbenchConflict(
                    "PROP_MASTER_SINGLE can only be used for a prop asset"
                )
            if presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE:
                raise AssetWorkbenchConflict(
                    "PROP_MASTER_SINGLE is incompatible with SCREEN_CONTENT_PLATE"
                )

    @staticmethod
    def _validate_asset_output(
        asset: Asset,
        layout_profile: AssetLayoutProfile,
        size: str,
    ) -> None:
        presentation_mode = (
            None if asset.brief is None else asset.brief.presentation_mode
        )
        AssetWorkbench._validate_asset_layout(
            asset.kind,
            layout_profile,
            presentation_mode,
        )
        AssetWorkbench._validate_layout_output_size(
            layout_profile,
            size,
        )

    @staticmethod
    def _validate_layout_output_size(
        layout_profile: AssetLayoutProfile | None,
        size: str,
    ) -> None:
        raw_width, raw_height = size.split("x", maxsplit=1)
        width, height = int(raw_width), int(raw_height)
        if layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
        } and width * 9 != height * 16:
            assert layout_profile is not None
            raise AssetWorkbenchConflict(
                f"{layout_profile.value} image tasks require a 16:9 size"
            )
        if (
            layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2
            and width * 2 != height * 3
        ):
            raise AssetWorkbenchConflict(
                "CHARACTER_SIX_VIEW_3X2 image tasks require a 3:2 size"
            )
        if (
            layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE
            and width != height
        ):
            raise AssetWorkbenchConflict(
                "PROP_MASTER_SINGLE image tasks require a square size"
            )

    @staticmethod
    def _validate_standard_views(
        strategy: AssetReferenceStrategy,
        views: list[AssetStandardView],
    ) -> None:
        allowed: frozenset[AssetStandardView] | None = None
        if strategy == AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS:
            allowed = _CHARACTER_STANDARD_VIEWS
        elif strategy == AssetReferenceStrategy.SCENE_COVERAGE:
            allowed = _SCENE_COVERAGE_VIEWS
        if allowed is None:
            return
        incompatible = [view.value for view in views if view not in allowed]
        if incompatible:
            raise AssetWorkbenchConflict(
                f"{strategy.value} contains incompatible views: "
                + ", ".join(incompatible)
            )

    def _validate_sheet_views(self, request: AssetReferenceSheetComposeRequest) -> None:
        self._validate_standard_views(
            request.strategy,
            [cell.view for cell in request.cells],
        )

    @staticmethod
    def _reference_sheet_columns(request: AssetReferenceSheetComposeRequest) -> int:
        if request.layout_profile is None:
            return request.columns
        columns_by_profile: dict[AssetLayoutProfile, int] = {
            AssetLayoutProfile.LIGHTWEIGHT_SINGLE: 1,
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: 2,
            AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: 3,
            AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1: 4,
            AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2: 2,
            AssetLayoutProfile.PROP_MASTER_SINGLE: 1,
        }
        return columns_by_profile.get(request.layout_profile, request.columns)

    @staticmethod
    def _render_reference_sheet(
        source_rows: list[tuple[Path, AssetReferenceSheetCell]],
        *,
        columns: int,
        layout_profile: AssetLayoutProfile | None,
        show_labels: bool,
        background_color: str,
        target_size: str | None,
        destination: Path,
    ) -> tuple[int, int]:
        rows = (len(source_rows) + columns - 1) // columns
        label_height = _REFERENCE_SHEET_LABEL_HEIGHT if show_labels else 0
        if target_size is not None:
            raw_width, raw_height = target_size.split("x", maxsplit=1)
            width, height = int(raw_width), int(raw_height)
        elif layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
        }:
            width, height = _REFERENCE_SHEET_WIDE_SIZE
        elif layout_profile == AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1:
            width, height = (2048, 1152)
        elif layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2:
            width, height = (1536, 1024)
        elif layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE:
            width, height = (768, 768)
        else:
            cell_height = _REFERENCE_SHEET_IMAGE_HEIGHT + label_height
            width = (
                columns * _REFERENCE_SHEET_CELL_WIDTH
                + (columns + 1) * _REFERENCE_SHEET_PADDING
            )
            height = rows * cell_height + (rows + 1) * _REFERENCE_SHEET_PADDING
        sheet = Image.new("RGB", (width, height), background_color)
        draw = ImageDraw.Draw(sheet)
        try:
            regions = AssetWorkbench._reference_sheet_regions(
                count=len(source_rows),
                columns=columns,
                width=width,
                height=height,
                label_height=label_height,
                layout_profile=layout_profile,
            )
            for (source_path, cell), region in zip(
                source_rows,
                regions,
                strict=True,
            ):
                left, top, right, bottom = region
                image_bottom = bottom - label_height
                image_width = right - left
                image_height = image_bottom - top
                if (
                    image_width < _REFERENCE_SHEET_MIN_CELL_IMAGE_DIMENSION
                    or image_height < _REFERENCE_SHEET_MIN_CELL_IMAGE_DIMENSION
                ):
                    raise AssetWorkbenchConflict(
                        "Reference sheet target_size leaves a cell image region smaller "
                        f"than {_REFERENCE_SHEET_MIN_CELL_IMAGE_DIMENSION} pixels"
                    )
                with Image.open(source_path) as source_image:
                    normalized = ImageOps.exif_transpose(source_image).convert("RGB")
                    fitted = ImageOps.contain(
                        normalized,
                        (image_width, image_height),
                        Image.Resampling.LANCZOS,
                    )
                image_left = left + (right - left - fitted.width) // 2
                image_top = top + (image_bottom - top - fitted.height) // 2
                sheet.paste(fitted, (image_left, image_top))
                draw.rectangle(
                    (
                        left,
                        top,
                        right - 1,
                        image_bottom - 1,
                    ),
                    outline="#C7CBD1",
                    width=2,
                )
                if show_labels:
                    draw.text(
                        (left + 8, image_bottom + 12),
                        cell.label,
                        fill="#20242A",
                    )
            sheet.save(destination, format="PNG", optimize=False, compress_level=9)
        except (OSError, UnidentifiedImageError) as exc:
            destination.unlink(missing_ok=True)
            raise AssetWorkbenchConflict(
                "Reference sheet input is not a readable local image"
            ) from exc
        finally:
            sheet.close()
        return width, height

    @staticmethod
    def _reference_sheet_regions(
        *,
        count: int,
        columns: int,
        width: int,
        height: int,
        label_height: int,
        layout_profile: AssetLayoutProfile | None,
    ) -> list[tuple[int, int, int, int]]:
        padding = _REFERENCE_SHEET_PADDING
        if layout_profile == AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9:
            split = width // 2
            regions = [(padding, padding, split - padding, height - padding)]
            right_left = split + padding
            available = width - right_left - padding
            cell_width = (available - 2 * padding) // 3
            for index in range(3):
                left = right_left + index * (cell_width + padding)
                right = width - padding if index == 2 else left + cell_width
                regions.append((left, padding, right, height - padding))
            return regions

        if layout_profile == AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2:
            columns = 2
        elif layout_profile == AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1:
            columns = 4
        elif layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2:
            columns = 3
        elif layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE:
            columns = 1

        rows = (count + columns - 1) // columns
        available_width = width - (columns + 1) * padding
        available_height = height - (rows + 1) * padding
        cell_width = available_width // columns
        cell_height = available_height // rows
        regions: list[tuple[int, int, int, int]] = []
        for index in range(count):
            row, column = divmod(index, columns)
            left = padding + column * (cell_width + padding)
            top = padding + row * (cell_height + padding)
            right = width - padding if column == columns - 1 else left + cell_width
            bottom = height - padding if row == rows - 1 else top + cell_height
            if bottom - top <= label_height:
                raise AssetWorkbenchConflict(
                    "Reference sheet target_size is too small for its labels"
                )
            regions.append((left, top, right, bottom))
        return regions

    def resolve_text_plate_content(
        self, document: ProjectDocument, request: AssetTextPlateComposeRequest, *, asset_id: str | None = None,
    ) -> tuple[AssetTextPlate, AssetVersion | None]:
        """Resolve saved wording/layout before validation; no drawing or provider IO."""
        if request.base_version_id is None:
            target = self._require_asset(document, asset_id) if asset_id is not None else None
            purpose = ("PHYSICAL_SURFACE_CONTENT" if target is not None and target.brief is not None
                and target.brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER else "SCREEN_CONTENT")
            return AssetTextPlate.model_validate({
                **request.model_dump(exclude={"base_version_id", "append_text_blocks", "parent_version_id", "descriptor"}),
                "purpose": purpose, "font_family": "resolved during rendering",
            }), None
        try:
            _owner, source = self._find_version(document, request.base_version_id)
        except AssetWorkbenchNotFound as exc:
            raise AssetWorkbenchConflict(f"/request/base_version_id={request.base_version_id}: no such version in this project") from exc
        if (source.text_plate is None or source.media_type != AssetMediaType.IMAGE
                or source.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
                or source.freshness != Freshness.CURRENT):
            raise AssetWorkbenchConflict(f"/request/base_version_id={source.id} must be an existing usable TextPlate image, including a usable DRAFT; choose a current non-rejected plate, not a photograph")
        self._text_plate_file_state(source)
        plate = source.text_plate.model_copy(deep=True)
        if asset_id is not None:
            target = self._require_asset(document, asset_id)
            plate.purpose = ("PHYSICAL_SURFACE_CONTENT" if target.brief is not None
                and target.brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER else "SCREEN_CONTENT")
        if len(plate.append_text_blocks) + len(request.append_text_blocks) > 64:
            raise AssetWorkbenchConflict("/request/append_text_blocks: this plate would exceed 64 accumulated additions")
        plate.append_text_blocks.extend(TextPlateAppendedTextBlock(
            **block.model_dump(), font_family="resolved during rendering",
        ) for block in request.append_text_blocks)
        plate.content_state = request.content_state.model_copy(deep=True) if request.content_state is not None else None
        plate.layout_description = request.layout_description
        return plate, source.model_copy(deep=True)

    @staticmethod
    def _text_plate_file_state(version: AssetVersion) -> tuple[str, int, int]:
        if version.local_path is None:
            raise AssetWorkbenchConflict(f"/request/base_version_id={version.id} has no local PNG; select an existing local TextPlate")
        path = Path(version.local_path)
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size <= 0:
                raise OSError("not a non-empty file")
        except OSError as exc:
            raise AssetWorkbenchConflict(f"/request/base_version_id={version.id}: local PNG is missing or empty at {path}") from exc
        return str(path), stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _append_text_plate(
        base: AssetVersion, plate: AssetTextPlate, request: AssetTextPlateComposeRequest,
        *, font_source: str, destination: Path,
    ) -> tuple[int, int]:
        assert base.local_path is not None
        try:
            with Image.open(base.local_path) as source:
                if source.format != "PNG":
                    raise AssetWorkbenchConflict(f"/request/base_version_id={base.id}: expected an actual PNG, received {source.format}")
                source.load()
                expected = tuple(int(part) for part in plate.size.split("x"))
                if source.size != expected or (base.width is not None and base.width != source.width) or (base.height is not None and base.height != source.height):
                    raise AssetWorkbenchConflict(f"/request/base_version_id={base.id}: actual PNG size {source.size} does not match saved TextPlate size {plate.size}; re-read or repair this source")
                rgb = source.mode == "RGB"
                with source.convert("RGBA") as original, Image.new("RGBA", source.size, (0, 0, 0, 0)) as additions:
                    drawing = AssetTextPlateComposeRequest(
                        size=plate.size, text_blocks=request.append_text_blocks,
                        foreground_color=plate.foreground_color, stroke_width=plate.stroke_width, stroke_color=plate.stroke_color,
                        layout_description=request.layout_description, descriptor=request.descriptor,
                    )
                    AssetWorkbench._draw_text_plate_elements(additions, drawing, font_source, text_field="append_text_blocks")
                    with Image.alpha_composite(original, additions) as composed:
                        if rgb:
                            with composed.convert("RGB") as result:
                                result.save(destination, format="PNG")
                        else:
                            composed.save(destination, format="PNG")
                return source.size
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetWorkbenchConflict(f"/request/base_version_id={base.id}: cannot decode the actual PNG: {exc}") from exc

    @staticmethod
    def validate_text_plate_contract(
        asset_kind: AssetKind,
        brief: CoreAssetBrief | None,
        request: AssetTextPlateComposeRequest | AssetTextPlate,
        *,
        asset_id: str,
    ) -> None:
        if brief is None or not (
            brief.presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE
            or asset_kind == AssetKind.PROP and brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER
        ):
            raise AssetWorkbenchConflict(
                "Text plates require SCREEN_CONTENT_PLATE or a PHYSICAL_MASTER PROP with required_visible_text; "
                "a physical prop's plate is a surface-content candidate, not a replacement photograph"
            )
        if not brief.required_visible_text:
            raise AssetWorkbenchConflict(
                "Text plate asset brief must declare required_visible_text; preserve the physical carrier's presentation_mode"
            )
        # Fixed asset copy remains authoritative. A state can declare additions
        # without changing earlier candidates or the physical carrier's brief.
        received = request.required_visible_text()
        state = request.content_state
        expected = brief.required_visible_text if state is None else state.required_visible_text
        expected_wording = AssetWorkbench.text_plate_wording(expected)
        if state is not None:
            cursor = 0
            state_entries = [AssetWorkbench.text_plate_wording([entry]) for entry in expected]
            for index, fixed_text in enumerate(brief.required_visible_text):
                fixed_wording = AssetWorkbench.text_plate_wording([fixed_text])
                try:
                    start = state_entries.index(fixed_wording, cursor)
                except ValueError:
                    raise AssetWorkbenchConflict(
                        f"asset_id={asset_id}: /request/content_state/required_visible_text must preserve "
                        f"brief.required_visible_text/{index}={fixed_text!r} as an unchanged entry in order. "
                        "Declare only this candidate's state additions around the fixed text; do not rewrite "
                        "the shared brief or remove fixed wording to create a later state."
                    ) from None
                cursor = start + 1
        if AssetWorkbench.text_plate_wording(received) != expected_wording:
            expected_source = "brief.required_visible_text" if state is None else "/request/content_state/required_visible_text"
            raise AssetWorkbenchConflict(
                f"asset_id={asset_id}: /request/text_blocks or /request/title + /request/exact_lines "
                f"must exactly match {expected_source} in ordered words and numbers. "
                "Blank rows, trailing label colons and trailing decorative rules may differ; internal punctuation, names, amounts and signs may not. "
                f"Expected {str(expected)[:700]}; received {str(received)[:700]}. "
                "Correct this request. For a narrative state with additional visible text, explicitly provide "
                "content_state.name and its complete required_visible_text, preserving fixed brief wording. "
                "Keep the shared brief and earlier candidates unchanged."
            )

    @staticmethod
    def text_plate_wording(lines: Sequence[str]) -> str:
        """Tolerate only line-edge layout decoration; body punctuation is content."""
        normalized: list[str] = []
        for line in lines:
            value = re.sub(r"[_＿─━]{2,}$", "", line.strip()).rstrip()
            if value.endswith((":", "：")):
                value = value[:-1].rstrip()
            normalized.append(value)
        return "".join(normalized)

    @staticmethod
    def _draw_text_plate_elements(sheet: Image.Image, request: AssetTextPlateComposeRequest, font_source: str, *, text_field: str = "text_blocks") -> None:
        draw = ImageDraw.Draw(sheet)
        width, height = sheet.size
        for index, block in enumerate(request.text_blocks):
            x, y, box_width, box_height = block.content_box
            left, top = round(x * width), round(y * height)
            right, bottom = round((x + box_width) * width), round((y + box_height) * height)
            font = ImageFont.truetype(font_source, block.font_size)
            bounds = draw.textbbox((0, 0), block.text, font=font, stroke_width=request.stroke_width)
            ink_width, ink_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            if ink_width > right - left or ink_height > bottom - top:
                raise AssetWorkbenchConflict(
                    f"/request/{text_field}/{index}: text {block.text!r} needs {ink_width}x{ink_height} pixels "
                    f"at font_size={block.font_size}; content_box provides {right-left}x{bottom-top}. "
                    "Enlarge this content_box, reduce this font_size, or split the same words into ordered blocks. "
                    "No text was clipped and no asset version was saved."
                )
            free_x, free_y = right - left - ink_width, bottom - top - ink_height
            offset_x = 0 if block.text_align == "left" else free_x if block.text_align == "right" else free_x // 2
            offset_y = 0 if block.vertical_align == "top" else free_y if block.vertical_align == "bottom" else free_y // 2
            draw.text((left + offset_x - bounds[0], top + offset_y - bounds[1]), block.text, font=font,
                fill=block.color or request.foreground_color, stroke_width=request.stroke_width,
                stroke_fill=request.stroke_color)
        for index, line in enumerate(request.lines):
            start = (round(line.start[0] * (width - 1)), round(line.start[1] * (height - 1)))
            end = (round(line.end[0] * (width - 1)), round(line.end[1] * (height - 1)))
            margin = line.width // 2
            if min(start[0], end[0]) < margin or min(start[1], end[1]) < margin or max(start[0], end[0]) >= width - margin or max(start[1], end[1]) >= height - margin:
                raise AssetWorkbenchConflict(
                    f"/request/lines/{index}: stroke width={line.width} extends beyond the canvas; move the endpoints inward or reduce width."
                )
            draw.line([start, end], fill=line.color or request.foreground_color, width=line.width)

    @staticmethod
    def _text_plate_font(visible_text: list[str]) -> tuple[str, str]:
        for path, family in _TEXT_PLATE_FONT_CANDIDATES:
            if path.is_file():
                return str(path), family
        if any(any(ord(character) > 127 for character in line) for line in visible_text):
            raise AssetWorkbenchConflict(
                "No local Unicode font is available for this text plate"
            )
        try:
            ImageFont.truetype("DejaVuSans.ttf", 16)
        except OSError as exc:
            raise AssetWorkbenchConflict(
                "No local font is available for this text plate"
            ) from exc
        return "DejaVuSans.ttf", "DejaVu Sans"

    @staticmethod
    def _render_text_plate(
        request: AssetTextPlateComposeRequest,
        *,
        font_source: str,
        destination: Path,
    ) -> tuple[int, int]:
        raw_width, raw_height = request.size.split("x", maxsplit=1)
        width, height = int(raw_width), int(raw_height)
        x, y, box_width, box_height = request.content_box
        left, top = round(x * width), round(y * height)
        region_width, region_height = round(box_width * width), round(box_height * height)
        minimum_margin = 32 if request.content_box == (0, 0, 1, 1) else 8
        horizontal_margin = max(minimum_margin, region_width // 12) + request.stroke_width
        vertical_margin = max(minimum_margin, region_height // 12) + request.stroke_width
        available_width = region_width - horizontal_margin * 2
        available_height = region_height - vertical_margin * 2
        rgba = ImageColor.getcolor(request.background_color, "RGBA")
        assert isinstance(rgba, tuple) and len(rgba) == 4
        sheet = Image.new("RGBA" if rgba[3] < 255 else "RGB", (width, height), request.background_color)
        draw = ImageDraw.Draw(sheet)
        if request.text_blocks:
            try:
                AssetWorkbench._draw_text_plate_elements(sheet, request, font_source)
                sheet.save(destination, format="PNG", optimize=False, compress_level=9)
            finally:
                sheet.close()
            return width, height
        selected: tuple[
            ImageFont.FreeTypeFont,
            ImageFont.FreeTypeFont | None,
            list[int],
            int,
            int,
        ] | None = None
        if request.content_box == (0, 0, 1, 1):
            starting_size = min(96, max(18, region_height // max(5, len(request.exact_lines) + 3)))
        else:
            # A content box already describes the intended text area. Do not
            # reserve five poster rows inside a single caption or price label.
            row_count = max(1, len(request.exact_lines) + (1 if request.title else 0))
            starting_size = max(12, min(available_width, available_height // row_count))
        try:
            for body_size in range(starting_size, 11, -1):
                body_font = ImageFont.truetype(font_source, body_size)
                line_heights: list[int] = []
                if any(
                    (bounds := draw.textbbox((0, 0), line, font=body_font))[2]
                    - bounds[0]
                    > available_width
                    for line in request.exact_lines
                ):
                    continue
                for line in request.exact_lines:
                    bounds = draw.textbbox((0, 0), line, font=body_font)
                    line_heights.append(body_size if not line else max(1, int(bounds[3] - bounds[1])))
                line_spacing = max(8, body_size // 2)
                body_height = sum(line_heights) + line_spacing * max(
                    0, len(line_heights) - 1
                )
                title_font: ImageFont.FreeTypeFont | None = None
                title_height = 0
                title_gap = 0
                if request.title is not None:
                    title_size = min(128, max(body_size + 4, body_size * 4 // 3))
                    while title_size >= body_size:
                        candidate = ImageFont.truetype(font_source, title_size)
                        bounds = draw.textbbox((0, 0), request.title, font=candidate)
                        if bounds[2] - bounds[0] <= available_width:
                            title_font = candidate
                            title_height = max(1, int(bounds[3] - bounds[1]))
                            break
                        title_size -= 1
                    if title_font is None:
                        continue
                    title_gap = max(16, body_size) if request.exact_lines else 0
                total_height = title_height + title_gap + body_height
                if total_height <= available_height:
                    selected = (
                        body_font,
                        title_font,
                        line_heights,
                        line_spacing,
                        total_height,
                    )
                    break
            if selected is None:
                raise AssetWorkbenchConflict(
                    "Exact text does not fit the requested plate size; enlarge or split it"
                )
            body_font, title_font, line_heights, line_spacing, total_height = selected
            free_height = available_height - total_height
            offset_y = 0 if request.vertical_align == "top" else free_height if request.vertical_align == "bottom" else free_height // 2
            cursor_y = top + vertical_margin + offset_y
            if request.title is not None:
                assert title_font is not None
                title_bounds = draw.textbbox((0, 0), request.title, font=title_font)
                title_align = "center" if request.content_box == (0, 0, 1, 1) else request.text_align
                title_x = (left + horizontal_margin if title_align == "left"
                           else left + region_width - horizontal_margin if title_align == "right"
                           else left + region_width // 2)
                draw.text(
                    (title_x, cursor_y),
                    request.title,
                    font=title_font,
                    fill=request.foreground_color,
                    anchor={"left": "lt", "center": "mt", "right": "rt"}[title_align],
                    stroke_width=request.stroke_width,
                    stroke_fill=request.stroke_color,
                )
                cursor_y += title_bounds[3] - title_bounds[1] + (max(16, body_font.size) if request.exact_lines else 0)
            for index, (line, line_height) in enumerate(
                zip(request.exact_lines, line_heights, strict=True)
            ):
                text_x = (left + horizontal_margin if request.text_align == "left"
                          else left + region_width - horizontal_margin if request.text_align == "right"
                          else left + region_width // 2)
                draw.text(
                    (text_x, cursor_y),
                    line,
                    font=body_font,
                    fill=request.foreground_color,
                    anchor={"left": "lt", "center": "mt", "right": "rt"}[request.text_align],
                    stroke_width=request.stroke_width,
                    stroke_fill=request.stroke_color,
                )
                cursor_y += line_height
                if index != len(request.exact_lines) - 1:
                    cursor_y += line_spacing
            AssetWorkbench._draw_text_plate_elements(sheet, request, font_source)
            sheet.save(destination, format="PNG", optimize=False, compress_level=9)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise AssetWorkbenchConflict(
                "Text plate font could not render the requested content"
            ) from exc
        finally:
            sheet.close()
        return width, height

    def _resolve_parent(self, asset: Asset, requested: str | None) -> str | None:
        if requested is not None:
            self._require_asset_version(asset, requested)
            return requested
        current = self._require_asset_version(asset, asset.current_version_id)
        return current.id if current.media_type != AssetMediaType.TEXT_REFERENCE else None

    def _validate_asset_media_kind(
        self,
        asset: Asset,
        media_type: AssetMediaType,
    ) -> None:
        if asset.kind != AssetKind.REFERENCE and media_type != AssetMediaType.IMAGE:
            raise AssetWorkbenchConflict(
                "Character, location, and prop assets currently accept image media only"
            )

    @staticmethod
    def _media_type_for_path(path: Path) -> AssetMediaType:
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return AssetMediaType.IMAGE
        if suffix in _VIDEO_EXTENSIONS:
            return AssetMediaType.VIDEO
        if suffix in _AUDIO_EXTENSIONS:
            return AssetMediaType.AUDIO
        raise AssetWorkbenchConflict(f"Unsupported asset file type: {suffix or 'none'}")

    @staticmethod
    def _image_dimensions(
        path: Path,
        media_type: AssetMediaType,
    ) -> tuple[int | None, int | None]:
        if media_type != AssetMediaType.IMAGE:
            return None, None
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                return image.size
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetWorkbenchConflict(f"Invalid image file: {path.name}") from exc

    @staticmethod
    def _validate_output_image(path: Path) -> tuple[int, int]:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("Provider output is not a readable image") from exc
        if width <= 0 or height <= 0:
            raise ValueError("Provider output has invalid dimensions")
        return width, height

    def _cost_record(self, usage: ImageUsage) -> ImageCostRecord:
        pricing = self.config_store.pricing_snapshot()
        estimated = (
            usage.input_tokens_details.image_tokens * pricing.image_input
            + usage.input_tokens_details.text_tokens * pricing.text_input
            + usage.output_tokens_details.image_tokens * pricing.image_output
        ) / 1_000_000
        if usage.output_tokens_details.image_tokens == 0 and usage.output_tokens:
            estimated += usage.output_tokens * pricing.image_output / 1_000_000
        return ImageCostRecord(
            estimated_amount=round(estimated, 8),
            pricing=pricing,
            usage=usage,
        )

    def _provider(self) -> ImageProvider:
        api_key = self.config_store.api_key()
        if not api_key:
            raise AssetProviderConfigurationError("OpenAI API key is not configured")
        return self.provider_factory(api_key, self.config_store.base_url())

    def _mark_task_failed(
        self,
        task: ImageGenerationTask,
        code: str,
        message: str,
        *,
        retry_disposition: ImageRetryDisposition = (
            ImageRetryDisposition.REVISE_AND_ENQUEUE
        ),
    ) -> ImageGenerationTask:
        now = utc_now()
        task.status = ImageTaskStatus.FAILED
        task.error_code = code
        task.error_message = message
        task.retry_disposition = retry_disposition
        task.finished_at = now
        task.updated_at = now
        self.repository.save_image_task(task)
        return task

    def _asset_directory(self, project_id: str, asset_id: str) -> Path:
        return self.workspace_root / project_id / "assets" / asset_id

    def _final_output_path(
        self,
        task: ImageGenerationTask,
        suffix: str | None = None,
    ) -> Path:
        resolved_suffix = suffix or Path(task.output_path or task.staged_path or ".png").suffix
        if resolved_suffix == ".part":
            resolved_suffix = Path(Path(task.staged_path or "image.png").stem).suffix or ".png"
        return self._asset_directory(task.project_id, task.asset_id) / (
            f"v{task.output_version_number}-{task.output_version_id}{resolved_suffix}"
        )

    def _require_project(self, project_id: str) -> ProjectDocument:
        document = self.repository.get_project(project_id)
        if document is None:
            raise AssetWorkbenchNotFound(project_id)
        return document

    @staticmethod
    def _require_asset(document: ProjectDocument, asset_id: str) -> Asset:
        asset = next((item for item in document.assets if item.id == asset_id), None)
        if asset is None:
            raise AssetWorkbenchNotFound(asset_id)
        return asset

    @staticmethod
    def _require_asset_version(asset: Asset, version_id: str) -> AssetVersion:
        version = next((item for item in asset.versions if item.id == version_id), None)
        if version is None:
            raise AssetWorkbenchNotFound(version_id)
        return version

    def _find_version(
        self,
        document: ProjectDocument,
        version_id: str,
    ) -> tuple[Asset, AssetVersion]:
        for asset in document.assets:
            for version in asset.versions:
                if version.id == version_id:
                    return asset, version
        raise AssetWorkbenchNotFound(version_id)

    def _require_image_task(self, task_id: str) -> ImageGenerationTask:
        task = self.repository.get_image_task(task_id)
        if task is None:
            raise AssetWorkbenchNotFound(task_id)
        return task

    def next_asset_version_number(self, asset: Asset) -> int:
        """Read the next unoccupied number while holding the shared mutation lock.

        The caller must save its version before releasing that same lock. This
        supports local frame and semantic edits using the same reservation space.
        """
        return self._next_version_number(asset)

    def _next_version_number(self, asset: Asset) -> int:
        """Allocate under the workbench lock; persisted tasks also reserve numbers.

        Terminal task reservations remain occupied so retries and delayed recovery
        cannot reuse a number already exposed in a task receipt or output path.
        """
        reserved = (
            task.output_version_number
            for task in self.repository.list_image_tasks(asset.project_id, asset.id)
        )
        return max([0, *(version.version for version in asset.versions), *reserved]) + 1
