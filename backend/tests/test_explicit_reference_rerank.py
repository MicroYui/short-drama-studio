"""Explicit reference inspection uses the same selected-media contract as registration."""

import base64
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest, AssetReviewRequest
from short_drama.models import ApprovalStatus, AssetKind, Freshness, ReferenceRole
from short_drama.reference_planner import ReferencePackEntryCreateRequest
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus, ToolResult
from short_drama.visual_input_quality import ReferenceVisualRerankResponse
from tests.test_visual_input_quality_tools import FakeVisualInputLanguageProvider, _service


@dataclass(frozen=True)
class RerankCase:
    service: ShortDramaService
    language: FakeVisualInputLanguageProvider
    project_id: str
    shot_id: str
    subject_id: str
    canonical_id: str
    root_id: str
    source_id: str
    entry_id: str


def _case(tmp_path: Path, *, canonical_entry: bool = False) -> RerankCase:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    project = service.initialize_fixture().document
    project_id = project.project.id
    subject_id = next(asset.id for asset in project.assets if asset.kind == AssetKind.CHARACTER)

    def import_image(asset_id: str, name: str, color: str) -> str:
        image_path = tmp_path / f"{name}.png"
        Image.new("RGB", (192, 320), color).save(image_path)
        return service.import_local_asset_version(project_id, asset_id, AssetImportLocalRequest(
            local_path=str(image_path), descriptor=name,
        )).version.id

    canonical_id = import_image(subject_id, "existing-canonical", "red")
    service.review_asset_version(project_id, subject_id, canonical_id, AssetReviewRequest(
        decision="APPROVED", purpose="CANONICAL_MASTER", notes="Existing identity choice",
    ))
    root_id = import_image(subject_id, "explicit-draft-root", "green")
    source_id = root_id
    if not canonical_entry:
        carrier = service.create_asset(project_id, AssetCreateRequest(
            kind=AssetKind.REFERENCE, name="Camera reference", descriptor="Alternate camera image",
        )).asset
        source_id = import_image(carrier.id, "explicit-draft-reference", "blue")
    entry = service.add_reference_pack_entry(project_id, ReferencePackEntryCreateRequest(
        subject_asset_id=subject_id,
        asset_version_id=source_id,
        lineage_root_version_id=root_id,
        role=ReferenceRole.CANONICAL_MASTER if canonical_entry else ReferenceRole.CHARACTER_FACE,
        description="camera-matched exact selected image",
    )).entry
    return RerankCase(service, language, project_id, project.shots[0].id,
                      subject_id, canonical_id, root_id, source_id, entry.id)


def _invoke(case: RerankCase) -> ToolResult:
    return case.service.tool_registry.invoke("qa.rerank_reference_visuals", {
        "project_id": case.project_id,
        "shot_id": case.shot_id,
        "request": {"candidate_entry_ids": [case.entry_id], "max_selected": 1},
    })


@pytest.mark.parametrize("canonical_entry", [False, True])
def test_explicit_draft_rerank_observes_exact_file_and_preserves_canonical_after_restart(
    tmp_path: Path, canonical_entry: bool,
) -> None:
    case = _case(tmp_path, canonical_entry=canonical_entry)
    before = case.service.get_project(case.project_id).document
    subject = next(asset for asset in before.assets if asset.id == case.subject_id)
    assert subject.approved_version_id == case.canonical_id != case.root_id
    versions = {version.id: version for asset in before.assets for version in asset.versions}
    assert versions[case.source_id].approval_status == ApprovalStatus.DRAFT
    assert versions[case.root_id].approval_status == ApprovalStatus.DRAFT

    result = _invoke(case)
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    response = ReferenceVisualRerankResponse.model_validate(result.output)
    assert [(item.entry_id, item.asset_version_id) for item in response.report.candidates] == [
        (case.entry_id, case.source_id),
    ]
    assert response.report.selections[0].candidate_id == case.entry_id
    assert len(case.language.calls) == 1
    _instructions, payload, image_urls = case.language.calls[0]
    assert payload["image_map"][0]["candidate_id"] == case.entry_id
    assert len(image_urls) == 1
    # Verify the selected pixels were supplied, not the existing approved master.
    source_path = versions[case.source_id].local_path
    assert source_path is not None
    assert base64.b64decode(image_urls[0].split(",", 1)[1]) == Path(source_path).read_bytes()
    assert case.service.get_project(case.project_id).document == before

    restarted = _service(tmp_path, case.language)
    reports = restarted.list_visual_input_reports(case.project_id).reference_reranks
    assert reports == [response.report]
    assert restarted.get_project(case.project_id).document == before


@pytest.mark.parametrize("fault", [
    "source_rejected", "source_stale", "source_missing",
    "root_rejected", "root_stale", "root_missing", "wrong_root", "unknown_root",
    "canonical_wrong_owner", "incompatible_role",
])
def test_explicit_reference_rerank_rechecks_current_media_and_subject_lineage_before_model(
    tmp_path: Path, fault: str,
) -> None:
    case = _case(tmp_path)
    document = case.service.get_project(case.project_id).document
    versions = {version.id: version for asset in document.assets for version in asset.versions}
    entry = next(entry for pack in document.reference_packs for entry in pack.entries
                 if entry.id == case.entry_id)
    target = versions[case.root_id if fault.startswith("root_") else case.source_id]
    if fault.endswith("_rejected"):
        target.approval_status = ApprovalStatus.REJECTED
    elif fault.endswith("_stale"):
        target.freshness = Freshness.STALE
    elif fault.endswith("_missing"):
        assert target.local_path is not None
        Path(target.local_path).unlink()
    elif fault == "wrong_root":
        # This is a usable DRAFT image, but it belongs to the reference carrier.
        entry.lineage_root_version_id = case.source_id
    elif fault == "unknown_root":
        entry.lineage_root_version_id = "unknown-root-version"
    elif fault == "canonical_wrong_owner":
        entry.role = ReferenceRole.CANONICAL_MASTER
        entry.lineage_root_version_id = case.source_id
    else:
        entry.role = ReferenceRole.LOCATION_MASTER
    case.service.repository.save_project(document)
    before = case.service.get_project(case.project_id).document

    result = _invoke(case)
    assert result.status == ToolCallStatus.FAILED
    assert result.error is not None
    assert result.error.kind == "WorkflowConflictError"
    assert case.entry_id in result.error.message
    if fault.endswith("_missing"):
        assert target.id in result.error.message
    assert case.language.calls == []
    assert case.service.list_visual_input_reports(case.project_id).reference_reranks == []
    assert case.service.get_project(case.project_id).document == before
