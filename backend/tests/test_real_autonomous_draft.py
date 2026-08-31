"""Offline end-to-end coverage for the resumable REAL_H3 autonomous runner."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image

from short_drama.h3_models import (
    H3DeleteResponse,
    H3ProviderConfigUpdate,
    H3RemoteStatus,
    H3RemoteTask,
    H3TaskContent,
    H3TaskUsage,
    H3VideoRequest,
)
from short_drama.image_models import (
    ImageProviderConfigUpdate,
    ImageTokenDetails,
    ImageUsage,
)
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    AutonomousDraftResponse,
    AutonomousDraftCreateRequest,
    AutonomousExecutionTarget,
    AutonomousRepeatCreateRequest,
    AutonomousRunAdvanceRequest,
    AutonomousRunStatus,
    AutonomousRunStep,
    EpisodeReviewStatus,
    Freshness,
    PlanningRunCreateRequest,
    PlanningRunStatus,
    ProductionProfile,
    ReferenceRequirement,
    ReferenceRole,
    RunMode,
    SourceMode,
)
from short_drama.production_planner import (
    _fallback_directed_scene_plan,
    _fallback_episode_outline,
)
from short_drama.providers.image_provider import ImageProviderInput, ImageProviderResult
from short_drama.providers.openai_compatible_language_client import LanguageProviderError
from short_drama.script_documents import EpisodeScenePatch, scene_dialogue_lines
from short_drama.service import (
    ShortDramaService,
    _filter_generation_reference_versions,
    _order_keyframe_reference_versions,
)


class FakeImageProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.operations: list[str] = []

    def validate_credentials(self) -> None:
        pass

    def create(self, request, inputs: list[ImageProviderInput]) -> ImageProviderResult:
        del inputs
        self.calls += 1
        self.operations.append(request.operation.value)
        buffer = BytesIO()
        color = (30 + self.calls * 17) % 255
        Image.new("RGB", (768, 1344), (color, 80, 120)).save(buffer, format="PNG")
        return ImageProviderResult(
            image_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            mime_type="image/png",
            provider_request_id=f"image-request-{self.calls}",
            usage=ImageUsage(
                input_tokens=100,
                input_tokens_details=ImageTokenDetails(image_tokens=50, text_tokens=50),
                output_tokens=800,
                output_tokens_details=ImageTokenDetails(image_tokens=800, text_tokens=0),
                total_tokens=900,
            ),
        )


def test_keyframe_reference_order_uses_reference_plan_subject_priority() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    lin = next(
        item
        for item in document.assets
        if item.kind == AssetKind.CHARACTER and "林夏" in item.name
    )
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    plan = next(
        item for item in document.reference_plan if item.shot_id.endswith("shot-04")
    ).model_copy(
        deep=True,
        update={
            "requirements": [
                ReferenceRequirement(
                    id="face-lock",
                    subject_asset_id=lin.id,
                    acceptable_roles=[ReferenceRole.CHARACTER_FACE],
                    purpose="Preserve the approved lead identity for a close-up.",
                )
            ]
        },
    )

    ordered = _order_keyframe_reference_versions(
        [
            (location, location.versions[-1]),
            (prop, prop.versions[-1]),
            (lin, lin.versions[-1]),
        ],
        plan,
        [],
    )

    assert ordered[0][0].id == lin.id


def test_generation_references_exclude_continuity_only_assets() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    lin = next(
        item
        for item in document.assets
        if item.kind == AssetKind.CHARACTER and "林夏" in item.name
    )
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    plan = next(
        item for item in document.reference_plan if item.shot_id.endswith("shot-04")
    ).model_copy(
        deep=True,
        update={
            "requirements": [
                ReferenceRequirement(
                    id="face-lock",
                    subject_asset_id=lin.id,
                    acceptable_roles=[ReferenceRole.CHARACTER_FACE],
                    purpose="The character is visible in this shot.",
                ),
                ReferenceRequirement(
                    id="location-lock",
                    subject_asset_id=location.id,
                    acceptable_roles=[ReferenceRole.LOCATION_MASTER],
                    purpose="The location is visible in this shot.",
                ),
            ]
        },
    )

    filtered = _filter_generation_reference_versions(
        [
            (lin, lin.versions[-1]),
            (location, location.versions[-1]),
            (prop, prop.versions[-1]),
        ],
        plan,
    )

    assert [asset.id for asset, _version in filtered] == [lin.id, location.id]


class FakeLanguageClient:
    def __init__(
        self,
        *,
        reject_first_asset: bool = False,
        repair_first_shot: bool = False,
        repair_shot_sequences: set[int] | None = None,
        persistent_repair_shot_sequences: set[int] | None = None,
        episode_repair_shot_sequences: set[int] | None = None,
        persistent_episode_repair_shot_sequences: set[int] | None = None,
        fail_video_reviews: int = 0,
        fail_episode_reviews: int = 0,
    ) -> None:
        self.reject_first_asset = reject_first_asset
        self.repair_shot_sequences = set(repair_shot_sequences or set())
        if repair_first_shot:
            self.repair_shot_sequences.add(1)
        self.persistent_repair_shot_sequences = set(
            persistent_repair_shot_sequences or set()
        )
        self.repair_shot_sequences.update(self.persistent_repair_shot_sequences)
        self.repaired_shot_sequences: set[int] = set()
        self.episode_repair_shot_sequences = set(
            episode_repair_shot_sequences or set()
        )
        self.persistent_episode_repair_shot_sequences = set(
            persistent_episode_repair_shot_sequences or set()
        )
        self.episode_repair_shot_sequences.update(
            self.persistent_episode_repair_shot_sequences
        )
        self.fail_video_reviews = fail_video_reviews
        self.fail_episode_reviews = fail_episode_reviews
        self.asset_review_calls = 0
        self.director_calls = 0
        self.asset_review_instructions: list[str] = []
        self.route_instructions: list[str] = []
        self.video_observation_instructions: list[str] = []
        self.episode_scene_instructions: list[str] = []
        self.episode_summary_instructions: list[str] = []
        self.director_instructions: list[str] = []
        self.episode_scene_payloads: list[dict[str, Any]] = []
        self.episode_summary_payloads: list[dict[str, Any]] = []

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
        if "visual asset inspection tool" in instructions:
            self.asset_review_instructions.append(instructions)
            self.asset_review_calls += 1
            if self.reject_first_asset and self.asset_review_calls == 1:
                return json.dumps(
                    {
                        "recommended": False,
                        "score": 2.0,
                        "issues": [
                            {
                                "code": "REFERENCE_LAYOUT_CORRUPTED",
                                "severity": "HIGH",
                                "message": "The first candidate is not a coherent reference board.",
                                "repair_layer": "IMAGE_RETRY",
                                "category": "COMPOSITION_QUALITY",
                            }
                        ],
                        "summary": "Regenerate the same asset without changing its identity.",
                    }
                )
            return json.dumps(
                {
                    "recommended": True,
                    "score": 4.6,
                    "issues": [],
                    "summary": "The generated image is usable as a coherent production reference.",
                }
            )
        if "production route agent" in instructions:
            self.route_instructions.append(instructions)
            payload = json.loads(input_text)
            return json.dumps(
                {
                    "choices": [
                        {
                            "shot_id": shot["shot"]["id"],
                            "candidate_id": max(
                                shot["candidates"],
                                key=lambda item: item["quality_score_hint"],
                            )["id"],
                            "rationale": "Use the highest-evidence executable route.",
                            "qa_focus_categories": [],
                            "warnings": [],
                        }
                        for shot in payload["shots"]
                    ]
                }
            )
        if "video observation tool" in instructions:
            self.video_observation_instructions.append(instructions)
            if self.fail_video_reviews > 0:
                self.fail_video_reviews -= 1
                raise LanguageProviderError("simulated transient model overload")
            payload = json.loads(input_text)
            sequence = int(payload["current_shot"]["sequence"])
            if (
                sequence in self.repair_shot_sequences
                and (
                    sequence in self.persistent_repair_shot_sequences
                    or sequence not in self.repaired_shot_sequences
                )
            ):
                return json.dumps(
                    {
                        "observations": [],
                        "issues": [
                            {
                                "code": "VISIBLE_COMPOSITION_FAILURE",
                                "severity": "MAJOR",
                                "message": "The sampled composition visibly contradicts the declared shot intent.",
                                "repair_layer": "DIRECTOR",
                                "category": "COMPOSITION_QUALITY",
                            }
                        ],
                        "summary": "The first shot needs a generic Director repair before reuse.",
                    }
                )
            return json.dumps(
                {
                    "observations": [],
                    "issues": [],
                    "summary": "No severe visible contradiction was found in sampled frames.",
                }
            )
        if "scene-level episode-review Agent" in instructions:
            self.episode_scene_instructions.append(instructions)
            if self.fail_episode_reviews > 0:
                self.fail_episode_reviews -= 1
                raise LanguageProviderError("simulated transient episode reviewer overload")
            payload = json.loads(input_text)
            self.episode_scene_payloads.append(payload)
            target: dict[str, Any] | None = None
            for evidence in payload["shot_evidence"]:
                sequence = int(evidence["shot_plan"]["sequence"])
                if (
                    sequence in self.episode_repair_shot_sequences
                    and (
                        sequence in self.persistent_episode_repair_shot_sequences
                        or sequence not in self.repaired_shot_sequences
                    )
                ):
                    target = evidence["shot_plan"]
                    break
            findings = []
            if target is not None:
                findings.append(
                    {
                        "dimension": "STORY_CLARITY",
                        "code": "EPISODE_BEAT_VISIBLY_UNREADABLE",
                        "severity": "HIGH",
                        "message": "The edited scene does not visibly communicate the linked narrative beat.",
                        "category": "COMPOSITION_QUALITY",
                        "evidence": "The selected contact sheet and persisted observations leave the declared beat unreadable in the edit.",
                        "scene_ids": [payload["scene"]["id"]],
                        "shot_ids": [target["id"]],
                        "recommended_action": "Regenerate only the linked shot with a clearer readable composition while preserving the story beat.",
                        "repair_recommended": True,
                    }
                )
            return json.dumps(
                {
                    "observations": ["The scene was reviewed from selected Takes in timeline order."],
                    "findings": findings,
                    "summary": (
                        "One linked shot needs a local episode-level repair."
                        if findings
                        else "The edited scene has no supported episode-level finding."
                    ),
                }
            )
        if "episode-level reviewer Agent" in instructions:
            self.episode_summary_instructions.append(instructions)
            if self.fail_episode_reviews > 0:
                self.fail_episode_reviews -= 1
                raise LanguageProviderError("simulated transient episode reviewer overload")
            payload = json.loads(input_text)
            self.episode_summary_payloads.append(payload)
            return json.dumps(
                {
                    "findings": [],
                    "summary": "The scene handoffs form a readable episode without another cross-scene finding.",
                }
            )
        if "film director agent advising a user" in instructions:
            self.director_instructions.append(instructions)
            payload = json.loads(input_text)
            self.director_calls += 1
            self.repaired_shot_sequences.add(int(payload["shot"]["sequence"]))
            revised_visual = (
                payload["shot"]["visual"]
                + " Keep the same narrative beat with a simpler, clearly readable composition."
            )
            return json.dumps(
                {
                    "advisories": [
                        {
                            "category": "CAMERA",
                            "message": "Simplify the coverage before regenerating.",
                            "rationale": "The visible failure concerns composition rather than a scene-specific object rule.",
                            "suggested_action": "Regenerate the same beat with a clearer composition.",
                            "dismissible": True,
                        }
                    ],
                    "repair_options": [
                        {
                            "strategy": "PROMPT_RETRY",
                            "summary": "Clarify the shot composition and retry.",
                            "rationale": "A generic composition change directly addresses the observed evidence.",
                            "tradeoff": "One additional H3 generation round.",
                            "recommended": True,
                            "dismissible": True,
                        }
                    ],
                    "recommended_strategy": "PROMPT_RETRY",
                    "proposed_repair": {
                        "decision": "APPLIED",
                        "strategy": "PROMPT_RETRY",
                        "reason": "Simplify the visible composition while preserving the narrative beat.",
                        "source_take_id": None,
                        "issue_codes": ["VISIBLE_COMPOSITION_FAILURE"],
                        "shot_patch": {"visual": revised_visual},
                        "reference_plan": None,
                        "split_shots": [],
                    },
                }
            )
        raise AssertionError(f"Unexpected language request: {instructions[:100]}")


class LongFormProductionLanguageClient(FakeLanguageClient):
    """Serve both persisted long-form planning and the existing production Agents."""

    def __init__(
        self,
        request: PlanningRunCreateRequest,
        *,
        repair_shot_sequences: set[int] | None = None,
    ) -> None:
        super().__init__(repair_shot_sequences=repair_shot_sequences)
        self.outline = _fallback_episode_outline(request)
        blueprint_payload = self.outline.model_dump(mode="python")
        if request.source_mode != SourceMode.SCRIPT_LOCKED:
            blueprint_payload["lines"] = []
            for scene in blueprint_payload["scenes"]:
                scene["dialogue_line_indices"] = []
        self.blueprint = type(self.outline).model_validate(blueprint_payload)
        self.scene_plans = {}
        self.planning_scene_calls: list[int] = []
        self.scene_prompt_payloads: list[dict[str, Any]] = []
        self.expected_handoffs: dict[int, dict[str, Any]] = {}
        handoff = self.outline.scenes[0].continuity_in.model_copy(deep=True)
        for scene in self.outline.scenes:
            self.expected_handoffs[scene.sequence] = handoff.model_dump(mode="json")
            plan = _fallback_directed_scene_plan(
                request,
                self.outline,
                scene,
                handoff,
            )
            self.scene_plans[scene.sequence] = plan
            handoff = plan.continuity_out.model_copy(deep=True)

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        if "story-architect Agent" in instructions and "revising an episode" not in instructions:
            return self.blueprint.model_dump_json()
        if "Scene Writer Agent" in instructions:
            payload = json.loads(input_text)
            sequence = int(payload["current_scene_node"]["sequence"])
            node = self.blueprint.scenes[sequence - 1]
            source_scene = self.outline.scenes[sequence - 1]
            return EpisodeScenePatch(
                scene_sequence=sequence,
                heading=node.heading,
                summary=node.summary,
                narrative_purpose=node.narrative_purpose,
                playable_actions=list(node.playable_actions),
                target_duration_seconds=node.target_duration_seconds,
                location_names=list(node.location_names),
                continuity_in=node.continuity_in.model_copy(deep=True),
                continuity_out=node.continuity_out.model_copy(deep=True),
                lines=scene_dialogue_lines(self.outline, source_scene),
            ).model_dump_json()
        if "independent script-critic Agent" in instructions:
            return json.dumps(
                {
                    "summary": "The deterministic long-form fixture is ready for scene direction.",
                    "strengths": ["The causal handoff is explicit across scenes."],
                    "issues": [],
                    "requires_revision": False,
                }
            )
        if "scene-director Agent" in instructions:
            payload = json.loads(input_text)
            self.scene_prompt_payloads.append(payload)
            sequence = int(payload["scene_contract"]["sequence"])
            self.planning_scene_calls.append(sequence)
            return self.scene_plans[sequence].model_dump_json()
        return super().generate_text(
            instructions=instructions,
            input_text=input_text,
            image_urls=image_urls,
            max_output_tokens=max_output_tokens,
        )


class AutoCompletingH3Client:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self.requests: dict[str, H3VideoRequest] = {}
        self.remote_tasks: dict[str, H3RemoteTask] = {}
        self.query_counts: dict[str, int] = {}
        self.uploads: list[Path] = []

    def submit(self, request: H3VideoRequest) -> str:
        task_id = f"remote-{len(self.requests) + 1}"
        self.requests[task_id] = request.model_copy(deep=True)
        self.remote_tasks[task_id] = H3RemoteTask(
            id=task_id,
            model=request.model,
            status=H3RemoteStatus.QUEUED,
            resolution=request.resolution,
            duration=request.duration,
            ratio=request.ratio,
            task_type="generation",
            modality="video",
        )
        self.query_counts[task_id] = 0
        return task_id

    def query(self, task_id: str) -> H3RemoteTask:
        self.query_counts[task_id] += 1
        task = self.remote_tasks[task_id]
        if self.query_counts[task_id] == 1:
            task.status = H3RemoteStatus.RUNNING
        else:
            task.status = H3RemoteStatus.SUCCEEDED
            task.content = H3TaskContent(url=f"fake://{task_id}.mp4")
            task.usage = H3TaskUsage(
                total_seconds=task.duration,
                input_seconds=0,
                output_seconds=task.duration,
                input_image_count=1,
                input_audio_seconds=0,
            )
        return task.model_copy(deep=True)

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]:
        return [
            item.model_copy(deep=True)
            for item in list(self.remote_tasks.values())[:page_size]
        ]

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse:
        self.remote_tasks[task_id].status = H3RemoteStatus.CANCELLED
        return H3DeleteResponse(task_id=task_id, action="cancelled", status="cancelled")

    def upload_file(self, path: Path) -> str:
        self.uploads.append(path.resolve())
        return f"mm_file://upload-{len(self.uploads)}"

    def download(self, url: str, destination: Path) -> None:
        task_id = url.removeprefix("fake://").removesuffix(".mp4")
        duration = self.requests[task_id].duration
        source = self.cache_root / f"h3-{duration}s.mp4"
        if not source.is_file():
            _render_h3_video(source, duration)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


class FailFirstH3Client(AutoCompletingH3Client):
    def __init__(self, cache_root: Path) -> None:
        super().__init__(cache_root)
        self.failed_remote_id: str | None = None

    def query(self, task_id: str) -> H3RemoteTask:
        if self.failed_remote_id is None:
            self.failed_remote_id = task_id
        if task_id == self.failed_remote_id:
            task = self.remote_tasks[task_id]
            task.status = H3RemoteStatus.FAILED
            return task.model_copy(deep=True)
        return super().query(task_id)


class SlowFirstH3Client(AutoCompletingH3Client):
    def __init__(self, cache_root: Path) -> None:
        super().__init__(cache_root)
        self.slow_remote_id: str | None = None

    def query(self, task_id: str) -> H3RemoteTask:
        if self.slow_remote_id is None:
            self.slow_remote_id = task_id
        if task_id == self.slow_remote_id:
            self.query_counts[task_id] += 1
            task = self.remote_tasks[task_id]
            task.status = H3RemoteStatus.RUNNING
            return task.model_copy(deep=True)
        return super().query(task_id)


def _render_h3_video(path: Path, duration: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=768x1366:r=12:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _service(
    tmp_path: Path,
    image: FakeImageProvider,
    language: FakeLanguageClient,
    h3: AutoCompletingH3Client,
) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=lambda _api_key, _base_url: image,
        language_provider_factory=lambda _api_key, _base_url: language,
        h3_client_factory=lambda _api_key: h3,
    )
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key="image-key",
            base_url="https://image.example.test/v1",
        )
    )
    service.update_h3_config(H3ProviderConfigUpdate(api_key="h3-key"))
    return service


def _finish_long_form_planning(
    service: ShortDramaService,
    request: PlanningRunCreateRequest,
) -> tuple[str, str]:
    response = service.create_planning_run(request)
    for _ in range(80):
        if response.run.status == PlanningRunStatus.COMPLETE:
            break
        response = service.advance_planning_run(response.run.id)
    assert response.run.status == PlanningRunStatus.COMPLETE
    assert response.run.project_id is not None
    assert response.run.autonomous_run_id is not None
    return response.run.project_id, response.run.autonomous_run_id


def _finish_real_autonomous_run(
    service: ShortDramaService,
    *,
    project_id: str,
    run_id: str,
    max_cycles: int = 600,
    selected_take_trace: list[int] | None = None,
) -> AutonomousDraftResponse:
    for _ in range(max_cycles):
        response = service.advance_autonomous_run(
            project_id,
            run_id,
            AutonomousRunAdvanceRequest(max_actions=60),
        )
        assert response.run.status != AutonomousRunStatus.FAILED
        if selected_take_trace is not None:
            selected_take_trace.append(
                response.project.progress.shots_with_selected_take
            )
        if response.run.status == AutonomousRunStatus.COMPLETE:
            return response
    raise AssertionError("long-form autonomous run did not finish within the test bound")


def test_long_form_planning_handoff_completes_two_minute_export_after_restart(
    tmp_path: Path,
) -> None:
    request = PlanningRunCreateRequest(
        name="两分钟无人值守制片",
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
        target_duration_seconds=120,
        run_mode=RunMode.AUTO_DRAFT,
        execution_target=AutonomousExecutionTarget.REAL_H3,
        render_export=True,
    )
    image = FakeImageProvider()
    language = LongFormProductionLanguageClient(
        request,
        repair_shot_sequences={1, 2, 3, 4},
    )
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    first = _service(tmp_path, image, language, h3)
    first.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    project_id, run_id = _finish_long_form_planning(first, request)

    planned = first.get_project(project_id)
    assert planned.document.project.target_duration_seconds == 120
    assert len(planned.document.shots) >= 8
    assert planned.document.autonomous_runs[-1].current_step.value == "PREPARE_ASSETS"

    before_restart: AutonomousDraftResponse | None = None
    for _ in range(80):
        candidate = first.advance_autonomous_run(
            project_id,
            run_id,
            AutonomousRunAdvanceRequest(max_actions=8),
        )
        assert candidate.run.status == AutonomousRunStatus.RUNNING
        selected = candidate.project.progress.shots_with_selected_take
        if 0 < selected < candidate.project.progress.total_shots:
            before_restart = candidate
            break
    assert before_restart is not None

    restarted = _service(tmp_path, image, language, h3)
    restarted.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    selected_take_trace = [before_restart.project.progress.shots_with_selected_take]
    completed = _finish_real_autonomous_run(
        restarted,
        project_id=project_id,
        run_id=run_id,
        selected_take_trace=selected_take_trace,
    )

    assert completed.run.status == AutonomousRunStatus.COMPLETE
    assert completed.export is not None
    manifest = completed.project.document.manifests[-1]
    assert manifest.duration_seconds == 120
    assert completed.project.progress.shots_with_selected_take == len(
        completed.project.document.shots
    )
    assert completed.project.progress.export_ready is True
    assert language.planning_scene_calls == [1, 2]
    assert completed.run.repair_round == 4
    assert language.director_calls == 4
    assert len(h3.requests) == (
        len(completed.project.document.shots) + language.director_calls * 2
    )
    assert len(restarted.repository.list_h3_route_plans(project_id)) >= 4
    assert selected_take_trace == sorted(selected_take_trace)
    assert selected_take_trace[-1] == len(completed.project.document.shots)
    revisions = {
        shot.sequence: shot.revision for shot in completed.project.document.shots
    }
    assert all(revisions[sequence] == 2 for sequence in {1, 2, 3, 4})
    assert all(
        revision == 1
        for sequence, revision in revisions.items()
        if sequence not in {1, 2, 3, 4}
    )


def test_five_minute_planning_handoff_completes_timeline_without_second_runtime(
    tmp_path: Path,
) -> None:
    request = PlanningRunCreateRequest(
        name="五分钟无人值守制片",
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
        target_duration_seconds=300,
        run_mode=RunMode.AUTO_DRAFT,
        execution_target=AutonomousExecutionTarget.REAL_H3,
        render_export=False,
    )
    image = FakeImageProvider()
    language = LongFormProductionLanguageClient(request)
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    project_id, run_id = _finish_long_form_planning(service, request)
    completed = _finish_real_autonomous_run(
        service,
        project_id=project_id,
        run_id=run_id,
    )

    assert completed.run.status == AutonomousRunStatus.COMPLETE
    assert completed.export is None
    manifest = completed.project.document.manifests[-1]
    assert manifest.duration_seconds == 300
    assert len(completed.project.document.shots) == 20
    assert completed.project.progress.shots_with_selected_take == 20
    assert completed.project.progress.manifest_ready is True
    assert completed.project.progress.export_ready is False
    assert len(language.planning_scene_calls) == 4
    assert len(language.scene_prompt_payloads) == 4
    assert all(
        set(payload) == {
            "episode",
            "scene_contract",
                "assigned_dialogue",
                "scene_pacing_evidence",
                "previous_actual_handoff",
                "routed_review_handoff",
                "next_scene",
                "output_schema",
        }
        for payload in language.scene_prompt_payloads
    )
    assert all(
        payload["previous_actual_handoff"]
        == language.expected_handoffs[int(payload["scene_contract"]["sequence"])]
        for payload in language.scene_prompt_payloads
    )
    assert len(h3.requests) == len(completed.project.document.shots)
    assert len(language.episode_scene_payloads) == 4
    assert len(language.episode_summary_payloads) == 1
    assert language.asset_review_instructions
    assert all(
        "Asset Reviewer rubric:" in instructions
        and "Active Production Skill: live-action Douyin vertical short drama." in instructions
        for instructions in language.asset_review_instructions
    )
    assert language.route_instructions
    assert all(
        "Route Planner rubric:" in instructions
        and "Active Production Skill: live-action Douyin vertical short drama." in instructions
        for instructions in language.route_instructions
    )
    assert language.video_observation_instructions
    assert all(
        "Active Production Skill:" not in instructions
        and "Production constitution:" not in instructions
        for instructions in language.video_observation_instructions
    )
    assert all(
        "Episode Reviewer rubric:" in instructions
        and "Production constitution:" in instructions
        for instructions in language.episode_scene_instructions
        + language.episode_summary_instructions
    )
    assert all(
        len(payload["shot_evidence"]) == 5
        for payload in language.episode_scene_payloads
    )
    assert all(
        set(payload) == {
            "output_schema",
            "episode",
            "scene",
            "scene_sequence",
            "shot_evidence",
            "image_map",
            "allowed_dimensions",
            "allowed_issue_categories",
            "product_rule",
        }
        for payload in language.episode_scene_payloads
    )
    assert completed.project.episode_reviews[-1].status == EpisodeReviewStatus.COMPLETE
    assert completed.project.episode_reviews[-1].findings == []
    assert language.director_calls == 0
    assert all(
        run.execution_target == AutonomousExecutionTarget.REAL_H3
        for run in completed.project.document.autonomous_runs
    )


def test_episode_reviewer_repairs_only_linked_shot_and_resumes_after_restart(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(episode_repair_shot_sequences={2})
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    first = _service(tmp_path, image, language, h3)
    response = first.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    first.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    partial: AutonomousDraftResponse | None = None
    for _ in range(240):
        response = first.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=12),
        )
        if (
            response.run.current_step == AutonomousRunStep.EPISODE_REVIEW
            and response.project.episode_reviews
            and response.project.episode_reviews[-1].status == EpisodeReviewStatus.RUNNING
            and response.project.episode_reviews[-1].next_scene_index > 0
        ):
            partial = response
            break
    assert partial is not None

    restarted = _service(tmp_path, image, language, h3)
    restarted.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    completed = _finish_real_autonomous_run(
        restarted,
        project_id=partial.project.document.project.id,
        run_id=partial.run.id,
    )

    assert completed.run.status == AutonomousRunStatus.COMPLETE
    assert completed.run.repair_round == 1
    assert language.director_calls == 1
    revisions = {
        shot.sequence: shot.revision for shot in completed.project.document.shots
    }
    assert revisions[2] == 2
    assert all(revision == 1 for sequence, revision in revisions.items() if sequence != 2)
    repaired_report = next(
        item
        for item in completed.project.episode_reviews
        if item.repair_applied_shot_ids
    )
    shot_two = next(
        item for item in completed.project.document.shots if item.sequence == 2
    )
    assert repaired_report.repair_applied_shot_ids == [shot_two.id]
    assert completed.project.episode_reviews[-1].status == EpisodeReviewStatus.COMPLETE
    assert completed.project.episode_reviews[-1].findings == []


def test_episode_reviewer_failure_does_not_block_export_or_require_attention(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(fail_episode_reviews=3)
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=True,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    completed = _finish_real_autonomous_run(
        service,
        project_id=response.project.document.project.id,
        run_id=response.run.id,
    )

    assert completed.run.status == AutonomousRunStatus.COMPLETE
    assert completed.export is not None
    assert language.director_calls == 0
    report = completed.project.episode_reviews[-1]
    assert report.status == EpisodeReviewStatus.FAILED
    assert report.active_stage_attempts == 3
    assert "导出不受阻" in report.summary
    assert any(f"Episode review {report.id}:" in item for item in completed.run.warnings)


def test_episode_reviewer_respects_existing_per_shot_repair_limit(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(persistent_episode_repair_shot_sequences={1})
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    completed = _finish_real_autonomous_run(
        service,
        project_id=response.project.document.project.id,
        run_id=response.run.id,
        max_cycles=900,
    )

    assert completed.run.status == AutonomousRunStatus.COMPLETE
    assert completed.run.repair_round == completed.run.max_repair_rounds == 3
    assert language.director_calls == 3
    assert len(completed.project.episode_reviews) == 4
    last_report = completed.project.episode_reviews[-1]
    first_shot = min(completed.project.document.shots, key=lambda item: item.sequence)
    assert last_report.repair_skipped_shot_ids == [first_shot.id]
    assert any("per-shot automatic repair limit" in item for item in last_report.warnings)


def test_repeat_run_reuses_approved_preproduction_but_starts_new_generation(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient()
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    source = service.initialize_fixture()
    asset_path = tmp_path / "approved-core.png"
    Image.new("RGB", (768, 1344), (40, 60, 80)).save(asset_path)
    source_document = source.document
    source_document.project.production_profile = ProductionProfile.GENERAL_VIDEO
    for asset in source_document.assets:
        if asset.kind not in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}:
            continue
        version = asset.versions[-1].model_copy(
            deep=True,
            update={
                "media_type": AssetMediaType.IMAGE,
                "local_path": str(asset_path),
                "approval_status": ApprovalStatus.APPROVED,
                "freshness": Freshness.CURRENT,
            },
        )
        asset.versions = [version]
        asset.current_version_id = version.id
        asset.approved_version_id = version.id
    service.repository.save_project(source_document)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    repeated = service.create_autonomous_repeat(
        source.document.project.id,
        AutonomousRepeatCreateRequest(name="固定制片包独立重复 2"),
    )

    assert repeated.project.document.project.id != source.document.project.id
    assert repeated.project.document.project.production_profile == (
        ProductionProfile.GENERAL_VIDEO
    )
    assert repeated.run.status == AutonomousRunStatus.RUNNING
    assert repeated.run.current_step.value in {"PREPARE_SHOT_ASSETS", "GENERATE"}
    assert repeated.project.tasks == []
    assert repeated.project.takes == []
    assert repeated.project.document.manifests == []
    assert repeated.run.planner.startswith("fixed-preproduction-repeat:")
    source_versions = {
        asset.name: asset.approved_version_id
        for asset in service.get_project(source.document.project.id).document.assets
        if asset.kind in {AssetKind.CHARACTER, AssetKind.LOCATION, AssetKind.PROP}
    }
    repeat_versions = {
        asset.name: asset.approved_version_id
        for asset in repeated.project.document.assets
    }
    assert repeat_versions == source_versions
    assert {
        item.id for item in repeated.project.document.shots
    }.isdisjoint({item.id for item in source.document.shots})


def test_real_autonomous_draft_resumes_and_exports_without_shot_by_shot_input(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient()
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)

    started = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            name="自动真人草稿",
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=True,
        )
    )
    assert started.run.status == AutonomousRunStatus.RUNNING
    assert started.project.tasks == []
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    response = started
    rebuilt = False
    for _ in range(160):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        assert response.run.status != AutonomousRunStatus.FAILED
        if response.run.waiting_reason and "MiniMax-H3" in response.run.waiting_reason:
            service = _service(tmp_path, image, language, h3)
            rebuilt = True
        if response.run.status == AutonomousRunStatus.COMPLETE:
            break

    assert rebuilt is True
    assert response.run.status == AutonomousRunStatus.COMPLETE
    assert "PLAN_ROUTES" in {
        step.value for step in response.run.completed_steps
    }
    assert "AUDIO_QA" not in {
        step.value for step in response.run.completed_steps
    }
    assert response.export is not None
    assert Path(response.export.subtitled_video_path).is_file()
    selected = [item for item in response.project.takes if item.is_selected]
    assert len(selected) == len(response.project.document.shots)
    assert {item.provider for item in selected} == {"minimax"}
    assert response.project.video_inspections
    assert response.project.audio_inspections == []
    later_shot_ids = {
        item.id for item in response.project.document.shots if item.sequence > 1
    }
    assert all(
        "SHOT_BOUNDARY" in report.scopes
        for report in response.project.video_inspections
        if report.shot_id in later_shot_ids
    )
    core_assets = [
        item
        for item in response.project.document.assets
        if item.kind.value in {"CHARACTER", "LOCATION", "PROP"}
    ]
    assert all(item.approved_version_id for item in core_assets)
    assert "EDIT" in image.operations
    derived_assets = [
        item
        for item in response.project.document.assets
        if item.kind == AssetKind.REFERENCE
        and any(
            "AUTO_REFERENCE_VIEW:" in version.descriptor
            for version in item.versions
        )
    ]
    assert derived_assets
    assert all(item.approved_version_id for item in derived_assets)
    assert service.inspect_asset_coverage(
        response.project.document.project.id
    ).ready
    plans = service.repository.list_h3_route_plans(response.project.document.project.id)
    assert any(
        preparation.approved_version_id is not None
        for plan in plans
        for decision in plan.decisions
        for preparation in decision.asset_preparations
    )
    assert any(
        decision.video_guides
        for plan in plans
        for decision in plan.decisions
        if decision.shot_sequence > 1
    )


def test_real_autonomous_draft_persists_attention_when_image_provider_is_missing(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient()
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=lambda _api_key, _base_url: image,
        language_provider_factory=lambda _api_key, _base_url: language,
        h3_client_factory=lambda _api_key: h3,
    )

    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
        )
    )

    assert response.run.status == AutonomousRunStatus.NEEDS_ATTENTION
    assert "配置 GPT Image 2" in (response.run.error_message or "")
    assert response.project.document.autonomous_runs[-1].id == response.run.id
    assert response.project.tasks == []


def test_real_autonomous_draft_retries_a_rejected_visual_asset_once(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(reject_first_asset=True)
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    started = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    response = started
    for _ in range(20):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=1),
        )
        first_character = next(
            item
            for item in response.project.document.assets
            if item.kind.value == "CHARACTER"
        )
        if first_character.approved_version_id is not None:
            break

    first_character = next(
        item
        for item in response.project.document.assets
        if item.kind.value == "CHARACTER"
    )
    assert first_character.approved_version_id is not None
    assert any(
        version.approval_status.value == "REJECTED"
        for version in first_character.versions
    )
    tasks = service.repository.list_image_tasks(
        response.project.document.project.id,
        first_character.id,
    )
    assert sorted(item.attempt for item in tasks) == [1, 2]


def test_real_autonomous_draft_creates_only_one_retry_for_a_failed_h3_task(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient()
    h3 = FailFirstH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    failed_task_id: str | None = None
    for _ in range(180):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        failed = next(
            (task for task in response.project.tasks if task.status.value == "FAILED"),
            None,
        )
        if failed is not None:
            failed_task_id = failed.id
            children = [
                task
                for task in response.project.tasks
                if task.retry_of_task_id == failed_task_id
            ]
            if children:
                break

    assert failed_task_id is not None
    for _ in range(6):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=5),
        )
    children = [
        task
        for task in response.project.tasks
        if task.retry_of_task_id == failed_task_id
    ]
    assert len(children) == 1


def test_real_autonomous_draft_rotates_polling_past_a_slow_h3_task(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient()
    h3 = SlowFirstH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    document = service.repository.get_project(response.project.document.project.id)
    assert document is not None
    for index, shot in enumerate(document.shots):
        shot.scene_id = f"polling-test-scene-{index % 2}"
    service.repository.save_project(document)
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    for _ in range(180):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        polled = [count for count in h3.query_counts.values() if count > 0]
        if len(polled) >= 2:
            break

    assert h3.slow_remote_id is not None
    assert h3.query_counts[h3.slow_remote_id] >= 1
    assert len([count for count in h3.query_counts.values() if count > 0]) >= 2


def test_real_autonomous_draft_retries_transient_semantic_review_failure(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(fail_video_reviews=1)
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    for _ in range(240):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        if response.run.status == AutonomousRunStatus.COMPLETE:
            break

    assert response.run.status == AutonomousRunStatus.COMPLETE
    failed = [
        report
        for report in response.project.video_inspections
        if report.semantic_status.value == "FAILED"
    ]
    assert len(failed) == 1
    assert any(
        report.take_id == failed[0].take_id
        and report.semantic_status.value == "COMPLETE"
        for report in response.project.video_inspections
    )


def test_real_autonomous_draft_repairs_rejected_video_and_replans(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(repair_first_shot=True)
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    for _ in range(220):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        assert response.run.status != AutonomousRunStatus.FAILED
        if response.run.status == AutonomousRunStatus.COMPLETE:
            break

    assert response.run.status == AutonomousRunStatus.COMPLETE
    assert response.run.repair_round == 1
    assert language.director_calls == 1
    assert len(response.project.document.repair_history) == 1
    first_shot = min(response.project.document.shots, key=lambda item: item.sequence)
    assert first_shot.revision == 2
    assert "simpler, clearly readable composition" in first_shot.visual
    assert any(
        take.shot_id == first_shot.id and take.freshness.value == "STALE"
        for take in response.project.takes
    )
    assert any(
        take.shot_id == first_shot.id
        and take.freshness.value == "CURRENT"
        and take.is_selected
        for take in response.project.takes
    )


def test_real_autonomous_draft_limits_repairs_per_shot_not_per_episode(
    tmp_path: Path,
) -> None:
    image = FakeImageProvider()
    language = FakeLanguageClient(persistent_repair_shot_sequences={1})
    h3 = AutoCompletingH3Client(tmp_path / "h3-cache")
    service = _service(tmp_path, image, language, h3)
    response = service.create_autonomous_draft(
        AutonomousDraftCreateRequest(
            source_mode=SourceMode.AUTO_ORIGINAL,
            target_duration_seconds=50,
            input_text="",
            execution_target=AutonomousExecutionTarget.REAL_H3,
            render_export=False,
        )
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )

    for _ in range(260):
        response = service.advance_autonomous_run(
            response.project.document.project.id,
            response.run.id,
            AutonomousRunAdvanceRequest(max_actions=40),
        )
        assert response.run.status != AutonomousRunStatus.FAILED
        if response.run.status == AutonomousRunStatus.COMPLETE:
            break

    assert response.run.status == AutonomousRunStatus.COMPLETE
    assert response.run.repair_round == response.run.max_repair_rounds == 3
    assert language.director_calls == 3
    first_shot = min(response.project.document.shots, key=lambda item: item.sequence)
    selected = next(
        take
        for take in response.project.takes
        if take.shot_id == first_shot.id and take.is_selected
    )
    assert selected.qa_status.value == "REJECTED"
    assert "bounded-auto-draft-best-effort-waiver" in selected.qa_report.checks
    assert "3 automatic Director repair attempt(s) for this shot" in (
        selected.qa_report.reviewer_notes or ""
    )
