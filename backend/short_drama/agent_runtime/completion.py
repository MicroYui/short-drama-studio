"""Verify explicitly claimed outputs against saved state, without judging the goal."""

from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from short_drama.agent_runtime.artifact_resolver import ProjectArtifactResolver, ProjectDocumentStore
from short_drama.agent_runtime.contract_validation import AgentResultContractError
from short_drama.agent_runtime.models import ArtifactRef
from short_drama.models import AssetMediaType, TakeAvailability


_FILE_FIELDS = {"file_path", "output_path", "media_path", "local_path", "video_path", "clean_video_path", "subtitled_video_path", "srt_path"}


def _file_receipt(value: str | None, *, field: str) -> dict[str, JsonValue]:
    if not value:
        raise AgentResultContractError(f"{field}: the claimed output has no saved file path; save the output or report PARTIAL")
    path = Path(value).expanduser()
    try:
        if not path.is_file() or (path.stat().st_size == 0 and not field.endswith("/srt_path")):
            raise OSError("missing or empty file")
        # Opening is a cheap availability check. It does not claim semantic media QA.
        with path.open("rb") as source:
            source.read(1)
        size = path.stat().st_size
    except OSError as exc:
        raise AgentResultContractError(f"{field}: saved file is not usable: {value}; restore the file or report PARTIAL") from exc
    return {"path": str(path.resolve()), "size_bytes": size, "availability": "LOCAL_FILE"}


def _deliverable_claims(value: JsonValue, path: str = "/deliverable") -> tuple[list[ArtifactRef], list[dict[str, JsonValue]]]:
    refs: list[ArtifactRef] = []
    files: list[dict[str, JsonValue]] = []
    if isinstance(value, dict):
        if {"project_id", "artifact_type", "artifact_id"} <= value.keys():
            try:
                refs.append(ArtifactRef.model_validate({key: value[key] for key in ("project_id", "artifact_type", "artifact_id", "version") if key in value}))
            except ValueError as exc:
                raise AgentResultContractError(f"{path}: invalid claimed ArtifactRef: {exc}") from exc
        for key, item in value.items():
            pointer = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key in _FILE_FIELDS and isinstance(item, str):
                files.append({"field": pointer, **_file_receipt(item, field=pointer)})
            else:
                nested_refs, nested_files = _deliverable_claims(item, pointer)
                refs.extend(nested_refs)
                files.extend(nested_files)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested_refs, nested_files = _deliverable_claims(item, f"{path}/{index}")
            refs.extend(nested_refs)
            files.extend(nested_files)
    return refs, files


