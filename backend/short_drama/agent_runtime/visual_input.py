"""Supply exact images from the latest Tool decision, including read batches."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue, ValidationError

from short_drama.agent_runtime.models import SpecialistTurnRecord, SpecialistTurnStatus, WorkOrder
from short_drama.image_observation import VisualAttachment, encode_attachment


@dataclass(frozen=True)
class PreparedVisualInput:
    image_urls: tuple[str, ...]
    observations: dict[str, JsonValue]


def prepare_worker_visual_input(work_order: WorkOrder, *, project_id: str) -> PreparedVisualInput | None:
    if not work_order.specialist_turns:
        return None
    turn = max(work_order.specialist_turns, key=lambda item: item.sequence)
    if turn.batch_id is None:
        return _prepare_turn_visual_input(turn, project_id=project_id)
    turns = sorted((item for item in work_order.specialist_turns if item.batch_id == turn.batch_id),
        key=lambda item: item.sequence)
    if any(item.status not in {SpecialistTurnStatus.COMPLETED, SpecialistTurnStatus.INTERRUPTED} for item in turns):
        return None
    urls: list[str] = []
    images: list[JsonValue] = []
    unavailable: list[JsonValue] = []
    attempts: list[JsonValue] = []
    has_selections = False
    for item in turns:
        identity: dict[str, JsonValue] = {
            "tool_attempt_id": item.id, "source_sequence": item.sequence, "tool_name": item.tool_name,
            "updated_at": item.updated_at.isoformat(),
        }
        attempts.append({**identity, "status": item.status.value,
            "result_status": item.result.get("status") if isinstance(item.result, dict) else None})
        prepared = _prepare_turn_visual_input(item, project_id=project_id)
        if prepared is None:
            continue
        has_selections = True
        selected_images = prepared.observations["images"]
        if isinstance(selected_images, list):
            for selected in selected_images:
                if isinstance(selected, dict):
                    images.append({**selected, **identity, "image_index": len(images) + 1})
        selected_unavailable = prepared.observations["unavailable"]
        if isinstance(selected_unavailable, list):
            unavailable.extend({**selected, **identity} for selected in selected_unavailable if isinstance(selected, dict))
        urls.extend(prepared.image_urls)
    if not has_selections:
        return None
    return PreparedVisualInput(tuple(urls), {
        "batch_id": turn.batch_id, "tool_attempts": attempts, "images": images, "unavailable": unavailable,
        "guidance": (
            "The attached images, in image_index order, are exact explicit selections from successful Tools in your latest read batch. "
            "Each image and unavailable selection identifies its own tool_attempt_id, source_sequence and tool_name; "
            "selection_index is local to that Tool, while image_index indexes this decision's attached images. "
            "Failed or interrupted Tools supply no images. You can inspect the attached pixels now; viewing does not approve, "
            "adopt, complete production or create a QA score. Do not claim an unavailable image was observed. "
            "Use recorded purpose, status and dimensions, and save useful findings with exact version and purpose."
        ),
    })


def _prepare_turn_visual_input(turn: SpecialistTurnRecord, *, project_id: str) -> PreparedVisualInput | None:
    result = turn.result
    if turn.status != SpecialistTurnStatus.COMPLETED or not isinstance(result, dict) or result.get("status") != "SUCCEEDED":
        return None
    selections = result.get("visual_attachments")
    if not isinstance(selections, list) or not selections:
        # Older records remain readable; never guess their images from current.
        return None
    urls: list[str] = []
    images: list[JsonValue] = []
    unavailable: list[JsonValue] = []
    for selection_index, selection in enumerate(selections, 1):
        identity = ({key: value for key, value in selection.items()
            if key in {"artifact_ref", "asset_id", "role", "region"}} if isinstance(selection, dict) else None)
        try:
            selected = VisualAttachment.model_validate(selection)
            if selected.artifact_ref.project_id != project_id:
                raise ValueError("Visual selection belongs to a different project")
            encoded = encode_attachment(selected)
        except ValidationError as exc:
            unavailable.append({"selection_index": selection_index, "selection": identity,
                "error": "Invalid image selection metadata: " + "; ".join(
                    "/".join(str(part) for part in issue["loc"]) + ": " + issue["type"]
                    for issue in exc.errors(include_input=False, include_context=False))})
            continue
        except (ValueError, OSError) as exc:
            unavailable.append({"selection_index": selection_index, "selection": identity, "error": str(exc)})
            continue
        urls.append(encoded.image_url)
        images.append({**encoded.metadata, "image_index": len(urls), "selection_index": selection_index})
    return PreparedVisualInput(tuple(urls), {
        "tool_attempt_id": turn.id, "source_sequence": turn.sequence, "tool_name": turn.tool_name,
        "completed_at": turn.updated_at.isoformat(), "images": images, "unavailable": unavailable,
        "guidance": (
            "The attached images, in image_index order, are the exact selections from your latest completed Tool. "
            "You can inspect these pixels in this decision; a separate QA request is not needed merely to see them. "
            "Use the recorded purpose, status and observation dimensions. Rejected/stale candidates may be shown for diagnosis; "
            "viewing does not approve, adopt, complete production, or create a QA score. "
            "Do not claim an unavailable image was observed. Save useful findings with exact version and purpose; "
            "use image.view for an earlier exact image or native region, or professional QA when it resolves a remaining question."
        ),
    })
