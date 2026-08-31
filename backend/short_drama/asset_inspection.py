"""Agent-callable visual asset planning and semantic inspection."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
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
from short_drama.production_profiles import (
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
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
    descriptor = canonical_base_asset_descriptor(document, asset)
    skill = production_skill(document.project.production_profile)
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
        context=descriptor,
        include_constitution=False,
    )
    label_source = descriptor
    if asset.kind == AssetKind.LOCATION:
        profile = next(
            (
                item
                for item in bible.locations
                if item.name == asset.name or item.name in asset.name
            ),
            None,
        )
        if profile is not None:
            label_source = profile.layout
    required_labels = _required_diegetic_labels(label_source)
    is_screen_content = (
        asset.kind == AssetKind.PROP and _is_screen_content_asset(asset.name)
    )
    if is_screen_content:
        text_policy = (
            "允许且只允许资产描述所需的虚构界面文字；关键人名、"
            "金额关系、账号状态、标题和引号内文案必须准确可读。"
            "不要乱码、无关文字、真实平台品牌、水印或广告。"
        )
    elif required_labels:
        label_list = "、".join(f"“{item}”" for item in required_labels)
        text_policy = (
            f"只允许资产描述明确要求的场内实物文字：{label_list}；"
            "这些文字必须准确、可读、安装在对应实物上。"
            "除此之外不要任何文字、标签、水印、品牌名或界面元素。"
        )
    else:
        text_policy = "不要任何文字、标签、水印、品牌名或界面元素。"
    shared = (
        f"{skill.medium}制片参考资产，写实电影质感，真实皮肤和材质，9:16 竖图，"
        f"纯视觉内容，{text_policy}"
    )
    if asset.kind == AssetKind.CHARACTER:
        layout = (
            "生成一张单幅、正面略带四分之三角度的真人全身身份母版。"
            "完整展示同一人的脸、发型、妆容、体型、鞋子和整套服装；比例自然，"
            "均匀柔光，背景干净中性。不要宫格、拼贴、多人物、转面图或漫画设定图。"
        )
    elif asset.kind == AssetKind.LOCATION:
        layout = (
            "生成一张单幅空场 Master Wide，完整、清楚地交代同一个真实空间的总布局、"
            "摄影轴线、入口、出口、通道、门窗、桌椅和主要光源。空间必须可拍摄且几何"
            "关系无歧义。不要人物、分镜宫格、拼贴、多个互相冲突的机位或平面图标签。"
        )
    elif is_screen_content:
        layout = (
            "生成一张单幅、正视、可供后期贴入手机屏幕的虚构移动界面母版。"
            "信息层级、列表、时间、金额或账号区域必须像真实产品，但不复制任何"
            "真实平台的 logo 或完整视觉识别。不要手、手机外壳、透视变形、反光、"
            "多个页面、宫格或拼贴。"
        )
    elif asset.kind == AssetKind.PROP:
        layout = (
            "生成一张单幅、三分之四角度的纯产品母版，清楚展示真实尺度、形状、颜色、"
            "材质和资产描述中确实存在的标志性结构。按钮、接口、指示灯、把手、附件等"
            "只在该物体本来具有时展示，不得为了丰富画面凭空增加部件。背景干净中性；"
            "不要出现完整人物、交接动作、房间、柜台、商品背景或剧情场景。纸张、合同和"
            "文件只保留不可辨认的中性版式块，不生成乱码或可读正文。不要宫格、拼贴、"
            "商标或额外部件；若上方文字策略列出必需场内文字，仅例外保留该文字。"
        )
    else:
        layout = "生成一张可供后续镜头构图使用的清晰写实视觉参考图。"
    style_context = ""
    if asset.kind == AssetKind.LOCATION:
        style_context = (
            "\n只使用上述基础母版时点已经可见的色彩、天气与材质。"
            "全片风格可能包含后段状态，不得把后续烟雾、火灾、灰烬、"
            "损坏或灯光变化提前画进本图。"
        )
    asset_identity = (
        f"资产名称：{asset.name}"
        if asset.kind != AssetKind.PROP
        else f"资产类型：{'手机屏幕内容母版' if is_screen_content else '实体道具母版'}"
    )
    return (
        f"{shared}\n{layout}\n{asset_identity}\n资产描述：{descriptor}。"
        f"{style_context}\n制作类型策略：{skill_prompt.text}"
    )


def build_stock_grounded_character_prompt(
    document: ProjectDocument,
    asset: Asset,
) -> str:
    """Turn a stock portrait into a new fictional character master.

    Stock people are useful photographic and styling references, but are not
    silently promoted to story identities. User uploads and approved catalog
    characters remain separate paths and can preserve identity when requested.
    """

    if asset.kind != AssetKind.CHARACTER:
        raise ValueError("Stock identity grounding only applies to character assets")
    return (
        build_base_asset_prompt(document, asset)
        + "\n\n输入图像是素材库真人照片，只作为真实摄影质感、自然皮肤与人体比例、"
        "大致年龄感和造型方向的软参考。创建一个全新的虚构人物身份；不得复制、"
        "保留或近似复刻输入人物可识别的面孔与身份。必须优先服从本项目资产描述，"
        "输出仍是一张可供后续多角度派生和镜头关键帧使用的单人全身身份母版。"
    )


def build_stock_grounded_asset_prompt(
    document: ProjectDocument,
    asset: Asset,
) -> str:
    if asset.kind == AssetKind.CHARACTER:
        return build_stock_grounded_character_prompt(document, asset)
    descriptor = canonical_base_asset_descriptor(document, asset)
    required_labels = _required_diegetic_labels(descriptor)
    required_label_scope = (
        "并按资产描述准确重建必需的场内实物文字"
        if required_labels
        else ""
    )
    grounding_scope = {
        AssetKind.LOCATION: (
            "输入图像是现实空间的布局、建筑尺度和材质参考。创建本项目中的虚构地点，"
            "保留有用的空间拓扑和摄影真实感，但必须服从资产描述重构入口、通道、"
            f"设备区与光线；删除原图中的无关文字、品牌和不属于剧情的物体{required_label_scope}。"
        ),
        AssetKind.PROP: (
            "输入图像是现实物体的尺度、结构和材质参考。创建本项目中的虚构道具，"
            f"保留合理工业构造，但必须服从资产描述；删除品牌、无关可读文字和无关附件{required_label_scope}。"
        ),
    }.get(
        asset.kind,
        "输入图像仅作摄影真实感与构图参考，输出必须服从本项目资产描述。",
    )
    return f"{build_base_asset_prompt(document, asset)}\n\n{grounding_scope}"


def _required_diegetic_labels(descriptor: str) -> list[str]:
    """Return only text that the Bible explicitly quotes as a visible set element."""

    labels: list[str] = []
    for match in re.finditer(r"[“\"]([^”\"\n]{1,80})[”\"]", descriptor):
        label = match.group(1).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _is_screen_content_asset(name: str) -> bool:
    return any(
        token in name
        for token in (
            "页面",
            "界面",
            "截图",
            "付款记录",
            "转账记录",
            "聊天记录",
            "短信记录",
        )
    )


def canonical_base_asset_descriptor(
    document: ProjectDocument,
    asset: Asset,
) -> str:
    """Remove shot-state prose from the canonical image-master request."""

    bible = next(
        item
        for item in document.bible_versions
        if item.id == document.project.current_bible_version_id
    )
    if asset.kind == AssetKind.CHARACTER:
        profile = next(
            (
                item
                for item in bible.characters
                if item.name == asset.name or item.name in asset.name
            ),
            None,
        )
        if profile is not None:
            return f"{profile.age}岁；{profile.appearance}；{profile.wardrobe}"
    if asset.kind == AssetKind.LOCATION:
        profile = next(
            (
                item
                for item in bible.locations
                if item.name == asset.name or item.name in asset.name
            ),
            None,
        )
        if profile is not None:
            first_shot = _first_location_appearance_shot(document, asset)
            if first_shot is not None:
                return (
                    f"稳定空间拓扑：{profile.layout}；"
                    "基础母版时点：只呈现该地点首次出场时的环境和静态状态；"
                    f"首次出场镜头：{first_shot.visual}；"
                    f"首次出场物件状态：{first_shot.continuity.prop_state_in}；"
                    "只从镜头描述中提取场景、天气、光线、门窗、固定设备和静态道具，"
                    "不要画人物或正在发生的动作。不得提前出现后续镜头才发生的烟雾、"
                    "火灾、损坏、开关变化、门锁变化、物件位移或其他剧情状态；"
                    "后续状态应由派生资产或镜头关键帧表达"
                )
            return f"{profile.layout}；{profile.lighting}；{profile.palette}"
    if asset.kind == AssetKind.PROP:
        profile = next(
            (
                item
                for item in bible.props
                if item.name == asset.name or item.name in asset.name
            ),
            None,
        )
        if profile is not None:
            return profile.appearance
    return asset.versions[-1].descriptor


def _first_location_appearance_shot(
    document: ProjectDocument,
    asset: Asset,
) -> ShotPlan | None:
    """Find the first shot that actually uses a location as its current setting."""

    planned_shot_ids = {
        plan.shot_id
        for plan in document.reference_plan
        if any(
            requirement.subject_asset_id == asset.id
            for requirement in plan.requirements
        )
    }
    ordered = sorted(document.shots, key=lambda item: item.sequence)
    planned = next(
        (shot for shot in ordered if shot.id in planned_shot_ids),
        None,
    )
    if planned is not None:
        return planned

    normalized_name = "".join(
        character.lower() for character in asset.name if character.isalnum()
    )
    if not normalized_name:
        return None
    for shot in ordered:
        context = "\n".join(
            (
                shot.visual,
                shot.camera,
                shot.continuity.character_positions,
                shot.continuity.prop_state_in,
            )
        )
        normalized_context = "".join(
            character.lower() for character in context if character.isalnum()
        )
        if normalized_name in normalized_context:
            return shot
    return None


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
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
        context="\n".join(
            (
                asset.name,
                version.descriptor,
                shot.model_dump_json() if shot is not None else "",
                preparation.model_dump_json() if preparation is not None else "",
            )
        ),
        include_constitution=False,
    )
    if asset.kind == AssetKind.PROP and _is_screen_content_asset(asset.name):
        review_scope = (
            "Judge this fictional screen-content asset for readable hierarchy, compositing "
            "usefulness, and the exact names, relationships, statuses, amounts, or quoted copy "
            "explicitly required by the descriptor. Reject gibberish, semantic substitutions, "
            "unrelated UI text, and recognizable real-platform branding. Do not require a phone "
            "body or actor hand because this is a clean screen plate."
        )
    else:
        review_scope = {
            AssetKind.CHARACTER: (
                "Judge the named character's identity, anatomy, wardrobe, and coherence across views."
            ),
            AssetKind.LOCATION: (
                "Judge spatial topology, lighting, materials, and production usefulness. Incidental "
                "anonymous scale figures are not story-character references and must not be rejected "
                "for identity or wardrobe unless the request explicitly names a character. For a "
                "project-level location master, the descriptor's earliest-appearance state is "
                "authoritative: report future-state leakage when weather, smoke, fire, damage, "
                "opened locks, moved props, switched equipment, or other later story changes appear "
                "too early. Stable spatial topology and temporal state variants must remain separate; "
                "a clearly visible later-only state makes the candidate not recommended."
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
        "You are a visual asset inspection tool for a live-action vertical production pipeline. "
        f"{skill_prompt.text} "
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
