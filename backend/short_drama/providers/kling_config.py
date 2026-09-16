"""Independent local Kling generation/upload credentials with explicit fallbacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal, cast

from pydantic import Field
from short_drama.models import DomainModel
from short_drama.providers.h3_config import H3ProviderConfigStore


class KlingProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, description="Nonblank replaces the local generation key; omitted, null or blank preserves it.")
    upload_token: str | None = Field(default=None, description="Optional separate file-upload credential; blank preserves the saved value.")
    clear_api_key: bool = False
    clear_upload_token: bool = False


class KlingProviderConfigResponse(DomainModel):
    provider: Literal["kling"] = "kling"
    model: Literal["kling-lip-sync"] = "kling-lip-sync"
    base_url: str = "https://api.acedata.cloud"
    upload_url: str = "https://platform.acedata.cloud/api/v1/files/"
    has_api_key: bool
    key_source: Literal["KLING_ENVIRONMENT", "LOCAL_CONFIG", "H3_FALLBACK", "MISSING"]
    inherited_key_source: str | None = None
    has_saved_api_key: bool
    has_upload_token: bool
    upload_token_source: Literal["KLING_UPLOAD_ENVIRONMENT", "LOCAL_CONFIG", "API_KEY_FALLBACK", "MISSING"]
    has_saved_upload_token: bool
    credentials_configured: bool
    permissions_verified: Literal[False] = False
    message: str = "此处只确认凭据是否配置，尚未验证上传权限、口型生成权限或账户额度。"


class KlingProviderConfigStore:
    def __init__(self, path: Path, fallback: H3ProviderConfigStore) -> None:
        self.path, self.fallback = path, fallback
        self._lock = RLock()

    def _saved(self) -> dict[str, str]:
        try:
            value = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        data = cast(dict[str, object], value)
        return {key: item.strip() for key in ("api_key", "upload_token") if isinstance(item := data.get(key), str)}

    def api_key(self) -> str:
        with self._lock:
            return os.environ.get("KLING_API_KEY", "").strip() or self._saved().get("api_key", "") or self.fallback.api_key()

    def upload_token(self) -> str:
        with self._lock:
            return os.environ.get("KLING_UPLOAD_TOKEN", "").strip() or self._saved().get("upload_token", "") or self.api_key()

    def credentials(self) -> tuple[str, str]:
        with self._lock:
            return self.api_key(), self.upload_token()

    def response(self) -> KlingProviderConfigResponse:
        with self._lock:
            saved = self._saved()
            key_source = ("KLING_ENVIRONMENT" if os.environ.get("KLING_API_KEY", "").strip() else
                "LOCAL_CONFIG" if saved.get("api_key") else "H3_FALLBACK" if self.fallback.api_key() else "MISSING")
            upload_source = ("KLING_UPLOAD_ENVIRONMENT" if os.environ.get("KLING_UPLOAD_TOKEN", "").strip() else
                "LOCAL_CONFIG" if saved.get("upload_token") else "API_KEY_FALLBACK" if self.api_key() else "MISSING")
            return KlingProviderConfigResponse(has_api_key=bool(self.api_key()), key_source=key_source,
                inherited_key_source=self.fallback.key_source() if key_source == "H3_FALLBACK" else None,
                has_saved_api_key=bool(saved.get("api_key")), has_upload_token=bool(self.upload_token()),
                upload_token_source=upload_source, has_saved_upload_token=bool(saved.get("upload_token")),
                credentials_configured=bool(self.api_key() and self.upload_token()))

    def update(self, request: KlingProviderConfigUpdate) -> KlingProviderConfigResponse:
        with self._lock:
            saved = self._saved()
            updated = dict(saved)
            for key, value, clear in (("api_key", request.api_key, request.clear_api_key),
                                     ("upload_token", request.upload_token, request.clear_upload_token)):
                if clear:
                    updated.pop(key, None)
                elif value and value.strip():
                    updated[key] = value.strip()
            if updated != saved:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(updated, stream, ensure_ascii=False, indent=2)
                temporary.replace(self.path)
            return self.response()
