"""Current method names and read compatibility for existing project records.

The catalog exposes purpose-based names. Historical trace files keep their
original identifiers; loading a project or run resolves those identifiers here.
"""

from collections.abc import Iterable
from typing import Annotated, Final

from pydantic import AfterValidator


LEGACY_METHOD_SKILL_IDS: Final[dict[str, str]] = {
    "method.minimax_design.drama_series": "method.drama_series",
    "method.minimax_design.ad_tvc": "method.advertising",
    "method.minimax_design.mv": "method.music_video",
    "method.minimax_h3.3d_animation_short": "method.animation_short",
    "method.minimax_h3.brand_promo_video": "method.brand_promo",
    "method.minimax_h3.co_op_game_intro": "method.co_op_game_intro",
    "method.minimax_h3.handdrawn_live_video": "method.handdrawn_live",
    "method.minimax_h3.minimalist_product_ad": "method.minimalist_product_ad",
    "method.minimax_h3.music_video_subtitle": "method.lyric_video",
    "method.minimax_h3.paper_collage_explainer": "method.paper_collage_explainer",
    "method.minimax_h3.papercraft_stop_motion_explainer": "method.papercraft_stop_motion",
}


def canonical_method_skill_id(skill_id: str) -> str:
    """Resolve only known old names; unknown IDs still fail normal validation."""
    return LEGACY_METHOD_SKILL_IDS.get(skill_id, skill_id)


def canonical_skill_ids(skill_ids: Iterable[str]) -> list[str]:
    # Preserve order and duplicates so each existing DTO owns its validation.
    return [canonical_method_skill_id(skill_id) for skill_id in skill_ids]


SkillIds = Annotated[list[str], AfterValidator(canonical_skill_ids)]
