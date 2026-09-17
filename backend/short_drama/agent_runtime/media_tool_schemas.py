"""Load adjacent media contracts when their persisted objects become available."""
from __future__ import annotations

from short_drama.agent_runtime.models import SpecialistTurnStatus, WorkOrder


def media_tool_schema_ids(work_order: WorkOrder, project_id: str) -> list[str]:
    """Expose useful contracts, without scheduling actions or judging media.

    An enqueued task needs lifecycle controls; a returned Take makes observation
    and adoption meaningful. Neither requires another model turn just to ask
    for those schemas. Other tools remain available through normal discovery.
    """
    kinds = {ref.artifact_type for ref in work_order.scope if ref.project_id == project_id}
    returned_video = "take" in kinds
    for turn in work_order.specialist_turns:
        if turn.status != SpecialistTurnStatus.COMPLETED or turn.result is None:
            continue
        result = turn.result
        if result.get("status") != "SUCCEEDED":
            continue
        refs = result.get("artifact_refs")
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, dict) and ref.get("project_id") == project_id:
                    kind = ref.get("artifact_type")
                    if isinstance(kind, str):
                        kinds.add(kind)
        output = result.get("output")
        if not isinstance(output, dict) or output.get("project_id") != project_id:
            continue
        if turn.tool_name in {"task.get", "task.advance"} and output.get("status") == "SUCCEEDED":
            returned_video = True
        if turn.tool_name in {"route.prepare_h3_group", "route.get_h3_group_operation"}:
            statuses = output.get("task_statuses")
            if isinstance(statuses, dict) and "SUCCEEDED" in statuses.values():
                returned_video = True
    ids: list[str] = []
    if "h3_group_operation" in kinds:
        ids.append("route.get_h3_group_operation")
    if "generation_task" in kinds:
        ids.extend(("task.get", "task.advance"))
    if returned_video or "take" in kinds:
        ids.extend(("video.inspect_take", "audio.transcribe_native", "route.review_h3_take", "video.select_take"))
    if "video_inspection" in kinds:
        ids.append("video.get_take_inspection")
    return ids
