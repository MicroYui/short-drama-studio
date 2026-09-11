"""Offline contract and lifecycle coverage for the MiniMax-H3 adapter."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
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
    H3ReconciliationCandidatesResponse,
    H3RemoteStatus,
    H3RemoteTask,
    H3TakeReviewRequest,
    H3TaskContent,
    H3TaskUsage,
    H3UploadedInput,
    H3VideoRequest,
    derive_h3_reference_alias,
)
from short_drama.image_models import (
    AssetImportLocalRequest,
    AssetReviewPurpose,
    AssetReviewRequest,
)
from short_drama.models import (
    ActionBeat,
    AssetKind,
    CreateProjectRequest,
    DirectorRepairDecision,
    DirectorRepairStrategy,
    EvidencePurpose,
    EvidenceStatus,
    Freshness,
    ProjectStage,
    QaFailureCategory,
    QaIssue,
    QaStatus,
    ReferenceRequirement,
    ReferenceRole,
    ShotPlan,
    ShotReferenceCue,
    ShotTimelineBeat,
    SpatialInteractionConstraint,
    SourceMode,
    SurfaceAttachmentConstraint,
    TaskStatus,
    VideoGenerationRoute,
)
from short_drama.production_planner import (
    build_project_document_from_plan,
    create_production_plan,
)
from short_drama.reference_planner import (
    DirectorRepairDecisionRequest,
    ReferencePackEntryCreateRequest,
    ShotPlanPatch,
)
from short_drama.providers.minimax_h3_client import H3SubmissionUnknown
from short_drama.providers.h3_media_bindings import resolve_h3_media_bindings
from short_drama.providers.minimax_h3_compiler import (
    H3CapabilityError,
    build_h3_prompt,
    compile_h3_request,
)
from short_drama.service import (
    ArtifactNotFoundError,
    ShortDramaService,
    WorkflowConflictError,
)
from short_drama.tools import ToolCallStatus


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


class ConcurrentSubmitH3Client(FakeH3Client):
    """Makes an unguarded duplicate submission race deterministic."""

    def __init__(self) -> None:
        super().__init__()
        self.submit_barrier = threading.Barrier(2)
        self.submit_record_lock = threading.Lock()

    def submit(self, request: H3VideoRequest) -> str:
        try:
            self.submit_barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        with self.submit_record_lock:
            return super().submit(request)


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
    assert "Do not introduce undeclared characters, props" in dialogue_prompt
    assert "红色录音笔" in prop_prompt
    assert "the opening state is 道具从周衡右手进入" in prop_prompt


def test_h3_prompt_omits_hidden_prop_state_from_generation_projection() -> None:
    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    shot = next(item for item in document.shots if item.sequence == 4)
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    lin = next(
        item
        for item in document.assets
        if item.kind == AssetKind.CHARACTER and "林夏" in item.name
    )
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    plan.requirements = [
        ReferenceRequirement(
            id="lin-visible",
            subject_asset_id=lin.id,
            acceptable_roles=[ReferenceRole.CHARACTER_FACE],
            purpose="The lead is visible.",
        ),
        ReferenceRequirement(
            id="location-visible",
            subject_asset_id=location.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="The location is visible.",
        ),
    ]
    shot.continuity.prop_state_in = "录音笔在画外且灯灭"
    shot.continuity.prop_state_out = "录音笔仍未放下"
    shot.continuity.action_out = "周衡准备放下录音笔"
    shot.continuity.eyeline = "林夏直视周衡，不看录音笔"

    prompt = build_h3_prompt(document, shot)

    assert "录音笔" not in prompt
    assert "Show only the visible subjects declared by the shot reference plan" in prompt


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

    assert "Perform the declared action beats in order" in prompt
    assert "Maintain that contact through the shot" in prompt
    assert (
        "entrance.primary_door.handle.exterior remains rigidly attached to "
        "entrance.primary_door.face.exterior"
    ) in prompt
    assert "Parts never disappear, switch faces, duplicate, detach, or drift" in prompt


def test_h3_prompt_skill_emits_official_mode_schemas_and_dialogue_markup() -> None:
    document = build_dont_trust_me_fixture("2026-08-15T00:00:00+00:00")
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    bible.style.medium = "纸雕与水墨混合视觉"
    bible.style.cinematography = "平面构图按叙事关系变化"
    bible.style.color = "低饱和墨色与单一强调色"
    bible.style.texture = "纸张纤维与墨色层次"
    dialogue_shot = next(shot for shot in document.shots if shot.sequence == 2)
    voiceover_shot = next(shot for shot in document.shots if shot.sequence == 5)
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
        source=H3MediaSource(external_url="https://example.invalid/reference.png"),
    )

    direct = build_h3_prompt(
        document,
        dialogue_shot,
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=8,
    )
    frame = build_h3_prompt(
        document,
        dialogue_shot,
        route=VideoGenerationRoute.FRAME_FIRST,
        duration_seconds=8,
        media_inputs=[first],
    )
    last_frame = build_h3_prompt(
        document,
        dialogue_shot,
        route=VideoGenerationRoute.FRAME_LAST,
        duration_seconds=8,
        media_inputs=[last],
    )
    first_last = build_h3_prompt(
        document,
        dialogue_shot,
        route=VideoGenerationRoute.FRAME_FIRST_LAST,
        duration_seconds=8,
        media_inputs=[first, last],
    )
    referenced = build_h3_prompt(
        document,
        dialogue_shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        media_inputs=[reference],
    )
    voiceover = build_h3_prompt(
        document,
        voiceover_shot,
        route=VideoGenerationRoute.FRAME_FIRST,
        duration_seconds=8,
        media_inputs=[first],
    )
    keyframe_bindings = resolve_h3_media_bindings(document, [first, last], [])

    assert direct.startswith("integrated_multimodal_description:")
    assert "medium=纸雕与水墨混合视觉" in direct
    assert "texture=纸张纤维与墨色层次" in direct
    assert "Live-action and cinematic" not in direct
    assert "(S1) says in Mandarin Chinese: <d>[Chinese]" in direct
    assert frame.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    assert [item.prompt_label for item in keyframe_bindings] == [
        "<Picture 1>",
        "<Picture 2>",
    ]
    assert last_frame.startswith(
        "How the reference pictures align with the target video — "
        "<Picture 1> (from [Shot 1]) aligns with the 8.00-second mark"
    )
    assert "only the final frame" in last_frame
    assert "must not be treated as the opening image" in last_frame
    assert first_last.startswith(
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark"
    )
    assert "continuous observable path" not in first_last
    assert "temporal behavior declared by the ShotPlan" in first_last
    fields = [
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    ]
    assert [referenced.index(field) for field in fields] == sorted(
        referenced.index(field) for field in fields
    )
    assert "<Subject 1>" in referenced
    assert "extracted from its reference images" in referenced
    assert "<Picture 1> provides" in referenced
    assert "medium=纸雕与水墨混合视觉" in referenced
    assert "says in an off-screen voiceover" in voiceover
    assert "lips remain completely closed" in voiceover


def test_shot_timeline_beats_require_contiguous_ranges_and_preserve_dialogue() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    source = next(shot for shot in document.shots if shot.sequence == 2)
    line_id = source.dialogue_line_ids[0]
    base_payload = source.model_dump(mode="python")
    base_payload["timeline_beats"] = [
        ShotTimelineBeat(
            start_seconds=0.0,
            end_seconds=3.0,
            visual="林夏稳定地看向周衡",
            framing="近景",
            camera="固定机位",
            action="林夏抬眼确认对方反应",
            performance="语气克制",
            emotion="警觉",
            dialogue_line_ids=[line_id],
            sound="便利店环境音",
        ),
        ShotTimelineBeat(
            start_seconds=3.0,
            end_seconds=8.0,
            visual="林夏说完后保持视线",
            framing="近景",
            camera="末段轻微推近",
            action="林夏停住动作等待回应",
            performance="呼吸稳定",
            emotion="压迫感上升",
        ),
    ]

    validated = ShotPlan.model_validate(base_payload)
    assert validated.timeline_beats[-1].end_seconds == source.duration_seconds

    spanning_dialogue_payload = validated.model_dump(mode="python")
    spanning_dialogue_payload["timeline_beats"][1]["dialogue_line_ids"] = [line_id]
    spanning_dialogue = ShotPlan.model_validate(spanning_dialogue_payload)
    assert [
        beat.dialogue_line_ids for beat in spanning_dialogue.timeline_beats
    ] == [[line_id], [line_id]]

    gap_payload = validated.model_dump(mode="python")
    gap_payload["timeline_beats"][1]["start_seconds"] = 3.2
    with pytest.raises(ValueError, match="contiguous and ordered"):
        ShotPlan.model_validate(gap_payload)

    missing_dialogue_payload = validated.model_dump(mode="python")
    missing_dialogue_payload["timeline_beats"][0]["dialogue_line_ids"] = []
    with pytest.raises(ValueError, match="cover shot dialogue lines in order"):
        ShotPlan.model_validate(missing_dialogue_payload)


def test_h3_reference_prompt_emits_timed_beats_and_normalized_semantic_alias() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    source = next(shot for shot in document.shots if shot.sequence == 2)
    character_asset = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    line_id = source.dialogue_line_ids[0]
    shot_payload = source.model_dump(mode="python")
    shot_payload["timeline_beats"] = [
        ShotTimelineBeat(
            start_seconds=0.0,
            end_seconds=4.0,
            visual="林夏侧身站在柜台前",
            framing="中近景",
            camera="缓慢向前推近",
            action="林夏抬眼看向周衡",
            performance="语气平静但目光专注",
            emotion="克制的警觉",
            dialogue_line_ids=[line_id],
            sound="雨声与便利店底噪",
            references=[
                ShotReferenceCue(
                    subject_asset_id=character_asset.id,
                    alias="林夏定妆",
                    purpose="锁定林夏的身份和服饰",
                )
            ],
        ),
        ShotTimelineBeat(
            start_seconds=4.0,
            end_seconds=8.0,
            visual="林夏说完后观察周衡的反应",
            framing="近景",
            camera="焦点保持在林夏眼神",
            action="林夏保持姿态不动",
            performance="台词结束后留出反应空间",
            emotion="对峙感持续",
            references=[
                ShotReferenceCue(
                    subject_asset_id=character_asset.id,
                    alias="林夏定妆",
                    purpose="继续锁定林夏的身份和服饰",
                )
            ],
        ),
    ]
    shot = ShotPlan.model_validate(shot_payload)
    media = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(
            external_url="https://example.invalid/linxia-reference.png"
        ),
        asset_version_id=character_asset.versions[0].id,
        subject_asset_id=character_asset.id,
        reference_alias="＠ 林夏 / 定妆 ",
        binding_purpose="锁定林夏的身份、发型和服饰",
    )

    prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=8,
        media_inputs=[media],
    )

    assert media.reference_alias == "林夏_定妆"
    assert "@林夏_定妆 = <Subject 1>" in prompt
    assert "<Subject 1> is the reusable visible content" in prompt
    assert "extracted from its reference images" in prompt
    assert "<Picture 1> provides" in prompt
    assert "[0.00-4.00 seconds]" in prompt
    assert "[4.00-8.00 seconds]" in prompt
    assert "@林夏_定妆 (<Subject 1>)" in prompt


def test_h3_reference_prompt_rejects_alias_collisions_after_normalization() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    shot = next(shot for shot in document.shots if shot.sequence == 2)
    first_asset, second_asset = document.assets[:2]
    media_inputs = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url="https://example.invalid/first.png"),
            asset_version_id=first_asset.versions[0].id,
            subject_asset_id=first_asset.id,
            reference_alias="食堂 / 窗口",
        ),
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url="https://example.invalid/second.png"),
            asset_version_id=second_asset.versions[0].id,
            subject_asset_id=second_asset.id,
            reference_alias="食堂 窗口",
        ),
    ]

    with pytest.raises(ValueError, match="identifies multiple media inputs"):
        build_h3_prompt(
            document,
            shot,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=8,
            media_inputs=media_inputs,
        )


def test_generated_h3_aliases_truncate_after_nfkc_normalization() -> None:
    alias = derive_h3_reference_alias("ﬃ" * 80)

    assert len(alias) == 80
    assert alias.startswith("ffiffi")


def test_shot_reference_cues_normalize_readable_aliases_and_reject_punctuation() -> None:
    cue = ShotReferenceCue(
        subject_asset_id="asset-cafeteria-window",
        alias="食堂 / 窗口",
        purpose="锁定打饭窗口空间",
    )
    assert cue.alias == "食堂_窗口"

    with pytest.raises(ValueError, match="readable name"):
        ShotReferenceCue(
            subject_asset_id="asset-invalid",
            alias="///",
            purpose="无效别名",
        )


def test_legacy_h3_bindings_reject_repeated_final_alias_and_identity() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    shot = document.shots[0]
    asset = document.assets[0]
    media_inputs = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(external_url=source_url),
            asset_version_id=version_id,
            subject_asset_id=asset.id,
        )
        for source_url, version_id in [
            ("https://example.invalid/1.png", asset.versions[0].id),
            ("https://example.invalid/2.png", "legacy-version-2"),
            ("https://example.invalid/2.png", "legacy-version-2"),
        ]
    ]

    with pytest.raises(ValueError, match="duplicates semantic binding"):
        build_h3_prompt(
            document,
            shot,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=5,
            media_inputs=media_inputs,
        )


def test_legacy_h3_bindings_keep_distinct_naked_urls_separate() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    shot = document.shots[0]
    media_inputs = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(
                external_url=f"https://example.invalid/legacy-{index}.png"
            ),
        )
        for index in range(1, 3)
    ]

    prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=5,
        media_inputs=media_inputs,
    )
    bindings = resolve_h3_media_bindings(document, media_inputs, [])

    assert len(bindings) == 2
    assert bindings[0].alias != bindings[1].alias
    assert bindings[0].provider_label == "<Picture 1>"
    assert bindings[1].provider_label == "<Picture 2>"
    assert bindings[0].prompt_label == "<Subject 1>"
    assert bindings[1].prompt_label == "<Subject 2>"
    assert f"@{bindings[0].alias} = <Subject 1>" in prompt
    assert f"@{bindings[1].alias} = <Subject 2>" in prompt
    assert "<Subject 1> is the reusable visible content" in prompt
    assert "<Picture 1> provides" in prompt
    assert "<Picture 2> provides" in prompt


def test_reference_views_for_one_explicit_subject_share_one_subject_label() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    shot = document.shots[0]
    character_asset = next(
        asset for asset in document.assets if asset.kind == AssetKind.CHARACTER
    )
    location_asset = next(
        asset for asset in document.assets if asset.kind == AssetKind.LOCATION
    )
    media_inputs = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(
                external_url=f"https://example.invalid/character-{view}.png"
            ),
            subject_asset_id=character_asset.id,
            reference_alias=f"林夏_{view}",
            binding_purpose=f"锁定林夏的{view}视图",
            reference_tags=[view],
        )
        for view in ("front", "profile", "back")
    ]
    media_inputs.append(
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(
                external_url="https://example.invalid/location-master.png"
            ),
            subject_asset_id=location_asset.id,
            reference_alias="场景_主视图",
            binding_purpose="锁定场景结构",
        )
    )

    bindings = resolve_h3_media_bindings(document, media_inputs, [])
    prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=5,
        media_inputs=media_inputs,
    )
    definitions = prompt.split("\n\nsummary:", maxsplit=1)[0]

    assert [item.provider_label for item in bindings] == [
        "<Picture 1>",
        "<Picture 2>",
        "<Picture 3>",
        "<Picture 4>",
    ]
    assert [item.prompt_label for item in bindings] == [
        "<Subject 1>",
        "<Subject 1>",
        "<Subject 1>",
        "<Subject 2>",
    ]
    assert definitions.count("<Subject 1> is the reusable visible content") == 1
    assert definitions.count("<Subject 2> is the reusable visible content") == 1
    assert all(
        f"<Picture {index}> provides" in definitions for index in range(1, 5)
    )
    assert all(
        f"reference tags: {view}" in definitions
        for view in ("front", "profile", "back")
    )
    assert "<Subject 3>" not in prompt


def test_reference_images_resolved_to_one_asset_share_one_subject_label() -> None:
    document = build_dont_trust_me_fixture("2026-09-02T00:00:00+00:00")
    asset = next(
        item for item in document.assets if item.kind == AssetKind.CHARACTER
    )
    first_version = asset.versions[0]
    second_version = first_version.model_copy(
        update={"id": "asset-version-shared-subject-2", "version": 2}
    )
    asset.versions.append(second_version)
    media_inputs = [
        H3MediaInput(
            kind=H3MediaKind.IMAGE,
            role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(
                external_url=f"https://example.invalid/resolved-view-{index}.png"
            ),
            asset_version_id=version.id,
        )
        for index, version in enumerate((first_version, second_version), start=1)
    ]

    bindings = resolve_h3_media_bindings(document, media_inputs, [])

    assert [item.prompt_label for item in bindings] == [
        "<Subject 1>",
        "<Subject 1>",
    ]
    assert bindings[0].alias == bindings[1].alias


def test_empty_h3_reference_metadata_preserves_legacy_request_fingerprint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeH3Client())
    project = service.initialize_fixture()
    document = project.document
    shot = document.shots[0]
    source_url = "https://example.invalid/legacy-reference.png"
    media = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url=source_url),
    )

    task, _run, _evidence = service._new_h3_task_and_run(
        document=document,
        shot_id=shot.id,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=5,
        prompt="legacy-compatible prompt",
        media_inputs=[media],
        purpose=EvidencePurpose.CONTRACT_SMOKE,
        learning_objective=None,
        regression_case_id=None,
        candidate_index=1,
        attempt=1,
        retry_of_task_id=None,
    )
    legacy_request = {
        "provider": "minimax",
        "model": "MiniMax-H3",
        "project_id": document.project.id,
        "shot_id": shot.id,
        "route": VideoGenerationRoute.REFERENCE.value,
        "resolution": "768P",
        "duration": 5,
        "prompt": "legacy-compatible prompt",
        "media_inputs": [
            {
                "kind": H3MediaKind.IMAGE.value,
                "role": H3MediaRole.REFERENCE_IMAGE.value,
                "source": {"external_url": source_url},
            }
        ],
        "candidate_index": 1,
        "script_version_id": document.project.current_script_version_id,
        "bible_version_id": document.project.current_bible_version_id,
        "shot_revision": shot.revision,
        "voice_reference_revision": document.project.voice_reference_revision,
        "learning_objective": None,
        "regression_case_id": None,
        "attempt": 1,
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert task.request_fingerprint == expected


def test_h3_compiler_omits_unresolved_scaffold_style() -> None:
    request = CreateProjectRequest(
        name="未定视觉方向",
        source_mode=SourceMode.AUTO_ORIGINAL,
        input_text="用户只提供了一个内容想法",
        target_duration_seconds=12,
    )
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-unresolved-style",
        request=request,
        planning=planning,
        created_at="2026-09-01T00:00:00Z",
    )

    prompt = build_h3_prompt(
        document,
        document.shots[0],
        route=VideoGenerationRoute.DIRECT,
        duration_seconds=12,
    )

    assert "未指定" not in prompt
    assert "Follow the project's declared style" not in prompt


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

    final_frame = compile_h3_request(
        route=VideoGenerationRoute.FRAME_LAST,
        prompt="尾帧驱动",
        duration_seconds=5,
        media_inputs=[last],
        resolve_url=lambda item: item.source.external_url or "unused",
    )
    assert final_frame.ratio is None
    assert final_frame.content[1].role == H3MediaRole.LAST_FRAME

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
    with pytest.raises(H3CapabilityError, match="FRAME_LAST"):
        compile_h3_request(
            route=VideoGenerationRoute.FRAME_LAST,
            prompt="尾帧路线不能接收首帧",
            duration_seconds=5,
            media_inputs=[first],
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
    pending = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(shot_id=shot.id),
    )

    assert service.advance_task(probe.task.id).status == TaskStatus.ACKNOWLEDGED
    fake.set_status("remote-1", H3RemoteStatus.RUNNING)
    assert service.advance_task(probe.task.id).status == TaskStatus.RUNNING
    fake.set_status("remote-1", H3RemoteStatus.SUCCEEDED)
    completed = service.tool_registry.invoke("task.advance", {"task_id": probe.task.id})
    assert completed.status == ToolCallStatus.SUCCEEDED
    assert completed.output is not None and completed.output["status"] == TaskStatus.SUCCEEDED.value

    aggregate = service.get_project(project_id)
    assert next(task for task in aggregate.tasks if task.id == pending.task.id).status == (
        TaskStatus.QUEUED
    )
    assert aggregate.document.project.stage == ProjectStage.GENERATING
    take = next(item for item in aggregate.takes if item.task_id == probe.task.id)
    assert take.qa_status == QaStatus.PENDING
    assert take.is_selected is False
    assert any(ref.artifact_type == "take" and ref.artifact_id == take.id for ref in completed.artifact_refs)
    persisted_output = service.tool_registry.invoke("task.get", {"task_id": probe.task.id})
    assert persisted_output.status == ToolCallStatus.SUCCEEDED
    assert persisted_output.output is not None
    assert persisted_output.output["takes"] == [take.model_dump(mode="json")]
    assert any(ref.artifact_id == take.id for ref in persisted_output.artifact_refs)
    assert len(fake.submitted_requests) == 1
    assert take.width == 768
    assert take.height == 1366
    evidence = next(item for item in aggregate.route_evidence if item.task_id == probe.task.id)
    assert evidence.technical_passed is True
    assert evidence.status == EvidenceStatus.AWAITING_REVIEW
    run = service.repository.get_provider_run(probe.task.id)
    assert run is not None
    assert run.estimated_cost_usd == 0.4
    assert run.estimated_cost_credits == 3.003541
    assert run.downloaded_path == take.media_path

    evidence.purpose = EvidencePurpose.CONTRACT_SMOKE
    service.repository.save_route_evidence(evidence)
    selected_without_review = service.select_take(take.id)
    selected_candidate = next(item for item in selected_without_review.takes if item.id == take.id)
    assert selected_candidate.is_selected is True
    assert selected_candidate.qa_status == QaStatus.PENDING
    evidence.purpose = EvidencePurpose.QUALITY_PROBE
    service.repository.save_route_evidence(evidence)

    reviewed = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=True,
            score=4.6,
            notes="身份、道具、动作、台词、声线和口型人工检查通过。",
        ),
    )
    assert reviewed.take.qa_status == QaStatus.PASSED
    assert reviewed.take.is_selected is True
    assert reviewed.evidence.status == EvidenceStatus.REVIEWED
    assert reviewed.project.document.project.stage == ProjectStage.GENERATING
    first_rejection = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=False,
            score=2.8,
            notes="The first semantic review found a composition issue.",
            issues=[
                QaIssue(
                    code="OLD_COMPOSITION_ISSUE",
                    severity="HIGH",
                    message="This issue is superseded by the next semantic review.",
                    repair_layer="SHOT_COMPOSITION",
                    category=QaFailureCategory.COMPOSITION_QUALITY,
                )
            ],
        ),
    )
    assert [item.code for item in first_rejection.take.qa_report.issues] == [
        "OLD_COMPOSITION_ISSUE"
    ]
    second_rejection = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=False,
            score=3.1,
            notes="The current semantic review replaces the superseded issue list.",
            issues=[
                QaIssue(
                    code="CURRENT_FRAMING_ISSUE",
                    severity="MEDIUM",
                    message="Only the current framing deviation remains visible.",
                    repair_layer="SHOT_COMPOSITION",
                    category=QaFailureCategory.COMPOSITION_QUALITY,
                )
            ],
        ),
    )
    assert [item.code for item in second_rejection.take.qa_report.issues] == [
        "CURRENT_FRAMING_ISSUE"
    ]
    reviewed = service.review_h3_take(
        take.id,
        H3TakeReviewRequest(
            hard_gate_passed=True,
            score=4.6,
            notes="The latest review passes and clears superseded semantic issues.",
        ),
    )
    assert reviewed.take.qa_report.issues == []
    persisted_evidence = service.repository.get_route_evidence_for_take(take.id)
    assert persisted_evidence is not None
    assert persisted_evidence.purpose == EvidencePurpose.QUALITY_PROBE
    persisted_evidence.purpose = EvidencePurpose.CONTRACT_SMOKE
    service.repository.save_route_evidence(persisted_evidence)
    selected = service.select_take(take.id)
    assert next(item for item in selected.takes if item.id == take.id).is_selected is True
    assert selected.document.project.stage == ProjectStage.GENERATING
    evidence_after_selection = service.repository.get_route_evidence_for_take(take.id)
    assert evidence_after_selection is not None
    assert evidence_after_selection.purpose == EvidencePurpose.CONTRACT_SMOKE


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
    shot.timeline_beats = [
        ShotTimelineBeat(
            start_seconds=0.0,
            end_seconds=shot.duration_seconds,
            visual=shot.visual,
            framing=shot.framing,
            camera=shot.camera,
            action=shot.continuity.action_out,
            dialogue_line_ids=list(shot.dialogue_line_ids),
            sound=shot.audio_intent,
        )
    ]
    service.repository.save_project(project.document)
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
            shot_patch=ShotPlanPatch(
                camera="rear coverage",
                continuity=shot.continuity.model_copy(
                    update={"eyeline": "rear coverage eyeline"}
                ),
            ),
        ),
    )
    current_shot = next(item for item in updated.document.shots if item.id == shot.id)
    assert current_shot.revision == 2
    assert current_shot.continuity.eyeline == "rear coverage eyeline"
    assert current_shot.timeline_beats == []

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


def test_director_repair_rejects_invalid_timeline_beats_atomically(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeH3Client())
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = next(item for item in project.document.shots if item.sequence == 2)
    original_revision = shot.revision

    invalid_beat = ShotTimelineBeat(
        start_seconds=0.0,
        end_seconds=shot.duration_seconds - 1.0,
        visual=shot.visual,
        framing=shot.framing,
        camera=shot.camera,
        action=shot.continuity.action_out,
        dialogue_line_ids=list(shot.dialogue_line_ids),
        sound=shot.audio_intent,
    )
    with pytest.raises(WorkflowConflictError, match="Invalid ShotPlan repair"):
        service.decide_director_repair(
            project_id,
            shot.id,
            DirectorRepairDecisionRequest(
                decision=DirectorRepairDecision.APPLIED,
                strategy=DirectorRepairStrategy.CHANGE_KEYFRAME,
                reason="Attempt an invalid partial timeline update.",
                shot_patch=ShotPlanPatch(timeline_beats=[invalid_beat]),
            ),
        )

    persisted = service.get_project(project_id).document
    persisted_shot = next(item for item in persisted.shots if item.id == shot.id)
    assert persisted_shot.revision == original_revision
    assert persisted_shot.timeline_beats == []


def test_h3_prepare_submit_and_retry_reject_stale_inputs(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    shot = project.document.shots[0]
    queued_probe = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(shot_id=shot.id),
    )
    submitting_probe = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(shot_id=shot.id),
    )
    assert service.advance_task(submitting_probe.task.id).status == TaskStatus.SUBMITTING

    updated = service.decide_director_repair(
        project_id,
        shot.id,
        DirectorRepairDecisionRequest(
            decision=DirectorRepairDecision.APPLIED,
            strategy=DirectorRepairStrategy.CHANGE_CAMERA,
            reason="Change the shot before either queued request reaches the provider.",
            shot_patch=ShotPlanPatch(camera="locked-off profile coverage"),
        ),
    )
    current_shot = next(item for item in updated.document.shots if item.id == shot.id)
    assert current_shot.revision == shot.revision + 1

    blocked_during_prepare = service.advance_task(queued_probe.task.id)
    blocked_during_submit = service.advance_task(submitting_probe.task.id)

    assert blocked_during_prepare.status == TaskStatus.FAILED
    assert blocked_during_prepare.error_code == "H3_INPUTS_STALE"
    assert blocked_during_submit.status == TaskStatus.FAILED
    assert blocked_during_submit.error_code == "H3_INPUTS_STALE"
    assert fake.submitted_requests == []
    assert service.repository.provider_submission_count(
        blocked_during_submit.request_fingerprint
    ) == 0
    for blocked in (blocked_during_prepare, blocked_during_submit):
        task_count = len(service.repository.list_tasks(project_id))
        with pytest.raises(WorkflowConflictError, match="compile a new H3"):
            service.retry_task(blocked.id)
        assert len(service.repository.list_tasks(project_id)) == task_count


def test_h3_worker_prepares_current_subject_supplemental_reference(
    tmp_path: Path,
) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    subject = next(
        asset for asset in project.document.assets if asset.kind == AssetKind.CHARACTER
    )

    def import_image(name: str, color: str) -> str:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (768, 1344), color).save(path)
        return service.import_local_asset_version(
            project_id,
            subject.id,
            AssetImportLocalRequest(local_path=str(path), descriptor=name),
        ).version.id

    canonical_id = import_image("worker-canonical", "#d6d3d1")
    service.review_asset_version(
        project_id,
        subject.id,
        canonical_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.CANONICAL_MASTER,
            notes="worker canonical",
        ),
    )
    supplemental_id = import_image("worker-profile", "#bfdbfe")
    service.review_asset_version(
        project_id,
        subject.id,
        supplemental_id,
        AssetReviewRequest(
            decision="APPROVED",
            purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE,
            notes="worker profile reference",
        ),
    )
    service.add_reference_pack_entry(
        project_id,
        ReferencePackEntryCreateRequest(
            subject_asset_id=subject.id,
            asset_version_id=supplemental_id,
            lineage_root_version_id=canonical_id,
            role=ReferenceRole.CHARACTER_PROFILE,
            description="current character profile",
        ),
    )
    document = service.get_project(project_id).document
    shot = document.shots[0]
    media = H3MediaInput(
        kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE,
        source=H3MediaSource(external_url="https://ignored.example/profile.png"),
        asset_version_id=supplemental_id,
        subject_asset_id=subject.id,
        reference_alias="主角侧脸",
        binding_purpose="锁定主角侧脸身份",
    )
    prompt = build_h3_prompt(
        document,
        shot,
        route=VideoGenerationRoute.REFERENCE,
        duration_seconds=5,
        media_inputs=[media],
    )
    probe = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(
            shot_id=shot.id,
            route=VideoGenerationRoute.REFERENCE,
            duration_seconds=5,
            prompt_override=prompt,
            media_inputs=[media],
        ),
    )

    prepared = service.advance_task(probe.task.id)

    assert prepared.status == TaskStatus.SUBMITTING
    assert prepared.error_code is None
    assert len(fake.upload_calls) == 1


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
    scoped_candidates = service.list_h3_reconciliation_candidates_for_task(
        project.document.project.id,
        probe.task.id,
    )
    assert [item.id for item in scoped_candidates.tasks] == ["remote-1"]

    resolved = service.resolve_h3_submission(probe.task.id, "remote-1")
    assert resolved.task.status == TaskStatus.ACKNOWLEDGED
    assert resolved.task.provider_task_id == "remote-1"
    assert service.repository.provider_submission_count(
        resolved.task.request_fingerprint
    ) == 1
    assert len(fake.submitted_requests) == 1
    assert resolved.project.route_evidence[0].status == EvidenceStatus.AWAITING_OUTPUT


def test_h3_reconciliation_tool_is_scoped_to_one_compatible_unknown_task(
    tmp_path: Path,
) -> None:
    fake = FakeH3Client()
    fake.submit_unknown_once = True
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    project_id = project.document.project.id
    probe = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(
            shot_id=project.document.shots[0].id,
            duration_seconds=6,
        ),
    )
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMIT_UNKNOWN

    compatible = H3RemoteTask(
        id="compatible-template",
        status=H3RemoteStatus.QUEUED,
        resolution="768P",
        duration=6,
        ratio="9:16",
        task_type="generation",
        modality="video",
    )
    fake.remote_tasks["wrong-model"] = compatible.model_copy(
        update={"id": "wrong-model", "model": "Another-Model"}
    )
    fake.remote_tasks["wrong-resolution"] = compatible.model_copy(
        update={"id": "wrong-resolution", "resolution": "1080P"}
    )
    fake.remote_tasks["wrong-duration"] = compatible.model_copy(
        update={"id": "wrong-duration", "duration": 10}
    )
    fake.remote_tasks["wrong-ratio"] = compatible.model_copy(
        update={"id": "wrong-ratio", "ratio": "16:9"}
    )
    fake.remote_tasks["wrong-task-type"] = compatible.model_copy(
        update={"id": "wrong-task-type", "task_type": "upscale"}
    )
    fake.remote_tasks["wrong-modality"] = compatible.model_copy(
        update={"id": "wrong-modality", "modality": "image"}
    )
    fake.remote_tasks["compatible-unknown-fields"] = H3RemoteTask(
        id="compatible-unknown-fields",
        status=H3RemoteStatus.QUEUED,
    )

    candidates = service.list_h3_reconciliation_candidates_for_task(
        project_id,
        probe.task.id,
    )
    assert [item.id for item in candidates.tasks] == [
        "remote-1",
        "compatible-unknown-fields",
    ]

    result = service.tool_registry.invoke(
        "route.list_h3_reconciliation",
        {"project_id": project_id, "task_id": probe.task.id},
    )
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    tool_candidates = H3ReconciliationCandidatesResponse.model_validate(result.output)
    assert [item.id for item in tool_candidates.tasks] == [
        "remote-1",
        "compatible-unknown-fields",
    ]
    assert [
        (ref.project_id, ref.artifact_type, ref.artifact_id)
        for ref in result.artifact_refs
    ] == [(project_id, "generation_task", probe.task.id)]

    other_project = service.create_project(
        CreateProjectRequest(
            name="Other reconciliation project",
            source_mode=SourceMode.IDEA_EXPANSION,
            input_text="Another project must not inspect this task through its Agent Tool.",
        )
    ).document.project
    with pytest.raises(ArtifactNotFoundError):
        service.list_h3_reconciliation_candidates_for_task(
            other_project.id,
            probe.task.id,
        )

    queued = service.enqueue_h3_probe(
        project_id,
        H3ProbeRequest(shot_id=project.document.shots[0].id),
    )
    with pytest.raises(WorkflowConflictError, match="not waiting for reconciliation"):
        service.list_h3_reconciliation_candidates_for_task(project_id, queued.task.id)

    with pytest.raises(WorkflowConflictError, match="different aspect ratio"):
        service.resolve_h3_submission(probe.task.id, "wrong-ratio")


def test_h3_duplicate_local_task_reuses_acknowledged_submission_before_post(
    tmp_path: Path,
) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(
        project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id),
    )
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    acknowledged = service.advance_task(probe.task.id)
    assert acknowledged.status == TaskStatus.ACKNOWLEDGED
    assert acknowledged.provider_task_id == "remote-1"
    assert len(fake.submitted_requests) == 1

    original_run = service.repository.get_provider_run(probe.task.id)
    original_evidence = service.repository.get_route_evidence_for_task(probe.task.id)
    assert original_run is not None
    assert original_evidence is not None
    duplicate_task = probe.task.model_copy(
        deep=True,
        update={
            "id": "task-local-duplicate",
            "provider_run_id": "h3-run-local-duplicate",
            "status": TaskStatus.QUEUED,
            "provider_task_id": None,
            "submitted_at": None,
            "started_at": None,
            "finished_at": None,
        },
    )
    duplicate_run = original_run.model_copy(
        deep=True,
        update={"id": "h3-run-local-duplicate", "task_id": duplicate_task.id},
    )
    duplicate_evidence = original_evidence.model_copy(
        deep=True,
        update={
            "id": "evidence-local-duplicate",
            "task_id": duplicate_task.id,
            "status": EvidenceStatus.AWAITING_OUTPUT,
        },
    )
    service.repository.save_task(duplicate_task)
    service.repository.save_provider_run(duplicate_run)
    service.repository.save_route_evidence(duplicate_evidence)

    assert service.advance_task(duplicate_task.id).status == TaskStatus.SUBMITTING
    reused = service.advance_task(duplicate_task.id)

    assert reused.status == TaskStatus.ACKNOWLEDGED
    assert reused.provider_task_id == acknowledged.provider_task_id
    assert len(fake.submitted_requests) == 1


def test_concurrent_h3_advance_submits_once_per_request_fingerprint(
    tmp_path: Path,
) -> None:
    fake = ConcurrentSubmitH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(
        project.document.project.id,
        H3ProbeRequest(shot_id=project.document.shots[0].id),
    )
    original_run = service.repository.get_provider_run(probe.task.id)
    original_evidence = service.repository.get_route_evidence_for_task(probe.task.id)
    assert original_run is not None
    assert original_evidence is not None
    duplicate_task = probe.task.model_copy(
        deep=True,
        update={
            "id": "task-concurrent-duplicate",
            "provider_run_id": "h3-run-concurrent-duplicate",
        },
    )
    duplicate_run = original_run.model_copy(
        deep=True,
        update={
            "id": "h3-run-concurrent-duplicate",
            "task_id": duplicate_task.id,
        },
    )
    duplicate_evidence = original_evidence.model_copy(
        deep=True,
        update={
            "id": "evidence-concurrent-duplicate",
            "task_id": duplicate_task.id,
        },
    )
    service.repository.save_task(duplicate_task)
    service.repository.save_provider_run(duplicate_run)
    service.repository.save_route_evidence(duplicate_evidence)
    assert duplicate_task.request_fingerprint == probe.task.request_fingerprint
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    assert service.advance_task(duplicate_task.id).status == TaskStatus.SUBMITTING

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.advance_task, task_id)
            for task_id in (probe.task.id, duplicate_task.id)
        ]
        results = [future.result() for future in futures]

    assert len(fake.submitted_requests) == 1
    assert all(result.status == TaskStatus.ACKNOWLEDGED for result in results)
    assert {result.provider_task_id for result in results} == {"remote-1"}
    assert service.repository.provider_submission_count(
        probe.task.request_fingerprint
    ) == 1


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


def test_h3_run_read_tool_returns_exact_saved_run_without_provider_io(tmp_path: Path) -> None:
    fake = FakeH3Client()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(
        project.document.project.id, H3ProbeRequest(shot_id=project.document.shots[0].id),
    )
    before = probe.provider_run.model_dump(mode="json")
    result = service.tool_registry.invoke("route.get_h3_run", {"task_id": probe.task.id})
    assert result.status.value == "SUCCEEDED"
    assert result.output == before
    assert {ref.artifact_id for ref in result.artifact_refs} == {probe.provider_run.id, probe.task.id}
    persisted = service.repository.get_provider_run(probe.task.id)
    assert persisted is not None and persisted.model_dump(mode="json") == before
    assert fake.submitted_requests == []
    assert service.tool_registry.describe("route.get_h3_run").effect.value == "READ"


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
    stat = image_path.stat()
    legacy_cache_key = hashlib.sha256(
        f"{image_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    ).hexdigest()
    service.repository.save_provider_upload(
        cache_key=legacy_cache_key,
        provider="minimax",
        record=H3UploadedInput(
            local_path=str(image_path.resolve()),
            provider_file_id="mm_file://obsolete-unadapted-input",
            media_kind=H3MediaKind.IMAGE,
            role=H3MediaRole.FIRST_FRAME,
            expires_at="2099-01-01T00:00:00+00:00",
        ),
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
