"""Real image transport, persistent preparation and exact request mapping without HTTP."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from short_drama.h3_models import H3MediaInput, H3MediaKind, H3MediaRole, H3MediaSource, H3ProbeRequest, H3UploadedInput
from short_drama.models import TaskStatus, VideoGenerationRoute
from short_drama.providers.minimax_h3_client import (
    H3DefiniteRequestError, encode_h3_media_data_uri, prepare_h3_image_dimensions,
)
from short_drama.service import WorkflowConflictError
from tests.test_short_drama_h3 import FakeH3Client, _service


class ImageTransportClient(FakeH3Client):
    def upload_file(self, path: Path) -> str:
        self.upload_calls.append(path.resolve())
        return encode_h3_media_data_uri(path)


@pytest.mark.parametrize("size", [(256, 256), (5760, 2560)])
def test_legal_boundary_images_keep_original_bytes_and_path(tmp_path: Path, size: tuple[int, int]) -> None:
    source = tmp_path / "legal.png"
    Image.new("RGB", size, "#497d39").save(source)
    before = source.read_bytes()
    destination = tmp_path / "prepared" / "unused.png"
    assert prepare_h3_image_dimensions(source, destination) == source
    assert not destination.exists()
    encoded = encode_h3_media_data_uri(source)
    assert base64.b64decode(encoded.split(",", 1)[1]) == before
    assert source.read_bytes() == before


@pytest.mark.parametrize(("size", "expected"), [((404, 254), (407, 256)), ((6000, 3000), (5760, 2880))])
def test_size_derivative_uses_uniform_lanczos_and_preserves_original(
    tmp_path: Path, size: tuple[int, int], expected: tuple[int, int],
) -> None:
    source = tmp_path / "reference.png"
    image = Image.new("RGBA", size, (120, 70, 30, 128))
    image.paste((10, 190, 230, 255), (0, 0, size[0] // 2, size[1] // 2))
    image.save(source)
    before = source.read_bytes()
    destination = tmp_path / "prepared" / "scaled.png"
    assert prepare_h3_image_dimensions(source, destination) == destination
    with Image.open(destination) as prepared:
        assert prepared.size == expected
        assert ImageChops.difference(prepared, image.resize(expected, Image.Resampling.LANCZOS)).getbbox(alpha_only=False) is None
    # Direct client use and worker preparation apply the same size transform.
    encoded = encode_h3_media_data_uri(source)
    with Image.open(BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as actual:
        assert actual.size == expected
        assert ImageChops.difference(actual, image.resize(expected, Image.Resampling.LANCZOS)).getbbox(alpha_only=False) is None
    assert source.read_bytes() == before
    assert list(destination.parent.iterdir()) == [destination]
    with pytest.raises(H3DefiniteRequestError, match="must not overwrite the source file"):
        prepare_h3_image_dimensions(source, source)
    assert source.read_bytes() == before


def test_sixth_reference_is_prepared_uploaded_mapped_and_persisted_without_changing_assets(tmp_path: Path) -> None:
    fake = ImageTransportClient()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    pid = project.document.project.id
    inputs: list[H3MediaInput] = []
    originals: dict[Path, bytes] = {}
    for index in range(6):
        path = tmp_path / f"reference-{index}.png"
        Image.new("RGB", (404, 254) if index == 5 else (320, 256), (index * 35, 60, 110)).save(path)
        originals[path] = path.read_bytes()
        inputs.append(H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path=str(path))))
    small = Path(inputs[5].source.local_path or "")
    stat = small.stat()
    old_key = hashlib.sha256(
        f"acedata-h3-image-jpeg95-padding-v2:{small.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    ).hexdigest()
    service.repository.save_provider_upload(cache_key=old_key, provider="minimax", record=H3UploadedInput(
        local_path=str(small), provider_file_id="mm_file://old-404x254", media_kind=H3MediaKind.IMAGE,
        role=H3MediaRole.REFERENCE_IMAGE, expires_at="2099-01-01T00:00:00+00:00"))
    request = H3ProbeRequest(shot_id=project.document.shots[0].id, route=VideoGenerationRoute.REFERENCE,
        media_inputs=inputs)
    probe = service.enqueue_h3_probe(pid, request)
    assert service.advance_task(probe.task.id).status == TaskStatus.SUBMITTING
    run = service.repository.get_provider_run(probe.task.id)
    assert run is not None and run.request is not None
    assert run.media_inputs == inputs and len(run.uploaded_inputs) == 6
    assert len(fake.upload_calls) == 6
    prepared_path = run.uploaded_inputs[5].prepared_local_path
    assert prepared_path and fake.upload_calls[5] == Path(prepared_path)
    assert Path(prepared_path).parent == service.h3_worker.workspace_root / pid / "h3-inputs"
    assert run.uploaded_inputs[5].local_path == str(small)
    with Image.open(prepared_path) as actual_upload:
        assert actual_upload.size == (407, 256)
    for index, uploaded in enumerate(run.uploaded_inputs):
        if index < 5:
            assert uploaded.prepared_local_path is None
            assert fake.upload_calls[index] == Path(inputs[index].source.local_path or "")
        item = run.request.content[index + 1]
        assert item.image_url and item.image_url.url == uploaded.provider_file_id
    assert service.advance_task(probe.task.id).status == TaskStatus.ACKNOWLEDGED
    sent = fake.submitted_requests[0].content[6].image_url
    assert sent
    with Image.open(BytesIO(base64.b64decode(sent.url.split(",", 1)[1]))) as submitted:
        assert submitted.size == (407, 256)
    reopened = _service(tmp_path, fake)
    persisted = reopened.repository.get_provider_run(probe.task.id)
    assert persisted and persisted.uploaded_inputs == run.uploaded_inputs
    second = reopened.enqueue_h3_probe(pid, request)
    assert reopened.advance_task(second.task.id).status == TaskStatus.SUBMITTING
    assert len(fake.upload_calls) == 6  # The corrected profile's upload is reusable.
    assert reopened.get_project(pid).document.assets == project.document.assets
    for path, before in originals.items():
        assert path.read_bytes() == before


def test_impossible_uniform_scale_fails_locally_before_upload_or_submit(tmp_path: Path) -> None:
    source = tmp_path / "extreme.png"
    Image.new("RGB", (6000, 100), "#123456").save(source)
    before = source.read_bytes()
    with pytest.raises(H3DefiniteRequestError, match=r"6000x100.*uniform scaling.*\[256, 5760\]"):
        encode_h3_media_data_uri(source)
    fake = ImageTransportClient()
    service = _service(tmp_path, fake)
    project = service.initialize_fixture()
    probe = service.enqueue_h3_probe(project.document.project.id, H3ProbeRequest(
        shot_id=project.document.shots[0].id, route=VideoGenerationRoute.REFERENCE,
        media_inputs=[H3MediaInput(kind=H3MediaKind.IMAGE, role=H3MediaRole.REFERENCE_IMAGE,
            source=H3MediaSource(local_path=str(source)))]))
    with pytest.raises(WorkflowConflictError, match="Select or derive a less elongated reference crop"):
        service.advance_task(probe.task.id)
    assert fake.upload_calls == [] and fake.submitted_requests == []
    task = service.repository.get_task(probe.task.id)
    assert task and task.status == TaskStatus.QUEUED and task.provider_task_id is None
    run = service.repository.get_provider_run(probe.task.id)
    assert run and run.request is None and not run.uploaded_inputs
    assert source.read_bytes() == before
