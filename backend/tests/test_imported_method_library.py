"""The expanded library remains selectable, scoped and honest about execution."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from _routes.short_drama import router
from short_drama.models import ProductionProfile
from short_drama.production_profiles import (
    compile_production_prompt,
    production_method_skill,
    production_method_skill_catalog,
)
from short_drama.service import ShortDramaService
from short_drama.skills.loader import DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT
from short_drama.skills.models import MethodCategory, ProductionPromptStage, ProductionSkillRole
from short_drama.studio_trace import production_trace, trace_context


ADDED_METHODS = {
    "title_sequence", "educational_video", "video_deconstruction", "ecommerce_images",
    "visual_packaging", "creator_video", "ui_motion", "anime_game_promo",
    "blender_workflow", "compositing_workflow", "image_editing_workflow",
    "procedural_3d", "realtime_visuals", "voice_reference", "skill_authoring", "skill_review",
}


def test_all_imported_methods_are_in_the_product_capability_catalog(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    methods = {item.id: item for item in service.get_agent_capabilities().production_methods}
    assert {f"method.{name}" for name in ADDED_METHODS} <= methods.keys()
    assert len(methods) == 27
    assert {item.category for item in methods.values()} == set(MethodCategory)
    assert all("minimax" not in f"{item.id} {item.label} {item.description}".lower() for item in methods.values())
    for item in methods.values():
        if item.category in {MethodCategory.SOFTWARE, MethodCategory.AUDIO, MethodCategory.MAINTENANCE}:
            assert item.requirements


@pytest.mark.parametrize("name", sorted(ADDED_METHODS))
def test_new_method_only_loads_the_selected_role_and_preserves_execution_conditions(name: str) -> None:
    method = production_method_skill(f"method.{name}")
    role = ProductionSkillRole.PLANNER
    body = method.prompt_for(role, stage=ProductionPromptStage.ORCHESTRATION)
    assert body is not None
    compiled = compile_production_prompt(
        ProductionProfile.GENERAL_VIDEO, roles=(role,), include_constitution=False,
        method_skill_ids=(method.id, method.id), stage=ProductionPromptStage.ORCHESTRATION,
    )
    assert compiled.selected_method_skill_ids == (method.id,)
    assert compiled.text.count(body) == 1
    assert method.constitution not in compiled.text
    for requirement in method.requirements:
        assert requirement in compiled.text
    other = production_method_skill("method.drama_series")
    assert other.role_prompts[ProductionSkillRole.DIRECTOR] not in compiled.text
    assert "minimax" not in compiled.text.casefold()


def test_post_production_uses_the_imported_editing_knowledge_and_emits_trace(tmp_path: Path) -> None:
    with production_trace(tmp_path, lambda _kind, _payload: None), trace_context(run_id="library-test", agent_role="director"):
        compiled = compile_production_prompt(
            ProductionProfile.GENERAL_VIDEO, roles=(ProductionSkillRole.DIRECTOR,),
            method_skill_ids=("method.ui_motion",), stage=ProductionPromptStage.POST,
            include_constitution=False,
        )
    method = production_method_skill("method.ui_motion")
    post = method.prompt_for(ProductionSkillRole.DIRECTOR, stage=ProductionPromptStage.POST)
    assert post is not None and post in compiled.text
    assert method.role_prompts[ProductionSkillRole.DIRECTOR] not in compiled.text
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    loaded = [record for record in records if record["kind"] == "skills.loaded"]
    assert len(loaded) == 1
    assert loaded[0]["data"]["run_id"] == "library-test"
    assert [skill["id"] for skill in loaded[0]["data"]["skills"]] == ["method.ui_motion"]


def test_method_detail_exposes_software_requirements_and_brand_neutral_overviews() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/short-drama/studio/methods/method.blender_workflow")
        assert response.status_code == 200
        payload = response.json()
        assert payload["category"] == "software" and payload["requirements"]
        assert payload["sections"] and payload["upstream_sources"]
        assert "minimax" not in payload["principles"].lower()
        legacy = client.get("/api/short-drama/studio/methods/method.minimax_h3.brand_promo_video")
        assert legacy.status_code == 200 and legacy.json()["id"] == "method.brand_promo"


def test_imported_source_index_has_one_real_runtime_destination_per_package() -> None:
    root = DEFAULT_PRODUCTION_METHOD_SKILLS_ROOT.parent
    index = root / "sources/design_library/3.0.14/catalog.json"
    entries = json.loads(index.read_text())
    assert len(entries) == 19 and len({entry["source"] for entry in entries}) == 19
    for entry in entries:
        assert (index.parent / entry["source"] / "SKILL.md").is_file()
        assert (index.parent / entry["source"] / "meta.yaml").is_file()
        assert (root / entry["target"] / "SKILL.md").is_file()
        if entry["method_id"] is not None:
            assert production_method_skill(entry["method_id"]).upstream_sources


def test_new_method_conditions_are_in_the_same_descriptor_used_by_the_leader() -> None:
    from short_drama.production_manager import _agent_method_skill_descriptor

    descriptors = production_method_skill_catalog()
    for descriptor in descriptors:
        agent = _agent_method_skill_descriptor(descriptor)
        assert agent.category == descriptor.category
        assert agent.requirements == list(descriptor.requirements)
        assert agent.supported_roles == list(descriptor.supported_roles)
