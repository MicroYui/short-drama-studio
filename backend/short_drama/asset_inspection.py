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
    AssetPresentationMode,
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
from short_drama.style_context import declared_style_value


class SemanticAssetReview(DomainModel):
    recommended: bool
    score: float = Field(ge=0.0, le=5.0)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue], max_length=16)
    summary: str = Field(min_length=1, max_length=3000)


class AssetVersionInspectionRequest(DomainModel):
    """Optional production context for inspecting one concrete image version."""

    shot_id: str | None = Field(
        default=None,
        description="Only set when this version is intended for one concrete shot.",
    )
    canonical_reference_version_ids: list[str] = Field(
        default_factory=list[str],
        max_length=8,
        description=(
            "Current approved image versions that define the identity, appearance, "
            "space, or prop structure the candidate should preserve."
        ),
    )


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
        include_constitution=False,
    )
    required_labels = (
        list(asset.brief.required_visible_text) if asset.brief is not None else []
    )
    presentation_mode = (
        asset.brief.presentation_mode if asset.brief is not None else None
    )
    is_screen_content = presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE
    if is_screen_content and required_labels:
        label_list = "、".join(f"“{item}”" for item in required_labels)
        text_policy = (
            f"只允许上游结构化简报明确列出的虚构界面文字：{label_list}；"
            "人名、金额、关系、状态和标题必须按该清单准确可读。"
            "不要乱码、无关文字、真实平台品牌、水印或广告。"
        )
    elif is_screen_content:
        text_policy = (
            "上游未提供结构化的必需可见文字清单；不得从资产名称、"
            "描述、引号或界面语义中自行猜测文案。只用不可读的中性信息块，"
            "待 Asset Agent 补充显式文字清单后再生成可读内容。"
        )
    elif required_labels:
        label_list = "、".join(f"“{item}”" for item in required_labels)
        text_policy = (
            f"只允许结构化资产简报明确列出的场内实物文字：{label_list}；"
            "这些文字必须准确、可读、安装在对应实物上。"
            "除此之外不要任何文字、标签、水印、品牌名或界面元素。"
        )
    else:
        text_policy = "不要任何文字、标签、水印、品牌名或界面元素。"
    style = bible.style
    style_fields = [
        f"{label}={value}"
        for label, raw_value in (
            ("视觉媒介", style.medium),
            ("镜头语言", style.cinematography),
            ("色彩", style.color),
            ("质感", style.texture),
        )
        if (value := declared_style_value(raw_value)) is not None
    ]
    style_instruction = (
        "严格服从当前项目 Style：" + "；".join(style_fields) + "。"
        if style_fields
        else "当前项目尚未声明具体视觉 Style。"
    )
    shared = (
        f"{skill.medium}制片参考资产。{style_instruction}"
        f"不要擅自追加当前 Style 未声明的视觉媒介、审美、材质或表现规则。输出适合竖屏制作的"
        f"单幅纯视觉内容，{text_policy}"
    )
    if is_screen_content:
        layout = (
            "生成一张单幅、正视、可供后期贴入屏幕的虚构界面内容母版。"
            "信息层级与区域只服从结构化资产简报，不复制任何真实平台的 logo "
            "或完整视觉识别。不要手、手机外壳、透视变形、反光、多个页面、"
            "宫格或拼贴。"
        )
    elif asset.kind == AssetKind.CHARACTER:
        layout = (
            "生成一张能够稳定表达该角色身份与造型的单幅角色母版。取景、角度、比例、"
            "细节和背景服从当前 Style 与资产描述，并为后续实际镜头提供清楚锚点。"
            "不要宫格、拼贴、无关人物、文字、logo 或水印。"
        )
    elif asset.kind == AssetKind.LOCATION:
        layout = (
            "生成一张能够稳定表达该场景规则与后续镜头所需关系的单幅场景母版。"
            "空间、层次、透视、构图或图形组织方式服从当前 Style 与资产描述。"
            "不要无关人物、分镜宫格、拼贴、互相冲突的视角或说明标签。"
        )
    elif (
        asset.kind == AssetKind.PROP
        and presentation_mode == AssetPresentationMode.PHYSICAL_MASTER
    ):
        layout = (
            "生成一张能够稳定表达该道具身份和剧情所需结构的单幅母版，清楚展示资产描述"
            "中确实存在的形状、颜色、材质和标志性特征。按钮、接口、指示灯、把手、附件等"
            "只在该物体本来具有时展示，不得为了丰富画面凭空增加部件。背景干净中性；"
            "不要出现完整人物、交接动作、房间、柜台、商品背景或剧情场景。纸张、合同和"
            "文件只保留不可辨认的中性版式块，不生成乱码或可读正文。不要宫格、拼贴、"
            "商标或额外部件；若上方文字策略列出必需场内文字，仅例外保留该文字。"
        )
    elif asset.kind == AssetKind.PROP:
        layout = (
            "上游尚未用结构化字段声明该资产是实体道具母版还是屏幕内容母版。"
            "不得根据资产名称、描述中的词语或引号猜测呈现形态；由 Asset Agent "
            "先补充 presentation_mode，再选择对应母版生成方式。"
        )
    else:
        layout = "生成一张符合当前 Style、可供后续镜头构图使用的清晰视觉参考图。"
    style_context = ""
    if asset.kind == AssetKind.LOCATION:
        style_context = (
            "\n只使用上述基础母版时点已经可见的色彩、天气与材质。"
            "全片风格可能包含后段状态，不得把后续烟雾、火灾、灰烬、"
            "损坏或灯光变化提前画进本图。"
        )
    asset_identity = f"资产名称：{asset.name}"
    presentation_context = (
        f"\n结构化呈现类型：{presentation_mode.value}"
        if presentation_mode is not None
        else "\n结构化呈现类型：未声明"
    )
    return (
        f"{shared}\n{layout}\n{asset_identity}\n资产描述：{descriptor}。"
        f"{presentation_context}{style_context}\n制作类型策略：{skill_prompt.text}"
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
        + "\n\n输入图像是素材库人物照片，只作为当前任务声明范围内的构图、造型或人物特征软参考。"
        "输出媒介与质感仍服从项目 Style。创建一个全新的虚构人物身份；不得复制、"
        "保留或近似复刻输入人物可识别的面孔与身份。必须优先服从本项目资产描述，"
        "输出仍是一张可供后续派生和镜头关键帧使用的角色身份母版。"
    )


