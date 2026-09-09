"""Local URL and credential configuration for 语言模型."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from short_drama.language_models import (
    LanguageProviderConfigResponse,
    LanguageProviderConfigUpdate,
)
from short_drama.providers.openai_compatible_language_client import (
    is_loopback_openai_url,
)


class LanguageProviderConfigStore:
    """Trusted-desktop settings store; responses never expose the secret."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def api_key(self) -> str:
        dedicated_key = os.environ.get("GPT_SOL_API_KEY", "").strip()
        return (
            dedicated_key
            or self._saved_key()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )

    def base_url(self) -> str:
        dedicated_url = os.environ.get("GPT_SOL_BASE_URL", "").strip()
        return (
            dedicated_url
            or self._saved_base_url()
            or os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1"
        ).rstrip("/")

    def key_source(self) -> str:
        if os.environ.get("GPT_SOL_API_KEY", "").strip():
            return "GPT_SOL_ENVIRONMENT"
        if self._saved_key():
            return "LOCAL_CONFIG"
        if os.environ.get("OPENAI_API_KEY", "").strip():
            return "OPENAI_ENVIRONMENT_FALLBACK"
        return "MISSING"

    def response(self) -> LanguageProviderConfigResponse:
        key = self.api_key()
        base_url = self.base_url()
        return LanguageProviderConfigResponse(
            base_url=base_url,
            has_api_key=bool(key),
            key_source=self.key_source(),
            can_connect=bool(key) or is_loopback_openai_url(base_url),
        )

    def update(
        self,
        request: LanguageProviderConfigUpdate,
    ) -> LanguageProviderConfigResponse:
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

    def _saved_key(self) -> str:
        value = self._saved_payload().get("api_key", "")
        return value.strip() if isinstance(value, str) else ""

    def _saved_base_url(self) -> str:
        value = self._saved_payload().get("base_url", "")
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
