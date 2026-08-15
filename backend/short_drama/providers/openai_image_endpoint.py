"""Resolve standard OpenAI and Azure deployment-scoped image endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


AZURE_GPT_IMAGE_MIN_API_VERSION = "2025-04-01-preview"
_AZURE_GPT_IMAGE_MIN_API_DATE = date(2025, 4, 1)


@dataclass(frozen=True)
class OpenAIImageEndpoint:
    configured_url: str
    generation_url: str
    edit_url: str
    model_url: str | None
    auth_mode: Literal["bearer", "azure-api-key"]

    @property
    def is_azure(self) -> bool:
        return self.auth_mode == "azure-api-key"


def normalize_image_provider_url(value: str) -> str:
    """Normalize path slashes and upgrade obsolete Azure GPT Image versions."""

    raw = value.strip()
    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    query = parts.query
    if _is_azure_deployment_endpoint(parts.hostname, path):
        query = _normalized_azure_query(query)
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def resolve_image_endpoint(value: str) -> OpenAIImageEndpoint:
    configured_url = normalize_image_provider_url(value)
    parts = urlsplit(configured_url)
    path = parts.path.rstrip("/")
    is_azure = _is_azure_deployment_endpoint(parts.hostname, path)

    if path.endswith("/images/generations"):
        generation_path = path
        edit_path = f"{path.removesuffix('/images/generations')}/images/edits"
        api_root = path.removesuffix("/images/generations")
    elif path.endswith("/images/edits"):
        edit_path = path
        generation_path = f"{path.removesuffix('/images/edits')}/images/generations"
        api_root = path.removesuffix("/images/edits")
    else:
        api_root = path
        generation_path = f"{path}/images/generations"
        edit_path = f"{path}/images/edits"

    def build(target_path: str) -> str:
        return urlunsplit(
            (parts.scheme, parts.netloc, target_path, parts.query, parts.fragment)
        )

    return OpenAIImageEndpoint(
        configured_url=configured_url,
        generation_url=build(generation_path),
        edit_url=build(edit_path),
        model_url=None if is_azure else build(f"{api_root}/models/gpt-image-2"),
        auth_mode="azure-api-key" if is_azure else "bearer",
    )


def _is_azure_deployment_endpoint(hostname: str | None, path: str) -> bool:
    host = (hostname or "").lower()
    return host.endswith(".openai.azure.com") or "/openai/deployments/" in path


def _normalized_azure_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    normalized: list[tuple[str, str]] = []
    found_api_version = False
    for key, value in pairs:
        if key.lower() != "api-version":
            normalized.append((key, value))
            continue
        found_api_version = True
        normalized.append((key, _upgrade_azure_api_version(value)))
    if not found_api_version:
        normalized.append(("api-version", AZURE_GPT_IMAGE_MIN_API_VERSION))
    return urlencode(normalized)


def _upgrade_azure_api_version(value: str) -> str:
    try:
        version_date = date.fromisoformat(value[:10])
    except ValueError:
        return value
    if version_date < _AZURE_GPT_IMAGE_MIN_API_DATE:
        return AZURE_GPT_IMAGE_MIN_API_VERSION
    return value
