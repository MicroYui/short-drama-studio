"""Asset versioning, imports, approval, and persistent image-provider tasks."""

from __future__ import annotations

import base64
import mimetypes
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFilter,
    ImageOps,
    UnidentifiedImageError,
)

from short_drama.image_models import (
    AssetBriefUpdateRequest,
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetReferenceSheetComposeRequest,
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
    ImageTaskCreateRequest,
    ImageTaskStatus,
    ImageUsage,
)
from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetLayoutProfile,
    AssetMediaType,
    AssetPresentationMode,
    AssetReferenceSheet,
    AssetReferenceSheetCell,
    AssetReferenceStrategy,
    AssetSheetInputPolicy,
    AssetSourceRecord,
    AssetStandardView,
    AssetVersion,
    Freshness,
    OriginType,
    ProjectDocument,
)
from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
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
_CHARACTER_STANDARD_VIEWS = frozenset(
    {
        AssetStandardView.CANONICAL_MASTER,
        AssetStandardView.CHARACTER_FRONT,
        AssetStandardView.CHARACTER_LEFT_PROFILE,
        AssetStandardView.CHARACTER_RIGHT_PROFILE,
        AssetStandardView.CHARACTER_BACK,
        AssetStandardView.CHARACTER_WARDROBE_DETAIL,
    }
)
_SCENE_COVERAGE_VIEWS = frozenset(
    {
        AssetStandardView.CANONICAL_MASTER,
        AssetStandardView.SCENE_MASTER,
        AssetStandardView.SCENE_OVERHEAD,
        AssetStandardView.SCENE_REVERSE,
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
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root
        self.config_store = config_store
        self.provider_factory = provider_factory
        self._lock = threading.RLock()
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

    def create_asset(self, project_id: str, request: AssetCreateRequest) -> AssetVersion:
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
        with self._lock:
            document = self._require_project(project_id)
            now = utc_now()
            asset_id = f"asset-{uuid4().hex[:12]}"
            version = AssetVersion(
                id=f"asset-version-{uuid4().hex[:12]}",
                version=1,
                origin_type=OriginType.MANUAL,
                descriptor=request.descriptor.strip(),
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
            asset = Asset(
                id=asset_id,
                project_id=project_id,
                kind=request.kind,
                name=request.name.strip(),
                brief=(
                    request.brief.model_copy(deep=True)
                    if request.brief is not None
                    else None
                ),
                current_version_id=version.id,
                versions=[version],
            )
            document.assets.append(asset)
            document.project.updated_at = now
            self.repository.save_project(document)
            return version

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
            output_number = self._next_version_number(target)
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
        output_path = output_directory / (
            f"v{output_number}-{output_version_id}-reference-sheet.png"
        )
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
                if (
                    target.current_version_id != expected_current_version_id
                    or self._next_version_number(target) != output_number
                ):
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
            next_version = self._next_version_number(asset) if asset is not None else 1
            resolved_asset_id = asset.id if asset is not None else f"asset-{uuid4().hex[:12]}"
            expected_current_version_id = (
                asset.current_version_id if asset is not None else None
            )
        version_id = f"asset-version-{uuid4().hex[:12]}"
        directory = self._asset_directory(project_id, resolved_asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        source_path = directory / f".{version_id}-upload{suffix}"
        output_path = directory / f"v{next_version}-{version_id}.wav"
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
            version=next_version,
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
            self._validate_asset_output_size(asset, provider_request.size)
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
        task = self._require_image_task(task_id)
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
        if task.status not in {
            ImageTaskStatus.FAILED,
            ImageTaskStatus.CANCELLED,
            ImageTaskStatus.OUTPUT_MISSING,
        }:
            raise AssetWorkbenchConflict(
                f"Image task {task.id} cannot retry from {task.status.value}"
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
            candidate = self._final_output_path(task)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(candidate)
            task.output_path = str(candidate)
        width, height = self._validate_output_image(candidate)
        return self._finalize_image_version(task, candidate, width, height)

    def _submit_image_task(self, task: ImageGenerationTask) -> ImageGenerationTask:
        task.submitted_at = utc_now()
        task.updated_at = task.submitted_at
        task.error_code = None
        task.error_message = None
        self.repository.save_image_task(task)
        inputs = self._resolve_provider_inputs(task)
        try:
            result = self._provider().create(task.request, inputs)
        except ImageProviderDefiniteError as exc:
            return self._mark_task_failed(task, exc.code, str(exc))
        except ImageProviderSubmissionUnknown as exc:
            task.status = ImageTaskStatus.SUBMIT_UNKNOWN
            task.error_code = "IMAGE_SUBMIT_UNKNOWN"
            task.error_message = str(exc)
            task.updated_at = utc_now()
            self.repository.save_image_task(task)
            return task

        task.provider_request_id = result.provider_request_id
        task.revised_prompt = result.revised_prompt
        task.usage = result.usage
        task.cost = self._cost_record(result.usage)
        try:
            output_path, width, height = self._land_provider_output(task, result)
            return self._finalize_image_version(task, output_path, width, height)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            task.status = ImageTaskStatus.OUTPUT_MISSING
            task.error_code = "IMAGE_OUTPUT_MISSING"
            task.error_message = str(exc)
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
        if task.request.mask_version_id is not None:
            self._apply_masked_composite(task, staged, width, height)
            width, height = self._validate_output_image(staged)
        output_path = self._final_output_path(task, suffix)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(output_path)
        task.output_path = str(output_path)
        task.updated_at = utc_now()
        self.repository.save_image_task(task)
        return output_path, width, height

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
                origin = (
                    OriginType.AI_EDITED
                    if task.operation == ImageOperation.EDIT
                    else OriginType.AI_GENERATED
                )
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
                    source=AssetSourceRecord(
                        source_type=origin,
                        source_label=(
                            "GPT Image 2 编辑" if task.operation == ImageOperation.EDIT else "GPT Image 2 生成"
                        ),
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
            if version.external_url is not None:
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
            raise AssetWorkbenchConflict("Edit requires at least one input AssetVersion")
        for version_id in input_version_ids:
            _asset, version = self._find_version(document, version_id)
            if version.media_type != AssetMediaType.IMAGE:
                raise AssetWorkbenchConflict("GPT Image edit inputs must be images")
            if version.local_path is None and version.external_url is None:
                raise AssetWorkbenchConflict("GPT Image edit input has no usable media")
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
    def _validate_asset_output_size(asset: Asset, size: str) -> None:
        if asset.brief is None:
            return
        raw_width, raw_height = size.split("x", maxsplit=1)
        width, height = int(raw_width), int(raw_height)
        layout_profile = asset.brief.layout_profile
        if layout_profile in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
        } and width * 9 != height * 16:
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
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9: 2,
            AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2: 3,
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
                with Image.open(source_path) as source_image:
                    normalized = ImageOps.exif_transpose(source_image).convert("RGB")
                    fitted = ImageOps.contain(
                        normalized,
                        (max(1, right - left), max(1, image_bottom - top)),
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
    ) -> ImageGenerationTask:
        now = utc_now()
        task.status = ImageTaskStatus.FAILED
        task.error_code = code
        task.error_message = message
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

    @staticmethod
    def _next_version_number(asset: Asset) -> int:
        return max((version.version for version in asset.versions), default=0) + 1
