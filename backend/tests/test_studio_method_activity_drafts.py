"""Offline product contracts for methods, real streamed drafts and live work."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue
import pytest

from _routes.short_drama import router
from short_drama.agent_runtime.models import DelegationStatus, SpecialistTurnRecord, SpecialistTurnStatus
from short_drama.providers.openai_compatible_language_client import OpenAICompatibleLanguageClient, LanguageProviderError
from short_drama.service import ShortDramaService
from short_drama.studio_generation_draft import creative_text
from short_drama.studio_methods import method_detail
from short_drama.studio_models import StudioJob, StudioScope
from short_drama.studio_trace import production_trace, trace_context
from tests.test_language_provider import FakeResponse, FakeSession


def test_method_detail_http_uses_current_role_and_post_knowledge() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/short-drama/studio/methods/method.drama_series")
        assert response.status_code == 200
        missing = client.get("/api/short-drama/studio/methods/no-such-method")
        assert missing.status_code == 404
    detail = method_detail("method.drama_series")
    post = next(item for item in detail.sections if item.role == "DIRECTOR" and item.stage == "POST")
    assert "字幕" in post.text and "当前" in post.text
    assert "镜头与光学承载情绪" not in post.text
    assert detail.description and detail.upstream_sources


@pytest.mark.parametrize("success", [True, False])
def test_only_real_provider_deltas_publish_uncommitted_draft(tmp_path: Path, success: bool) -> None:
    fragments = ['{"tool_name":"script.save_scene","arguments":{"request":{"screenplay_text":"清晨，', '她说：\\"回来吧\\"。"}}}']
    lines = [line for fragment in fragments for line in ["data: " + json.dumps({"type": "response.output_text.delta", "delta": fragment}), ""]]
    if success:
        lines += ['data: {"type":"response.completed","response":{"status":"completed"}}', ""]
    session = FakeSession(FakeResponse(200, {}, headers={"Content-Type": "text/event-stream"}, stream_lines=lines))
    client = OpenAICompatibleLanguageClient(api_key="", base_url="http://127.0.0.1:4141/v1", session=session)  # type: ignore[arg-type]
    entries: list[tuple[str, dict[str, JsonValue]]] = []
    with production_trace(tmp_path, lambda kind, value: entries.append((kind, value))), trace_context(work_order_id="worker-a", job_id="job-a"):
        if success:
            assert client.generate_text(instructions="写当前场次", input_text="清晨") == "".join(fragments)
        else:
            with pytest.raises(LanguageProviderError):
                client.generate_text(instructions="写当前场次", input_text="清晨")
    drafts = [value for kind, value in entries if kind == "model.draft"]
    assert drafts and drafts[0]["text"] == "剧本\n清晨，"
    assert drafts[-1]["text"] == '剧本\n清晨，她说："回来吧"。'
    assert drafts[-1]["status"] == ("received" if success else "interrupted")
    assert all(value["saved"] is False and value["source"] == "provider_delta" and value["work_order_id"] == "worker-a" for value in drafts)
    assert not any(kind == "content.updated" for kind, _ in entries)
    assert "tool_name" not in str(drafts)


def test_json_without_delta_and_incomplete_escapes_do_not_invent_draft(tmp_path: Path) -> None:
    assert creative_text('{"screenplay_text":"\\u4e') == ""
    assert creative_text('{"screenplay_text":"\\u4e00\\ud83d') == "剧本\n一"
    assert creative_text('{"screenplay_text":"\\u4e00\\ud83d\\ude00') == "剧本\n一😀"
    assert creative_text('{"reason":"隐藏协议\\"visual\\":\\"不是正文\\""}') == ""
    client = OpenAICompatibleLanguageClient(api_key="", base_url="http://127.0.0.1:4141/v1",
        session=FakeSession(FakeResponse(200, {"status": "completed", "output_text": '{"screenplay_text":"完成"}'})))  # type: ignore[arg-type]
    kinds: list[str] = []
    with production_trace(tmp_path, lambda kind, _value: kinds.append(kind)):
        client.generate_text(instructions="写当前场次", input_text="清晨")
    assert "model.draft" not in kinds


def test_activity_reads_persisted_polling_and_scoped_error(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    pid = service.initialize_fixture().document.project.id
    runtime = service.agent_runtime
    run = runtime.create_run(project_id=pid, objective="制作当前镜头", root_agent="leader")
    work = runtime.create_work_order(run.id, sender="leader", recipient_role="video", objective="制作镜头一", why_now="已就绪")
    work.status = DelegationStatus.IN_PROGRESS
    work.continuation_not_before = datetime.now(UTC) + timedelta(seconds=30)
    work.specialist_turns = [SpecialistTurnRecord(id="turn", sequence=1, tool_name="task.get", reason="查询本次任务",
        status=SpecialistTurnStatus.COMPLETED, result={"receipt": {"current_state": {"status": "SUBMITTED"}}})]
    assert work.context_manifest is not None
    work.context_manifest.selected_skill_ids = ["method.drama_series"]
    runtime.store.save_work_order(work)
    now = datetime.now(UTC).isoformat()
    service.repository.save_studio_job(StudioJob(id="job", project_id=pid, run_id=run.id, objective=run.objective,
        status="running", scope=StudioScope(), created_at=now, updated_at=now))
    run.goal_summary = "当前只完成视频生成前准备，不提交视频。"
    runtime.store.save_run(run)
    activity = service.studio_runtime.activity(pid)
    assert activity.jobs[0].current_goal == run.goal_summary
    assert activity.jobs[0].objective == "制作当前镜头"
    persisted_job = service.repository.list_studio_jobs(pid)[0]
    assert persisted_job.objective == "制作当前镜头"
    assert persisted_job.current_goal is None
    worker = service.studio_runtime.activity(pid).workers[0]
    assert worker.waiting_until == work.continuation_not_before.isoformat()
    assert not worker.waiting_review and worker.latest_error is None
    assert worker.selected_method_skill_ids == ["method.drama_series"]
    assert worker.latest_receipt == {"current_state": {"status": "SUBMITTED"}}
    work.continuation_not_before = None
    work.specialist_turns[-1].status = SpecialistTurnStatus.COMPLETED
    work.specialist_turns[-1].result = {"error": {"message": "本镜参考图文件缺失"}}
    runtime.store.save_work_order(work)
    worker = service.studio_runtime.activity(pid).workers[0]
    assert worker.waiting_until is None and worker.latest_error == "本镜参考图文件缺失"
