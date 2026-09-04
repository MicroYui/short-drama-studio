"""Read-only artifact summaries used to compile focused Agent context."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from typing import Protocol, cast

from short_drama.agent_runtime.models import ArtifactRef, ArtifactSummary
from short_drama.image_models import ImageGenerationTask
from short_drama.models import (
    GenerationTask,
    PlanningRunRecord,
    PlanningRunStep,
    ProjectDocument,
    Take,
)


class ProjectDocumentStore(Protocol):
    def get_project(self, project_id: str) -> ProjectDocument | None: ...

    def get_planning_run(self, run_id: str) -> PlanningRunRecord | None: ...

    def get_task(self, task_id: str) -> GenerationTask | None: ...

    def get_image_task(self, task_id: str) -> ImageGenerationTask | None: ...

    def get_take(self, take_id: str) -> Take | None: ...


class ArtifactRecordLike(Protocol):
    id: str
    project_id: str

    def model_dump_json(self) -> str: ...


class ProjectArtifactRecordStore(Protocol):
    def list_exports(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_provider_runs(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def list_route_evidence(self, project_id: str) -> Sequence[ArtifactRecordLike]: ...

    def get_video_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_take_comparison(self, report_id: str) -> ArtifactRecordLike | None: ...

    def list_reference_visual_reranks(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_keyframe_comparisons(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def list_input_compatibility_reports(
        self, project_id: str
    ) -> Sequence[ArtifactRecordLike]: ...

    def get_audio_inspection(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_episode_review(self, report_id: str) -> ArtifactRecordLike | None: ...

    def get_h3_route_plan(self, plan_id: str) -> ArtifactRecordLike | None: ...


def _current_script(document: ProjectDocument):
    return next(
        (
            item
            for item in document.script_versions
            if item.id == document.project.current_script_version_id
        ),
        None,
    )


def _current_bible(document: ProjectDocument):
    return next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        None,
    )


class ProjectArtifactResolver:
    """Resolve focused summaries from the canonical ProjectDocument."""

    def __init__(self, projects: ProjectDocumentStore) -> None:
        self._projects = projects

    def summarize_artifact(self, ref: ArtifactRef) -> ArtifactSummary | None:
        document = self._projects.get_project(ref.project_id)
        if document is None:
            return None
        if ref.artifact_type == "project":
            return ArtifactSummary(
                ref=ref,
                title=document.project.name,
                summary=project_summary(document),
            )
        if ref.artifact_type in {"content_planning_job", "planning_run"}:
            planning_run = self._projects.get_planning_run(ref.artifact_id)
            if (
                planning_run is None
                or planning_run.id != ref.artifact_id
                or planning_run.project_id != ref.project_id
            ):
                return None
            step_title = planning_run.current_step.value
            if (
                planning_run.current_step == PlanningRunStep.SCENE_WRITER
                and planning_run.total_scenes
            ):
                current = min(
                    planning_run.next_script_scene_sequence,
                    planning_run.total_scenes,
                )
                step_title += f" {current}/{planning_run.total_scenes}"
            elif (
                planning_run.current_step
                == PlanningRunStep.STORY_METADATA_REVISION
            ):
                if planning_run.revision_repair_round:
                    step_title += (
                        " verifier-repair "
                        f"round {planning_run.revision_repair_round}"
                    )
                else:
                    step_title += " initial"
            elif (
                planning_run.current_step == PlanningRunStep.SCENE_REVISION
                and planning_run.revision_scene_sequences
            ):
                total = len(planning_run.revision_scene_sequences)
                current = min(
                    planning_run.next_revision_scene_index + 1,
                    total,
                )
                step_title += f" {current}/{total}"
            elif (
                planning_run.current_step == PlanningRunStep.SCENE_DIRECTOR
                and planning_run.total_scenes
            ):
                current = min(
                    planning_run.next_scene_sequence,
                    planning_run.total_scenes,
                )
                step_title += f" {current}/{planning_run.total_scenes}"
            return ArtifactSummary(
                ref=ref,
                title=f"Content planning job {step_title}",
                summary=(
                    "Execution cursor only; canonical script, Bible, shots, and assets "
                    "remain in ProjectDocument. "
                    + planning_run.model_dump_json()
                ),
            )
        if ref.artifact_type in {"script", "script_version"}:
            script = next(
                (
                    item
                    for item in document.script_versions
                    if item.id == ref.artifact_id
                    or (ref.version is not None and item.version == ref.version)
                ),
                None,
            )
            if script is None:
                return None
            scenes: list[str] = []
            line_by_id = {line.id: line for line in script.lines}
            for scene in script.structured_scenes:
                lines = [
                    f"{line_by_id[line_id].speaker}: {line_by_id[line_id].spoken_text}"
                    for line_id in scene.line_ids
                    if line_id in line_by_id
                ]
                scenes.append(
                    f"{scene.id} {scene.heading} | {scene.summary} | " + " / ".join(lines)
                )
            return ArtifactSummary(
                ref=ref,
                title=f"Script v{script.version}",
                summary="\n".join(scenes) or script.spoken_text,
            )
        if ref.artifact_type in {"bible", "bible_version"}:
            bible = next(
                (
                    item
                    for item in document.bible_versions
                    if item.id == ref.artifact_id
                    or (ref.version is not None and item.version == ref.version)
                ),
                None,
            )
            if bible is None:
                return None
            summary = {
                "characters": [item.model_dump(mode="json") for item in bible.characters],
                "locations": [item.model_dump(mode="json") for item in bible.locations],
                "props": [item.model_dump(mode="json") for item in bible.props],
                "style": bible.style.model_dump(mode="json"),
            }
            return ArtifactSummary(
                ref=ref,
                title=f"Project Bible v{bible.version}",
                summary=json.dumps(summary, ensure_ascii=False),
            )
        if ref.artifact_type == "scene":
            script = _current_script(document)
            if script is None:
                return None
            scene = next(
                (item for item in script.structured_scenes if item.id == ref.artifact_id),
                None,
            )
            if scene is None:
                return None
            line_by_id = {line.id: line for line in script.lines}
            lines = [
                line_by_id[line_id].model_dump(mode="json")
                for line_id in scene.line_ids
                if line_id in line_by_id
            ]
            return ArtifactSummary(
                ref=ref,
                title=scene.heading,
                summary=json.dumps(
                    {**scene.model_dump(mode="json"), "lines": lines},
                    ensure_ascii=False,
                ),
            )
        if ref.artifact_type == "shot":
            shot = next((item for item in document.shots if item.id == ref.artifact_id), None)
            if shot is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Shot {shot.sequence}",
                summary=shot.model_dump_json(),
            )
        if ref.artifact_type == "character":
            bible = _current_bible(document)
            character = None if bible is None else next(
                (item for item in bible.characters if item.id == ref.artifact_id),
                None,
            )
            if character is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=character.name,
                summary=character.model_dump_json(),
            )
        if ref.artifact_type in {"asset", "asset_version"}:
            asset = next(
                (
                    item
                    for item in document.assets
                    if item.id == ref.artifact_id
                    or any(version.id == ref.artifact_id for version in item.versions)
                ),
                None,
            )
            if asset is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=asset.name,
                summary=asset.model_dump_json(),
            )
        if ref.artifact_type in {"reference_pack", "reference_pack_entry"}:
            pack = next(
                (
                    item
                    for item in document.reference_packs
                    if item.id == ref.artifact_id
                    or any(entry.id == ref.artifact_id for entry in item.entries)
                ),
                None,
            )
            if pack is None:
                return None
            if ref.artifact_type == "reference_pack_entry":
                entry = next(
                    (item for item in pack.entries if item.id == ref.artifact_id),
                    None,
                )
                if entry is None:
                    return None
                return ArtifactSummary(
                    ref=ref,
                    title=f"Reference pack entry {entry.id}",
                    summary=entry.model_dump_json(),
                )
            return ArtifactSummary(
                ref=ref,
                title=pack.name,
                summary=pack.model_dump_json(),
            )
        if ref.artifact_type in {"timeline", "timeline_manifest"}:
            manifest = next(
                (item for item in document.manifests if item.id == ref.artifact_id),
                None,
            )
            if manifest is None:
                return None
            return ArtifactSummary(
                ref=ref,
                title=f"Timeline v{manifest.version}",
                summary=manifest.model_dump_json(),
            )
        if ref.artifact_type == "generation_task":
            return _artifact_record_summary(ref, self._projects.get_task(ref.artifact_id))
        if ref.artifact_type == "image_task":
            return _artifact_record_summary(
                ref,
                self._projects.get_image_task(ref.artifact_id),
            )
        if ref.artifact_type == "take":
            return _artifact_record_summary(ref, self._projects.get_take(ref.artifact_id))

        records = cast(ProjectArtifactRecordStore, self._projects)
        if ref.artifact_type == "h3_route_plan":
            return _artifact_record_summary(
                ref,
                records.get_h3_route_plan(ref.artifact_id),
            )
        if ref.artifact_type == "video_inspection":
            return _artifact_record_summary(
                ref,
                records.get_video_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "take_comparison":
            return _artifact_record_summary(
                ref,
                records.get_take_comparison(ref.artifact_id),
            )
        if ref.artifact_type == "audio_inspection":
            return _artifact_record_summary(
                ref,
                records.get_audio_inspection(ref.artifact_id),
            )
        if ref.artifact_type == "episode_review":
            return _artifact_record_summary(
                ref,
                records.get_episode_review(ref.artifact_id),
            )

        collection_loaders: dict[
            str,
            Callable[[str], Sequence[ArtifactRecordLike]],
        ] = {
            "export": records.list_exports,
            "h3_provider_run": records.list_provider_runs,
            "route_evidence": records.list_route_evidence,
            "reference_visual_rerank": records.list_reference_visual_reranks,
            "keyframe_comparison": records.list_keyframe_comparisons,
            "input_compatibility": records.list_input_compatibility_reports,
        }
        loader = collection_loaders.get(ref.artifact_type)
        if loader is not None:
            record = next(
                (item for item in loader(ref.project_id) if item.id == ref.artifact_id),
                None,
            )
            return _artifact_record_summary(ref, record)
        return None


def _artifact_record_summary(
    ref: ArtifactRef,
    record: ArtifactRecordLike | None,
) -> ArtifactSummary | None:
    if (
        record is None
        or record.id != ref.artifact_id
        or record.project_id != ref.project_id
    ):
        return None
    return ArtifactSummary(
        ref=ref,
        title=ref.artifact_type.replace("_", " ").title(),
        summary=record.model_dump_json(),
    )


def project_artifact_refs(document: ProjectDocument) -> list[ArtifactRef]:
    refs = [
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id=document.project.id,
        )
    ]
    script = _current_script(document)
    if script is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="script_version",
                artifact_id=script.id,
                version=script.version,
            )
        )
    bible = _current_bible(document)
    if bible is not None:
        refs.append(
            ArtifactRef(
                project_id=document.project.id,
                artifact_type="bible_version",
                artifact_id=bible.id,
                version=bible.version,
            )
        )
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="scene",
            artifact_id=scene.id,
            version=None if script is None else script.version,
        )
        for scene in ([] if script is None else script.structured_scenes)
    )
    refs.extend(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
        for shot in document.shots
    )
    return refs


def project_summary(document: ProjectDocument) -> str:
    script = _current_script(document)
    bible = _current_bible(document)
    scene_summaries = [] if script is None else [
        f"{scene.id}:{scene.heading}:{scene.summary}" for scene in script.structured_scenes
    ]
    characters = [] if bible is None else [
        f"{item.id}:{item.name}:{item.performance}" for item in bible.characters
    ]
    shot_summaries = [
        f"{shot.id}#{shot.sequence}:{shot.narrative_purpose}:{shot.visual}"
        for shot in document.shots
    ]
    planning_summary = "Planning metadata unavailable"
    if document.planning is not None:
        planning = document.planning
        warning_summary = " | ".join(planning.warnings) or "none"
        planning_summary = (
            f"Content planning job {planning.status.value}; "
            f"planner {planning.planner or 'pending'}; "
            f"job {planning.run_id or 'none'}; warnings {warning_summary}"
        )
    return "\n".join(
        [
            f"Project {document.project.name} ({document.project.id})",
            f"Profile {document.project.production_profile.value}; stage {document.project.stage.value}; "
            f"language {document.project.language}; aspect {document.project.aspect_ratio}; "
            f"target {document.project.target_duration_seconds}s",
            "Characters: " + " | ".join(characters),
            "Scenes: " + " | ".join(scene_summaries),
            "Shots: " + " | ".join(shot_summaries),
            f"Assets {len(document.assets)}; manifests {len(document.manifests)}; "
            f"current script {document.project.current_script_version_id}; "
            f"current bible {document.project.current_bible_version_id}",
            planning_summary,
        ]
    )


__all__ = ["ProjectArtifactResolver", "project_artifact_refs", "project_summary"]
