"""Deterministic compiler for Agent-selected AssetPlan execution semantics.

The Agent chooses canonical roots, supplemental outputs, and exact text surfaces.
This module translates those choices into the linked low-level AssetPlan contract;
it does not make creative routing decisions or call a media Provider.
"""

from __future__ import annotations

from dataclasses import dataclass

from short_drama.image_models import AssetReviewPurpose
from short_drama.models import (
    AssetKind,
    AssetLayoutProfile,
    AssetPresentationMode,
    AssetReferenceStrategy,
    AssetStandardView,
    CoreAssetBrief,
    ImageReferenceAuthority,
    ImageReferenceRole,
    ReferenceAngle,
    ReferenceFraming,
    ReferenceRole,
)
from short_drama.production_contracts import (
    AssetPlanAppliedWorkExecutionOverride,
    AssetPlanContent,
    AssetPlanDisposition,
    AssetPlanExecutionContractIssue,
    AssetPlanExecutionContractIssueCode,
    AssetPlanExecutionContractReview,
    AssetPlanExecutionReconcileRequest,
    AssetPlanItem,
    AssetPlanOperation,
    AssetPlanReference,
    AssetPlanReferencePackIntent,
    AssetPlanReferencePackOverride,
    AssetPlanTextPlateIntent,
    AssetPlanTextPlateSpec,
    AssetPlanWorkExecutionOverride,
    AssetPlanWorkItem,
)


class AssetPlanReconcileError(ValueError):
    """The semantic request cannot be compiled against the selected AssetPlan."""


@dataclass(frozen=True)
class AssetPlanReconcileResult:
    asset_plan: AssetPlanContent
    canonicalized_item_ids: list[str]
    canonical_work_item_ids: list[str]
    supplemental_work_item_ids: list[str]
    created_text_plate_item_ids: list[str]
    removed_text_work_item_ids: list[str]
    created_physical_master_work_item_ids: list[str]
    normalized_layout_item_ids: list[str]
    applied_execution_overrides: list[AssetPlanAppliedWorkExecutionOverride]


def _output_layout_accepts_size(
    layout_profile: AssetLayoutProfile,
    size: str,
) -> bool:
    """Mirror the workbench's output-level layout constraint without provider I/O."""

    try:
        raw_width, raw_height = size.lower().split("x", maxsplit=1)
        width, height = int(raw_width), int(raw_height)
    except ValueError:
        return False
    if layout_profile in {
        AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
        AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
    }:
        return width * 9 == height * 16
    if layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2:
        return width * 2 == height * 3
    if layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE:
        return width == height
    return True


def _apply_work_execution_override(
    item: AssetPlanItem,
    override: AssetPlanWorkExecutionOverride,
) -> tuple[AssetPlanItem, AssetPlanAppliedWorkExecutionOverride]:
    work_index = next(
        (
            index
            for index, work in enumerate(item.work_items)
            if work.id == override.work_item_id
        ),
        None,
    )
    if work_index is None:
        raise AssetPlanReconcileError(
            "UNKNOWN_WORK_ITEM: "
            f"{override.work_item_id} does not belong to item {override.item_id}"
        )
    current = item.work_items[work_index]
    updates: dict[str, object] = {}
    if override.size is not None:
        updates["size"] = override.size
    if override.output_layout_profile is not None:
        updates["output_layout_profile"] = override.output_layout_profile
    if current.operation == AssetPlanOperation.COMPOSE:
        assert current.compose_intent is not None
        compose_updates: dict[str, object] = {}
        if override.size is not None:
            compose_updates["target_size"] = override.size
        if override.output_layout_profile is not None:
            compose_updates["layout_profile"] = override.output_layout_profile
        updates["compose_intent"] = current.compose_intent.model_copy(
            update=compose_updates,
            deep=True,
        )
    updated = current.model_copy(update=updates, deep=True)
    try:
        updated = AssetPlanWorkItem.model_validate(updated.model_dump(mode="json"))
    except ValueError as exc:
        raise AssetPlanReconcileError(
            "INVALID_WORK_EXECUTION_OVERRIDE: "
            f"{override.item_id}/{override.work_item_id}: {exc}"
        ) from exc
    resolved_layout = updated.resolved_output_layout_profile()
    if not _output_layout_accepts_size(resolved_layout, updated.size):
        raise AssetPlanReconcileError(
            "OUTPUT_LAYOUT_WORK_SIZE_CONFLICT: "
            f"{override.item_id}/{override.work_item_id} uses "
            f"{resolved_layout.value} with {updated.size}"
        )
    work_items = list(item.work_items)
    work_items[work_index] = updated
    compose_intent = updated.compose_intent
    return (
        item.model_copy(update={"work_items": work_items}, deep=True),
        AssetPlanAppliedWorkExecutionOverride(
            item_id=item.id,
            work_item_id=updated.id,
            size=updated.size,
            output_layout_profile=resolved_layout,
            compose_target_size=(
                None if compose_intent is None else compose_intent.target_size
            ),
            compose_layout_profile=(
                None if compose_intent is None else compose_intent.layout_profile
            ),
        ),
    )


