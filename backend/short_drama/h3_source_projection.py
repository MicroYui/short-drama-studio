"""Pure H3 source projections shared by compilation and source comparison."""

from __future__ import annotations

from copy import deepcopy

from pydantic import JsonValue

from short_drama.models import AssetKind, ProjectDocument, ShotPlan, ShotReferenceCue


def terminal_source(source: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Use authored outgoing continuity; retain prose when that boundary is missing.

    This is a content contract, not a claim that nonempty prose proves semantic
    completeness. Incoming/mid-shot action is not an outgoing dependency.
    """
    result = deepcopy(source)
    if all(isinstance(result.get(key), str) and str(result[key]).strip()
           for key in ("action_out", "prop_state_out")):
        result.pop("visual", None)
    return result


def cue_subjects_by_version(shot: ShotPlan) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for owner in (shot, *shot.timeline_beats):
        for cue in owner.references:
            if cue.asset_version_id is not None:
                subjects = result.setdefault(cue.asset_version_id, [])
                if cue.subject_asset_id not in subjects:
                    subjects.append(cue.subject_asset_id)
    return result


def normalize_legacy_composition_owners(
    context: dict[str, JsonValue], document: ProjectDocument,
) -> None:
    """Normalize only duplicate media owners proven by the saved exact cues.

    Older inputs counted a REFERENCE file container as a physical subject after
    reference-plan refresh. Keep explicitly selected subjects and structured
    actors; never discard an unrelated visible id or Bible entity.
    """
    rows = context.get("locked_shots")
    if not isinstance(rows, list):
        return
    owners = {version.id: asset for asset in document.assets for version in asset.versions}
    removed_names: set[str] = set()
    retained_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(raw_shot := row.get("locked_shot"), dict):
            continue
        # locked_shot deliberately projects content, not the whole ShotPlan
        # lifecycle. Read only the exact saved cue fields this comparison uses.
        raw_references = raw_shot.get("references", [])
        beats = raw_shot.get("timeline_beats", [])
        if not isinstance(raw_references, list) or not isinstance(beats, list):
            continue
        raw_cues = list(raw_references)
        for beat in beats:
            if isinstance(beat, dict) and isinstance(beat_cues := beat.get("references"), list):
                raw_cues.extend(beat_cues)
        try:
            cues = [ShotReferenceCue.model_validate(cue) for cue in raw_cues]
        except ValueError:
            continue  # Incomplete legacy references are not guessed.
        visible = row.get("visible_asset_ids")
        if not isinstance(visible, list):
            continue
        explicit_subjects = {cue.subject_asset_id for cue in cues}
        plan = next((p for p in document.reference_plan if p.shot_id == raw_shot.get("id")), None)
        structured = set(explicit_subjects)
        if plan is not None:
            structured.update(r.subject_asset_id for r in plan.requirements)
            structured.update(b.actor_asset_id for b in plan.action_beats)
            for interaction in plan.interactions:
                structured.update((interaction.actor_asset_id, interaction.target_asset_id))
        redundant: set[str] = set()
        for cue in cues:
            owner = owners.get(cue.asset_version_id) if cue.asset_version_id is not None else None
            if (owner is not None and owner.kind == AssetKind.REFERENCE
                    and owner.id not in structured and owner.id != cue.subject_asset_id):
                redundant.add(owner.id)
                removed_names.add(owner.name)
        filtered = [value for value in visible if not isinstance(value, str) or value not in redundant]
        row["visible_asset_ids"] = filtered
        retained_ids.update(value for value in filtered if isinstance(value, str))
    # A container may itself be an explicit subject in another shot of this unit.
    removed_names.difference_update(asset.name for asset in document.assets if asset.id in retained_ids)
    names = context.get("visible_assets")
    if isinstance(names, list):
        context["visible_assets"] = [name for name in names if not isinstance(name, str) or name not in removed_names]
