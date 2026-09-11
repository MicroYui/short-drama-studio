"""AceData H3 output options from its public videos API contract."""

from short_drama.models import ProjectDocument, VideoGenerationRoute

H3_RESOLUTIONS = ("768P", "2K")
H3_ASPECT_RATIOS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
H3_FRAME_ROUTES = frozenset({VideoGenerationRoute.FRAME_FIRST, VideoGenerationRoute.FRAME_LAST,
                            VideoGenerationRoute.FRAME_FIRST_LAST})


def resolve_h3_output_format(*, route: VideoGenerationRoute, resolution: str = "768P",
                             aspect_ratio: str | None = None,
                             default_aspect_ratio: str = "9:16") -> tuple[str, str | None]:
    if resolution not in H3_RESOLUTIONS:
        raise ValueError(f"AceData H3 supports resolution {', '.join(H3_RESOLUTIONS)}; got {resolution!r}")
    ratio = aspect_ratio if route in H3_FRAME_ROUTES else aspect_ratio or default_aspect_ratio
    if ratio is not None and ratio not in H3_ASPECT_RATIOS:
        raise ValueError(f"Unsupported AceData H3 aspect ratio {ratio!r}; supported: {', '.join(H3_ASPECT_RATIOS)}")
    if route in H3_FRAME_ROUTES and ratio not in {None, "adaptive"}:
        raise ValueError("AceData H3 frame routes require omitted/adaptive ratio; prepare a frame with the intended shape")
    if route == VideoGenerationRoute.DIRECT and ratio == "adaptive":
        raise ValueError("Text-only H3 generation requires an explicit fixed aspect ratio")
    return resolution, ratio


def h3_prompt_document(document: ProjectDocument, *, route: VideoGenerationRoute,
                       aspect_ratio: str | None = None) -> ProjectDocument:
    """A rendering view, never a write to the project's intended delivery shape."""
    _resolution, ratio = resolve_h3_output_format(route=route, aspect_ratio=aspect_ratio,
        default_aspect_ratio="9:16" if route == VideoGenerationRoute.DIRECT and document.project.aspect_ratio == "adaptive" else document.project.aspect_ratio)
    return document.model_copy(update={"project": document.project.model_copy(update={"aspect_ratio": ratio or "adaptive"})})
