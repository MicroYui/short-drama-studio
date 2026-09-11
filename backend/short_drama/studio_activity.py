"""Current persisted execution state for the product task panel."""

from __future__ import annotations

from typing import TYPE_CHECKING
from pydantic import JsonValue

from short_drama.agent_runtime.token_budget import ContextBudget
from short_drama.studio_models import StudioActivity, StudioWorker
from short_drama.studio_review_scope import ReviewScope, pending_review_scope

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def current_activity(service: ShortDramaService, project_id: str, after: int = 0) -> StudioActivity:
    jobs = service.repository.list_studio_jobs(project_id)
    workers: list[StudioWorker] = []
    run_id = jobs[0].run_id if jobs else None
    if run_id is not None:
        store = service.agent_runtime.store
        work_orders = store.list_work_orders(run_id)
        document = service.repository.get_project(project_id)
        review = ReviewScope()
        if document is not None and document.studio_changes:
            snapshot = service.agent_runtime.snapshot(run_id)
            review = pending_review_scope(document, run_id, work_orders,
                snapshot.current_plan.steps if snapshot.current_plan else [], snapshot.actions)
        for work in work_orders:
            result = store.get_agent_result(work.id)
            latest = work.specialist_turns[-1] if work.specialist_turns else None
            payload = next((turn.result for turn in reversed(work.specialist_turns) if turn.result is not None), dict[str, JsonValue]())
            error, receipt = payload.get("error"), payload.get("receipt")
            created = [*result.created_artifacts, *result.updated_artifacts] if result is not None else []
            workers.append(StudioWorker(id=work.id, run_id=run_id, role=work.recipient_role,
                skill_role=work.production_skill_role, stage=work.production_prompt_stage,
                objective=work.objective, status=work.status.value,
                next_owner=None if work.next_owner is None else work.next_owner.value,
                summary=None if result is None else result.concise_summary, tool_turns=len(work.specialist_turns),
                latest_tool=latest.tool_name if latest else None, latest_reason=latest.reason if latest else None,
                waiting_review=work.id in review.work_order_ids,
                waiting_until=work.continuation_not_before.isoformat() if work.continuation_not_before else None,
                latest_error=str(error["message"]) if isinstance(error, dict) and error.get("message") else None,
                latest_receipt=receipt if isinstance(receipt, dict) else None,
                completed_artifact_count=len({(ref.artifact_type, ref.artifact_id) for ref in created}),
                selected_method_skill_ids=work.context_manifest.selected_skill_ids if work.context_manifest else []))
    return StudioActivity(jobs=jobs, workers=workers, context_window_tokens=ContextBudget().window_tokens,
        events=service.repository.list_studio_events(project_id, after=after, limit=2000))
