"""Task-sized current-content tools used by the product Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from pydantic import JsonValue, Field
from short_drama.models import DomainModel
from short_drama.studio_models import StudioSceneSave, StudioShotSave, StudioTimelineSave, StudioExportRequest, StudioExport, StudioEditorialEdit, StudioAssetOverlay
from short_drama.tools.contracts import ArtifactRef, ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolSpec
from short_drama.tools.registry import ToolRegistry
from short_drama.studio_comfy import ComfyRunRequest, ComfyRun
from short_drama.studio_models import StudioChangeRequest, StudioChangeSet, StudioSceneOutline

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


class TimelineWriteInput(ToolModel):
    project_id: str
    shot_id: str | None = None
    request: StudioTimelineSave


class TimelineReadInput(ToolModel):
    project_id: str
    include_project: bool = False
    shot_id: str | None = None
    anchor_clip_id: str | None = None


class TimelineExportInput(ToolModel):
    project_id: str
    request: StudioExportRequest


class ExportReadInput(ToolModel):
    project_id: str
    export_id: str | None = None


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


class TextProposalInput(ToolModel):
    project_id: str
    request: StudioChangeRequest


class SceneOutlineInput(ToolModel):
    project_id: str
    scenes: list[StudioSceneOutline] = Field(min_length=1, max_length=200)


def register_tools(registry: ToolRegistry, service: ShortDramaService) -> None:
    def current(request: CurrentReadInput) -> ToolExecution[CurrentReadOutput]:
        document = service.studio.current_project(request.project_id).document
        script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
        content: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "script_id": script.id,
            "episode_title": document.project.episode_title,
            "input_text": document.project.input_text,
            "scene_index": [{"id": scene.id, "heading": scene.heading, "summary": scene.summary} for scene in script.structured_scenes],
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
        if request.shot_id:
            shot = next((item for item in document.shots if item.id == request.shot_id), None)
            if shot is None:
                raise KeyError(request.shot_id)
            content["shot"] = cast(dict[str, JsonValue], shot.model_dump(mode="json"))
        return ToolExecution(output=CurrentReadOutput(content=content))

    def save_scene(request: SceneWriteInput) -> ToolExecution[CurrentWriteOutput]:
        result = service.studio.save_scene(request.project_id, request.request)
        document = result.project.document
        script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id,
            scene_ids=[item.id for item in script.structured_scenes], shot_ids=[], notices=result.notices),
            artifact_refs=(ArtifactRef(project_id=request.project_id, artifact_type="script_version", artifact_id=script.id, version=script.version),))

    def save_shot(request: ShotWriteInput) -> ToolExecution[CurrentWriteOutput]:
        result = service.studio.save_shot(request.project_id, request.shot_id, request.request)
        saved = next(shot for shot in result.project.document.shots if shot.id == request.shot_id)
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id,
            scene_ids=[], shot_ids=[saved.id], notices=result.notices,
            shot_revisions={saved.id: saved.revision}),
            artifact_refs=(ArtifactRef(project_id=request.project_id, artifact_type="shot",
                artifact_id=saved.id, version=saved.revision),))

    def save_timeline(request: TimelineWriteInput) -> ToolExecution[CurrentWriteOutput]:
        if request.shot_id:
            from short_drama.studio_shot_editorial import save_shot_editorial
            save_shot_editorial(service, request.project_id, request.shot_id, request.request)
        else:
            service.studio.save_timeline(request.project_id, request.request)
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id, scene_ids=[], shot_ids=[], notices=["已保存当前剪辑。用户已有剪辑必须先读取、局部修改并保留。 "]))

    def add_overlay(request: TimelineOverlayInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_overlays import add_asset_overlay
        result = add_asset_overlay(service, request.project_id, request.request)
        return ToolExecution(output=CurrentReadOutput(content={
            "project_id": request.project_id, "editorial_revision": result.revision,
            "overlay_id": request.request.overlay_id,
            "asset_version_id": request.request.asset_version_id,
            "saved": True,
        }))

    def read_timeline(request: TimelineReadInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_editorial import read_editorial, EditorProject
        from short_drama.studio_shot_editorial import read_shot_editorial
        value = read_shot_editorial(service, request.project_id, request.shot_id, request.anchor_clip_id) if request.shot_id else read_editorial(service, request.project_id)
        editor = EditorProject.model_validate(value.project)
        timeline = next(item for item in editor.timelines if item.id == editor.activeTimelineId)
        content: dict[str, JsonValue] = {"project_id": request.project_id, "editorial_revision": value.revision,
            "is_saved": value.revision > 0, "timeline_id": timeline.id,
            "clips": [{"id": clip.id, "type": clip.type, "source_shot_id": (clip.model_extra or {}).get("sourceShotId"),
                "start_seconds": clip.startTime, "duration_seconds": clip.duration, "source_in_seconds": clip.trimStart,
                "speed": clip.speed, "reversed": clip.reversed,
                "source_window_start_seconds": (clip.model_extra or {}).get("sourceWindowStart"),
                "source_window_end_seconds": (clip.model_extra or {}).get("sourceWindowEnd"),
                "volume": clip.volume, "muted": clip.muted, "track_index": clip.trackIndex} for clip in timeline.clips],
            "subtitles": [cast(dict[str, JsonValue], item.model_dump(mode="json")) for item in timeline.subtitles],
            "exports": [cast(JsonValue, item) for item in service.get_project(request.project_id).document.studio_exports]}
        if value.scope:
            content["shot_scope"] = cast(JsonValue, value.scope.model_dump(mode="json"))
            content["time_coordinates"] = "当前镜头内部时间，从 0 秒开始。素材入点仍是源文件时间。edit_clips 必须传递相同 shot_id 和 anchor_clip_id，保存后同步成片。"
        if request.include_project:
            # Assets are present once in the library; save rehydrates each clip by assetId.
            for item in editor.timelines:
                for clip in item.clips:
                    clip.asset = None
            content["project"] = cast(dict[str, JsonValue], editor.model_dump(mode="json", exclude_none=True))
        return ToolExecution(output=CurrentReadOutput(content=content))

    def read_export(request: ExportReadInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_export import inspect_export
        return ToolExecution(output=CurrentReadOutput(content=inspect_export(service, request.project_id, request.export_id)))

    def export_timeline(request: TimelineExportInput) -> ToolExecution[StudioExport]:
        from short_drama.studio_export import start_export
        record = start_export(service, request.project_id, request.request, background=False)
        if record.status != "completed":
            raise ValueError(record.error or "成片导出未完成。")
        return ToolExecution(output=record)

    def edit_timeline(request: TimelineEditInput) -> ToolExecution[CurrentReadOutput]:
        from short_drama.studio_editorial import edit_editorial
        value = edit_editorial(service, request.project_id, request.request)
        return ToolExecution(output=CurrentReadOutput(content={"editorial_revision": value.revision, "message": "所选片段已保存，其他剪辑与字幕保留。"}))

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
    registry.register(ToolSpec(id="timeline.read_editorial", version=1, category=ToolCategory.TIMELINE,
        description="读取当前剪辑的修订号、时间、声音、字幕与导出。单镜头编辑传 shot_id，返回仅该镜头的局部时间线与使用位置；多处采用时用 anchor_clip_id 定位。未传 shot_id 读取整集。默认摘要足够局部编辑，整体结构才需 include_project=true。实际文件用 read_export；revision=0 时先保存再导出。",
        effect=ToolEffect.READ, idempotent=True, input_model=TimelineReadInput, output_model=CurrentReadOutput, handler=read_timeline,
        tags=frozenset({"studio", "editorial", "current-content"})))
    registry.register(ToolSpec(id="timeline.read_export", version=1, category=ToolCategory.TIMELINE,
        description="读取并检查本集已存在的剪辑导出；export_id 可精确指定，省略取最新完成记录。实际解码两个 MP4 并返回尺寸、时长、帧率、音轨、保存修订与技术检查。不重导出、不修改内容，不依赖旧 TimelineManifest；普通成片导出验收优先用此工具。",
        effect=ToolEffect.READ, idempotent=True, input_model=ExportReadInput, output_model=CurrentReadOutput, handler=read_export,
        tags=frozenset({"studio", "editorial", "export", "inspection"})))
    registry.register(ToolSpec(id="timeline.export_editorial", version=1, category=ToolCategory.TIMELINE,
        description="把当前已保存的剪辑导出为实际 MP4，使用相同 LTX 时间线、声音和字幕。等待本地 FFmpeg 完成后返回媒体路径；存在用户剪辑时必须使用本工具，不能用重新组接覆盖用户修改。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineExportInput, output_model=StudioExport, handler=export_timeline,
        tags=frozenset({"studio", "editorial", "export"})))
    registry.register(ToolSpec(id="timeline.edit_clips", version=1, category=ToolCategory.TIMELINE,
        description="修改已读取片段的起点、素材入点、时长、音量或静音；只发送变化字段，默认同步原声。单镜头裁切在 request 中传 shot_id 与读取到的 anchor_clip_id，以局部时间编辑，保存后按时长差顺移后续镜头、声音、字幕。整集编辑不传 shot_id，保留其他片段的位置。设计时长用 shot.save_current 修改，裁切不改写分镜。clips=[] 可保存首次投影。",
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
    registry.register(ToolSpec(id="script.save_scene", version=1, category=ToolCategory.SCRIPT,
        description="按完整场次新增或直接修改当前剧本，实时显示。已有场次需带 expected_text；同时写入正文中逐字存在的 lines，保留不受影响的场次。已读取镜头并判断修改范围时，用 affected_shot_ids 限定需更新画面的镜头，保留同场其它镜头的人工修改和可用视频；范围未知时省略。更改台词会自动关联使用该台词的镜头。只要求剧本时完成后停止，不建立分镜/资产/视频。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=SceneWriteInput, output_model=CurrentWriteOutput, handler=save_scene,
        tags=frozenset({"studio", "script", "partial-edit"})))
    registry.register(ToolSpec(id="shot.save_current", version=1, category=ToolCategory.SHOT,
        description="保存一个当前镜头和逐时段分镜，返回实际保存的镜头修订号与精确引用。references 使用对象 id、精确 asset_version_id、purpose、field 和 Unicode 字符 text_start/text_end，作用时段来自 beat。使用 expected_revision 保护用户编辑，保留已有视频。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=ShotWriteInput, output_model=CurrentWriteOutput, handler=save_shot,
        tags=frozenset({"studio", "shot", "references"})))
    registry.register(ToolSpec(id="timeline.save_editorial", version=1, category=ToolCategory.TIMELINE,
        description="保存已读取并编辑的 LTX 时间线。整集保存必须保留其他片段；单镜头投影必须传 shot_id 和 request.anchor_clip_id，局部合并并顺移后续内容。使用当前 expected_revision，不能用一个镜头的 project 覆盖全片。普通裁切优先 edit_clips。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineWriteInput, output_model=CurrentWriteOutput, handler=save_timeline,
        tags=frozenset({"studio", "editorial"})))
    registry.register(ToolSpec(id="timeline.add_asset_overlay", version=1, category=ToolCategory.TIMELINE,
        description="把已有精确图片放到当前剪辑的上层轨道，保留原画面、声音、字幕与时长。透明 PNG 会叠加在原视频上；按完整画幅等比显示。开场说明、菜价等可先用 image.compose_text_plate 指定透明背景和 content_box 排版，再用此工具放入。无需 ComfyUI。传 shot_id 时起止是该镜头内部时间，并传 read_editorial 返回的 anchor_clip_id；不传时为全片时间。overlay_id 是这次放置的稳定 ID；带当前 expected_revision 可直接更新该图版的图片和起止，保留轨道及其他用户设置，相同请求重试不重复插入。",
        effect=ToolEffect.WRITE, idempotent=True, input_model=TimelineOverlayInput,
        output_model=CurrentReadOutput, handler=add_overlay,
        tags=frozenset({"studio", "editorial", "graphics", "overlay", "partial-edit"})))
