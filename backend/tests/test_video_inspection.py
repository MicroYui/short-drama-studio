"""Video observation tools and Director-Agent handoff."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    EnqueueRequest,
    GenerationUnitShotWindow,
    ReferenceBinding,
    VideoInspectionRequest,
)
from short_drama.reference_planner import DirectorAdviceRequest
from short_drama.service import ShortDramaService
from short_drama.video_inspection import VideoInspector, _semantic_reference_images


class FakeInspectionLanguageProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.generations: list[tuple[str, str, list[str] | None, int]] = []

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
        self.generations.append(
            (instructions, input_text, image_urls, max_output_tokens)
        )
        if not self.outputs:
            raise AssertionError("Unexpected language-provider call")
        return self.outputs.pop(0)


def _render_video(path: Path, duration_seconds: float) -> None:
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
            f"testsrc2=s=432x768:r=24:d={duration_seconds:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration_seconds:.3f}",
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


def test_adaptive_sampling_covers_a_long_shot_without_frame_by_frame_decode(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "long-shot.mp4"
    _render_video(video_path, 18.0)
    inspector = VideoInspector(tmp_path / "workspace")
    probe = inspector.probe_video(video_path)

    samples = inspector.sample_video(
        media_path=video_path,
        take_id="take-long",
        probe=probe,
        output_dir=tmp_path / "samples",
        max_frames=12,
    )

    assert len(samples) == 12
    assert samples[0].timestamp_seconds <= 0.1
    assert samples[-1].timestamp_seconds >= 17.0
    assert any("VISUAL_CHANGE" in sample.reasons for sample in samples)
    assert all(Path(sample.image_path).is_file() for sample in samples)


def test_semantic_review_includes_approved_bound_asset_references(tmp_path: Path) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    plan = document.reference_plan[0]
    asset = document.assets[0]
    image_path = tmp_path / "canonical-character.png"
    Image.new("RGB", (96, 160), (40, 60, 80)).save(image_path)
    approved = asset.versions[-1].model_copy(
        deep=True,
        update={
            "id": "approved-canonical-character",
            "media_type": AssetMediaType.IMAGE,
            "local_path": str(image_path),
            "approval_status": ApprovalStatus.APPROVED,
        },
    )
    asset.versions.append(approved)
    asset.approved_version_id = approved.id
    plan.bindings.append(
        ReferenceBinding(
            kind=AssetKind.CHARACTER,
            target_id=approved.id,
            purpose="canonical identity comparison",
            required=True,
        )
    )

    references = _semantic_reference_images(document, plan)

    assert len(references) == 1
    assert "CHARACTER canonical reference" in references[0][0]
    assert references[0][1].startswith("data:image/png;base64,")


def test_grouped_take_inspection_receives_full_group_windows_and_later_shot_references(tmp_path: Path) -> None:
    fake = FakeInspectionLanguageProvider(['{"observations":[],"issues":[],"summary":"Observed the complete grouped clip."}'])
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "workspace",
                                language_provider_factory=lambda _key, _url: fake)
    document = service.initialize_fixture().document
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1"))
    first, second = document.shots[:2]
    service.enqueue(document.project.id, EnqueueRequest(shot_ids=[first.id], candidate_count=1))
    take = service.drain(document.project.id, 50).project.takes[0]
    first.duration_seconds, second.duration_seconds = 4, 9
    second.visual = "SECOND_SHOT_ACTION_ONLY"
    late_asset = document.assets[-1]
    image = tmp_path / "late-reference.png"
    Image.new("RGB", (96, 96), "blue").save(image)
    version = late_asset.versions[-1].model_copy(update={
        "id": "approved-later-shot", "media_type": AssetMediaType.IMAGE,
        "local_path": str(image), "approval_status": ApprovalStatus.APPROVED,
    })
    late_asset.versions.append(version)
    late_asset.approved_version_id = version.id
    late_plan = next(item for item in document.reference_plan if item.shot_id == second.id)
    late_plan.bindings.append(ReferenceBinding(kind=late_asset.kind, target_id=version.id,
                                             purpose="Only the later shot uses this prop", required=True))
    service.repository.save_project(document)
    video = tmp_path / "group.mp4"
    _render_video(video, 13)
    take.media_path, take.duration_seconds, take.generation_unit_id = str(video), 13, "group-1"
    take.shot_windows = [
        GenerationUnitShotWindow(shot_id=first.id, shot_revision=first.revision,
                                 source_in_seconds=0, source_out_seconds=4, duration_seconds=4),
        GenerationUnitShotWindow(shot_id=second.id, shot_revision=second.revision,
                                 source_in_seconds=4, source_out_seconds=13, duration_seconds=9),
    ]
    service.repository.save_take(take)
    uninspected = service.tool_registry.invoke("video.get_take_inspection", {"take_id": take.id})
    assert uninspected.status.value == "SUCCEEDED" and uninspected.output["report"] is None
    report = service.inspect_take_video(take.id, VideoInspectionRequest(
        semantic_review=True, include_previous_boundary=False, max_frames=8,
    )).report
    assert report.semantic_status.value == "COMPLETE"
    instructions, text, images, _limit = fake.generations[0]
    payload = json.loads(text)
    assert payload["inspection_target"]["scope"] == "GENERATION_UNIT"
    assert payload["inspection_target"]["requested_duration_seconds"] == 13
    assert payload["current_shot"] is None
    assert [item["id"] for item in payload["current_shots"]] == [first.id, second.id]
    assert "SECOND_SHOT_ACTION_ONLY" in text and "approved-later-shot" in text
    assert "不能拿第一个编辑镜头的时长判断整组超长" in instructions
    assert images is not None and len(images) >= 2

    newer = report.model_copy(update={
        "id": "newer-inspection", "summary": "Later saved observation.", "created_at": "2099-01-01T00:00:00Z",
    })
    service.repository.save_video_inspection(newer)
    before_take = service.repository.get_take(take.id)
    latest = service.tool_registry.invoke("video.get_take_inspection", {"take_id": take.id})
    exact = service.tool_registry.invoke("video.get_take_inspection", {"take_id": take.id, "report_id": report.id})
    assert latest.output["report"] == newer.model_dump(mode="json")
    assert exact.output["report"] == report.model_dump(mode="json")
    assert {ref.artifact_id for ref in exact.artifact_refs} == {take.id, report.id}
    assert len(fake.generations) == 1
    assert len(service.repository.list_video_inspections(document.project.id)) == 2
    assert service.repository.get_take(take.id) == before_take
    assert service.tool_registry.describe("video.get_take_inspection").effect.value == "READ"
    missing = service.tool_registry.invoke("video.get_take_inspection", {"take_id": take.id, "report_id": "missing"})
    assert missing.status.value == "FAILED"


def test_inspection_persists_intra_shot_and_boundary_evidence(client) -> None:
    initialized = client.post("/api/short-drama/fixtures/dont-trust-me").json()
    project_id = initialized["document"]["project"]["id"]
    shot_ids = [item["id"] for item in initialized["document"]["shots"][:2]]
    queued = client.post(
        f"/api/short-drama/projects/{project_id}/enqueue",
        json={"shot_ids": shot_ids, "candidate_count": 1},
    )
    assert queued.status_code == 200, queued.text
    drained = client.post(
        "/api/short-drama/worker/drain",
        json={"project_id": project_id, "max_steps": 100},
    )
    assert drained.status_code == 200, drained.text
    second_take = next(
        item
        for item in drained.json()["project"]["takes"]
        if item["shot_id"] == shot_ids[1]
    )

    inspected = client.post(
        f"/api/short-drama/takes/{second_take['id']}/inspect-video",
        json={"semantic_review": False, "max_frames": 10},
    )

    assert inspected.status_code == 200, inspected.text
    body = inspected.json()
    report = body["report"]
    assert report["scopes"] == ["INTRA_SHOT", "SHOT_BOUNDARY"]
    assert report["semantic_status"] == "NOT_REQUESTED"
    assert len(report["samples"]) == 14
    assert Path(report["contact_sheet_path"]).is_file()
    assert Path(report["boundary_contact_sheet_path"]).is_file()
    assert body["project"]["video_inspections"][-1]["id"] == report["id"]


def test_semantic_observation_flows_into_optional_director_advice(
    tmp_path: Path,
) -> None:
    fake = FakeInspectionLanguageProvider(
        [
            """{
              "observations": [
                {
                  "start_seconds": 1.0,
                  "end_seconds": 3.0,
                  "description": "The character face changes noticeably across sampled frames.",
                  "confidence": "HIGH"
                }
              ],
              "issues": [
                {
                  "code": "AUTO_IDENTITY_DRIFT",
                  "severity": "HIGH",
                  "message": "Visible identity drift occurs inside the take.",
                  "repair_layer": "DIRECTOR",
                  "category": "IDENTITY_CONTINUITY"
                }
              ],
              "summary": "Identity continuity should be reviewed before this take enters the timeline."
            }""",
            """{
              "advisories": [
                {
                  "category": "CONTINUITY",
                  "message": "Use a stronger identity anchor.",
                  "rationale": "The observation report found visible face drift.",
                  "suggested_action": "Replace the keyframe or identity reference.",
                  "dismissible": true
                }
              ],
              "repair_options": [
                {
                  "strategy": "CHANGE_KEYFRAME",
                  "summary": "Replace the identity keyframe.",
                  "rationale": "It directly addresses the observed identity drift.",
                  "tradeoff": "Requires another generation.",
                  "recommended": true,
                  "dismissible": true
                }
                  ],
                  "recommended_strategy": "CHANGE_KEYFRAME",
                  "proposed_repair": {
                    "decision": "APPLIED",
                    "strategy": "CHANGE_KEYFRAME",
                    "reason": "Use a stronger identity keyframe before regenerating.",
                    "shot_patch": {
                      "visual": "Preserve the same story beat with one stable identity anchor."
                    },
                    "split_shots": []
                  }
                }""",
        ]
    )
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: fake,
    )
    project = service.initialize_fixture()
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://localhost:4141/v1")
    )
    shot = project.document.shots[0]
    service.enqueue(
        project.document.project.id,
        EnqueueRequest(shot_ids=[shot.id], candidate_count=1),
    )
    generated = service.drain(project.document.project.id, 50).project
    take = next(item for item in generated.takes if item.shot_id == shot.id)

    inspected = service.inspect_take_video(
        take.id,
        VideoInspectionRequest(
            semantic_review=True,
            include_previous_boundary=False,
            max_frames=8,
        ),
    )
    advice = service.get_director_advice(
        project.document.project.id,
        shot.id,
        DirectorAdviceRequest(
            take_id=take.id,
            inspection_id=inspected.report.id,
        ),
    )

    assert inspected.report.semantic_status == "COMPLETE"
    assert inspected.report.issues[0].code == "AUTO_IDENTITY_DRIFT"
    assert advice.qa_issue_codes == ["AUTO_IDENTITY_DRIFT"]
    assert advice.recommended_strategy == "CHANGE_KEYFRAME"
    assert fake.generations[0][2] is not None
    assert len(fake.generations[0][2] or []) == 1
    assert "稀疏抽帧无法证明某个动作从未发生" in fake.generations[0][0]
    assert "严重度只使用 LOW、MEDIUM、HIGH 或 CRITICAL" in fake.generations[0][0]
    assert "根据它对成片故事的影响校准" in fake.generations[0][0]
    assert "不要仅因为某项次要偏差写在 ShotPlan 中" in fake.generations[0][0]
    assert "提供标准资产参考图时" in fake.generations[0][0]
    assert "AUTO_IDENTITY_DRIFT" in fake.generations[1][1]
