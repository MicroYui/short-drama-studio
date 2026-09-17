"""Task-sized current-content tools used by the product Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Self, cast
from pydantic import JsonValue, Field, model_validator
from short_drama.models import DomainModel, ReferencePlanEntry, ScriptLine, StructuredScene
from short_drama.studio_models import StudioSceneSave, StudioShotSave, StudioTimelineSave, StudioExportRequest, StudioExport, StudioEditorialEdit, StudioAssetOverlay
from short_drama.tools.contracts import ArtifactRef, ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolSpec
from short_drama.tools.registry import ToolRegistry
from short_drama.studio_comfy import ComfyRunRequest, ComfyRun
from short_drama.studio_models import StudioChangeRequest, StudioChangeSet, StudioSceneOutline
from short_drama.reference_planner import (
    ReferencePlanPreviewRequest, ReferencePlanPreviewResponse, preview_reference_plan,
)
from short_drama.shot_write_receipts import SavedShotSummary, shot_write_receipt, summarize_saved_shot
from short_drama.shot_media_registration import ShotMediaRegistration, ShotMediaRegistrationSummary, save_shot_with_media_registration

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class CurrentReadInput(ToolModel):
    project_id: str
    scene_id: str | None = None
    shot_id: str | None = None


class CurrentReadOutput(DomainModel):
    content: dict[str, JsonValue]


class SceneWriteInput(ToolModel):
    project_id: str
    request: StudioSceneSave


class ShotWriteInput(ToolModel):
    project_id: str
    shot_id: str
    request: StudioShotSave
    media_registration: ShotMediaRegistration | None = None


class TimelineWriteInput(ToolModel):
    project_id: str
    shot_id: str | None = None
    request: StudioTimelineSave


class TimelineReadInput(ToolModel):
    project_id: str
    include_project: bool = False
    shot_id: str | None = None
    anchor_clip_id: str | None = None
    timeline_id: str | None = Field(default=None, description="Copy the saved editor_selection timeline ID. Defaults to active only for unambiguous IDs; identical IDs across timelines require this field.")
    subtitle_ids: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list, max_length=100,
        description="Exact current subtitle IDs. Returns only selected rows in FILM coordinates plus actual shot-local intersections.")
    clip_ids: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list, max_length=100,
        description="Exact current clip IDs, including image/audio clips without sourceShotId. Can be combined with subtitle_ids.")

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.include_project and (self.subtitle_ids or self.clip_ids):
            raise ValueError("include_project cannot be combined with subtitle_ids/clip_ids; omit it for a compact exact read.")
        if self.anchor_clip_id and not self.shot_id:
            raise ValueError("anchor_clip_id requires shot_id; exact film IDs can be read without either field.")
        return self


class TimelineExportInput(ToolModel):
    project_id: str
    request: StudioExportRequest


class ExportReadInput(ToolModel):
    project_id: str
    export_id: str | None = None
    frame_times_seconds: list[Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=list[float], max_length=4,
        description="Optional up to four exact EXPORT_FILM times. Attaches actual rendered pixels to the next Worker decision for subtitle/title/layout or cut inspection; omit for metadata only. Times must be before the export duration.")
    frame_variant: Literal["subtitled", "clean"] = "subtitled"


class TimelineEditInput(ToolModel):
    project_id: str
    request: StudioEditorialEdit


class TimelineOverlayInput(ToolModel):
    project_id: str
    request: StudioAssetOverlay


class WorkflowInput(ToolModel):
    project_id: str
    request: ComfyRunRequest


class WorkflowReadInput(ToolModel):
    project_id: str
    workflow_id: str | None = None


class CurrentWriteOutput(DomainModel):
    project_id: str
    scene_ids: list[str]
    shot_ids: list[str]
    notices: list[str]
    shot_revisions: dict[str, int] = Field(default_factory=dict[str, int])
    editorial_revision: int | None = None
    timeline_id: str | None = None
    changed_shot_ids: list[str] = Field(default_factory=list[str])
    changed_scene_ids: list[str] = Field(default_factory=list[str])


class SavedSceneContent(DomainModel):
    scene: StructuredScene
    lines: list[ScriptLine]
    structure_stale: bool


class SceneWriteOutput(CurrentWriteOutput):
    script_ref: ArtifactRef
    saved_scenes: list[SavedSceneContent] = Field(description=(
        "Actual saved content for the selected and related scenes only, including unchanged selected scenes. "
        "Lines come from the saved ScriptVersion; structure_stale flags text whose dialogue structure needs updating."
    ))


class ShotWriteOutput(CurrentWriteOutput):
    asset_plan_identity: dict[str, JsonValue] | None = None
    saved_shot: SavedShotSummary | None = None
    media_registration: ShotMediaRegistrationSummary | None = None
    reference_plan_preview_scope: Literal["GENERATION_ONLY"] = Field(default="GENERATION_ONLY", description=(
        "The preview selects video-generation references only. EDITORIAL bindings remain in saved_shot.shot_cues. "
        "Its complexity.action_beat_count counts reference-plan action_beats, not saved_shot.beats."
    ))
    reference_plan_preview: ReferencePlanPreviewResponse | None = None
    reference_plan_preview_error: str | None = None


class TextProposalInput(ToolModel):
    project_id: str
    request: StudioChangeRequest


class SceneOutlineInput(ToolModel):
    project_id: str
    scenes: list[StudioSceneOutline] = Field(min_length=1, max_length=200)


def _editorial_refs(project_id: str, project: JsonValue, revision: JsonValue,
                    *, timeline_id: str | None = None) -> tuple[ArtifactRef, ...]:
    if not isinstance(project, dict) or not isinstance(revision, int) or revision < 1:
        return ()
    target_id = timeline_id or project.get("activeTimelineId")
    timelines = project.get("timelines")
    if not isinstance(target_id, str) or not isinstance(timelines, list):
        return ()
    if not any(isinstance(item, dict) and item.get("id") == target_id for item in timelines):
        return ()
    return (ArtifactRef(project_id=project_id, artifact_type="editorial_timeline", artifact_id=target_id, version=revision),)


def register_tools(registry: ToolRegistry, service: ShortDramaService) -> None:
    def current(request: CurrentReadInput) -> ToolExecution[CurrentReadOutput]:
        document = service.studio.current_project(request.project_id).document
        script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
        content: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "script_id": script.id,
            "episode_title": document.project.episode_title,
            "input_text": document.project.input_text,
            "scene_index": [{"id": scene.id, "heading": scene.heading, "summary": scene.summary,
                             "narrative_purpose": scene.narrative_purpose} for scene in script.structured_scenes],
            "shot_index": [{"id": shot.id, "sequence": shot.sequence, "scene_id": shot.scene_id, "duration_seconds": shot.duration_seconds} for shot in document.shots],
            "asset_index": [{"id": asset.id, "name": asset.name, "kind": asset.kind.value, "media": [
                {"id": version.id, "descriptor": version.descriptor, "media_type": version.media_type.value}
                for version in asset.versions if version.local_path or version.external_url
            ]} for asset in document.assets],
            "structure_stale_scene_ids": list(script.structure_stale_scene_ids),
            "editorial_revision": (document.editorial_timeline or {}).get("revision", 0),
            "text_change_reviews": [{"id": item.get("id"), "title": item.get("title"), "items": item.get("items")}
                                    for item in document.studio_changes[-3:]],
            "story_intent": None if script.story_intent is None else cast(JsonValue, script.story_intent.model_dump(mode="json")),
            "bible": cast(JsonValue, next(item for item in document.bible_versions
                if item.id == document.project.current_bible_version_id).model_dump(mode="json")),
            "production_documents": [{"id": item.id, "kind": item.kind, "version_id": item.current_version_id}
                                     for item in document.production_documents],
        }
        if request.scene_id:
            scene = next((item for item in script.structured_scenes if item.id == request.scene_id), None)
            if scene is None:
                raise KeyError(request.scene_id)
            content["scene"] = cast(dict[str, JsonValue], scene.model_dump(mode="json"))
            content["lines"] = [cast(dict[str, JsonValue], line.model_dump(mode="json")) for line in script.lines if line.id in scene.line_ids]
            content["script_ref"] = ArtifactRef(project_id=request.project_id, artifact_type="script_version",
                artifact_id=script.id, version=script.version).model_dump(mode="json")
            content["structure_stale"] = scene.id in script.structure_stale_scene_ids
        if request.shot_id:
            shot = next((item for item in document.shots if item.id == request.shot_id), None)
            if shot is None:
                raise KeyError(request.shot_id)
            content["shot"] = cast(dict[str, JsonValue], shot.model_dump(mode="json"))
        return ToolExecution(output=CurrentReadOutput(content=content))

    def save_scene(request: SceneWriteInput) -> ToolExecution[SceneWriteOutput]:
        result = service.studio.save_scene(request.project_id, request.request)
        document = result.project.document
        script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
        selected_ids = set(result.changed_scene_ids)
        if request.request.scene_id is not None:
            selected_ids.add(request.request.scene_id)
        selected_ids.update(edit.scene_id for edit in request.request.related_scene_updates)
        script_ref = ArtifactRef(project_id=request.project_id, artifact_type="script_version",
            artifact_id=script.id, version=script.version)
        return ToolExecution(output=SceneWriteOutput(project_id=request.project_id,
            scene_ids=[item.id for item in script.structured_scenes], shot_ids=[], notices=result.notices,
            changed_scene_ids=result.changed_scene_ids, changed_shot_ids=result.changed_shot_ids,
            script_ref=script_ref, saved_scenes=[SavedSceneContent(scene=scene,
                lines=[line for line in script.lines if line.id in scene.line_ids],
                structure_stale=scene.id in script.structure_stale_scene_ids)
                for scene in script.structured_scenes if scene.id in selected_ids]),
            artifact_refs=(script_ref,))

    def save_shot(request: ShotWriteInput) -> ToolExecution[ShotWriteOutput]:
        registration = None
        plan_receipt = None
        if request.media_registration is None:
            result = service.studio.save_shot(request.project_id, request.shot_id, request.request)
        else:
            result, registration, plan_receipt = save_shot_with_media_registration(service, request.project_id, request.shot_id,
                request.request, request.media_registration)
        document = result.project.document
        saved = next(shot for shot in document.shots if shot.id == request.shot_id)
        plan = next((item for item in document.reference_plan if item.shot_id == saved.id), None)
        from short_drama.studio_references import generation_bindings_from_cues, generation_cues
        from short_drama.studio_storyboard import resolve_current_shot
        preview = None
        preview_error = None
        try:
            resolved = resolve_current_shot(document, saved.id) if plan is None else None
            draft_cues = resolved is not None and resolved.draft_source is not None
            if draft_cues:
                # The saved draft's exact cues are already the selection. Derive
                # this return view only; do not create another persisted draft plan.
                plan = ReferencePlanEntry(shot_id=saved.id, bindings=generation_bindings_from_cues(document, saved),
                    capability_valid=True, validation_notes=[], max_reference_images=9)
            if plan is not None:
                preview = preview_reference_plan(document, saved, plan, ReferencePlanPreviewRequest())
                if draft_cues:
                    missing = [cue.id or cue.alias for cue in generation_cues(saved) if cue.asset_version_id is None]
                    if missing:
                        preview_error = "草稿与已有绑定已保存；以下生成引用尚未选择图片：" + ", ".join(missing)
            else:
                preview_error = "当前镜头尚无参考计划；分镜已保存，引用待后续准备。"
        except Exception as exc:
            # Only this derived diagnostic is optional. The write has already
            # succeeded; preserve its saved content and receipt if preview fails.
            preview_error = str(exc)
        output = ShotWriteOutput(project_id=request.project_id,
            scene_ids=[], shot_ids=[saved.id], notices=result.notices,
            shot_revisions={saved.id: saved.revision}, changed_shot_ids=result.changed_shot_ids if registration is not None else [saved.id],
            saved_shot=summarize_saved_shot(saved),
            media_registration=registration,
            asset_plan_identity=cast(dict[str, JsonValue], plan_receipt.current_state["asset_plan_identity"]) if plan_receipt else None,
            reference_plan_preview=preview, reference_plan_preview_error=preview_error)
        refs = (ArtifactRef(project_id=request.project_id, artifact_type="shot",
            artifact_id=saved.id, version=saved.revision),)
        if registration is not None:
            refs += (ArtifactRef(project_id=request.project_id, artifact_type="asset_version", artifact_id=registration.asset_version_id),)
            refs += tuple(ArtifactRef(project_id=request.project_id, artifact_type="reference_pack_entry", artifact_id=identifier)
                for identifier in registration.reference_entry_ids)
            if registration.asset_plan_document_id:
                refs += (ArtifactRef(project_id=request.project_id, artifact_type="asset_plan",
                    artifact_id=registration.asset_plan_document_id, version=registration.asset_plan_version),)
        receipt = shot_write_receipt(output.model_dump(mode="json"), refs)
        if plan_receipt is not None:
            receipt.current_state.update(plan_receipt.current_state)
            receipt.changed_ids.update(plan_receipt.changed_ids)
        if registration is not None:
            receipt.current_state["media_registration"] = registration.model_dump(mode="json")
            receipt.changed_ids["created_reference_entry_ids"] = registration.created_reference_entry_ids
            receipt.changed_ids["completed_work_item_ids"] = registration.completed_work_item_ids if registration.plan_changed else []
        return ToolExecution(output=output,
            warnings=tuple(preview.warnings) if preview is not None else (preview_error or "引用预览不可用",),
            artifact_refs=refs, receipt=receipt)

    def save_timeline(request: TimelineWriteInput) -> ToolExecution[CurrentWriteOutput]:
        if request.shot_id:
            from short_drama.studio_shot_editorial import save_shot_editorial
            result = save_shot_editorial(service, request.project_id, request.shot_id, request.request)
        else:
            result = service.studio.save_timeline(request.project_id, request.request)
        saved = result.project.document.editorial_timeline or {}
        project = saved.get("project")
        refs = _editorial_refs(request.project_id, project, saved.get("revision"))
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id, scene_ids=[], shot_ids=[],
            editorial_revision=refs[0].version if refs else None,
            timeline_id=refs[0].artifact_id if refs else None,
            notices=result.notices or ["已保存当前剪辑。"]), artifact_refs=refs)

    def add_overlay(request: TimelineOverlayInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_overlays import add_asset_overlay
        result = add_asset_overlay(service, request.project_id, request.request)
        return ToolExecution(output=CurrentReadOutput(content={
            "project_id": request.project_id, "editorial_revision": result.revision,
            "overlay_id": request.request.overlay_id,
            "asset_version_id": request.request.asset_version_id,
            "saved": True,
        }), artifact_refs=_editorial_refs(request.project_id, result.project, result.revision))

    def read_timeline(request: TimelineReadInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_editorial import read_editorial, EditorProject
        from short_drama.studio_visual_edits import clip_visual_fields
        from short_drama.studio_shot_editorial import read_shot_editorial
        if request.subtitle_ids or request.clip_ids:
            from short_drama.studio_editorial_selection import read_editorial_selection
            return ToolExecution(output=CurrentReadOutput(content=read_editorial_selection(
                service, request.project_id, timeline_id=request.timeline_id,
                subtitle_ids=request.subtitle_ids, clip_ids=request.clip_ids,
                shot_id=request.shot_id, anchor_clip_id=request.anchor_clip_id)))
        value = read_shot_editorial(service, request.project_id, request.shot_id, request.anchor_clip_id,
            timeline_id=request.timeline_id) if request.shot_id else read_editorial(service, request.project_id)
        editor = EditorProject.model_validate(value.project)
        target_id = request.timeline_id or editor.activeTimelineId
        timeline = next((item for item in editor.timelines if item.id == target_id), None)
        if timeline is None:
            raise ValueError(f"/timeline_id: {target_id!r} is not available in this editorial scope. Use current timeline_id or omit shot_id for a film read.")
        # A shot without a saved occurrence can project a candidate even when
        # other shots have already advanced the episode's save revision.
        editorial_is_saved = value.revision > 0
        is_saved = editorial_is_saved and (value.scope is None or value.scope.anchor_clip_id is not None)
        content: dict[str, JsonValue] = {"project_id": request.project_id, "editorial_revision": value.revision,
            "is_saved": is_saved, "editorial_is_saved": editorial_is_saved,
            "content_origin": "SAVED_EDITORIAL" if is_saved else "CANDIDATE_PREVIEW",
            "timeline_id": timeline.id,
            "coordinate_space": "SHOT_LOCAL" if value.scope else "FILM",
            "film_start_seconds": value.scope.film_start_seconds if value.scope else 0,
            "clips": [{"id": clip.id, "is_saved": is_saved, "type": clip.type, "source_shot_id": (clip.model_extra or {}).get("sourceShotId"),
                "start_seconds": clip.startTime, "duration_seconds": clip.duration, "source_in_seconds": clip.trimStart,
                "speed": clip.speed, "reversed": clip.reversed,
                "source_window_start_seconds": (clip.model_extra or {}).get("sourceWindowStart"),
                "source_window_end_seconds": (clip.model_extra or {}).get("sourceWindowEnd"),
                "volume": clip.volume, "muted": clip.muted, "track_index": clip.trackIndex,
                **clip_visual_fields(clip)} for clip in timeline.clips],
            "subtitles": [cast(dict[str, JsonValue], item.model_dump(mode="json")) for item in timeline.subtitles],
            "exports": [cast(JsonValue, item) for item in service.get_project(request.project_id).document.studio_exports]}
        if not is_saved:
            content["read_notice"] = ("本次范围为候选预览，尚未保存到本集。未采用镜头的局部预览 clip ID 不属于当前本集时间线，不能用于本集精确 clip_ids 查询；采用并保存后重新读取本集，取得真实片段 ID。editorial_revision 仍是本集保存并发校验用修订号，不代表本镜已采用。"
                if value.scope and value.scope.anchor_clip_id is None else
                "本集尚未保存剪辑，本次对象来自候选预览，不是已保存的成片采用记录；采用并保存后重新读取真实片段 ID。")
        if value.scope:
            content["shot_scope"] = cast(JsonValue, value.scope.model_dump(mode="json"))
            content["time_coordinates"] = "当前镜头内部时间，从 0 秒开始。素材入点仍是源文件时间。edit_clips 必须传递相同 shot_id 和 anchor_clip_id，保存后同步成片。"
        if request.include_project:
            editor.activeTimelineId = timeline.id
            # Assets are present once in the library; save rehydrates each clip by assetId.
            for item in editor.timelines:
                for clip in item.clips:
                    clip.asset = None
            content["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json", exclude_none=True))
        return ToolExecution(output=CurrentReadOutput(content=content))

    def read_export(request: ExportReadInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_export import inspect_export
        from short_drama.studio_export_frames import export_frame_attachments
        content = inspect_export(service, request.project_id, request.export_id)
        attachments = export_frame_attachments(service, content, times=request.frame_times_seconds, variant=request.frame_variant)
        return ToolExecution(output=CurrentReadOutput(content=content), visual_attachments=attachments)

    def export_timeline(request: TimelineExportInput) -> ToolExecution[StudioExport]:
        from short_drama.studio_export import start_export
        record = start_export(service, request.project_id, request.request, background=False)
        if record.status != "completed":
            raise ValueError(record.error or "成片导出未完成。")
        return ToolExecution(output=record, artifact_refs=(ArtifactRef(project_id=record.project_id,
            artifact_type="studio_export", artifact_id=record.id, version=record.editorial_revision),))

    def edit_timeline(request: TimelineEditInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_editorial import edit_editorial, EditorProject
        from short_drama.studio_visual_edits import clip_visual_fields
        clip_ids: set[str] = set()
        value = edit_editorial(service, request.project_id, request.request, changed_clip_ids=clip_ids)
        refs = _editorial_refs(request.project_id, value.project, value.revision, timeline_id=request.request.timeline_id)
        editor = EditorProject.model_validate(value.project)
        target_id = request.request.timeline_id or editor.activeTimelineId
        timeline = next(item for item in editor.timelines if item.id == target_id)
        cue_ids = {item.id for item in (*request.request.subtitles, *request.request.add_subtitles)}
        return ToolExecution(output=CurrentReadOutput(content={"project_id": request.project_id,
            "editorial_revision": value.revision, "timeline_id": refs[0].artifact_id if refs else None,
            "saved": True, "message": "指定片段、标题与字幕的变化字段已保存；相邻溶解的另一端同步处理，其余内容保留。",
            "changed_clips": [{"id": clip.id, "type": clip.type, "track_index": clip.trackIndex,
                "start_seconds": clip.startTime, "duration_seconds": clip.duration,
                "source_in_seconds": clip.trimStart, "volume": clip.volume, "muted": clip.muted, "speed": clip.speed,
                **clip_visual_fields(clip)}
                for clip in timeline.clips if clip.id in clip_ids],
            "created_title_ids": [item.id for item in request.request.add_titles],
            "removed_clip_ids": cast(JsonValue, sorted(clip_ids.difference(clip.id for clip in timeline.clips))),
            "changed_subtitles": [cast(JsonValue, item.model_dump(mode="json")) for item in timeline.subtitles if item.id in cue_ids],
            "created_subtitle_ids": [item.id for item in request.request.add_subtitles],
            "removed_subtitle_ids": [item.id for item in request.request.subtitles if item.remove],
            "time_coordinates": "保存后的全片时间；字幕样式与文字仅更新显式字段。"}), artifact_refs=refs)

    def workflow(request: WorkflowInput) -> ToolExecution[ComfyRun]:
        from short_drama.studio_comfy import run_comfy
        value = run_comfy(service, request.project_id, request.request, background=False)
        if value.status != "completed":
            raise ValueError(value.error or "高级工作流未完成。")
        return ToolExecution(output=value)

    def read_workflow(request: WorkflowReadInput) -> ToolExecution[CurrentReadOutput]:
        import json
        from pathlib import Path
        from short_drama.studio_comfy import comfy_config, get_comfy_run
        content: dict[str, JsonValue] = {"connection": cast(dict[str, JsonValue], comfy_config(service).model_dump(mode="json")),
            "workflows": [item for item in service.get_project(request.project_id).document.comfy_workflows]}
        if request.workflow_id:
            record = get_comfy_run(service, request.project_id, request.workflow_id)
            if record.input_path:
                content["workflow"] = cast(JsonValue, json.loads(Path(record.input_path).read_text(encoding="utf-8")))
        return ToolExecution(output=CurrentReadOutput(content=content))

    registry.register(ToolSpec(id="artifact.read_current", version=1, category=ToolCategory.ARTIFACT,
        description="读取当前集场次/镜头/资产索引；按 scene_id 或 shot_id 取得当前正文和精确图片引用。完整 trace 不进入此工作集。",
        effect=ToolEffect.READ, idempotent=True, input_model=CurrentReadInput, output_model=CurrentReadOutput, handler=current,
        tags=frozenset({"studio", "current-content", "scoped-read"})))
    def propose_text(request: TextProposalInput) -> ToolExecution[StudioChangeSet]:
        from short_drama.studio_changes import propose_changes
        return ToolExecution(output=propose_changes(service, request.project_id, request.request))

    def plan_scenes(request: SceneOutlineInput) -> ToolExecution[CurrentReadOutput]:
        # Empty scene rows are current content, filled in place by subsequent
        # scene saves. Repeated requests reuse matching, still-empty headings.
        result: list[JsonValue] = []
        for outline in request.scenes:
            document = service.studio.current_project(request.project_id).document
            script = next(s for s in document.script_versions if s.id == document.project.current_script_version_id)
            scene = next((s for s in script.structured_scenes if s.heading == outline.heading), None)
            if scene is None:
                saved = service.studio.save_scene(request.project_id, StudioSceneSave(heading=outline.heading,
                    screenplay_text="", lines=[], narrative_purpose=outline.narrative_purpose,
                    target_duration_seconds=outline.target_duration_seconds))
                scene = saved.project.document.script_versions[-1].structured_scenes[-1]
            result.append({"scene_id": scene.id, "heading": scene.heading, "has_text": bool(scene.screenplay_text),
                           "narrative_purpose": scene.narrative_purpose})
        saved_document = service.studio.current_project(request.project_id).document
        saved_script = next(item for item in saved_document.script_versions
                            if item.id == saved_document.project.current_script_version_id)
        return ToolExecution(output=CurrentReadOutput(content={
            "scenes": result, "saved_scene_count": len(result),
            "script_version": {"id": saved_script.id, "version": saved_script.version}}),
            artifact_refs=(ArtifactRef(project_id=request.project_id, artifact_type="script_version",
                artifact_id=saved_script.id, version=saved_script.version),))

    registry.register(ToolSpec(id="script.plan_scenes", version=1, category=ToolCategory.SCRIPT,
        description="将 Agent 决定的场次安排写入当前剧本，立即显示场次目录和叙事目的，返回真实 scene_id。随后用 script.save_scene 逐场填入完整正文与台词。按故事需要决定场次数，不要求凑数；已有同标题场次保留，不覆盖其正文。",
        effect=ToolEffect.WRITE, idempotent=True, input_model=SceneOutlineInput, output_model=CurrentReadOutput, handler=plan_scenes,
        tags=frozenset({"studio", "script", "outline"})))

    registry.register(ToolSpec(id="change.propose_text", version=1, category=ToolCategory.CHANGE,
        description="把用户要求审阅的剧本或分镜修改写成逐项可接受/拒绝的真实文字差异。先读取当前内容，每项 before 必须是指定字段中唯一存在的原文；after 是该段替换文字。场次修改用 affected_shot_ids 指明实际影响的镜头，同场无关视频和人工修改保留；更改台词仍会自动关联对应镜头。可一次提出多处修改。不会改写正式内容；用户处理后原任务收到实际接受/拒绝结果再继续。初稿创作和无需审阅的自主制作直接用保存工具。",
        effect=ToolEffect.WRITE, idempotent=True, input_model=TextProposalInput, output_model=StudioChangeSet, handler=propose_text,
        tags=frozenset({"studio", "review", "partial-edit"})))
    registry.register(ToolSpec(id="timeline.read_editorial", version=3, category=ToolCategory.TIMELINE,
        description="读取当前剪辑。已有 editor_selection 时直接传 timeline_id 与 subtitle_ids/clip_ids：只返回这些对象的真实 FILM 起止、revision及实际镜头采用位置的 SHOT_LOCAL 相交映射；跨镜字幕保持完整本集边界，未知ID报具体错误，不需要先按shot_id猜测或读取全片。精确选择不能同时include_project。无具体ID时兼容：shot_id/anchor_clip_id读取镜头局部剪辑，不传shot_id读取本集摘要；整体结构才需include_project=true。is_saved表示本次返回范围已保存到本集，editorial_is_saved只表示本集已有保存；content_origin=CANDIDATE_PREVIEW表示尚未保存的预览，未采用镜头的局部预览ID不能用于本集精确选择，采用保存后重新读取真实ID。全局editorial_revision不证明本镜已采用。后续timeline.edit_clips必须传相同timeline_id及当前expected_revision；按FILM字段修改不传shot_id，按局部映射修改传shot_id和anchor。实际文件用read_export。",
        effect=ToolEffect.READ, idempotent=True, input_model=TimelineReadInput, output_model=CurrentReadOutput, handler=read_timeline,
        tags=frozenset({"studio", "editorial", "current-content"})))
    registry.register(ToolSpec(id="timeline.read_export", version=2, category=ToolCategory.TIMELINE,
        description="读取并检查本集已存在的剪辑导出；export_id 可精确指定，省略取最新完成记录。实际解码两个 MP4 并返回尺寸、时长、帧率、音轨、保存修订与技术检查。需要看合成字幕、标题、画幅或接缝时，传frame_times_seconds（最多四个EXPORT_FILM时间）及frame_variant=subtitled或clean，实际导出帧按请求顺序附到下一次Worker决策，返回真实PTS及导出修订；时间不得等于或超过导出长度。不会用源Take画面冒充合成结果，旧导出不代表当前剪辑，静帧不证明听审或两帧间运动。不重导出、不修改内容、不调用额外语言或媒体Provider，不依赖旧TimelineManifest；按本次剩余问题选择时点，不强制重复检查。",
        effect=ToolEffect.READ, idempotent=True, input_model=ExportReadInput, output_model=CurrentReadOutput, handler=read_export,
        tags=frozenset({"studio", "editorial", "export", "inspection"})))
    registry.register(ToolSpec(id="timeline.export_editorial", version=4, category=ToolCategory.TIMELINE,
        description="把当前已保存的剪辑导出为实际 MP4，使用相同 LTX 时间线、声音和字幕；支持时间裁切，不支持空间裁景 crop。默认省略width和height，由实际采用的视频尺寸与项目画幅确定画布，保留原素材细节；只有明确需要指定尺寸时同时传宽高，不能把2K素材默认为720p。无采用视频才使用画幅对应默认尺寸。支持硬切、多轨、透明度、PNG图层、时间裁切、变速、翻转、调色、声音、字幕及基本中文标题。空间裁切 crop 尚未接通；实际导出片段的非空 crop 会明确报错，请删除该字段或设为 null，不能把它当作已生效的构图修改。标题支持多行正文、字号、粗体、六位十六进制颜色、百分比位置和透明度；复杂字体/阴影/描边样式、adjustment片段、擦除等转场尚未接通。基本转场支持fade-to-black黑场淡入/淡出，以及同轨相邻片段dissolve；溶解两端时长相同，不改变原切点与总时长，区间不能覆盖第三画面。通过edit_clips精确添加/修改标题和转场；不把未支持的编辑效果当作可导出能力。等待本地 FFmpeg 完成后返回实际尺寸和媒体路径；存在用户剪辑时必须使用本工具，不能用重新组接覆盖用户修改。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineExportInput, output_model=StudioExport, handler=export_timeline,
        tags=frozenset({"studio", "editorial", "export"})))
    registry.register(ToolSpec(id="timeline.edit_clips", version=5, category=ToolCategory.TIMELINE,
        description="精确修改当前片段与字幕，支持时间裁切，不支持空间裁景 crop。只发变化字段，无需读取或回写整个LTX project。request.timeline_id使用editor_selection/read_editorial返回的准确时间线，保留用户当前active选择和其他时间线；省略仅兼容修改当前active。expected_revision必须是当前读取的全局剪辑revision，目标删除或对象不在范围会报错。clips按id修改起点、素材入点、时长、音量、静音或speed，默认同步原声。标题用add_titles:[{id,text,start_seconds,duration_seconds,style?,track_index?}]新增，默认使用基本中文样式与不占用其他片段的可见画面轨；clips按标题id修改text/title_style或remove，title_style只合并font_size(1080高度单位)/font_weight(normal|bold)/color(#RRGGBB)/position_x/position_y/opacity(0–100)。视频/图片用transition_in/transition_out:{type:none|fade-to-black|dissolve,duration_seconds}；none/0清除转场，其他时长必须为正且不超过涉及片段。dissolve自动同步同轨相邻片段另一端，替换/清除或删除时同步清旧搭档；跨镜切点必须用FILM范围，区间不能覆盖第三画面，不改变原切点与总时长。标题与转场不应用到关联声音；subtitles按已有id修改start_seconds/end_seconds/text/style（仅合并给定样式）或remove。首次字幕用add_subtitles:[{id,text,start_seconds,end_seconds,style?,track_index?}]，id必须在所选时间线中不存在，默认复用可用字幕轨或新增字幕轨；可与修改、删除及clips一次保存，保留其他内容。对白字幕来自audio.transcribe_native的实际声音及有依据的识别校正，不把未听见的剧本台词写成转录；后续audio.align_subtitles仅修已有字幕时码。只改字幕可以clips=[]。SHOT_LOCAL修改同时传shot_id与anchor_clip_id，新增字幕或标题不得超过编辑后本次采用时长；跨镜字幕用FILM时间且不传shot_id。返回实际创建/变化/删除的标题、片段、字幕与新revision，包括自动同步的转场另一端。设计时长用shot.save_current。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineEditInput, output_model=CurrentReadOutput, handler=edit_timeline,
        tags=frozenset({"studio", "editorial", "partial-edit"})))
    registry.register(ToolSpec(id="task.read_workflow", version=1, category=ToolCategory.TASK,
        description="读取可选 ComfyUI 的实际连接情况、本项目工作流列表；指定 workflow_id 返回已提交的 API 工作流和输入绑定，便于按用户要求继续处理。",
        effect=ToolEffect.READ, idempotent=True, input_model=WorkflowReadInput, output_model=CurrentReadOutput, handler=read_workflow,
        tags=frozenset({"studio", "comfyui", "workflow"})))
    registry.register(ToolSpec(id="task.run_workflow", version=1, category=ToolCategory.TASK,
        description="执行已配置 ComfyUI 的 API 格式工作流。bindings 绑定项目确切素材到 node_id/input_name，实际输出导入共享资产并保留记录。用于用户需要的高级处理；服务未连接时给出具体连接需求，日常创作不依赖此服务。",
        effect=ToolEffect.EXTERNAL, idempotent=False, input_model=WorkflowInput, output_model=ComfyRun, handler=workflow,
        tags=frozenset({"studio", "comfyui", "workflow"})))
    registry.register(ToolSpec(id="script.save_scene", version=2, category=ToolCategory.SCRIPT,
        description="按完整场次新增或修改当前剧本，实时显示。已有场次带 expected_text；同时写入正文中逐字存在的 lines，summary/narrative_purpose 与当前正文一致。entry_state/exit_state 可记录正文实际建立的相关事实，勿照抄未实现的蓝图。修改回报、关键道具、人物知情或因果时，先用 artifact.read_current 的场次索引定位相关铺垫/回收，再按 scene_id 读取正文；必要的跨场修复用 related_scene_updates 一次保存，每场独立 expected_text，任一冲突整体不保存。无关联无需填写，不默认重写全剧。用每场 affected_shot_ids 限定需更新画面的镜头；[] 保留画面，范围未知时省略。变更台词仍自动关联使用它的镜头。返回实际 changed_scene_ids/changed_shot_ids、script_ref 及本次 saved_scenes 正文/lines/structure_stale，可直接继续创作，无需为找回刚保存的正文重复读取；不包含无关场次正文。只要求剧本时完成即交付。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=SceneWriteInput, output_model=SceneWriteOutput, handler=save_scene,
        tags=frozenset({"studio", "script", "partial-edit"})))
    registry.register(ToolSpec(id="shot.save_current", version=4, category=ToolCategory.SHOT,
        description="保存当前镜头及素材引用，可同时登记同一图片的制作项完成和参考包用途。返回实际修订号及 saved_shot：实际保存的 GENERATION/EDITORIAL 引用、正文字符范围、作用时段和对白 IDs。receipt 同样保留这些事实，可直接核对，不因生成预览为空而重复读取。已提交草稿组也可保存绑定；其预览直接从实际保存的cue派生，不需要另调仅适用正式镜头的 shot.update_reference_plan。reference_plan_preview_scope=GENERATION_ONLY：reference_plan_preview 只解析视频生成参考，EDITORIAL 不进入 selected；其 action_beat_count 是参考计划的动作数量，不是已保存的分镜时段数量。references 使用对象 id、精确 asset_version_id、purpose、field；新引用用 text_anchor.exact 复制原文，重复时补紧邻 prefix/suffix，程序定位且无需手算字符；旧人工 text_start/text_end 保留。同一个镜头（Shot）或同一个时段（Beat）的同一 field 内，各 cue 的文字选区必须互不重叠，完全重合、包含和部分重叠都不允许。身份、衣装等多张图片应各自选择现有正文中准确且互不重叠的片段；不能自动猜绑定，也不要改写正文来凑选区。不同 field、不同 Beat 的选区独立。作用时段来自 beat。使用 expected_revision 保护用户编辑，保留已有视频。已明确选定的同一图片可用可选 media_registration 同时登记具体工作项完成与参考包用途。自有 REFERENCE 构图可直接cue，不要求pack；同图用于多个主体时，各cue须有该主体的自有图或pack关系，登记房间不会覆盖其他道具。entry.subject是实际人物/场景/道具，不是图片容器；可选root是该主体自有可用图，不是任意EDIT parent。已解析的组合请求会一起指出独立entry/cue关系错误，按具体位置修正；CURRENT DRAFT 可用，不切换素材 current/approved 指针，不自动审核、不推断全部服务镜头。全部验证成功才一起保存；相同内容重试复用，目标冲突不部分写。省略仍只保存镜头。保存引用不表示已剪入成片，预览不证明图片或视频质量；预览问题不会把已成功保存的正文报为写入失败。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=ShotWriteInput, output_model=ShotWriteOutput, handler=save_shot,
        tags=frozenset({"studio", "shot", "references"})))
    registry.register(ToolSpec(id="timeline.save_editorial", version=3, category=ToolCategory.TIMELINE,
        description="保存已读取并编辑的 LTX 时间线；支持时间裁切，不支持空间裁景 crop。整集保存必须保留其他片段；单镜头投影必须传 shot_id 和 request.anchor_clip_id，局部合并并顺移后续内容。使用当前 expected_revision，不能用一个镜头的 project 覆盖全片。把同一实际 Take 的多个镜头合并为一个片段时，保留 sourceTakeId 与准确素材路径，并用 sourceShotIds 列出其中已采用的镜头 ID（同 Take 的 shot_windows，包含 sourceShotId）；裁切后仍保留该关联，按实际源区间与各窗口交集定位单镜。已拆成独立单镜并有 sourceWindowStart/End 的片段保留单镜归属，不再挂多镜关联。普通时间裁切优先 edit_clips。空间裁切 crop 尚未接通，提交的 clip.crop 必须省略或设为 null；旧项目含该字段仍可读取，但非空值保存会返回具体片段与字段位置，不会当作已生效的构图修改。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineWriteInput, output_model=CurrentWriteOutput, handler=save_timeline,
        tags=frozenset({"studio", "editorial"})))
    registry.register(ToolSpec(id="timeline.add_asset_overlay", version=1, category=ToolCategory.TIMELINE,
        description="把已有精确图片放到当前剪辑的上层轨道，保留原画面、声音、字幕与时长。透明 PNG 会叠加在原视频上；按完整画幅等比显示。开场说明、菜价等可先用 image.compose_text_plate 指定透明背景和 content_box 排版，再用此工具放入。无需 ComfyUI。传 shot_id 时起止是该镜头内部时间，并传 read_editorial 返回的 anchor_clip_id；不传时为全片时间。overlay_id 是这次放置的稳定 ID；带当前 expected_revision 可直接更新该图版的图片和起止，保留轨道及其他用户设置，相同请求重试不重复插入。",
        effect=ToolEffect.WRITE, idempotent=True, input_model=TimelineOverlayInput,
        output_model=CurrentReadOutput, handler=add_overlay,
        tags=frozenset({"studio", "editorial", "graphics", "overlay", "partial-edit"})))