def verify_completion_claims(
    projects: ProjectDocumentStore,
    *,
    project_id: str,
    artifact_refs: Sequence[ArtifactRef],
    expected_output_slot_ids: Sequence[str] = (),
    deliverable: dict[str, JsonValue] | None = None,
) -> list[dict[str, JsonValue]]:
    """Return factual receipts only for named outputs; analysis can have no artifacts.

    Queued task records may be legitimate deliverables. Their receipt exposes the
    actual task status and never promotes submission into completed media.
    """
    document = projects.get_project(project_id)
    if document is None:
        raise AgentResultContractError(f"project_id={project_id}: project does not exist")
    nested_refs, files = _deliverable_claims(deliverable or {})
    refs = [*artifact_refs, *nested_refs]
    for slot_id in expected_output_slot_ids:
        slot = next((item for item in document.output_slots if item.id == slot_id), None)
        if slot is None or slot.current_artifact_ref is None or slot.invalidation_reason:
            raise AgentResultContractError(f"expected_output_slot_ids/{slot_id}: no usable current output is bound; save/bind the named output or report PARTIAL")
        refs.append(ArtifactRef.model_validate(slot.current_artifact_ref.model_dump()))
    unique = {ref.identity(): ref for ref in refs}
    refs = list(unique.values())
    for ref in refs:
        if ref.project_id != project_id:
            raise AgentResultContractError(f"{ref.artifact_type}/{ref.artifact_id}: claimed output belongs to project {ref.project_id}, not {project_id}")
    resolver = ProjectArtifactResolver(projects)
    # Studio exports are stored in the current ProjectDocument rather than the legacy export table.
    ordinary_refs = [ref for ref in refs if ref.artifact_type != "studio_export"]
    summaries = resolver.summarize_artifacts(ordinary_refs)
    resolved = {ref.identity(): summary for ref, summary in zip(ordinary_refs, summaries, strict=True)}
    receipts: list[dict[str, JsonValue]] = []
    for ref in refs:
        field = f"{ref.artifact_type}/{ref.artifact_id}"
        state: dict[str, JsonValue] = {}
        media: list[dict[str, JsonValue]] = []
        if ref.artifact_type == "studio_export":
            record = next((item for item in document.studio_exports if item.get("id") == ref.artifact_id), None)
            if record is None or record.get("status") != "completed":
                raise AgentResultContractError(f"{field}: the claimed export is absent or incomplete; inspect the export or report PARTIAL")
            if ref.version is not None and record.get("editorial_revision") != ref.version:
                raise AgentResultContractError(f"{field}: editorial revision does not match the saved export")
            state = {key: record.get(key) for key in ("status", "editorial_revision", "duration_seconds")}
            video_path = record.get("video_path")
            media.append(_file_receipt(video_path if isinstance(video_path, str) else None, field=f"{field}/video_path"))
            for key in ("clean_video_path", "srt_path"):
                value = record.get(key)
                if isinstance(value, str):
                    media.append(_file_receipt(value, field=f"{field}/{key}"))
        else:
            if resolved.get(ref.identity()) is None:
                raise AgentResultContractError(f"{field}: claimed artifact/version was not found in saved state; return the ArtifactRef from the successful write or report PARTIAL")
            if ref.artifact_type == "take":
                take = projects.get_take(ref.artifact_id)
                assert take is not None
                if take.availability != TakeAvailability.AVAILABLE:
                    raise AgentResultContractError(f"{field}: take availability is {take.availability.value}; restore its media or report PARTIAL")
                media.append(_file_receipt(take.media_path, field=f"{field}/media_path"))
                state = {"availability": take.availability.value, "freshness": take.freshness.value}
            elif ref.artifact_type == "export":
                summary = resolved[ref.identity()]
                assert summary is not None
                record = cast(dict[str, JsonValue], json.loads(summary.summary))
                for key in ("clean_video_path", "subtitled_video_path", "srt_path"):
                    value = record.get(key)
                    media.append(_file_receipt(value if isinstance(value, str) else None, field=f"{field}/{key}"))
            elif ref.artifact_type == "asset_version":
                version = next(item for asset in document.assets for item in asset.versions if item.id == ref.artifact_id)
                state = {"approval_status": version.approval_status.value, "freshness": version.freshness.value}
                if version.media_type != AssetMediaType.TEXT_REFERENCE:
                    if version.local_path:
                        media.append(_file_receipt(version.local_path, field=f"{field}/local_path"))
                    elif version.external_url:
                        media.append({"url": version.external_url, "availability": "REMOTE_NOT_CHECKED"})
                    else:
                        _file_receipt(None, field=f"{field}/local_path")
            elif ref.artifact_type in {"generation_task", "image_task"}:
                task = projects.get_task(ref.artifact_id) if ref.artifact_type == "generation_task" else projects.get_image_task(ref.artifact_id)
                assert task is not None
                state["status"] = task.status.value
            elif ref.artifact_type == "editorial_timeline":
                state["editorial_revision"] = (document.editorial_timeline or {}).get("revision")
        receipts.append({"artifact_ref": cast(JsonValue, ref.model_dump(mode="json")), "record_verified": True,
                         "current_state": state, "files": cast(JsonValue, media)})
    if files:
        receipts.append({"files": cast(JsonValue, files)})
    return receipts
