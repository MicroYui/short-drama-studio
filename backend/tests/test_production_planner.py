"""Planning regressions for structured locked-screenplay input."""

from __future__ import annotations

import json

from short_drama.models import (
    CreateProjectRequest,
    ProductionProfile,
    ProjectDocument,
    SourceMode,
)
from short_drama.production_planner import (
    ProductionPlan,
    _fallback_plan,
    _locked_dialogue,
    _planner_prompt,
    build_project_document_from_plan,
    create_production_plan,
    refresh_lightweight_reference_plans,
)


LOCKED_SCRIPT = """
《别相信我》
类型：真人都市悬疑短剧；普通话；雨夜便利店；约 45–60 秒。
角色：林夏，28 岁，短黑发，米白风衣，克制但警觉。周衡，32 岁，深灰西装，左眉有浅疤，表面平静。
核心道具：一支磨损红壳、无天线、无品牌文字的旧录音笔。

雨夜，便利店内。林夏与周衡隔着柜台对峙。
林夏：“你说他十点离开，可监控里，十点零三分他还在这里。”
周衡右手握着红色录音笔：“监控会骗人。”
林夏：“人也会。”
周衡把录音笔放到柜台，指示灯亮。录音笔中传出林夏的声音：“如果你听到这段话，别相信我。”
林夏听见自己的声音，神情动摇。
周衡：“现在，你还信自己吗？”
林夏左手拿起录音笔，转身离开便利店，走进雨夜。

要求：保留以上五句台词原文和说话人；规划为 8–10 个最终剪辑镜头。
""".strip()


class RepairingLanguageClient:
    def __init__(self, request: CreateProjectRequest) -> None:
        self.request = request
        self.calls = 0

    def validate_credentials(self) -> None:
        pass

    def generate_text(
        self,
        *,
        instructions: str,
        input_text: str,
        image_urls: list[str] | None = None,
        max_output_tokens: int = 2200,
    ) -> str:
        del instructions, input_text, image_urls, max_output_tokens
        self.calls += 1
        payload = _fallback_plan(self.request).model_dump(mode="json")
        if self.calls == 1:
            payload["shots"][0]["dialogue_line_indices"] = [0]
        return json.dumps(payload, ensure_ascii=False)


def _request() -> CreateProjectRequest:
    return CreateProjectRequest(
        name="别相信我",
        source_mode=SourceMode.SCRIPT_LOCKED,
        input_text=LOCKED_SCRIPT,
    )


def test_production_profiles_inject_distinct_policies_without_fixed_shot_count() -> None:
    general_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    douyin_request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
    )

    general_instructions, general_input = _planner_prompt(general_request)
    douyin_instructions, douyin_input = _planner_prompt(douyin_request)
    general_payload = json.loads(general_input)
    douyin_payload = json.loads(douyin_input)

    assert "Atmosphere, silence" in general_instructions
    assert "Do not invent conflict" in general_instructions
    assert "curiosity gap" in douyin_instructions
    assert "Never add shots" in douyin_instructions
    assert general_payload["production_profile"] == "GENERAL_VIDEO"
    assert douyin_payload["production_profile"] == "DOUYIN_SHORT_DRAMA"
    assert general_payload["requirements"]["shots"] == (
        "choose the smallest coherent executable set; no fixed count"
    )
    assert "6-10" not in general_instructions + general_input
    assert "6-10" not in douyin_instructions + douyin_input


def test_general_profile_accepts_silent_long_take_plan_without_density_gate() -> None:
    request = CreateProjectRequest(
        source_mode=SourceMode.AUTO_ORIGINAL,
        production_profile=ProductionProfile.GENERAL_VIDEO,
    )
    fallback = _fallback_plan(request)
    assert len(fallback.shots) == 4
    assert fallback.lines == []
    assert fallback.style.genre == "都市生活观察"
    payload = fallback.model_dump(mode="json")
    payload["lines"] = []
    payload["shots"] = payload["shots"][:3]
    for shot in payload["shots"]:
        shot["duration_seconds"] = 15.0
        shot["dialogue_line_indices"] = []

    plan = ProductionPlan.model_validate(payload)

    assert len(plan.shots) == 3
    assert sum(shot.duration_seconds for shot in plan.shots) == 45.0
    assert plan.lines == []


