"""Compact Agent-facing projections for versioned production artifacts.

The service and HTTP API intentionally retain complete immutable version histories.
Language-model Tools need a different projection: one selected body, exact version
identity, and no convenience copy of every historical payload.
"""

from __future__ import annotations

from pydantic import Field, JsonValue

from short_drama.storyboard_receipts import StoryboardWriteContext

from short_drama.models import (
    DomainModel,
    ProductionDocument,
    ProductionDocumentArtifactRef,
    ProductionDocumentOrigin,
    ProductionDocumentVersion,
    ProjectOutputSlot,
)
from short_drama.production_contracts import (
    AssetPlanContent,
    AssetPlanReadResponse,
    ProductionSpecContent,
    ProductionSpecReadResponse,
    SavedProjectProductionSettings,
    StoryboardContent,
    StoryboardReadResponse,
)


class CompactProductionDocument(DomainModel):
    """Stable document metadata without its immutable version history."""

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    current_version_id: str = Field(min_length=1)
    current_version: int = Field(ge=1)


class CompactProductionDocumentVersion(DomainModel):
    """Exact selected-version identity with no duplicated document body."""

    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    parent_version_id: str | None = Field(default=None, min_length=1)
    source_artifact_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )
    origin: ProductionDocumentOrigin
    created_at: str = Field(min_length=1)


class CompactProductionDocumentPayloadVersion(CompactProductionDocumentVersion):
    """Selected generic document version whose payload is the single body copy."""

    payload: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class CompactOutputSlot(DomainModel):
    """Current logical pointer without previous bindings or revision history."""

    id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    current_artifact_ref: ProductionDocumentArtifactRef | None = None
    invalidation_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    producer_agent_run_id: str | None = Field(default=None, min_length=1)
    producer_work_order_id: str | None = Field(default=None, min_length=1)
    updated_at: str = Field(min_length=1)


class ProductionArtifactMutationSummaryResponse(DomainModel):
    """Lightweight acknowledgement for a persisted document version."""

    document: CompactProductionDocument
    version: CompactProductionDocumentVersion
    output_slot: CompactOutputSlot | None = None
    project_settings: SavedProjectProductionSettings | None = None


class ProductionDocumentCompactReadResponse(DomainModel):
    """Generic document read with the selected payload present exactly once."""

    document: CompactProductionDocument
    version: CompactProductionDocumentPayloadVersion


class ProductionDocumentListItem(DomainModel):
    """One current generic document identity without a body or history."""

    document: CompactProductionDocument
    current_version: CompactProductionDocumentVersion


class ProductionDocumentCompactListResponse(DomainModel):
    """Payload-free inventory for selecting an exact document to read."""

    documents: list[ProductionDocumentListItem] = Field(
        default_factory=list[ProductionDocumentListItem]
    )


class ProductionSpecCompactReadResponse(DomainModel):
    """Typed ProductionSpec read without payload/history duplication."""

    spec: ProductionSpecContent
    document: CompactProductionDocument
    version: CompactProductionDocumentVersion
    output_slot: CompactOutputSlot | None = None


class StoryboardCompactReadResponse(DomainModel):
    """Typed Storyboard read without payload/history duplication."""

    storyboard: StoryboardContent
    document: CompactProductionDocument
    version: CompactProductionDocumentVersion
    output_slot: CompactOutputSlot | None = None


class StoryboardShotDirectoryEntry(DomainModel):
    shot_id: str
    sequence: int
    scene_id: str | None
    duration_seconds: float


class StoryboardGroupDirectoryEntry(DomainModel):
    clip_group_id: str
    shot_ids: list[str]
    duration_seconds: float


class StoryboardMutationSummary(DomainModel):
    """Content identity retained after a Storyboard write, without shot prose."""

    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    script_version_id: str = Field(min_length=1)
    bible_version_id: str = Field(min_length=1)
    production_spec_document_id: str = Field(min_length=1)
    production_spec_version_id: str = Field(min_length=1)
    computed_duration_seconds: float = Field(gt=0.0)
    scene_nodes: list[str] = Field(default_factory=list[str])
    shots: list[str] = Field(default_factory=list[str])
    clip_groups: list[str] = Field(default_factory=list[str])
    script_line_ids: list[str] = Field(default_factory=list[str])
    shot_directory: list[StoryboardShotDirectoryEntry] | None = Field(
        default=None, description="Saved shot order and design durations; absent in older receipts. No performance or audio validation implied.")
    clip_group_directory: list[StoryboardGroupDirectoryEntry] | None = Field(
        default=None, description="Saved group membership in render order and design duration; not prepared or generated video.")


class StoryboardMutationSummaryResponse(ProductionArtifactMutationSummaryResponse):
    """Lightweight canonical Storyboard acknowledgement."""

    storyboard: StoryboardMutationSummary
    write_context: StoryboardWriteContext | None = None


class AssetPlanCompactReadResponse(DomainModel):
    """Typed AssetPlan read without payload/history duplication."""

    asset_plan: AssetPlanContent
    document: CompactProductionDocument
    version: CompactProductionDocumentVersion
    output_slot: CompactOutputSlot | None = None


