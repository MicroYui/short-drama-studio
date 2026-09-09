"""Product commands over the existing project and episode documents."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import TYPE_CHECKING
from uuid import uuid4

from short_drama.models import (
    ApprovalStatus, CreateProjectRequest, Freshness, ProjectAggregate,
    ProjectDocument, ProjectStage, SourceMode, StructuredScene,
    ProductionDocumentOrigin,
)
from short_drama.production_contracts import StoryboardPatchRequest, StoryboardShotReplacement
from short_drama.studio_models import (
    StudioEpisodeCreate, StudioEpisodes, StudioMutationResult,
    StudioRename, StudioSceneSave, StudioShotSave, StudioTimelineSave, StudioProjectCreate,
    StudioPreferences,
)
from short_drama.studio_references import project_legacy_references, sync_shot_references

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


class StudioContentConflict(ValueError):
    """A scoped editor is based on content that has since changed."""


class StudioWorkspaceService:
    def __init__(self, service: ShortDramaService, *, lock: RLock) -> None:
        self.service = service
        self.lock = lock

    def _document(self, project_id: str) -> ProjectDocument:
        document = self.service.repository.get_project(project_id)
        if document is None:
            raise KeyError(project_id)
        return document

    def current_project(self, project_id: str) -> ProjectAggregate:
        result = self.service.get_project(project_id)
        project_legacy_references(result.document)
        return result

    def create_project(self, request: StudioProjectCreate) -> ProjectAggregate:
        created = self.service.create_project(CreateProjectRequest(
            name=request.name, source_mode=SourceMode.IDEA_EXPANSION,
            input_text=request.brief or "等待创作要求",
        ))
        document = created.document
        document.project.input_text = request.brief
        document.project.stage = ProjectStage.INPUT
        document.project.current_manifest_id = None
        document.shots = []
        document.reference_plan = []
        document.execution_plan = []
        document.assets = []
        document.reference_packs = []
        document.manifests = []
        document.production_documents = []
        document.output_slots = []
        document.storyboard_document_id = None
        document.production_spec_document_id = None
        document.master_audio_document_id = None
        document.audio_structure_document_id = None
        document.timed_text_document_id = None
        document.asset_plan_document_id = None
        document.generation_plan_document_id = None
        script = document.script_versions[-1]
        script.original_text = script.screenplay_text = script.spoken_text = script.subtitle_text = ""
        script.structured_scenes = []
        script.lines = []
        script.approval_status = ApprovalStatus.DRAFT
        bible = document.bible_versions[-1]
        bible.characters = []
        bible.locations = []
        bible.props = []
        self.service.repository.save_project(document)
        return self.current_project(document.project.id)

    def preferences(self, project_id: str, request: StudioPreferences) -> ProjectAggregate:
        from short_drama.production_profiles import production_method_skill_catalog
        available = {item.id for item in production_method_skill_catalog()}
        if any(item not in available for item in request.method_skill_ids):
            raise ValueError("创作方法不存在，请刷新方法列表。")
        with self.lock:
            document = self._document(project_id)
            document.project.preferred_method_skill_ids = list(dict.fromkeys(request.method_skill_ids))
            document.project.creative_direction = request.creative_direction
            document.project.aspect_ratio = request.aspect_ratio
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            self.service.production_manager.refresh_project_memory(document)
            return self.current_project(project_id)

    def episodes(self, project_id: str) -> StudioEpisodes:
        selected = self._document(project_id)
        root_id = selected.project.parent_project_id or selected.project.id
        episodes = [
            document.project for document in self.service.repository.list_projects()
            if document.project.id == root_id or document.project.parent_project_id == root_id
        ]
        return StudioEpisodes(project_id=root_id, episodes=sorted(episodes, key=lambda item: item.episode_number))

    def create_episode(self, project_id: str, request: StudioEpisodeCreate) -> ProjectAggregate:
        with self.lock:
            episodes = self.episodes(project_id)
            root = self._document(episodes.project_id)
            number = max(item.episode_number for item in episodes.episodes) + 1
            created = self.create_project(StudioProjectCreate(name=request.title))
            document = created.document
            document.project.parent_project_id = root.project.id
            document.project.episode_number = number
            document.project.episode_title = request.title
            document.project.stage = ProjectStage.INPUT
            document.project.input_text = ""
            document.project.current_manifest_id = None
            document.project.production_profile = root.project.production_profile
            document.project.target_duration_seconds = root.project.target_duration_seconds
            document.project.preferred_method_skill_ids = list(root.project.preferred_method_skill_ids)
            document.project.creative_direction = root.project.creative_direction
            document.project.aspect_ratio = root.project.aspect_ratio
            document.project.language = root.project.language
            document.assets = root.assets
            document.reference_packs = root.reference_packs
            document.shots = []
            document.reference_plan = []
            document.execution_plan = []
            document.manifests = []
            script = document.script_versions[-1]
            script.original_text = script.screenplay_text = script.spoken_text = script.subtitle_text = ""
            script.structured_scenes = []
            script.lines = []
            script.approval_status = ApprovalStatus.DRAFT
            bible = next(item for item in root.bible_versions if item.id == root.project.current_bible_version_id).model_copy(deep=True)
            bible.id = document.project.current_bible_version_id
            bible.project_id = document.project.id
            document.bible_versions = [bible]
            self.service.repository.save_project(document)
            return self.service.get_project(document.project.id)

    def rename(self, project_id: str, request: StudioRename) -> ProjectAggregate:
        with self.lock:
            document = self._document(project_id)
            if document.project.parent_project_id:
                document.project.episode_title = request.name
            document.project.name = request.name
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            return self.service.get_project(project_id)

    def save_scene(self, project_id: str, request: StudioSceneSave) -> StudioMutationResult:
        with self.lock:
            document = self._document(project_id)
            script = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
            # The UI edits current content; existing Agent inputs keep a stable
            # internal reference so writing one scene cannot invalidate its work.
            previous = script
            script = previous.model_copy(deep=True)
            script.version = max(item.version for item in document.script_versions) + 1
            script.id = f"{project_id}-script-v{script.version}"
            script.parent_version_id = previous.id
            script.change_reason = "当前场次编辑"
            script.created_at = datetime.now(UTC).isoformat()
            script.review_report = None
            script.review_disposition = None
            script.final_cold_read_report = None
            scene = next((item for item in script.structured_scenes if item.id == request.scene_id), None)
            if request.scene_id is not None and scene is None:
                raise KeyError(request.scene_id)
            if scene is not None and request.expected_text is not None and scene.screenplay_text != request.expected_text:
                raise StudioContentConflict("此场次已更新；你的编辑仍保留，请读取最新内容后合并。")
            if scene is None:
                scene = StructuredScene(id=f"scene-{uuid4().hex[:12]}", heading=request.heading, summary="", line_ids=[])
                script.structured_scenes.append(scene)
            scene.heading = request.heading
            changed_text = scene.screenplay_text != request.screenplay_text
            scene.screenplay_text = request.screenplay_text
            if request.summary is not None:
                scene.summary = request.summary
            if request.lines is not None:
                for line in request.lines:
                    if line.spoken_text and line.spoken_text not in request.screenplay_text:
                        raise ValueError("结构化台词必须逐字来自当前场次正文")
                replaced_ids = set(scene.line_ids)
                script.lines = [line for line in script.lines if line.id not in replaced_ids] + request.lines
                scene.line_ids = [line.id for line in request.lines]
                script.structure_stale_scene_ids = [item for item in script.structure_stale_scene_ids if item != scene.id]
            elif changed_text and scene.id not in script.structure_stale_scene_ids:
                script.structure_stale_scene_ids.append(scene.id)
            script.screenplay_text = "\n\n".join(f"{item.heading}\n{item.screenplay_text}" for item in script.structured_scenes)
            script.original_text = script.screenplay_text
            script.spoken_text = "\n".join(line.spoken_text for line in script.lines)
            script.subtitle_text = "\n".join(line.subtitle_text for line in script.lines)
            script.approval_status = ApprovalStatus.APPROVED
            document.script_versions.append(script)
            document.project.current_script_version_id = script.id
            affected = [shot.id for shot in document.shots if shot.scene_id == scene.id]
            for shot in document.shots:
                if shot.id in affected:
                    shot.freshness = Freshness.STALE
                    shot.revision += 1
            self.service.repository.mark_takes_stale(project_id, affected)
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            return StudioMutationResult(project=self.service.get_project(project_id), notices=["剧本已保存；相关镜头保留。下一次制作前由 Agent 按当前正文同步台词与分镜。"])

    def save_shot(self, project_id: str, shot_id: str, request: StudioShotSave) -> StudioMutationResult:
        with self.lock:
            document = self._document(project_id)
            current = next((item for item in document.shots if item.id == shot_id), None)
            if current is None:
                raise KeyError(shot_id)
            if current.revision != request.expected_revision:
                raise StudioContentConflict("此镜头已更新；你的修改已保留在编辑器中，请合并最新内容。")
            if request.shot.id != shot_id or request.shot.project_id != project_id:
                raise ValueError("镜头必须属于当前集")
            replacement = request.shot.model_copy(deep=True)
            replacement.revision = current.revision + 1
            replacement.freshness = Freshness.CURRENT
            sync_shot_references(document, replacement)
            if document.storyboard_document_id is not None:
                storyboard = self.service.get_storyboard(project_id)
                source_shot = next((item for item in storyboard.storyboard.shots if item.id == shot_id), None)
                if source_shot is not None:
                    shots_by_id = {item.id: item for item in storyboard.storyboard.shots}
                    shots_by_id[shot_id] = replacement
                    clip_groups = [
                        group.model_copy(update={"duration_seconds": float(sum(
                            (Decimal(str(shots_by_id[item_id].duration_seconds)) for item_id in group.shot_ids),
                            start=Decimal(0),
                        ))}) if shot_id in group.shot_ids else group
                        for group in storyboard.storyboard.clip_groups
                    ]
                    self.service.patch_storyboard(project_id, StoryboardPatchRequest(
                        base_version_id=storyboard.version.id,
                        clip_groups=clip_groups,
                        replacements=[StoryboardShotReplacement(shot_id=shot_id,
                            expected_revision=source_shot.revision, replacement=replacement)],
                        origin=ProductionDocumentOrigin.USER,
                    ), current_content_edit=True)
                    document = self._document(project_id)
                    replacement = next(item for item in document.shots if item.id == shot_id)
                    sync_shot_references(document, replacement)
            document.shots = [replacement if item.id == shot_id else item for item in document.shots]
            self.service.repository.mark_takes_stale(project_id, [shot_id])
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            return StudioMutationResult(project=self.current_project(project_id), notices=["镜头设计已保存；已有视频保留，可按新设计重新生成。"])

    def save_timeline(self, project_id: str, request: StudioTimelineSave) -> StudioMutationResult:
        with self.lock:
            document = self._document(project_id)
            current_revision = (document.editorial_timeline or {}).get("revision", 0)
            if current_revision != request.expected_revision:
                raise StudioContentConflict("剪辑已在其他位置更新；当前编辑仍保留，请合并后再保存。")
            timeline = dict(request.timeline)
            if isinstance(timeline.get("project"), dict):
                from short_drama.studio_editorial import validate_editorial
                from typing import cast
                from pydantic import JsonValue
                if cast(dict[str, JsonValue], timeline["project"]).get("studioShotScope"):
                    raise ValueError("这是镜头局部剪辑；请使用镜头保存入口，不能用局部内容覆盖整集。")
                parsed = validate_editorial(self.service, project_id, cast(dict[str, JsonValue], timeline["project"]))
                timeline["project"] = cast(dict[str, JsonValue], parsed.model_dump(mode="json"))
            timeline["revision"] = request.expected_revision + 1
            document.editorial_timeline = timeline
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            return StudioMutationResult(project=self.service.get_project(project_id))