def build_stock_grounded_asset_prompt(
    document: ProjectDocument,
    asset: Asset,
) -> str:
    if asset.kind == AssetKind.CHARACTER:
        return build_stock_grounded_character_prompt(document, asset)
    required_labels = (
        list(asset.brief.required_visible_text) if asset.brief is not None else []
    )
    required_label_scope = (
        "并按结构化资产简报准确重建必需的场内实物文字"
        if required_labels
        else ""
    )
    presentation_mode = (
        asset.brief.presentation_mode if asset.brief is not None else None
    )
    if presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE:
        grounding_scope = (
            "输入图像只是布局、层级或信息密度参考。按结构化资产简报重建"
            f"虚构屏幕内容，删除真实平台识别、品牌和无关文字{required_label_scope}。"
        )
    elif asset.kind == AssetKind.PROP and presentation_mode is None:
        grounding_scope = (
            "上游尚未显式声明实体道具或屏幕内容呈现类型；不从名称、描述或图像"
            "内容猜测，待 Asset Agent 补充 presentation_mode 后再选择对应重建方式。"
        )
    else:
        grounding_scope = {
            AssetKind.LOCATION: (
                "输入图像是场景布局、尺度或材质的参考。创建本项目中的虚构地点，"
                "只保留对当前 Style 和镜头有用的关系，并服从资产描述重构入口、通道、"
                f"设备区与光线；删除原图中的无关文字、品牌和不属于剧情的物体{required_label_scope}。"
            ),
            AssetKind.PROP: (
                "输入图像是现实物体的尺度、结构和材质参考。创建本项目中的虚构道具，"
                f"保留合理工业构造，但必须服从资产描述；删除品牌、无关可读文字和无关附件{required_label_scope}。"
            ),
        }.get(
            asset.kind,
            "输入图像仅作声明范围内的视觉参考，输出必须服从本项目资产描述与 Style。",
        )
    return f"{build_base_asset_prompt(document, asset)}\n\n{grounding_scope}"


def canonical_base_asset_descriptor(
    document: ProjectDocument,
    asset: Asset,
) -> str:
    """Remove shot-state prose from the canonical image-master request."""

    descriptor = _structured_asset_descriptor(asset)
    if asset.kind == AssetKind.LOCATION:
        first_shot = _first_location_appearance_shot(document, asset)
        if first_shot is not None:
            return (
                f"结构化基础描述：{descriptor}；"
                "基础母版时点：以显式绑定该资产的最早镜头为准；"
                f"首次显式引用镜头：{first_shot.visual}；"
                f"该镜头入点物件状态：{first_shot.continuity.prop_state_in}；"
                "由视觉 Agent 语义区分定场资产与人物动作；该时点只来自显式的"
                "资产引用，后续状态由派生资产或镜头关键帧表达"
            )
    return descriptor


def _structured_asset_descriptor(asset: Asset) -> str:
    brief = asset.brief
    if brief is None:
        return asset.versions[-1].descriptor
    parts = [f"身份锚点：{brief.identity_anchor}"]
    if brief.tone_or_temperament is not None:
        parts.append(f"气质或氛围：{brief.tone_or_temperament}")
    structured_lists = (
        ("可见属性", brief.visual_attributes),
        ("服饰或材质", brief.wardrobe_or_materials),
        ("背景约束", brief.background_constraints),
        ("质感约束", brief.texture_constraints),
        ("连续性锚点", brief.continuity_anchors),
        ("命名视图或区域", brief.named_views_or_zones),
    )
    parts.extend(
        f"{label}：{'、'.join(values)}"
        for label, values in structured_lists
        if values
    )
    return "；".join(parts)


