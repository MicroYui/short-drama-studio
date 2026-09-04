"""Apply dependency-aware semantic changes to canonical project artifacts.

The planner remains editable and advisory.  Once the user or Production Manager
chooses to apply it, this module performs ordinary versioned product mutations:
create new Script/Bible/Asset versions, invalidate only the affected downstream
branch, and return a compact verification summary.  It deliberately contains no
approval gate, content freeze, hash chain, or provider submission.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import Field, model_validator

from short_drama.change_planner import ChangeTargetKind, SemanticChangePlan
from short_drama.models import (
    ApprovalStatus,
    AssetMediaType,
    AssetSourceRecord,
    AssetVersion,
    DomainModel,
    Freshness,
    OriginType,
    ProjectAggregate,
    ProjectBibleVersion,
    ProjectDocument,
    ScriptVersion,
    ShotPlan,
)
from short_drama.worker import utc_now


class ChangeExecutionError(ValueError):
    """Raised when a requested patch cannot be applied to the current project."""


class SemanticPatchOperation(StrEnum):
    SET_FIELDS = "SET_FIELDS"


_ALLOWED_FIELDS: dict[ChangeTargetKind, frozenset[str]] = {
    ChangeTargetKind.SCRIPT_LINE: frozenset({"spoken_text", "subtitle_text"}),
    ChangeTargetKind.SCENE: frozenset(
        {"heading", "summary", "narrative_purpose"}
    ),
    ChangeTargetKind.CHARACTER: frozenset(
        {"name", "appearance", "wardrobe", "performance", "voice_identity"}
    ),
    ChangeTargetKind.LOCATION: frozenset(
        {"name", "layout", "lighting", "palette"}
    ),
    ChangeTargetKind.PROP: frozenset({"name", "appearance"}),
    ChangeTargetKind.ASSET: frozenset({"name", "descriptor"}),
    ChangeTargetKind.SHOT: frozenset(
        {
            "narrative_purpose",
            "visual",
            "framing",
            "camera",
            "audio_intent",
            "transition_out",
            "local_rework_risk",
        }
    ),
}


class SemanticArtifactPatch(DomainModel):
    """A small typed field patch produced by an Agent or edited by the user."""

    target_kind: ChangeTargetKind
    target_id: str = Field(min_length=1, max_length=240)
    operation: SemanticPatchOperation = SemanticPatchOperation.SET_FIELDS
    updates: dict[str, str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_fields(self) -> SemanticArtifactPatch:
        allowed = _ALLOWED_FIELDS.get(self.target_kind)
        if allowed is None:
            raise ValueError(
                f"{self.target_kind.value} is not directly patchable; patch its owning artifact"
            )
        unknown = sorted(set(self.updates) - allowed)
        if unknown:
            raise ValueError(
                f"Unsupported {self.target_kind.value} fields: {', '.join(unknown)}"
            )
        if any(not value.strip() for value in self.updates.values()):
            raise ValueError("Semantic patch values must not be blank")
        return self


class SemanticChangeApplyRequest(DomainModel):
    plan: SemanticChangePlan
    patches: list[SemanticArtifactPatch] = Field(
        min_length=1,
        max_length=200,
    )
    change_reason: str = Field(min_length=1, max_length=2000)


class SemanticChangeVerification(DomainModel):
    applied_target_ids: list[str]
    unchanged_requested_target_ids: list[str]
    invalidated_shot_ids: list[str]
    messages: list[str]


class SemanticChangeApplyResult(DomainModel):
    project_id: str
    applied_patch_count: int
    script_version_id: str | None = None
    bible_version_id: str | None = None
    asset_version_ids: list[str] = Field(default_factory=list[str])
    invalidated_shot_ids: list[str] = Field(default_factory=list[str])
    stale_take_ids: list[str] = Field(default_factory=list[str])
    stale_manifest_ids: list[str] = Field(default_factory=list[str])
    verification: SemanticChangeVerification


class SemanticChangeApplyResponse(DomainModel):
    result: SemanticChangeApplyResult
    project: ProjectAggregate


class PreparedSemanticChange(DomainModel):
    document: ProjectDocument
    result: SemanticChangeApplyResult


def _current_script(document: ProjectDocument) -> ScriptVersion:
    current = next(
        (
            version
            for version in document.script_versions
            if version.id == document.project.current_script_version_id
        ),
        None,
    )
    if current is None:
        raise ChangeExecutionError("Current ScriptVersion is missing")
    return current


def _current_bible(document: ProjectDocument) -> ProjectBibleVersion:
    current = next(
        (
            version
            for version in document.bible_versions
            if version.id == document.project.current_bible_version_id
        ),
        None,
    )
    if current is None:
        raise ChangeExecutionError("Current ProjectBibleVersion is missing")
    return current


def _validate_plan_scope(
    document: ProjectDocument,
    request: SemanticChangeApplyRequest,
) -> None:
    plan = request.plan
    if plan.project_id != document.project.id:
        raise ChangeExecutionError("Semantic change plan belongs to another project")
    allowed_by_kind: dict[ChangeTargetKind, set[str]] = {
        ChangeTargetKind.SCRIPT_LINE: set(plan.affected_script_line_ids),
        ChangeTargetKind.SCENE: set(plan.affected_scene_ids),
        ChangeTargetKind.ASSET: set(plan.affected_asset_ids),
        ChangeTargetKind.SHOT: set(plan.affected_shot_ids),
    }
    for target in plan.intent.targets:
        allowed_by_kind.setdefault(target.kind, set()).add(target.target_id)
    for patch in request.patches:
        allowed = allowed_by_kind.get(patch.target_kind, set())
        if patch.target_id not in allowed:
            raise ChangeExecutionError(
                f"Patch target {patch.target_id} is outside the editable impact plan"
            )


def prepare_semantic_change(
    document: ProjectDocument,
    request: SemanticChangeApplyRequest,
) -> PreparedSemanticChange:
    """Prepare one atomic project mutation without performing external side effects."""

    _validate_plan_scope(document, request)
    updated = document.model_copy(deep=True)
    now = utc_now()
    updated.project.updated_at = now
    applied_target_ids: list[str] = []
    script_patches = [
        patch
        for patch in request.patches
        if patch.target_kind in {ChangeTargetKind.SCRIPT_LINE, ChangeTargetKind.SCENE}
    ]
    bible_patches = [
        patch
        for patch in request.patches
        if patch.target_kind
        in {ChangeTargetKind.CHARACTER, ChangeTargetKind.LOCATION, ChangeTargetKind.PROP}
    ]
    asset_patches = [
        patch
        for patch in request.patches
        if patch.target_kind == ChangeTargetKind.ASSET
    ]
    shot_patches = [
        patch
        for patch in request.patches
        if patch.target_kind == ChangeTargetKind.SHOT
    ]

    next_script_id: str | None = None
    if script_patches:
        current_script = _current_script(updated)
        next_script = current_script.model_copy(deep=True)
        next_script.version = max(item.version for item in updated.script_versions) + 1
        next_script.id = f"{updated.project.id}-script-v{next_script.version}"
        next_script.parent_version_id = current_script.id
        next_script.change_reason = request.change_reason
        next_script.approval_status = ApprovalStatus.APPROVED
        next_script.freshness = Freshness.CURRENT
        next_script.created_at = now
        line_by_id = {line.id: line for line in next_script.lines}
        scene_by_id = {scene.id: scene for scene in next_script.structured_scenes}
        for patch in script_patches:
            target = (
                line_by_id.get(patch.target_id)
                if patch.target_kind == ChangeTargetKind.SCRIPT_LINE
                else scene_by_id.get(patch.target_id)
            )
            if target is None:
                raise ChangeExecutionError(f"Unknown script target: {patch.target_id}")
            for field, value in patch.updates.items():
                setattr(target, field, value.strip())
            applied_target_ids.append(patch.target_id)
        next_script.spoken_text = "\n".join(line.spoken_text for line in next_script.lines)
        next_script.subtitle_text = "\n".join(
            line.subtitle_text for line in next_script.lines
        )
        current_script.approval_status = ApprovalStatus.SUPERSEDED
        current_script.freshness = Freshness.STALE
        updated.script_versions.append(next_script)
        updated.project.current_script_version_id = next_script.id
        next_script_id = next_script.id

    next_bible_id: str | None = None
    if bible_patches:
        current_bible = _current_bible(updated)
        next_bible = current_bible.model_copy(deep=True)
        next_bible.version = max(item.version for item in updated.bible_versions) + 1
        next_bible.id = f"{updated.project.id}-bible-v{next_bible.version}"
        next_bible.parent_version_id = current_bible.id
        next_bible.approval_status = ApprovalStatus.APPROVED
        next_bible.freshness = Freshness.CURRENT
        next_bible.created_at = now
        targets: dict[ChangeTargetKind, dict[str, object]] = {
            ChangeTargetKind.CHARACTER: {
                item.id: item for item in next_bible.characters
            },
            ChangeTargetKind.LOCATION: {
                item.id: item for item in next_bible.locations
            },
            ChangeTargetKind.PROP: {item.id: item for item in next_bible.props},
        }
        for patch in bible_patches:
            target = targets[patch.target_kind].get(patch.target_id)
            if target is None:
                raise ChangeExecutionError(f"Unknown bible target: {patch.target_id}")
            for field, value in patch.updates.items():
                setattr(target, field, value.strip())
            applied_target_ids.append(patch.target_id)
        current_bible.approval_status = ApprovalStatus.SUPERSEDED
        current_bible.freshness = Freshness.STALE
        updated.bible_versions.append(next_bible)
        updated.project.current_bible_version_id = next_bible.id
        next_bible_id = next_bible.id

    created_asset_version_ids: list[str] = []
    asset_by_id = {asset.id: asset for asset in updated.assets}
    for patch in asset_patches:
        asset = asset_by_id.get(patch.target_id)
        if asset is None:
            raise ChangeExecutionError(f"Unknown asset target: {patch.target_id}")
        if "name" in patch.updates:
            asset.name = patch.updates["name"].strip()
        if "descriptor" in patch.updates:
            current = next(
                (item for item in asset.versions if item.id == asset.current_version_id),
                None,
            )
            if current is None:
                raise ChangeExecutionError(
                    f"Asset {asset.id} current version is missing"
                )
            current.approval_status = ApprovalStatus.SUPERSEDED
            current.freshness = Freshness.STALE
            next_number = max(item.version for item in asset.versions) + 1
            version = AssetVersion(
                id=f"asset-version-{uuid4().hex[:12]}",
                version=next_number,
                origin_type=OriginType.MANUAL,
                descriptor=patch.updates["descriptor"].strip(),
                media_type=AssetMediaType.TEXT_REFERENCE,
                parent_version_id=current.id,
                source=AssetSourceRecord(
                    source_type=OriginType.MANUAL,
                    source_label="语义修改后的资产规格",
                    created_at=now,
                ),
                approval_status=ApprovalStatus.APPROVED,
                freshness=Freshness.CURRENT,
                created_at=now,
            )
            asset.versions.append(version)
            asset.current_version_id = version.id
            asset.approved_version_id = version.id
            created_asset_version_ids.append(version.id)
        applied_target_ids.append(patch.target_id)

    shot_by_id = {shot.id: shot for shot in updated.shots}
    for patch in shot_patches:
        shot = shot_by_id.get(patch.target_id)
        if shot is None:
            raise ChangeExecutionError(f"Unknown shot target: {patch.target_id}")
        payload = shot.model_dump(mode="python")
        for field, value in patch.updates.items():
            payload[field] = value.strip()
        if {
            "visual",
            "framing",
            "camera",
            "audio_intent",
            "transition_out",
        }.intersection(patch.updates):
            payload["timeline_beats"] = []
        payload["revision"] = shot.revision + 1
        payload["approval_status"] = ApprovalStatus.APPROVED
        payload["freshness"] = Freshness.CURRENT
        try:
            validated_shot = ShotPlan.model_validate(payload)
        except ValueError as exc:
            raise ChangeExecutionError(f"Invalid shot patch: {exc}") from exc
        shot_index = updated.shots.index(shot)
        updated.shots[shot_index] = validated_shot
        shot_by_id[patch.target_id] = validated_shot
        applied_target_ids.append(patch.target_id)

    invalidated_shot_ids = list(dict.fromkeys(request.plan.affected_shot_ids))
    directly_patched_shots = {patch.target_id for patch in shot_patches}
    for shot_id in invalidated_shot_ids:
        shot = shot_by_id.get(shot_id)
        if shot is not None and shot_id not in directly_patched_shots:
            shot.freshness = Freshness.STALE

    updated.execution_plan = [
        item for item in updated.execution_plan if item.shot_id not in invalidated_shot_ids
    ]
    for reference in updated.reference_plan:
        if reference.shot_id in invalidated_shot_ids:
            reference.capability_valid = False
            note = "Upstream semantic change requires reference-plan refresh"
            if note not in reference.validation_notes:
                reference.validation_notes.append(note)

    stale_manifest_ids: list[str] = []
    for manifest in updated.manifests:
        if manifest.freshness == Freshness.CURRENT:
            manifest.freshness = Freshness.STALE
            stale_manifest_ids.append(manifest.id)
    updated.project.current_manifest_id = None

    requested_target_ids = [target.target_id for target in request.plan.intent.targets]
    unchanged = [
        target_id for target_id in requested_target_ids if target_id not in applied_target_ids
    ]
    result = SemanticChangeApplyResult(
        project_id=updated.project.id,
        applied_patch_count=len(request.patches),
        script_version_id=next_script_id,
        bible_version_id=next_bible_id,
        asset_version_ids=created_asset_version_ids,
        invalidated_shot_ids=invalidated_shot_ids,
        stale_take_ids=list(request.plan.stale_take_ids),
        stale_manifest_ids=stale_manifest_ids,
        verification=SemanticChangeVerification(
            applied_target_ids=list(dict.fromkeys(applied_target_ids)),
            unchanged_requested_target_ids=unchanged,
            invalidated_shot_ids=invalidated_shot_ids,
            messages=[
                "Canonical artifacts were versioned before downstream invalidation.",
                "No external generation provider was called by change application.",
            ],
        ),
    )
    return PreparedSemanticChange(document=updated, result=result)
