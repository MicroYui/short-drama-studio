"""Compare a pending new group with edits to already committed draft groups."""

from short_drama.production_contracts import StoryboardDraftAppendRequest, StoryboardDraftContent


def append_rebase_conflict(
    base: StoryboardDraftContent, current: StoryboardDraftContent, request: StoryboardDraftAppendRequest,
) -> str | None:
    # Source, scene outline, timing, authoring phase and verification remain
    # shared authoring inputs. A local Shot save does not change these fields.
    if base.model_dump(exclude={"committed_groups"}) != current.model_dump(exclude={"committed_groups"}):
        return "shared source, outline, timing or draft state changed"
    if len(base.committed_groups) != len(current.committed_groups):
        return "committed group coverage changed"
    for old, saved in zip(base.committed_groups, current.committed_groups, strict=True):
        # Keep group identity/order/source/continuity and duration authoritative;
        # only local prose, view details and cue edits can be unrelated here.
        if old.model_dump(exclude={"shots"}) != saved.model_dump(exclude={"shots"}):
            return f"group source or structure changed: {old.clip_group.id}"
        if old == saved:
            continue
        if saved.clip_group.id == request.group.clip_group.id or saved.work_unit_id == request.work_unit.id:
            return f"the requested group or work unit was edited: {saved.clip_group.id}"
        if len(old.shots) != len(saved.shots):
            return f"shot coverage changed: {saved.clip_group.id}"
        local_fields = {"visual", "framing", "camera", "narrative_purpose", "local_rework_risk", "references", "revision", "timeline_beats"}
        beat_local_fields = {"visual", "framing", "camera", "action", "performance", "references"}
        for before, after in zip(old.shots, saved.shots, strict=True):
            if before.model_dump(exclude=local_fields) != after.model_dump(exclude=local_fields):
                return f"shot identity, source timing, continuity or status changed: {before.id}"
            if ([beat.model_dump(exclude=beat_local_fields) for beat in before.timeline_beats]
                    != [beat.model_dump(exclude=beat_local_fields) for beat in after.timeline_beats]):
                return f"shot beat timing or dialogue changed: {before.id}"
    return None
