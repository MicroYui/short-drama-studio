"""Actual PNG additions preserve existing paper layout and persisted plan state."""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from short_drama.asset_inspection import inspect_asset_image
from short_drama.asset_plan_reconciler import _plate_item
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest, AssetReviewPurpose, AssetTextPlateComposeRequest
from short_drama.models import ApprovalStatus, AssetKind, AssetPresentationMode, AssetVersion, CoreAssetBrief, Freshness, ProductionDocumentOrigin, ReferenceAngle, ReferenceFraming, ReferenceRole
from short_drama.production_contracts import AssetPlanCreateRequest, AssetPlanDisposition, AssetPlanItem, AssetPlanOperation, AssetPlanReference, AssetPlanReferencePackIntent, AssetPlanReplaceRequest, AssetPlanTextPlateIntent, AssetPlanTextPlateSpec, AssetPlanWorkItem
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_asset_plan_runtime import _asset_plan, _setup_story_truth
from tests.test_text_plate_layout import WORDS, _invoke, _setup
from tests.test_text_plate_states import _paper_request


def _append(base_id: str, *, double: bool = False) -> dict[str, object]:
    return {
        "base_version_id": base_id,
        "append_text_blocks": [{"text": "许岚" if double else "赵衡", "content_box": [.37, .592 if double else .752, .3, .048], "font_size": 70, "vertical_align": "center"}],
        "content_state": {"name": "两栏已签" if double else "仅复核签后", "required_visible_text": [*WORDS, "赵衡", *(["许岚"] if double else [])]},
        "layout_description": "复制既有纸面，仅增加本次签名位置的准确文字。",
        "descriptor": "双签内容字版" if double else "仅复核签后内容字版",
    }


def _version(result: object) -> AssetVersion:
    # The production registry returns JSON, then the public domain model verifies it.
    assert hasattr(result, "status") and result.status == ToolCallStatus.SUCCEEDED, result
    assert hasattr(result, "output") and result.output is not None
    return AssetVersion.model_validate(result.output["version"])


def _asset(service: ShortDramaService, pid: str, aid: str):
    return next(asset for asset in service.get_project(pid).document.assets if asset.id == aid)


