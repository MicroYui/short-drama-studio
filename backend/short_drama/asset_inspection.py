"""Agent-callable visual asset planning and semantic inspection."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, ValidationError, field_validator, model_validator

from short_drama.h3_models import H3RouteAssetPreparation
from short_drama.image_models import ImageGenerationTask, ImageRegion, parse_image_size
from short_drama.image_edit_guides import (
    ImageEditGuideRegion, ImageGuideArrow, validate_edit_guide_geometry,
)
from short_drama.image_plane_composite import (
    AssetImagePlaneCompositeRequest, validate_plane_composite_geometry,
)
from short_drama.image_regions import read_image_region, region_box
from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetKind,
    AssetImageCrop,
    AssetLayoutProfile,
    AssetPresentationMode,
    AssetVersion,
    DomainModel,
    Freshness,
    ImageReferenceAuthority,
    ImageReferenceRole,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    ShotPlan,
)
from short_drama.production_profiles import (
    ProductionPromptStage,
    ProductionSkillRole,
    compile_production_prompt,
    production_skill,
)
from short_drama.providers.openai_compatible_language_client import LanguageProviderClient
from short_drama.style_context import declared_style_value


class SemanticAssetReviewEvidenceReference(DomainModel):
    """Exact image evidence attached by the Tool runtime after semantic review."""

    source_project_id: str = Field(min_length=1)
    asset_version_id: str = Field(min_length=1)


class AssetInspectionRegion(DomainModel):
    asset_version_id: str | None = Field(
        default=None, description="Omit for the candidate; otherwise use one of its actual inspection reference IDs.",
    )
    label: str = Field(min_length=1, max_length=200)
    region: ImageRegion


class AssetInspectionRegionEvidence(DomainModel):
    asset_version_id: str
    label: str
    source_width: int
    source_height: int
    box_pixels: tuple[int, int, int, int]


class AssetCropSuggestion(DomainModel):
    """An optional observed crop, never an automatic edit or a passed review."""

    region: ImageRegion
    reason: str = Field(min_length=1, max_length=1000)
    source: AssetImageCrop | None = Field(
        default=None,
        description="Runtime-attached candidate version, original visible size and pixel box. Return null in model output.",
    )


class AssetEditGuideSuggestion(DomainModel):
    """Observed optional geometry for the existing candidate edit-guide Tool."""

    regions: list[ImageEditGuideRegion] = Field(min_length=1, max_length=16)
    arrows: list[ImageGuideArrow] = Field(default_factory=list[ImageGuideArrow], max_length=16)
    descriptor: str = Field(min_length=1, max_length=2000)
    source_asset_version_id: str | None = Field(default=None,
        description="Runtime-attached exact candidate. Return null; never choose another reference as edit source.")


class SemanticAssetReview(DomainModel):
    recommended: bool
    score: float = Field(ge=0.0, le=5.0)
    issues: list[QaIssue] = Field(default_factory=list[QaIssue], max_length=16)
    summary: str = Field(min_length=1, max_length=3000)
    review_focus: str | None = None
    candidate_output_layout: AssetLayoutProfile | None = None
    region_evidence: list[AssetInspectionRegionEvidence] = Field(
        default_factory=list[AssetInspectionRegionEvidence], max_length=6,
    )
    crop_suggestion: AssetCropSuggestion | None = Field(
        default=None,
        description=(
            "Optional candidate-only crop when visible framing can be repaired by cropping. "
            "Use normalized x/y/width/height in the full EXIF-oriented candidate, not a detail crop. "
            "The suggestion preserves the existing recommendation and does not perform an edit."
        ),
    )
    edit_guide_suggestion: AssetEditGuideSuggestion | None = Field(default=None,
        description="Optional observed candidate regions and target arrows for image.create_edit_guide; null if uncertain or a local edit cannot preserve the required composition.")
    plane_composite_suggestion: AssetImagePlaneCompositeRequest | None = Field(
        default=None,
        description=(
            "Optional image.composite_plane request using only the actual candidate and visible, "
            "locally available references. Preserve source-to-target corner identities for whole-plane "
            "rotation. Null when corners, foreground protection or suitable source content are uncertain."
        ),
    )
    evidence_references: list[SemanticAssetReviewEvidenceReference] = Field(
        default_factory=list[SemanticAssetReviewEvidenceReference],
        max_length=16,
        description=(
            "Tool runtime attached exact image evidence. This is provenance, not a "
            "request for the language model to create artifact references."
        ),
    )

    @field_validator("edit_guide_suggestion", mode="before")
    @classmethod
    def keep_only_usable_optional_guide(cls, value: object) -> AssetEditGuideSuggestion | None:
        if value is None:
            return None
        try:
            return AssetEditGuideSuggestion.model_validate(value)
        except ValidationError:
            # A malformed optional edit cannot invalidate an otherwise usable QA.
            return None

    @field_validator("plane_composite_suggestion", mode="before")
    @classmethod
    def keep_only_usable_optional_plane(cls, value: object) -> AssetImagePlaneCompositeRequest | None:
        if value is None:
            return None
        try:
            return AssetImagePlaneCompositeRequest.model_validate(value)
        except ValidationError:
            return None


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
            "Current explicitly selected usable image versions that define the identity, appearance, "
            "space, or prop structure the candidate should preserve."
        ),
    )
    review_focus: str | None = Field(default=None, min_length=1, max_length=2000)
    regions: list[AssetInspectionRegion] = Field(
        default_factory=list[AssetInspectionRegion], max_length=6,
        description=(
            "Optional detail areas seen in the image: face, small prop, text or contact. "
            "Full images remain available; these local crops add evidence in the same visual call. "
            "Coordinates are normalized after EXIF orientation. They do not improve source detail."
        ),
    )


class SourceImageHandling(StrEnum):
    """Agent-facing ways an exact source image can enter production."""

    REUSE_AS_TARGET_MASTER = "REUSE_AS_TARGET_MASTER"
    DERIVE_STANDARD_ASSET = "DERIVE_STANDARD_ASSET"
    SUPPORTING_REFERENCE_ONLY = "SUPPORTING_REFERENCE_ONLY"
    NOT_USABLE = "NOT_USABLE"


class SourceImageAssignment(DomainModel):
    """A semantic assignment already decided by the Agent or explicitly by the user."""

    target_asset_kind: AssetKind
    target_name: str = Field(min_length=1, max_length=240)
    reference_role: ImageReferenceRole
    reference_authority: ImageReferenceAuthority
    purpose: str = Field(min_length=1, max_length=1000)
    target_layout_profile: AssetLayoutProfile | None = None


class SourceImageAnalysisRequest(DomainModel):
    """Optional intended use for visually analysing one exact source image."""

    assignment: SourceImageAssignment | None = None
    additional_context: str | None = Field(default=None, min_length=1, max_length=2000)


class SemanticSourceImageAnalysis(DomainModel):
    """Visible-evidence analysis that informs, but never replaces, Agent judgment."""

    observed_content: str = Field(min_length=1, max_length=3000)
    usable_for_assignment: bool
    visible_coverage: list[str] = Field(default_factory=list[str], max_length=24)
    missing_coverage: list[str] = Field(default_factory=list[str], max_length=24)
    suggested_reference_roles: list[ImageReferenceRole] = Field(
        default_factory=list[ImageReferenceRole],
        max_length=8,
    )
    recommended_handling: SourceImageHandling
    reusable_as_target_master: bool
    needs_derivation: bool
    cautions: list[str] = Field(default_factory=list[str], max_length=16)
    summary: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_handling_flags(self) -> SemanticSourceImageAnalysis:
        if self.reusable_as_target_master and self.needs_derivation:
            raise ValueError(
                "A source image cannot require derivation and be directly reusable"
            )
        expected_flags = {
            SourceImageHandling.REUSE_AS_TARGET_MASTER: (True, True, False),
            SourceImageHandling.DERIVE_STANDARD_ASSET: (True, False, True),
            SourceImageHandling.NOT_USABLE: (False, False, False),
        }
        expected = expected_flags.get(self.recommended_handling)
        if expected is not None and (
            self.usable_for_assignment,
            self.reusable_as_target_master,
            self.needs_derivation,
        ) != expected:
            raise ValueError(
                "Source image handling is inconsistent with its usability flags"
            )
        if self.recommended_handling == SourceImageHandling.SUPPORTING_REFERENCE_ONLY:
            if self.reusable_as_target_master or self.needs_derivation:
                raise ValueError(
                    "A supporting-only source cannot be a target master or derivation job"
                )
        return self


class AssetInspectionReferenceSource(StrEnum):
    """Why one exact image is included in a semantic asset inspection."""

    EXPLICIT_CANONICAL = "EXPLICIT_CANONICAL"
    DERIVATION_SOURCE = "DERIVATION_SOURCE"


@dataclass(frozen=True)
class AssetInspectionReference:
    """Resolved image evidence with the exact semantic contract used in production."""

    asset: Asset
    version: AssetVersion
    project_id: str
    reference_role: ImageReferenceRole
    reference_authority: ImageReferenceAuthority
    purpose: str
    subject_alias: str | None
    source: AssetInspectionReferenceSource
    normative_for_declared_role: bool


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
        stage=ProductionPromptStage.ASSETS,
    )
    required_labels = (
        list(asset.brief.required_visible_text) if asset.brief is not None else []
    )
    presentation_mode = (
        asset.brief.presentation_mode if asset.brief is not None else None
    )
    layout_profile = (
        asset.brief.layout_profile
        if asset.brief is not None
        else AssetLayoutProfile.LIGHTWEIGHT_SINGLE
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
    output_shape = (
        "输出严格的 16:9 横向机器参考卡，"
        if layout_profile
        in {
            AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9,
            AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2,
        }
        else "输出单幅纯视觉内容，"
    )
    shared = (
        f"{skill.medium}制片参考资产。{style_instruction}"
        "不要擅自追加当前 Style 未声明的视觉媒介、审美、材质或表现规则。"
        f"{output_shape}{text_policy}"
    )
    if is_screen_content:
        layout = (
            "生成一张单幅、正视、可供后期贴入屏幕的虚构界面内容母版。"
            "信息层级与区域只服从结构化资产简报，不复制任何真实平台的 logo "
            "或完整视觉识别。不要手、手机外壳、透视变形、反光、多个页面、"
            "宫格或拼贴。"
        )
    elif layout_profile == AssetLayoutProfile.DRAMA_CHARACTER_DUAL_ZONE_16_9:
        if asset.kind != AssetKind.CHARACTER:
            raise ValueError(
                "DRAMA_CHARACTER_DUAL_ZONE_16_9 requires a character asset"
            )
        layout = (
            "按短剧角色标准卡生成一张严格 16:9 横图。画面只分左右两个区域："
            "左侧占整幅约 50%，展示角色正面大尺寸脸部近景；右侧占整幅约 50%，"
            "再等分为三列，依次展示同一角色、同一身份、同一发型、同一服饰的"
            "全身正面、全身侧面、全身背面。各视图身材比例、五官、发型、服装、"
            "配饰和颜色必须一致，不增加动作、表情变化或剧情状态。统一使用"
            "中性灰 #7c7c7c 背景和柔和影棚光；不要文字、视图名称、分隔说明、"
            "logo、水印、额外人物或场景道具。"
        )
    elif layout_profile == AssetLayoutProfile.CHARACTER_FOUR_VIEW_4X1:
        if asset.kind != AssetKind.CHARACTER:
            raise ValueError("CHARACTER_FOUR_VIEW_4X1 requires a character asset")
        layout = (
            "同一人物的完整全身四视图，横向四列依次为正面、人物左侧、背面、人物右侧。"
            "四个人物等高，头顶和脚底齐平，脸型、发型、服装、配饰、身材保持一致；"
            "侧面是真正的侧身而不是重复正面或镜像。若提供身份原图，严格延续这张图中的人。"
            "柔和均匀光线、干净中性背景，不添加剧情动作、文字、水印或第五个人物。"
        )
    elif layout_profile == AssetLayoutProfile.CHARACTER_SIX_VIEW_3X2:
        if asset.kind != AssetKind.CHARACTER:
            raise ValueError("CHARACTER_SIX_VIEW_3X2 requires a character asset")
        layout = (
            "按角色六视图标准卡生成一张 3×2 规则网格：同一角色的正面全身、"
            "左侧全身、右侧全身、背面全身、正面脸部近景和服饰细节。六格必须"
            "保持同一身份、五官、体型、发型、服装、配饰、颜色与材质；使用统一"
            "中性背景和柔和影棚光。不要文字、标签、logo、水印、剧情动作、"
            "额外人物或场景。"
        )
    elif layout_profile == AssetLayoutProfile.SCENE_FOUR_DIRECTION_2X2:
        if asset.kind != AssetKind.LOCATION:
            raise ValueError("SCENE_FOUR_DIRECTION_2X2 requires a location asset")
        layout = (
            "按场景标准卡生成一张严格 16:9 横图，以 2×2 网格展示同一空间的"
            "正向、反向、左向、右向四个观察方向。四格必须是同一地点、同一时点、"
            "同一平面布局、家具位置、材质、照明、天气和时代设定，门窗、通道、"
            "柜台及关键空间地标能够互相对应。只展示空场环境，不出现人物、动作"
            "或互相矛盾的空间结构；不要文字、方向标签、logo 或水印。"
        )
    elif layout_profile == AssetLayoutProfile.PROP_MASTER_SINGLE:
        if asset.kind != AssetKind.PROP:
            raise ValueError("PROP_MASTER_SINGLE requires a prop asset")
        layout = (
            "生成单个实体道具的单角度产品母版。完整呈现其真实轮廓、尺度感、"
            "颜色、材质、磨损和剧情所需结构，置于干净中性背景和柔和影棚光下。"
            "只出现一个道具，不出现手、人物、房间、柜台、商品陈列、剧情动作、"
            "多视图、宫格或拼贴；不凭空增加按钮、接口、附件、文字、logo 或水印。"
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
        f"{presentation_context}\n结构化参考卡形态：{layout_profile.value}"
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


def analyze_source_image(
    *,
    document: ProjectDocument,
    asset: Asset,
    version: AssetVersion,
    request: SourceImageAnalysisRequest,
    client: LanguageProviderClient,
) -> SemanticSourceImageAnalysis:
    """Observe one exact user/source image before the Agent assigns asset work."""

    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_REVIEWER,),
        include_constitution=False,
        stage=ProductionPromptStage.ASSETS,
    )
    assignment = request.assignment
    assignment_policy = (
        "当前没有预先指定用途。只根据可见证据提出可能承担的参考职责；"
        "不要把人物、服饰、场景、道具、风格或构图中的任何一种自动扩散为全片规则。"
        if assignment is None
        else (
            "结构化 assignment 是 Agent 根据完整对话作出的用途声明，不是让你重新路由。"
            "你只判断这张图在该用途下能看见什么、缺少什么、能否直接复用，或是否应以它"
            "为精确参考派生标准资产。"
        )
    )
    authority_policy = ""
    if (
        assignment is not None
        and assignment.reference_role == ImageReferenceRole.SUBJECT_IDENTITY
        and assignment.reference_authority == ImageReferenceAuthority.AUTHORITATIVE
    ):
        authority_policy = (
            "用户或 Agent 已把图中人物声明为目标角色的权威身份来源。不得质疑、替换、"
            "平均或重新设计这个人的身份；只评估脸部、发型、体型、服装及视角覆盖是否"
            "足以直接承担目标母版。若需要侧面、背面、全身或服饰补全，结论应是从此图"
            "派生，并明确不得变成另一个人。"
        )
    instructions = (
        "你是视频制片中的源素材观察 Agent。只返回符合给定 schema 的 JSON。"
        f"{skill_prompt.text} "
        "图像是用户或上游工作流提供的精确原始证据。先客观描述可见主体、取景、角度、"
        "服饰、空间、道具、可读文字和画质；不得臆造画外的侧面、背面、全身、空间拓扑"
        "或物体结构。再结合结构化 assignment 判断处理方式。完整且覆盖目标布局的素材"
        "可以建议 REUSE_AS_TARGET_MASTER；能锁定身份或物体但覆盖不足时建议"
        " DERIVE_STANDARD_ASSET；只提供风格、构图或局部信息时建议"
        " SUPPORTING_REFERENCE_ONLY；确实无法承担声明用途时才建议 NOT_USABLE。"
        "reusable_as_target_master 与 needs_derivation 要反映可见覆盖，不按文件名、关键词"
        "或美观程度猜测。分析是 Agent 可结合完整上下文覆盖的语义建议，不是审批门。"
        f"{assignment_policy}{authority_policy}"
    )
    payload = {
        "output_schema": SemanticSourceImageAnalysis.model_json_schema(),
        "source_asset": {
            "id": asset.id,
            "kind": asset.kind.value,
            "name": asset.name,
            "descriptor": version.descriptor,
            "version_id": version.id,
            "width": version.width,
            "height": version.height,
        },
        "assignment": (
            assignment.model_dump(mode="json") if assignment is not None else None
        ),
        "additional_context": request.additional_context,
        "project_style": next(
            item.style.model_dump(mode="json")
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        "decision_guidance": {
            "explicit_assignment_is_context_truth": assignment is not None,
            "identity_must_be_preserved": bool(
                assignment is not None
                and assignment.reference_role
                == ImageReferenceRole.SUBJECT_IDENTITY
                and assignment.reference_authority
                == ImageReferenceAuthority.AUTHORITATIVE
            ),
            "do_not_infer_unseen_views": True,
        },
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=[_asset_image_url(version)],
        max_output_tokens=16000,
    )
    return SemanticSourceImageAnalysis.model_validate_json(_strip_json_fence(output))


def inspect_asset_image(
    *,
    document: ProjectDocument,
    asset: Asset,
    version: AssetVersion,
    client: LanguageProviderClient,
    shot: ShotPlan | None = None,
    preparation: H3RouteAssetPreparation | None = None,
    canonical_references: list[tuple[Asset, AssetVersion]] | None = None,
    inspection_references: list[AssetInspectionReference] | None = None,
    generation_task: ImageGenerationTask | None = None,
    request: AssetVersionInspectionRequest | None = None,
) -> SemanticAssetReview:
    request = request or AssetVersionInspectionRequest()
    skill_prompt = compile_production_prompt(
        document.project.production_profile,
        roles=(ProductionSkillRole.ASSET_VERIFIER,),
        include_constitution=False,
        stage=ProductionPromptStage.ASSETS,
    )
    if version.text_plate is not None:
        review_scope = (
            "审核本张确定性平面内容候选的准确文字、信息层级、留白、线条和声明的贴合用途。"
            "依据 candidate_creation.text_plate 检查实际文字和布局；required_visible_text 中的正文、"
            "名称、金额及内部标点不得改变，空白行、字段标签末尾冒号和装饰横线属于排版。"
            "若本候选有 content_state，以其 name 和 required_visible_text 为本状态的完整准确文字，"
            "同时保留 brief 的固定文字；明确登记的本状态签名不是多余文字，不要求旧空白候选也出现该签名。"
            "未声明 content_state 时仍使用 brief.required_visible_text，不从 descriptor 猜测新增文字。"
            "若 purpose=PHYSICAL_SURFACE_CONTENT，它只承担物理道具的表面内容，"
            "不要求本图出现纸张厚度、摄影透视或演员接触，也不据此判定物理母图完成。"
        )
    elif (
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
        "只返回符合给定 schema 的 JSON。evidence_references 由工具运行时附着，"
        "你始终返回空数组，不要自行填写项目或版本 ID。"
        "region_evidence、review_focus、candidate_output_layout 也由工具附着，分别返回空数组/null/null。"
        "crop_suggestion 默认为null。仅当 candidate_creation.local_crop_available 为true，且已看清"
        "通过本地裁切能解决的景别、留白或单视图提取问题时，在本次观察中给出一个可用region和reason，"
        "避免只说建议裁切却让后续再看同一图取坐标；source返回null，由工具附着实际源版本和像素范围。"
        "region使用Image 1完整候选经EXIF校正后的0—1 x/y/width/height，不使用参考图、追加局部图或"
        "缩略图像素坐标。保留声明必需的头发、肢体、文字及视线空间；以实际源宽高计算裁后画幅。"
        "若源和目标画幅相同，归一化width和height应相同。裁切不能增加细节、改变背景结构或移动主体；"
        "无法兼顾要求或边界看不清时返回null并说明限制，不靠放大凑像素。"
        "建议尚未执行，不能把裁后的预期效果写成当前图已合格，也不能消除其他实际问题。"
        "edit_guide_suggestion也默认为null。只有candidate_creation.local_edit_guide_available为true，"
        "已看清候选中的具体问题且适合局部编辑时，才一次给出regions、可选arrows和descriptor；"
        "source_asset_version_id返回null，运行时只绑定Image 1候选。坐标均为完整候选EXIF校正后"
        "的0—1位置，不能使用参考或局部放大图的坐标。polygon圈出实际要改的范围；如需移动物体，"
        "包含原位置、明确目标位置及必要接触或阴影范围，arrows说明可见特征的目标方向。"
        "箭头只是编辑指示，不是要画入成图的内容。只按已声明目标和本次实际可见位置作图，"
        "不猜遮挡边界、画外布局或不存在的新画面。大机位、多人站位或整体透视变化通常不能用"
        "狭小mask修复，应说明需扩大编辑范围或不用局部mask；范围无法确定则留空。"
        "建议不执行编辑、不改变recommended或已有问题，也不表示编辑后已经验收。"
        "只比较图像中的可见证据与输入的资产简报、项目 "
        "Bible，以及可选的 ShotPlan 或关键帧简报。项目 Bible 已声明的媒介和 Style 是依据，"
        "candidate_creation 是这张图片实际保存的生成/裁切意图；asset.brief 是整个资产的总体要求。"
        "只审核本张图片承担的视图和职责；总体brief列四视图，不代表单张正脸或侧面图必须展示四视图。"
        "candidate_creation.text_plate 表示本地确定性平面内容；PHYSICAL_SURFACE_CONTENT 只检查准确文字、版式与当前文案状态，"
        "不要求该平面PNG已呈现母道具的摄影厚度、透视或手部接触，也不据此判定物理母图已完成。"
        "image_map 中参考的 text_plate 是实际输入字版的内容与状态来源；审核派生摄影时，"
        "按本次 generation_prompt 与 reference_role/purpose 指定的用途核对所选字版的准确文字和签名状态。"
        "不要把多个不同参考状态合并，也不要因总体 brief 只列固定正文而忽略明确选中的状态姓名；"
        "目标状态未明确时记录缺少依据，不自行推断。"
        "生成prompt是意图，不是已观察事实。先说明真实可见的身份、轮廓、空间或文字，再比较要求。"
        "细节区域与原图属于同一证据，不是不同身份或新增参考。小脸、细小文字和部件看不清时明确未观察，"
        "不能用总分或同色服装代替身份比对；记录哪个部位和下游用途仍缺可见依据。"
        "不要追加项目未要求的视觉媒介、表面质感、机位或运动规则。"
        "比较不同视角时，区分实际结构变化与透视、遮挡和正常的可见面变化。"
        "某个正面部件在背面不可见，并不证明它缺失或被换成另一种结构；"
        "只有可见证据支持时才报告跨视图结构不一致，不要求不同视角显示相同表面。"
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
        "提供参考图时，Image 1 始终是待审核候选，后续图片的准确职责、权威级别、"
        "用途、主体别名、生命周期状态和来源都在 image_map 中结构化声明。只在它声明的"
        "reference_role 范围内比较，不要平均或混合不同角色身份。"
        "参考项 descriptor 来自该精确图片版本，是它原有机位、空间或内容的说明，"
        "不是当前候选已符合要求的结论，也不要求候选照搬参考图原机位。"
        "EXPLICIT_CANONICAL 是本次明确选择的可用参考，可为 DRAFT；DERIVATION_SOURCE 是候选生成时实际使用的"
        "历史证据，不因后来更换母版而消失。approval_status=DRAFT 不等于该图已被"
        "批准为资产；但若它是用户明确锁定的权威源图且 normative_for_declared_role=true，"
        "它仍是该声明职责内的事实来源，不得仅因 DRAFT 状态判定派生候选失败。"
        "normative_for_declared_role=false 的派生输入只说明模型看过它，不是已批准的"
        "身份、外观、场景或道具真相。"
        + review_scope
    )
    resolved_references = list(inspection_references or [])
    resolved_references.extend(
        AssetInspectionReference(
            asset=reference_asset,
            version=reference_version,
            project_id=document.project.id,
            reference_role=_default_reference_role(reference_asset.kind),
            reference_authority=ImageReferenceAuthority.AUTHORITATIVE,
            purpose="当前明确选择的可用参考，用于审核候选的连续性。",
            subject_alias=reference_asset.name,
            source=AssetInspectionReferenceSource.EXPLICIT_CANONICAL,
            normative_for_declared_role=True,
        )
        for reference_asset, reference_version in (canonical_references or [])
        if reference_version.id
        not in {item.version.id for item in resolved_references}
    )
    reference_images = _inspection_reference_images(resolved_references)
    detail_urls: list[str] = []
    detail_evidence: list[AssetInspectionRegionEvidence] = []
    available_versions = {version.id: version, **{item.version.id: item.version for item in resolved_references}}
    for detail in request.regions:
        detail_id = detail.asset_version_id or version.id
        detail_version = available_versions.get(detail_id)
        if detail_version is None:
            raise ValueError(f"Inspection region asset_version_id is not a candidate or declared reference: {detail_id}")
        if detail_version.local_path is None:
            raise ValueError(f"Inspection region requires a locally cached image: {detail_id}")
        cropped, source_size, box = read_image_region(Path(detail_version.local_path), detail.region)
        with cropped:
            encoded = base64.b64encode(_encode_review_image(cropped)).decode("ascii")
        detail_urls.append(f"data:image/jpeg;base64,{encoded}")
        detail_evidence.append(AssetInspectionRegionEvidence(
            asset_version_id=detail_id, label=detail.label,
            source_width=source_size[0], source_height=source_size[1], box_pixels=box,
        ))
    output_layout = (
        generation_task.request.layout_profile if generation_task is not None
        else AssetLayoutProfile.LIGHTWEIGHT_SINGLE if version.image_crop is not None
        else version.reference_sheet.layout_profile if version.reference_sheet is not None
        else None
    )
    crop_source_size = _local_candidate_visible_size(version)
    crop_available = (
        crop_source_size is not None
        and version.approval_status in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
        and version.freshness == Freshness.CURRENT
    )
    edit_guide_available = False
    if crop_available and version.local_path is not None and crop_source_size is not None:
        with Image.open(version.local_path) as source:
            # The guide and masked EDIT share the original upright pixel grid.
            try:
                parse_image_size(f"{crop_source_size[0]}x{crop_source_size[1]}")
            except ValueError:
                pass
            else:
                edit_guide_available = source.getexif().get(274, 1) == 1
    plane_base_size = _local_plane_image_size(version)
    plane_source_sizes: dict[str, tuple[int, int]] = {}
    whole_content_plate_ids: set[str] = set()
    for reference, _url in reference_images:
        # A cross-project image can remain visual evidence, but this Tool's
        # source IDs resolve in the current project only.
        if reference.project_id != document.project.id or reference.version.id == version.id:
            continue
        size = _local_plane_image_size(reference.version)
        if size is not None:
            plane_source_sizes[reference.version.id] = size
            if reference.version.text_plate is not None:
                whole_content_plate_ids.add(reference.version.id)
    plane_available = plane_base_size is not None and bool(plane_source_sizes)
    instructions += (
        "可选plane_composite_suggestion可直接交给image.composite_plane。仅从image_map中"
        "local_plane_composite_source.available=true的本次所见精确源图选择，base填写当前候选version_id。"
        "坐标按对应整图实际EXIF转正尺寸，不按局部放大图；摄影源明确source_quad，只有"
        "full_image_is_content_plate=true可省略。源角逐点对应目标角，不能两边各按屏幕左上排序。"
        "整页字形与版面同转不等于竖排重写。适用性、遮挡与混合方式按已加载方法和schema；"
        "边角不清、旧纸残留或实际需要改手姿机位时留null，不改变已有QA结论。"
        if plane_available else
        "本次没有可用于本地平面合成的基图和已观察来源，plane_composite_suggestion返回null，"
        "不为取得建议追加发现、回读或视觉调用。"
    )
    creation = {
        "output_layout_profile": output_layout,
        "actual_size": {
            "width": crop_source_size[0] if crop_source_size else version.width,
            "height": crop_source_size[1] if crop_source_size else version.height,
        },
        "local_crop_available": crop_available,
        "local_edit_guide_available": edit_guide_available,
        "local_plane_composite_available": plane_available,
        "plane_composite_tool": "image.composite_plane",
        "operation": generation_task.operation if generation_task is not None else version.origin_type,
        "generation_prompt": generation_task.request.prompt if generation_task is not None
        else version.source.prompt if version.source is not None else None,
        "crop": version.image_crop.model_dump(mode="json") if version.image_crop is not None else None,
        "reference_sheet": version.reference_sheet.model_dump(mode="json") if version.reference_sheet is not None else None,
        "text_plate": version.text_plate.model_dump(mode="json") if version.text_plate is not None else None,
    }
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
        "candidate_creation": creation,
        "review_focus": request.review_focus,
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
                    "role": (
                        "approved_canonical_reference"
                        if reference.source
                        == AssetInspectionReferenceSource.EXPLICIT_CANONICAL
                        else "derivation_reference"
                    ),
                    "reference_source": reference.source.value,
                    "source_project_id": reference.project_id,
                    "asset_id": reference.asset.id,
                    "asset_kind": reference.asset.kind.value,
                    "asset_name": reference.asset.name,
                    "version_id": reference.version.id,
                    "descriptor": reference.version.descriptor,
                    "reference_role": reference.reference_role.value,
                    "reference_authority": reference.reference_authority.value,
                    "purpose": reference.purpose,
                    "subject_alias": reference.subject_alias,
                    "approval_status": reference.version.approval_status.value,
                    "local_plane_composite_source": {
                        "available": reference.version.id in plane_source_sizes,
                        "actual_size": (
                            {"width": plane_source_sizes[reference.version.id][0],
                             "height": plane_source_sizes[reference.version.id][1]}
                            if reference.version.id in plane_source_sizes else None
                        ),
                        "full_image_is_content_plate": reference.version.id in whole_content_plate_ids,
                    },
                    **({"text_plate": reference.version.text_plate.model_dump(mode="json")} if reference.version.text_plate is not None else {}),
                    "is_current_canonical_master": (
                        reference.asset.approved_version_id == reference.version.id
                    ),
                    "normative_for_declared_role": (
                        reference.normative_for_declared_role
                    ),
                }
                for index, (reference, _url) in enumerate(
                    reference_images,
                    start=2,
                )
            ],
            *[
                {"image": len(reference_images) + 2 + index, "role": "DETAIL_OF_EXISTING_IMAGE",
                 **evidence.model_dump(mode="json")}
                for index, evidence in enumerate(detail_evidence)
            ],
        ],
    }
    output = client.generate_text(
        instructions=instructions,
        input_text=json.dumps(payload, ensure_ascii=False, indent=2),
        image_urls=[
            _asset_image_url(version),
            *(url for _reference, url in reference_images),
            *detail_urls,
        ],
        max_output_tokens=16000,
    )
    result = SemanticAssetReview.model_validate_json(_strip_json_fence(output))
    suggestion = result.crop_suggestion
    if suggestion is not None and crop_available and crop_source_size is not None:
        width, height = crop_source_size
        box = region_box(suggestion.region, width, height)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            suggestion = None
        else:
            suggestion = suggestion.model_copy(update={"source": AssetImageCrop(
                source_asset_version_id=version.id, source_width=width, source_height=height,
                box_pixels=box,
            )})
    else:
        suggestion = None
    edit_guide = result.edit_guide_suggestion
    if edit_guide is not None and edit_guide_available and crop_source_size is not None:
        width, height = crop_source_size
        try:
            validate_edit_guide_geometry(edit_guide.regions, edit_guide.arrows, width, height)
        except ValueError:
            edit_guide = None
        else:
            edit_guide = edit_guide.model_copy(update={"source_asset_version_id": version.id})
    else:
        edit_guide = None
    plane = result.plane_composite_suggestion
    if plane is not None and plane_available and plane_base_size is not None:
        plane = plane.model_copy(update={"base_asset_version_id": version.id})
        if any(
            layer.source_asset_version_id not in plane_source_sizes
            or (layer.source_quad is None and layer.source_asset_version_id not in whole_content_plate_ids)
            for layer in plane.layers
        ):
            plane = None
        else:
            try:
                validate_plane_composite_geometry(plane, plane_base_size, plane_source_sizes)
            except ValueError:
                plane = None
    else:
        plane = None
    return result.model_copy(update={
        "review_focus": request.review_focus, "candidate_output_layout": output_layout,
        "region_evidence": detail_evidence, "crop_suggestion": suggestion,
        "edit_guide_suggestion": edit_guide,
        "plane_composite_suggestion": plane,
    })


def _local_plane_image_size(version: AssetVersion) -> tuple[int, int] | None:
    """Only map observed local pixels; never substitute a cache for a remote image."""
    if (
        version.approval_status not in {ApprovalStatus.DRAFT, ApprovalStatus.APPROVED}
        or version.freshness != Freshness.CURRENT
    ):
        return None
    try:
        size = _local_candidate_visible_size(version)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    if size is None or min(size) < 2 or size[0] * size[1] > _ANALYSIS_IMAGE_MAX_SOURCE_PIXELS:
        return None
    return size


def _local_candidate_visible_size(version: AssetVersion) -> tuple[int, int] | None:
    # The visual call uses external_url first. Do not map a remote observation to
    # potentially different cached pixels or invent a local source for it.
    if version.external_url is not None or not version.local_path:
        return None
    with Image.open(version.local_path) as opened:
        width, height = opened.size
        if opened.getexif().get(274) in (5, 6, 7, 8):
            width, height = height, width
        return width, height


def _default_reference_role(kind: AssetKind) -> ImageReferenceRole:
    return {
        AssetKind.CHARACTER: ImageReferenceRole.SUBJECT_IDENTITY,
        AssetKind.LOCATION: ImageReferenceRole.SCENE_IDENTITY,
        AssetKind.PROP: ImageReferenceRole.PROP_IDENTITY,
    }.get(kind, ImageReferenceRole.SUPPORTING_VISUAL)


def _inspection_reference_images(
    references: list[AssetInspectionReference],
) -> list[tuple[AssetInspectionReference, str]]:
    result: list[tuple[AssetInspectionReference, str]] = []
    seen: set[str] = set()
    for reference in references:
        if reference.version.id in seen:
            continue
        url = _asset_image_url(reference.version)
        result.append((reference, url))
        seen.add(reference.version.id)
    return result


def _asset_image_url(version: AssetVersion) -> str:
    if version.external_url is not None:
        return version.external_url
    if version.local_path is None:
        raise ValueError(f"AssetVersion {version.id} has no usable image")
    path = Path(version.local_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"AssetVersion image is missing: {path}")
    encoded = base64.b64encode(_analysis_thumbnail_bytes(path)).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


_ANALYSIS_IMAGE_MAX_EDGE = 2048
_ANALYSIS_IMAGE_MAX_BYTES = 3 * 1024 * 1024
_ANALYSIS_IMAGE_MAX_SOURCE_PIXELS = 50_000_000


def _analysis_thumbnail_bytes(path: Path) -> bytes:
    """Build an EXIF-correct, bounded in-memory review image without changing source."""

    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > _ANALYSIS_IMAGE_MAX_SOURCE_PIXELS:
                raise ValueError(
                    "AssetVersion image is too large for in-process semantic review; "
                    "create a bounded derivative while preserving the original source"
                )
            # JPEG decoders can downsample during decode. Other formats still remain
            # untouched on disk and are reduced immediately after decoding.
            opened.draft("RGB", (_ANALYSIS_IMAGE_MAX_EDGE, _ANALYSIS_IMAGE_MAX_EDGE))
            image = ImageOps.exif_transpose(opened)
            return _encode_review_image(image)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(f"AssetVersion image cannot be decoded: {path}") from exc


def _encode_review_image(source: Image.Image) -> bytes:
    image = source.copy()
    image.thumbnail(
        (_ANALYSIS_IMAGE_MAX_EDGE, _ANALYSIS_IMAGE_MAX_EDGE),
        Image.Resampling.LANCZOS, reducing_gap=3.0,
    )
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        # Mid-grey keeps both white and black text visible during asset inspection.
        flattened = Image.new("RGB", rgba.size, "#808080")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        image = flattened
    elif image.mode != "RGB":
        image = image.convert("RGB")

    quality = 90
    while True:
        buffer = BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        output = buffer.getvalue()
        if len(output) <= _ANALYSIS_IMAGE_MAX_BYTES:
            return output
        if quality > 72:
            quality -= 6
            continue
        next_size = (
            max(1, int(image.width * 0.85)),
            max(1, int(image.height * 0.85)),
        )
        if next_size == image.size:
            raise ValueError("AssetVersion review thumbnail cannot be bounded")
        image = image.resize(next_size, Image.Resampling.LANCZOS)


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
