"""Optional one-shot registration of an explicitly chosen image, without approval."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import Field, model_validator

from short_drama.asset_plan_receipts import asset_plan_write_receipt
from short_drama.tools.contracts import ToolWriteReceipt
from short_drama.models import ApprovalStatus, Asset, AssetMediaType, AssetVersion, DomainModel, Freshness, ProductionDocumentOrigin, ProjectDocument
from short_drama.production_contracts import (
    AssetPlanFieldPatch, AssetPlanMutationResponse, AssetPlanPatchRequest, StoryboardDraftGroupReplaceRequest,
    StoryboardPatchRequest, StoryboardShotReplacement, StoryboardWorkUnitContract,
)
from short_drama.reference_planner import ReferencePackEntryCreateRequest
from short_drama.studio_models import StudioMutationResult, StudioShotSave
from short_drama.studio_references import shot_cues, sync_shot_references
from short_drama.studio_storyboard import current_draft, project_draft_shots

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class ShotMediaPlanCompletion(DomainModel):
    base_version_id: str = Field(min_length=1, description="Exact already-read AssetPlan version; unrelated target changes may merge.")
    item_ids: list[str] = Field(default_factory=list[str], max_length=32)
    work_item_ids: list[str] = Field(default_factory=list[str], max_length=32)

    @model_validator(mode="after")
    def exact_targets(self) -> Self:
        if not self.item_ids and not self.work_item_ids:
            raise ValueError("Choose the exact item_ids or work_item_ids to mark complete")
        for values in (self.item_ids, self.work_item_ids):
            if any(not value.strip() for value in values) or len(set(values)) != len(values):
                raise ValueError("Completion targets must be nonempty unique IDs")
        return self


class ShotMediaRegistration(DomainModel):
    asset_version_id: str = Field(min_length=1, description="One explicitly selected CURRENT usable DRAFT/APPROVED image used by a cue in the supplied Shot. No implicit approval or current-version selection.")
    plan_completion: ShotMediaPlanCompletion | None = None
    reference_entries: list[ReferencePackEntryCreateRequest] = Field(default_factory=list[ReferencePackEntryCreateRequest], max_length=16,
        description="Optional explicit subject, role and source lineage. Existing identical entries are reused; different saved metadata conflicts instead of being overwritten.")

    @model_validator(mode="after")
    def consistent_version(self) -> Self:
        if self.plan_completion is None and not self.reference_entries:
            raise ValueError("media_registration requires plan completion or reference entries; otherwise omit it")
        keys: set[tuple[str, str, str, str]] = set()
        for entry in self.reference_entries:
            if entry.asset_version_id != self.asset_version_id:
                raise ValueError("Every registration entry must use the explicitly selected asset_version_id")
            key = (entry.subject_asset_id, entry.role.value, entry.angle.value, entry.framing.value)
            if key in keys:
                raise ValueError("Each reference subject/role/angle/framing may be registered only once")
            keys.add(key)
        return self


class ShotMediaRegistrationSummary(DomainModel):
    asset_id: str
    asset_version_id: str
    asset_plan_document_id: str | None = None
    asset_plan_version_id: str | None = None
    asset_plan_version: int | None = None
    completed_item_ids: list[str] = Field(default_factory=list[str])
    completed_work_item_ids: list[str] = Field(default_factory=list[str])
    reference_entry_ids: list[str] = Field(default_factory=list[str])
    created_reference_entry_ids: list[str] = Field(default_factory=list[str])
    plan_changed: bool = False
    reused: bool = False


def _selected_image(service: ShortDramaService, document: ProjectDocument, version_id: str) -> tuple[Asset, AssetVersion]:
    from short_drama.service import WorkflowConflictError

    owner, version = service._find_asset_version(document, version_id)  # pyright: ignore[reportPrivateUsage]
    if (version.media_type != AssetMediaType.IMAGE or version.freshness != Freshness.CURRENT
            or version.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
            or (not version.local_path and not version.external_url)):
        raise WorkflowConflictError("Media registration requires a current usable image: " + version.id)
    if version.local_path and (not Path(version.local_path).is_file() or Path(version.local_path).stat().st_size == 0):
        raise WorkflowConflictError("Selected media file is missing or empty: " + version.id)
    return owner, version


def _plan_receipt(result: AssetPlanMutationResponse, *, changed: bool) -> ToolWriteReceipt:
    receipt = asset_plan_write_receipt(result, [])
    if not changed:
        # A replay acknowledges current truth, not the older transition that
        # originally created this already-current plan version.
        receipt.current_state["base_asset_plan_identity"] = receipt.current_state["asset_plan_identity"]
        receipt.current_state["sources_changed"] = False
        receipt.changed_ids = {key: [] for key in receipt.changed_ids}
    return receipt


def save_shot_with_media_registration(
    service: ShortDramaService, project_id: str, shot_id: str, request: StudioShotSave,
    registration: ShotMediaRegistration,
) -> tuple[StudioMutationResult, ShotMediaRegistrationSummary, ToolWriteReceipt | None]:
    from short_drama.service import WorkflowConflictError, utc_now
    from short_drama.studio_service import StudioContentConflict

    with service._lock:  # pyright: ignore[reportPrivateUsage]
        document = service._require_project_document(project_id)  # pyright: ignore[reportPrivateUsage]
        owner, version = _selected_image(service, document, registration.asset_version_id)
        if request.shot.id != shot_id or request.shot.project_id != project_id:
            raise ValueError("Shot must belong to this project and target ID")
        draft = current_draft(document)
        if draft is not None:
            project_draft_shots(document)
        current = next((shot for shot in document.shots if shot.id == shot_id), None)
        if current is None:
            raise KeyError(shot_id)
        replacement = request.shot.model_copy(deep=True)
        if not any(cue.asset_version_id == version.id for cue in shot_cues(replacement)):
            raise ValueError("The selected media must occur in the supplied Shot's explicit cues")
        summary = ShotMediaRegistrationSummary(asset_id=owner.id, asset_version_id=version.id)
        existing_entry_ids = {entry.id for pack in document.reference_packs for entry in pack.entries}
        for entry in registration.reference_entries:
            added = service._add_reference_pack_entry_document(document, entry, preserve_existing=True)  # pyright: ignore[reportPrivateUsage]
            summary.reference_entry_ids.append(added.entry.id)
            if added.entry.id not in existing_entry_ids:
                summary.created_reference_entry_ids.append(added.entry.id)
        sync_shot_references(document, replacement)
        same_shot = replacement.model_dump(exclude={"revision", "freshness"}) == current.model_dump(exclude={"revision", "freshness"})
        if current.revision != request.expected_revision and not same_shot:
            raise StudioContentConflict("此镜头已更新；未保存任何素材登记，请合并当前镜头正文。")

        changed_shots: list[str] = []
        if not same_shot:
            replacement.revision = current.revision + 1
            replacement.freshness = Freshness.CURRENT
            if draft is not None:
                source, draft_version, content = draft
                group = next(group for group in content.committed_groups if any(shot.id == shot_id for shot in group.shots))
                revised = group.model_copy(deep=True)
                revised.shots = [replacement if shot.id == shot_id else shot for shot in revised.shots]
                revised.clip_group.duration_seconds = sum(shot.duration_seconds for shot in revised.shots)
                service._replace_storyboard_draft_group_document(document, StoryboardDraftGroupReplaceRequest(  # pyright: ignore[reportPrivateUsage]
                    draft_document_id=source.id, base_version_id=draft_version.id, group=revised,
                    replaced_clip_group_id=group.clip_group.id, origin=ProductionDocumentOrigin.USER,
                    work_unit=StoryboardWorkUnitContract(id=group.work_unit_id, title="当前镜头素材登记",
                        source_range=group.source_range, objective="保存明确选择的当前镜头内容，保留其他内容。",
                        completion_criteria=["当前镜头和明确素材用途一起保存"])))
                project_draft_shots(document)
            elif document.storyboard_document_id:
                board_document = service._production_document(document, document.storyboard_document_id)  # pyright: ignore[reportPrivateUsage]
                board_version = service._production_document_version(board_document)  # pyright: ignore[reportPrivateUsage]
                board = service._parse_storyboard(board_version)  # pyright: ignore[reportPrivateUsage]
                source_shot = next((shot for shot in board.shots if shot.id == shot_id), None)
                if source_shot is not None:
                    by_id = {shot.id: replacement if shot.id == shot_id else shot for shot in board.shots}
                    groups = [group.model_copy(update={"duration_seconds": float(sum(
                        (Decimal(str(by_id[item].duration_seconds)) for item in group.shot_ids), start=Decimal(0)))})
                        if shot_id in group.shot_ids else group for group in board.clip_groups]
                    service._patch_storyboard_document(document, StoryboardPatchRequest(  # pyright: ignore[reportPrivateUsage]
                        base_version_id=board_version.id, clip_groups=groups,
                        replacements=[StoryboardShotReplacement(shot_id=shot_id, expected_revision=source_shot.revision,
                            replacement=replacement)], origin=ProductionDocumentOrigin.USER), current_content_edit=True)
            document.shots = [replacement if shot.id == shot_id else shot for shot in document.shots]
            sync_shot_references(document, replacement)
            changed_shots = [shot_id]

        plan_receipt: ToolWriteReceipt | None = None
        completion_request: AssetPlanPatchRequest | None = None
        completion = registration.plan_completion
        if completion is not None:
            patches: list[AssetPlanFieldPatch] = []
            for ids, key in ((completion.item_ids, "item_id"), (completion.work_item_ids, "work_item_id")):
                for identifier in ids:
                    for field, value in (("completed_asset_id", owner.id), ("completed_asset_version_id", version.id)):
                        patches.append(AssetPlanFieldPatch.model_validate({key: identifier, "op": "replace", "path": "/" + field, "value": value}))
            before_plan = service._production_document(document, document.asset_plan_document_id or "").current_version_id  # pyright: ignore[reportPrivateUsage]
            completion_request = AssetPlanPatchRequest(
                base_version_id=completion.base_version_id, field_patches=patches, origin=ProductionDocumentOrigin.AGENT)
            plan_result = service._patch_asset_plan_document(document, completion_request, skip_unchanged=True)  # pyright: ignore[reportPrivateUsage]
            summary.asset_plan_document_id = plan_result.document.id
            summary.asset_plan_version_id = plan_result.version.id
            summary.asset_plan_version = plan_result.version.version
            summary.completed_item_ids = list(completion.item_ids)
            summary.completed_work_item_ids = list(completion.work_item_ids)
            summary.plan_changed = before_plan != plan_result.version.id
            plan_receipt = _plan_receipt(plan_result, changed=summary.plan_changed)
        summary.reused = not (changed_shots or summary.plan_changed or summary.created_reference_entry_ids)

        def validate_merged(merged: ProjectDocument) -> None:
            nonlocal plan_receipt
            # The merge protects writes. Recheck the small set of actual read
            # dependencies against its result before any content is committed.
            # Work on a copy: validation must not re-resolve unrelated cues or
            # mutate a user's current content as a side effect.
            checked = merged.model_copy(deep=True)
            actual_owner, actual_version = _selected_image(service, checked, version.id)
            if actual_owner.id != owner.id:
                raise WorkflowConflictError("Selected media ownership changed during registration: " + version.id)
            if (actual_version.local_path, actual_version.external_url) != (version.local_path, version.external_url):
                raise WorkflowConflictError("Selected media source changed during registration: " + version.id)
            if current_draft(checked) is not None:
                project_draft_shots(checked)
            actual_shot = next((shot for shot in checked.shots if shot.id == shot_id), None)
            if actual_shot is None or not any(cue.asset_version_id == version.id for cue in shot_cues(actual_shot)):
                raise WorkflowConflictError("Selected media is no longer used by the current Shot: " + shot_id)
            sync_shot_references(checked, actual_shot)
            actual_entry_ids: list[str] = []
            for entry_request in registration.reference_entries:
                matches = [entry for pack in checked.reference_packs
                    if pack.subject_asset_id == entry_request.subject_asset_id for entry in pack.entries
                    if (entry.asset_version_id, entry.role, entry.angle, entry.framing) == (
                        entry_request.asset_version_id, entry_request.role, entry_request.angle, entry_request.framing)]
                if len(matches) != 1:
                    raise WorkflowConflictError("Reference registration changed concurrently; retry against the current entry: "
                        + entry_request.subject_asset_id + "/" + entry_request.asset_version_id)
                checked_entry = service._add_reference_pack_entry_document(checked, entry_request, preserve_existing=True)  # pyright: ignore[reportPrivateUsage]
                actual_entry_ids.append(checked_entry.entry.id)
            summary.reference_entry_ids = actual_entry_ids
            summary.created_reference_entry_ids = [identifier for identifier in summary.created_reference_entry_ids
                if identifier in actual_entry_ids]
            if completion_request is not None:
                current_plan = service._production_document(checked, checked.asset_plan_document_id or "")  # pyright: ignore[reportPrivateUsage]
                current_plan_id = current_plan.current_version_id
                verified = service._patch_asset_plan_document(checked, completion_request, skip_unchanged=True)  # pyright: ignore[reportPrivateUsage]
                if verified.version.id != current_plan_id:
                    raise WorkflowConflictError("Selected AssetPlan completion changed during registration")
                summary.asset_plan_document_id = verified.document.id
                summary.asset_plan_version_id = verified.version.id
                summary.asset_plan_version = verified.version.version
                plan_receipt = _plan_receipt(verified, changed=summary.plan_changed)

        if not summary.reused:
            document.project.updated_at = utc_now()
        service.repository.save_shot_media_registration(document, changed_shots,
            validate_merged=validate_merged, write_content=not summary.reused)
        result = StudioMutationResult(project=service.studio.current_project(project_id), changed_shot_ids=changed_shots,
            notices=["镜头与所选素材登记已一起保存。" if not summary.reused else "相同内容已保存，复用实际结果。"])
        return result, summary, plan_receipt
