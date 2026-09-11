"""Episode review hands failures back without concealing repeated model calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import EnqueueRequest, EpisodeReviewRequest, EpisodeReviewStatus
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderIncompleteError,
)
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_models import StudioTimelineSave


class RecoveringReviewProvider:
    def __init__(self, *, fail_first: bool = True) -> None:
        self.calls: list[int] = []
        self.fail_first = fail_first
        self.payloads: list[dict[str, JsonValue]] = []

    def validate_credentials(self) -> None:
        return None

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        self.calls.append(max_output_tokens)
        if len(self.calls) == 1 and self.fail_first:
            raise LanguageProviderIncompleteError(
                reason="max_output_tokens", partial_output_characters=0,
            )
        payload = json.loads(input_text)
        self.payloads.append(payload)
        result: dict[str, object] = {"findings": [], "summary": "Reviewed available evidence."}
        if "scene" in payload:
            result["observations"] = []
        return json.dumps(result)


def test_review_returns_first_failure_and_resumes_saved_cursor(tmp_path: Path) -> None:
    provider = RecoveringReviewProvider()
    service = ShortDramaService(
        database_path=tmp_path / "state.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: provider,
    )
    project_id = service.initialize_fixture().document.project.id
    service.enqueue(project_id, EnqueueRequest(candidate_count=1))
    service.drain(project_id, 300)
    service.rebuild_manifest(project_id)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"),
    )

    interrupted = service.review_current_episode(project_id, EpisodeReviewRequest(max_actions=32))

    assert len(provider.calls) == 1
    assert interrupted.report.status == EpisodeReviewStatus.RUNNING
    assert interrupted.report.next_scene_index == 0
    assert interrupted.report.active_stage_attempts == 1
    assert "max_output_tokens" in interrupted.report.warnings[-1]
    assert interrupted.project.progress.manifest_ready

    completed = service.review_current_episode(project_id, EpisodeReviewRequest(max_actions=32))

    assert completed.report.id == interrupted.report.id
    assert completed.report.status == EpisodeReviewStatus.COMPLETE
    assert completed.report.active_stage_attempts == 0
    assert completed.report.advisory_only
    assert len(provider.calls) == 1 + len(completed.report.scene_reviews) + 1
    assert min(provider.calls) >= 16_000
    assert completed.project.progress.manifest_ready


def test_review_uses_saved_editorial_without_rebuilding_legacy_content(tmp_path: Path) -> None:
    provider = RecoveringReviewProvider(fail_first=False)
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _key, _url: provider)
    pid = service.initialize_fixture().document.project.id
    service.enqueue(pid, EnqueueRequest(candidate_count=1))
    service.drain(pid, 300)
    saved = read_editorial(service, pid)
    project = EditorProject.model_validate(saved.project)
    timeline = project.timelines[0]
    clip = next(item for item in timeline.clips if item.type == "video")
    clip.trimStart = .25
    clip.duration = 1
    clip.speed = 1.2
    clip.reversed = True
    tail_start = max(item.startTime + item.duration for item in timeline.clips)
    black = clip.model_copy(deep=True)
    black.id = "silent-black-tail"
    black.startTime = tail_start
    black.duration = .5
    black.opacity = 0
    black.muted = True
    assert black.model_extra is not None
    black.model_extra.pop("sourceTakeId", None)
    timeline.clips.append(black)
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=saved.revision,
        timeline={"project": cast(dict[str, JsonValue], project.model_dump(mode="json"))}))
    before = service.get_project(pid).document
    assert before.project.current_manifest_id is None
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    result = service.review_current_episode(pid, EpisodeReviewRequest(max_actions=32))
    assert result.report.status == EpisodeReviewStatus.COMPLETE
    assert result.report.source_editorial_revision == 1
    assert result.report.review_manifest is not None
    assert result.report.review_manifest.duration_seconds == tail_start + .5
    assert black.id not in {item.id for item in result.report.review_manifest.video_clips}
    reviewed = next(item for item in result.report.review_manifest.video_clips if item.id == clip.id)
    assert reviewed.source_in_seconds == .25 and reviewed.duration_seconds == 1
    scene = next(payload for payload in provider.payloads if "saved_editorial_scene" in payload)
    context = cast(dict[str, JsonValue], scene["saved_editorial_scene"])
    clips = cast(list[dict[str, JsonValue]], context["clips"])
    current = next(item for item in clips if item["id"] == clip.id)
    assert current["speed"] == 1.2 and current["reversed"] is True
    padding = next(item for item in clips if item["id"] == black.id)
    assert padding["opacity"] == 0 and padding["muted"] is True
    assert "不是当前剪辑合成画面" in str(scene["visual_evidence_scope"])
    after = service.get_project(pid).document
    assert after.editorial_timeline == before.editorial_timeline
    assert after.manifests == before.manifests
    assert after.shots == before.shots
    calls = len(provider.calls)
    reused = service.review_current_episode(pid, EpisodeReviewRequest())
    assert reused.report.id == result.report.id and len(provider.calls) == calls
