"""Keyframe inputs follow current prose media, including the actual visual call."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetMediaType,
    AssetVersion,
    Freshness,
    OriginType,
    ProjectDocument,
    ReferenceBinding,
    ReferencePack,
    ReferencePackEntry,
    ReferencePlanEntry,
    ReferenceRequirement,
    ReferenceRole,
    ShotPlan,
    ShotReferenceCue,
    ShotTimelineBeat,
)
from short_drama.reference_planner import (
    KeyframeBriefCompileRequest,
    KeyframePosition,
    ReferencePlanPreviewRequest,
    compile_keyframe_brief,
    preview_reference_plan,
)
from short_drama.service import ShortDramaService
from short_drama.video_inspection import selected_generation_reference_ids
from short_drama.visual_input_quality import KeyframeComparisonRequest
from tests.test_visual_input_quality_tools import FakeVisualInputLanguageProvider, _service


@dataclass
class CurrentReferences:
    service: ShortDramaService
    language: FakeVisualInputLanguageProvider
    document: ProjectDocument
    shot: ShotPlan
    plan: ReferencePlanEntry
    generation_ids: list[str]
    candidate_id: str


@pytest.fixture
def current_references(tmp_path: Path) -> CurrentReferences:
    language = FakeVisualInputLanguageProvider()
    service = _service(tmp_path, language)
    document = service.initialize_fixture().document
    shot = document.shots[0]
    project_id = document.project.id
    created_at = document.project.created_at
    subjects = [
        ("canteen", "食堂", AssetKind.LOCATION),
        ("lin", "林一", AssetKind.CHARACTER),
        ("aunt", "阿姨", AssetKind.CHARACTER),
        ("student-a", "学生甲", AssetKind.CHARACTER),
        ("principal", "校长", AssetKind.CHARACTER),
        ("tray", "餐盘", AssetKind.PROP),
        ("spoon", "菜勺", AssetKind.PROP),
        ("student-b", "学生乙", AssetKind.CHARACTER),
        ("price-a", "土豆烧肉8元", AssetKind.PROP),
        ("price-b", "番茄炒蛋5元", AssetKind.PROP),
        ("candidate", "待比较关键帧", AssetKind.REFERENCE),
    ]
    assets: list[Asset] = []
    packs: list[ReferencePack] = []
    generation_cues: list[ShotReferenceCue] = []
    editorial_cues: list[ShotReferenceCue] = []
    for index, (asset_id, name, kind) in enumerate(subjects):
        versions: list[AssetVersion] = []
        for revision in (1, 2):
            version_id = f"{asset_id}-v{revision}"
            path = tmp_path / f"{version_id}.png"
            Image.new("RGB", (32, 32), (index * 20, revision * 70, 40)).save(path)
            versions.append(AssetVersion(
                id=version_id, version=revision, origin_type=OriginType.UPLOAD,
                descriptor=f"{name} revision {revision}", media_type=AssetMediaType.IMAGE,
                local_path=str(path), approval_status=(
                    ApprovalStatus.DRAFT if asset_id == "student-b" and revision == 2
                    else ApprovalStatus.APPROVED
                ), freshness=Freshness.CURRENT, created_at=created_at,
            ))
        assets.append(Asset(
            id=asset_id, project_id=project_id, kind=kind, name=name,
            current_version_id=versions[-1].id, approved_version_id=versions[0].id,
            versions=versions,
        ))
        if asset_id == "candidate":
            continue
        packs.append(ReferencePack(
            id=f"pack-{asset_id}", project_id=project_id, subject_asset_id=asset_id,
            name=f"Historical {name} views", created_at=created_at, updated_at=created_at,
            entries=[ReferencePackEntry(
                id=f"entry-{asset_id}", asset_version_id=versions[0].id,
                role=ReferenceRole.CANONICAL_MASTER, description=f"Old {name}", priority=100,
            )],
        ))
        editorial = asset_id.startswith("price-")
        cue = ShotReferenceCue(
            id=f"cue-{asset_id}", subject_asset_id=asset_id, asset_version_id=versions[-1].id,
            alias=name, purpose=f"当前{name}具体用途", usage="EDITORIAL" if editorial else "GENERATION",
        )
        (editorial_cues if editorial else generation_cues).append(cue)
    shot.references = generation_cues[:4]
    shot.timeline_beats = [ShotTimelineBeat(
        start_seconds=0, end_seconds=shot.duration_seconds,
        visual="学生乙在食堂出现，价牌后期叠加", framing="中景", camera="固定", action="端起餐盘",
        dialogue_line_ids=list(shot.dialogue_line_ids),
        references=[*generation_cues[4:], *editorial_cues],
    )]
    # The old order puts two price plates before student B, overflowing the nine-image budget.
    old_order = [assets[1], assets[0], assets[8], *assets[2:7], assets[9], assets[7]]
    plan = ReferencePlanEntry(
        shot_id=shot.id, capability_valid=True, validation_notes=[], max_reference_images=9,
        bindings=[ReferenceBinding(kind=asset.kind, target_id=asset.versions[0].id,
                                   purpose=f"Old {asset.name}") for asset in old_order],
        requirements=[ReferenceRequirement(
            id=f"requirement-{asset.id}", subject_asset_id=asset.id,
            acceptable_roles=[ReferenceRole.CANONICAL_MASTER], purpose=f"Recognize {asset.name}",
        ) for asset in old_order],
    )
    document.assets = assets
    document.reference_packs = packs
    document.shots = [shot]
    document.reference_plan = [plan]
    service.repository.save_project(document)
    return CurrentReferences(
        service, language, document, shot, plan,
        [cue.asset_version_id for cue in generation_cues if cue.asset_version_id is not None],
        "candidate-v2",
    )


@pytest.mark.parametrize("position", [KeyframePosition.START, KeyframePosition.END])
@pytest.mark.parametrize("with_requirements", [True, False])
def test_current_generation_cues_win_without_reviving_editorial_or_replaced_media(
    current_references: CurrentReferences, position: KeyframePosition, with_requirements: bool,
) -> None:
    case = current_references
    if not with_requirements:
        case.plan.requirements = []
    before = case.document.model_dump_json()
    brief = compile_keyframe_brief(
        case.document, case.shot, case.plan, KeyframeBriefCompileRequest(position=position),
    )
    assert brief.input_version_ids == case.generation_ids
    assert "student-b-v2" in brief.input_version_ids
    assert not any("price-" in item or item.endswith("-v1") for item in brief.input_version_ids)
    assert brief.input_version_ids == selected_generation_reference_ids(case.document, case.plan)
    assert not any("price-" in item for item in brief.missing_requirement_ids)
    assert "当前学生乙具体用途" in brief.prompt
    assert "仅用于 Old" not in brief.prompt
    assert case.document.model_dump_json() == before


@pytest.mark.parametrize("position", [KeyframePosition.START, KeyframePosition.END])
def test_explicit_requirement_scope_and_primary_canvas_keep_their_meaning(
    current_references: CurrentReferences, position: KeyframePosition,
) -> None:
    case = current_references
    brief = compile_keyframe_brief(case.document, case.shot, case.plan, KeyframeBriefCompileRequest(
        position=position, requirement_ids=["requirement-student-b"],
        primary_canvas_version_id=case.candidate_id,
    ))
    assert brief.input_version_ids == [case.candidate_id, "student-b-v2"]
    assert brief.primary_input_version_id == case.candidate_id
    assert [item.subject_asset_id for item in brief.selected] == ["student-b"]


@pytest.mark.parametrize("unavailable", ["missing-file", "rejected", "stale", "unresolved"])
def test_unavailable_current_choice_does_not_restore_older_image(
    current_references: CurrentReferences, unavailable: str,
) -> None:
    case = current_references
    asset = next(item for item in case.document.assets if item.id == "student-b")
    case.plan.bindings.append(ReferenceBinding(
        kind=asset.kind, target_id=asset.current_version_id, purpose="Previously copied current choice",
    ))
    if unavailable == "missing-file":
        asset.versions[-1].local_path = "/does-not-exist/current-student.png"
    elif unavailable == "rejected":
        asset.versions[-1].approval_status = ApprovalStatus.REJECTED
    elif unavailable == "stale":
        asset.versions[-1].freshness = Freshness.STALE
    else:
        cue = next(item for item in case.shot.timeline_beats[0].references if item.subject_asset_id == asset.id)
        cue.asset_version_id = None
    result = preview_reference_plan(
        case.document, case.shot, case.plan, ReferencePlanPreviewRequest(),
    )
    assert not any(item.subject_asset_id == asset.id for item in result.selected)
    assert "cue-student-b" in result.missing_requirement_ids
    assert any("no older image substituted" in item for item in result.warnings)


@pytest.mark.parametrize("position", [KeyframePosition.START, KeyframePosition.END])
def test_compare_keyframes_sends_current_exact_images_and_honest_media_metadata(
    current_references: CurrentReferences, position: KeyframePosition,
) -> None:
    case = current_references
    before = case.service.repository.get_project(case.document.project.id)
    case.service.compare_shot_keyframes(
        case.document.project.id, case.shot.id, KeyframeComparisonRequest(
            position=position, candidate_version_ids=[case.candidate_id, "candidate-v1"],
            include_previous_boundary=False,
        ),
    )
    assert len(case.language.calls) == 1
    _instructions, payload, images = case.language.calls[0]
    expected_ids = [case.candidate_id, "candidate-v1", *case.generation_ids]
    assert [item["asset_version_id"] for item in payload["image_map"]] == expected_ids
    paths = {version.id: version.local_path for asset in case.document.assets for version in asset.versions}
    for index, (version_id, url) in enumerate(zip(expected_ids, images, strict=True), start=1):
        path = paths[version_id]
        assert path is not None
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == Path(path).read_bytes()
        assert payload["image_map"][index - 1]["image"] == index
    student = next(item for item in payload["image_map"] if item["asset_version_id"] == "student-b-v2")
    assert student["role"] == "KEYFRAME_GENERATION_REFERENCE"
    assert student["approval_status"] == "DRAFT"
    assert student["purpose"] == "当前学生乙具体用途"
    assert student["subject_asset_id"] == "student-b"
    assert case.service.repository.get_project(case.document.project.id) == before


def test_current_selection_leaves_legacy_specialized_views_for_unmentioned_subjects(
    current_references: CurrentReferences,
) -> None:
    case = current_references
    case.shot.references = [case.shot.references[1]]  # only Lin is explicitly selected
    case.shot.timeline_beats = []
    result = preview_reference_plan(case.document, case.shot, case.plan, ReferencePlanPreviewRequest(
        requirement_ids=["requirement-lin", "requirement-canteen"], max_images=2,
    ))
    assert [item.asset_version_id for item in result.selected] == ["lin-v2", "canteen-v1"]
    assert result.selected[1].entry_id == "entry-canteen"