def assess_asset_plan_execution_contract(
    plan: AssetPlanContent,
) -> AssetPlanExecutionContractReview:
    """Expose current execution mismatches so the Agent can choose the repair.

    This is deliberately an inspection, not a migration gate.  It reports facts
    that the current workbench/reconciler semantics cannot execute faithfully;
    the Leader still decides whether to delegate a Planner and which semantic
    reconciliation request to author.
    """

    issues: list[AssetPlanExecutionContractIssue] = []
    for item in plan.items:
        conflicting_work_ids = [
            work.id
            for work in item.work_items
            if work.operation
            in {
                AssetPlanOperation.GENERATE,
                AssetPlanOperation.REFERENCE_GENERATE,
                AssetPlanOperation.EDIT,
                AssetPlanOperation.COMPOSE,
            }
            and not _output_layout_accepts_size(
                work.resolved_output_layout_profile(),
                work.size,
            )
        ]
        if conflicting_work_ids:
            issues.append(
                AssetPlanExecutionContractIssue(
                    code=(
                        AssetPlanExecutionContractIssueCode.OUTPUT_LAYOUT_WORK_SIZE_CONFLICT
                    ),
                    item_id=item.id,
                    work_item_ids=conflicting_work_ids,
                    detail=(
                        "Stored work-item output layout and size have different "
                        "aspect ratios; base-image output layout is independent from "
                        "the parent asset brief and any composed reference sheet."
                    ),
                )
            )

        canonical_work = [
            work
            for work in item.work_items
            if work.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
        ]
        supplemental_work = [
            work
            for work in item.work_items
            if work.review_purpose == AssetReviewPurpose.SUPPLEMENTAL_REFERENCE
        ]
        canonical = canonical_work[0] if len(canonical_work) == 1 else None
        canonical_is_executable_root = (
            canonical is not None
            and canonical.operation
            not in {AssetPlanOperation.COMPOSE, AssetPlanOperation.TEXT_PLATE}
        )
        if (
            supplemental_work
            and item.completed_asset_id is None
            and not canonical_is_executable_root
        ):
            issues.append(
                AssetPlanExecutionContractIssue(
                    code=(
                        AssetPlanExecutionContractIssueCode.CANONICAL_ROOT_REQUIRES_RECONCILIATION
                    ),
                    item_id=item.id,
                    work_item_ids=[
                        *[work.id for work in canonical_work],
                        *[work.id for work in supplemental_work],
                    ],
                    detail=(
                        "Supplemental outputs do not have one executable identity or "
                        "physical canonical root; a composed sheet or text plate is "
                        "a derived output rather than that root."
                    ),
                )
            )
        if canonical_is_executable_root or item.completed_asset_id is not None:
            incompatible_supplemental_ids: list[str] = []
            for work in supplemental_work:
                intent = work.reference_pack_intent
                lineage = None if intent is None else intent.lineage_root
                lineage_matches_completed_item = (
                    item.completed_asset_id is not None
                    and item.completed_asset_version_id is not None
                    and lineage is not None
                    and lineage.asset_id == item.completed_asset_id
                    and lineage.asset_version_id == item.completed_asset_version_id
                )
                lineage_matches_canonical = (
                    canonical is not None
                    and lineage is not None
                    and lineage.source_work_item_id == canonical.id
                    and canonical.id in work.dependency_ids
                )
                if (
                    intent is None
                    or intent.subject_item_id != item.id
                    or not (
                        lineage_matches_completed_item
                        or lineage_matches_canonical
                    )
                    or work.operation == AssetPlanOperation.GENERATE
                ):
                    incompatible_supplemental_ids.append(work.id)
            if incompatible_supplemental_ids:
                issues.append(
                    AssetPlanExecutionContractIssue(
                        code=(
                            AssetPlanExecutionContractIssueCode.SUPPLEMENTAL_LINEAGE_REQUIRES_RECONCILIATION
                        ),
                        item_id=item.id,
                        work_item_ids=incompatible_supplemental_ids,
                        detail=(
                            "Supplemental outputs are not explicitly derived from the "
                            "subject's canonical work item or explicitly selected completed version."
                        ),
                    )
                )

        text_work = [
            work
            for work in item.work_items
            if work.operation == AssetPlanOperation.TEXT_PLATE
        ]
        if (
            item.asset_kind == AssetKind.PROP
            and text_work
            and not (
                item.brief is not None
                and item.brief.presentation_mode == AssetPresentationMode.PHYSICAL_MASTER
                and item.brief.required_visible_text
            )
            and any(
                work.operation != AssetPlanOperation.TEXT_PLATE
                for work in item.work_items
            )
        ):
            issues.append(
                AssetPlanExecutionContractIssue(
                    code=(
                        AssetPlanExecutionContractIssueCode.TEXT_SURFACE_REQUIRES_SEPARATION
                    ),
                    item_id=item.id,
                    work_item_ids=[work.id for work in item.work_items],
                    detail=(
                        "A PROP item currently carries exact TEXT_PLATE content in the "
                        "same semantic asset as its physical carrier; the Agent must "
                        "decide whether to split the carrier master and screen-content "
                        "plate."
                    ),
                )
            )

    return AssetPlanExecutionContractReview(
        needs_reconciliation=bool(issues),
        issues=issues,
    )


