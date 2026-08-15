"""Local AceData credential and MiniMax-H3 pricing configuration."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from short_drama.h3_models import (
    H3PricingSnapshot,
    H3ProviderConfigResponse,
    H3ProviderConfigUpdate,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class H3ProviderConfigStore:
    """Small trusted-desktop config store; API responses never expose the key."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def api_key(self) -> str:
        for variable in ("ACEDATACLOUD_API_KEY", "MINIMAX_API_KEY"):
            environment_key = os.environ.get(variable, "").strip()
            if environment_key:
                return environment_key
        return self._saved_key()

    def key_source(self) -> str:
        if os.environ.get("ACEDATACLOUD_API_KEY", "").strip():
            return "ACEDATACLOUD_ENVIRONMENT"
        if os.environ.get("MINIMAX_API_KEY", "").strip():
            return "MINIMAX_ENVIRONMENT_LEGACY"
        if self._saved_key():
            return "LOCAL_CONFIG"
        return "MISSING"

    def response(self) -> H3ProviderConfigResponse:
        key = self.api_key()
        return H3ProviderConfigResponse(
            has_api_key=bool(key),
            key_source=self.key_source(),
            can_submit=bool(key),
            pricing=self.pricing_snapshot(),
        )

    def update(self, request: H3ProviderConfigUpdate) -> H3ProviderConfigResponse:
        if request.clear_api_key:
            self._write_saved_key("")
        elif request.api_key is not None:
            self._write_saved_key(request.api_key.strip())
        return self.response()

    def pricing_snapshot(self) -> H3PricingSnapshot:
        return H3PricingSnapshot(captured_at=_utc_now())

    def _saved_key(self) -> str:
        if not self.path.exists():
            return ""
        try:
            payload = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        key = cast(dict[str, object], payload).get("api_key", "")
        return key.strip() if isinstance(key, str) else ""

    def _write_saved_key(self, api_key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"api_key": api_key}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)
