"""Official Context IR protocol and restart behaviour with Fake HTTP only."""

from pathlib import Path
from typing import cast

from PIL import Image
import pytest
import requests

from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3RemoteTask
from short_drama.models import VideoGenerationRoute
from short_drama.providers.h3_context_ir import (
    ContextIRRecord, H3ContextIRPending, H3ContextIRRequest, OfficialH3ContextIRClient,
    advance_context_ir, build_context_ir_request, compile_h3_context_ir,
)
from short_drama.providers.minimax_h3_client import H3DefiniteRequestError, H3SubmissionUnknown, H3TransientError, encode_h3_media_data_uri
from short_drama.providers.h3_language_prompt import LanguagePromptValidationError
from tests.test_minimax_h3_client import FakeSession, FakeResponse
from tests.test_h3_generation_unit_language import _fixture


def request() -> H3ContextIRRequest:
    return build_context_ir_request("A person opens a door.", route=VideoGenerationRoute.DIRECT,
        duration=5, media_inputs=[], resolve_url=lambda _item: "")


def test_official_protocol_uses_separate_endpoint_auth_and_prompt_task_type() -> None:
    session = FakeSession()
    session.responses = [FakeResponse(200, {"task_id": "ir-1"}), FakeResponse(200, {"task": {
        "id": "ir-1", "status": "succeeded", "task_type": "h3_context_ir", "content": {"prompt": "IR output"}}})]
    client = OfficialH3ContextIRClient(api_key="official-test-key", session=cast(requests.Session, session))
    assert client.submit(request()) == "ir-1"
    assert client.query("ir-1").content is not None
    assert [(method, url) for method, url, _data in session.calls] == [
        ("POST", "https://api.minimax.io/v2/h3_context_ir"),
        ("GET", "https://api.minimax.io/v2/query/video_generation/ir-1")]
    submitted = session.calls[0][2]
    assert submitted["headers"]["Authorization"] == "Bearer official-test-key"
    assert submitted["json"] == {"model": "MiniMax-H3", "duration": 5, "ratio": "9:16",
        "content": [{"type": "text", "text": "A person opens a door."}]}
    with pytest.raises(ValueError, match="different credential"):
        OfficialH3ContextIRClient(api_key="")


@pytest.mark.parametrize("status,error", [(401, H3DefiniteRequestError), (500, H3SubmissionUnknown)])
def test_submit_failure_classification_does_not_turn_uncertainty_into_safe_retry(status: int, error: type[Exception]) -> None:
    session = FakeSession()
    session.responses = [FakeResponse(status, {"error": {"message": "request rejected"}})]
    client = OfficialH3ContextIRClient(api_key="official-test", session=cast(requests.Session, session))
    with pytest.raises(error):
        client.submit(request())


def test_query_rejects_video_task_masquerading_as_prompt_task() -> None:
    session = FakeSession()
    session.responses = [FakeResponse(200, {"task": {"id": "ir-1", "status": "succeeded", "task_type": "generation", "content": {"url": "https://example.test/video.mp4"}}})]
    client = OfficialH3ContextIRClient(api_key="official-test", session=cast(requests.Session, session))
    with pytest.raises(H3TransientError, match="another task/type"):
        client.query("ir-1")


class FakeIR:
    def __init__(self, prompt: str, *, unknown: bool = False) -> None:
        self.prompt, self.unknown = prompt, unknown
        self.submitted: list[H3ContextIRRequest] = []
        self.queries: list[str] = []

    def submit(self, value: H3ContextIRRequest) -> str:
        self.submitted.append(value)
        if self.unknown:
            raise H3SubmissionUnknown("connection lost after submit")
        return "ir-saved"

    def query(self, task_id: str) -> H3RemoteTask:
        self.queries.append(task_id)
        return H3RemoteTask.model_validate({"id": task_id, "status": "succeeded", "task_type": "h3_context_ir",
            "content": {"prompt": self.prompt}, "usage": {"total_tokens": 123}})


def due(directory: Path) -> ContextIRRecord:
    path = next(directory.glob("*.json"))
    record = ContextIRRecord.model_validate_json(path.read_text())
    record.next_poll_at = "2000-01-01T00:00:00+00:00"
    path.write_text(record.model_dump_json())
    return record