def _reference_role_for_kind(kind: AssetKind) -> ImageReferenceRole:
    return {
        AssetKind.CHARACTER: ImageReferenceRole.SUBJECT_IDENTITY,
        AssetKind.LOCATION: ImageReferenceRole.SCENE_IDENTITY,
        AssetKind.PROP: ImageReferenceRole.PROP_IDENTITY,
    }.get(kind, ImageReferenceRole.SUPPORTING_VISUAL)


def _inferred_pack_semantics(
    kind: AssetKind,
    work_item: AssetPlanWorkItem,
) -> tuple[ReferenceRole, ReferenceAngle, ReferenceFraming, list[str]]:
    view = work_item.output_view
    mapping: dict[
        AssetStandardView,
        tuple[ReferenceRole, ReferenceAngle, ReferenceFraming, list[str]],
    ] = {
        AssetStandardView.CHARACTER_FRONT: (
            ReferenceRole.CHARACTER_BODY,
            ReferenceAngle.FRONT,
            ReferenceFraming.FULL_BODY,
            ["identity", "front"],
        ),
        AssetStandardView.CHARACTER_LEFT_PROFILE: (
            ReferenceRole.CHARACTER_PROFILE,
            ReferenceAngle.LEFT_PROFILE,
            ReferenceFraming.FULL_BODY,
            ["identity", "left-profile"],
        ),
        AssetStandardView.CHARACTER_RIGHT_PROFILE: (
            ReferenceRole.CHARACTER_PROFILE,
            ReferenceAngle.RIGHT_PROFILE,
            ReferenceFraming.FULL_BODY,
            ["identity", "right-profile"],
        ),
        AssetStandardView.CHARACTER_BACK: (
            ReferenceRole.CHARACTER_BACK,
            ReferenceAngle.BACK,
            ReferenceFraming.FULL_BODY,
            ["identity", "back"],
        ),
        AssetStandardView.CHARACTER_WARDROBE_DETAIL: (
            ReferenceRole.CHARACTER_BODY,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.DETAIL,
            ["identity", "wardrobe"],
        ),
        AssetStandardView.CHARACTER_SHEET: (
            ReferenceRole.CHARACTER_REFERENCE_SHEET,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
            ["identity", "character-sheet"],
        ),
        AssetStandardView.SCENE_MASTER: (
            ReferenceRole.LOCATION_MASTER,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
            ["location", "master"],
        ),
        AssetStandardView.SCENE_FRONT: (
            ReferenceRole.LOCATION_MASTER,
            ReferenceAngle.FRONT,
            ReferenceFraming.WIDE,
            ["location", "front"],
        ),
        AssetStandardView.SCENE_OVERHEAD: (
            ReferenceRole.LOCATION_INTERACTION,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
            ["location", "overhead"],
        ),
        AssetStandardView.SCENE_REVERSE: (
            ReferenceRole.LOCATION_REVERSE,
            ReferenceAngle.BACK,
            ReferenceFraming.WIDE,
            ["location", "reverse"],
        ),
        AssetStandardView.SCENE_LEFT: (
            ReferenceRole.LOCATION_REVERSE,
            ReferenceAngle.LEFT_PROFILE,
            ReferenceFraming.WIDE,
            ["location", "left"],
        ),
        AssetStandardView.SCENE_RIGHT: (
            ReferenceRole.LOCATION_REVERSE,
            ReferenceAngle.RIGHT_PROFILE,
            ReferenceFraming.WIDE,
            ["location", "right"],
        ),
        AssetStandardView.SCENE_INTERACTION_ZONE: (
            ReferenceRole.LOCATION_INTERACTION,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.WIDE,
            ["location", "interaction-zone"],
        ),
        AssetStandardView.PROP_PRODUCT: (
            ReferenceRole.PROP_PRODUCT,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.DETAIL,
            ["prop", "product"],
        ),
        AssetStandardView.PROP_IN_HAND: (
            ReferenceRole.PROP_IN_HAND,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.DETAIL,
            ["prop", "in-hand"],
        ),
        AssetStandardView.PROP_STATE: (
            ReferenceRole.PROP_STATE,
            ReferenceAngle.NOT_APPLICABLE,
            ReferenceFraming.DETAIL,
            ["prop", "state"],
        ),
    }
    inferred = mapping.get(view) if view is not None else None
    if inferred is None:
        raise AssetPlanReconcileError(
            f"AMBIGUOUS_PACK_ROLE: work item {work_item.id} needs an explicit "
            "pack override"
        )
    allowed_by_kind = {
        AssetKind.CHARACTER: {
            ReferenceRole.CHARACTER_FACE,
            ReferenceRole.CHARACTER_BODY,
            ReferenceRole.CHARACTER_PROFILE,
            ReferenceRole.CHARACTER_BACK,
            ReferenceRole.CHARACTER_REFERENCE_SHEET,
        },
        AssetKind.LOCATION: {
            ReferenceRole.LOCATION_MASTER,
            ReferenceRole.LOCATION_REVERSE,
            ReferenceRole.LOCATION_INTERACTION,
        },
        AssetKind.PROP: {
            ReferenceRole.PROP_PRODUCT,
            ReferenceRole.PROP_IN_HAND,
            ReferenceRole.PROP_STATE,
        },
    }
    if inferred[0] not in allowed_by_kind.get(kind, set()):
        raise AssetPlanReconcileError(
            f"INCOMPATIBLE_PACK_ROLE: {view.value if view else 'NONE'} is not valid "
            f"for {kind.value} item"
        )
    return inferred


