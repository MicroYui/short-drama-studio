"""One physical paper, explicit blank/single/double signature candidates."""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from short_drama.asset_inspection import AssetVersionInspectionRequest, inspect_asset_image
from short_drama.asset_plan_reconciler import assess_asset_plan_execution_contract
from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest, AssetReviewPurpose, AssetTextPlateComposeRequest, ImageOperation, ImageProviderConfigUpdate, ImageReferenceInput, ImageTaskCreateRequest, ImageTaskStatus
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import AssetKind, AssetPresentationMode, CoreAssetBrief, ImageReferenceAuthority, ImageReferenceRole, ProductionDocumentOrigin, ReferenceAngle, ReferenceFraming, ReferenceRole
from short_drama.production_contracts import (
    AssetPlanCreateRequest, AssetPlanDisposition, AssetPlanItem, AssetPlanOperation,
    AssetPlanTextPlateIntent, AssetPlanTextPlateSpec, AssetPlanWorkItem,
    AssetPlanReference, AssetPlanReferencePackIntent, AssetPlanReplaceRequest,
)
from short_drama.service import ShortDramaService, WorkflowConflictError
from short_drama.tools import ToolCallStatus
from tests.test_asset_inspection import CapturingLanguageClient
from tests.test_asset_plan_runtime import _asset_plan, _setup_story_truth
from tests.test_text_plate_layout import WORDS, _invoke, _setup
from tests.test_openai_image_client import FakeResponse, FakeSession, _client, _success_payload
from tests.test_short_drama_assets import FakeImageProvider


def _paper_request() -> dict[str, object]:
    # Exact live v3 geometry; v20 appended Zhao's name to these four blocks.
    return {
        "size": "1440x2032", "background_color": "#FFFFFF", "foreground_color": "#171717",
        "text_blocks": [
            {"text": WORDS[0], "content_box": [.1, .1, .8, .1], "font_size": 88, "text_align": "center", "vertical_align": "center"},
            {"text": WORDS[1], "content_box": [.1, .28, .8, .07], "font_size": 44, "text_align": "center", "vertical_align": "center"},
            {"text": "经办：", "content_box": [.16, .59, .16, .055], "font_size": 52, "vertical_align": "center"},
            {"text": "复核：", "content_box": [.16, .75, .16, .055], "font_size": 52, "vertical_align": "center"},
        ],
        "lines": [{"start": [.33, .645], "end": [.8, .645], "width": 2},
                  {"start": [.33, .805], "end": [.8, .805], "width": 2}],
        "layout_description": "上方准确正文，下半部签字栏；姓名为准确字形位置底稿，不冒充手写摄影。",
        "descriptor": "双空白表面内容",
    }


def _signed_request(*, double: bool = False, explicit: bool = True) -> dict[str, object]:
    request = _paper_request()
    blocks = request["text_blocks"]
    assert isinstance(blocks, list)
    blocks.append({"text": "赵衡", "content_box": [.37, .752, .3, .048], "font_size": 70, "vertical_align": "center"})
    names = ["赵衡"]
    if double:
        names.append("许岚")
        blocks.append({"text": "许岚", "content_box": [.37, .592, .3, .048], "font_size": 70, "vertical_align": "center"})
    if explicit:
        request["content_state"] = {
            "name": "经办许岚、复核赵衡均已签" if double else "仅复核赵衡已签，经办空白",
            "required_visible_text": [*WORDS, *names],
        }
    return request


