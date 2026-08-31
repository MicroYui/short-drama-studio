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
from short_drama.models import (
    ApprovalStatus,
    AssetKind,
    AssetMediaType,
    ProductionProfile,
)


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


def test_prop_reference_prompt_excludes_full_people_and_story_context(tmp_path) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    prompt = build_base_asset_prompt(document, prop)

    assert "不要出现完整人物" in prompt
    assert "不要出现完整人物、交接动作、房间、柜台、商品背景或剧情场景" in prompt
    assert "只在该物体本来具有时展示" in prompt
    assert "不得为了丰富画面凭空增加部件" in prompt
    assert "不生成乱码或可读正文" in prompt
    assert "视觉色彩与材质" not in prompt
    assert "Active Production Skill: live-action Douyin vertical short drama." in prompt
    assert "Asset Reviewer rubric:" in prompt
    assert "Production constitution:" not in prompt

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
    assert "Do not require story character identity" in client.instructions
    assert "handoff order, laterality, or scene continuity" in client.instructions
    assert "inspect in two passes" in client.instructions
    assert "do not guess that it is correct" in client.instructions
    assert "otherwise attractive image does not cancel" in client.instructions
    assert "explicitly places an actor, hand, prop, or state offscreen" in client.instructions
    assert "its absence is compliant evidence" in client.instructions
    assert "Active Production Skill: live-action Douyin vertical short drama." in client.instructions
    assert "Asset Reviewer rubric:" in client.instructions
    assert "Production constitution:" not in client.instructions


def test_base_asset_prompt_uses_general_profile_without_douyin_policy() -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    document.project.production_profile = ProductionProfile.GENERAL_VIDEO
    character = next(item for item in document.assets if item.kind == AssetKind.CHARACTER)

    prompt = build_base_asset_prompt(document, character)

    assert "真人竖屏视频制片参考资产" in prompt
    assert "Active Production Skill: general live-action vertical video." in prompt
    assert "Asset Reviewer rubric:" in prompt
    assert "Neutral reference views and quiet imagery are valid" in prompt
    assert "live-action Douyin vertical short drama" not in prompt


def test_location_prompt_allows_only_explicitly_required_diegetic_label() -> None:
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

    prompt = build_stock_grounded_asset_prompt(document, location)

    assert "只允许资产描述明确要求的场内实物文字" in prompt
    assert "“紧急出口，禁止堆物”" in prompt
    assert "这些文字必须准确、可读" in prompt
    assert "删除原图中的无关文字" in prompt
    assert "并按资产描述准确重建必需的场内实物文字" in prompt


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
    first_shot = min(document.shots, key=lambda item: item.sequence)
    first_shot.visual = "雨夜便利店开场晴朗，入口完好，室内保持正常白光。"
    first_shot.continuity.prop_state_in = "门锁完整，固定设备正常运行。"

    prompt = build_base_asset_prompt(document, location)

    assert "基础母版时点" in prompt
    assert "开场晴朗，入口完好" in prompt
    assert "门锁完整，固定设备正常运行" in prompt
    assert "结尾暴雨、浓烟和红色警报灯侵入" not in prompt
    assert "不得提前出现后续镜头才发生" in prompt
    assert "后续状态应由派生资产或镜头关键帧表达" in prompt
    assert "全片风格可能包含后段状态" in prompt
    assert "只允许资产描述明确要求的场内实物文字" not in prompt

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

    assert "future-state leakage" in client.instructions
    assert (
        "Stable spatial topology and temporal state variants must remain separate"
        in client.instructions
    )


def test_screen_content_prop_uses_compositing_plate_prompt_and_semantic_review(
    tmp_path: Path,
) -> None:
    document = build_dont_trust_me_fixture("2026-08-17T00:00:00Z")
    prop = next(item for item in document.assets if item.kind == AssetKind.PROP)
    screen = prop.model_copy(deep=True, update={"name": "买家付款页面"})
    screen.versions[-1].descriptor = "普通付款记录，收款人清楚显示为周蔓。"

    prompt = build_base_asset_prompt(document, screen)

    assert "可供后期贴入手机屏幕" in prompt
    assert "关键人名" in prompt
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

    assert "screen-content asset" in client.instructions
    assert "exact names, relationships, statuses" in client.instructions
    assert "Reject gibberish" in client.instructions
    assert "clean screen plate" in client.instructions


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
    assert "Image 1 is always the candidate" in client.instructions
