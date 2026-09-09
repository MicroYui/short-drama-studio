"""Task-sized current-content tools used by the product Agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from pydantic import JsonValue
from short_drama.models import DomainModel
from short_drama.studio_models import StudioSceneSave, StudioShotSave, StudioTimelineSave, StudioExportRequest, StudioExport, StudioEditorialEdit
from short_drama.tools.contracts import ArtifactRef, ToolCategory, ToolEffect, ToolExecution, ToolModel, ToolSpec
from short_drama.tools.registry import ToolRegistry
from short_drama.studio_comfy import ComfyRunRequest, ComfyRun

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
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id,
            scene_ids=[], shot_ids=[request.shot_id], notices=result.notices))

    def save_timeline(request: TimelineWriteInput) -> ToolExecution[CurrentWriteOutput]:
        if request.shot_id:
            from short_drama.studio_shot_editorial import save_shot_editorial
            save_shot_editorial(service, request.project_id, request.shot_id, request.request)
        else:
            service.studio.save_timeline(request.project_id, request.request)
        return ToolExecution(output=CurrentWriteOutput(project_id=request.project_id, scene_ids=[], shot_ids=[], notices=["已保存当前剪辑。用户已有剪辑必须先读取、局部修改并保留。 "]))

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
        description="按完整场次新增或直接修改当前剧本，实时显示。已有场次需带 expected_text；同时写入正文中逐字存在的 lines，保留不受影响的场次。只要求剧本时完成后停止，不建立分镜/资产/视频。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=SceneWriteInput, output_model=CurrentWriteOutput, handler=save_scene,
        tags=frozenset({"studio", "script", "partial-edit"})))
    registry.register(ToolSpec(id="shot.save_current", version=1, category=ToolCategory.SHOT,
        description="保存一个当前镜头和逐时段分镜。references 使用对象 id、精确 asset_version_id、purpose、field 和 Unicode 字符 text_start/text_end，作用时段来自 beat。使用 expected_revision 保护用户编辑，保留已有视频。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=ShotWriteInput, output_model=CurrentWriteOutput, handler=save_shot,
        tags=frozenset({"studio", "shot", "references"})))
    registry.register(ToolSpec(id="timeline.save_editorial", version=1, category=ToolCategory.TIMELINE,
        description="保存已读取并编辑的 LTX 时间线。整集保存必须保留其他片段；单镜头投影必须传 shot_id 和 request.anchor_clip_id，局部合并并顺移后续内容。使用当前 expected_revision，不能用一个镜头的 project 覆盖全片。普通裁切优先 edit_clips。",
        effect=ToolEffect.WRITE, idempotent=False, input_model=TimelineWriteInput, output_model=CurrentWriteOutput, handler=save_timeline,
        tags=frozenset({"studio", "editorial"})))
