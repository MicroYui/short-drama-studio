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
    StudioRename, StudioRelatedSceneUpdate, StudioSceneEdit, StudioSceneSave, StudioShotSave, StudioTimelineSave, StudioProjectCreate,
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
        from short_drama.studio_storyboard import project_draft_shots
        project_draft_shots(result.document)
        project_legacy_references(result.document)
        return result

    def remember_initial_request(self, project_id: str, text: str) -> bool:
        """Recover a blank workspace's brief without treating its scaffold as intent."""
        with self.lock:
            document = self._document(project_id)
            changed = False
            if not document.project.input_text.strip() and text.strip():
                document.project.input_text = text
                changed = True
            first = min(document.script_versions, key=lambda item: item.version)
            current = next(item for item in document.script_versions if item.id == document.project.current_script_version_id)
            # Older empty Studio workspaces inherited the deterministic planner's
            # 300-second placeholder. Only remove that unchanged, identifiable
            # scaffold before production; preserve any authored story decisions.
            if (
                not first.original_text and not first.screenplay_text
                and not first.structured_scenes
                and first.story_intent is not None
                and first.story_intent.protected_elements == ["等待创作要求"]
                and current.story_intent == first.story_intent
                and not document.production_spec_document_id and not document.shots
            ):
                current.story_intent = None
                if current.ending_contract == first.ending_contract:
                    current.ending_contract = None
                changed = True
            if not changed:
                return False
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            self.service.production_manager.refresh_project_memory(document)
            return True

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
        # This is an empty editor, not an authored story. The Agent supplies
        # creative and duration decisions after receiving the user's goal.
        script.story_intent = None
        script.ending_contract = None
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
            edits: list[StudioSceneEdit | StudioRelatedSceneUpdate] = [request, *request.related_scene_updates]
            existing_ids = [edit.scene_id for edit in edits if edit.scene_id is not None]
            if len(existing_ids) != len(set(existing_ids)):
                raise ValueError("同一次保存不能重复修改同一场次。")
            line_owners = {line_id: scene.id for scene in script.structured_scenes for line_id in scene.line_ids}
            # Validate every related scene before touching any current content or
            # media state. A stale payoff must not leave its setup half revised.
            for edit in edits:
                scene = next((item for item in script.structured_scenes if item.id == edit.scene_id), None)
                if edit.scene_id is not None and scene is None:
                    raise KeyError(edit.scene_id)
                if scene is not None and edit.expected_text is not None and scene.screenplay_text != edit.expected_text:
                    raise StudioContentConflict(f"场次 {scene.id} 已更新；关联修改均未保存，请读取最新内容后合并。")
                scene_shot_ids = {shot.id for shot in document.shots if scene is not None and shot.scene_id == scene.id}
                if edit.affected_shot_ids is not None and set(edit.affected_shot_ids) - scene_shot_ids:
                    raise ValueError("受影响镜头必须属于当前修改的场次。")
                if edit.lines is not None:
                    for line in edit.lines:
                        if line.spoken_text and line.spoken_text not in edit.screenplay_text:
                            raise ValueError("结构化台词必须逐字来自当前场次正文")
                        if line.id in line_owners and line_owners[line.id] != edit.scene_id:
                            raise ValueError("结构化台词 ID 必须属于唯一场次，不能覆盖其他场次的台词。")
                    if len({line.id for line in edit.lines}) != len(edit.lines):
                        raise ValueError("同一场次的结构化台词 ID 不能重复。")
            # The UI edits current content; existing Agent inputs keep a stable
            # internal reference so writing one scene cannot invalidate its work.
            previous = script
            script = previous.model_copy(deep=True)
            script.version = max(item.version for item in document.script_versions) + 1
            script.id = f"{project_id}-script-v{script.version}"
            script.parent_version_id = previous.id
            script.change_reason = "当前场次编辑" + "".join(
                f"；{edit.scene_id}：{edit.reason}" for edit in request.related_scene_updates
            )
            script.created_at = datetime.now(UTC).isoformat()
            script.review_report = None
            script.review_disposition = None
            script.final_cold_read_report = None
            changed_scene_ids: list[str] = []
            affected: set[str] = set()
            for edit in edits:
                scene = next((item for item in script.structured_scenes if item.id == edit.scene_id), None)
                previous_scene = None if scene is None else scene.model_copy(deep=True)
                scene_shots = [shot for shot in document.shots if scene is not None and shot.scene_id == scene.id]
                previous_lines = {line.id: line for line in script.lines if scene is not None and line.id in scene.line_ids}
                if scene is None:
                    scene = StructuredScene(id=f"scene-{uuid4().hex[:12]}", heading=edit.heading, summary="", line_ids=[])
                    script.structured_scenes.append(scene)
                was_structure_stale = scene.id in script.structure_stale_scene_ids
                scene.heading = edit.heading
                changed_text = scene.screenplay_text != edit.screenplay_text
                scene.screenplay_text = edit.screenplay_text
                if edit.summary is not None:
                    scene.summary = edit.summary
                if edit.target_duration_seconds is not None:
                    scene.target_duration_seconds = edit.target_duration_seconds
                if edit.narrative_purpose is not None:
                    scene.narrative_purpose = edit.narrative_purpose
                if "entry_state" in edit.model_fields_set:
                    scene.entry_state = edit.entry_state
                if "exit_state" in edit.model_fields_set:
                    scene.exit_state = edit.exit_state
                changed_lines: set[str] = set()
                if edit.lines is not None:
                    next_lines = {line.id: line for line in edit.lines}
                    changed_lines = {line_id for line_id in previous_lines.keys() | next_lines.keys()
                                     if previous_lines.get(line_id) != next_lines.get(line_id)}
                    replaced_ids = set(scene.line_ids)
                    script.lines = [line for line in script.lines if line.id not in replaced_ids] + edit.lines
                    scene.line_ids = [line.id for line in edit.lines]
                    script.structure_stale_scene_ids = [item for item in script.structure_stale_scene_ids if item != scene.id]
                elif changed_text and scene.id not in script.structure_stale_scene_ids:
                    script.structure_stale_scene_ids.append(scene.id)
                if scene != previous_scene or changed_lines or (
                    was_structure_stale and scene.id not in script.structure_stale_scene_ids
                ):
                    changed_scene_ids.append(scene.id)
                    affected.update(edit.affected_shot_ids if edit.affected_shot_ids is not None
                                    else (shot.id for shot in scene_shots))
                    affected.update(shot.id for shot in scene_shots if changed_lines.intersection(shot.dialogue_line_ids))
            scene_line_ids = [line_id for scene in script.structured_scenes for line_id in scene.line_ids]
            if len(scene_line_ids) != len(set(scene_line_ids)):
                raise ValueError("结构化台词 ID 必须属于唯一场次，不能覆盖其他场次的台词。")
            if not changed_scene_ids and script.structure_stale_scene_ids == previous.structure_stale_scene_ids:
                return StudioMutationResult(project=self.service.get_project(project_id), notices=["当前场次内容未变化。"])
            script.order_lines_by_scenes()
            script.screenplay_text = "\n\n".join(f"{item.heading}\n{item.screenplay_text}" for item in script.structured_scenes)
            script.original_text = script.screenplay_text
            script.spoken_text = "\n".join(line.spoken_text for line in script.lines)
            script.subtitle_text = "\n".join(line.subtitle_text for line in script.lines)
            script.approval_status = ApprovalStatus.APPROVED
            document.script_versions.append(script)
            document.project.current_script_version_id = script.id
            for shot in document.shots:
                if shot.id in affected:
                    shot.freshness = Freshness.STALE
                    shot.revision += 1
            self.service.repository.mark_takes_stale(project_id, sorted(affected))
            document.project.updated_at = datetime.now(UTC).isoformat()
            self.service.repository.save_project(document)
            return StudioMutationResult(project=self.service.get_project(project_id),
                changed_scene_ids=changed_scene_ids, changed_shot_ids=sorted(affected),
                notices=["剧本及本次明确关联的场次已保存；相关镜头保留。下一次制作前由 Agent 按当前正文同步台词与分镜。"])

    def save_shot(self, project_id: str, shot_id: str, request: StudioShotSave) -> StudioMutationResult:
        with self.lock:
            document = self._document(project_id)
            from short_drama.studio_storyboard import save_draft_shot
            if save_draft_shot(self.service, document, shot_id, request):
                return StudioMutationResult(project=self.current_project(project_id), notices=["已保存到当前分镜组；其他组继续制作。"])
            current = next((item for item in document.shots if item.id == shot_id), None)
            if current is None:
                raise KeyError(shot_id)
            if current.revision != request.expected_revision:
                raise StudioContentConflict(
                    f"镜头 {shot_id} 已更新（请求修订 {request.expected_revision}，当前修订 {current.revision}）；"
                    "本次未保存。请读取最新镜头，合并所需修改后重试。"
                )
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
