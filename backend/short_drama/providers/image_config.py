"""Local GPT Image provider credential and pricing configuration."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from short_drama.image_models import (
    ImagePricingSnapshot,
    ImageProviderConfigResponse,
    ImageProviderConfigUpdate,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ImageProviderConfigStore:
    """Trusted-desktop API-key store; responses never return the secret."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def api_key(self) -> str:
        dedicated_key = os.environ.get("GPT_IMAGE_API_KEY", "").strip()
        return (
            dedicated_key
            or self._saved_key()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )

    def base_url(self) -> str:
        dedicated_url = os.environ.get("GPT_IMAGE_BASE_URL", "").strip()
        return (
            dedicated_url
            or self._saved_base_url()
            or os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1"
        ).rstrip("/")

    def key_source(self) -> str:
        if os.environ.get("GPT_IMAGE_API_KEY", "").strip():
            return "GPT_IMAGE_ENVIRONMENT"
        if self._saved_key():
            return "LOCAL_CONFIG"
        if os.environ.get("OPENAI_API_KEY", "").strip():
            return "OPENAI_ENVIRONMENT_FALLBACK"
        return "MISSING"

    def response(self) -> ImageProviderConfigResponse:
        key = self.api_key()
        return ImageProviderConfigResponse(
            base_url=self.base_url(),
            has_api_key=bool(key),
            key_source=self.key_source(),
            can_generate=bool(key),
            pricing=self.pricing_snapshot(),
        )

    def update(self, request: ImageProviderConfigUpdate) -> ImageProviderConfigResponse:
        current_key = self._saved_key()
        current_base_url = self._saved_base_url() or "https://api.openai.com/v1"
        if request.clear_api_key:
            current_key = ""
        elif request.api_key is not None:
            current_key = request.api_key.strip()
        if request.base_url is not None:
            current_base_url = request.base_url.strip().rstrip("/")
        self._write_saved_config(current_key, current_base_url)
        return self.response()

    def pricing_snapshot(self) -> ImagePricingSnapshot:
        return ImagePricingSnapshot(captured_at=_utc_now())

    def _saved_key(self) -> str:
        payload = self._saved_payload()
        key = payload.get("api_key", "")
        return key.strip() if isinstance(key, str) else ""

    def _saved_base_url(self) -> str:
        payload = self._saved_payload()
        value = payload.get("base_url", "")
        return value.strip().rstrip("/") if isinstance(value, str) else ""

    def _saved_payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            payload = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return cast(dict[str, object], payload)

    def _write_saved_config(self, api_key: str, base_url: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"api_key": api_key, "base_url": base_url.rstrip("/")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)
