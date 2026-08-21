"""Recoverable long-form script planning integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from _routes.short_drama import router as short_drama_router
from short_drama.deps import get_short_drama_service
from short_drama.models import (
    AutonomousExecutionTarget,
    AutonomousRunStep,
    PlanningRunCreateRequest,
    PlanningRunStatus,
    PlanningRunStep,
    ReviseScriptLineRequest,
    RunMode,
    ScriptReviewIssue,
    ScriptReviewReport,
    ScriptReviewSeverity,
    SourceMode,
)
from short_drama.production_planner import (
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
)
from short_drama.providers.language_config import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService


class StagedLanguageClient:
    def __init__(
        self,
        request: PlanningRunCreateRequest,
        *,
        revision_rounds_required: int = 0,
        fail_scene_once: int | None = None,
        review_issue_counts: list[int] | None = None,
    ) -> None:
        self.outline = _fallback_episode_outline(request)
        self.revision_rounds_required = revision_rounds_required
        self.fail_scene_once = fail_scene_once
        self.review_issue_counts = review_issue_counts
        self.critic_calls = 0
        self.revision_calls = 0
        self.scene_calls: list[int] = []
        self.scene_inputs: list[dict[str, object]] = []
        self._failed_scene = False
        self.scene_plans = {}
        handoff = self.outline.scenes[0].continuity_in.model_copy(deep=True)
        for scene in self.outline.scenes:
            plan = _fallback_directed_scene_plan(request, self.outline, scene, handoff)
            plan.shots[0].narrative_purpose = (
                f"staged-scene-{scene.sequence}: " + plan.shots[0].narrative_purpose
            )
            plan.continuity_out.known_facts.append(
                f"staged-scene-{scene.sequence}-complete"
            )
            self.scene_plans[scene.sequence] = plan
            handoff = plan.continuity_out.model_copy(deep=True)

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
        del image_urls, max_output_tokens
        if "independent script-critic Agent" in instructions:
            self.critic_calls += 1
            issue_count = (
                self.review_issue_counts[
                    min(self.critic_calls - 1, len(self.review_issue_counts) - 1)
                ]
                if self.review_issue_counts
                else int(self.critic_calls <= self.revision_rounds_required)
            )
            requires_revision = (
                issue_count > 0
                if self.review_issue_counts is not None
                else self.critic_calls <= self.revision_rounds_required
            )
            return ScriptReviewReport(
                summary=(
                    "因果链需要一次修订。"
                    if requires_revision
                    else "剧本可继续进入逐场规划。"
                ),
                strengths=["悬念线持续推进"],
                issues=[
                    ScriptReviewIssue(
                        category=f"CAUSAL_LOGIC_{index + 1}",
                        severity=(
                            ScriptReviewSeverity.HIGH
                            if index == 0
                            else ScriptReviewSeverity.MEDIUM
                        ),
                        scene_sequences=[1, len(self.outline.scenes)],
                        observation=f"第 {index + 1} 项因果尚未闭合。",
                        recommendation="明确规则并回收开场事件。",
                    )
                    for index in range(issue_count)
                ],
                requires_revision=requires_revision,
            ).model_dump_json()
        if "revising an episode" in instructions:
            self.revision_calls += 1
            payload = self.outline.model_dump(mode="python")
            payload["story_summary"] += " 已补全开场与结尾的因果闭环。"
            return json.dumps(payload, ensure_ascii=False)
        if "story-architect Agent" in instructions:
            return self.outline.model_dump_json()
        payload = json.loads(input_text)
        self.scene_inputs.append(payload)
        sequence = int(payload["scene_contract"]["sequence"])
        self.scene_calls.append(sequence)
        if (
            sequence == self.fail_scene_once
            and not self._failed_scene
        ):
            self._failed_scene = True
            raise RuntimeError(f"transient scene {sequence} failure")
        return self.scene_plans[sequence].model_dump_json()


def _request(target_seconds: int = 300) -> PlanningRunCreateRequest:
    return PlanningRunCreateRequest(
        name=f"可恢复 {target_seconds} 秒短剧",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=target_seconds,
        run_mode=RunMode.GUIDED,
    )


def _service(
    root: Path,
    language: StagedLanguageClient,
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


def _finish(service: ShortDramaService, run_id: str, *, max_steps: int = 30):
    response = service.get_planning_run(run_id)
    for _ in range(max_steps):
        if response.run.status == PlanningRunStatus.COMPLETE:
            return response
        response = service.advance_planning_run(run_id)
    raise AssertionError("planning run did not finish within the test bound")


def test_long_form_planning_advances_one_agent_stage_or_scene_at_a_time(
    tmp_path: Path,
) -> None:
    request = _request(430)
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)

    started = service.create_planning_run(request)
    assert started.run.current_step == PlanningRunStep.STORY_ARCHITECT
    assert started.project is None

    after_architect = service.advance_planning_run(started.run.id)
    assert after_architect.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert after_architect.run.total_scenes == len(language.outline.scenes)
    assert after_architect.run.directed_scene_json == []

    after_critic = service.advance_planning_run(started.run.id)
    assert after_critic.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert after_critic.run.review_report is not None

    first_scene = service.advance_planning_run(started.run.id)
    assert len(first_scene.run.directed_scene_json) == 1
    assert first_scene.run.next_scene_sequence == 2
    assert language.scene_calls == [1]

    completed = _finish(service, started.run.id)
    assert completed.project is not None
    assert completed.project.document.project.target_duration_seconds == 430
    assert completed.project.document.autonomous_runs == []
    assert len(completed.project.document.script_versions[0].structured_scenes) == len(
        language.outline.scenes
    )
    assert language.scene_calls == list(range(1, len(language.outline.scenes) + 1))
    assert service.repository.list_image_tasks(completed.project.document.project.id) == []
    assert service.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []
    assert [
        snapshot.revision_round
        for snapshot in service.repository.list_planning_outline_snapshots(
            started.run.id
        )
    ] == [0]
    assert [
        snapshot.review_round
        for snapshot in service.repository.list_planning_review_snapshots(
            started.run.id
        )
    ] == [1]
    assert completed.project.document.script_versions[0].planning_run_id == started.run.id
    first_line = completed.project.document.script_versions[0].lines[0]
    revised_project = service.revise_script_line(
        completed.project.document.project.id,
        ReviseScriptLineRequest(
            line_id=first_line.id,
            spoken_text=first_line.spoken_text,
            subtitle_text=first_line.subtitle_text,
            change_reason="确认手动版本仍可追溯规划审稿轨迹",
        ),
    )
    revised_script = revised_project.document.script_versions[-1]
    assert revised_script.planning_run_id == started.run.id
    assert revised_script.review_report == completed.project.document.script_versions[0].review_report


def test_planning_run_persists_revision_and_scene_progress_across_restart(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, revision_rounds_required=1)
    first = _service(tmp_path, language)
    run = first.create_planning_run(request).run

    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.STORY_REVISION
    revised = first.advance_planning_run(run.id)
    assert revised.run.current_step == PlanningRunStep.SCRIPT_CRITIC
    assert revised.run.revision_round == 1
    assert first.advance_planning_run(run.id).run.current_step == PlanningRunStep.SCENE_DIRECTOR
    first_scene = first.advance_planning_run(run.id)
    assert len(first_scene.run.directed_scene_json) == 1

    restarted = _service(tmp_path, language)
    restored = restarted.get_planning_run(run.id)
    assert restored.run.revision_round == 1
    assert len(restored.run.directed_scene_json) == 1

    completed = _finish(restarted, run.id)
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert completed.run.project_id is not None
    assert language.revision_calls == 1
    assert language.critic_calls == 2
    assert language.scene_inputs[1]["previous_actual_handoff"]["known_facts"][-1] == (
        "staged-scene-1-complete"
    )
    history = restarted.get_planning_run_script_quality(run.id)
    assert [item.revision_round for item in history.outline_snapshots] == [0, 1]
    assert [item.review_round for item in history.review_snapshots] == [1, 2]
    assert [item.total_issues for item in history.comparison.rounds] == [1, 0]
    assert history.comparison.trend.value == "FEWER_REPORTED_ISSUES"
    assert history.comparison.total_issue_delta == -1
    assert history.comparison.high_issue_delta == -1


def test_transient_scene_failure_keeps_the_same_local_retry_point(
    tmp_path: Path,
) -> None:
    request = _request()
    language = StagedLanguageClient(request, fail_scene_once=2)
    service = _service(tmp_path, language)
    run = service.create_planning_run(request).run
    service.advance_planning_run(run.id)
    service.advance_planning_run(run.id)
    service.advance_planning_run(run.id)

    failed = service.advance_planning_run(run.id)
    assert failed.run.status == PlanningRunStatus.NEEDS_ATTENTION
    assert failed.run.current_step == PlanningRunStep.SCENE_DIRECTOR
    assert failed.run.next_scene_sequence == 2
    assert len(failed.run.directed_scene_json) == 1

    retried = service.advance_planning_run(run.id)
    assert retried.run.status == PlanningRunStatus.RUNNING
    assert retried.run.next_scene_sequence == 3
    assert len(retried.run.directed_scene_json) == 2
    assert language.scene_calls.count(2) == 2


def test_latest_review_regression_is_advisory_and_does_not_block(
    tmp_path: Path,
) -> None:
    request = _request(260)
    language = StagedLanguageClient(
        request,
        revision_rounds_required=2,
        review_issue_counts=[4, 2, 3],
    )
    service = _service(tmp_path, language)
    completed = _finish(
        service,
        service.create_planning_run(request).run.id,
    )

    history = service.get_planning_run_script_quality(completed.run.id)
    assert [item.total_issues for item in history.comparison.rounds] == [4, 2, 3]
    assert history.comparison.total_issue_delta == -1
    assert history.comparison.latest_total_issue_delta == 1
    assert history.comparison.latest_high_issue_delta == 0
    assert history.comparison.latest_report_regression_risk is True
    assert completed.run.status == PlanningRunStatus.COMPLETE
    assert any("建议比较最近两版" in item for item in completed.run.warnings)


def test_planning_run_api_can_finish_with_local_script_only_fallback(
    tmp_path: Path,
) -> None:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
    )
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service

    with TestClient(app) as client:
        created = client.post(
            "/api/short-drama/planning-runs",
            json=_request(300).model_dump(mode="json"),
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run"]["id"]
        for _ in range(30):
            advanced = client.post(
                f"/api/short-drama/planning-runs/{run_id}/advance"
            )
            assert advanced.status_code == 200, advanced.text
            if advanced.json()["run"]["status"] == "COMPLETE":
                break
        else:
            raise AssertionError("local planning API did not complete")

        listed = client.get("/api/short-drama/planning-runs")
        restored = client.get(f"/api/short-drama/planning-runs/{run_id}")
        quality = client.get(
            f"/api/short-drama/planning-runs/{run_id}/script-quality"
        )

    assert listed.status_code == 200
    assert listed.json()["runs"][0]["id"] == run_id
    assert restored.status_code == 200
    assert restored.json()["project"]["document"]["project"][
        "target_duration_seconds"
    ] == 300
    assert any(
        "未配置" in warning
        for warning in restored.json()["run"]["warnings"]
    )
    assert quality.status_code == 200
    assert quality.json()["comparison"]["rounds"] == []
    assert quality.json()["comparison"]["trend"] == "NOT_COMPARABLE"


def test_auto_mode_planning_stops_before_image_or_h3_generation(
    tmp_path: Path,
) -> None:
    request = PlanningRunCreateRequest(
        name="先完成剧本再进入制片",
        source_mode=SourceMode.AUTO_ORIGINAL,
        target_duration_seconds=300,
        run_mode=RunMode.AUTO_DRAFT,
        execution_target=AutonomousExecutionTarget.REAL_H3,
    )
    language = StagedLanguageClient(request)
    service = _service(tmp_path, language)

    completed = _finish(
        service,
        service.create_planning_run(request).run.id,
    )

    assert completed.project is not None
    autonomous = completed.project.document.autonomous_runs[-1]
    assert autonomous.current_step == AutonomousRunStep.PREPARE_ASSETS
    assert completed.run.autonomous_run_id == autonomous.id
    assert service.repository.list_image_tasks(
        completed.project.document.project.id
    ) == []
    assert service.repository.list_tasks(completed.project.document.project.id) == []
    assert service.repository.list_provider_runs(
        completed.project.document.project.id
    ) == []


def test_script_quality_api_compares_multiple_custom_duration_runs(
    tmp_path: Path,
) -> None:
    requests = [
        PlanningRunCreateRequest(
            name="180 秒都市误会",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="婚礼当天，女主发现新郎只是替别人出席。",
            target_duration_seconds=180,
            run_mode=RunMode.GUIDED,
        ),
        PlanningRunCreateRequest(
            name="430 秒职场悬疑",
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=430,
            run_mode=RunMode.GUIDED,
        ),
        PlanningRunCreateRequest(
            name="900 秒家庭反转",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="父亲的遗嘱要求三个子女在旧屋共住一夜才能继承遗产。",
            target_duration_seconds=900,
            run_mode=RunMode.GUIDED,
        ),
    ]
    run_ids: list[str] = []
    for request in requests:
        language = StagedLanguageClient(request, revision_rounds_required=1)
        service = _service(tmp_path, language)
        run = service.create_planning_run(request).run
        completed = _finish(service, run.id)
        assert completed.project is not None
        run_ids.append(run.id)

    service = _service(tmp_path, StagedLanguageClient(requests[-1]))
    app = FastAPI()
    app.include_router(short_drama_router)
    app.dependency_overrides[get_short_drama_service] = lambda: service
    with TestClient(app) as client:
        catalog = client.get("/api/short-drama/script-quality")
        detail = client.get(
            f"/api/short-drama/planning-runs/{run_ids[1]}/script-quality"
        )

    assert catalog.status_code == 200, catalog.text
    evaluations = catalog.json()["evaluations"]
    assert {item["target_duration_seconds"] for item in evaluations} == {
        180.0,
        430.0,
        900.0,
    }
    assert all(item["trend"] == "FEWER_REPORTED_ISSUES" for item in evaluations)
    assert detail.status_code == 200, detail.text
    assert detail.json()["comparison"]["target_duration_seconds"] == 430.0
    assert [
        item["revision_round"] for item in detail.json()["outline_snapshots"]
    ] == [0, 1]
    assert [
        item["review_round"] for item in detail.json()["review_snapshots"]
    ] == [1, 2]
