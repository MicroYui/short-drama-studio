"""Agent-callable visual asset planning and semantic inspection."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from pydantic import Field

from short_drama.h3_models import H3RouteAssetPreparation
from short_drama.models import (
    Asset,
    AssetKind,
    AssetVersion,
    DomainModel,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    ShotPlan,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient


class SemanticAssetReview(DomainModel):
    recommended: bool
    score: float = Field(ge=0.0, le=5.0)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue], max_length=16)
    summary: str = Field(min_length=1, max_length=3000)


def build_base_asset_prompt(document: ProjectDocument, asset: Asset) -> str:
    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    descriptor = asset.versions[-1].descriptor
    shared = (
        "真人都市短剧制片参考资产，写实电影质感，真实皮肤和材质，9:16 竖图，"
        "纯视觉内容，不要任何文字、标签、水印、品牌名或界面元素。"
    )
    if asset.kind == AssetKind.CHARACTER:
        layout = (
            "生成同一位真人、同一发型、同一妆容和同一套完整服装的制片参考板。"
            "画面同时清楚展示正面全身、四分之三角度、侧面轮廓和脸部近景；"
            "各视图必须是同一个人，比例自然，背景简洁，不做漫画设定图。"
        )
    elif asset.kind == AssetKind.LOCATION:
        layout = (
            "生成同一个真实空间的制片参考板，以空场建立视角、反打视角和互动方向为主。"
            "如需展示尺度，只允许无身份特征的远景站位轮廓，不要生成完整角色定妆。"
            "门窗、柜台、桌椅、通道和主要光源在各视图中的拓扑必须一致，空间可拍摄。"
        )
    elif asset.kind == AssetKind.PROP:
        layout = (
            "生成同一个真实道具的纯产品制片参考板，包含正反面、两侧、顶部、底部和关键细节。"
            "所有视图中的形状、颜色、按钮、接口、指示灯位置和材质必须完全一致。"
            "最多增加一只匿名、裁切到手腕的中性手掌表示尺度；不要出现完整人物、交接动作、"
            "房间、柜台、商品背景或剧情场景，不要自动添加文字、商标或额外部件。"
        )
    else:
        layout = "生成一张可供后续镜头构图使用的清晰写实视觉参考图。"
    return (
        f"{shared}\n{layout}\n资产名称：{asset.name}\n资产描述：{descriptor}\n"
        f"全片风格：{bible.style.cinematography}；{bible.style.color}；{bible.style.texture}。\n"
        f"禁止：{'；'.join(bible.style.forbidden)}。"
    )


def build_keyframe_correction_prompt(
    preparation: H3RouteAssetPreparation,
    prior_summary: str | None,
) -> str:
    if not prior_summary:
        return preparation.prompt
    return (
        f"{preparation.prompt}\n\n上一候选未通过制片检查。仅修复以下通用问题，"
        f"不要改变已声明的镜头意图或新增动作：{prior_summary}"
    )


def inspect_asset_image(
    *,
    document: ProjectDocument,
    asset: Asset,
    version: AssetVersion,
    client: LanguageProviderClient,
    shot: ShotPlan | None = None,
    preparation: H3RouteAssetPreparation | None = None,
    canonical_references: list[tuple[Asset, AssetVersion]] | None = None,
) -> SemanticAssetReview:
    review_scope = {
        AssetKind.CHARACTER: (
            "Judge the named character's identity, anatomy, wardrobe, and coherence across views."
        ),
        AssetKind.LOCATION: (
            "Judge spatial topology, lighting, materials, and production usefulness. Incidental "
            "anonymous scale figures are not story-character references and must not be rejected "
            "for identity or wardrobe unless the request explicitly names a character."
        ),
        AssetKind.PROP: (
            "Judge only the prop's shape, scale, materials, component placement, and consistency "
            "across views. An anonymous cropped hand is only a scale cue. Do not require story "
            "character identity, wardrobe, handoff order, laterality, or scene continuity unless "
            "the supplied keyframe preparation explicitly requests them."
        ),
        AssetKind.REFERENCE: (
            "Judge the supplied shot or reference purpose and every visible subject it explicitly requests."
        ),
    }.get(asset.kind, "Judge visible production usefulness against the supplied descriptor.")
    instructions = (
        "You are a visual asset inspection tool for a live-action vertical short-drama pipeline. "
        "Return JSON only and match the supplied schema. Compare only visible image evidence with "
        "the supplied asset description, project Bible, and optional ShotPlan/keyframe brief. "
        "For a shot keyframe, inspect in two passes: first identify the visible actors, limbs, "
        "contacts, prop state, gaze, and action phase without assuming the requested answer; then "
        "compare those observations with the declared start/end state. If an explicitly declared "
        "left/right hand, contact side, or action phase is visibly contradicted, report a generic "
        "continuity issue. If laterality or phase is visually ambiguous, do not guess that it is "
        "correct: lower the score and recommend a clearer keyframe only when that element is "
        "required to be visible in the frame. If the ShotPlan explicitly places an actor, hand, "
        "prop, or state offscreen or out of frame, its absence is compliant evidence: do not "
        "require its contact, laterality, light state, or action phase to be visually readable, "
        "and do not reject the image merely because an offscreen state cannot be verified. You "
        "may mention that limitation as a non-blocking observation. For a keyframe, set "
        "recommended=false when a visible explicit start/end state is contradicted, or when a "
        "contact or laterality that the frame is required to show remains ambiguous; an otherwise "
        "attractive image does not cancel those production-control failures. "
        "Check realism, severe anatomy or object corruption, identity coherence within a reference "
        "sheet, location geometry, prop shape, framing usefulness, and unwanted text or watermarks. "
        "Do not invent requirements. A recommendation is an overridable production suggestion, "
        "not a user-facing approval gate. Use generic QA categories and stable issue codes. "
        "When canonical reference images are supplied, Image 1 is always the candidate under "
        "review and later images are approved references. Compare visible identity, hairstyle, "
        "wardrobe, location topology, and prop construction against the matching approved "
        "reference instead of judging the candidate in isolation. Do not average or merge "
        "different character identities. "
        + review_scope
    )
    reference_images = _canonical_reference_images(canonical_references or [])
    payload = {
        "output_schema": SemanticAssetReview.model_json_schema(),
        "asset": {
            "id": asset.id,
            "kind": asset.kind.value,
            "name": asset.name,
            "descriptor": version.descriptor,
        },
        "bible": next(
            item.model_dump(mode="json")
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        "shot": shot.model_dump(mode="json") if shot is not None else None,
        "keyframe_preparation": (
            preparation.model_dump(mode="json") if preparation is not None else None
        ),
        "allowed_issue_categories": [item.value for item in QaFailureCategory],
        "decision_guidance": {
            "recommended": (
                "true only when the image is usable as a production reference without a severe "
                "visible contradiction or corruption"
            ),
            "score": "0-5 production usefulness, not aesthetic novelty",
            "asset_kind_scope": review_scope,
        },
        "image_map": [
            {
                "image": 1,
                "role": "candidate_under_review",
                "asset_id": asset.id,
                "asset_name": asset.name,
                "version_id": version.id,
            },
            *[
                {
                    "image": index,
                    "role": "approved_canonical_reference",
                    "asset_id": reference_asset.id,
                    "asset_kind": reference_asset.kind.value,
                    "asset_name": reference_asset.name,
                    "version_id": reference_version.id,
                }
                for index, (reference_asset, reference_version, _url) in enumerate(
                    reference_images,
                    start=2,
                )
            ],
        ],
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=[
            _asset_image_url(version),
            *(url for _asset, _version, url in reference_images),
        ],
        max_output_tokens=3000,
    )
    return SemanticAssetReview.model_validate_json(_strip_json_fence(output))


def _canonical_reference_images(
    references: list[tuple[Asset, AssetVersion]],
) -> list[tuple[Asset, AssetVersion, str]]:
    result: list[tuple[Asset, AssetVersion, str]] = []
    seen: set[str] = set()
    for asset, version in references:
        if version.id in seen or version.approval_status.value != "APPROVED":
            continue
        try:
            url = _asset_image_url(version)
        except ValueError:
            continue
        result.append((asset, version, url))
        seen.add(version.id)
        if len(result) >= 8:
            break
    return result


def _asset_image_url(version: AssetVersion) -> str:
    if version.external_url is not None:
        return version.external_url
    if version.local_path is None:
        raise ValueError(f"AssetVersion {version.id} has no usable image")
    path = Path(version.local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"AssetVersion image is missing: {path}")
    mime_type = version.mime_type or mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


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
