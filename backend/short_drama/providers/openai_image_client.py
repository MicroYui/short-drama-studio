"""OpenAI GPT Image 2 adapter for the provider-neutral ImageProvider boundary."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import cast
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from requests import Response, Session

from short_drama.image_models import ImageOperation, ImageProviderRequest, ImageUsage
from short_drama.models import ImageReferenceAuthority, ImageReferenceRole
from short_drama.providers.image_provider import (
    ImageProviderDefiniteError,
    ImageProviderInput,
    ImageProviderResult,
    ImageProviderSubmissionUnknown,
)
from short_drama.providers.openai_image_endpoint import resolve_image_endpoint


_MAX_EDIT_IMAGE_BYTES = 20 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_DEFINITE_TRANSIENT_STATUS_CODES = frozenset({408, 409, 429})


class OpenAIImageClient:
    """Small requests-based client for GPT Image generation and edit endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        session: Session | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.endpoint = resolve_image_endpoint(base_url)
        self.base_url = self.endpoint.configured_url
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def validate_credentials(self) -> None:
        if self.endpoint.is_azure:
            self._validate_azure_credentials()
            return
        if self.endpoint.model_url is None:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image endpoint did not provide a model-validation URL"
            )
        try:
            response = self.session.get(
                self.endpoint.model_url,
                headers=self._headers(),
                timeout=30.0,
            )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI credential validation could not reach the API"
            ) from exc
        self._raise_error(response, ambiguous_server_error=False)

    def _validate_azure_credentials(self) -> None:
        """Use a schema-invalid request so Azure checks auth/route without generating."""

        try:
            response = self.session.post(
                self.endpoint.generation_url,
                headers=self._headers(),
                json={"model": "gpt-image-2", "prompt": None, "n": 0},
                timeout=30.0,
            )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "Azure GPT Image credential validation could not reach the API"
            ) from exc
        if response.status_code in {400, 422}:
            code, message = self._error_details(response)
            fatal_detail = f"{code} {message}".lower()
            if any(
                marker in fatal_detail
                for marker in (
                    "api-version",
                    "api version",
                    "deploymentnotfound",
                    "deployment not found",
                    "operationnotsupported",
                    "operation not supported",
                )
            ):
                self._raise_error(response, ambiguous_server_error=False)
            return
        self._raise_error(response, ambiguous_server_error=False)

    def create(
        self,
        request: ImageProviderRequest,
        inputs: list[ImageProviderInput],
    ) -> ImageProviderResult:
        if request.operation == ImageOperation.GENERATE and inputs:
            raise ValueError("Image generation does not accept edit inputs")
        if request.operation in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        } and not inputs:
            raise ValueError(
                f"{request.operation.value} requires at least one input image"
            )
        visual_input_ids = [
            item.asset_version_id
            for item in inputs
            if item.asset_version_id != request.mask_version_id
        ]
        expected_input_ids = request.ordered_input_version_ids()
        if (
            request.primary_input_version_id is not None
            and not request.reference_inputs
        ):
            expected_input_ids = [
                request.primary_input_version_id,
                *(
                    item
                    for item in expected_input_ids
                    if item != request.primary_input_version_id
                ),
            ]
        if visual_input_ids != expected_input_ids:
            raise ValueError(
                "Provider image inputs must preserve the declared reference order"
            )

        provider_prompt = request.prompt
        if request.operation in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        } and request.reference_inputs:
            reference_lines: list[str] = []
            for index, reference in enumerate(request.reference_inputs, start=1):
                alias = (
                    f"（主体别名：{reference.subject_alias}）"
                    if reference.subject_alias is not None
                    else ""
                )
                semantic_rule = self._reference_semantic_rule(
                    reference.reference_role,
                    reference.reference_authority,
                )
                reference_lines.append(
                    f"参考图{index}{alias}"
                    f"（角色：{reference.reference_role.value}；"
                    f"权威级别：{reference.reference_authority.value}）："
                    f"{reference.purpose}。{semantic_rule}"
                )
            provider_prompt = (
                f"{request.prompt}\n\n有序参考图职责（按上传顺序）：\n"
                + "\n".join(reference_lines)
            )

        payload: dict[str, object] = {
            "model": "gpt-image-2",
            "prompt": provider_prompt,
            "n": 1,
            "size": request.size,
            "quality": request.quality.value,
            "background": request.background.value,
            "output_format": request.output_format.value,
        }
        endpoint = self.endpoint.generation_url
        edit_request: tuple[
            dict[str, str],
            dict[str, str],
            list[tuple[str, tuple[str, bytes, str]]],
        ] | None = None
        if request.operation in {
            ImageOperation.REFERENCE_GENERATE,
            ImageOperation.EDIT,
        }:
            endpoint = self.endpoint.edit_url
            payload["input_fidelity"] = "high"
            mask = next(
                (
                    item
                    for item in inputs
                    if item.asset_version_id == request.mask_version_id
                ),
                None,
            )
            visual_inputs = [
                item
                for item in inputs
                if item.asset_version_id != request.mask_version_id
            ]
            if not visual_inputs:
                raise ValueError("Image editing requires at least one visual input image")
            if request.mask_version_id is not None:
                if mask is None:
                    raise ValueError("Image edit mask input is missing")
            edit_request = self._edit_request_parts(
                payload,
                visual_inputs,
                mask,
            )

        try:
            if edit_request is None:
                response = self.session.post(
                    endpoint,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            else:
                headers, form, files = edit_request
                response = self.session.post(
                    endpoint,
                    headers=headers,
                    data=form,
                    files=files,
                    timeout=self.timeout_seconds,
                )
        except requests.RequestException as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image submission transport outcome is unknown"
            ) from exc
        self._raise_error(response, ambiguous_server_error=True)
        return self._parse_result(response, request)

    @staticmethod
    def _reference_semantic_rule(
        role: ImageReferenceRole,
        authority: ImageReferenceAuthority,
    ) -> str:
        if (
            role == ImageReferenceRole.SUBJECT_IDENTITY
            and authority == ImageReferenceAuthority.AUTHORITATIVE
        ):
            return (
                "这是同一人物的权威身份基准；必须保留面部几何、肤色、"
                "发型、年龄感和身体比例，不得改成另一人。"
            )
        if role == ImageReferenceRole.LAYOUT_GUIDE:
            return (
                "只使用版式、分区和构图关系；不得复制其中的人物身份、"
                "服装或画面风格。"
            )
        if role == ImageReferenceRole.STYLE:
            return (
                "只提取材质、色彩、光线和媒介风格；不得复制其中的人物"
                "身份或具体主体。"
            )
        if authority == ImageReferenceAuthority.AUTHORITATIVE:
            return "这是用户确认的视觉事实，不得擅自替换或反转其声明内容。"
        if authority == ImageReferenceAuthority.GUIDE_ONLY:
            return "只用于已声明的辅助职责，不得扩展为身份或内容事实。"
        return "只在其已声明的职责范围内作为辅助参考。"

    def _parse_result(
        self,
        response: Response,
        request: ImageProviderRequest,
    ) -> ImageProviderResult:
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response was not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ImageProviderSubmissionUnknown("OpenAI image response was not an object")
        typed = cast(dict[str, object], payload)
        raw_data = typed.get("data")
        if not isinstance(raw_data, list) or not raw_data or not isinstance(raw_data[0], dict):
            raise ImageProviderSubmissionUnknown("OpenAI image response did not contain image data")
        first = cast(dict[str, object], raw_data[0])
        encoded = first.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response did not contain a base64 image"
            )
        try:
            base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ImageProviderSubmissionUnknown(
                "OpenAI image response contained invalid base64 data"
            ) from exc

        usage = ImageUsage()
        raw_usage = typed.get("usage")
        if isinstance(raw_usage, dict):
            try:
                usage = ImageUsage.model_validate(raw_usage)
            except ValidationError as exc:
                raise ImageProviderSubmissionUnknown(
                    "OpenAI image response contained invalid usage data"
                ) from exc
        revised_prompt = first.get("revised_prompt")
        mime_type = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }[request.output_format.value]
        return ImageProviderResult(
            image_base64=encoded,
            mime_type=mime_type,
            provider_request_id=(
                response.headers.get("x-request-id")
                or response.headers.get("apim-request-id")
            ),
            revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
            usage=usage,
        )

    def _headers(self) -> dict[str, str]:
        return {**self._auth_headers(), "Content-Type": "application/json"}

    def _auth_headers(self) -> dict[str, str]:
        if self.endpoint.is_azure:
            return {"api-key": self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _edit_request_parts(
        self,
        payload: dict[str, object],
        inputs: list[ImageProviderInput],
        mask: ImageProviderInput | None,
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        list[tuple[str, tuple[str, bytes, str]]],
    ]:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, item in enumerate(inputs, start=1):
            image_bytes, mime_type, suffix = self._prepare_image_file(item.image_url)
            files.append(
                ("image[]", (f"input-{index}.{suffix}", image_bytes, mime_type))
            )
        if mask is not None:
            mask_bytes, mask_mime, _ = self._prepare_image_file(
                mask.image_url,
                require_png=True,
            )
            files.append(("mask", ("mask.png", mask_bytes, mask_mime)))
        form = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in payload.items()
        }
        return self._auth_headers(), form, files

    def _prepare_image_file(
        self,
        value: str,
        *,
        require_png: bool = False,
    ) -> tuple[bytes, str, str]:
        if value.startswith("data:"):
            image_bytes = self._decode_data_image(value)
        elif urlsplit(value).scheme.lower() in {"http", "https"}:
            image_bytes = self._download_image(value)
        else:
            raise ValueError(
                "Image edit inputs must be base64 data URLs or HTTP(S) URLs"
            )
        return self._normalize_image(image_bytes, require_png=require_png)

    @staticmethod
    def _decode_data_image(value: str) -> bytes:
        try:
            header, encoded = value.split(",", maxsplit=1)
        except ValueError as exc:
            raise ValueError("Image edit input contains an invalid data URL") from exc
        if not header.startswith("data:image/") or ";base64" not in header:
            raise ValueError("Image edit input contains an invalid data URL")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("Image edit input contains invalid base64") from exc
        if len(image_bytes) > _MAX_EDIT_IMAGE_BYTES:
            raise ValueError("Image edit input exceeds the 20 MiB size limit")
        return image_bytes

    def _download_image(self, url: str) -> bytes:
        try:
            response = self.session.get(
                url,
                stream=True,
                timeout=min(self.timeout_seconds, 30.0),
            )
        except requests.RequestException as exc:
            raise ValueError("Image edit input could not be downloaded") from exc
        try:
            if response.status_code >= 400:
                raise ValueError(
                    f"Image edit input download failed with HTTP {response.status_code}"
                )
            raw_content_length = response.headers.get("Content-Length")
            if raw_content_length is not None:
                try:
                    content_length = int(raw_content_length)
                except ValueError:
                    content_length = 0
                if content_length > _MAX_EDIT_IMAGE_BYTES:
                    raise ValueError("Image edit input exceeds the 20 MiB size limit")

            image_bytes = bytearray()
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                image_bytes.extend(chunk)
                if len(image_bytes) > _MAX_EDIT_IMAGE_BYTES:
                    raise ValueError("Image edit input exceeds the 20 MiB size limit")
            return bytes(image_bytes)
        finally:
            response.close()

    @staticmethod
    def _normalize_image(
        image_bytes: bytes,
        *,
        require_png: bool,
    ) -> tuple[bytes, str, str]:
        try:
            with Image.open(BytesIO(image_bytes)) as opened:
                opened.load()
                image_format = opened.format
                if image_format == "WEBP":
                    normalized = BytesIO()
                    opened.save(
                        normalized,
                        format="PNG",
                        optimize=False,
                        compress_level=9,
                    )
                    png_bytes = normalized.getvalue()
                    if len(png_bytes) > _MAX_EDIT_IMAGE_BYTES:
                        raise ValueError(
                            "Normalized image edit input exceeds the 20 MiB size limit"
                        )
                    return png_bytes, "image/png", "png"
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("Image edit input is not a valid PNG, JPEG, or WebP") from exc

        if image_format == "PNG":
            return image_bytes, "image/png", "png"
        if image_format == "JPEG" and not require_png:
            return image_bytes, "image/jpeg", "jpg"
        if require_png:
            raise ValueError("Image edit mask must be a PNG image")
        raise ValueError("Image edit input is not a valid PNG, JPEG, or WebP")

    @staticmethod
    def _raise_error(response: Response, *, ambiguous_server_error: bool) -> None:
        if response.status_code < 400:
            return
        code, message = OpenAIImageClient._error_details(response)
        if ambiguous_server_error and (
            response.status_code == 408 or response.status_code >= 500
        ):
            raise ImageProviderSubmissionUnknown(message)
        raise ImageProviderDefiniteError(
            response.status_code,
            code,
            message,
            same_request_retryable=(
                response.status_code in _DEFINITE_TRANSIENT_STATUS_CODES
                or (response.status_code >= 500 and not ambiguous_server_error)
            ),
        )

    @staticmethod
    def _error_details(response: Response) -> tuple[str, str]:
        message = response.text or f"OpenAI request failed with {response.status_code}"
        code = str(response.status_code)
        try:
            payload = cast(object, response.json())
            if isinstance(payload, dict):
                error = cast(dict[str, object], payload).get("error")
                if isinstance(error, dict):
                    typed_error = cast(dict[str, object], error)
                    raw_code = typed_error.get("code") or typed_error.get("type")
                    raw_message = typed_error.get("message")
                    if isinstance(raw_code, (str, int)):
                        code = str(raw_code)
                    if isinstance(raw_message, str):
                        message = raw_message
        except ValueError:
            pass
        return code, message
