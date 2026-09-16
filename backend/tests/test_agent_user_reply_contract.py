"""The actual Leader request explains which authored fields become chat text."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioMessage


class ReplyLanguageClient:
    def __init__(self, reply: str, *, waiting: bool) -> None:
        self.reply = reply
        self.waiting = waiting
        self.instructions: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def validate_credentials(self) -> None:
        pass

    def generate_text(self, *, instructions: str, input_text: str,
                      image_urls: list[str] | None = None, max_output_tokens: int = 2200) -> str:
        del image_urls, max_output_tokens
        self.instructions.append(instructions)
        self.payloads.append(json.loads(input_text))
        return json.dumps({
            "summary": "等待连接恢复后继续原任务。" if self.waiting else self.reply,
            "goal_complete": not self.waiting,
            "steps": [{
                "id": "waiting-for-connection", "title": "等待连接恢复", "kind": "USER_INPUT",
                "assigned_agent": "production-manager", "instructions": self.reply,
                "arguments": {"internal_note": "THIS_IS_NOT_CHAT_TEXT"},
            }] if self.waiting else [],
        }, ensure_ascii=False)


@pytest.mark.parametrize("waiting", [True, False], ids=["waiting-user", "completed"])
def test_leader_receives_and_runner_preserves_user_reply_contract(tmp_path: Path, waiting: bool) -> None:
    original = tmp_path / "原片目录" / "film-subtitled.mp4"
    original.parent.mkdir()
    original.write_bytes(b"local path fixture; no media inference")
    reply = (
        f"原片保存在：\n`{original}`\n\n"
        + ("视频上传被 HTTP 401 拒绝，生成提交尚未开始，目前没有修复候选。请恢复连接后告诉我。"
           if waiting else "本次文件位置已核对；没有新增修复候选，口型问题仍未解决。")
    )
    client = ReplyLanguageClient(reply, waiting=waiting)
    service = ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media",
                               language_provider_factory=lambda _key, _url: client)
    project_id = service.initialize_fixture().document.project.id
    service.update_language_provider_config(LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1"))
    job = service.studio_runtime.send(project_id, StudioMessage(text="核对原片位置并告诉我实际结果。"))
    expected_status = "waiting_user" if waiting else "completed"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = next(item for item in service.repository.list_studio_jobs() if item.id == job.id)
        thread = service.studio_runtime.threads.get(job.id)
        if saved.status == expected_status and (thread is None or not thread.is_alive()):
            break
        if saved.status == "failed":
            pytest.fail(f"Unexpected job failure: {saved.error}")
        threading.Event().wait(0.01)
    else:
        pytest.fail(f"Job did not reach {expected_status}")

    assert len(client.payloads) == 1  # No second rewrite/inference turn.
    prompt = client.instructions[0]
    assert "USER_INPUT 的 instructions 会原样作为聊天正文展示给用户" in prompt
    assert "arguments 不会作为正文展开" in prompt
    assert "普通 TOOL_CALL、ARTIFACT_PATCH 和 DELEGATION" in prompt
    assert "本计划完成且 goal_complete=true 时，summary" in prompt
    contract = client.payloads[0]["output_contracts"]["plan"]
    step_fields = contract["$defs"]["PlanStep"]["properties"]
    assert "仅 kind=USER_INPUT" in step_fields["instructions"]["description"]
    assert "实际路径" in step_fields["instructions"]["description"]
    assert "其他 kind 仍写工具执行或 Worker 交接说明" in step_fields["instructions"]["description"]
    assert "不会作为聊天正文展开" in step_fields["arguments"]["description"]
    assert "goal_complete=true" in contract["properties"]["summary"]["description"]
    messages = [event for event in service.studio_runtime.activity(project_id).events
                if event.kind == "chat.assistant" and event.data.get("job_id") == job.id]
    assert len(messages) == 1
    assert messages[0].data["text"] == reply
    assert messages[0].data.get("needs_input", False) is waiting
    assert original.read_bytes() == b"local path fixture; no media inference"
