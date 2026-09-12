"""Canonical asset, catalog, review, and reference-pack tool adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue, model_validator

from short_drama.agent_runtime import ToolAttemptRef
from short_drama.asset_plan_receipts import asset_plan_write_receipt

from short_drama.asset_catalog import (
    AssetCatalogImportRequest,
    AssetCatalogSearchRequest,
    AssetCatalogSearchResponse,
    AssetCoverageResponse,
)
from short_drama.image_models import (
    AssetBriefUpdateRequest,
    AssetCreateRequest,
    AssetBatchCreateRequest,
    AssetBatchMutationResponse,
    AssetImportLocalRequest,
    AssetImportUrlRequest,
    AssetMutationResponse,
    AssetReviewRequest,
)
from short_drama.models import (
    ProductionDocument,
    ProductionDocumentCreateRequest,
    ProductionDocumentListResponse,
    ProductionDocumentMutationResponse,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ProductionDocumentReadResponse,
    ProductionDocumentReviseRequest,
    ProductionDocumentVersion,
)
from short_drama.production_contracts import (
    AssetPlanCreateRequest,
    AssetPlanExecutionReconcileMutationResponse,
    AssetPlanExecutionReconcileRequest,
    AssetPlanExecutionReconcileSummary,
    AssetPlanFieldPatch,
    AssetPlanIndexResponse,
    AssetPlanMutationResponse,
    AssetPlanMutationOperation,
    AssetPlanMutationSummaryResponse,
    AssetPlanPatchRequest,
    AssetPlanPatchSummaryResponse,
    AssetPlanReadResponse,
    AssetPlanReplaceRequest,
    AssetPlanningContextResponse,
    AssetWorkContextResponse,
    AssetWorkContextVersion,
    ProductionSpecCreateRequest,
    ProductionSpecMutationResponse,
    ProductionSpecReadResponse,
    ProductionSpecReplaceRequest,
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
from short_drama.tools.artifact_views import (
    AssetPlanCompactReadResponse,
    ProductionArtifactMutationSummaryResponse,
    ProductionDocumentCompactListResponse,
    ProductionDocumentCompactReadResponse,
    ProductionSpecCompactReadResponse,
    compact_asset_plan_read,
    compact_production_document_list,
    compact_production_document_read,
    compact_production_spec_read,
    mutation_summary,
)
from short_drama.tools.registry import ToolRegistry


class ArtifactToolService(Protocol):
    def create_asset_plan(
        self,
        project_id: str,
        request: AssetPlanCreateRequest,
    ) -> AssetPlanMutationResponse: ...

    def replace_asset_plan(
        self,
        project_id: str,
        request: AssetPlanReplaceRequest,
    ) -> AssetPlanMutationResponse: ...

    def patch_asset_plan(
        self,
        project_id: str,
        request: AssetPlanPatchRequest,
    ) -> AssetPlanMutationResponse: ...

    def reconcile_asset_plan_execution_contract(
        self,
        project_id: str,
        request: AssetPlanExecutionReconcileRequest,
    ) -> AssetPlanExecutionReconcileMutationResponse: ...

    def get_asset_plan(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> AssetPlanReadResponse: ...

    def get_asset_plan_index(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> AssetPlanIndexResponse: ...

    def get_asset_planning_context(
        self,
        project_id: str,
    ) -> AssetPlanningContextResponse: ...

    def get_asset_work_context(
        self,
        project_id: str,
        *,
        item_ids: list[str],
        work_item_ids: list[str],
        shot_ids: list[str],
        clip_group_ids: list[str],
        version_id: str | None = None,
    ) -> AssetWorkContextResponse: ...

    def create_production_spec(
        self,
        project_id: str,
        request: ProductionSpecCreateRequest,
    ) -> ProductionSpecMutationResponse: ...

    def replace_production_spec(
        self,
        project_id: str,
        request: ProductionSpecReplaceRequest,
    ) -> ProductionSpecMutationResponse: ...

    def get_production_spec(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> ProductionSpecReadResponse: ...

    def create_production_document(
        self,
        project_id: str,
        request: ProductionDocumentCreateRequest,
    ) -> ProductionDocumentMutationResponse: ...

    def revise_production_document(
        self,
        project_id: str,
        production_document_id: str,
        request: ProductionDocumentReviseRequest,
    ) -> ProductionDocumentMutationResponse: ...

    def get_production_document(
        self,
        project_id: str,
        production_document_id: str,
        *,
        version_id: str | None = None,
    ) -> ProductionDocumentReadResponse: ...

    def list_production_documents(
        self,
        project_id: str,
    ) -> ProductionDocumentListResponse: ...

    def create_asset(
        self,
        project_id: str,
        request: AssetCreateRequest,
    ) -> AssetMutationResponse: ...

    def create_assets(self, project_id: str, request: AssetBatchCreateRequest) -> AssetBatchMutationResponse: ...

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


class AssetPlanAttemptPatchOperation(StrEnum):
    """RFC 6902 operations supported by compact AssetPlan attempt repair."""

    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"
    TEST = "test"


class AssetPlanAttemptPatch(ToolModel):
    """One JSON Patch operation relative to a stable AssetPlan entity."""

    item_id: str | None = Field(default=None, min_length=1, max_length=200)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=200)
    op: AssetPlanAttemptPatchOperation
    path: str = Field(max_length=1000)
    value: JsonValue = None

    @model_validator(mode="after")
    def validate_target(self) -> AssetPlanAttemptPatch:
        if not self.path:
            if (
                self.op != AssetPlanAttemptPatchOperation.REMOVE
                or (self.item_id is None and self.work_item_id is None)
            ):
                raise ValueError(
                    "An empty AssetPlan patch path is only valid for removing "
                    "one stable-ID item or work item"
                )
            return self
        if not self.path.startswith("/"):
            raise ValueError("AssetPlan attempt patch path must be an RFC 6901 pointer")
        if (
            self.op
            in {
                AssetPlanAttemptPatchOperation.ADD,
                AssetPlanAttemptPatchOperation.REPLACE,
                AssetPlanAttemptPatchOperation.TEST,
            }
            and "value" not in self.model_fields_set
        ):
            raise ValueError(
                f"AssetPlan attempt patch operation {self.op.value} requires value"
            )
        return self


class AssetPlanAttemptRetryInput(ToolModel):
    """Retry one failed AssetPlan mutation by patching its persisted arguments."""

    project_id: str = Field(min_length=1)
    attempt_ref: ToolAttemptRef
    patches: list[AssetPlanAttemptPatch] = Field(min_length=1, max_length=64)


class AssetPlanAttemptRetrier(Protocol):
    def retry_asset_plan_attempt(
        self,
        request: AssetPlanAttemptRetryInput,
    ) -> ToolExecution[AssetPlanMutationSummaryResponse]: ...


class AssetCreateInput(ToolModel):
    project_id: str
    request: AssetCreateRequest


class AssetsCreateInput(ToolModel):
    project_id: str
    request: AssetBatchCreateRequest


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


class ProductionDocumentCreateInput(ToolModel):
    project_id: str
    request: ProductionDocumentCreateRequest


class ProductionDocumentReviseInput(ToolModel):
    project_id: str
    production_document_id: str
    request: ProductionDocumentReviseRequest


class ProductionDocumentGetInput(ToolModel):
    project_id: str
    production_document_id: str
    version_id: str | None = None


class ProductionDocumentListInput(ToolModel):
    project_id: str


class ProductionSpecCreateInput(ToolModel):
    project_id: str
    request: ProductionSpecCreateRequest


class ProductionSpecReplaceInput(ToolModel):
    project_id: str
    request: ProductionSpecReplaceRequest


class ProductionSpecGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


class AssetPlanCreateInput(ToolModel):
    project_id: str
    request: AssetPlanCreateRequest


class AssetPlanReplaceInput(ToolModel):
    project_id: str
    request: AssetPlanReplaceRequest


class AssetPlanPatchInput(ToolModel):
    project_id: str
    request: AssetPlanPatchRequest


class AssetPlanFieldPatchInput(ToolModel):
    """Compact atomic field changes against one exact current AssetPlan."""

    project_id: str = Field(min_length=1)
    base_version_id: str = Field(min_length=1)
    patches: list[AssetPlanFieldPatch] = Field(min_length=1)
    notes: list[str] | None = Field(
        default=None,
        description=(
            "共享 AssetPlan 的完整备注列表；省略或 null 保留现有备注，"
            "传列表表示整表替换（[] 清空），并按 base 检查共享备注冲突。"
            "个人进度写 working_notes；只有确需修改共享备注时才提供完整目标列表。"
        ),
    )
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin


class AssetPlanExecutionReconcileInput(ToolModel):
    project_id: str = Field(min_length=1)
    request: AssetPlanExecutionReconcileRequest


class AssetPlanGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


class AssetPlanIndexGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


class AssetPlanningContextGetInput(ToolModel):
    project_id: str


class AssetItemContextScope(ToolModel):
    """Select complete AssetPlan items for planning-level inspection."""

    mode: Literal["ITEM"] = "ITEM"
    item_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_item_ids(self) -> AssetItemContextScope:
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("Asset item scope repeats an item ID")
        return self


class AssetWorkItemContextScope(ToolModel):
    """Select executable work items without expanding sibling work."""

    mode: Literal["WORK_ITEM"] = "WORK_ITEM"
    work_item_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_work_item_ids(self) -> AssetWorkItemContextScope:
        if len(self.work_item_ids) != len(set(self.work_item_ids)):
            raise ValueError("Asset work-item scope repeats a work-item ID")
        return self


AssetWorkContextScope = Annotated[
    AssetItemContextScope | AssetWorkItemContextScope,
    Field(discriminator="mode"),
]


class AssetWorkContextGetInput(ToolModel):
    project_id: str
    scope: AssetWorkContextScope
    shot_ids: list[str] = Field(
        default_factory=list[str],
        description=(
            "仅返回本任务确实需要的 Storyboard Shot ID；必须属于所选 "
            "AssetPlan item 的 serves_shot_ids。仅选 work_item 时默认不携带镜头正文。"
        ),
    )
    clip_group_ids: list[str] = Field(
        default_factory=list[str],
        description=(
            "仅返回本任务确实需要的 Storyboard ClipGroup ID；必须属于"
            "所选 AssetPlan item 的 serves_clip_group_ids。"
        ),
    )
    version_id: str | None = Field(
        default=None,
        description=(
            "可省略以读取当前 AssetPlan。指定时只能使用 asset_plan_identity.version_id "
            "返回的生产文档版本 ID；不是整数 version、v17 或图片 AssetVersion ID。"
        ),
    )

    @model_validator(mode="after")
    def require_selector(self) -> AssetWorkContextGetInput:
        for label, values in (
            ("shot", self.shot_ids),
            ("clip-group", self.clip_group_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Asset work context repeats a {label} ID")
        return self


def _production_document_refs(
    project_id: str,
    document: ProductionDocument,
    version: ProductionDocumentVersion,
) -> tuple[ArtifactRef, ArtifactRef]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_document",
            artifact_id=document.id,
            version=version.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_document_version",
            artifact_id=version.id,
            version=version.version,
        ),
    )


def _production_spec_refs(
    project_id: str,
    result: ProductionSpecReadResponse,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_spec",
            artifact_id=result.document.id,
            version=result.version.version,
        ),
        *_production_document_refs(project_id, result.document, result.version),
    )


def _asset_plan_refs(
    project_id: str,
    result: AssetPlanReadResponse,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan",
            artifact_id=result.document.id,
            version=result.version.version,
        ),
        *_production_document_refs(project_id, result.document, result.version),
    )


def _asset_plan_index_refs(
    project_id: str,
    result: AssetPlanIndexResponse,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan_index",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
    )


def _asset_plan_patch_refs(
    project_id: str,
    result: AssetPlanPatchSummaryResponse,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan_index",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
    )


def _asset_plan_reconcile_refs(
    project_id: str,
    result: AssetPlanExecutionReconcileSummary,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan_index",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
    )


def _asset_plan_mutation_refs(
    project_id: str,
    result: AssetPlanMutationSummaryResponse,
) -> tuple[ArtifactRef, ...]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan_index",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
    )


def _asset_plan_mutation_summary(
    *,
    project_id: str,
    result: AssetPlanMutationResponse,
    operation: AssetPlanMutationOperation,
    base_version_id: str | None,
) -> AssetPlanMutationSummaryResponse:
    return AssetPlanMutationSummaryResponse(
        project_id=project_id,
        operation=operation,
        asset_plan_identity=AssetWorkContextVersion(
            document_id=result.document.id,
            version_id=result.version.id,
            version=result.version.version,
        ),
        base_version_id=base_version_id,
        item_count=len(result.asset_plan.items),
        work_item_count=sum(
            len(item.work_items) for item in result.asset_plan.items
        ),
    )


def _asset_planning_context_refs(
    project_id: str,
    result: AssetPlanningContextResponse,
) -> tuple[ArtifactRef, ...]:
    refs = [
        ArtifactRef(
            project_id=project_id,
            artifact_type="project",
            artifact_id=project_id,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="script_version",
            artifact_id=result.script.id,
            version=result.script.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="bible_version",
            artifact_id=result.bible.id,
            version=result.bible.version,
        ),
    ]
    for artifact_type, identity in (
        ("production_spec", result.production_spec_identity),
        ("storyboard", result.storyboard_identity),
    ):
        if identity is None:
            continue
        refs.extend(
            (
                ArtifactRef(
                    project_id=project_id,
                    artifact_type=artifact_type,
                    artifact_id=identity.document_id,
                    version=identity.version,
                ),
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="production_document",
                    artifact_id=identity.document_id,
                    version=identity.version,
                ),
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="production_document_version",
                    artifact_id=identity.version_id,
                    version=identity.version,
                ),
            )
        )
    if result.storyboard is not None:
        refs.extend(
            ArtifactRef(
                project_id=project_id,
                artifact_type="shot",
                artifact_id=shot.id,
                version=shot.revision,
            )
            for shot in result.storyboard.shots
        )
        if result.storyboard_identity is not None:
            refs.extend(
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="clip_group",
                    artifact_id=group.id,
                    version=result.storyboard_identity.version,
                )
                for group in result.storyboard.clip_groups
            )
    if result.existing_asset_plan_index is not None:
        refs.append(
            ArtifactRef(
                project_id=project_id,
                artifact_type="asset_plan",
                artifact_id=(
                    result.existing_asset_plan_index.asset_plan_identity.document_id
                ),
                version=result.existing_asset_plan_index.asset_plan_identity.version,
            )
        )
    for asset in result.existing_assets:
        refs.append(
            ArtifactRef(
                project_id=project_id,
                artifact_type="asset",
                artifact_id=asset.id,
            )
        )
        version_ids = [(asset.current_version_id, asset.current_version)]
        if (
            asset.approved_version_id is not None
            and asset.approved_version_id != asset.current_version_id
        ):
            assert asset.approved_version is not None
            version_ids.append((asset.approved_version_id, asset.approved_version))
        refs.extend(
            ArtifactRef(
                project_id=project_id,
                artifact_type="asset_version",
                artifact_id=version_id,
                version=version,
            )
            for version_id, version in version_ids
        )
    unique: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    for ref in refs:
        unique[(ref.project_id, ref.artifact_type, ref.artifact_id, ref.version)] = ref
    return tuple(unique.values())


def _asset_work_context_refs(
    project_id: str,
    result: AssetWorkContextResponse,
) -> tuple[ArtifactRef, ...]:
    refs = [
        ArtifactRef(
            project_id=project_id,
            artifact_type="asset_plan_index",
            artifact_id=result.asset_plan_identity.document_id,
            version=result.asset_plan_identity.version,
        ),
    ]
    refs.extend(
        ArtifactRef(
            project_id=project_id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
        for shot in result.storyboard.shots
    )
    refs.extend(
        ArtifactRef(
            project_id=project_id,
            artifact_type="clip_group",
            artifact_id=group.id,
            version=result.storyboard.identity.version,
        )
        for group in result.storyboard.clip_groups
    )
    for item in result.asset_plan_items:
        for work_item in item.work_items:
            references = list(work_item.ordered_references)
            if work_item.reference_pack_intent is not None:
                lineage_root = work_item.reference_pack_intent.lineage_root
                if lineage_root is not None:
                    references.append(lineage_root)
            if work_item.compose_intent is not None:
                references.extend(
                    cell.source for cell in work_item.compose_intent.cells
                )
            for reference in references:
                if (
                    reference.asset_id is None
                    or reference.asset_version_id is None
                ):
                    continue
                refs.extend(
                    (
                        ArtifactRef(
                            project_id=project_id,
                            artifact_type="asset",
                            artifact_id=reference.asset_id,
                        ),
                        ArtifactRef(
                            project_id=project_id,
                            artifact_type="asset_version",
                            artifact_id=reference.asset_version_id,
                        ),
                    )
                )
    for media_version in result.media_versions:
        refs.extend((
            ArtifactRef(project_id=project_id, artifact_type="asset",
                        artifact_id=media_version.asset_id),
            ArtifactRef(project_id=project_id, artifact_type="asset_version",
                        artifact_id=media_version.asset_version_id,
                        version=media_version.version),
        ))
    refs.extend(
        ArtifactRef(project_id=project_id, artifact_type="reference_pack", artifact_id=pack.id)
        for pack in result.reference_packs
    )
    for intake in result.source_intake:
        refs.extend(
            (
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="asset",
                    artifact_id=intake.asset_id,
                ),
                ArtifactRef(
                    project_id=project_id,
                    artifact_type="asset_version",
                    artifact_id=intake.version.id,
                    version=intake.version.version,
                ),
            )
        )
    unique: dict[tuple[str, str, str, int | None], ArtifactRef] = {}
    for ref in refs:
        unique[(ref.project_id, ref.artifact_type, ref.artifact_id, ref.version)] = ref
    return tuple(unique.values())


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
    def create_production_spec(
        request: ProductionSpecCreateInput,
    ) -> ToolExecution[ProductionArtifactMutationSummaryResponse]:
        result = service.create_production_spec(request.project_id, request.request)
        return ToolExecution(
            output=mutation_summary(
                document=result.document,
                version=result.version,
                output_slot=result.output_slot,
            ),
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_production_spec",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "创建可选制作规格，记录片名与视听方向，并同步项目语言、画幅及明确目标时长；不生成媒体。"
                "ProductionSpec 还记录 Provider 偏好；规格内的片名不更改项目标题，"
                "不修改 ProjectBible、用户原始要求或已保存正文。"
                "成功时只返回精确新版本身份和输出槽指针；"
                "需要正文时显式读取 artifact.get_production_spec。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionSpecCreateInput,
            output_model=ProductionArtifactMutationSummaryResponse,
            handler=create_production_spec,
            tags=frozenset({"production-spec", "planning", "handoff"}),
        )
    )

    def replace_production_spec(
        request: ProductionSpecReplaceInput,
    ) -> ToolExecution[ProductionArtifactMutationSummaryResponse]:
        result = service.replace_production_spec(request.project_id, request.request)
        return ToolExecution(
            output=mutation_summary(
                document=result.document,
                version=result.version,
                output_slot=result.output_slot,
            ),
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.replace_production_spec",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "当用户意图或 Agent 的制片判断发生整体变化时，基于当前版本替换"
                " ProductionSpec 并保留历史版本；它不会静默改写既有分镜。"
                "成功响应是新版本身份的轻量确认，不回传正文和历史。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionSpecReplaceInput,
            output_model=ProductionArtifactMutationSummaryResponse,
            handler=replace_production_spec,
            tags=frozenset({"production-spec", "revision", "handoff"}),
        )
    )

    def get_production_spec(
        request: ProductionSpecGetInput,
    ) -> ToolExecution[ProductionSpecCompactReadResponse]:
        result = service.get_production_spec(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=compact_production_spec_read(result),
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_production_spec",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "读取当前或指定历史版本的 ProductionSpec，供分镜、资产和生成 Agent"
                " 使用同一份制片约束。响应只携带所选正文一次，"
                "不携带 document.versions 历史或 version.payload 副本。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionSpecGetInput,
            output_model=ProductionSpecCompactReadResponse,
            handler=get_production_spec,
            tags=frozenset({"production-spec", "read", "handoff"}),
        )
    )

    def create_asset_plan(
        request: AssetPlanCreateInput,
    ) -> ToolExecution[AssetPlanMutationSummaryResponse]:
        result = service.create_asset_plan(request.project_id, request.request)
        summary = _asset_plan_mutation_summary(
            project_id=request.project_id,
            result=result,
            operation=AssetPlanMutationOperation.CREATE,
            base_version_id=None,
        )
        return ToolExecution(
            output=summary,
            artifact_refs=_asset_plan_mutation_refs(request.project_id, summary),
            receipt=asset_plan_write_receipt(result, _asset_plan_mutation_refs(request.project_id, summary)),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_asset_plan",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "基于当前精确 Script、ProjectBible、ProductionSpec 与 Storyboard，"
                "创建版本化 AssetPlan。计划显式记录哪些主体需要锚点、仅保留文字"
                "或复用既有资产，并携带稳定工作项、依赖、最终提示词和有序引用；"
                "首次可只保存已确定的基础 items 或空清单，随后用 "
                "artifact.patch_asset_plan.upsert_items 按依赖分批追加；"
                "不要求一次写完整集，避免大型结构化输出截断。"
                "只因后续确定性文字不同的同规格重复载体，优先共享一份"
                "单件干净物理母版并分开计划 TEXT_PLATE；"
                "不会调用图片 Provider。成功响应只返回新版本身份与项目/"
                "工作项数量摘要，不回传完整 AssetPlan。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanCreateInput,
            output_model=AssetPlanMutationSummaryResponse,
            handler=create_asset_plan,
            tags=frozenset({"asset-plan", "planning", "handoff"}),
        )
    )

    def replace_asset_plan(
        request: AssetPlanReplaceInput,
    ) -> ToolExecution[AssetPlanMutationSummaryResponse]:
        result = service.replace_asset_plan(request.project_id, request.request)
        summary = _asset_plan_mutation_summary(
            project_id=request.project_id,
            result=result,
            operation=AssetPlanMutationOperation.REPLACE,
            base_version_id=request.request.base_version_id,
        )
        return ToolExecution(
            output=summary,
            artifact_refs=_asset_plan_mutation_refs(request.project_id, summary),
            receipt=asset_plan_write_receipt(result, _asset_plan_mutation_refs(request.project_id, summary)),
        )

    registry.register(
        ToolSpec(
            id="artifact.replace_asset_plan",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "基于当前版本修订 AssetPlan，保留稳定计划身份和历史版本。"
                "Agent 可按完整语义调整少数资产或整体计划，但这是完整快照替换，"
                "不是单条 item 的 patch：即使只调整少数资产，"
                "asset_plan 也必须保留其余 item/work item。只有真正的语义删除"
                "才分别列入 removed_item_ids 或 removed_work_item_ids；Tool 不替 Agent 做"
                "四视图、轻量参考等创意路由，也不会调用 Provider。"
                "恢复中断或发现旧 work item 缺少当前 Method 需要的精确"
                "布局 guide、identity reference 或其他真实输入时，Agent 可用它"
                "局部补全未提交工作，不因计划已持久化就沿用过时输入。"
                "成功响应只返回新版本身份与数量摘要，不回传完整"
                " AssetPlan；需要继续调度时读取轻量 index 或任务切片。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanReplaceInput,
            output_model=AssetPlanMutationSummaryResponse,
            handler=replace_asset_plan,
            tags=frozenset({"asset-plan", "revision", "handoff"}),
        )
    )

    def patch_asset_plan(
        request: AssetPlanPatchInput,
    ) -> ToolExecution[AssetPlanPatchSummaryResponse]:
        result = service.patch_asset_plan(request.project_id, request.request)
        summary = AssetPlanPatchSummaryResponse(
            project_id=request.project_id,
            asset_plan_identity=AssetWorkContextVersion(
                document_id=result.document.id,
                version_id=result.version.id,
                version=result.version.version,
            ),
            base_version_id=request.request.base_version_id,
            upserted_item_ids=[item.id for item in request.request.upsert_items],
            work_item_patch_item_ids=[
                patch.item_id for patch in request.request.work_item_patches
            ],
            upserted_work_item_ids=[
                work_item.id
                for patch in request.request.work_item_patches
                for work_item in patch.upsert_work_items
            ],
            removed_item_ids=list(request.request.removed_item_ids),
            removed_work_item_ids=[
                work_item_id
                for patch in request.request.work_item_patches
                for work_item_id in patch.removed_work_item_ids
            ],
            notes_updated=request.request.notes is not None,
        )
        return ToolExecution(
            output=summary,
            artifact_refs=_asset_plan_patch_refs(request.project_id, summary),
            receipt=asset_plan_write_receipt(result, _asset_plan_patch_refs(request.project_id, summary)),
        )

    registry.register(
        ToolSpec(
            id="artifact.patch_asset_plan",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "对当前精确 AssetPlan 做局部语义修改：按稳定 item/work-item ID "
                "原位替换或追加，并显式移除指定 ID。服务端会把 patch 合并成完整"
                "计划后执行结构、依赖、来源存在性、媒体与完成引用校验。"
                "上游剧本正在修改时仍可保存局部计划，保留实际声明的来源；"
                "无需先重存所有上游文档。生成前再同步本次实际使用的内容。"
                "也用于首次规划的分批写入：upsert_items 只携带这一批新项，"
                "已保存项自动保留；依赖项先于引用它的工作保存。"
                "跨多个 item 但只需修改局部字段时，使用 field_patches "
                "按稳定 item_id/work_item_id 提交一组原子 RFC 6902 修订，避免"
                "重传多个完整 item；work_item_id 在计划内唯一时可省略父 item_id。"
                "若新增项使用更新后的分镜，可在同一请求用不带 item_id 的 field_patches "
                "replace /storyboard_version_id 同步明确采用的分镜来源；"
                "source_artifact_refs 只记录出处，不会替换计划正文中的来源字段。"
                "仅提交字段修改且本次对象未变化时，会与其他工作项的并行保存合并，"
                "可沿用最新已知 base_version_id；实际对象或制作来源发生变化才返回冲突。"
                "校验成功后保存；不会调用 Provider。局部完成"
                "回写、补参考或少量修复优先使用本 Tool，只有整体结构重构才提交"
                " artifact.replace_asset_plan 的完整快照。成功响应只返回新版本身份与"
                "本次变更 ID 摘要，不回传完整 AssetPlan。receipt 保留新"
                "asset_plan_identity.version_id、实际变更ID和执行合同诊断；"
                "后续局部保存可沿用该精确身份，无需只为取得版本再读index。"
                "需要完整工作目录时按需读取 artifact.get_asset_plan_index。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanPatchInput,
            output_model=AssetPlanPatchSummaryResponse,
            handler=patch_asset_plan,
            tags=frozenset({"asset-plan", "patch", "revision", "handoff"}),
        )
    )

    def reconcile_asset_plan_execution(
        request: AssetPlanExecutionReconcileInput,
    ) -> ToolExecution[AssetPlanExecutionReconcileSummary]:
        result = service.reconcile_asset_plan_execution_contract(
            request.project_id,
            request.request,
        )
        summary = AssetPlanExecutionReconcileSummary(
            project_id=request.project_id,
            base_version_id=request.request.base_version_id,
            asset_plan_identity=AssetWorkContextVersion(
                document_id=result.document.id,
                version_id=result.version.id,
                version=result.version.version,
            ),
            canonicalized_item_ids=result.canonicalized_item_ids,
            canonical_work_item_ids=result.canonical_work_item_ids,
            supplemental_work_item_ids=result.supplemental_work_item_ids,
            created_text_plate_item_ids=result.created_text_plate_item_ids,
            removed_text_work_item_ids=result.removed_text_work_item_ids,
            created_physical_master_work_item_ids=(
                result.created_physical_master_work_item_ids
            ),
            normalized_layout_item_ids=result.normalized_layout_item_ids,
            applied_execution_overrides=result.applied_execution_overrides,
        )
        return ToolExecution(
            output=summary,
            artifact_refs=_asset_plan_reconcile_refs(
                request.project_id,
                summary,
            ),
            receipt=asset_plan_write_receipt(result, _asset_plan_reconcile_refs(request.project_id, summary)),
        )

    registry.register(
        ToolSpec(
            id="artifact.reconcile_asset_plan_execution_contract",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "把 Agent 已经做出的资产执行语义决定编译成一个合法的 AssetPlan "
                "版本：为主体指定唯一 canonical work、supplemental work 与必要的 "
                "ReferencePack override；把物理载体和精确文字图版拆成独立 item。"
                "Tool 会确定性同步 review purpose、ReferencePack role、lineage、"
                "dependency、authoritative ordered reference、角色参考卡语义和布局，"
                "也可通过 execution_overrides 精确修改任意基础或补充 "
                "work-item 的 size/output layout；COMPOSE 的底层 intent 会自动同步。"
                "并构造合法的 SCREEN_CONTENT_PLATE；完整校验后只持久化一个版本。"
                "它不替 Agent 决定创意、不调用图片 Provider，也不返回完整 AssetPlan。"
                "notes 只是说明，不能代替结构化变更；成功摘要来自实际编译"
                "并落盘的结果。"
                "跨多个 item 的执行合同迁移优先使用本 Tool；"
                "artifact.patch_asset_plan_fields 只用于少量局部字段修正。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanExecutionReconcileInput,
            output_model=AssetPlanExecutionReconcileSummary,
            handler=reconcile_asset_plan_execution,
            tags=frozenset(
                {"asset-plan", "execution-contract", "reconcile", "handoff"}
            ),
        )
    )

    def patch_asset_plan_fields(
        request: AssetPlanFieldPatchInput,
    ) -> ToolExecution[AssetPlanPatchSummaryResponse]:
        result = service.patch_asset_plan(
            request.project_id,
            AssetPlanPatchRequest(
                base_version_id=request.base_version_id,
                field_patches=request.patches,
                notes=request.notes,
                source_artifact_refs=request.source_artifact_refs,
                origin=request.origin,
            ),
        )
        summary = AssetPlanPatchSummaryResponse(
            project_id=request.project_id,
            asset_plan_identity=AssetWorkContextVersion(
                document_id=result.document.id,
                version_id=result.version.id,
                version=result.version.version,
            ),
            base_version_id=request.base_version_id,
            notes_updated=request.notes is not None,
        )
        return ToolExecution(
            output=summary,
            artifact_refs=_asset_plan_patch_refs(request.project_id, summary),
            receipt=asset_plan_write_receipt(result, _asset_plan_patch_refs(request.project_id, summary)),
        )

    registry.register(
        ToolSpec(
            id="artifact.patch_asset_plan_fields",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "对当前精确 AssetPlan 提交一组紧凑、原子的字段修订。"
                "每项修订用稳定 item_id/work_item_id 选择实体，path 是相对"
                "该实体的 RFC 6901 路径；删除选中的整个 item/work item "
                "使用 remove 和空 path。新增顶层 item 时不填 item_id，"
                "使用 add /items/- 并在 value 中提供新 item。该 Tool 只适合"
                "少量、局部且字段语义已经明确的修正；跨多项的 canonical、"
                "lineage、参考依赖或文字载体迁移应使用 "
                "artifact.reconcile_asset_plan_execution_contract。服务端"
                "先在内存合并全部修订，再对完整计划做一次结构、依赖、"
                "当前上游版本、媒体和完成引用校验，成功后只写入一个"
                "新 AssetPlan 版本；不调用 Provider，不回传完整计划。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanFieldPatchInput,
            output_model=AssetPlanPatchSummaryResponse,
            handler=patch_asset_plan_fields,
            tags=frozenset(
                {"asset-plan", "field-patch", "revision", "handoff"}
            ),
        )
    )

    def get_asset_planning_context(
        request: AssetPlanningContextGetInput,
    ) -> ToolExecution[AssetPlanningContextResponse]:
        result = service.get_asset_planning_context(request.project_id)
        return ToolExecution(
            output=result,
            artifact_refs=_asset_planning_context_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_asset_planning_context",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "为 ASSET_PLANNER 一次读取去重后的当前制片真相：当前 ScriptVersion、"
                "ProjectBibleVersion、ProductionSpec 与完整 Storyboard 正文各一份，"
                "以及用户原始要求、source intake 的当前/已批准精确版本和已有资产"
                "轻量索引。若 AssetPlan 已存在，只附轻量 identity/index，不附完整"
                "计划。响应不包含 ProjectDocument、script/bible 历史、"
                "ProductionDocument.versions 或重复 payload；只有整体修改已有计划时"
                "再单独调用 artifact.get_asset_plan。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetPlanningContextGetInput,
            output_model=AssetPlanningContextResponse,
            handler=get_asset_planning_context,
            tags=frozenset(
                {"asset-plan", "planning-context", "read", "handoff"}
            ),
        )
    )

    def get_asset_plan_index(
        request: AssetPlanIndexGetInput,
    ) -> ToolExecution[AssetPlanIndexResponse]:
        result = service.get_asset_plan_index(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_asset_plan_index_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_asset_plan_index",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "轻量读取 AssetPlan 的版本身份和可分派目录：item/work-item 稳定 "
                "ID、名称、kind/disposition、执行状态、依赖、服务的 Shot/ClipGroup "
                "ID 与完成 AssetVersion 引用，并给出当前执行合同是否需要"
                "语义协调的结构化事实。不返回 brief、final prompt、有序"
                "参考或完整 Storyboard/ProductionSpec。已有计划时 Leader 应先用本"
                "索引语义判断是先委派 Planner 协调旧合同，还是按 ID 分组"
                "委派 Builder/Verifier；Worker 再调用 artifact.get_asset_work_context；"
                "只有明确需要全局结构重构的 Worker 才读取完整 AssetPlan。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetPlanIndexGetInput,
            output_model=AssetPlanIndexResponse,
            handler=get_asset_plan_index,
            tags=frozenset(
                {"asset-plan", "inventory", "index", "read", "handoff"}
            ),
        )
    )

    def get_asset_plan(
        request: AssetPlanGetInput,
    ) -> ToolExecution[AssetPlanCompactReadResponse]:
        result = service.get_asset_plan(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=compact_asset_plan_read(result),
            artifact_refs=_asset_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_asset_plan",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "读取当前或指定历史版本的 AssetPlan，供资产 Worker、图片 Worker "
                "和后续镜头输入编排共享同一份资产决策与依赖。该 Tool 返回完整 "
                "brief、prompt 与 references；已有计划的 Leader 分派应先读取 "
                "artifact.get_asset_plan_index，只有明确的全局结构审阅或重构才读取本"
                "完整载荷。所选 AssetPlan 正文只出现一次，"
                "不回传 document.versions 历史或 version.payload 副本。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetPlanGetInput,
            output_model=AssetPlanCompactReadResponse,
            handler=get_asset_plan,
            tags=frozenset({"asset-plan", "read", "handoff"}),
        )
    )

    def get_asset_work_context(
        request: AssetWorkContextGetInput,
    ) -> ToolExecution[AssetWorkContextResponse]:
        item_ids = (
            request.scope.item_ids
            if isinstance(request.scope, AssetItemContextScope)
            else []
        )
        work_item_ids = (
            request.scope.work_item_ids
            if isinstance(request.scope, AssetWorkItemContextScope)
            else []
        )
        result = service.get_asset_work_context(
            request.project_id,
            item_ids=item_ids,
            work_item_ids=work_item_ids,
            shot_ids=request.shot_ids,
            clip_group_ids=request.clip_group_ids,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_asset_work_context_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_asset_work_context",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "通过互斥 scope 按稳定 AssetPlan ID 读取一次资产任务所需的精确"
                "上下文切片：所选工作项及依赖、关联 Storyboard Shot/ClipGroup、"
                "当前 ProductionSpec、原始用户要求和已登记 source intake。返回的"
                "版本身份不携带完整文档 payload，避免 project.get、完整 Storyboard、"
                "ProductionSpec 与 AssetPlan 的重复传输。WORK_ITEM scope 只自动补齐父 item "
                "和真实依赖，永不展开兄弟 work；ITEM scope 才读取整个 item。"
                "item_ids 与 work_item_ids 只能出现在各自的 scope 中，不接受"
                "顶层旧式 selector 或混合 scope。Worker 按本次职责显式传 shot_ids/"
                "clip_group_ids。source intake 也只返回当前工作项精确引用的"
                " AssetVersion。media_versions 返回候选、完成版本和输入版本的真实尺寸、"
                "审核状态、review_notes 和 lineage；返工先使用已有拒绝理由，不因缺少"
                "历史对话而重复验收。reference_packs 给出当前范围涉及的实际可用条目，"
                "包括载体包内的独立文字层，保留真实role/tags/angle/framing；"
                "shot_reference_plans 返回显式请求Shot的现有引用计划，供合并后更新。"
                "编排Shot引用时按这些已保存字段选择，不把reference_pack_intent或自拟标签"
                "当成已存在的条目。完整计划审阅仍使用"
                " artifact.get_asset_plan。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetWorkContextGetInput,
            output_model=AssetWorkContextResponse,
            handler=get_asset_work_context,
            tags=frozenset({"asset-plan", "context-slice", "read", "handoff"}),
        )
    )

    def create_production_document(
        request: ProductionDocumentCreateInput,
    ) -> ToolExecution[ProductionArtifactMutationSummaryResponse]:
        result = service.create_production_document(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=mutation_summary(
                document=result.document,
                version=result.version,
            ),
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_production_document",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "Create a versioned structured production document for durable "
                "cross-agent handoff. The success result is a lightweight exact-version "
                "acknowledgement; read the document explicitly when its payload is needed."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionDocumentCreateInput,
            output_model=ProductionArtifactMutationSummaryResponse,
            handler=create_production_document,
            tags=frozenset({"production-document", "handoff", "planning"}),
        )
    )

    def revise_production_document(
        request: ProductionDocumentReviseInput,
    ) -> ToolExecution[ProductionArtifactMutationSummaryResponse]:
        result = service.revise_production_document(
            request.project_id,
            request.production_document_id,
            request.request,
        )
        return ToolExecution(
            output=mutation_summary(
                document=result.document,
                version=result.version,
            ),
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.revise_production_document",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "Append a structured production-document version from its current base. "
                "The success result contains version identity rather than payload history."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionDocumentReviseInput,
            output_model=ProductionArtifactMutationSummaryResponse,
            handler=revise_production_document,
            tags=frozenset({"production-document", "handoff", "revision"}),
        )
    )

    def get_production_document(
        request: ProductionDocumentGetInput,
    ) -> ToolExecution[ProductionDocumentCompactReadResponse]:
        result = service.get_production_document(
            request.project_id,
            request.production_document_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=compact_production_document_read(
                document=result.document,
                version=result.version,
            ),
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_production_document",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "Read one current or historical production-document version. The selected "
                "payload appears exactly once; document.versions is not returned."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionDocumentGetInput,
            output_model=ProductionDocumentCompactReadResponse,
            handler=get_production_document,
            tags=frozenset({"production-document", "handoff", "read"}),
        )
    )

    def list_production_documents(
        request: ProductionDocumentListInput,
    ) -> ToolExecution[ProductionDocumentCompactListResponse]:
        result = service.list_production_documents(request.project_id)
        refs: list[ArtifactRef] = []
        for document in result.documents:
            version = next(
                item
                for item in document.versions
                if item.id == document.current_version_id
            )
            refs.extend(
                _production_document_refs(request.project_id, document, version)
            )
        return ToolExecution(
            output=compact_production_document_list(result.documents),
            artifact_refs=tuple(refs),
        )

    registry.register(
        ToolSpec(
            id="artifact.list_production_documents",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "List durable production-document identities and their exact current "
                "versions without payloads or document.versions history. Read a selected "
                "body explicitly with artifact.get_production_document."
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionDocumentListInput,
            output_model=ProductionDocumentCompactListResponse,
            handler=list_production_documents,
            tags=frozenset({"production-document", "handoff", "discovery"}),
        )
    )

    def create_asset(request: AssetCreateInput) -> ToolExecution[AssetMutationResponse]:
        return _asset_mutation_execution(
            request.project_id,
            service.create_asset(request.project_id, request.request),
        )

    def create_assets(request: AssetsCreateInput) -> ToolExecution[AssetBatchMutationResponse]:
        result = service.create_assets(request.project_id, request.request)
        refs: list[ArtifactRef] = []
        for asset in result.assets:
            refs.append(ArtifactRef(project_id=request.project_id, artifact_type="asset", artifact_id=asset.id))
            refs.extend(ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=version.id,
                version=version.version) for version in asset.versions)
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(ToolSpec(id="artifact.create_assets", version=1, category=ToolCategory.ARTIFACT,
        description="一次保存多个已确定的新人物、场景、道具或参考对象的文字设定。request.assets 每项复用 create_asset 的 kind/name/descriptor/brief；全批先校验，成功后一次局部追加保存，返回本批实际 Asset、brief、初始文本版本与精确引用，不返回整份项目。不生成图片、不修改已有对象、不要求凑齐全片对象；多个对象已明确时优先此入口，避免逐个语言轮搬运。已有对象设定用 artifact.update_brief，图像制作另用 image 工具。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=AssetsCreateInput, output_model=AssetBatchMutationResponse,
        handler=create_assets, tags=frozenset({"asset", "project", "batch", "current-content"})))

    registry.register(
        ToolSpec(
            id="artifact.create_asset",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "当角色、场景、道具或通用参考需要稳定 ID 与版本系谱时"
                "创建资产。只会建立结构化 Asset 和初始文本版本，不生成图片；"
                "项目视听风格仍写入 ProjectBible.style。多个对象已确定时用 artifact.create_assets 一次保存。"
            ),
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
                "在生成、导入或派生参考图之前，为已有核心资产写入身份/"
                "空间锚点、气质、外观/材质、连续性、参考深度与所需视图。"
                "当身份、状态或覆盖策略改变时再更新；它不自动生图。"
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
            description=(
                "在生成新图前搜索本地项目目录中可复用的已批准资产版本；"
                "返回候选供 Agent 按当前 brief 与 Style 判断，不会自动导入。"
            ),
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
            description=(
                "当 Shot ReferencePlan 已说明参考需求时，如实返回缺失的"
                " canonical 母版和未被当前 ReferencePack 满足的视图。"
                "每个缺口保留全部可接受职责、偏好角度、景别与 required 标记；"
                "只有必需缺口使 ready=false，可选缺口是建议。Tool 不替 Agent"
                "挑选候选集的第一项；Agent 根据完整语义和 Method Skill 取舍。"
                "这也不等于"
                " CoreAssetBrief.requested_views 已全部完成。"
            ),
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
            description=(
                "当 artifact.search_catalog 返回与当前 brief 匹配的已批准"
                "候选时，使用候选的 source_asset_id 定位实际拥有该版本的"
                "资产，将 canonical 或 supplemental 图像导入为新资产版本；导入后仍需"
                "按本项目用途检查并记录结论。"
            ),
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
            description=(
                "当项目需要现实素材或构图/材质参考，且本地已批准目录"
                "无合适结果时搜索配置的素材源。只返回候选，不会自动导入或推定人物身份。"
            ),
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
            description=(
                "在 Agent 明确选中素材候选及其用途后，导入或派生为当前项目"
                "的资产版本。人物素材默认只作视觉软参考，不默认复制真实身份。"
            ),
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
            description=(
                "当用户或其他 Worker 已提供本地媒体时，将文件登记为既有"
                " Asset 的新版本。导入只建立可追溯版本，不代表内容已验收。"
            ),
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
            description=(
                "当用户或其他 Worker 已提供远程媒体 URL 时，将它登记为"
                "既有 Asset 的新版本。导入后先调用 qa.inspect_asset_version "
                "观察实际图像，再决定是否批准。"
            ),
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
            description=(
                "在 Agent 已观察实际媒体并完成语义判断后，记录对某个"
                " AssetVersion 的批准或拒绝及理由。它只写入决策，不会自动看图；"
                "图片候选通常先调用 qa.inspect_asset_version。"
                "记录决策时按资产的实际制作职责判断问题是否阻碍使用，"
                "不把一图生成卡的近似分区或轻微背景渐变默认变成隐式门禁。"
                "同规格重复载体中能独立裁用的单件，不因副本间轻微尺寸"
                "或透视差就默认拒绝并全量重绘。"
                "CANONICAL_MASTER 更换资产母版；SUPPLEMENTAL_REFERENCE "
                "保留额外图像而不顶替母版，不适用于音频或视频。"
            ),
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
            description=(
                "把已明确选用、CURRENT 且有可用图片媒体的精确版本加入主体 "
                "ReferencePack，声明身份、角度、空间或道具职责。DRAFT 与 "
                "APPROVED 均可；登记不自动审核，不改变资产的 current_version_id "
                "或 approved_version_id。CANONICAL_MASTER 只接受主体自身图片，"
                "不要求已是 approved_version_id；lineage_root_version_id 省略或等于"
                "本版本，保存为自身。其他角色若声明来源根，该根须为主体自身 "
                "CURRENT 且可用的图片；独立来源可留空。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ReferencePackEntryAddInput,
            output_model=ReferencePackEntryMutationResponse,
            handler=add_reference_entry,
            tags=frozenset({"asset", "reference-pack", "shot-input"}),
        )
    )


def register_asset_plan_attempt_tools(
    registry: ToolRegistry,
    retrier: AssetPlanAttemptRetrier,
) -> None:
    """Register compact repair for failed, not-yet-persisted AssetPlan drafts."""

    if registry.contains("artifact.retry_asset_plan_attempt"):
        return

    registry.register(
        ToolSpec(
            id="artifact.retry_asset_plan_attempt",
            version=2,
            category=ToolCategory.ARTIFACT,
            description=(
                "对已持久化的失败 AssetPlan create/replace/retry 尝试做局部修复并"
                "原地重试。使用 ToolAttemptRef 保留完整候选，每个 patch 先按稳定"
                " item_id 或全局唯一 work_item_id 选中对象，再用相对 RFC 6901 "
                "JSON Pointer "
                "执行 RFC 6902 add/remove/replace/test；需删除选中的整个 "
                "item/work item 时使用 remove 和空 path，不使用数组下标。"
                "不要先把整份失败请求读回并"
                "重传；只提交需要修改的小补丁。失败重试可继续引用上一次"
                " retry 尝试，运行时会恢复并累积既有补丁。成功时只返回现有"
                " AssetPlan 版本与数量摘要，不回传完整正文，不调用媒体 Provider。"
                "适用对象是未成功落盘的计划候选；图片质量审核后的新版本制作使用"
                "已有 image Tool，并从 media_versions.review_notes 取得具体修改依据。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanAttemptRetryInput,
            output_model=AssetPlanMutationSummaryResponse,
            handler=retrier.retry_asset_plan_attempt,
            tags=frozenset(
                {"asset-plan", "attempt", "patch", "retry", "handoff"}
            ),
        )
    )


__all__ = [
    "AssetPlanAttemptPatch",
    "AssetPlanAttemptPatchOperation",
    "AssetPlanAttemptRetrier",
    "AssetPlanAttemptRetryInput",
    "AssetPlanCreateInput",
    "AssetPlanGetInput",
    "AssetPlanReplaceInput",
    "AssetBriefUpdateInput",
    "AssetCatalogImportInput",
    "AssetCatalogSearchInput",
    "AssetCoverageInput",
    "AssetCreateInput",
    "AssetVersionImportLocalInput",
    "AssetVersionImportUrlInput",
    "AssetVersionReviewInput",
    "ProductionDocumentCreateInput",
    "ProductionDocumentGetInput",
    "ProductionDocumentListInput",
    "ProductionDocumentReviseInput",
    "ProductionSpecCreateInput",
    "ProductionSpecGetInput",
    "ProductionSpecReplaceInput",
    "ReferencePackEntryAddInput",
    "StockAssetImportInput",
    "StockAssetSearchInput",
    "register_asset_plan_attempt_tools",
    "register_tools",
]
