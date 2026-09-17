"""Offline HTTP contract coverage for the GPT Image 2 adapter."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from typing import Any

import pytest
import requests
from PIL import Image

from short_drama.image_models import (
    ImageOperation,
    ImageProviderConfigUpdate,
    ImageProviderRequest,
    ImageReferenceInput,
)
from short_drama.models import ImageReferenceAuthority, ImageReferenceRole
from short_drama.providers.image_config import ImageProviderConfigStore
from short_drama.providers.image_provider import (
    ImageProviderDefiniteError,
    ImageProviderInput,
    ImageProviderSubmissionUnknown,
)
from short_drama.providers.openai_image_client import OpenAIImageClient
from short_drama.providers.openai_image_endpoint import resolve_image_endpoint


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}
        self.content = content
        self.closed = False

    def json(self) -> object:
        return self.payload

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [
            self.content[offset : offset + chunk_size]
            for offset in range(0, len(self.content), chunk_size)
        ]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.responses: list[FakeResponse | Exception] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self) -> FakeResponse:
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._next()


def _client(session: FakeSession) -> OpenAIImageClient:
    return OpenAIImageClient(
        api_key="secret",
        base_url="https://image.example.test/v1/",
        session=session,  # type: ignore[arg-type]
        timeout_seconds=3,
    )


def _success_payload() -> dict[str, object]:
    return {
        "created": 1,
        "data": [
            {
                "b64_json": base64.b64encode(b"image-bytes").decode("ascii"),
                "revised_prompt": "revised",
            }
        ],
        "usage": {
            "input_tokens": 30,
            "input_tokens_details": {"image_tokens": 20, "text_tokens": 10},
            "output_tokens": 100,
            "output_tokens_details": {"image_tokens": 100, "text_tokens": 0},
            "total_tokens": 130,
        },
    }


def _image_bytes(image_format: str) -> bytes:
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    color = (20, 40, 60) if mode == "RGB" else (20, 40, 60, 255)
    image = Image.new(mode, (2, 2), color)
    output = BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def _data_image_url(image_format: str = "PNG") -> str:
    mime_type = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }[image_format]
    encoded = base64.b64encode(_image_bytes(image_format)).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def test_generation_uses_current_gpt_image_json_contract() -> None:
    session = FakeSession()
    session.responses.append(
        FakeResponse(200, _success_payload(), headers={"x-request-id": "req-generation"})
    )
    request = ImageProviderRequest(
        operation=ImageOperation.GENERATE,
        prompt="真人都市悬疑角色定妆",
        size="768x1344",
    )

    result = _client(session).create(request, [])

    call = session.calls[0]
    assert call[0:2] == ("POST", "https://image.example.test/v1/images/generations")
    assert call[2]["headers"] == {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
    }
    assert call[2]["json"] == {
        "model": "gpt-image-2",
        "prompt": "真人都市悬疑角色定妆",
        "n": 1,
        "size": "768x1344",
        "quality": "high",
        "background": "opaque",
        "output_format": "png",
    }
    assert result.provider_request_id == "req-generation"
    assert result.usage is not None
    assert result.usage.output_tokens_details.image_tokens == 100


@pytest.mark.parametrize("raw_usage", [None, {}, {"input_tokens": 30}])
def test_missing_image_usage_is_unknown_not_zero(raw_usage: object) -> None:
    payload = _success_payload()
    if raw_usage is None:
        del payload["usage"]
    else:
        payload["usage"] = raw_usage
    session = FakeSession()
    session.responses.append(FakeResponse(200, payload))
    result = _client(session).create(ImageProviderRequest(operation=ImageOperation.GENERATE, prompt="image"), [])
    if raw_usage in (None, {}):
        assert result.usage is None
    else:
        assert result.usage is not None
        assert result.usage.input_tokens == 30
        assert result.usage.total_tokens is None
        assert result.usage.output_tokens is None
        assert result.usage.input_tokens_details.image_tokens is None
        assert result.usage.input_tokens_details.cached_tokens is None


def test_image_cached_modalities_and_safe_usage_survive_provider_parsing() -> None:
    payload = _success_payload()
    payload["usage"] = {
        "input_tokens": 30, "output_tokens": 100, "total_tokens": 130,
        "input_tokens_details": {"image_tokens": 20, "text_tokens": 10, "cached_tokens": 12,
            "cached_tokens_details": {"image_tokens": 8, "text_tokens": 4},
            "cache_write_tokens": 5, "authorization": "secret-detail"},
        "output_tokens_details": {"image_tokens": 100, "text_tokens": 0},
        "api_key": "secret-usage", "request": {"prompt": "secret-prompt"},
    }
    session = FakeSession()
    session.responses.append(FakeResponse(200, payload))
    result = _client(session).create(ImageProviderRequest(operation=ImageOperation.GENERATE, prompt="image"), [])
    assert result.usage is not None
    assert result.usage.input_tokens_details.cached_tokens == 12
    cached = result.usage.input_tokens_details.cached_tokens_details
    assert cached is not None
    assert (cached.image_tokens, cached.text_tokens) == (8, 4)
    assert result.usage.reported_usage is not None
    assert result.usage.reported_usage["input_tokens_details"] == {
        "image_tokens": 20, "text_tokens": 10, "cached_tokens": 12,
        "cached_tokens_details": {"image_tokens": 8, "text_tokens": 4}, "cache_write_tokens": 5,
    }
    assert "secret-" not in result.usage.model_dump_json()


def test_edit_uses_high_fidelity_multi_image_inputs() -> None:
    session = FakeSession()
    remote_webp = _image_bytes("WEBP")
    session.responses.append(
        FakeResponse(
            200,
            {},
            headers={"Content-Length": str(len(remote_webp))},
            content=remote_webp,
        )
    )
    session.responses.append(FakeResponse(200, _success_payload()))
    request = ImageProviderRequest(
        operation=ImageOperation.EDIT,
        prompt="保留人物身份并放入便利店",
        input_version_ids=["version-1", "version-2"],
    )
    inputs = [
        ImageProviderInput(
            asset_version_id="version-1",
            image_url=_data_image_url(),
        ),
        ImageProviderInput(asset_version_id="version-2", image_url="https://example.com/store.png"),
    ]

    _client(session).create(request, inputs)

    assert session.calls[0][0:2] == ("GET", "https://example.com/store.png")
    call = session.calls[1]
    assert call[1] == "https://image.example.test/v1/images/edits"
    assert call[2]["headers"] == {"Authorization": "Bearer secret"}
    assert "json" not in call[2]
    assert call[2]["data"]["input_fidelity"] == "high"
    files = call[2]["files"]
    assert [item[0] for item in files] == ["image[]", "image[]"]
    assert files[0][1][0] == "input-1.png"
    assert files[0][1][2] == "image/png"
    assert files[1][1][0] == "input-2.png"
    assert files[1][1][2] == "image/png"
    with Image.open(BytesIO(files[1][1][1])) as normalized:
        assert normalized.format == "PNG"


def test_edit_projects_typed_reference_inputs_to_provider_images_in_order() -> None:
    session = FakeSession()
    remote_jpeg = _image_bytes("JPEG")
    session.responses.append(FakeResponse(200, {}, content=remote_jpeg))
    session.responses.append(FakeResponse(200, _success_payload()))
    request = ImageProviderRequest(
        operation=ImageOperation.EDIT,
        prompt="keep the hero identity and use the canteen layout",
        reference_inputs=[
            ImageReferenceInput(
                asset_version_id="hero-version",
                purpose="identity lock",
                subject_alias="hero",
            ),
            ImageReferenceInput(
                asset_version_id="canteen-version",
                purpose="location topology",
                subject_alias="canteen",
            ),
        ],
        primary_input_version_id="hero-version",
    )
    inputs = [
        ImageProviderInput(
            asset_version_id="hero-version",
            image_url=_data_image_url(),
            purpose="identity lock",
            subject_alias="hero",
        ),
        ImageProviderInput(
            asset_version_id="canteen-version",
            image_url="https://example.com/canteen.png",
            purpose="location topology",
            subject_alias="canteen",
        ),
    ]

    _client(session).create(request, inputs)

    call = session.calls[1]
    payload = call[2]["data"]
    assert [item[0] for item in call[2]["files"]] == ["image[]", "image[]"]
    assert call[2]["files"][1][1][2] == "image/jpeg"
    assert payload["prompt"].startswith(
        "keep the hero identity and use the canteen layout\n\n"
        "有序参考图职责（按上传顺序）：\n"
        "参考图1（主体别名：hero）"
    )
    assert "角色：SUPPORTING_VISUAL；权威级别：SUPPORTING" in payload["prompt"]
    assert "只在其已声明的职责范围内作为辅助参考" in payload["prompt"]
    assert payload["prompt"].count("有序参考图职责") == 1
    assert request.prompt == "keep the hero identity and use the canteen layout"


def test_reference_generation_uses_edit_endpoint_and_semantic_reference_rules() -> None:
    session = FakeSession()
    for image_format in ("PNG", "JPEG", "WEBP"):
        session.responses.append(
            FakeResponse(200, {}, content=_image_bytes(image_format))
        )
    session.responses.append(FakeResponse(200, _success_payload()))
    request = ImageProviderRequest(
        operation=ImageOperation.REFERENCE_GENERATE,
        prompt="生成男主角双区标准卡",
        reference_inputs=[
            ImageReferenceInput(
                asset_version_id="uploaded-hero",
                purpose="用户指定这张照片就是男主角",
                subject_alias="hero",
                reference_role=ImageReferenceRole.SUBJECT_IDENTITY,
                reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
            ),
            ImageReferenceInput(
                asset_version_id="layout-guide",
                purpose="双区角色卡版式",
                reference_role=ImageReferenceRole.LAYOUT_GUIDE,
                reference_authority=ImageReferenceAuthority.GUIDE_ONLY,
            ),
            ImageReferenceInput(
                asset_version_id="style-guide",
                purpose="真人校园短剧质感",
                reference_role=ImageReferenceRole.STYLE,
                reference_authority=ImageReferenceAuthority.GUIDE_ONLY,
            ),
        ],
        primary_input_version_id="uploaded-hero",
    )
    inputs = [
        ImageProviderInput(
            asset_version_id=reference.asset_version_id,
            image_url=f"https://example.com/{index}.png",
        )
        for index, reference in enumerate(request.reference_inputs, start=1)
    ]

    _client(session).create(request, inputs)

    call = session.calls[3]
    assert call[1] == "https://image.example.test/v1/images/edits"
    assert call[2]["data"]["input_fidelity"] == "high"
    assert [item[0] for item in call[2]["files"]] == [
        "image[]",
        "image[]",
        "image[]",
    ]
    assert [item[1][2] for item in call[2]["files"]] == [
        "image/png",
        "image/jpeg",
        "image/png",
    ]
    provider_prompt = call[2]["data"]["prompt"]
    assert "不得改成另一人" in provider_prompt
    assert "只使用版式、分区和构图关系" in provider_prompt
    assert "不得复制其中的人物身份或具体主体" in provider_prompt


def test_image_operation_contract_distinguishes_generation_reference_and_edit() -> None:
    reference = ImageReferenceInput(
        asset_version_id="uploaded-hero",
        purpose="identity",
    )
    with pytest.raises(ValueError, match="does not accept reference"):
        ImageProviderRequest(
            operation=ImageOperation.GENERATE,
            prompt="generate",
            reference_inputs=[reference],
        )
    with pytest.raises(ValueError, match="requires at least one reference"):
        ImageProviderRequest(
            operation=ImageOperation.REFERENCE_GENERATE,
            prompt="derive first master",
        )


def test_image_size_contract_matches_azure_gpt_image_2_bounds() -> None:
    assert ImageProviderRequest(
        operation=ImageOperation.GENERATE,
        prompt="sixteen by nine",
        size="2048x1152",
    ).size == "2048x1152"
    assert ImageProviderRequest(
        operation=ImageOperation.GENERATE,
        prompt="minimum pixels",
        size="1024x640",
    ).size == "1024x640"
    assert ImageProviderRequest(
        operation=ImageOperation.GENERATE,
        prompt="maximum pixels",
        size="3840x2160",
    ).size == "3840x2160"

    with pytest.raises(ValueError, match="long edge"):
        ImageProviderRequest(
            operation=ImageOperation.GENERATE,
            prompt="too long",
            size="3856x2160",
        )
    with pytest.raises(ValueError, match="multiples of 16"):
        ImageProviderRequest(
            operation=ImageOperation.GENERATE,
            prompt="unaligned full hd",
            size="1920x1080",
        )
    with pytest.raises(ValueError, match="pixel count"):
        ImageProviderRequest(
            operation=ImageOperation.GENERATE,
            prompt="too few pixels",
            size="1024x624",
        )
    with pytest.raises(ValueError, match="pixel count"):
        ImageProviderRequest(
            operation=ImageOperation.GENERATE,
            prompt="too many pixels",
            size="3840x2176",
        )


def test_azure_masked_edit_uses_multipart_and_applies_mask_to_primary() -> None:
    session = FakeSession()
    session.responses.append(FakeResponse(200, _success_payload()))
    client = OpenAIImageClient(
        api_key="azure-secret",
        base_url=(
            "https://resource.openai.azure.com/openai/deployments/gpt-image-2/"
            "images/generations?api-version=2025-04-01-preview"
        ),
        session=session,  # type: ignore[arg-type]
    )
    request = ImageProviderRequest(
        operation=ImageOperation.EDIT,
        prompt="add the actor while preserving the door",
        input_version_ids=["primary", "actor"],
        primary_input_version_id="primary",
        mask_version_id="mask",
    )
    inputs = [
        ImageProviderInput(
            asset_version_id="primary",
            image_url=_data_image_url(),
        ),
        ImageProviderInput(
            asset_version_id="actor",
            image_url=_data_image_url("JPEG"),
        ),
        ImageProviderInput(
            asset_version_id="mask",
            image_url=_data_image_url(),
        ),
    ]

    client.create(request, inputs)

    call = session.calls[0]
    assert call[0] == "POST"
    assert call[1].endswith("/images/edits?api-version=2025-04-01-preview")
    assert call[2]["headers"] == {"api-key": "azure-secret"}
    assert "json" not in call[2]
    assert call[2]["data"]["input_fidelity"] == "high"
    files = call[2]["files"]
    assert [item[0] for item in files] == ["image[]", "image[]", "mask"]
    assert files[0][1][1] == _image_bytes("PNG")
    assert files[1][1][2] == "image/jpeg"
    assert files[-1][1][2] == "image/png"


def test_edit_rejects_remote_images_larger_than_20_mib_before_submission() -> None:
    session = FakeSession()
    session.responses.append(
        FakeResponse(
            200,
            {},
            headers={"Content-Length": str(20 * 1024 * 1024 + 1)},
        )
    )
    request = ImageProviderRequest(
        operation=ImageOperation.EDIT,
        prompt="keep the actor",
        input_version_ids=["actor"],
    )

    with pytest.raises(ValueError, match="20 MiB"):
        _client(session).create(
            request,
            [
                ImageProviderInput(
                    asset_version_id="actor",
                    image_url="https://example.com/too-large.png",
                )
            ],
        )

    assert [call[0] for call in session.calls] == ["GET"]


def test_validation_and_error_classification() -> None:
    session = FakeSession()
    session.responses.append(FakeResponse(200, {"id": "gpt-image-2"}))
    client = _client(session)
    client.validate_credentials()
    assert session.calls[0][0:2] == (
        "GET",
        "https://image.example.test/v1/models/gpt-image-2",
    )

    session.responses.append(
        FakeResponse(400, {"error": {"code": "invalid_image", "message": "bad image"}})
    )
    with pytest.raises(ImageProviderDefiniteError) as definite:
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])
    assert definite.value.code == "invalid_image"
    assert definite.value.same_request_retryable is False

    session.responses.append(
        FakeResponse(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "try later"}},
        )
    )
    with pytest.raises(ImageProviderDefiniteError) as transient:
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])
    assert transient.value.code == "rate_limit_exceeded"
    assert transient.value.same_request_retryable is True

    session.responses.append(FakeResponse(408, {"error": {"message": "timed out"}}))
    with pytest.raises(ImageProviderSubmissionUnknown):
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])

    session.responses.append(FakeResponse(500, {"error": {"message": "server error"}}))
    with pytest.raises(ImageProviderSubmissionUnknown):
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])

    session.responses.append(requests.ConnectionError("offline"))
    with pytest.raises(ImageProviderSubmissionUnknown):
        client.create(ImageProviderRequest(operation="GENERATE", prompt="x"), [])


def test_azure_full_endpoint_uses_api_key_and_upgrades_old_api_version() -> None:
    session = FakeSession()
    session.responses.append(
        FakeResponse(
            200,
            _success_payload(),
            headers={"apim-request-id": "azure-request-1"},
        )
    )
    client = OpenAIImageClient(
        api_key="azure-secret",
        base_url=(
            "https://datasetannotator-openai.openai.azure.com/openai/deployments/"
            "gpt-image-2/images/generations?api-version=2024-02-01"
        ),
        session=session,  # type: ignore[arg-type]
    )

    result = client.create(
        ImageProviderRequest(operation=ImageOperation.GENERATE, prompt="都市短剧定妆"),
        [],
    )

    call = session.calls[0]
    assert call[0:2] == (
        "POST",
        "https://datasetannotator-openai.openai.azure.com/openai/deployments/"
        "gpt-image-2/images/generations?api-version=2025-04-01-preview",
    )
    assert call[2]["headers"] == {
        "api-key": "azure-secret",
        "Content-Type": "application/json",
    }
    assert result.provider_request_id == "azure-request-1"
    endpoint = resolve_image_endpoint(client.base_url)
    assert endpoint.edit_url.endswith(
        "/images/edits?api-version=2025-04-01-preview"
    )


def test_azure_validation_uses_a_no_generation_invalid_payload() -> None:
    session = FakeSession()
    session.responses.append(
        FakeResponse(
            400,
            {"error": {"code": "InvalidPayload", "message": "prompt is required"}},
        )
    )
    client = OpenAIImageClient(
        api_key="azure-secret",
        base_url=(
            "https://resource.openai.azure.com/openai/deployments/gpt-image-2/"
            "images/generations?api-version=2025-04-01-preview"
        ),
        session=session,  # type: ignore[arg-type]
    )

    client.validate_credentials()

    call = session.calls[0]
    assert call[0] == "POST"
    assert call[2]["json"] == {"model": "gpt-image-2", "prompt": None, "n": 0}


def test_image_config_persists_the_normalized_azure_endpoint(tmp_path: Path) -> None:
    store = ImageProviderConfigStore(tmp_path / "image-provider.json")
    response = store.update(
        request=ImageProviderConfigUpdate(
            api_key="azure-secret",
            base_url=(
                "https://resource.openai.azure.com/openai/deployments/gpt-image-2/"
                "images/generations?api-version=2024-02-01"
            ),
        )
    )

    assert response.base_url.endswith("api-version=2025-04-01-preview")
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["base_url"] == response.base_url
