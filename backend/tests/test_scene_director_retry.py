"""Persisted Scene Director retry behavior."""

from __future__ import annotations

import json
from pathlib import Path

from short_drama.models import (
    CreateProjectRequest,
    PlanningRunStatus,
    PlanningRunStep,
    ReferenceRole,
    SourceMode,
)
from short_drama.production_planner import (
    PlannedReferenceNeed,
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
)
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService


class RetryOnceSceneDirectorClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        self.request = request
        self.outline = _fallback_episode_outline(request)
        self.inputs: list[dict[str, object]] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del instructions, image_urls, max_output_tokens
        payload = json.loads(input_text)
        self.inputs.append(payload)
        scene = self.outline.scenes[0]
        plan = _fallback_directed_scene_plan(
            self.request,
            self.outline,
            scene,
            scene.continuity_in,
        )
        if len(self.inputs) == 1:
            plan.shots[0].reference_needs.append(
                PlannedReferenceNeed(
                    subject_name="未声明的临时道具",
                    acceptable_roles=[ReferenceRole.PROP_PRODUCT],
                    purpose="首次输出制造一个结构错误",
                )
            )
        return plan.model_dump_json()


def _service(
    root: Path,
    language: RetryOnceSceneDirectorClient,
) -> ShortDramaService:
    service = ShortDramaService(
        database_path=root / "app-data" / "short-drama.sqlite3",
        workspace_root=root / "workspace",
        language_provider_factory=lambda _api_key, _base_url: language,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    return service


def test_scene_director_structural_failure_retries_after_persisted_advance(
    tmp_path: Path,
) -> None:
    request = CreateProjectRequest(
        name="Scene Director 持久化重试",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=60,
    )
    language = RetryOnceSceneDirectorClient(request)
    service = _service(tmp_path, language)
    project = service.create_project(
        request,
        project_id="project-scene-director-retry",
        owner_agent_run_id="agent-run-scene-director-retry",
    )
    assert project.document.planning is not None
    assert project.document.planning.run_id is not None
    run_id = project.document.planning.run_id

    run = service.get_planning_run(run_id).run
    run.current_step = PlanningRunStep.SCENE_DIRECTOR
    run.status = PlanningRunStatus.RUNNING
    run.outline_json = language.outline.model_dump_json()
    run.outline_source = "test scene director retry"
    run.total_scenes = len(language.outline.scenes)
    run.directed_scene_json = []
    run.next_scene_sequence = 1
    service._save_planning_run_state(run)

    first = service.advance_planning_run(run_id)

    assert first.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert first.run.next_scene_sequence == 1
    assert first.run.directed_scene_json == []
    assert first.run.step_retry_feedback is not None
    assert first.run.step_retry_count == 1
    assert len(language.inputs) == 1
    assert language.inputs[0]["previous_retry_feedback"] is None

    restarted = _service(tmp_path, language)
    persisted = restarted.get_planning_run(run_id)
    assert persisted.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert persisted.run.next_scene_sequence == 1
    assert persisted.run.directed_scene_json == []
    assert persisted.run.step_retry_feedback == first.run.step_retry_feedback

    second = restarted.advance_planning_run(run_id)

    assert second.run.current_step == PlanningRunStep.FINALIZE
    assert second.run.next_scene_sequence == 2
    assert len(second.run.directed_scene_json) == 1
    assert second.run.step_retry_feedback is None
    assert second.run.step_retry_count == 0
    assert len(language.inputs) == 2
    assert (
        language.inputs[1]["previous_retry_feedback"]
        == first.run.step_retry_feedback
    )