def test_live_v3_to_reviewer_only_then_double_preserves_early_candidates(tmp_path: Path) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    photo = tmp_path / "paper-photo.png"
    Image.new("RGB", (256, 360), "lightgray").save(photo)
    imported = service.import_local_asset_version(pid, aid, AssetImportLocalRequest(local_path=str(photo), descriptor="原物理母图"))
    blank = _invoke(service, pid, aid, _paper_request())
    assert blank.status == ToolCallStatus.SUCCEEDED and blank.output is not None
    baseline = deepcopy(blank.output["asset"])
    blank_version = blank.output["version"]
    assert blank_version["version"] == 3
    blank_bytes = Path(blank_version["local_path"]).read_bytes()

    undeclared = _signed_request(explicit=False)
    undeclared["parent_version_id"] = blank_version["id"]
    failed = _invoke(service, pid, aid, undeclared)
    assert failed.status == ToolCallStatus.FAILED and failed.error is not None
    assert "content_state.name" in failed.error.message

    single_request = _signed_request()
    single_request["parent_version_id"] = blank_version["id"]
    single = _invoke(service, pid, aid, single_request)
    assert single.status == ToolCallStatus.SUCCEEDED and single.output is not None, single.error
    double_request = _signed_request(double=True)
    double_request["parent_version_id"] = single.output["version"]["id"]
    double = _invoke(service, pid, aid, double_request)
    assert double.status == ToolCallStatus.SUCCEEDED and double.output is not None, double.error

    current = double.output["asset"]
    for key in ("brief", "current_version_id", "approved_version_id"):
        assert current[key] == baseline[key]
    assert current["current_version_id"] == imported.version.id
    assert current["versions"][:len(baseline["versions"])] == baseline["versions"]
    assert current["versions"][-2] == single.output["version"]
    assert Path(blank_version["local_path"]).read_bytes() == blank_bytes
    for result, request in ((single, single_request), (double, double_request)):
        assert result.output is not None
        version = result.output["version"]
        assert version["text_plate"]["content_state"] == request["content_state"]
        assert version["parent_version_id"] == request["parent_version_id"]
        assert version["approval_status"] == "DRAFT"
        assert version["text_plate"]["purpose"] == "PHYSICAL_SURFACE_CONTENT"
    with Image.open(blank_version["local_path"]) as blank_png, Image.open(single.output["version"]["local_path"]) as single_png, Image.open(double.output["version"]["local_path"]) as double_png:
        changed = ImageChops.difference(blank_png.convert("RGB"), single_png.convert("RGB")).getbbox()
        assert changed is not None and changed[0] >= 532 and changed[1] >= 1528 and changed[3] <= 1626
        second_change = ImageChops.difference(single_png.convert("RGB"), double_png.convert("RGB")).getbbox()
        assert second_change is not None and second_change[0] >= 532 and second_change[1] >= 1202 and second_change[3] <= 1301

    reopened = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    asset = next(item for item in reopened.get_project(pid).document.assets if item.id == aid)
    assert asset.model_dump(mode="json") == current
    for version in asset.versions[-3:]:
        assert version.local_path and Path(version.local_path).is_file()
        client = CapturingLanguageClient()
        inspect_asset_image(document=reopened.get_project(pid).document, asset=asset, version=version, client=client)
        payload = json.loads(client.input_text)
        assert version.text_plate is not None
        assert payload["candidate_creation"]["text_plate"]["content_state"] == version.text_plate.model_dump(mode="json")["content_state"]
        assert "不要求旧空白候选也出现该签名" in client.instructions
    assert reopened.list_image_tasks(pid).tasks == []


@pytest.mark.parametrize("expected", [
    [WORDS[0], "本次报价正确，现补充核对。", *WORDS[2:], "赵衡"],
    [WORDS[0], WORDS[1], "复核", "经办", "赵衡"],
    [WORDS[0], WORDS[1], "经办", "复核赵衡"],
    [WORDS[0], WORDS[1], "经办", "赵衡"],
])
def test_declared_state_cannot_replace_drop_or_reorder_fixed_entries(tmp_path: Path, expected: list[str]) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    request = _signed_request()
    request["content_state"] = {"name": "复核签后", "required_visible_text": expected}
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert "/request/content_state/required_visible_text" in result.error.message
    assert "brief.required_visible_text/" in result.error.message
    assert len(next(asset for asset in service.get_project(pid).document.assets if asset.id == aid).versions) == 1


@pytest.mark.parametrize("rendered_name", ["赵恒", "赵衡许岚"])
def test_declared_state_rejects_changed_or_undeclared_rendered_name(tmp_path: Path, rendered_name: str) -> None:
    service, pid, aid = _setup(tmp_path, physical=True)
    request = _signed_request()
    blocks = request["text_blocks"]
    assert isinstance(blocks, list)
    blocks[-1]["text"] = rendered_name
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    assert "must exactly match /request/content_state/required_visible_text" in result.error.message


