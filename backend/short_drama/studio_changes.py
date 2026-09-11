"""Review current Agent text edits without introducing a content history UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pydantic import JsonValue
from short_drama.models import ProjectDocument, ShotPlan, StructuredScene
from short_drama.studio_models import (
    StudioChangeRequest, StudioChangeSet, StudioTextChange, StudioTextChangeInput,
    StudioChangeDecision, StudioChangeResult, StudioSceneSave, StudioShotSave,
)
from short_drama.studio_service import StudioContentConflict
from short_drama.studio_trace import current_trace_context

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def list_changes(document: ProjectDocument) -> list[StudioChangeSet]:
    return [StudioChangeSet.model_validate(value) for value in document.studio_changes]


def _target(document: ProjectDocument, item: StudioTextChangeInput) -> StructuredScene | ShotPlan:
    if item.target == "scene":
        script = next(s for s in document.script_versions if s.id == document.project.current_script_version_id)
        target = next((scene for scene in script.structured_scenes if scene.id == item.target_id), None)
    else:
        from short_drama.studio_storyboard import project_draft_shots
        view = document.model_copy(deep=True)
        project_draft_shots(view)
        target = next((shot for shot in view.shots if shot.id == item.target_id), None)
    if target is None:
        raise KeyError(item.target_id)
    return target


def _field(target: StructuredScene | ShotPlan, item: StudioTextChangeInput) -> tuple[object, str, str]:
    owner: object = target
    name = item.field
    if isinstance(target, StructuredScene):
        allowed = {"screenplay_text", "heading", "summary"}
    else:
        allowed = {"visual", "camera", "framing", "narrative_purpose", "audio_intent", "transition_out", "local_rework_risk"}
        if name.startswith("timeline_beats."):
            parts = name.split(".")
            if len(parts) != 3:
                raise ValueError("时段字段使用 timeline_beats.<index>.<field>。")
            owner = target.timeline_beats[int(parts[1])] if parts[1].isdigit() and int(parts[1]) < len(target.timeline_beats) else None
            if owner is None:
                raise KeyError(parts[1])
            name = parts[2]
            allowed = {"visual", "camera", "framing", "action", "performance", "sound", "emotion"}
    value: object = getattr(owner, name, None)
    if name not in allowed or not isinstance(value, str):
        raise ValueError(f"不支持审阅这个文本字段：{item.field}")
    return owner, name, value


def _replace(value: str, item: StudioTextChangeInput) -> str:
    if value.count(item.before) != 1:
        raise StudioContentConflict("这段内容已被修改，或无法唯一定位。已保留当前内容，请让 Agent 按最新正文重新提出这一项修改。")
    return value.replace(item.before, item.after, 1)


def propose_changes(service: ShortDramaService, project_id: str, request: StudioChangeRequest) -> StudioChangeSet:
    with service.studio.lock:
        document = service.get_project(project_id).document
        for item in request.items:
            if item.before == item.after:
                raise ValueError("修改前后相同，不需要生成审阅项。")
            _replace(_field(_target(document, item), item)[2], item)
            if item.affected_shot_ids is not None:
                scene_shots = {shot.id for shot in document.shots if shot.scene_id == item.target_id}
                if item.target != "scene" or set(item.affected_shot_ids) - scene_shots:
                    raise ValueError("受影响镜头必须属于当前修改的场次。")
        # Identical retries return the same current proposal.
        for previous in list_changes(document):
            if all(i.status == "pending" for i in previous.items) and request.title == previous.title and [
                i.model_dump(exclude={"id", "status", "decided_at"}) for i in previous.items
            ] == [i.model_dump() for i in request.items]:
                return previous
        context = current_trace_context()
        run_id = context.get("run_id")
        change = StudioChangeSet(id=f"change-{uuid4().hex[:12]}", project_id=project_id,
            run_id=run_id if isinstance(run_id, str) else None,
            title=request.title, summary=request.summary, created_at=datetime.now(UTC).isoformat(),
            items=[StudioTextChange(**item.model_dump(), id=f"edit-{uuid4().hex[:12]}") for item in request.items])
        document.studio_changes.append(cast(dict[str, JsonValue], change.model_dump(mode="json")))
        service.repository.save_project(document)
        service.repository.append_studio_event(project_id, "change.proposed", {
            "change_id": change.id, "run_id": change.run_id, "title": change.title,
            "item_count": len(change.items), "summary": change.summary})
        return change


def _remap_references(target: ShotPlan, field: str, before: str, after: str, start: int) -> None:
    """Keep exact text anchors when an accepted edit changes surrounding prose."""
    if field.startswith("timeline_beats."):
        _, index, name = field.split(".")
        references = target.timeline_beats[int(index)].references
    else:
        name, references = field, target.references
    end, delta = start + len(before), len(after) - len(before)
    for cue in list(references):
        if cue.field != name or cue.text_start is None or cue.text_end is None:
            continue
        if cue.text_end <= start:
            continue
        if cue.text_start >= end:
            cue.text_start += delta
            cue.text_end += delta
        else:
            label = before[max(0, cue.text_start - start):cue.text_end - start]
            if cue.text_start < start or cue.text_end > end or not label:
                raise StudioContentConflict("修改覆盖了图片引用且无法保持原绑定。请让 Agent 将引用文字保留在修改中，或通过分镜编辑明确更换引用。")
            occurrences = after.count(label)
            if occurrences == 0:
                # Accepting removal of a complete mention also removes that
                # occurrence. Other mentions and the source asset remain intact.
                references.remove(cue)
                continue
            if occurrences != 1:
                raise StudioContentConflict("修改后的图片引用出现多处，无法确定原绑定的位置。请明确引用位置后再保存。")
            cue.text_start = start + after.index(label)
            cue.text_end = cue.text_start + len(label)


def decide_change(service: ShortDramaService, project_id: str, change_id: str, item_id: str,
                  request: StudioChangeDecision) -> StudioChangeResult:
    with service.studio.lock:
        document = service.get_project(project_id).document
        change = next((value for value in list_changes(document) if value.id == change_id), None)
        if change is None:
            raise KeyError(change_id)
        item = next((value for value in change.items if value.id == item_id), None)
        if item is None:
            raise KeyError(item_id)
        status = "accepted" if request.decision == "accept" else "rejected"
        if item.status != "pending":
            if item.status != status:
                raise StudioContentConflict("这项修改已处理。当前结果保留，请提出新的修改。")
            return StudioChangeResult(change=change, project=service.studio.current_project(project_id))
        if request.decision == "accept":
            target = _target(document, item).model_copy(deep=True)
            owner, field, value = _field(target, item)
            updated = _replace(value, item)
            if isinstance(target, StructuredScene):
                setattr(owner, field, updated)
                script = next(s for s in document.script_versions if s.id == document.project.current_script_version_id)
                original = next(s for s in script.structured_scenes if s.id == target.id)
                lines = [line for line in script.lines if line.id in target.line_ids]
                if item.lines is not None:
                    replacements = {line.id: line for line in item.lines}
                    lines = [replacements.pop(line.id, line) for line in lines]
                    lines.extend(replacements.values())
                valid_lines = all(not line.spoken_text or line.spoken_text in target.screenplay_text for line in lines)
                service.studio.save_scene(project_id, StudioSceneSave(scene_id=target.id,
                    heading=target.heading, screenplay_text=target.screenplay_text, expected_text=original.screenplay_text,
                    summary=target.summary, lines=lines if valid_lines else None,
                    affected_shot_ids=item.affected_shot_ids))
            else:
                _remap_references(target, item.field, item.before, item.after, value.index(item.before))
                setattr(owner, field, updated)
                service.studio.save_shot(project_id, target.id, StudioShotSave(shot=target, expected_revision=target.revision))
            document = service.get_project(project_id).document
        item.status = status
        item.decided_at = datetime.now(UTC).isoformat()
        document.studio_changes = [cast(dict[str, JsonValue], change.model_dump(mode="json"))
            if value.get("id") == change_id else value for value in document.studio_changes]
        service.repository.save_project(document)
        service.repository.append_studio_event(project_id, "change.decided", {
            "change_id": change_id, "item_id": item_id, "run_id": change.run_id,
            "decision": request.decision, "title": item.title})
        result = StudioChangeResult(change=change, project=service.studio.current_project(project_id))
    if change.run_id and not any(i.status == "pending" for i in change.items):
        service.studio_runtime.review_resolved(project_id, change)
    return result
