"""Asset prompt and semantic-review scope regressions."""

from __future__ import annotations

import json

from PIL import Image

from short_drama.asset_inspection import build_base_asset_prompt, inspect_asset_image
from short_drama.fixture import build_dont_trust_me_fixture
from short_drama.models import ApprovalStatus, AssetKind, AssetMediaType


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
    assert "指示灯位置" in prompt

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
