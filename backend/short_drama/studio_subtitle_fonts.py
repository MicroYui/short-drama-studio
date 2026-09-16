"""Resolve an installed CJK subtitle family shared by preview and local export."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys
import unicodedata

from PIL import ImageFont


def _font_candidates() -> list[tuple[Path, str]]:
    if sys.platform == "darwin":
        paths = [Path("/System/Library/Fonts/PingFang.ttc"), Path("/Library/Fonts/PingFang.ttc")]
        paths.extend(sorted(Path("/System/Library/AssetsV2").glob("com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc")))
        return [(path, "PingFang SC") for path in paths]
    if sys.platform == "win32":
        return [(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc", "Microsoft YaHei")]
    if sys.platform.startswith("linux"):
        return [(Path(path), "Noto Sans CJK SC") for path in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf")]
    return []


def readable_font_family(path: Path, preferred: str) -> str | None:
    try:
        available = path.is_file()
    except OSError:
        return None
    if not available:
        return None
    # TTC face 0 is not necessarily Simplified Chinese (PingFang starts with HK).
    for index in range(32):
        try:
            font = ImageFont.truetype(str(path), 16, index=index)
            family, _style = font.getname()
        except (OSError, ValueError):
            break
        if family and family.casefold() == preferred.casefold():
            return family
    return None


@lru_cache(maxsize=1)
def default_subtitle_font_family() -> str | None:
    for path, preferred in _font_candidates():
        if family := readable_font_family(path, preferred):
            return family
    return None


def resolved_subtitle_family(value: object, default: str | None) -> str:
    family = value.split(",", 1)[0].strip().strip("\"'").strip() if isinstance(value, str) else ""
    if not family or family.casefold() in {"sans-serif", "system-ui"}:
        # With no confirmed family, keep the existing renderer fallback; the
        # response remains null and does not claim preview/export equivalence.
        return default or "Arial"
    return family


@lru_cache(maxsize=8)
def _measurement_font(family: str, bold: bool) -> ImageFont.FreeTypeFont | None:
    fallback: ImageFont.FreeTypeFont | None = None
    for path, preferred in _font_candidates():
        if preferred.casefold() != family.casefold():
            continue
        for index in range(32):
            try:
                font = ImageFont.truetype(str(path), 1000, index=index)
                actual_family, style = font.getname()
            except (OSError, ValueError):
                break
            if not actual_family or actual_family.casefold() != family.casefold():
                continue
            if fallback is None:
                fallback = font
            normalized_style = (style or "").casefold()
            if (bold and "bold" in normalized_style) or (not bold and normalized_style == "regular"):
                return font
    return fallback


def wrap_subtitle_for_export(text: str, family: str, font_size: float, max_width: float, *, bold: bool = False) -> str:
    """Add render-only line breaks for the installed shared subtitle face.

    The bundled libass cannot break unspaced Chinese text. Measure the same
    ascent+descent size as ASS and use the monitor's greedy available width.
    Explicit newlines and unbreakable Latin words remain intact. Unknown user
    fonts keep their existing rendering rather than using another face's widths.
    """
    font = _measurement_font(family, bold)
    if font is None or font_size <= 0 or max_width <= 0:
        return text
    ascent, descent = font.getmetrics()
    scale = font_size / (ascent + descent)
    closing = "，。！？、：；）】》」』”’％%"
    opening = "（【《「『“‘"

    def cjk(value: str) -> bool:
        return unicodedata.name(value, "").startswith(("CJK UNIFIED", "CJK COMPATIBILITY"))

    lines: list[str] = []
    for paragraph in text.split("\n"):
        start = 0
        current = ""
        for end in range(1, len(paragraph) + 1):
            previous = paragraph[end - 1]
            following = paragraph[end] if end < len(paragraph) else ""
            can_break = not following or previous.isspace() or (
                (cjk(previous) or cjk(following)) and previous not in opening
                and following not in closing and not unicodedata.category(following).startswith("M")
            )
            if not can_break:
                continue
            part = paragraph[start:end]
            if current and font.getlength(current + part) * scale > max_width:
                lines.append(current)
                current = part
            else:
                current += part
            start = end
        lines.append(current)
    return "\n".join(lines)