def _canonical_pack(
    item: AssetPlanItem,
    work_item: AssetPlanWorkItem,
) -> AssetPlanReferencePackIntent:
    current = work_item.reference_pack_intent
    angle = ReferenceAngle.NOT_APPLICABLE if current is None else current.angle
    framing = ReferenceFraming.WIDE if current is None else current.framing
    tags = ["canonical-master"] if current is None else list(current.tags)
    return AssetPlanReferencePackIntent(
        subject_item_id=item.id,
        role=ReferenceRole.CANONICAL_MASTER,
        angle=angle,
        framing=framing,
        tags=tags,
        lineage_root=None,
    )


def _supplemental_pack(
    item: AssetPlanItem,
    work_item: AssetPlanWorkItem,
    canonical_work_item_id: str,
    override: AssetPlanReferencePackOverride | None,
) -> AssetPlanReferencePackIntent:
    if override is not None:
        role = override.role
        angle = override.angle
        framing = override.framing
        tags = list(override.tags)
    else:
        current = work_item.reference_pack_intent
        if current is not None and current.role != ReferenceRole.CANONICAL_MASTER:
            role = current.role
            angle = current.angle
            framing = current.framing
            tags = list(current.tags)
        else:
            role, angle, framing, tags = _inferred_pack_semantics(
                item.asset_kind,
                work_item,
            )
    if role == ReferenceRole.CANONICAL_MASTER:
        raise AssetPlanReconcileError(
            f"INCOMPATIBLE_PACK_ROLE: supplemental work item {work_item.id} "
            "cannot use CANONICAL_MASTER"
        )
    reference_role = _reference_role_for_kind(item.asset_kind)
    return AssetPlanReferencePackIntent(
        subject_item_id=item.id,
        role=role,
        angle=angle,
        framing=framing,
        tags=tags,
        lineage_root=AssetPlanReference(
            role="identity_lineage_root",
            purpose="将补充参考归属到同一主体的 canonical 母版。",
            reference_role=reference_role,
            reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
            source_work_item_id=canonical_work_item_id,
        ),
    )