def test_legacy_project_document_defaults_to_douyin_profile() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-profile-backward-compatible",
        request=request,
        planning=planning,
        created_at="2026-08-21T00:00:00Z",
    )
    payload = document.model_dump(mode="json")
    del payload["project"]["production_profile"]

    restored = ProjectDocument.model_validate(payload)

    assert restored.project.production_profile == ProductionProfile.DOUYIN_SHORT_DRAMA


def test_locked_dialogue_ignores_metadata_and_classifies_recorded_voice() -> None:
    lines = _locked_dialogue(LOCKED_SCRIPT)

    assert [(line.speaker, line.audio_kind.value) for line in lines] == [
        ("林夏", "DIALOGUE"),
        ("周衡", "DIALOGUE"),
        ("林夏", "DIALOGUE"),
        ("录音笔中的林夏", "VOICEOVER"),
        ("周衡", "DIALOGUE"),
    ]
    assert [line.text for line in lines] == [
        "你说他十点离开，可监控里，十点零三分他还在这里。",
        "监控会骗人。",
        "人也会。",
        "如果你听到这段话，别相信我。",
        "现在，你还信自己吗？",
    ]


def test_locked_dialogue_uses_actor_at_action_start_and_voice_owner_at_source() -> None:
    lines = _locked_dialogue(
        "\n".join(
            [
                "角色：周衡，32 岁。林夏，28 岁。",
                "周衡看向林夏：“监控会骗人。”",
                "周衡按下录音笔，录音中传出林夏的声音：“别相信我。”",
            ]
        )
    )

    assert [(line.speaker, line.audio_kind.value) for line in lines] == [
        ("周衡", "DIALOGUE"),
        ("录音笔中的林夏", "VOICEOVER"),
    ]


def test_locked_fallback_does_not_turn_metadata_or_voiceover_into_characters() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-locked-parser",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )

    assert [character.name for character in planning.plan.characters] == ["林夏", "周衡"]
    assert [line.speaker for line in planning.plan.lines] == [
        "林夏",
        "周衡",
        "林夏",
        "录音笔中的林夏",
        "周衡",
    ]
    assert [
        asset.name
        for asset in document.assets
        if asset.kind.value == "CHARACTER"
    ] == ["林夏定妆", "周衡定妆"]


def test_invalid_language_plan_gets_one_structured_repair() -> None:
    request = _request()
    client = RepairingLanguageClient(request)

    planning = create_production_plan(request, client)

    assert client.calls == 2
    assert planning.planner == "gpt-5.6-sol-repaired"
    assert any("自动修复后采用" in warning for warning in planning.warnings)
    assert [character.name for character in planning.plan.characters] == ["林夏", "周衡"]
    assert all(
        index >= 1
        for shot in planning.plan.shots
        for index in shot.dialogue_line_indices
    )


def test_lightweight_reference_enrichment_uses_declared_shot_intent() -> None:
    request = _request()
    planning = create_production_plan(request, None)
    document = build_project_document_from_plan(
        project_id="project-lightweight-reference",
        request=request,
        planning=planning,
        created_at="2026-08-17T00:00:00Z",
    )
    shot = next(item for item in document.shots if item.sequence == 5)
    plan = next(item for item in document.reference_plan if item.shot_id == shot.id)
    prop = next(item for item in document.assets if item.kind.value == "PROP")
    prop_version_ids = {item.id for item in prop.versions}
    plan.bindings = [
        item for item in plan.bindings if item.target_id not in prop_version_ids
    ]
    plan.max_reference_images = 3

    changed = refresh_lightweight_reference_plans(document)

    assert changed is True
    assert any(item.target_id == prop.current_version_id for item in plan.bindings)
    assert plan.max_reference_images >= len(plan.bindings)
