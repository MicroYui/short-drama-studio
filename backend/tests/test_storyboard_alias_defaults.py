"""The unchanged Fresh5 rejected prefix must save through the actual Registry."""
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from short_drama.models import ProjectDocument, ShotPlan, ShotReferenceCue, ShotTimelineBeat
from short_drama.production_contracts import StoryboardDraftContent, StoryboardDraftProgress
from short_drama.service import ShortDramaService
from short_drama.tools.contracts import ToolResult, ToolCallStatus


FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/fresh5_storyboard_alias.json").read_text()
)
PROJECT_ID: str = FIXTURE["source_project_id"]
PAPER_ID = "asset-08548f4926f3"


def _no_provider(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("A storyboard save must not call a Provider")


def _service(tmp_path: Path) -> ShortDramaService:
    service = ShortDramaService(
        database_path=tmp_path / "alias.sqlite3", workspace_root=tmp_path / "media",
        image_provider_factory=_no_provider, h3_client_factory=_no_provider,
        language_provider_factory=_no_provider,
    )
    service.repository.save_project(ProjectDocument.model_validate(FIXTURE["seed_project"]))
    return service


def _invoke(
    service: ShortDramaService, sequence: int, previous: ToolResult | None = None,
    *, arguments: dict[str, Any] | None = None,
) -> ToolResult:
    turn: dict[str, Any] = FIXTURE["turns"][str(sequence)]
    args = deepcopy(arguments if arguments is not None else turn["arguments"])
    if previous is not None:
        assert previous.output is not None
        progress = StoryboardDraftProgress.model_validate(previous.output["progress"])
        args["request"]["draft_document_id"] = progress.draft_ref.artifact_id
        for key in ("base_version_id", "draft_version_id"):
            if key in args["request"]:
                args["request"][key] = progress.draft_version_id
    original = deepcopy(args)
    result = service.tool_registry.invoke(turn["tool_name"], args)
    assert args == original
    return result


def _paper_cues(shot: ShotPlan) -> list[ShotReferenceCue]:
    return [cue for beat in shot.timeline_beats for cue in beat.references
            if cue.subject_asset_id == PAPER_ID]


def _original_shot() -> dict[str, Any]:
    return deepcopy(FIXTURE["turns"]["4"]["arguments"]["request"]["groups"][0]["shots"][0])


def test_actual_failed_prefix_saves_without_rewriting_six_shots_and_survives_reopen(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original_fixture = deepcopy(FIXTURE)
    result = _invoke(service, 2)
    for sequence in (3, 4, 7, 8):
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        result = _invoke(service, sequence, result)
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        if sequence == 4:
            assert result.output is not None
            progress = StoryboardDraftProgress.model_validate(result.output["progress"])
            current = StoryboardDraftContent.model_validate(service.get_production_document(
                PROJECT_ID, progress.draft_ref.artifact_id,
            ).version.payload)
            assert sum(len(group.shots) for group in current.committed_groups) == 13
            assert service.get_project(PROJECT_ID).document.shots == []
    reopened = ShortDramaService(database_path=tmp_path / "alias.sqlite3",
                                workspace_root=tmp_path / "reopened")
    saved = reopened.get_project(PROJECT_ID).document
    assert len(saved.shots) == 16
    for shot_id in ("shot-andingao-08", "shot-andingao-10", "shot-andingao-11"):
        shot = next(item for item in saved.shots if item.id == shot_id)
        owner = next(cue for cue in shot.references if cue.subject_asset_id == PAPER_ID)
        assert owner.required_tags and _paper_cues(shot)
        assert all(cue.required_tags == owner.required_tags for cue in _paper_cues(shot))
    assert FIXTURE == original_fixture


def test_explicit_conflicting_state_still_rejects_entire_prefix_without_losing_saved_shots(tmp_path: Path) -> None:
    service = _service(tmp_path)
    begun = _invoke(service, 2)
    prefix = _invoke(service, 3, begun)
    assert prefix.status == ToolCallStatus.SUCCEEDED
    before = service.get_project(PROJECT_ID).document.model_dump(mode="json")
    arguments = deepcopy(FIXTURE["turns"]["4"]["arguments"])
    shot = arguments["request"]["groups"][0]["shots"][0]
    cue = next(c for beat in shot["timeline_beats"] for c in beat["references"]
               if c["subject_asset_id"] == PAPER_ID)
    cue["required_tags"] = ["explicitly-different-state"]
    result = _invoke(service, 4, prefix, arguments=arguments)
    assert result.status == ToolCallStatus.INVALID_ARGUMENTS
    assert service.get_project(PROJECT_ID).document.model_dump(mode="json") == before


@pytest.mark.parametrize("field,value", [
    ("subject_asset_id", "another-subject"),
    ("asset_version_id", "another-exact-image"),
    ("required_tags", ["different-state"]),
])
def test_inheritance_never_overwrites_explicit_identity_media_or_state(field: str, value: Any) -> None:
    raw = _original_shot()
    cue = next(c for beat in raw["timeline_beats"] for c in beat["references"]
               if c["subject_asset_id"] == PAPER_ID)
    cue[field] = value
    with pytest.raises(ValidationError, match="alias cannot identify different"):
        ShotPlan.model_validate(raw)


def test_tags_are_a_set_but_save_preserves_declared_order_and_caller_instances() -> None:
    raw = _original_shot()
    owner = next(c for c in raw["references"] if c["subject_asset_id"] == PAPER_ID)
    owner["required_tags"] = ["initial", "portrait"]
    beats = [ShotTimelineBeat.model_validate(beat) for beat in raw["timeline_beats"]]
    cues = [c for b in beats for c in b.references if c.subject_asset_id == PAPER_ID]
    cues[-1].required_tags = ["portrait", "initial"]
    original = [beat.model_dump(mode="json") for beat in beats]
    raw["timeline_beats"] = beats
    saved = ShotPlan.model_validate(raw)
    assert [beat.model_dump(mode="json") for beat in beats] == original
    assert _paper_cues(saved)[0].required_tags == ["initial", "portrait"]
    assert _paper_cues(saved)[-1].required_tags == ["portrait", "initial"]


def test_missing_declaration_does_not_borrow_tags_from_other_shots_or_beats() -> None:
    raw = _original_shot()
    raw["references"] = [c for c in raw["references"] if c["subject_asset_id"] != PAPER_ID]
    saved = ShotPlan.model_validate(raw)
    assert all(not cue.required_tags for cue in _paper_cues(saved))
    _paper_cues(saved)[-1].required_tags = ["cannot-be-inherited-backward"]
    with pytest.raises(ValidationError, match="alias cannot identify different"):
        ShotPlan.model_validate(saved.model_dump(mode="json"))
