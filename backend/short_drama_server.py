"""Lightweight production server for the short-drama desktop application."""

from __future__ import annotations

import faulthandler
import hmac
import logging
import os
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from _routes._errors import HTTPError, build_http_error_response
from _routes.short_drama import router as short_drama_router
from short_drama.audio_inspection import TransformersAudioSemanticService
from short_drama.deps import init_short_drama_service
from short_drama.service import ShortDramaService


faulthandler.enable(file=sys.stderr, all_threads=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_app_data_dir() -> Path:
    configured = os.environ.get("LTX_APP_DATA_DIR")
    if not configured:
        raise RuntimeError("LTX_APP_DATA_DIR must point to the desktop application data directory")
    path = Path(configured).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _select_port() -> int:
    configured = os.environ.get("LTX_PORT", "").strip()
    if configured:
        return int(configured)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


APP_DATA_DIR = _resolve_app_data_dir()
OUTPUTS_DIR = APP_DATA_DIR / "outputs" / "short-drama-projects"
SERVICE = ShortDramaService(
    database_path=APP_DATA_DIR / "short-drama.sqlite3",
    workspace_root=OUTPUTS_DIR,
    audio_semantic_service=TransformersAudioSemanticService(
        transcription_model=os.environ.get(
            "SHORT_DRAMA_TRANSCRIPTION_MODEL",
            "openai/whisper-small",
        ),
        speaker_model=(
            os.environ.get(
                "SHORT_DRAMA_SPEAKER_MODEL",
                "microsoft/wavlm-base-plus-sv",
            ).strip()
            or None
        ),
        device=os.environ.get("SHORT_DRAMA_AUDIO_DEVICE", "auto"),
    ),
)
init_short_drama_service(SERVICE)


def create_short_drama_app(auth_token: str) -> FastAPI:
    app = FastAPI(title="Short Drama Studio Product API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _auth_middleware(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        if not auth_token or request.method == "OPTIONS":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        candidate = header[7:] if header.startswith("Bearer ") else ""
        if hmac.compare_digest(candidate, auth_token):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content=build_http_error_response(401, "Unauthorized").model_dump(),
        )

    @app.exception_handler(HTTPError)
    async def _http_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request,
        exc: HTTPError,
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.response.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=build_http_error_response(422, str(exc)).model_dump(),
        )

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "service": "short-drama"}

    @app.post("/api/system/shutdown")
    def shutdown() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if _running_server is not None:
            _running_server.should_exit = True
        return {"status": "shutting-down"}

    app.include_router(short_drama_router)
    return app


AUTH_TOKEN = os.environ.get("LTX_AUTH_TOKEN", "")
app = create_short_drama_app(AUTH_TOKEN)
_running_server: uvicorn.Server | None = None


def main() -> None:
    global _running_server
    port = _select_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    _running_server = uvicorn.Server(config)
    print(f"Server running on http://127.0.0.1:{port}", flush=True)
    _running_server.run()


if __name__ == "__main__":
    main()
