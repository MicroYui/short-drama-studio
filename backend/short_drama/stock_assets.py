"""Searchable external stock-photo sources for the Asset Agent.

The stock layer is intentionally small: it returns source-aware candidates and
lets the existing AssetVersion workbench own selection, approval, and GPT Image
derivation.  A search result is advice, never an automatic license or identity
approval.
"""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

import requests
from pydantic import Field, field_validator
from requests import Session

from short_drama.models import AssetKind, DomainModel


class StockAssetProvider(StrEnum):
    OPENVERSE = "OPENVERSE"
    PEXELS = "PEXELS"
    PIXABAY = "PIXABAY"


class StockAssetOrientation(StrEnum):
    ANY = "ANY"
    PORTRAIT = "PORTRAIT"
    LANDSCAPE = "LANDSCAPE"
    SQUARE = "SQUARE"


class StockAssetRecommendedUse(StrEnum):
    DIRECT_OR_DERIVE = "DIRECT_OR_DERIVE"
    VISUAL_GROUNDING_NEW_IDENTITY = "VISUAL_GROUNDING_NEW_IDENTITY"


class StockAssetImportMode(StrEnum):
    AUTO = "AUTO"
    DIRECT = "DIRECT"
    DERIVE = "DERIVE"


class StockProviderConfigResponse(DomainModel):
    provider: StockAssetProvider
    base_url: str
    has_api_key: bool
    key_source: str
    can_search: bool


class StockProviderConfigListResponse(DomainModel):
    providers: list[StockProviderConfigResponse]


class StockProviderConfigUpdate(DomainModel):
    api_key: str | None = Field(default=None, max_length=4096)
    base_url: str | None = Field(default=None, min_length=1, max_length=8192)
    clear_api_key: bool = False


class StockAssetSearchRequest(DomainModel):
    provider: StockAssetProvider = StockAssetProvider.OPENVERSE
    query: str = Field(min_length=1, max_length=1000)
    asset_kind: AssetKind
    orientation: StockAssetOrientation = StockAssetOrientation.PORTRAIT
    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=12, ge=1, le=40)
    commercial_and_modifiable_only: bool = True


class StockAssetCandidate(DomainModel):
    provider: StockAssetProvider
    provider_asset_id: str = Field(min_length=1, max_length=500)
    asset_kind: AssetKind
    title: str = Field(min_length=1, max_length=1000)
    source_page_url: str = Field(min_length=1, max_length=8192)
    image_url: str = Field(min_length=1, max_length=8192)
    preview_url: str = Field(min_length=1, max_length=8192)
    creator: str | None = Field(default=None, max_length=500)
    creator_url: str | None = Field(default=None, max_length=8192)
    license_name: str = Field(min_length=1, max_length=200)
    license_url: str | None = Field(default=None, max_length=8192)
    attribution: str | None = Field(default=None, max_length=4000)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list[str], max_length=48)
    recommended_use: StockAssetRecommendedUse
    advisory_notes: list[str] = Field(default_factory=list[str], max_length=12)

    @field_validator(
        "source_page_url",
        "image_url",
        "preview_url",
        "creator_url",
        "license_url",
    )
    @classmethod
    def require_http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("Stock asset URLs must use HTTP or HTTPS")
        return value


class StockAssetSearchResponse(DomainModel):
    provider: StockAssetProvider
    query: str
    page: int
    candidates: list[StockAssetCandidate]
    warnings: list[str] = Field(default_factory=list[str], max_length=12)


class StockAssetImportRequest(DomainModel):
    target_asset_id: str = Field(min_length=1, max_length=240)
    candidate: StockAssetCandidate
    descriptor: str = Field(min_length=1, max_length=4000)
    parent_version_id: str | None = None
    import_mode: StockAssetImportMode = StockAssetImportMode.AUTO


class StockAssetSearchError(RuntimeError):
    pass


class StockAssetSearchClient(Protocol):
    def search(self, request: StockAssetSearchRequest) -> StockAssetSearchResponse: ...


_DEFAULT_BASE_URLS: dict[StockAssetProvider, str] = {
    StockAssetProvider.OPENVERSE: "https://api.openverse.org/v1/images",
    StockAssetProvider.PEXELS: "https://api.pexels.com/v1",
    StockAssetProvider.PIXABAY: "https://pixabay.com/api",
}

