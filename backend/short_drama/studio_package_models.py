"""Portable project contracts and small persisted transfer lifecycle records."""

from __future__ import annotations

from typing import Literal
from pydantic import Field
from short_drama.models import DomainModel, ProjectAggregate


class PackageMedia(DomainModel):
    source_path: str
    archive_path: str
    status: Literal["included", "missing"] = "included"
    bytes: int = 0
    reason: str | None = None


class PackageEpisode(DomainModel):
    project_id: str
    title: str
    document_path: str
    trace_path: str


class ProjectPackageManifest(DomainModel):
    format: Literal["short-drama-project"] = "short-drama-project"
    schema_version: Literal[1] = 1
    root_project_id: str
    created_at: str
    episodes: list[PackageEpisode]
    media: list[PackageMedia] = Field(default_factory=list[PackageMedia])


class StudioProjectPackage(DomainModel):
    package_id: str
    path: str
    filename: str
    download_url: str
    episode_count: int
    media_count: int
    missing_media: list[PackageMedia]


class StudioProjectPackageImport(DomainModel):
    project: ProjectAggregate
    episode_ids: list[str]
    media_count: int
    missing_media: list[PackageMedia]
    trace_path: str
    notice: str


class StudioProjectImportSummary(DomainModel):
    project_id: str
    episode_ids: list[str]
    media_count: int
    missing_media: list[PackageMedia]
    trace_path: str
    notice: str


class StudioProjectTransfer(DomainModel):
    id: str
    operation: Literal["collect", "import"]
    project_id: str | None = None
    project_name: str
    status: Literal["queued", "running", "cancelling", "cancelled", "completed", "failed"] = "queued"
    stage: str = "queued"
    message: str = "等待开始"
    processed_files: int = 0
    total_files: int | None = None
    processed_bytes: int = 0
    total_bytes: int | None = None
    cancellable: bool = True
    created_at: str
    updated_at: str
    finished_at: str | None = None
    error: str | None = None
    package: StudioProjectPackage | None = None
    imported: StudioProjectImportSummary | None = None
    input_path: str | None = Field(default=None, exclude=True)
    working_path: str | None = Field(default=None, exclude=True)


class StudioProjectTransfers(DomainModel):
    transfers: list[StudioProjectTransfer]
    next_cursor: str | None = None