def test_actual_blank_single_double_append_preserves_pixels_metadata_and_current(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    blank = _version(_invoke(service, pid, aid, _paper_request()))
    baseline = _asset(service, pid, aid).model_dump(mode="json")
    assert blank.local_path is not None
    blank_bytes = Path(blank.local_path).read_bytes()
    first = _append(blank.id)
    first["parent_version_id"] = blank.id
    single = _version(_invoke(service, pid, aid, first))
    second = _append(single.id, double=True)
    # Parent remains a caller-controlled lineage label, distinct from the actual base.
    second["parent_version_id"] = blank.id
    double = _version(_invoke(service, pid, aid, second))
    assert double.parent_version_id == blank.id
    assert double.derivation_references[0].asset_version_id == single.id
    for result, old in ((single, blank), (double, single)):
        assert result.text_plate is not None and old.text_plate is not None
        assert result.text_plate.size == "1440x2032"
        for field in ("title", "exact_lines", "text_blocks", "lines", "content_box", "foreground_color", "background_color", "font_family"):
            assert getattr(result.text_plate, field) == getattr(blank.text_plate, field)
        assert result.text_plate.append_text_blocks[:-1] == old.text_plate.append_text_blocks
        assert result.text_plate.append_text_blocks[-1].font_family != "resolved during rendering"
        assert result.local_path is not None and old.local_path is not None
        with Image.open(old.local_path) as previous, Image.open(result.local_path) as current:
            box = ImageChops.difference(previous.convert("RGB"), current.convert("RGB")).getbbox()
            assert box is not None
            y = 1202 if result.id == double.id else 1528
            assert box[0] >= 532 and box[2] <= 966 and box[1] >= y and box[3] <= y + 98
        assert result.approval_status == ApprovalStatus.DRAFT
    current = _asset(service, pid, aid).model_dump(mode="json")
    assert current["versions"][:len(baseline["versions"])] == baseline["versions"]
    for field in ("brief", "current_version_id", "approved_version_id"):
        assert current[field] == baseline[field]
    assert Path(blank.local_path).read_bytes() == blank_bytes
    reopened = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    assert _asset(reopened, pid, aid).model_dump(mode="json") == current
    qa = CapturingLanguageClient()
    inspect_asset_image(document=reopened.get_project(pid).document, asset=_asset(reopened, pid, aid), version=double, client=qa)
    actual = json.loads(qa.input_text)["candidate_creation"]["text_plate"]
    assert [block["text"] for block in actual["append_text_blocks"]] == ["赵衡", "许岚"]
    assert actual["content_state"]["required_visible_text"] == [*WORDS, "赵衡", "许岚"]
    assert reopened.list_image_tasks(pid).tasks == []


def test_legacy_transparent_plate_can_append_without_reflow_or_canvas_defaults(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path, words=["TITLE", "BODY"])
    base = _version(_invoke(service, pid, aid, {"size": "512x768", "background_color": "#00000000", "foreground_color": "#222222", "title": "TITLE", "exact_lines": ["", "BODY"], "content_box": [0, 0, 1, .5], "layout_description": "Existing legacy layout", "descriptor": "Base"}))
    before = _asset(service, pid, aid).model_dump(mode="json")
    request = {"base_version_id": base.id, "append_text_blocks": [{"text": "SIGNED", "content_box": [.2, .7, .6, .1], "font_size": 32}], "content_state": {"name": "Signed", "required_visible_text": ["TITLE", "BODY", "SIGNED"]}, "layout_description": "Add one label", "descriptor": "Signed"}
    # Model dumps include historical full-mode defaults; reload must still inherit the base.
    restored = AssetTextPlateComposeRequest.model_validate(AssetTextPlateComposeRequest.model_validate(request).model_dump())
    result = service.compose_asset_text_plate(pid, aid, restored).version
    assert result.width == 512 and result.height == 768
    assert result.text_plate is not None and base.text_plate is not None
    assert result.text_plate.exact_lines == ["", "BODY"] and result.text_plate.title == "TITLE"
    assert result.text_plate.background_color == "#00000000"
    assert result.local_path is not None and base.local_path is not None
    with Image.open(base.local_path) as previous, Image.open(result.local_path) as current:
        assert previous.mode == current.mode == "RGBA"
        assert previous.crop((0, 0, 512, 537)).tobytes() == current.crop((0, 0, 512, 537)).tobytes()
        assert previous.getpixel((0, 767)) == current.getpixel((0, 767)) == (0, 0, 0, 0)
        assert current.crop((102, 537, 410, 614)).getchannel("A").getbbox() is not None
    assert _asset(service, pid, aid).current_version_id == before["current_version_id"]
    assert service.list_image_tasks(pid).tasks == []


@pytest.mark.parametrize("problem", ["unknown", "photograph", "missing", "empty", "corrupt", "wrong_dimensions", "rejected", "stale"])
def test_append_requires_an_actual_current_usable_text_plate(tmp_path: Path, problem: str) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    base = _version(_invoke(service, pid, aid, _paper_request()))
    assert base.local_path is not None
    if problem == "unknown":
        base_id = "missing-base-version"
    elif problem == "photograph":
        path = tmp_path / "photo.png"
        Image.new("RGB", (256, 360), "white").save(path)
        base_id = service.import_local_asset_version(pid, aid, AssetImportLocalRequest(local_path=str(path), descriptor="Actual photo without text-plate metadata")).version.id
    else:
        base_id = base.id
        if problem == "missing":
            Path(base.local_path).unlink()
        elif problem == "empty":
            Path(base.local_path).write_bytes(b"")
        elif problem == "corrupt":
            Path(base.local_path).write_bytes(b"not a PNG")
        elif problem == "wrong_dimensions":
            Image.new("RGB", (256, 360), "white").save(base.local_path)
        else:
            document = service.get_project(pid).document
            candidate = next(version for version in next(asset for asset in document.assets if asset.id == aid).versions if version.id == base.id)
            if problem == "rejected":
                candidate.approval_status = ApprovalStatus.REJECTED
            else:
                candidate.freshness = Freshness.STALE
            service.repository.save_project(document)
    before = _asset(service, pid, aid).model_dump(mode="json")
    result = _invoke(service, pid, aid, _append(base_id))
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert "base_version_id" in result.error.message and base_id in result.error.message
    assert _asset(service, pid, aid).model_dump(mode="json") == before
    assert service.list_image_tasks(pid).tasks == []


@pytest.mark.parametrize("change", ["no_base", "no_blocks", "no_state", "rebuild_content", "wrong_name", "missing_fixed", "overflow"])
def test_append_rejects_incomplete_or_contradictory_changes_without_saving(tmp_path: Path, change: str) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    base = _version(_invoke(service, pid, aid, _paper_request()))
    request = _append(base.id)
    if change == "no_base":
        request.pop("base_version_id")
    elif change == "no_blocks":
        request["append_text_blocks"] = []
    elif change == "no_state":
        request.pop("content_state")
    elif change == "rebuild_content":
        request["title"] = "情况说明"
    elif change == "wrong_name":
        request["append_text_blocks"] = [{"text": "赵恒", "content_box": [.3, .7, .4, .1]}]
    elif change == "missing_fixed":
        request["content_state"] = {"name": "签后", "required_visible_text": ["赵衡"]}
    else:
        request["append_text_blocks"] = [{"text": "赵衡", "content_box": [.3, .7, .01, .01], "font_size": 70}]
    before = _asset(service, pid, aid).model_dump(mode="json")
    result = _invoke(service, pid, aid, request)
    assert result.status in {ToolCallStatus.INVALID_ARGUMENTS, ToolCallStatus.FAILED} and result.error is not None
    if change == "overflow":
        assert "/request/append_text_blocks/0" in result.error.message
    assert _asset(service, pid, aid).model_dump(mode="json") == before
    assert service.list_image_tasks(pid).tasks == []


def test_plan_append_survives_save_reload_completion_and_checks_actual_base(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    pid, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, pid, spec=spec, storyboard=storyboard)
    brief = CoreAssetBrief(identity_anchor="同一张情况说明", visual_attributes=["白纸"], presentation_mode=AssetPresentationMode.PHYSICAL_MASTER, required_visible_text=WORDS)
    created = service.create_asset(pid, AssetCreateRequest(kind=AssetKind.PROP, name="情况说明", descriptor="物理白纸", brief=brief))
    aid = created.asset.id
    photo = tmp_path / "photo.png"
    Image.new("RGB", (256, 360), "white").save(photo)
    physical = service.import_local_asset_version(pid, aid, AssetImportLocalRequest(local_path=str(photo), descriptor="物理母图")).version
    blank = _version(_invoke(service, pid, aid, _paper_request()))
    payload = {key: value for key, value in _append(blank.id).items() if key != "descriptor"}
    intent = AssetPlanTextPlateIntent.model_validate(payload)
    planned = AssetPlanItem(id="paper", disposition=AssetPlanDisposition.ANCHOR, asset_kind=AssetKind.PROP, name="情况说明", rationale="同一纸张的状态", brief=brief, completed_asset_id=aid, completed_asset_version_id=physical.id, work_items=[
        AssetPlanWorkItem(id="paper-photo", operation=AssetPlanOperation.GENERATE, output_role="physical_carrier", final_prompt="物理纸张", size="1024x1024", review_purpose=AssetReviewPurpose.CANONICAL_MASTER, completed_asset_id=aid, completed_asset_version_id=physical.id),
        AssetPlanWorkItem(id="signed-surface", operation=AssetPlanOperation.TEXT_PLATE, output_role="signed_surface", final_prompt="仅追加复核姓名", size="1440x2032", review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE, text_plate_intent=intent, dependency_ids=["paper-photo"], reference_pack_intent=AssetPlanReferencePackIntent(subject_item_id="paper", role=ReferenceRole.PROP_STATE, angle=ReferenceAngle.FRONT, framing=ReferenceFraming.FULL_BODY, tags=["复核签后"], lineage_root=AssetPlanReference(role="physical_carrier", purpose="同一张纸", source_work_item_id="paper-photo"))),
    ])
    plan.items.append(planned)
    saved = service.create_asset_plan(pid, AssetPlanCreateRequest(asset_plan=plan, origin=ProductionDocumentOrigin.AGENT))
    reopened = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    loaded = reopened.get_asset_plan(pid)
    loaded_intent = loaded.asset_plan.items[-1].work_items[-1].text_plate_intent
    assert loaded_intent is not None and loaded_intent.base_version_id == blank.id
    context = reopened.get_asset_work_context(pid, item_ids=[], work_item_ids=["signed-surface"], shot_ids=[], clip_group_ids=[])
    selected_base = next(version for version in context.media_versions if version.asset_version_id == blank.id)
    assert selected_base.local_path == blank.local_path and selected_base.width == 1440
    rendered = reopened.compose_asset_text_plate(pid, aid, loaded_intent.to_compose_request(size="1440x2032", descriptor="计划中的单签")).version
    completed = loaded.asset_plan.model_copy(deep=True)
    completed.items[-1].work_items[-1].completed_asset_id = aid
    completed.items[-1].work_items[-1].completed_asset_version_id = rendered.id
    adopted = reopened.replace_asset_plan(pid, AssetPlanReplaceRequest(asset_plan=completed, base_version_id=saved.version.id, origin=ProductionDocumentOrigin.AGENT))
    assert adopted.asset_plan.items[-1].work_items[-1].completed_asset_version_id == rendered.id
    wrong = adopted.asset_plan.model_copy(deep=True)
    wrong.items[-1].work_items[-1].completed_asset_version_id = blank.id
    with pytest.raises(WorkflowConflictError, match="different content_state or exact visible wording"):
        reopened.replace_asset_plan(pid, AssetPlanReplaceRequest(asset_plan=wrong, base_version_id=adopted.version.id, origin=ProductionDocumentOrigin.AGENT))
    # The independent-content Spec preserves the same full state and source fields.
    plate_spec = AssetPlanTextPlateSpec.model_validate({**payload, "item_id": "copy", "work_item_id": "copied-state", "name": "明确引用的内容副本", "rationale": "按需内容分层", "output_role": "content", "size": "1440x2032"})
    item = _plate_item(plate_spec)
    assert item.brief is not None and item.brief.required_visible_text == [*WORDS, "赵衡"]
    assert item.work_items[0].text_plate_intent is not None
    assert item.work_items[0].text_plate_intent.base_version_id == blank.id
    assert reopened.list_image_tasks(pid).tasks == []
