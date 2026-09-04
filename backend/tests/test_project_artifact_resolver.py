"""Focused coverage for the extracted project artifact resolver."""

from __future__ import annotations

import json
from pathlib import Path

from short_drama.agent_runtime import ArtifactRef
from short_drama.agent_runtime.artifact_resolver import (
    ProjectArtifactResolver as RuntimeProjectArtifactResolver,
    project_artifact_refs,
    project_summary,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import (
    CreateProjectRequest,
    PlanningRunRecord,
    PlanningRunStatus,
    PlanningRunStep,
    ProjectPlanningMetadata,
)
from short_drama.production_manager import ProjectArtifactResolver
from short_drama.repository import ShortDramaRepository


def _fixture_resolver(
    tmp_path: Path,
) -> tuple[RuntimeProjectArtifactResolver, ShortDramaRepository]:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    repository.save_project(
        build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    )
    return RuntimeProjectArtifactResolver(repository), repository


def test_production_manager_reexports_extracted_resolver(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]

    assert ProjectArtifactResolver is RuntimeProjectArtifactResolver

    summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="project",
            artifact_id=document.project.id,
        )
    )

    assert summary is not None
    assert summary.title == document.project.name
    assert summary.summary == project_summary(document)
    assert f"Project {document.project.name} ({document.project.id})" in summary.summary


def test_resolver_preserves_focused_document_summaries(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]
    script = next(
        item
        for item in document.script_versions
        if item.id == document.project.current_script_version_id
    )
    shot = document.shots[0]

    script_summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="script_version",
            artifact_id=script.id,
            version=script.version,
        )
    )
    shot_summary = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id=shot.id,
            version=shot.revision,
        )
    )

    assert script_summary is not None
    assert script_summary.title == f"Script v{script.version}"
    assert script.structured_scenes[0].heading in script_summary.summary
    assert shot_summary is not None
    assert shot_summary.title == f"Shot {shot.sequence}"
    assert json.loads(shot_summary.summary)["id"] == shot.id


def test_project_refs_and_missing_artifacts_remain_stable(tmp_path: Path) -> None:
    resolver, repository = _fixture_resolver(tmp_path)
    document = repository.list_projects()[0]

    refs = project_artifact_refs(document)
    ref_types = [ref.artifact_type for ref in refs]

    assert ref_types[0] == "project"
    assert "script_version" in ref_types
    assert "bible_version" in ref_types
    assert ref_types.count("scene") == len(document.script_versions[0].structured_scenes)
    assert ref_types.count("shot") == len(document.shots)
    assert resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="shot",
            artifact_id="missing-shot",
        )
    ) is None


def test_content_planning_job_resolver_accepts_legacy_artifact_ref(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    run = PlanningRunRecord(
        id="planning-run-resolver-compat",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.STORY_ARCHITECT,
        request=CreateProjectRequest(
            name=document.project.name,
            source_mode=document.project.source_mode,
            target_duration_seconds=document.project.target_duration_seconds,
            input_text=document.project.input_text,
            run_mode=document.project.run_mode,
        ),
        project_id=document.project.id,
        owner_agent_run_id="agent-run-resolver-compat",
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    document.planning = ProjectPlanningMetadata(
        run_id=run.id,
        status=run.status,
        owner_agent_run_id=run.owner_agent_run_id,
        planning_job_kind=run.job_kind,
    )
    assert repository.insert_project_with_planning_run(document, run)
    resolver = RuntimeProjectArtifactResolver(repository)

    canonical = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="content_planning_job",
            artifact_id=run.id,
        )
    )
    legacy = resolver.summarize_artifact(
        ArtifactRef(
            project_id=document.project.id,
            artifact_type="planning_run",
            artifact_id=run.id,
        )
    )

    assert canonical is not None
    assert legacy is not None
    assert canonical.title == legacy.title == "Content planning job STORY_ARCHITECT"
    assert "canonical script, Bible, shots, and assets remain in ProjectDocument" in (
        canonical.summary
    )


def test_scene_revision_artifact_title_exposes_persisted_queue_progress(
    tmp_path: Path,
) -> None:
    repository = ShortDramaRepository(tmp_path / "short-drama.sqlite3")
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    run = PlanningRunRecord(
        id="planning-run-scene-revision-progress",
        status=PlanningRunStatus.RUNNING,
        current_step=PlanningRunStep.SCENE_REVISION,
        request=CreateProjectRequest(
            name=document.project.name,
            source_mode=document.project.source_mode,
            target_duration_seconds=document.project.target_duration_seconds,
            input_text=document.project.input_text,
            run_mode=document.project.run_mode,
        ),
        revision_scene_sequences=[1, 4],
        next_revision_scene_index=0,
        project_id=document.project.id,
        owner_agent_run_id="agent-run-scene-revision-progress",
        created_at="2026-09-02T00:00:00+00:00",
        updated_at="2026-09-02T00:00:00+00:00",
    )
    document.planning = ProjectPlanningMetadata(
        run_id=run.id,
        status=run.status,
        owner_agent_run_id=run.owner_agent_run_id,
        planning_job_kind=run.job_kind,
    )
    assert repository.insert_project_with_planning_run(document, run)
    resolver = RuntimeProjectArtifactResolver(repository)
    ref = ArtifactRef(
        project_id=document.project.id,
        artifact_type="content_planning_job",
        artifact_id=run.id,
    )

    first = resolver.summarize_artifact(ref)

    assert first is not None
    assert first.title == "Content planning job SCENE_REVISION 1/2"

    run.next_revision_scene_index = 1
    run.updated_at = "2026-09-02T00:00:01+00:00"
    repository.save_planning_run_with_project(run, document)
    second = resolver.summarize_artifact(ref)

    assert second is not None
    assert second.title == "Content planning job SCENE_REVISION 2/2"