def _with_canonical_reference(
    item: AssetPlanItem,
    work_item: AssetPlanWorkItem,
    canonical_work_item_id: str,
) -> list[AssetPlanReference]:
    reference = AssetPlanReference(
        role="identity_master",
        purpose="锁定同一主体的 canonical 身份、空间或道具外观。",
        reference_role=_reference_role_for_kind(item.asset_kind),
        reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
        source_work_item_id=canonical_work_item_id,
    )
    return [reference, *[
        existing.model_copy(deep=True)
        for existing in work_item.ordered_references
        if existing.source_work_item_id != canonical_work_item_id
    ]]


def _reconcile_subject(
    item: AssetPlanItem,
    *,
    canonical_work_item_id: str,
    supplemental_work_item_ids: list[str],
    overrides: list[AssetPlanReferencePackOverride],
) -> AssetPlanItem:
    if item.asset_kind not in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}:
        raise AssetPlanReconcileError(
            f"INCOMPATIBLE_SUBJECT_KIND: item {item.id} cannot own a ReferencePack"
        )
    work_by_id = {work.id: work for work in item.work_items}
    selected_ids = {canonical_work_item_id, *supplemental_work_item_ids}
    unknown = sorted(selected_ids - set(work_by_id))
    if unknown:
        raise AssetPlanReconcileError(
            f"UNKNOWN_WORK_ITEM: item {item.id} has no work item " + ", ".join(unknown)
        )
    canonical = work_by_id[canonical_work_item_id]
    if canonical.operation in {
        AssetPlanOperation.COMPOSE,
        AssetPlanOperation.TEXT_PLATE,
    }:
        raise AssetPlanReconcileError(
            f"INVALID_CANONICAL_ROOT: work item {canonical.id} must be an identity "
            "or physical master, not a composed/text plate output"
        )
    if set(canonical.dependency_ids).intersection(supplemental_work_item_ids):
        raise AssetPlanReconcileError(
            f"INVALID_CANONICAL_ROOT: work item {canonical.id} depends on a selected "
            "supplemental output"
        )
    other_canonicals = [
        work.id
        for work in item.work_items
        if work.id != canonical.id
        and work.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
        and work.id not in supplemental_work_item_ids
    ]
    if other_canonicals:
        raise AssetPlanReconcileError(
            "MULTIPLE_CANONICALS: include the old canonical work in supplemental_work_item_ids: "
            + ", ".join(other_canonicals)
        )

    override_by_id = {override.work_item_id: override for override in overrides}
    reconciled: dict[str, AssetPlanWorkItem] = {}
    reconciled[canonical.id] = canonical.model_copy(
        update={
            "review_purpose": AssetReviewPurpose.CANONICAL_MASTER,
            "reference_pack_intent": _canonical_pack(item, canonical),
            "output_layout_profile": AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
        },
        deep=True,
    )
    for work_item_id in supplemental_work_item_ids:
        current = work_by_id[work_item_id]
        if current.operation == AssetPlanOperation.TEXT_PLATE:
            raise AssetPlanReconcileError(
                f"INCOMPATIBLE_SUPPLEMENTAL: text plate {current.id} must be an "
                "independent screen-content item"
            )
        operation = current.operation
        ordered_references = list(current.ordered_references)
        if operation == AssetPlanOperation.GENERATE:
            operation = AssetPlanOperation.REFERENCE_GENERATE
        if operation in {
            AssetPlanOperation.REFERENCE_GENERATE,
            AssetPlanOperation.EDIT,
        }:
            ordered_references = _with_canonical_reference(
                item,
                current,
                canonical.id,
            )
        output_view = current.output_view
        if current.operation == AssetPlanOperation.COMPOSE and item.asset_kind == AssetKind.CHARACTER:
            output_view = AssetStandardView.CHARACTER_SHEET
        output_layout_profile = (
            (
                current.compose_intent.layout_profile
                or AssetLayoutProfile.LIGHTWEIGHT_SINGLE
            )
            if current.operation == AssetPlanOperation.COMPOSE
            and current.compose_intent is not None
            else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
        )
        reconciled[current.id] = current.model_copy(
            update={
                "operation": operation,
                "output_view": output_view,
                "output_layout_profile": output_layout_profile,
                "review_purpose": AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
                "reference_pack_intent": _supplemental_pack(
                    item,
                    current.model_copy(update={"output_view": output_view}),
                    canonical.id,
                    override_by_id.get(current.id),
                ),
                "ordered_references": ordered_references,
                "dependency_ids": [
                    canonical.id,
                    *[
                        dependency_id
                        for dependency_id in current.dependency_ids
                        if dependency_id != canonical.id
                    ],
                ],
            },
            deep=True,
        )

    ordered_work_items = [reconciled[canonical.id]]
    ordered_work_items.extend(
        reconciled.get(work.id, work).model_copy(deep=True)
        for work in item.work_items
        if work.id != canonical.id
    )
    return item.model_copy(
        update={"work_items": ordered_work_items},
        deep=True,
    )


