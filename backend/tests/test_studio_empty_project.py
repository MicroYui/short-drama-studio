"""An empty editor must not lock production to a scaffold's fictional duration."""

from pathlib import Path

from short_drama.models import CreateProjectRequest, ProductionDocumentOrigin, SourceMode, StoryIntent
from short_drama.production_contracts import ProductionSpecContent, ProductionSpecCreateRequest
from short_drama.service import ShortDramaService
from short_drama.studio_models import StudioProjectCreate, StudioSceneSave
from tests.test_storyboard_agent_interfaces import _production_spec


def _service(tmp_path: Path) -> ShortDramaService:
    return ShortDramaService(database_path=tmp_path / "studio.sqlite3", workspace_root=tmp_path / "media")


def test_blank_workspace_accepts_authored_duration_without_rewriting_script(tmp_path: Path) -> None:
    service = _service(tmp_path)
    document = service.studio.create_project(StudioProjectCreate(name="午饭")).document
    project_id = document.project.id
    assert document.script_versions[-1].story_intent is None
    assert document.script_versions[-1].ending_contract is None
    service.studio.remember_initial_request(project_id, "完成一部45–60秒的食堂短片。")
    saved = service.studio.save_scene(project_id, StudioSceneSave(heading="食堂", screenplay_text="已保存的正文。"))
    before = saved.project.document.script_versions[-1].model_copy(deep=True)
    payload = _production_spec(service, project_id).model_dump(mode="json")
    payload.update(target_duration_seconds=52, duration_contract={
        "target_seconds": 52, "target_origin": "USER_DECLARED", "authority": "TARGET_LED",
        "acceptable_range_seconds": {"minimum_seconds": 45, "maximum_seconds": 60},
        "source_fidelity_policy": "PRESERVE_STORY_COMMITMENTS", "mismatch_resolution": "REVISE_SCRIPT",
        "decision_basis": "用户要求45–60秒，Agent估算为52秒。",
    })
    service.create_production_spec(project_id, ProductionSpecCreateRequest(
        spec=ProductionSpecContent.model_validate(payload), origin=ProductionDocumentOrigin.AGENT,
    ))
    current = service.get_project(project_id).document
    assert current.project.target_duration_seconds == 52
    assert current.script_versions[-1] == before


def _legacy_project(service: ShortDramaService) -> tuple[str, StoryIntent]:
    template = service.create_project(CreateProjectRequest(name="旧骨架", input_text="等待创作要求", source_mode=SourceMode.IDEA_EXPANSION)).document
    intent = template.script_versions[-1].story_intent
    assert intent is not None
    document = service.studio.create_project(StudioProjectCreate(name="旧空白作品")).document
    document.script_versions[0].story_intent = intent.model_copy(deep=True)
    document.script_versions[0].ending_contract = template.script_versions[-1].ending_contract
    service.repository.save_project(document)
    service.studio.save_scene(document.project.id, StudioSceneSave(heading="食堂", screenplay_text="用户已保存的场次。"))
    return document.project.id, intent


def test_resume_clears_only_inherited_placeholder_and_keeps_saved_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, intent = _legacy_project(service)
    before = service.get_project(project_id).document.script_versions[-1]
    assert service.studio.remember_initial_request(project_id, "45–60秒，做到成片。")
    document = service.get_project(project_id).document
    current = document.script_versions[-1]
    assert current.story_intent is None and current.ending_contract is None
    assert current.id == before.id and current.structured_scenes == before.structured_scenes
    assert current.screenplay_text == before.screenplay_text
    assert document.script_versions[0].story_intent == intent
    assert not service.studio.remember_initial_request(project_id, "后来的修改意见。")
    assert service.get_project(project_id).document.project.input_text == "45–60秒，做到成片。"


def test_resume_preserves_authored_story_decisions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    project_id, intent = _legacy_project(service)
    document = service.get_project(project_id).document
    authored = intent.model_copy(deep=True, update={"protected_elements": ["用户确认的故事承诺"]})
    document.script_versions[-1].story_intent = authored
    service.repository.save_project(document)
    service.studio.remember_initial_request(project_id, "本次制作要求。")
    assert service.get_project(project_id).document.script_versions[-1].story_intent == authored
