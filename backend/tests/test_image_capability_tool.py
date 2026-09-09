"""Agent-facing image capability discovery stays secret-free and offline."""

from __future__ import annotations

from pathlib import Path

from short_drama.image_capabilities import ImageCapabilityOperation
from short_drama.image_models import ImageOperation, ImageProviderConfigUpdate
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus, ToolEffect


def _provider_must_not_be_created(_api_key: str, _base_url: str):
    raise AssertionError("image.capability must not construct or call a Provider")


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        image_provider_factory=_provider_must_not_be_created,
    )


def test_image_capability_is_read_only_secret_free_and_offline(tmp_path: Path) -> None:
    service = _service(tmp_path)
    secret = "azure-image-secret"
    service.update_image_provider_config(
        ImageProviderConfigUpdate(
            api_key=secret,
            base_url=(
                "https://example.openai.azure.com/openai/deployments/"
                "gpt-image-2/images/generations?api-version=2025-04-01-preview"
            ),
        )
    )

    descriptor = service.tool_registry.describe("image.capability")
    assert descriptor.effect == ToolEffect.READ
    assert descriptor.idempotent is True

    result = service.tool_registry.invoke("image.capability", {})
    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert result.output["provider"] == "openai-compatible"
    assert result.output["model"] == "gpt-image-2"
    assert result.output["is_azure"] is True
    assert result.output["credential_configured"] is True
    assert result.output["can_generate"] is True
    assert secret not in str(result.output)
    assert "base_url" not in result.output
    assert result.output["supports_multiple_references"] is True
    assert result.output["maximum_reference_images"] == 16
    assert result.output["transparent_background"] == {
        "accepted_by_adapter": False,
        "allowed_output_formats": [],
        "note": (
            "当前 Azure 适配器会在提交 Provider 前拒绝透明背景，"
            "请改用不透明背景。"
        ),
    }
    assert result.output["size"]["dimension_multiple"] == 16
    assert result.output["size"]["minimum_dimension_pixels"] == 16
    assert result.output["size"]["minimum_total_pixels"] == 655_360
    assert result.output["size"]["minimum_aspect_ratio"] == "1:3"
    assert result.output["size"]["maximum_aspect_ratio"] == "3:1"
    assert result.output["size"]["maximum_edge_pixels"] == 3_840
    assert result.output["size"]["maximum_total_pixels"] == 8_294_400
    assert result.output["size"]["layout_aspect_ratios"]["PROP_MASTER_SINGLE"] == "1:1"
    assert result.output["size"]["note"] == (
        "当前 GPT Image 请求适配器在提交 Provider 前强制："
        "宽高均为 16 的倍数、宽高比介于 1:3 至 3:1、"
        "最长边不超过 3840，总像素数介于 "
        "655360 至 8294400。"
    )

    operations = {
        item["operation"]: item for item in result.output["operations"]
    }
    assert set(operations) == {
        ImageCapabilityOperation.GENERATE.value,
        ImageCapabilityOperation.REFERENCE_GENERATE.value,
        ImageCapabilityOperation.EDIT.value,
    }
    assert operations["GENERATE"]["supported"] is True
    assert operations["EDIT"]["supported"] is True
    assert operations["EDIT"]["uses_high_input_fidelity"] is True
    assert operations["EDIT"]["supports_mask"] is True
    assert operations["REFERENCE_GENERATE"]["supported"] is (
        "REFERENCE_GENERATE" in {item.value for item in ImageOperation}
    )


def test_image_capability_reports_missing_credentials_and_openai_transparency(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in (
        "GPT_IMAGE_API_KEY",
        "GPT_IMAGE_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    service = _service(tmp_path)

    result = service.tool_registry.invoke("image.capability", {})

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output is not None
    assert result.output["is_azure"] is False
    assert result.output["credential_configured"] is False
    assert result.output["can_generate"] is False
    assert result.output["transparent_background"]["accepted_by_adapter"] is True
    assert result.output["transparent_background"]["allowed_output_formats"] == [
        "png",
        "webp",
    ]