def test_pending_restart_and_completed_prompt_reuse_do_not_resubmit(tmp_path: Path) -> None:
    client = FakeIR("Saved prompt")
    kwargs = {"project_id": "p", "source": "one unchanged group"}
    with pytest.raises(H3ContextIRPending) as pending:
        advance_context_ir(client=client, cache_directory=tmp_path, identity=kwargs, build_request=request)
    assert pending.value.task_id == "ir-saved"
    with pytest.raises(H3ContextIRPending):
        advance_context_ir(client=client, cache_directory=tmp_path, identity=kwargs, build_request=request)
    assert not client.queries
    due(tmp_path)
    assert advance_context_ir(client=client, cache_directory=tmp_path, identity=kwargs, build_request=request) == "Saved prompt"
    def missing_credential_client() -> FakeIR:
        raise ValueError("No credential remains after restart")
    assert advance_context_ir(client=missing_credential_client, cache_directory=tmp_path, identity=kwargs, build_request=request) == "Saved prompt"
    assert len(client.submitted) == len(client.queries) == 1
    assert ContextIRRecord.model_validate_json(next(tmp_path.glob("*.json")).read_text()).usage["total_tokens"] == 123


def test_unknown_submission_stays_non_resubmittable_even_with_retry_flag(tmp_path: Path) -> None:
    client = FakeIR("unused", unknown=True)
    for _ in range(2):
        with pytest.raises(H3SubmissionUnknown):
            advance_context_ir(client=client, cache_directory=tmp_path, identity={"source": "same"}, build_request=request, retry_failed=True)
    assert len(client.submitted) == 1


def test_shared_media_validation_order_encoding_and_text_limit(tmp_path: Path) -> None:
    image = tmp_path / "frame.png"
    Image.new("RGB", (256, 256), "blue").save(image)
    media = H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.FIRST_FRAME, source=H3MediaSource(local_path=str(image)))
    value = build_context_ir_request("Keep the first frame.", route=VideoGenerationRoute.FRAME_FIRST,
        duration=4, media_inputs=[media], resolve_url=lambda _item: encode_h3_media_data_uri(image))
    assert value.ratio == "adaptive"
    assert value.content[1].image_url is not None and value.content[1].image_url.url.startswith("data:image/png;base64,")
    with pytest.raises(ValueError, match="7000"):
        build_context_ir_request("x" * 7001, route=VideoGenerationRoute.DIRECT, duration=4, media_inputs=[], resolve_url=lambda _item: "")


def test_alternative_group_compiler_validates_and_caches_without_language_calls(tmp_path: Path) -> None:
    service, language, document, _generation, unit, compile_request = _fixture(tmp_path)
    client = FakeIR(language.output_text)
    with pytest.raises(H3ContextIRPending):
        compile_h3_context_ir(service, document.project.id, compile_request, client=client)
    source = client.submitted[0].content[0].text
    assert source is not None and len(source) <= 7000
    assert "OTHER_CLIP_ONLY" not in source
    assert document.shots[0].visual in source
    due(service.workspace_root / "h3-context-ir")
    result = compile_h3_context_ir(service, document.project.id, compile_request, client=client)
    assert result.shot_ids == unit.shot_ids
    assert result.provider == "minimax-official-context-ir"
    assert result.validation_notes
    assert compile_h3_context_ir(service, document.project.id, compile_request).compiled_prompt == result.compiled_prompt
    assert len(client.submitted) == len(client.queries) == 1
    assert language.calls == []


def test_verbose_group_and_compiled_prompt_input_are_rejected_before_paid_call(tmp_path: Path) -> None:
    service, language, document, _generation, _unit, compile_request = _fixture(tmp_path, verbose=True)
    client = FakeIR("unused")
    with pytest.raises(ValueError, match="No facts were truncated and no task was submitted"):
        compile_h3_context_ir(service, document.project.id, compile_request, client=client)
    with pytest.raises(ValueError, match="canonical source facts"):
        compile_h3_context_ir(service, document.project.id, compile_request.model_copy(update={"source_prompt": "Already compiled"}), client=client)
    assert not client.submitted and not language.calls


def test_empty_success_is_saved_as_failure_without_automatic_resubmission(tmp_path: Path) -> None:
    client = FakeIR("")
    with pytest.raises(H3ContextIRPending):
        advance_context_ir(client=client, cache_directory=tmp_path, identity={"source": "same"}, build_request=request)
    due(tmp_path)
    for _ in range(2):
        with pytest.raises(ValueError, match="without content.prompt"):
            advance_context_ir(client=client, cache_directory=tmp_path, identity={"source": "same"}, build_request=request)
    assert len(client.submitted) == len(client.queries) == 1


def test_unaccepted_output_stays_cached_without_a_second_compiler_or_paid_submit(tmp_path: Path) -> None:
    service, language, document, _generation, _unit, compile_request = _fixture(tmp_path)
    client = FakeIR("An unstructured result that is not accepted H3 IR.")
    with pytest.raises(H3ContextIRPending):
        compile_h3_context_ir(service, document.project.id, compile_request, client=client)
    due(service.workspace_root / "h3-context-ir")
    for _ in range(2):
        with pytest.raises(LanguagePromptValidationError):
            compile_h3_context_ir(service, document.project.id, compile_request, client=client, retry_failed=True)
    assert len(client.submitted) == len(client.queries) == 1
    assert not language.calls