def test_plan_intent_and_spec_preserve_the_actual_state_and_rendering_contract(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    # Use the same explicit geometry through saved AssetPlan and the production Tool.
    pid, spec, storyboard = _setup_story_truth(service)
    plan = _asset_plan(service, pid, spec=spec, storyboard=storyboard)
    request = _signed_request()
    intent_payload = {key: value for key, value in request.items() if key not in {"size", "descriptor"}}
    intent = AssetPlanTextPlateIntent.model_validate(intent_payload)
    plate_spec = AssetPlanTextPlateSpec.model_validate({**intent_payload,
        "item_id": "paper", "work_item_id": "reviewer-signed", "name": "纸张签后内容",
        "rationale": "本次状态", "output_role": "signed_surface", "size": "1440x2032"})
    assert plate_spec.to_compose_request(size=plate_spec.size, descriptor="test").model_dump() == intent.to_compose_request(size=plate_spec.size, descriptor="test").model_dump()
    plan.items.append(AssetPlanItem(id="paper", disposition=AssetPlanDisposition.ANCHOR,
        asset_kind=AssetKind.PROP, name="情况说明", rationale="同一物理纸张的具体状态",
        brief=CoreAssetBrief(identity_anchor="同一张情况说明", visual_attributes=["白纸"],
            presentation_mode=AssetPresentationMode.PHYSICAL_MASTER, required_visible_text=WORDS),
        work_items=[AssetPlanWorkItem(id="paper-photo", operation=AssetPlanOperation.GENERATE,
            output_role="physical_carrier", final_prompt="物理纸张照片", size="1024x1024",
            review_purpose=AssetReviewPurpose.CANONICAL_MASTER),
            AssetPlanWorkItem(id="reviewer-signed", operation=AssetPlanOperation.TEXT_PLATE,
            output_role="signed_surface", final_prompt="精确纸面状态", size="1440x2032",
            review_purpose=AssetReviewPurpose.SUPPLEMENTAL_REFERENCE, text_plate_intent=intent,
            dependency_ids=["paper-photo"], reference_pack_intent=AssetPlanReferencePackIntent(
                subject_item_id="paper", role=ReferenceRole.PROP_STATE, angle=ReferenceAngle.FRONT,
                framing=ReferenceFraming.FULL_BODY, tags=["复核签后"],
                lineage_root=AssetPlanReference(role="physical_carrier", purpose="同一张物理纸", source_work_item_id="paper-photo")))]))
    saved = service.create_asset_plan(pid, AssetPlanCreateRequest(asset_plan=plan, origin=ProductionDocumentOrigin.AGENT))
    saved_intent = saved.asset_plan.items[-1].work_items[-1].text_plate_intent
    assert saved_intent is not None and saved_intent.model_dump() == intent.model_dump()
    assert not [issue for issue in assess_asset_plan_execution_contract(saved.asset_plan).issues if issue.item_id == "paper"]
    created = service.create_asset(pid, AssetCreateRequest(kind=AssetKind.PROP, name="情况说明",
        descriptor="物理白纸", brief=plan.items[-1].brief))
    photo = tmp_path / "planned-physical-paper.png"
    Image.new("RGB", (256, 360), "lightgray").save(photo)
    physical = service.import_local_asset_version(pid, created.asset.id,
        AssetImportLocalRequest(local_path=str(photo), descriptor="物理母图"))
    blank = _invoke(service, pid, created.asset.id, _paper_request())
    assert blank.output is not None and blank.status == ToolCallStatus.SUCCEEDED
    rendered = service.compose_asset_text_plate(pid, created.asset.id,
        saved_intent.to_compose_request(size="1440x2032", descriptor="计划中的复核签后字版"))
    completed_plan = saved.asset_plan.model_copy(deep=True)
    completed_item = completed_plan.items[-1]
    completed_item.completed_asset_id = created.asset.id
    completed_item.completed_asset_version_id = physical.version.id
    for work, version_id in zip(completed_item.work_items, [physical.version.id, rendered.version.id], strict=True):
        work.completed_asset_id = created.asset.id
        work.completed_asset_version_id = version_id
    adopted = service.replace_asset_plan(pid, AssetPlanReplaceRequest(asset_plan=completed_plan,
        base_version_id=saved.version.id, origin=ProductionDocumentOrigin.AGENT))
    assert adopted.asset_plan.items[-1].work_items[-1].completed_asset_version_id == rendered.version.id
    wrong_state = adopted.asset_plan.model_copy(deep=True)
    wrong_state.items[-1].work_items[-1].completed_asset_version_id = blank.output["version"]["id"]
    with pytest.raises(WorkflowConflictError, match="different content_state or exact visible wording"):
        service.replace_asset_plan(pid, AssetPlanReplaceRequest(asset_plan=wrong_state,
            base_version_id=adopted.version.id, origin=ProductionDocumentOrigin.AGENT))
    # Direct model conversion cannot discard state/geometry, including blank legacy rows.
    legacy = AssetPlanTextPlateIntent(background_color="#FFF", foreground_color="#111", title="标题",
        exact_lines=["", "正文", ""], layout_description="空行布局")
    assert legacy.to_compose_request(size="1440x2032", descriptor="test").exact_lines == ["", "正文", ""]
    assert service.list_image_tasks(pid).tasks == []


@pytest.mark.parametrize("append_state", [False, True])
def test_exact_selected_state_reaches_real_adapter_and_derived_photo_qa(tmp_path: Path, append_state: bool) -> None:
    fake = FakeImageProvider()
    language = CapturingLanguageClient()
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace",
        image_provider_factory=lambda _key, _url: fake,
        language_provider_factory=lambda _key, _url: language)
    service.update_image_provider_config(ImageProviderConfigUpdate(api_key="test-key", base_url="https://image.example.test/v1/"))
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    pid = service.initialize_fixture().document.project.id
    asset = service.create_asset(pid, AssetCreateRequest(kind=AssetKind.PROP, name="情况说明", descriptor="物理白纸",
        brief=CoreAssetBrief(identity_anchor="同一张情况说明", visual_attributes=["白纸"],
            presentation_mode=AssetPresentationMode.PHYSICAL_MASTER, required_visible_text=WORDS))).asset
    plate_request = AssetTextPlateComposeRequest.model_validate(_signed_request())
    if append_state:
        blank = service.compose_asset_text_plate(pid, asset.id,
            AssetTextPlateComposeRequest.model_validate(_paper_request())).version
        plate_request = AssetTextPlateComposeRequest(
            base_version_id=blank.id, append_text_blocks=[plate_request.text_blocks[-1]],
            content_state=plate_request.content_state, layout_description=plate_request.layout_description,
            descriptor=plate_request.descriptor,
        )
    plate = service.compose_asset_text_plate(pid, asset.id, plate_request).version
    queued = service.enqueue_image_task(pid, asset.id, ImageTaskCreateRequest(operation=ImageOperation.EDIT,
        prompt="把当前选中的表面内容制作成物理纸张摄影，保留字面和位置。", descriptor="复核签后的物理摄影候选",
        parent_version_id=plate.id, reference_inputs=[ImageReferenceInput(asset_version_id=plate.id,
            purpose="本次纸张的准确表面内容和签名状态", reference_role=ImageReferenceRole.PROP_IDENTITY,
            reference_authority=ImageReferenceAuthority.AUTHORITATIVE)]))
    service.advance_image_task(queued.task.id)
    finished = service.advance_image_task(queued.task.id)
    assert finished.task.status == ImageTaskStatus.SUCCEEDED
    assert len(fake.calls) == 1
    actual_inputs = fake.calls[0][1]
    assert actual_inputs[0].text_plate == plate.text_plate
    assert actual_inputs[0].image_url.startswith("data:image/png;base64,")
    # Exercise the real HTTP adapter against an in-memory HTTP transport, never a provider.
    session = FakeSession()
    session.responses.append(FakeResponse(200, _success_payload()))
    _client(session).create(queued.task.request, actual_inputs)
    prompt = session.calls[0][2]["data"]["prompt"]
    assert '"name":"仅复核赵衡已签，经办空白"' in prompt
    assert '"required_visible_text":["情况说明","本次报价出现错误，现补充核对。","经办","复核","赵衡"]' in prompt
    assert '"rendered_visible_text":["情况说明","本次报价出现错误，现补充核对。","经办：","复核：","赵衡"]' in prompt
    assert "不要把不同状态合并" in prompt
    assert "赵衡" not in queued.task.request.prompt
    document = service.get_project(pid).document
    derived = next(version for item in document.assets for version in item.versions if version.id == finished.task.output_version_id)
    assert derived.text_plate is None  # Photography is not mislabeled as deterministic content.
    service.inspect_asset_version(pid, asset.id, derived.id, AssetVersionInspectionRequest())
    payload = json.loads(language.input_text)
    reference = next(item for item in payload["image_map"] if item["version_id"] == plate.id)
    assert reference["text_plate"]["content_state"]["required_visible_text"] == [*WORDS, "赵衡"]
    assert reference["purpose"] == "本次纸张的准确表面内容和签名状态"
    assert payload["candidate_creation"]["text_plate"] is None
    assert "不要把多个不同参考状态合并" in language.instructions