def _is_square_size(size: str) -> bool:
    try:
        width, height = (int(value) for value in size.lower().split("x", maxsplit=1))
    except ValueError as exc:
        raise AssetPlanReconcileError(
            f"INVALID_PHYSICAL_MASTER_SIZE: {size} must use WIDTHxHEIGHT"
        ) from exc
    return width == height


def _physical_brief(
    item: AssetPlanItem,
    master: AssetPlanWorkItem,
) -> CoreAssetBrief:
    requested_views = [] if master.output_view is None else [master.output_view]
    if item.brief is None:
        return CoreAssetBrief(
            identity_anchor=f"{item.name}的无字物理载体母版。",
            visual_attributes=["主体外形完整", "材质与结构清晰"],
            continuity_anchors=["载体尺寸、材质和边缘结构保持一致"],
            presentation_mode=AssetPresentationMode.PHYSICAL_MASTER,
            required_visible_text=[],
            reference_strategy=AssetReferenceStrategy.LIGHTWEIGHT,
            layout_profile=(
                AssetLayoutProfile.PROP_MASTER_SINGLE
                if _is_square_size(master.size)
                else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
            ),
            requested_views=requested_views,
            strategy_rationale="物理载体与精确文字内容分层生成。",
        )
    return item.brief.model_copy(
        update={
            "presentation_mode": AssetPresentationMode.PHYSICAL_MASTER,
            "required_visible_text": [],
            "reference_strategy": AssetReferenceStrategy.LIGHTWEIGHT,
            "layout_profile": (
                AssetLayoutProfile.PROP_MASTER_SINGLE
                if _is_square_size(master.size)
                else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
            ),
            "requested_views": requested_views,
        },
        deep=True,
    )


def _plate_item(spec: AssetPlanTextPlateSpec) -> AssetPlanItem:
    request = spec.to_compose_request(size=spec.size, descriptor=spec.name)
    visible_text = (request.content_state.required_visible_text if request.base_version_id is not None
        and request.content_state is not None else request.required_visible_text())
    return AssetPlanItem(
        id=spec.item_id,
        disposition=AssetPlanDisposition.ANCHOR,
        asset_kind=AssetKind.REFERENCE,
        name=spec.name,
        serves_shot_ids=list(spec.serves_shot_ids),
        serves_clip_group_ids=list(spec.serves_clip_group_ids),
        brief=CoreAssetBrief(
            identity_anchor=f"{spec.name}的独立精确文字图版。",
            visual_attributes=["确定性排版", "文字准确且清晰可读"],
            background_constraints=[spec.background_color],
            continuity_anchors=["逐字保持精确文案，不由生成模型改写"],
            presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
            required_visible_text=visible_text,
            reference_strategy=AssetReferenceStrategy.LIGHTWEIGHT,
            layout_profile=AssetLayoutProfile.LIGHTWEIGHT_SINGLE,
            requested_views=[],
            strategy_rationale="精确文字使用本地确定性渲染，与物理载体分层。",
        ),
        rationale=spec.rationale,
        work_items=[
            AssetPlanWorkItem(
                id=spec.work_item_id,
                operation=AssetPlanOperation.TEXT_PLATE,
                output_role=spec.output_role,
                output_view=None,
                final_prompt=(
                    "本地确定性渲染精确文字图版；不得改写文案。"
                    + spec.layout_description
                ),
                size=spec.size,
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                text_plate_intent=AssetPlanTextPlateIntent.model_validate(
                    spec.model_dump(include=set(AssetPlanTextPlateIntent.model_fields))
                ),
            )
        ],
    )


