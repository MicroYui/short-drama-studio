"""Dependency registration shared by the full and lightweight FastAPI apps."""

from __future__ import annotations

from short_drama.service import ShortDramaService


_service: ShortDramaService | None = None


def init_short_drama_service(service: ShortDramaService) -> None:
    global _service
    _service = service


def get_short_drama_service() -> ShortDramaService:
    if _service is None:
        raise RuntimeError("ShortDramaService has not been initialized")
    return _service