def compact_document(document: ProductionDocument) -> CompactProductionDocument:
    current = next(
        item for item in document.versions if item.id == document.current_version_id
    )
    return CompactProductionDocument(
        id=document.id,
        project_id=document.project_id,
        kind=document.kind,
        title=document.title,
        current_version_id=document.current_version_id,
        current_version=current.version,
    )


def compact_version(
    version: ProductionDocumentVersion,
) -> CompactProductionDocumentVersion:
    return CompactProductionDocumentVersion(
        id=version.id,
        version=version.version,
        parent_version_id=version.parent_version_id,
        source_artifact_refs=version.source_artifact_refs,
        origin=version.origin,
        created_at=version.created_at,
    )


def compact_payload_version(
    version: ProductionDocumentVersion,
) -> CompactProductionDocumentPayloadVersion:
    return CompactProductionDocumentPayloadVersion(
        **compact_version(version).model_dump(),
        payload=version.payload,
    )


def compact_output_slot(output_slot: ProjectOutputSlot | None) -> CompactOutputSlot | None:
    if output_slot is None:
        return None
    return CompactOutputSlot(
        id=output_slot.id,
        revision=output_slot.revision,
        current_artifact_ref=output_slot.current_artifact_ref,
        invalidation_reason=output_slot.invalidation_reason,
        producer_agent_run_id=output_slot.producer_agent_run_id,
        producer_work_order_id=output_slot.producer_work_order_id,
        updated_at=output_slot.updated_at,
    )


def mutation_summary(
    *,
    document: ProductionDocument,
    version: ProductionDocumentVersion,
    output_slot: ProjectOutputSlot | None = None,
    project_settings: SavedProjectProductionSettings | None = None,
) -> ProductionArtifactMutationSummaryResponse:
    return ProductionArtifactMutationSummaryResponse(
        document=compact_document(document),
        version=compact_version(version),
        output_slot=compact_output_slot(output_slot),
        project_settings=project_settings,
    )


def compact_production_document_read(
    *,
    document: ProductionDocument,
    version: ProductionDocumentVersion,
) -> ProductionDocumentCompactReadResponse:
    return ProductionDocumentCompactReadResponse(
        document=compact_document(document),
        version=compact_payload_version(version),
    )


def compact_production_document_list(
    documents: list[ProductionDocument],
) -> ProductionDocumentCompactListResponse:
    return ProductionDocumentCompactListResponse(
        documents=[
            ProductionDocumentListItem(
                document=compact_document(document),
                current_version=compact_version(
                    next(
                        version
                        for version in document.versions
                        if version.id == document.current_version_id
                    )
                ),
            )
            for document in documents
        ]
    )


def compact_production_spec_read(
    result: ProductionSpecReadResponse,
) -> ProductionSpecCompactReadResponse:
    return ProductionSpecCompactReadResponse(
        spec=result.spec,
        document=compact_document(result.document),
        version=compact_version(result.version),
        output_slot=compact_output_slot(result.output_slot),
    )


def compact_storyboard_read(
    result: StoryboardReadResponse,
) -> StoryboardCompactReadResponse:
    return StoryboardCompactReadResponse(
        storyboard=result.storyboard,
        document=compact_document(result.document),
        version=compact_version(result.version),
        output_slot=compact_output_slot(result.output_slot),
    )


def storyboard_mutation_summary(
    result: StoryboardReadResponse,
) -> StoryboardMutationSummaryResponse:
    storyboard = result.storyboard
    return StoryboardMutationSummaryResponse(
        storyboard=StoryboardMutationSummary(
            project_id=storyboard.project_id,
            title=storyboard.title,
            script_version_id=storyboard.script_version_id,
            bible_version_id=storyboard.bible_version_id,
            production_spec_document_id=storyboard.production_spec_document_id,
            production_spec_version_id=storyboard.production_spec_version_id,
            computed_duration_seconds=storyboard.computed_duration_seconds,
            scene_nodes=[item.id for item in storyboard.scene_nodes],
            shots=[item.id for item in storyboard.shots],
            clip_groups=[item.id for item in storyboard.clip_groups],
            script_line_ids=storyboard.script_line_ids,
            shot_directory=[StoryboardShotDirectoryEntry(
                shot_id=item.id, sequence=item.sequence, scene_id=item.scene_id,
                duration_seconds=item.duration_seconds,
            ) for item in storyboard.shots],
            clip_group_directory=[StoryboardGroupDirectoryEntry(
                clip_group_id=item.id, shot_ids=list(item.shot_ids),
                duration_seconds=item.duration_seconds,
            ) for item in storyboard.clip_groups],
        ),
        document=compact_document(result.document),
        version=compact_version(result.version),
        output_slot=compact_output_slot(result.output_slot),
    )


def compact_asset_plan_read(
    result: AssetPlanReadResponse,
) -> AssetPlanCompactReadResponse:
    return AssetPlanCompactReadResponse(
        asset_plan=result.asset_plan,
        document=compact_document(result.document),
        version=compact_version(result.version),
        output_slot=compact_output_slot(result.output_slot),
    )
