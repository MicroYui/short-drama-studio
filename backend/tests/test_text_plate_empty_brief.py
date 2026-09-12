"""Replay Fresh6's explicit candidate text without changing its physical master brief."""

from copy import deepcopy
import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from short_drama.image_models import AssetCreateRequest, AssetImportLocalRequest
from short_drama.models import AssetVersion
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus
from tests.test_text_plate_layout import _invoke


FIXTURE = Path(__file__).parent / "fixtures" / "fresh6_paper_candidate_contracts.json"


def _setup(tmp_path: Path):
    saved = json.loads(FIXTURE.read_text())
    original = saved["asset_before_text_plate"]
    service = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    pid = service.initialize_fixture().document.project.id
    created = service.create_asset(pid, AssetCreateRequest.model_validate({key: original[key] for key in ("kind", "name", "descriptor", "brief")}))
    path = tmp_path / "local-carrier.png"
    Image.new("RGB", (256, 360), "lightgray").save(path)
    imported = service.import_local_asset_version(pid, created.asset.id, AssetImportLocalRequest(local_path=str(path), descriptor="Local replay carrier; no generated quality claim"))
    # Only identities change in the exact failed request; content, geometry and brief remain real.
    request = deepcopy(saved["text_plate_attempt"]["arguments"]["request"])
    request["parent_version_id"] = imported.version.id
    return service, pid, created.asset.id, request, imported.asset.model_dump(mode="json")


def _result_version(result) -> AssetVersion:
    assert result.status == ToolCallStatus.SUCCEEDED, result.error
    assert result.output is not None
    return AssetVersion.model_validate(result.output["version"])


def test_real_explicit_state_renders_without_fixed_global_copy_and_survives_reopen(tmp_path: Path) -> None:
    service, pid, aid, request, before = _setup(tmp_path)
    assert before["brief"]["required_visible_text"] == []
    saved = json.loads(FIXTURE.read_text())
    assert saved["text_plate_attempt"]["result"]["status"] == "FAILED"
    assert "brief must declare required_visible_text" in saved["text_plate_attempt"]["result"]["error"]["message"]
    result = _invoke(service, pid, aid, request)
    plate = _result_version(result)
    assert plate.text_plate is not None and plate.local_path is not None
    assert plate.text_plate.content_state is not None
    assert plate.text_plate.content_state.model_dump(mode="json") == request["content_state"]
    assert plate.text_plate.purpose == "PHYSICAL_SURFACE_CONTENT"
    assert plate.parent_version_id == request["parent_version_id"]
    assert plate.approval_status == "DRAFT"
    assert any(ref.artifact_id == plate.id and ref.version == plate.version for ref in result.artifact_refs)
    with Image.open(plate.local_path) as image:
        assert image.format == "PNG" and image.size == (1136, 1600)
        ink = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white")).getbbox()
        assert ink is not None and ink[3] < 600
        assert image.convert("RGB").crop((0, 800, 1136, 1600)).getextrema() == ((255, 255),) * 3
    current = next(asset for asset in service.get_project(pid).document.assets if asset.id == aid)
    actual = current.model_dump(mode="json")
    for key in ("brief", "current_version_id", "approved_version_id"):
        assert actual[key] == before[key]
    assert actual["versions"][:-1] == before["versions"]
    reopened = ShortDramaService(database_path=tmp_path / "data.sqlite3", workspace_root=tmp_path / "workspace")
    restored = next(asset for asset in reopened.get_project(pid).document.assets if asset.id == aid)
    assert restored == current
    assert reopened.list_image_tasks(pid).tasks == []


@pytest.mark.parametrize("problem", ["no_state", "different_rendered_copy", "wrong_carrier"])
def test_empty_brief_still_requires_explicit_complete_copy_and_a_supported_carrier(tmp_path: Path, problem: str) -> None:
    service, pid, aid, request, _before = _setup(tmp_path)
    if problem == "no_state":
        request.pop("content_state")
    elif problem == "different_rendered_copy":
        request["text_blocks"][1]["text"] = "不是本次声明的文案。"
    else:
        project = service.get_project(pid)
        asset = next(asset for asset in project.document.assets if asset.id == aid)
        assert asset.brief is not None
        # An undeclared presentation remains outside the Tool's existing domain.
        asset.brief.presentation_mode = None
        service.repository.save_project(project.document)
    baseline = service.get_project(pid).document.model_dump(mode="json")
    result = _invoke(service, pid, aid, request)
    assert result.status == ToolCallStatus.FAILED and result.error is not None
    if problem == "no_state":
        assert "content_state.name" in result.error.message
    elif problem == "different_rendered_copy":
        assert "must exactly match" in result.error.message
    else:
        assert "PHYSICAL_MASTER" in result.error.message
    assert service.get_project(pid).document.model_dump(mode="json") == baseline
    assert service.list_image_tasks(pid).tasks == []


def test_empty_global_copy_keeps_append_only_pixels_and_previous_candidate(tmp_path: Path) -> None:
    service, pid, aid, request, before = _setup(tmp_path)
    base = _result_version(_invoke(service, pid, aid, request))
    assert base.local_path is not None
    original_bytes = Path(base.local_path).read_bytes()
    appended = _result_version(_invoke(service, pid, aid, {
        "base_version_id": base.id, "parent_version_id": base.id,
        "append_text_blocks": [{"text": "周明", "content_box": [.12, .7, .4, .08], "font_size": 42}],
        "content_state": {"name": "补写署名的候选", "required_visible_text": [*request["content_state"]["required_visible_text"], "周明"]},
        "layout_description": "Local append replay; not a handwritten photograph.", "descriptor": "追加署名字版候选",
    }))
    assert appended.local_path is not None and appended.text_plate is not None
    assert appended.derivation_references[0].asset_version_id == base.id
    with Image.open(base.local_path) as old, Image.open(appended.local_path) as new:
        changed = ImageChops.difference(old.convert("RGB"), new.convert("RGB")).getbbox()
        assert changed is not None and changed[0] >= 136 and changed[2] <= 591 and changed[1] >= 1120 and changed[3] <= 1248
    assert Path(base.local_path).read_bytes() == original_bytes
    asset = next(asset for asset in service.get_project(pid).document.assets if asset.id == aid)
    for key in ("brief", "current_version_id", "approved_version_id"):
        assert asset.model_dump(mode="json")[key] == before[key]
    assert service.list_image_tasks(pid).tasks == []
