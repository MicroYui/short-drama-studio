"""Canonical asset, catalog, review, and reference-pack tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
)
from short_drama.image_models import (
    AssetBriefUpdateRequest,
    AssetCreateRequest,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReviewRequest,
)
from short_drama.reference_planner import (
    ReferencePackEntryCreateRequest,
    ReferencePackEntryMutationResponse,
)
from short_drama.stock_assets import (
    StockAssetImportRequest,
    StockAssetSearchRequest,
    StockAssetSearchResponse,
)
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry


class ArtifactToolService(Protocol):
    def create_asset(
        self,
        project_id: str,
        request: AssetCreateRequest,
    ) -> AssetMutationResponse: ...

    def update_asset_brief(
        self,
        project_id: str,
        asset_id: str,
        request: AssetBriefUpdateRequest,
    ) -> AssetMutationResponse: ...

    def search_asset_catalog(
        self,
        request: AssetCatalogSearchRequest,
        *,
        current_project_id: str | None = None,
    ) -> AssetCatalogSearchResponse: ...

    def inspect_asset_coverage(self, project_id: str) -> AssetCoverageResponse: ...

    def import_catalog_asset(
        self,
        project_id: str,
        request: AssetCatalogImportRequest,
    ) -> AssetMutationResponse: ...

    def search_stock_assets(
        self,
        request: StockAssetSearchRequest,
    ) -> StockAssetSearchResponse: ...

    def import_stock_asset(
        self,
        project_id: str,
        request: StockAssetImportRequest,
    ) -> AssetMutationResponse: ...

    def import_local_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportLocalRequest,
    ) -> AssetMutationResponse: ...

    def import_url_asset_version(
        self,
        project_id: str,
        asset_id: str,
        request: AssetImportUrlRequest,
    ) -> AssetMutationResponse: ...

    def review_asset_version(
        self,
        project_id: str,
        asset_id: str,
        version_id: str,
        request: AssetReviewRequest,
    ) -> AssetMutationResponse: ...

    def add_reference_pack_entry(
        self,
        project_id: str,
        request: ReferencePackEntryCreateRequest,
    ) -> ReferencePackEntryMutationResponse: ...


class AssetCreateInput(ToolModel):
    project_id: str
    request: AssetCreateRequest


class AssetBriefUpdateInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetBriefUpdateRequest


class AssetCatalogSearchInput(ToolModel):
    request: AssetCatalogSearchRequest
    current_project_id: str | None = None


class AssetCoverageInput(ToolModel):
    project_id: str


class AssetCatalogImportInput(ToolModel):
    project_id: str
    request: AssetCatalogImportRequest


class StockAssetSearchInput(ToolModel):
    request: StockAssetSearchRequest


class StockAssetImportInput(ToolModel):
    project_id: str
    request: StockAssetImportRequest


class AssetVersionImportLocalInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetImportLocalRequest


class AssetVersionImportUrlInput(ToolModel):
    project_id: str
    asset_id: str
    request: AssetImportUrlRequest


class AssetVersionReviewInput(ToolModel):
    project_id: str
    asset_id: str
    version_id: str
    request: AssetReviewRequest


class ReferencePackEntryAddInput(ToolModel):
    project_id: str
    request: ReferencePackEntryCreateRequest


def _asset_mutation_execution(
    project_id: str,
    result: AssetMutationResponse,
) -> ToolExecution[AssetMutationResponse]:
    return ToolExecution(
        output=result,
        artifact_refs=(
            ArtifactRef(
                project_id=project_id,
                artifact_type="asset",
                artifact_id=result.asset.id,
            ),
            ArtifactRef(
                project_id=project_id,
                artifact_type="asset_version",
                artifact_id=result.version.id,
                version=result.version.version,
            ),
        ),
    )


def register_tools(registry: ToolRegistry, service: ArtifactToolService) -> None:
    def create_asset(request: AssetCreateInput) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.create_asset(request.project_id, request.request),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_asset",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Create a canonical character, location, prop, or style asset.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetCreateInput,
            output_model=AssetMutationResponse,
            handler=create_asset,
            tags=frozenset({"asset", "project"}),
        )
    )

    def update_brief(
        request: AssetBriefUpdateInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.update_asset_brief(
                request.project_id,
                request.asset_id,
                request.request,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.update_brief",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "Set structured identity, visual, material, texture, continuity, and "
                "optional reference-view strategy for an existing canonical asset."
            ),
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=AssetBriefUpdateInput,
            output_model=AssetMutationResponse,
            handler=update_brief,
            tags=frozenset({"asset", "brief", "continuity", "planning"}),
        )
    )

    registry.register(
        ToolSpec(
            id="artifact.search_catalog",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Search reusable approved asset versions in the local project catalog.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetCatalogSearchInput,
            output_model=AssetCatalogSearchResponse,
            handler=lambda request: ToolExecution(
                output=service.search_asset_catalog(
                    request.request,
                    current_project_id=request.current_project_id,
                )
            ),
            tags=frozenset({"asset", "catalog", "discovery"}),
        )
    )

    registry.register(
        ToolSpec(
            id="artifact.inspect_coverage",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Inspect missing canonical reference views for one project.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetCoverageInput,
            output_model=AssetCoverageResponse,
            handler=lambda request: ToolExecution(
                output=service.inspect_asset_coverage(request.project_id),
                artifact_refs=(
                    ArtifactRef(
                        project_id=request.project_id,
                        artifact_type="project",
                        artifact_id=request.project_id,
                    ),
                ),
            ),
            tags=frozenset({"asset", "coverage", "planning"}),
        )
    )

    def import_catalog(
        request: AssetCatalogImportInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.import_catalog_asset(request.project_id, request.request),
        )

    registry.register(
        ToolSpec(
            id="artifact.import_catalog",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Import a reusable catalog version into the current project.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetCatalogImportInput,
            output_model=AssetMutationResponse,
            handler=import_catalog,
            tags=frozenset({"asset", "catalog", "import"}),
        )
    )

    registry.register(
        ToolSpec(
            id="artifact.search_stock",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Search the configured stock catalog for an asset candidate.",
            effect=ToolEffect.EXTERNAL,
            idempotent=True,
            input_model=StockAssetSearchInput,
            output_model=StockAssetSearchResponse,
            handler=lambda request: ToolExecution(
                output=service.search_stock_assets(request.request)
            ),
            tags=frozenset({"asset", "stock", "discovery"}),
        )
    )

    def import_stock(
        request: StockAssetImportInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.import_stock_asset(request.project_id, request.request),
        )

    registry.register(
        ToolSpec(
            id="artifact.import_stock",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Import or derive a selected stock asset into a canonical asset.",
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=StockAssetImportInput,
            output_model=AssetMutationResponse,
            handler=import_stock,
            tags=frozenset({"asset", "stock", "import"}),
        )
    )

    def import_local(
        request: AssetVersionImportLocalInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.import_local_asset_version(
                request.project_id,
                request.asset_id,
                request.request,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.import_local_version",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Add a local media file as a new version of an existing asset.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetVersionImportLocalInput,
            output_model=AssetMutationResponse,
            handler=import_local,
            tags=frozenset({"asset", "import", "local"}),
        )
    )

    def import_url(
        request: AssetVersionImportUrlInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.import_url_asset_version(
                request.project_id,
                request.asset_id,
                request.request,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.import_url_version",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Add a remote media URL as a new version of an existing asset.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetVersionImportUrlInput,
            output_model=AssetMutationResponse,
            handler=import_url,
            tags=frozenset({"asset", "import", "url"}),
        )
    )

    def review_version(
        request: AssetVersionReviewInput,
    ) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.review_asset_version(
                request.project_id,
                request.asset_id,
                request.version_id,
                request.request,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.review_version",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Record approval or rejection for a generated or imported asset version.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=AssetVersionReviewInput,
            output_model=AssetMutationResponse,
            handler=review_version,
            tags=frozenset({"asset", "review"}),
        )
    )

    def add_reference_entry(
        request: ReferencePackEntryAddInput,
    ) -> ToolExecution[ReferencePackEntryMutationResponse]:
        result = service.add_reference_pack_entry(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="reference_pack",
                    artifact_id=result.pack.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="reference_pack_entry",
                    artifact_id=result.entry.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset_version",
                    artifact_id=result.entry.asset_version_id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.add_reference_entry",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Add an approved asset version to a subject reference pack.",
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ReferencePackEntryAddInput,
            output_model=ReferencePackEntryMutationResponse,
            handler=add_reference_entry,
            tags=frozenset({"asset", "reference-pack", "shot-input"}),
        )
    )


__all__ = [
    "AssetBriefUpdateInput",
    "AssetCatalogImportInput",
    "AssetCatalogSearchInput",
    "AssetCoverageInput",
    "AssetCreateInput",
    "AssetVersionImportLocalInput",
    "AssetVersionImportUrlInput",
    "AssetVersionReviewInput",
    "ReferencePackEntryAddInput",
    "StockAssetImportInput",
    "StockAssetSearchInput",
    "register_tools",
]