_ENV_KEYS: dict[StockAssetProvider, str] = {
    StockAssetProvider.OPENVERSE: "OPENVERSE_API_KEY",
    StockAssetProvider.PEXELS: "PEXELS_API_KEY",
    StockAssetProvider.PIXABAY: "PIXABAY_API_KEY",
}


class StockProviderConfigStore:
    """Local desktop settings for optional stock-provider credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list_response(self) -> StockProviderConfigListResponse:
        return StockProviderConfigListResponse(
            providers=[self.response(provider) for provider in StockAssetProvider]
        )

    def response(self, provider: StockAssetProvider) -> StockProviderConfigResponse:
        key = self.api_key(provider)
        return StockProviderConfigResponse(
            provider=provider,
            base_url=self.base_url(provider),
            has_api_key=bool(key),
            key_source=self.key_source(provider),
            can_search=provider == StockAssetProvider.OPENVERSE or bool(key),
        )

    def api_key(self, provider: StockAssetProvider) -> str:
        environment = os.environ.get(_ENV_KEYS[provider], "").strip()
        return environment or self._saved_value(provider, "api_key")

    def base_url(self, provider: StockAssetProvider) -> str:
        environment = os.environ.get(
            f"{provider.value}_BASE_URL",
            "",
        ).strip()
        return (
            environment
            or self._saved_value(provider, "base_url")
            or _DEFAULT_BASE_URLS[provider]
        ).rstrip("/")

    def key_source(self, provider: StockAssetProvider) -> str:
        if os.environ.get(_ENV_KEYS[provider], "").strip():
            return "ENVIRONMENT"
        if self._saved_value(provider, "api_key"):
            return "LOCAL_CONFIG"
        if provider == StockAssetProvider.OPENVERSE:
            return "NOT_REQUIRED"
        return "MISSING"

    def update(
        self,
        provider: StockAssetProvider,
        request: StockProviderConfigUpdate,
    ) -> StockProviderConfigResponse:
        payload = self._saved_payload()
        current = payload.get(provider.value)
        values = (
            cast(dict[str, object], current).copy()
            if isinstance(current, dict)
            else {}
        )
        if request.clear_api_key:
            values["api_key"] = ""
        elif request.api_key is not None:
            values["api_key"] = request.api_key.strip()
        if request.base_url is not None:
            values["base_url"] = request.base_url.strip().rstrip("/")
        payload[provider.value] = values
        self._write_saved_payload(payload)
        return self.response(provider)

    def _saved_value(self, provider: StockAssetProvider, key: str) -> str:
        provider_payload = self._saved_payload().get(provider.value)
        if not isinstance(provider_payload, dict):
            return ""
        value = cast(dict[str, object], provider_payload).get(key, "")
        return value.strip() if isinstance(value, str) else ""

    def _saved_payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            payload = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}
        return cast(dict[str, object], payload) if isinstance(payload, dict) else {}

    def _write_saved_payload(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)


class RequestsStockAssetSearchClient:
    def __init__(
        self,
        config: StockProviderConfigStore,
        *,
        session: Session | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def search(self, request: StockAssetSearchRequest) -> StockAssetSearchResponse:
        connection = self.config.response(request.provider)
        if not connection.can_search:
            raise StockAssetSearchError(
                f"{request.provider.value} requires an API key before search"
            )
        if request.provider == StockAssetProvider.OPENVERSE:
            candidates = self._search_openverse(request)
        elif request.provider == StockAssetProvider.PEXELS:
            candidates = self._search_pexels(request)
        else:
            candidates = self._search_pixabay(request)
        warnings = [
            "素材库结果是候选，不代表平台替用户确认肖像权、商标权或最终用途授权。",
        ]
        if request.asset_kind == AssetKind.CHARACTER:
            warnings.append(
                "人物候选默认用于真实摄影与造型参考并生成新虚构身份；若要保留同一人身份，应使用用户有权使用或已确认肖像授权的素材。"
            )
        return StockAssetSearchResponse(
            provider=request.provider,
            query=request.query,
            page=request.page,
            candidates=candidates[: request.limit],
            warnings=warnings,
        )

    def _search_openverse(
        self,
        request: StockAssetSearchRequest,
    ) -> list[StockAssetCandidate]:
        params: dict[str, str | int] = {
            "q": request.query,
            "page": request.page,
            "page_size": min(max(request.limit * 2, request.limit), 20),
            "mature": "false",
        }
        if request.commercial_and_modifiable_only:
            params["license"] = "cc0,pdm,by,by-sa"
        payload = self._get_json(self.config.base_url(request.provider), params=params)
        results = payload.get("results")
        if not isinstance(results, list):
            raise StockAssetSearchError("Openverse response did not contain results")
        candidates: list[StockAssetCandidate] = []
        for value in cast(list[object], results):
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, object], value)
            source_page = _text(item.get("foreign_landing_url"))
            image_url = _text(item.get("url"))
            preview_url = _text(item.get("thumbnail")) or image_url
            identifier = _text(item.get("id"))
            if not source_page or not image_url or not preview_url or not identifier:
                continue
            license_name = _text(item.get("license")) or "Creative Commons"
            license_version = _text(item.get("license_version"))
            if license_version:
                license_name = f"CC {license_name.upper()} {license_version}"
            tags = [
                name
                for tag in _objects(item.get("tags"))
                if (name := _text(tag.get("name")))
            ][:48]
            candidates.append(
                self._candidate(
                    request=request,
                    provider_asset_id=identifier,
                    title=_text(item.get("title")) or f"Openverse {identifier}",
                    source_page_url=source_page,
                    image_url=image_url,
                    preview_url=preview_url,
                    creator=_text(item.get("creator")),
                    creator_url=_text(item.get("creator_url")),
                    license_name=license_name,
                    license_url=_text(item.get("license_url")),
                    attribution=_text(item.get("attribution")),
                    width=_integer(item.get("width")),
                    height=_integer(item.get("height")),
                    tags=tags,
                )
            )
        return _prefer_orientation(candidates, request.orientation)

    def _search_pexels(
        self,
        request: StockAssetSearchRequest,
    ) -> list[StockAssetCandidate]:
        orientation = {
            StockAssetOrientation.ANY: None,
            StockAssetOrientation.PORTRAIT: "portrait",
            StockAssetOrientation.LANDSCAPE: "landscape",
            StockAssetOrientation.SQUARE: "square",
        }[request.orientation]
        params: dict[str, str | int] = {
            "query": request.query,
            "page": request.page,
            "per_page": request.limit,
            "locale": "zh-CN",
        }
        if orientation is not None:
            params["orientation"] = orientation
        payload = self._get_json(
            f"{self.config.base_url(request.provider)}/search",
            params=params,
            headers={"Authorization": self.config.api_key(request.provider)},
        )
        photos = payload.get("photos")
        if not isinstance(photos, list):
            raise StockAssetSearchError("Pexels response did not contain photos")
        candidates: list[StockAssetCandidate] = []
        for value in cast(list[object], photos):
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, object], value)
            src = item.get("src")
            if not isinstance(src, dict):
                continue
            sources = cast(dict[str, object], src)
            identifier = _identifier(item.get("id"))
            source_page = _text(item.get("url"))
            image_url = _text(sources.get("original")) or _text(sources.get("large2x"))
            preview_url = _text(sources.get("medium")) or image_url
            if not identifier or not source_page or not image_url or not preview_url:
                continue
            candidates.append(
                self._candidate(
                    request=request,
                    provider_asset_id=identifier,
                    title=_text(item.get("alt")) or f"Pexels photo {identifier}",
                    source_page_url=source_page,
                    image_url=image_url,
                    preview_url=preview_url,
                    creator=_text(item.get("photographer")),
                    creator_url=_text(item.get("photographer_url")),
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/legal-pages/license/",
                    attribution=None,
                    width=_integer(item.get("width")),
                    height=_integer(item.get("height")),
                    tags=[],
                )
            )
        return candidates

    def _search_pixabay(
        self,
        request: StockAssetSearchRequest,
    ) -> list[StockAssetCandidate]:
        orientation = {
            StockAssetOrientation.ANY: "all",
            StockAssetOrientation.PORTRAIT: "vertical",
            StockAssetOrientation.LANDSCAPE: "horizontal",
            StockAssetOrientation.SQUARE: "all",
        }[request.orientation]
        payload = self._get_json(
            self.config.base_url(request.provider),
            params={
                "key": self.config.api_key(request.provider),
                "q": request.query,
                "page": request.page,
                "per_page": max(3, request.limit),
                "image_type": "photo",
                "orientation": orientation,
                "safesearch": "true",
            },
        )
        hits = payload.get("hits")
        if not isinstance(hits, list):
            raise StockAssetSearchError("Pixabay response did not contain hits")
        candidates: list[StockAssetCandidate] = []
        for value in cast(list[object], hits):
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, object], value)
            identifier = _identifier(item.get("id"))
            source_page = _text(item.get("pageURL"))
            image_url = _text(item.get("largeImageURL")) or _text(item.get("webformatURL"))
            preview_url = _text(item.get("previewURL")) or image_url
            if not identifier or not source_page or not image_url or not preview_url:
                continue
            raw_tags = _text(item.get("tags"))
            tags = [value.strip() for value in raw_tags.split(",") if value.strip()][:48]
            candidates.append(
                self._candidate(
                    request=request,
                    provider_asset_id=identifier,
                    title=raw_tags or f"Pixabay image {identifier}",
                    source_page_url=source_page,
                    image_url=image_url,
                    preview_url=preview_url,
                    creator=_text(item.get("user")),
                    creator_url=None,
                    license_name="Pixabay Content License",
                    license_url="https://pixabay.com/service/license-summary/",
                    attribution=None,
                    width=_integer(item.get("imageWidth")),
                    height=_integer(item.get("imageHeight")),
                    tags=tags,
                )
            )
        return candidates

    def _candidate(
        self,
        *,
        request: StockAssetSearchRequest,
        provider_asset_id: str,
        title: str,
        source_page_url: str,
        image_url: str,
        preview_url: str,
        creator: str | None,
        creator_url: str | None,
        license_name: str,
        license_url: str | None,
        attribution: str | None,
        width: int | None,
        height: int | None,
        tags: list[str],
    ) -> StockAssetCandidate:
        character = request.asset_kind == AssetKind.CHARACTER
        return StockAssetCandidate(
            provider=request.provider,
            provider_asset_id=provider_asset_id,
            asset_kind=request.asset_kind,
            title=title[:1000],
            source_page_url=source_page_url,
            image_url=image_url,
            preview_url=preview_url,
            creator=creator or None,
            creator_url=creator_url or None,
            license_name=license_name,
            license_url=license_url or None,
            attribution=attribution or None,
            width=width,
            height=height,
            tags=tags,
            recommended_use=(
                StockAssetRecommendedUse.VISUAL_GROUNDING_NEW_IDENTITY
                if character
                else StockAssetRecommendedUse.DIRECT_OR_DERIVE
            ),
            advisory_notes=(
                [
                    "建议提取真实摄影质感、年龄、体型、发型和服装方向，再生成新的虚构角色身份。",
                    "用户确认拥有肖像授权时，可以覆盖建议并保留人物身份。",
                ]
                if character
                else ["导入前查看来源页、许可与画面中的商标或可识别人像。"]
            ),
        )

    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise StockAssetSearchError(f"Stock provider request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500].strip()
            raise StockAssetSearchError(
                f"Stock provider returned HTTP {response.status_code}: {detail}"
            )
        try:
            payload = cast(object, response.json())
        except ValueError as exc:
            raise StockAssetSearchError("Stock provider returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise StockAssetSearchError("Stock provider response was not an object")
        return cast(dict[str, object], payload)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _identifier(value: object) -> str:
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    return None


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, object], item)
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]


def _prefer_orientation(
    candidates: list[StockAssetCandidate],
    orientation: StockAssetOrientation,
) -> list[StockAssetCandidate]:
    if orientation == StockAssetOrientation.ANY:
        return candidates

    def matches(item: StockAssetCandidate) -> bool:
        if item.width is None or item.height is None:
            return False
        if orientation == StockAssetOrientation.PORTRAIT:
            return item.height > item.width
        if orientation == StockAssetOrientation.LANDSCAPE:
            return item.width > item.height
        return abs(item.width - item.height) / max(item.width, item.height) <= 0.12

    preferred = [item for item in candidates if matches(item)]
    remaining = [item for item in candidates if item not in preferred]
    return [*preferred, *remaining]
