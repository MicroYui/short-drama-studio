"""Asset prompt and semantic-review scope regressions."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from short_drama.asset_inspection import (
    build_base_asset_prompt,
    build_stock_grounded_asset_prompt,
    inspect_asset_image,
)
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.image_models import AssetImportLocalRequest
from short_drama.language_models import LanguageProviderConfigUpdate
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetLayoutProfile,
    AssetMediaType,
    AssetPresentationMode,
    AssetReferenceStrategy,
    CoreAssetBrief,
    ProductionProfile,
    ReferenceBinding,
    ReferenceRequirement,
    ReferenceRole,
    ShotReferenceCue,
    ShotTimelineBeat,
)
from short_drama.service import ShortDramaService
from short_drama.tools import ToolCallStatus


class CapturingLanguageClient:
    def __init__(self) -> None:
        self.instructions = ""
        self.input_text = ""
        self.image_urls: list[str] = []

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
        del max_output_tokens
        self.instructions = instructions
        self.input_text = input_text
        self.image_urls = list(image_urls or [])
        return json.dumps(
            {
                "recommended": True,
                "score": 4.5,
                "issues": [],
                "summary": "The prop is coherent across the reference views.",
            }
        )


def _brief(
    identity: str,
    *,
    presentation_mode: AssetPresentationMode | None = None,
    required_visible_text: list[str] | None = None,
) -> CoreAssetBrief:
    return CoreAssetBrief(
        identity_anchor=identity,
        visual_attributes=[identity],
        presentation_mode=presentation_mode,
        required_visible_text=list(required_visible_text or []),
    )


def test_base_asset_prompt_compiles_explicit_mature_layout_profiles() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    character.brief = CoreAssetBrief(
        identity_anchor="18 岁男高中生，黑色碎发，白色运动校服",
        visual_attributes=["五官清秀", "身形修长"],
        reference_strategy=AssetReferenceStrategy.CHARACTER_STANDARD_VIEWS,
        layout_profile=AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
    )

    character_prompt = build_base_asset_prompt(document, character)

    assert "严格 16:9 横图" in character_prompt
    assert "左侧占整幅约 50%" in character_prompt
    assert "全身正面、全身侧面、全身背面" in character_prompt
    assert "#7c7c7c" in character_prompt
    assert "不要文字、视图名称" in character_prompt
    assert "DRAMA_CHARACTER_DUAL_ZONE_16_9" in character_prompt

    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    location.brief = CoreAssetBrief(
        identity_anchor="高中食堂",
        visual_attributes=["打饭窗口、用餐区和通道关系稳定"],
        reference_strategy=AssetReferenceStrategy.SCENE_COVERAGE,
        layout_profile=AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
    )

    location_prompt = build_base_asset_prompt(document, location)

    assert "2×2 网格" in location_prompt
    assert "正向、反向、左向、右向" in location_prompt
    assert "只展示空场环境" in location_prompt
    assert "SCENE_FOUR_DIRECTION_2X2" in location_prompt


def test_prop_reference_prompt_excludes_full_people_and_story_context(tmp_path) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    prop.brief = _brief(
        "磨损红壳录音笔",
        presentation_mode=AssetPresentationMode.PHYSICAL_MASTER,
    )
    prompt = build_base_asset_prompt(document, prop)

    assert "不要出现完整人物" in prompt
    assert "不要出现完整人物、交接动作、房间、柜台、商品背景或剧情场景" in prompt
    assert "只在该物体本来具有时展示" in prompt
    assert "不得为了丰富画面凭空增加部件" in prompt
    assert "不生成乱码或可读正文" in prompt
    assert "视觉色彩与材质" not in prompt
    assert "当前制片技能：AI 短剧（本地格式适配）。" in prompt
    assert "资产审核的通用职责：" in prompt
    assert "类型创作原则：" not in prompt

    image_path = tmp_path / "prop.png"
    Image.new("RGB", (32, 56), (150, 20, 20)).save(image_path)
    version = prop.versions[-1].model_copy(
        deep=True,
        update={
            "media_type": AssetMediaType.IMAGE,
            "local_path": str(image_path),
            "approval_status": ApprovalStatus.DRAFT,
        },
    )
    client = CapturingLanguageClient()

    review = inspect_asset_image(
        document=document,
        asset=prop,
        version=version,
        client=client,
    )

    assert review.recommended is True
    assert "不要追加剧情角色身份" in client.instructions
    assert "交接顺序、左右手或场景连续性要求" in client.instructions
    assert "审核镜头关键帧时分两步" in client.instructions
    assert "不要猜测其正确" in client.instructions
    assert "画面美观不能抵消" in client.instructions
    assert "若 ShotPlan 显式把演员、手、道具或状态" in client.instructions
    assert "其缺席就是符合要求的证据" in client.instructions
    assert "当前制片技能：AI 短剧（本地格式适配）。" in client.instructions
    assert "资产审核的通用职责：" in client.instructions
    assert "类型创作原则：" not in client.instructions


def test_base_asset_prompt_uses_general_profile_without_ai_short_drama_policy() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    document.project.production_profile = ProductionProfile.GENERAL_VIDEO
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)

    prompt = build_base_asset_prompt(document, character)

    assert "按项目声明的视觉媒介制片参考资产" in prompt
    assert "当前制片技能：通用视频。" in prompt
    assert "资产审核的通用职责：" in prompt
    assert "中性参考视图与安静画面同样有效" in prompt
    assert "AI 原生短剧" not in prompt


def test_base_asset_prompt_obeys_project_style_without_injecting_realism() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    bible.style.medium = "纸雕与水墨混合视觉"
    bible.style.cinematography = "平面构图随叙事关系变化"
    bible.style.color = "项目自定义色彩"
    bible.style.texture = "纸张纤维与墨色层次"
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)

    prompt = build_base_asset_prompt(document, character)

    assert "视觉媒介=纸雕与水墨混合视觉" in prompt
    assert "纸张纤维与墨色层次" in prompt
    assert "真实皮肤" not in prompt
    assert "真人全身身份母版" not in prompt
    assert "不要擅自追加当前 Style 未声明的视觉媒介" in prompt


def test_location_prompt_allows_only_structured_required_visible_text() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    bible.locations[0] = bible.locations[0].model_copy(
        update={
            "layout": (
                "玻璃门在画面右侧，墙上安装标牌“紧急出口，禁止堆物”，"
                "收银台在左后方"
            )
        }
    )
    location.brief = _brief(
        "右侧玻璃门与左后方收银台",
        required_visible_text=["紧急出口，禁止堆物"],
    )

    prompt = build_stock_grounded_asset_prompt(document, location)

    assert "只允许结构化资产简报明确列出的场内实物文字" in prompt
    assert "“紧急出口，禁止堆物”" in prompt
    assert "这些文字必须准确、可读" in prompt
    assert "删除原图中的无关文字" in prompt
    assert "并按结构化资产简报准确重建必需的场内实物文字" in prompt


def test_quoted_descriptor_text_does_not_become_a_visible_text_policy() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    location.versions[-1].descriptor = '墙上写着“紧急出口”的便利店'

    prompt = build_base_asset_prompt(document, location)

    assert "只允许结构化资产简报明确列出的场内实物文字" not in prompt
    assert "不要任何文字、标签、水印、品牌名或界面元素" in prompt


def test_location_master_uses_first_appearance_without_future_state_leakage(
    tmp_path: Path,
) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    profile = next(item for item in bible.locations if item.name in location.name)
    profile.lighting = "开场正常白光；结尾暴雨、浓烟和红色警报灯侵入"
    location.brief = _brief("右侧玻璃门、左后方收银台、正常白光")
    first_shot = min(document.shots, key=lambda item: item.sequence)
    first_shot.visual = "雨夜便利店开场晴朗，入口完好，室内保持正常白光。"
    first_shot.continuity.prop_state_in = "门锁完整，固定设备正常运行。"
    document.reference_plan[0].requirements.append(
        ReferenceRequirement(
            id="location-first-appearance",
            subject_asset_id=location.id,
            acceptable_roles=[ReferenceRole.LOCATION_MASTER],
            purpose="显式声明该镜头使用场景母版",
        )
    )

    prompt = build_base_asset_prompt(document, location)

    assert "基础母版时点" in prompt
    assert "开场晴朗，入口完好" in prompt
    assert "门锁完整，固定设备正常运行" in prompt
    assert "结尾暴雨、浓烟和红色警报灯侵入" not in prompt
    assert "该时点只来自显式的资产引用" in prompt
    assert "后续状态由派生资产或镜头关键帧表达" in prompt
    assert "全片风格可能包含后段状态" in prompt
    assert "只允许结构化资产简报明确列出的场内实物文字" not in prompt

    image_path = tmp_path / "location.png"
    Image.new("RGB", (32, 56), (120, 120, 120)).save(image_path)
    candidate = location.versions[-1].model_copy(
        deep=True,
        update={
            "media_type": AssetMediaType.IMAGE,
            "local_path": str(image_path),
            "approval_status": ApprovalStatus.DRAFT,
            "descriptor": prompt,
        },
    )
    client = CapturingLanguageClient()

    inspect_asset_image(
        document=document,
        asset=location,
        version=candidate,
        client=client,
    )

    assert "未来状态泄漏" in client.instructions
    assert (
        "稳定空间拓扑与时间状态变体必须分开"
        in client.instructions
    )


def test_location_name_in_shot_text_does_not_imply_first_appearance() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    first_shot = min(document.shots, key=lambda item: item.sequence)
    assert location.name in first_shot.visual

    prompt = build_base_asset_prompt(document, location)

    assert "首次显式引用镜头" not in prompt
    assert first_shot.continuity.prop_state_in not in prompt


def test_location_version_binding_explicitly_selects_first_appearance() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    first_shot = min(document.shots, key=lambda item: item.sequence)
    first_shot.visual = "显式版本绑定的场景入点"
    document.reference_plan[0].bindings.append(
        ReferenceBinding(
            kind=AssetKind.LOCATION.value,
            target_id=location.current_version_id,
            purpose="当前镜头的场景母版",
            required=True,
        )
    )

    prompt = build_base_asset_prompt(document, location)

    assert "显式版本绑定的场景入点" in prompt


def test_location_timeline_cue_explicitly_selects_first_appearance() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    location = next(item for item in document.assets if item.kind == AssetKind.LOCATION)
    first_shot = min(document.shots, key=lambda item: item.sequence)
    first_shot.visual = "显式时间线引用的场景入点"
    first_shot.timeline_beats = [
        ShotTimelineBeat(
            start_seconds=0.0,
            end_seconds=first_shot.duration_seconds,
            visual=first_shot.visual,
            framing=first_shot.framing,
            camera=first_shot.camera,
            action="建立空间",
            dialogue_line_ids=list(first_shot.dialogue_line_ids),
            references=[
                ShotReferenceCue(
                    subject_asset_id=location.id,
                    alias="location-master",
                    purpose="该时间段显式使用场景母版",
                )
            ],
        )
    ]

    prompt = build_base_asset_prompt(document, location)

    assert "显式时间线引用的场景入点" in prompt


def test_screen_content_prop_uses_compositing_plate_prompt_and_semantic_review(
    tmp_path: Path,
) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    screen = prop.model_copy(deep=True, update={"name": "买家付款页面"})
    screen.versions[-1].descriptor = "普通付款记录，收款人清楚显示为周蔓。"
    screen.brief = _brief(
        "虚构买家付款信息板",
        presentation_mode=AssetPresentationMode.SCREEN_CONTENT_PLATE,
        required_visible_text=["收款人：周蔓"],
    )

    prompt = build_base_asset_prompt(document, screen)

    assert "可供后期贴入屏幕" in prompt
    assert "收款人：周蔓" in prompt
    assert "真实平台品牌" in prompt
    assert "不要手、手机外壳" in prompt
    assert "纯产品母版" not in prompt

    image_path = tmp_path / "screen.png"
    Image.new("RGB", (32, 56), (245, 245, 245)).save(image_path)
    candidate = screen.versions[-1].model_copy(
        deep=True,
        update={
            "local_path": str(image_path),
            "approval_status": ApprovalStatus.DRAFT,
        },
    )
    client = CapturingLanguageClient()

    inspect_asset_image(
        document=document,
        asset=screen,
        version=candidate,
        client=client,
    )

    assert "已显式声明为屏幕内容母版" in client.instructions
    assert "required_visible_text 中每一项" in client.instructions
    assert "乱码、语义替换" in client.instructions
    assert "这是干净的屏幕内容板" in client.instructions


def test_screen_like_asset_name_does_not_select_screen_content_mode(tmp_path: Path) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    prop.name = "买家付款页面"
    prop.versions[-1].descriptor = "收款人周蔓"

    prompt = build_base_asset_prompt(document, prop)

    assert "结构化呈现类型：未声明" in prompt
    assert "由 Asset Agent 先补充 presentation_mode" in prompt
    assert "可供后期贴入屏幕" not in prompt

    image_path = tmp_path / "unknown-prop.png"
    Image.new("RGB", (32, 56), (245, 245, 245)).save(image_path)
    candidate = prop.versions[-1].model_copy(
        deep=True,
        update={
            "local_path": str(image_path),
            "approval_status": ApprovalStatus.DRAFT,
        },
    )
    client = CapturingLanguageClient()

    inspect_asset_image(
        document=document,
        asset=prop,
        version=candidate,
        client=client,
    )

    assert "已显式声明为屏幕内容母版" not in client.instructions
    assert "只审核道具的形状" in client.instructions


def test_character_name_containment_does_not_rebind_bible_profile() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    character.name = "林夏定妆-候选"
    character.versions[-1].descriptor = "上游显式资产描述"
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    bible.characters[0].appearance = "不应通过名称包含关系注入"

    prompt = build_base_asset_prompt(document, character)

    assert "上游显式资产描述" in prompt
    assert "不应通过名称包含关系注入" not in prompt


def test_asset_review_compares_candidate_with_approved_canonical_images(tmp_path) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)
    candidate_path = tmp_path / "candidate.png"
    canonical_path = tmp_path / "canonical.png"
    Image.new("RGB", (32, 56), (100, 80, 70)).save(candidate_path)
    Image.new("RGB", (32, 56), (80, 70, 60)).save(canonical_path)
    candidate = character.versions[-1].model_copy(
        deep=True,
        update={
            "id": "candidate-version",
            "media_type": AssetMediaType.IMAGE,
            "local_path": str(candidate_path),
            "approval_status": ApprovalStatus.DRAFT,
        },
    )
    canonical = character.versions[-1].model_copy(
        deep=True,
        update={
            "id": "canonical-version",
            "media_type": AssetMediaType.IMAGE,
            "local_path": str(canonical_path),
            "approval_status": ApprovalStatus.APPROVED,
        },
    )
    client = CapturingLanguageClient()

    inspect_asset_image(
        document=document,
        asset=character,
        version=candidate,
        client=client,
        canonical_references=[(character, canonical)],
    )

    payload = json.loads(client.input_text)
    assert len(client.image_urls) == 2
    assert payload["image_map"] == [
        {
            "image": 1,
            "role": "candidate_under_review",
            "asset_id": character.id,
            "asset_name": character.name,
            "version_id": candidate.id,
        },
        {
            "image": 2,
            "role": "approved_canonical_reference",
            "asset_id": character.id,
            "asset_kind": "CHARACTER",
            "asset_name": character.name,
            "version_id": canonical.id,
        },
    ]
    assert "Image 1 始终是待审核候选" in client.instructions


def test_asset_version_semantic_inspection_is_agent_callable_before_review(
    tmp_path: Path,
) -> None:
    language = CapturingLanguageClient()
    service = ShortDramaService(
        database_path=tmp_path / "app-data" / "short-drama.sqlite3",
        workspace_root=tmp_path / "workspace",
        language_provider_factory=lambda _api_key, _base_url: language,
    )
    service.update_language_provider_config(
        LanguageProviderConfigUpdate(base_url="http://127.0.0.1:4141/v1")
    )
    project = service.initialize_fixture()
    asset = next(
        item for item in project.document.assets if item.kind == AssetKind.CHARACTER
    )
    candidate_path = tmp_path / "agent-inspection-candidate.png"
    Image.new("RGB", (64, 96), (112, 92, 80)).save(candidate_path)
    imported = service.import_local_asset_version(
        project.document.project.id,
        asset.id,
        AssetImportLocalRequest(
            local_path=str(candidate_path),
            descriptor="待 Agent 观察的角色正面候选",
        ),
    )

    result = service.tool_registry.invoke(
        "qa.inspect_asset_version",
        {
            "project_id": project.document.project.id,
            "asset_id": asset.id,
            "version_id": imported.version.id,
            "request": {
                "canonical_reference_version_ids": [],
            },
        },
    )

    assert result.status == ToolCallStatus.SUCCEEDED
    assert result.output == {
        "recommended": True,
        "score": 4.5,
        "issues": [],
        "summary": "The prop is coherent across the reference views.",
    }
    assert {item.artifact_type for item in result.artifact_refs} == {
        "asset",
        "asset_version",
    }
    assert len(language.image_urls) == 1
    current = service.get_project(project.document.project.id)
    current_asset = next(item for item in current.document.assets if item.id == asset.id)
    current_version = next(
        item for item in current_asset.versions if item.id == imported.version.id
    )
    assert current_version.approval_status == ApprovalStatus.DRAFT
