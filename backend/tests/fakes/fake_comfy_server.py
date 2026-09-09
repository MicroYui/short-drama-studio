"""Small local HTTP service for testing the documented ComfyUI adapter contract."""

from __future__ import annotations

from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from collections.abc import Iterator
from typing import Any


@contextmanager
def fake_comfy_server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"upload": None, "prompt": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def reply(self, value: dict[str, Any]) -> None:
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/system_stats": self.reply({"system": {"os": "test-service"}})
            elif self.path.startswith("/history/"):
                self.reply({"test-prompt": {"status": {"status_str": "success"}, "outputs": {"2": {"images": [{"filename": "processed.png", "type": "output", "subfolder": ""}]}}}})
            elif self.path.startswith("/view?"):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(state["upload"])
            else: self.send_error(404)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                header = f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                message = BytesParser(policy=policy.default).parsebytes(header + body)
                part = next(item for item in message.iter_parts() if item.get_filename())
                state["upload"] = part.get_payload(decode=True)
                state["filename"] = part.get_filename()
                self.reply({"name": state["filename"], "subfolder": "", "type": "input"})
            elif self.path == "/prompt":
                state["prompt"] = json.loads(body)["prompt"]
                self.reply({"prompt_id": "test-prompt"})
            else: self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