def _first_location_appearance_shot(
    document: ProjectDocument,
    asset: Asset,
) -> ShotPlan | None:
    """Find the first shot that actually uses a location as its current setting."""

    asset_version_ids = {version.id for version in asset.versions}
    planned_shot_ids = set[str]()
    for plan in document.reference_plan:
        explicitly_required = any(
            requirement.subject_asset_id == asset.id
            for requirement in plan.requirements
        )
        explicitly_bound = any(
            binding.target_id in asset_version_ids for binding in plan.bindings
        )
        if explicitly_required or explicitly_bound:
            planned_shot_ids.add(plan.shot_id)
    for shot in document.shots:
        if any(
            cue.subject_asset_id == asset.id
            for beat in shot.timeline_beats
            for cue in beat.references
        ):
            planned_shot_ids.add(shot.id)
    ordered = sorted(document.shots, key=lambda item: item.sequence)
    return next(
        (shot for shot in ordered if shot.id in planned_shot_ids),
        None,
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
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
        include_constitution=False,
    )
    if (
        asset.brief is not None
        and asset.brief.presentation_mode == AssetPresentationMode.SCREEN_CONTENT_PLATE
    ):
        review_scope = (
            "审核这个已显式声明为屏幕内容母版的虚构资产：判断信息层级、后期贴合用途，"
            "以及结构化资产简报 required_visible_text 中每一项可见文字是否准确。"
            "乱码、语义替换、无关界面文字或可识别的真实平台品牌都应记为问题。"
            "这是干净的屏幕内容板，不要求出现手机外壳或演员的手。"
        )
    else:
        review_scope = {
            AssetKind.CHARACTER: (
                "审核已声明角色的身份、人体结构、服装，以及不同参考视图之间的一致性。"
            ),
            AssetKind.LOCATION: (
                "审核空间拓扑、灯光、材质和实际制作可用性。偶然出现的匿名尺度参照人物"
                "不是剧情角色引用；除非输入显式绑定了角色，不要因其身份或服装拒绝资产。"
                "项目级场景母版以结构化描述中的最早显式引用状态为准；若画面提前出现后续"
                "才有的天气、烟雾、火焰、损坏、门锁开启、道具位移、设备切换或其他剧情"
                "状态，应报告未来状态泄漏。稳定空间拓扑与时间状态变体必须分开；明确出现"
                "仅属于后续的状态时，不建议采用该候选。"
            ),
            AssetKind.PROP: (
                "只审核道具的形状、尺度、材质、部件位置和跨视图一致性。匿名且被裁切的手"
                "只作为尺度参照。除非关键帧准备数据显式要求，不要追加剧情角色身份、服装、"
                "交接顺序、左右手或场景连续性要求。"
            ),
            AssetKind.REFERENCE: (
                "按输入显式声明的镜头或参考用途，审核其中明确要求可见的每个主体。"
            ),
        }.get(asset.kind, "依据输入的结构化描述审核画面的制作可用性。")
    instructions = (
        "你是 AI 原生竖屏视频生产管线中的视觉资产检查 Agent。"
        f"{skill_prompt.text} "
        "只返回符合给定 schema 的 JSON。只比较图像中的可见证据与输入的资产简报、项目 "
        "Bible，以及可选的 ShotPlan 或关键帧简报。项目 Bible 已声明的媒介和 Style 是依据，"
        "不要追加项目未要求的视觉媒介、表面质感、机位或运动规则。"
        "审核镜头关键帧时分两步：先独立识别画面中的主体、必要时的肢体、接触关系、道具"
        "状态、视线和动作阶段，不预设期望答案；再与显式声明的开始/结束状态比较。"
        "如果画面明确违背了已声明的左右手、接触侧或动作阶段，报告通用连续性问题。"
        "如果左右侧或阶段在视觉上含混，不要猜测其正确；只有当该元素被显式要求在画面中"
        "可见时，才降低评分并建议更清楚的关键帧。若 ShotPlan 显式把演员、手、道具或状态"
        "放在画外，其缺席就是符合要求的证据；不要强求画面展示它的接触、左右侧、亮灭状态"
        "或动作阶段，也不要仅因画外状态无法验证就拒绝图像。可以把这个限制写成不影响采用"
        "的观察项。若关键帧中的可见开始/结束状态与声明相反，或被要求清楚展示的接触或"
        "左右侧仍含混，应设置 recommended=false；画面美观不能抵消这些制作控制问题。"
        "检查已声明 Style、可见破损、参考表内身份一致性、必需的场景关系、道具形状、构图"
        "可用性，以及多余文字或水印。不要发明要求。recommendation 是可被 Agent 或用户"
        "覆盖的制作建议，不是用户审批条件；使用通用 QA 分类和稳定 issue code。"
        "提供标准参考图时，Image 1 始终是待审核候选，后续图片是已批准参考。只比较与当前"
        "资产 ID 对应的身份和外观锚点、必需场景关系及道具结构，不要孤立评价候选，也不要"
        "平均或混合不同角色身份。"
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
            "brief": (
                asset.brief.model_dump(mode="json")
                if asset.brief is not None
                else None
            ),
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
                "仅当图像可直接承担声明的制作参考职责，且没有严重可见矛盾或破损时为 true"
            ),
            "score": "0-5 分，衡量制作可用性而非审美新奇程度",
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
