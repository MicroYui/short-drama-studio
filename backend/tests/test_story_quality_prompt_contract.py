"""Check the actual role/stage composition, not merely available source files."""

import pytest

from short_drama.models import ProductionProfile
from short_drama.production_profiles import ProductionPromptStage, ProductionSkillRole, compile_production_prompt


@pytest.mark.parametrize(("role", "stage", "required"), [
    (ProductionSkillRole.CONCEPT, ProductionPromptStage.STORY,
     ("另一人或环境为何不配合", "因为刚才发生了什么", "开场的可见愿望与结尾回报", "不默认补成长篇查档")),
    (ProductionSkillRole.WRITER, ProductionPromptStage.STORY,
     ("叙述文字不是旁白", "明天几点", "related_scene_updates", "实际状态", "双方只在轮流交代信息")),
    (ProductionSkillRole.DIRECTOR, ProductionPromptStage.STORYBOARD,
     ("观众必须看清的证据", "实际发声者", "旁白描述的人物不必全出镜", "时间表是可行性的判断", "临时演读")),
])
def test_current_drama_roles_receive_concrete_quality_methods(
    role: ProductionSkillRole, stage: ProductionPromptStage, required: tuple[str, ...],
) -> None:
    prompt = compile_production_prompt(ProductionProfile.AI_SHORT_DRAMA, roles=(role,), stage=stage,
                                       method_skill_ids=("method.drama_series",))
    for phrase in required:
        assert phrase in prompt.text
    assert prompt.selected_method_skill_ids == ("method.drama_series",)
    assert "StageCapsule" not in prompt.text
    assert "submitMainAgent" not in prompt.text


def test_generic_writer_keeps_causal_revision_method_without_forcing_a_genre() -> None:
    prompt = compile_production_prompt(ProductionProfile.DIGITAL_HUMAN, roles=(ProductionSkillRole.WRITER,),
                                       stage=ProductionPromptStage.STORY)
    assert "实际要发声的内容与说话者" in prompt.text
    assert "不要求每次填写依赖表或增加审核轮次" in prompt.text
    assert "修鞋师傅" not in prompt.text
