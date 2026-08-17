"""Offline contract and lifecycle coverage for the MiniMax-H3 adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.h3_models import (
    H3CreateResponse,
    H3DeleteResponse,
    H3ListResponse,
    H3MediaInput,
    H3MediaKind,
    H3MediaRole,
    H3MediaSource,
    H3NotSubmittedResolutionRequest,
    H3ProbeRequest,
    H3ProviderConfigUpdate,
    H3QueryResponse,
    H3RemoteStatus,
    H3RemoteTask,
    H3TakeReviewRequest,
    H3TaskContent,
    H3TaskUsage,
    H3VideoRequest,
)
from short_drama.models import (
    ActionBeat,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    SpatialInteractionConstraint,
    SurfaceAttachmentConstraint,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.reference_planner import (
    DirectorRepairDecisionRequest,
    ShotPlanPatch,
)
from short_drama.providers.minimax_h3_client import H3SubmissionUnknown
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    compile_h3_request,
)
from short_drama.service import ShortDramaService, WorkflowConflictError


FIXTURES = Path(__file__).parent / "fixtures" / "minimax_h3"


class FakeH3Client:
    """Stateful in-process implementation of the H3 client protocol."""

    def __init__(self) -> None:
        self.submitted_requests: list[H3VideoRequest] = []
        self.remote_tasks: dict[str, H3RemoteTask] = {}
        self.upload_calls: list[Path] = []
        self.cancel_calls: list[str] = []
        self.output_source: Path | None = None
        self.submit_unknown_once = False
        self.fail_downloads = 0

    def submit(self, request: H3VideoRequest) -> str:
        self.submitted_requests.append(request.model_copy(deep=True))
        task_id = f"remote-{len(self.submitted_requests)}"
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
        if self.submit_unknown_once:
            self.submit_unknown_once = False
            raise H3SubmissionUnknown("simulated response loss after provider acceptance")
        return task_id

    def query(self, task_id: str) -> H3RemoteTask:
        return self.remote_tasks[task_id].model_copy(deep=True)

    def list_tasks(self, *, page_size: int = 20) -> list[H3RemoteTask]:
        return [
            task.model_copy(deep=True)
            for task in list(self.remote_tasks.values())[:page_size]
        ]

    def cancel_or_delete(self, task_id: str) -> H3DeleteResponse:
        self.cancel_calls.append(task_id)
        task = self.remote_tasks[task_id]
        task.status = H3RemoteStatus.CANCELLED
        return H3DeleteResponse(task_id=task_id, action="cancelled", status="cancelled")

    def upload_file(self, path: Path) -> str:
        self.upload_calls.append(path.resolve())
        return f"mm_file://file-{len(self.upload_calls)}"

    def download(self, url: str, destination: Path) -> None:
        del url
        if self.fail_downloads > 0:
            self.fail_downloads -= 1
            from short_drama.providers.minimax_h3_client import H3TransientError

            raise H3TransientError("simulated expired output URL")
        if self.output_source is None:
            raise AssertionError("Fake output_source must be configured before success")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.output_source, destination)

    def set_status(self, task_id: str, status: H3RemoteStatus) -> None:
        task = self.remote_tasks[task_id]
        task.status = status
        if status == H3RemoteStatus.SUCCEEDED:
            task.content = H3TaskContent(url=f"fake://{task_id}.mp4")
            task.usage = H3TaskUsage(
                total_seconds=task.duration,
                input_seconds=0,
                output_seconds=task.duration,
                input_image_count=0,
                input_audio_seconds=0,
            )

    def register_queued(self, task_id: str, *, duration: int = 5) -> None:
        self.remote_tasks[task_id] = H3RemoteTask(
            id=task_id,
            model="MiniMax-H3",
            status=H3RemoteStatus.QUEUED,
            resolution="768P",
            duration=duration,
            ratio="9:16",
            task_type="generation",
            modality="video",
        )


def _fake_factory(fake: FakeH3Client):
    def create(api_key: str) -> FakeH3Client:
        assert api_key == "test-minimax-key"
        return fake

    return create


def _service(tmp_path: Path, fake: FakeH3Client) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "app_data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        h3_client_factory=_fake_factory(fake),
    )
    config = service.update_h3_config(H3ProviderConfigUpdate(api_key="test-minimax-key"))
    assert config.has_api_key is True
    return service


def _render_h3_output(path: Path, duration: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x273447:s=768x1366:r=24:d={duration}",
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
            "-ar",
            "48000",
            "-ac",
            "2",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_acedata_h3_response_fixtures_parse() -> None:
    create_response = H3CreateResponse.model_validate(_load("create_success.json"))
    assert create_response.task_id
    assert create_response.trace_id == "trace_7f8c2b1a"
    assert H3QueryResponse.model_validate(_load("query_queued.json")).task.status == "queued"
    assert H3QueryResponse.model_validate(_load("query_running.json")).task.status == "running"
    succeeded = H3QueryResponse.model_validate(_load("query_succeeded.json")).task
    assert succeeded.content is not None
    assert succeeded.content.url is not None
    assert succeeded.usage is not None
    assert succeeded.usage.output_seconds == 5
    failed = H3QueryResponse.model_validate(_load("query_failed.json")).task
    assert failed.error is not None
    assert failed.error.code == "content_policy_violation"
    listed = H3ListResponse.model_validate(_load("list_tasks.json"))
    assert listed.total == 1
    assert listed.items[0].model == "MiniMax-H3"


def test_h3_prompt_only_constrains_assets_present_in_the_shot() -> None:
    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot_2 = next(shot for shot in document.shots if shot.sequence == 2)
    shot_5 = next(shot for shot in document.shots if shot.sequence == 5)

    dialogue_prompt = build_h3_prompt(document, shot_2)
    prop_prompt = build_h3_prompt(document, shot_5)

    assert "红色录音笔" not in dialogue_prompt
    assert "不要主动加入本镜头未提及的角色或道具" in dialogue_prompt
    assert "红色录音笔" in prop_prompt
    assert "连续性状态：开始时道具从周衡右手进入" in prop_prompt


def test_h3_prompt_carries_structured_contact_and_rigid_attachments() -> None:
    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot = next(item for item in document.shots if item.sequence == 8)
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    linxia = next(asset for asset in document.assets if asset.name == "林夏定妆")
    store = next(asset for asset in document.assets if asset.name == "雨夜便利店")
    plan.action_beats = [
        ActionBeat(
            order=1,
            actor_asset_id=linxia.id,
            action="用右手打开指定门体",
            target_anchor="entrance.primary_door",
            required_end_state="指定门体打开",
        )
    ]
    plan.interactions = [
        SpatialInteractionConstraint(
            id="exit-door",
            actor_asset_id=linxia.id,
            effector="RIGHT_HAND",
            action="打开",
            target_asset_id=store.id,
            target_anchor="entrance.primary_door",
            start_state="关闭",
            end_state="打开",
            unchanged_anchors=["entrance.fixed_panel"],
            contact_anchor="entrance.primary_door.handle.interior",
            contact_surface="entrance.primary_door.face.interior",
            contact_persists=True,
            rigid_attachments=[
                SurfaceAttachmentConstraint(
                    anchor="entrance.primary_door.handle.exterior",
                    surface="entrance.primary_door.face.exterior",
                )
            ],
        )
    ]

    prompt = build_h3_prompt(document, shot)

    assert "动作节拍必须依序执行" in prompt
    assert "本镜头内保持接触，除非后续动作节拍明确改变该状态" in prompt
    assert "entrance.primary_door.handle.exterior固定在entrance.primary_door.face.exterior" in prompt
    assert "部件不得消失、换面、复制、脱离或漂移" in prompt


def test_h3_compiler_enforces_routes_before_network() -> None:
    first = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(external_url="https://example.invalid/first.png"),
    )
    last = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.LAST_FRAME,
        source=H3MediaSource(external_url="https://example.invalid/last.png"),
    )
    reference = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(file_id="123"),
    )
    reference_video = H3MediaInput(
        kind=H3MediaKind.VIDEO,
        role=H3MediaRole.REFERENCE_VIDEO,
        source=H3MediaSource(external_url="https://example.invalid/reference.mp4"),
    )
    reference_audio = H3MediaInput(
        kind=H3MediaKind.AUDIO,
        role=H3MediaRole.REFERENCE_AUDIO,
        source=H3MediaSource(external_url="https://example.invalid/reference.wav"),
    )

    assert H3ProbeRequest(shot_id="shot-1").purpose == EvidencePurpose.CONTRACT_SMOKE

    direct = compile_h3_request(
        route=VideoGenerationRoute.DIRECT,
        prompt="真人都市短剧",
        duration_seconds=5,
        media_inputs=[],
        resolve_url=lambda item: item.source.external_url or "unused",
    )
    assert direct.model == "MiniMax-H3"
    assert direct.resolution == "768P"
    assert direct.ratio == "9:16"

    frame = compile_h3_request(
        route=VideoGenerationRoute.FRAME_FIRST,
        prompt="首帧驱动",
        duration_seconds=5,
        media_inputs=[first],
        resolve_url=lambda item: item.source.external_url or "unused",
    )
    assert frame.ratio is None
    assert frame.content[1].role == H3MediaRole.FIRST_FRAME

    both = compile_h3_request(
        route=VideoGenerationRoute.FRAME_FIRST_LAST,
        prompt="首尾帧驱动",
        duration_seconds=5,
        media_inputs=[first, last],
        resolve_url=lambda item: item.source.external_url or "unused",
    )
    assert [item.role for item in both.content[1:]] == [
        H3MediaRole.FIRST_FRAME,
        H3MediaRole.LAST_FRAME,
    ]

    referenced = compile_h3_request(
        route=VideoGenerationRoute.REFERENCE,
        prompt="角色参考",
        duration_seconds=5,
        media_inputs=[reference, reference_video, reference_audio],
        resolve_url=lambda item: item.source.external_url or "mm_file://123",
    )
    assert referenced.ratio == "9:16"
    assert referenced.content[1].image_url is not None
    assert referenced.content[1].image_url.url == "mm_file://123"
    assert [item.role for item in referenced.content[1:]] == [
        H3MediaRole.REFERENCE_IMAGE,
        H3MediaRole.REFERENCE_VIDEO,
        H3MediaRole.REFERENCE_AUDIO,
    ]

    reference_images = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url=f"https://example.invalid/reference-{index}.png"),
        )
        for index in range(10)
    ]
    reference_videos = [
        H3MediaInput(
            kind=H3MediaKind.VIDEO,
            role=H3MediaRole.REFERENCE_VIDEO,
            source=H3MediaSource(external_url=f"https://example.invalid/reference-{index}.mp4"),
        )
        for index in range(4)
    ]
    reference_audios = [
        H3MediaInput(
            kind=H3MediaKind.AUDIO,
            role=H3MediaRole.REFERENCE_AUDIO,
            source=H3MediaSource(external_url=f"https://example.invalid/reference-{index}.wav"),
        )
        for index in range(4)
    ]
    at_total_limit = reference_images[:9] + reference_videos[:3]
    compiled_at_limit = compile_h3_request(
        route=VideoGenerationRoute.REFERENCE,
        prompt="12 个参考素材合法",
        duration_seconds=5,
        media_inputs=at_total_limit,
        resolve_url=lambda item: item.source.external_url or "unused",
    )
    assert len(compiled_at_limit.content) == 13

    with pytest.raises(H3CapabilityError, match="mutually exclusive"):
        compile_h3_request(
            route=VideoGenerationRoute.REFERENCE,
            prompt="非法混用",
            duration_seconds=5,
            media_inputs=[first, reference],
            resolve_url=lambda _item: "unused",
        )
    with pytest.raises(H3CapabilityError, match="paired"):
        compile_h3_request(
            route=VideoGenerationRoute.FRAME_FIRST_LAST,
            prompt="官方字段 schema 仍要求尾帧与首帧配对",
            duration_seconds=5,
            media_inputs=[last],
            resolve_url=lambda _item: "unused",
        )
    with pytest.raises(H3CapabilityError, match="nine reference images"):
        compile_h3_request(
            route=VideoGenerationRoute.REFERENCE,
            prompt="超过参考图上限",
            duration_seconds=5,
            media_inputs=reference_images,
            resolve_url=lambda item: item.source.external_url or "unused",
        )
    with pytest.raises(H3CapabilityError, match="three reference videos"):
        compile_h3_request(
            route=VideoGenerationRoute.REFERENCE,
            prompt="超过参考视频上限",
            duration_seconds=5,
            media_inputs=reference_videos,
            resolve_url=lambda item: item.source.external_url or "unused",
        )
    with pytest.raises(H3CapabilityError, match="three reference audio"):
        compile_h3_request(
            route=VideoGenerationRoute.REFERENCE,
            prompt="超过参考音频上限",
            duration_seconds=5,
            media_inputs=reference_audios,
            resolve_url=lambda item: item.source.external_url or "unused",
        )
    with pytest.raises(H3CapabilityError, match="at most 12"):
        compile_h3_request(
            route=VideoGenerationRoute.REFERENCE,
            prompt="超过混合参考总数上限",
            duration_seconds=5,
            media_inputs=at_total_limit + reference_audios[:1],
            resolve_url=lambda item: item.source.external_url or "unused",
        )
    with pytest.raises(H3CapabilityError, match="LAST"):
        compile_h3_request(
            route=VideoGenerationRoute.FRAME_FIRST_LAST,
            prompt="缺少尾帧",
            duration_seconds=5,
            media_inputs=[first],
            resolve_url=lambda _item: "unused",
        )
    with pytest.raises(H3CapabilityError, match="DIRECT"):
        compile_h3_request(
            route=VideoGenerationRoute.DIRECT,
            prompt="非法媒体",
            duration_seconds=5,
            media_inputs=[first],
            resolve_url=lambda _item: "unused",
        )


def test_h3_lifecycle_download_review_and_take_selection(tmp_path: Path) -> None:
    fake = FakeH3Client()
    fake.output_source = tmp_path / "provider" / "h3-5s.mp4"
    _render_h3_output(fake.output_source)
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]

    probe = service.enqueue_h3_probe(
        project_id,
            H3ProbeRequest(
                shot_id=shot.id,
                route=VideoGenerationRoute.DIRECT,
                purpose=EvidencePurpose.QUALITY_PROBE,
                learning_objective="Verify the full H3 quality-review lifecycle.",
            ),
    )
    assert probe.task.status == TaskStatus.QUEUED
    assert probe.task.resolution == "768P"
    assert probe.provider_run.request is None
    assert probe.project.route_evidence[0].status == EvidenceStatus.AWAITING_OUTPUT

    drained = service.drain(project_id, 20)
    assert drained.processed_steps == 0
    assert service.repository.get_task(probe.task.id).status == TaskStatus.QUEUED  # type: ignore[union-attr]
    assert fake.submitted_requests == []

    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    acknowledged = service.advance_task(probe.task.id)
    assert acknowledged.status == TaskStatus.ACKNOWLEDGED
    assert acknowledged.provider_task_id == "remote-1"
    sent = fake.submitted_requests[0]
    assert sent.resolution == "768P"
    assert sent.ratio == "9:16"

    assert service.advance_task(probe.task.id).status == TaskStatus.ACKNOWLEDGED
    fake.set_status("remote-1", H3RemoteStatus.RUNNING)
    assert service.advance_task(probe.task.id).status == TaskStatus.RUNNING
    fake.set_status("remote-1", H3RemoteStatus.SUCCEEDED)
    assert service.advance_task(probe.task.id).status == TaskStatus.SUCCEEDED

    aggregate = service.get_project(project_id)
    take = next(item for item in aggregate.takes if item.task_id == probe.task.id)
    assert take.qa_status == QaStatus.PENDING
    assert take.is_selected is False
    assert take.width == 768
    assert take.height == 1366
    evidence = next(item for item in aggregate.route_evidence if item.task_id == probe.task.id)
    assert evidence.technical_passed is True
    assert evidence.status == EvidenceStatus.AWAITING_REVIEW
    run = service.repository.get_provider_run(probe.task.id)
    assert run is not None
    assert run.estimated_cost_usd == 0.4
    assert run.downloaded_path == take.media_path

    reviewed = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=True,
            score=4.6,
            notes="身份、道具、动作、台词、声线和口型人工检查通过。",
        ),
    )
    assert reviewed.take.qa_status == QaStatus.PASSED
    assert reviewed.take.is_selected is False
    assert reviewed.evidence.status == EvidenceStatus.REVIEWED
    persisted_evidence = service.repository.get_route_evidence_for_take(take.id)
    assert persisted_evidence is not None
    assert persisted_evidence.purpose == EvidencePurpose.QUALITY_PROBE
    persisted_evidence.purpose = EvidencePurpose.CONTRACT_SMOKE
    service.repository.save_route_evidence(persisted_evidence)
    with pytest.raises(WorkflowConflictError, match="contract-smoke"):
        service.select_take(take.id)
    persisted_evidence.purpose = EvidencePurpose.QUALITY_PROBE
    service.repository.save_route_evidence(persisted_evidence)
    selected = service.select_take(take.id)
    assert next(item for item in selected.takes if item.id == take.id).is_selected is True


def test_running_h3_output_is_stale_after_director_changes_shot_plan(
    tmp_path: Path,
) -> None:
    fake = FakeH3Client()
    fake.output_source = tmp_path / "provider" / "h3-5s.mp4"
    _render_h3_output(fake.output_source)
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    probe = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(shot_id=shot.id),
    )
    service.advance_task(probe.task.id)
    acknowledged = service.advance_task(probe.task.id)
    assert acknowledged.provider_task_id is not None
    fake.set_status(acknowledged.provider_task_id, H3RemoteStatus.RUNNING)
    assert service.advance_task(probe.task.id).status == TaskStatus.RUNNING

    updated = service.decide_director_repair(
        project_id,
        shot.id,
        DirectorRepairDecisionRequest(
            decision=DirectorRepairDecision.APPLIED,
            strategy=DirectorRepairStrategy.CHANGE_CAMERA,
            reason="Use coverage that reduces the failed continuity requirements.",
            shot_patch=ShotPlanPatch(camera="rear coverage"),
        ),
    )
    current_shot = next(item for item in updated.document.shots if item.id == shot.id)
    assert current_shot.revision == 2

    fake.set_status(acknowledged.provider_task_id, H3RemoteStatus.SUCCEEDED)
    assert service.advance_task(probe.task.id).status == TaskStatus.SUCCEEDED
    take = next(
        item for item in service.get_project(project_id).takes if item.task_id == probe.task.id
    )
    assert take.freshness == Freshness.STALE
    reviewed = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=False,
            score=1.5,
            notes="The actor turns their head back against the intended travel direction.",
            issues=[
                QaIssue(
                    code="HUMAN_CHARACTER_ORIENTATION",
                    severity="ERROR",
                    message="Head, torso, and travel direction are inconsistent.",
                    repair_layer="DIRECTOR_AGENT",
                    category=QaFailureCategory.CHARACTER_ORIENTATION,
                )
            ],
        ),
    )
    assert reviewed.take.qa_status == QaStatus.REJECTED
    profile = service.get_h3_capability_profile(project_id)
    orientation_risk = next(
        item for item in profile.risks if item.id == "turn-and-crossing-orientation"
    )
    assert orientation_risk.observed_failure_count == 1
    assert orientation_risk.evidence_take_ids == [take.id]
    with pytest.raises(WorkflowConflictError, match="stale"):
        service.select_take(take.id)


def test_h3_submit_unknown_never_blindly_reposts(tmp_path: Path) -> None:
    fake = FakeH3Client()
    fake.submit_unknown_once = True
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    shot = project.document.shots[0]
    probe = service.enqueue_h3_probe(
        project.document.project.id,
        H3ProbeRequest(shot_id=shot.id),
    )

    service.advance_task(probe.task.id)
    unknown = service.advance_task(probe.task.id)
    assert unknown.status == TaskStatus.SUBMIT_UNKNOWN
    evidence = service.repository.get_route_evidence_for_task(probe.task.id)
    assert evidence is not None
    assert evidence.status == EvidenceStatus.SUBMIT_UNKNOWN
    assert len(fake.submitted_requests) == 1
    with pytest.raises(WorkflowConflictError, match="cannot advance"):
        service.advance_task(probe.task.id)
    assert len(fake.submitted_requests) == 1
    candidates = service.list_h3_reconciliation_candidates()
    assert [item.id for item in candidates.tasks] == ["remote-1"]

    resolved = service.resolve_h3_submission(probe.task.id, "remote-1")
    assert resolved.task.status == TaskStatus.ACKNOWLEDGED
    assert resolved.task.provider_task_id == "remote-1"
    assert service.repository.provider_submission_count(
        resolved.task.request_fingerprint
    ) == 1
    assert len(fake.submitted_requests) == 1
    assert resolved.project.route_evidence[0].status == EvidenceStatus.AWAITING_OUTPUT


def test_interrupted_prepare_can_be_explicitly_resolved_as_not_submitted(
    tmp_path: Path,
) -> None:
    fake = FakeH3Client()
    first = _service(tmp_path, fake)
    project = first.initialize_fixture()
    probe = first.enqueue_h3_probe(
        project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id),
    )
    assert first.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    assert fake.submitted_requests == []

    restarted = _service(tmp_path, fake)
    recovered = restarted.get_project(project.document.project.id)
    unknown = next(task for task in recovered.tasks if task.id == probe.task.id)
    assert unknown.status == TaskStatus.SUBMIT_UNKNOWN
    assert unknown.error_code == "PROCESS_INTERRUPTED_DURING_SUBMIT"

    resolved = restarted.resolve_h3_not_submitted(
        unknown.id,
        H3NotSubmittedResolutionRequest(
            reason="Process ended after local prepare and before the provider POST."
        ),
    )
    assert resolved.task.status == TaskStatus.FAILED
    assert resolved.task.error_code == "H3_SUBMISSION_NOT_CREATED"
    assert resolved.provider_run.last_error_code == "H3_SUBMISSION_NOT_CREATED"
    assert resolved.project.route_evidence[0].status == EvidenceStatus.FAILED
    assert fake.submitted_requests == []
    retry = restarted.retry_task(resolved.task.id)
    assert retry.status == TaskStatus.QUEUED


def test_h3_restart_reconciles_interrupted_submit_and_recovers_output(tmp_path: Path) -> None:
    fake = FakeH3Client()
    fake.output_source = tmp_path / "provider" / "h3-5s.mp4"
    _render_h3_output(fake.output_source)
    first = _service(tmp_path, fake)
    project = first.initialize_fixture()
    shot = project.document.shots[0]
    probe = first.enqueue_h3_probe(
        project.document.project.id,
        H3ProbeRequest(shot_id=shot.id),
    )
    assert first.advance_task(probe.task.id).status == TaskStatus.SUBMITTING

    second = _service(tmp_path, fake)
    recovered = second.repository.get_task(probe.task.id)
    assert recovered is not None
    assert recovered.status == TaskStatus.SUBMIT_UNKNOWN
    recovered_evidence = second.repository.get_route_evidence_for_task(probe.task.id)
    assert recovered_evidence is not None
    assert recovered_evidence.status == EvidenceStatus.SUBMIT_UNKNOWN
    fake.register_queued("remote-recovered", duration=5)
    resolved = second.resolve_h3_submission(probe.task.id, "remote-recovered")
    assert resolved.task.status == TaskStatus.ACKNOWLEDGED
    assert fake.submitted_requests == []

    fake.fail_downloads = 1
    fake.set_status("remote-recovered", H3RemoteStatus.SUCCEEDED)
    missing = second.advance_task(probe.task.id)
    assert missing.status == TaskStatus.OUTPUT_MISSING
    missing_evidence = second.repository.get_route_evidence_for_task(probe.task.id)
    assert missing_evidence is not None
    assert missing_evidence.status == EvidenceStatus.OUTPUT_MISSING
    succeeded = second.recover_task_output(probe.task.id)
    assert succeeded.status == TaskStatus.SUCCEEDED
    succeeded_evidence = second.repository.get_route_evidence_for_task(probe.task.id)
    assert succeeded_evidence is not None
    assert succeeded_evidence.status == EvidenceStatus.AWAITING_REVIEW
    assert fake.submitted_requests == []


def test_h3_cancel_semantics_and_local_upload_cache(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]

    local_probe = service.enqueue_h3_probe(project_id, H3ProbeRequest(shot_id=shot.id))
    assert service.cancel_task(local_probe.task.id).status == TaskStatus.CANCELLED
    local_evidence = service.repository.get_route_evidence_for_task(local_probe.task.id)
    assert local_evidence is not None
    assert local_evidence.status == EvidenceStatus.CANCELLED
    assert fake.cancel_calls == []

    remote_probe = service.enqueue_h3_probe(project_id, H3ProbeRequest(shot_id=shot.id))
    service.advance_task(remote_probe.task.id)
    service.advance_task(remote_probe.task.id)
    assert service.cancel_task(remote_probe.task.id).status == TaskStatus.CANCELLED
    remote_evidence = service.repository.get_route_evidence_for_task(remote_probe.task.id)
    assert remote_evidence is not None
    assert remote_evidence.status == EvidenceStatus.CANCELLED
    assert fake.cancel_calls == ["remote-1"]

    running_probe = service.enqueue_h3_probe(project_id, H3ProbeRequest(shot_id=shot.id))
    service.advance_task(running_probe.task.id)
    running = service.advance_task(running_probe.task.id)
    assert running.provider_task_id == "remote-2"
    fake.set_status("remote-2", H3RemoteStatus.RUNNING)
    with pytest.raises(WorkflowConflictError, match="does not allow cancelling"):
        service.cancel_task(running_probe.task.id)
    persisted = service.repository.get_task(running_probe.task.id)
    assert persisted is not None
    assert persisted.status == TaskStatus.RUNNING

    image_path = tmp_path / "inputs" / "first-frame.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (768, 1366), "#28384a").save(image_path)
    frame_input = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.FIRST_FRAME,
        source=H3MediaSource(local_path=str(image_path)),
    )
    for _index in range(2):
        probe = service.enqueue_h3_probe(
            project_id,
            H3ProbeRequest(
                shot_id=shot.id,
                route=VideoGenerationRoute.FRAME_FIRST,
                media_inputs=[frame_input],
            ),
        )
        assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    assert fake.upload_calls == [image_path.resolve()]


def test_h3_config_api_never_returns_secret_and_preview_is_free(client) -> None:
    secret = "minimax-secret-that-must-not-be-returned"
    saved = client.post(
        "/api/short-drama/providers/minimax-h3",
        json={"api_key": secret, "clear_api_key": False},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["has_api_key"] is True
    assert saved.json()["base_url"] == "https://api.acedata.cloud"
    assert secret not in saved.text
    fetched = client.get("/api/short-drama/providers/minimax-h3")
    assert fetched.status_code == 200
    assert secret not in fetched.text

    project = client.post("/api/short-drama/fixtures/dont-trust-me").json()
    project_id = project["document"]["project"]["id"]
    shot_id = project["document"]["shots"][0]["id"]
    preview = client.post(
        f"/api/short-drama/projects/{project_id}/h3/preview",
        json={"shot_id": shot_id, "route": "DIRECT", "media_inputs": []},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["request"]["resolution"] == "768P"
    assert preview.json()["request"]["ratio"] == "9:16"
    assert secret not in preview.text