def reconcile_asset_plan_execution_contract(
    current: AssetPlanContent,
    request: AssetPlanExecutionReconcileRequest,
) -> AssetPlanReconcileResult:
    """Compile one Agent-authored semantic reconciliation into a valid plan."""

    item_by_id = {item.id: item.model_copy(deep=True) for item in current.items}
    work_item_owners = {
        work.id: item.id for item in current.items for work in item.work_items
    }
    global_work_ids = set(work_item_owners)
    normalized_layout_item_ids: list[str] = []
    canonicalized_item_ids: list[str] = []
    canonical_work_item_ids: list[str] = []
    supplemental_work_item_ids: list[str] = []

    for subject in request.subjects:
        item = item_by_id.get(subject.item_id)
        if item is None:
            raise AssetPlanReconcileError(
                f"UNKNOWN_ITEM: {subject.item_id}"
            )
        outside_subject = [
            f"{work_id} belongs to item {work_item_owners[work_id]}"
            for work_id in [subject.canonical_work_item_id, *subject.supplemental_work_item_ids]
            if work_id in work_item_owners and work_item_owners[work_id] != item.id
        ]
        if outside_subject:
            raise AssetPlanReconcileError(
                f"WORK_ITEM_OUTSIDE_SUBJECT: subject item {item.id}; "
                + "; ".join(outside_subject)
                + ". Reconcile only selects this item's own work_items. "
                "Use artifact.patch_asset_plan_fields for explicit cross-item "
                "reference changes; this operation does not move work items."
            )
        reconciled = _reconcile_subject(
            item,
            canonical_work_item_id=subject.canonical_work_item_id,
            supplemental_work_item_ids=subject.supplemental_work_item_ids,
            overrides=subject.pack_overrides,
        )
        item_by_id[item.id] = reconciled
        canonicalized_item_ids.append(item.id)
        canonical_work_item_ids.append(subject.canonical_work_item_id)
        supplemental_work_item_ids.extend(subject.supplemental_work_item_ids)

    created_plate_items: list[AssetPlanItem] = []
    removed_text_work_item_ids: list[str] = []
    created_physical_master_work_item_ids: list[str] = []
    created_item_ids: set[str] = set()
    created_work_ids: set[str] = set()
    for surface in request.text_surfaces:
        item = item_by_id.get(surface.carrier_item_id)
        if item is None:
            raise AssetPlanReconcileError(
                f"UNKNOWN_ITEM: {surface.carrier_item_id}"
            )
        if item.asset_kind != AssetKind.PROP:
            raise AssetPlanReconcileError(
                f"INCOMPATIBLE_CARRIER_KIND: {item.id} must be a PROP item"
            )
        work_by_id = {work.id: work for work in item.work_items}
        unknown_removed = sorted(
            set(surface.remove_text_work_item_ids) - set(work_by_id)
        )
        if unknown_removed:
            raise AssetPlanReconcileError(
                "UNKNOWN_WORK_ITEM: " + ", ".join(unknown_removed)
            )
        invalid_removed = [
            work_id
            for work_id in surface.remove_text_work_item_ids
            if work_by_id[work_id].operation != AssetPlanOperation.TEXT_PLATE
        ]
        if invalid_removed:
            raise AssetPlanReconcileError(
                "INCOMPATIBLE_REMOVAL: only TEXT_PLATE work can move off a carrier: "
                + ", ".join(invalid_removed)
            )
        remaining = [
            work.model_copy(deep=True)
            for work in item.work_items
            if work.id not in surface.remove_text_work_item_ids
        ]
        if surface.create_carrier_master is not None:
            create = surface.create_carrier_master
            if create.work_item_id in global_work_ids or create.work_item_id in created_work_ids:
                raise AssetPlanReconcileError(
                    f"DUPLICATE_WORK_ITEM: {create.work_item_id}"
                )
            master = AssetPlanWorkItem(
                id=create.work_item_id,
                operation=AssetPlanOperation.GENERATE,
                output_role=create.output_role,
                output_view=create.output_view,
                final_prompt=create.final_prompt,
                size=create.size,
                output_layout_profile=(
                    AssetLayoutProfile.PROP_MASTER_SINGLE
                    if _is_square_size(create.size)
                    else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
                ),
                review_purpose=AssetReviewPurpose.CANONICAL_MASTER,
                reference_pack_intent=AssetPlanReferencePackIntent(
                    subject_item_id=item.id,
                    role=ReferenceRole.CANONICAL_MASTER,
                    angle=ReferenceAngle.NOT_APPLICABLE,
                    framing=ReferenceFraming.DETAIL,
                    tags=["physical-master"],
                ),
            )
            created_work_ids.add(master.id)
            created_physical_master_work_item_ids.append(master.id)
            remaining.insert(0, master)
        else:
            assert surface.carrier_master_work_item_id is not None
            master = next(
                (
                    work
                    for work in remaining
                    if work.id == surface.carrier_master_work_item_id
                ),
                None,
            )
            if master is None:
                raise AssetPlanReconcileError(
                    f"MISSING_PHYSICAL_MASTER: {surface.carrier_master_work_item_id}"
                )
            assert master is not None
            if master.operation in {
                AssetPlanOperation.TEXT_PLATE,
                AssetPlanOperation.COMPOSE,
            }:
                raise AssetPlanReconcileError(
                    f"MISSING_PHYSICAL_MASTER: {master.id} is not a physical master"
                )
            master = master.model_copy(
                update={
                    "review_purpose": AssetReviewPurpose.CANONICAL_MASTER,
                    "reference_pack_intent": _canonical_pack(item, master),
                    "output_layout_profile": (
                        AssetLayoutProfile.PROP_MASTER_SINGLE
                        if _is_square_size(master.size)
                        else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
                    ),
                },
                deep=True,
            )
            master_id = master.id
            remaining = [
                master if work.id == master_id else work
                for work in remaining
            ]
            remaining.sort(key=lambda work: work.id != master_id)
        assert master is not None
        other_canonicals = [
            work.id
            for work in remaining
            if work.id != master.id
            and work.review_purpose == AssetReviewPurpose.CANONICAL_MASTER
        ]
        if other_canonicals:
            raise AssetPlanReconcileError(
                "MULTIPLE_CANONICALS: carrier retains another canonical work item: "
                + ", ".join(other_canonicals)
            )
        item_by_id[item.id] = item.model_copy(
            update={
                "brief": _physical_brief(item, master),
                "work_items": remaining,
            },
            deep=True,
        )
        if (
            item.brief is None
            or item.brief.layout_profile
            != item_by_id[item.id].brief.layout_profile  # type: ignore[union-attr]
        ):
            normalized_layout_item_ids.append(item.id)
        removed_text_work_item_ids.extend(surface.remove_text_work_item_ids)

        for plate in surface.plates:
            if plate.item_id in item_by_id or plate.item_id in created_item_ids:
                raise AssetPlanReconcileError(
                    f"DUPLICATE_ITEM: {plate.item_id}"
                )
            reusable_removed_id = plate.work_item_id in surface.remove_text_work_item_ids
            if (
                plate.work_item_id in created_work_ids
                or (plate.work_item_id in global_work_ids and not reusable_removed_id)
            ):
                raise AssetPlanReconcileError(
                    f"DUPLICATE_WORK_ITEM: {plate.work_item_id}"
                )
            created_item_ids.add(plate.item_id)
            created_work_ids.add(plate.work_item_id)
            created_plate_items.append(_plate_item(plate))

    for created_plate_item in created_plate_items:
        item_by_id[created_plate_item.id] = created_plate_item

    applied_execution_overrides: list[AssetPlanAppliedWorkExecutionOverride] = []
    for override in request.execution_overrides:
        item = item_by_id.get(override.item_id)
        if item is None:
            raise AssetPlanReconcileError(f"UNKNOWN_ITEM: {override.item_id}")
        reconciled_item, applied = _apply_work_execution_override(item, override)
        item_by_id[item.id] = reconciled_item
        applied_execution_overrides.append(applied)

    ordered_items = [item_by_id[item.id] for item in current.items]
    ordered_items.extend(item_by_id[item.id] for item in created_plate_items)
    candidate = current.model_copy(
        update={
            "items": ordered_items,
            "notes": list(current.notes) if request.notes is None else list(request.notes),
        },
        deep=True,
    )
    try:
        reconciled_plan = AssetPlanContent.model_validate(
            candidate.model_dump(mode="json")
        )
    except ValueError as exc:
        raise AssetPlanReconcileError(
            f"RECONCILED_PLAN_INVALID: {exc}"
        ) from exc
    return AssetPlanReconcileResult(
        asset_plan=reconciled_plan,
        canonicalized_item_ids=canonicalized_item_ids,
        canonical_work_item_ids=canonical_work_item_ids,
        supplemental_work_item_ids=supplemental_work_item_ids,
        created_text_plate_item_ids=[item.id for item in created_plate_items],
        removed_text_work_item_ids=removed_text_work_item_ids,
        created_physical_master_work_item_ids=created_physical_master_work_item_ids,
        normalized_layout_item_ids=list(dict.fromkeys(normalized_layout_item_ids)),
        applied_execution_overrides=applied_execution_overrides,
    )
