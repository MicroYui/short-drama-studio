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
from short_drama.models import (
    ProductionDocument,
    ProductionDocumentCreateRequest,
    ProductionDocumentListResponse,
    ProductionDocumentMutationResponse,
    ProductionDocumentReadResponse,
    ProductionDocumentReviseRequest,
    ProductionDocumentVersion,
)
from short_drama.production_contracts import (
    AssetPlanCreateRequest,
    AssetPlanMutationResponse,
    AssetPlanReadResponse,
    AssetPlanReplaceRequest,
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

    def get_asset_plan(
        self,
        project_id: str,
        *,
        version_id: str | None = None,
    ) -> AssetPlanReadResponse: ...

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


class AssetPlanGetInput(ToolModel):
    project_id: str
    version_id: str | None = None


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
    ) -> ToolExecution[ProductionSpecMutationResponse]:
        result = service.create_production_spec(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_production_spec",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "在完整剧本已确定、准备开始分镜和资产规划时，创建项目级"
                " ProductionSpec。它统一记录时长意图、画幅、语言、视听方向与"
                " Provider 偏好，但不会触发任何真实生成。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionSpecCreateInput,
            output_model=ProductionSpecMutationResponse,
            handler=create_production_spec,
            tags=frozenset({"production-spec", "planning", "handoff"}),
        )
    )

    def replace_production_spec(
        request: ProductionSpecReplaceInput,
    ) -> ToolExecution[ProductionSpecMutationResponse]:
        result = service.replace_production_spec(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.replace_production_spec",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "当用户意图或 Agent 的制片判断发生整体变化时，基于当前版本替换"
                " ProductionSpec 并保留历史版本；它不会静默改写既有分镜。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionSpecReplaceInput,
            output_model=ProductionSpecMutationResponse,
            handler=replace_production_spec,
            tags=frozenset({"production-spec", "revision", "handoff"}),
        )
    )

    def get_production_spec(
        request: ProductionSpecGetInput,
    ) -> ToolExecution[ProductionSpecReadResponse]:
        result = service.get_production_spec(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_production_spec_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_production_spec",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "读取当前或指定历史版本的 ProductionSpec，供分镜、资产和生成 Agent"
                " 使用同一份制片约束。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionSpecGetInput,
            output_model=ProductionSpecReadResponse,
            handler=get_production_spec,
            tags=frozenset({"production-spec", "read", "handoff"}),
        )
    )

    def create_asset_plan(
        request: AssetPlanCreateInput,
    ) -> ToolExecution[AssetPlanMutationResponse]:
        result = service.create_asset_plan(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_asset_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_asset_plan",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "基于当前精确 Script、ProjectBible、ProductionSpec 与 Storyboard，"
                "创建版本化 AssetPlan。计划显式记录哪些主体需要锚点、仅保留文字"
                "或复用既有资产，并携带稳定工作项、依赖、最终提示词和有序引用；"
                "不会调用图片 Provider。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanCreateInput,
            output_model=AssetPlanMutationResponse,
            handler=create_asset_plan,
            tags=frozenset({"asset-plan", "planning", "handoff"}),
        )
    )

    def replace_asset_plan(
        request: AssetPlanReplaceInput,
    ) -> ToolExecution[AssetPlanMutationResponse]:
        result = service.replace_asset_plan(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_asset_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.replace_asset_plan",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "基于当前版本修订 AssetPlan，保留稳定计划身份和历史版本。"
                "Agent 可按完整语义调整少数资产或整体计划；Tool 不替 Agent 做"
                "四视图、轻量参考等创意路由，也不会调用 Provider。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AssetPlanReplaceInput,
            output_model=AssetPlanMutationResponse,
            handler=replace_asset_plan,
            tags=frozenset({"asset-plan", "revision", "handoff"}),
        )
    )

    def get_asset_plan(
        request: AssetPlanGetInput,
    ) -> ToolExecution[AssetPlanReadResponse]:
        result = service.get_asset_plan(
            request.project_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_asset_plan_refs(request.project_id, result),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_asset_plan",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "读取当前或指定历史版本的 AssetPlan，供资产 Worker、图片 Worker "
                "和后续镜头输入编排共享同一份资产决策与依赖。"
            ),
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=AssetPlanGetInput,
            output_model=AssetPlanReadResponse,
            handler=get_asset_plan,
            tags=frozenset({"asset-plan", "read", "handoff"}),
        )
    )

    def create_production_document(
        request: ProductionDocumentCreateInput,
    ) -> ToolExecution[ProductionDocumentMutationResponse]:
        result = service.create_production_document(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.create_production_document",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "Create a versioned structured production document for durable "
                "cross-agent handoff."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionDocumentCreateInput,
            output_model=ProductionDocumentMutationResponse,
            handler=create_production_document,
            tags=frozenset({"production-document", "handoff", "planning"}),
        )
    )

    def revise_production_document(
        request: ProductionDocumentReviseInput,
    ) -> ToolExecution[ProductionDocumentMutationResponse]:
        result = service.revise_production_document(
            request.project_id,
            request.production_document_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.revise_production_document",
            version=1,
            category=ToolCategory.ARTIFACT,
            description=(
                "Append a structured production-document version from its current base."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ProductionDocumentReviseInput,
            output_model=ProductionDocumentMutationResponse,
            handler=revise_production_document,
            tags=frozenset({"production-document", "handoff", "revision"}),
        )
    )

    def get_production_document(
        request: ProductionDocumentGetInput,
    ) -> ToolExecution[ProductionDocumentReadResponse]:
        result = service.get_production_document(
            request.project_id,
            request.production_document_id,
            version_id=request.version_id,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="artifact.get_production_document",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="Read one current or historical production-document version.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionDocumentGetInput,
            output_model=ProductionDocumentReadResponse,
            handler=get_production_document,
            tags=frozenset({"production-document", "handoff", "read"}),
        )
    )

    def list_production_documents(
        request: ProductionDocumentListInput,
    ) -> ToolExecution[ProductionDocumentListResponse]:
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
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="artifact.list_production_documents",
            version=1,
            category=ToolCategory.ARTIFACT,
            description="List durable production documents and their current versions.",
            effect=ToolEffect.READ,
            idempotent=True,
            input_model=ProductionDocumentListInput,
            output_model=ProductionDocumentListResponse,
            handler=list_production_documents,
            tags=frozenset({"production-document", "handoff", "discovery"}),
        )
    )

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
            description=(
                "当角色、场景、道具或通用参考需要稳定 ID 与版本系谱时"
                "创建资产。只会建立结构化 Asset 和初始文本版本，不生成图片；"
                "项目视听风格仍写入 ProjectBible.style。"
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
                "当某个已批准图片版本将在后续镜头中承担明确的身份、"
                "角度、空间或道具职责时，把该精确版本加入主体 ReferencePack。"
                "接受当前已批准的 canonical 或 supplemental 图像，不会为"
                "草稿自动背书。从 canonical 派生的条目应显式指定当前"
                " lineage_root_version_id；独立来源可留空。CANONICAL_MASTER 不能伪装："
                "必须是主体资产自身当前的已批准母版。"
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=ReferencePackEntryAddInput,
            output_model=ReferencePackEntryMutationResponse,
            handler=add_reference_entry,
            tags=frozenset({"asset", "reference-pack", "shot-input"}),
        )
    )


__all__ = [
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
    "register_tools",
]
