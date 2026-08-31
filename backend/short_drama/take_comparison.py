"""Pairwise/multi-candidate Take comparison for the Director Agent."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator

from short_drama.models import (
    DomainModel,
    ProjectDocument,
    QaStatus,
    SemanticReviewStatus,
    ShotPlan,
    Take,
    TakeComparisonCandidateReview,
    TakeComparisonDecision,
    TakeComparisonReport,
    VideoInspectionReport,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.video_inspection import image_data_url, semantic_reference_images


class TakeComparisonJudgment(DomainModel):
    decision: TakeComparisonDecision
    selected_take_id: str | None = None
    candidate_reviews: list[TakeComparisonCandidateReview] = Field(
        min_length=2,
        max_length=5,
    )
    summary: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_selected_take(self) -> TakeComparisonJudgment:
        if self.decision == TakeComparisonDecision.SELECT_TAKE:
            if self.selected_take_id is None:
                raise ValueError("SELECT_TAKE requires selected_take_id")
        elif self.selected_take_id is not None:
            raise ValueError("Only SELECT_TAKE may include selected_take_id")
        return self


def compare_take_candidates(
    *,
    document: ProjectDocument,
    shot: ShotPlan,
    takes: list[Take],
    inspections: list[VideoInspectionReport],
    client: LanguageProviderClient,
    review_focus: str | None,
    created_at: str,
) -> TakeComparisonReport:
    """Compare candidates in one model call and allow an explicit no-winner result."""

    if len(takes) < 2:
        raise ValueError("Take comparison requires at least two candidates")
    if len(takes) > 5:
        raise ValueError("Take comparison accepts at most five candidates")
    if len({take.id for take in takes}) != len(takes):
        raise ValueError("Take comparison candidates must be unique")
    if any(take.project_id != document.project.id or take.shot_id != shot.id for take in takes):
        raise ValueError("All compared Takes must belong to the same project and shot")

    reports_by_take = {report.take_id: report for report in inspections}
    missing = [take.id for take in takes if take.id not in reports_by_take]
    if missing:
        raise ValueError("Missing video inspections for Takes: " + ", ".join(missing))

    reference_plan = next(
        (item for item in document.reference_plan if item.shot_id == shot.id),
        None,
    )
    image_urls: list[str] = []
    image_map: list[dict[str, str | int]] = []
    inspection_ids: list[str] = []
    for take in takes:
        report = reports_by_take[take.id]
        contact_sheet = Path(report.contact_sheet_path).expanduser().resolve()
        if not contact_sheet.is_file():
            raise ValueError(f"Inspection contact sheet is missing for Take {take.id}")
        image_urls.append(image_data_url(contact_sheet))
        image_map.append(
            {
                "index": len(image_urls),
                "role": "CANDIDATE_TIMELINE",
                "take_id": take.id,
            }
        )
        inspection_ids.append(report.id)
        if report.boundary_contact_sheet_path is not None:
            boundary = Path(report.boundary_contact_sheet_path).expanduser().resolve()
            if boundary.is_file():
                image_urls.append(image_data_url(boundary))
                image_map.append(
                    {
                        "index": len(image_urls),
                        "role": "CANDIDATE_PREVIOUS_BOUNDARY",
                        "take_id": take.id,
                    }
                )

    for label, url in semantic_reference_images(document, reference_plan):
        image_urls.append(url)
        image_map.append(
            {
                "index": len(image_urls),
                "role": "CANONICAL_ASSET_REFERENCE",
                "description": label,
            }
        )

    instructions = (
        "You are a pairwise Take comparison tool for a film-director Agent. Return JSON only "
        "and match the supplied schema. Compare all candidate timelines against the same ShotPlan, "
        "canonical references, and previous-shot boundary. Judge relative differences in character "
        "identity and wardrobe, scene and prop continuity, action and screen direction, dialogue or "
        "sound-source semantics, and cinematography. Do not choose a candidate merely because it is "
        "the least bad: return NONE_ACCEPTABLE when every candidate has a visible contradiction that "
        "makes it unsafe for the intended edit. Only a candidate already marked QA PASSED is eligible "
        "for SELECT_TAKE; the comparison does not override technical or semantic rejection. Sparse "
        "frames cannot prove an unseen event. Keep observations evidence-based, make no repair "
        "decision, and remember that the report is advisory and user-overridable. Use the full 0-5 "
        "relative_score range only to explain the comparison; do not pretend scores are calibrated "
        "across different comparison calls."
    )
    payload = {
        "output_schema": TakeComparisonJudgment.model_json_schema(),
        "shot": shot.model_dump(mode="json"),
        "candidates": [
            {
                "take": {
                    "id": take.id,
                    "candidate_index": take.candidate_index,
                    "route": take.route.value,
                    "qa_status": take.qa_status.value,
                    "qa_score": take.qa_report.score,
                    "qa_issues": [
                        issue.model_dump(mode="json") for issue in take.qa_report.issues
                    ],
                },
                "inspection": {
                    "id": reports_by_take[take.id].id,
                    "summary": reports_by_take[take.id].summary,
                    "issues": [
                        issue.model_dump(mode="json")
                        for issue in reports_by_take[take.id].issues
                    ],
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in reports_by_take[take.id].observations
                    ],
                },
            }
            for take in takes
        ],
        "eligible_take_ids": [
            take.id for take in takes if take.qa_status == QaStatus.PASSED
        ],
        "review_focus": review_focus,
        "image_map": image_map,
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=image_urls,
        max_output_tokens=4000,
    )
    judgment = TakeComparisonJudgment.model_validate_json(_strip_json_fence(output))
    known_ids = {take.id for take in takes}
    reviewed_ids = {item.take_id for item in judgment.candidate_reviews}
    if reviewed_ids != known_ids:
        raise ValueError("Take comparison must review every supplied candidate exactly once")
    if judgment.selected_take_id is not None:
        selected = next(
            (take for take in takes if take.id == judgment.selected_take_id),
            None,
        )
        if selected is None:
            raise ValueError("Take comparison selected an unknown candidate")
        if selected.qa_status != QaStatus.PASSED:
            raise ValueError("Take comparison selected a QA-rejected candidate")
    if judgment.decision == TakeComparisonDecision.SELECT_TAKE and not any(
        take.qa_status == QaStatus.PASSED for take in takes
    ):
        raise ValueError("Take comparison cannot select when no candidate passed QA")

    return TakeComparisonReport(
        id=f"take-comparison-{uuid4().hex}",
        project_id=document.project.id,
        shot_id=shot.id,
        candidate_take_ids=[take.id for take in takes],
        inspection_ids=inspection_ids,
        decision=judgment.decision,
        selected_take_id=judgment.selected_take_id,
        semantic_status=SemanticReviewStatus.COMPLETE,
        semantic_model="gpt-5.6-sol",
        candidate_reviews=judgment.candidate_reviews,
        summary=judgment.summary,
        advisory_only=True,
        created_at=created_at,
    )


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
