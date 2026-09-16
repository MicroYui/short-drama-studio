"""Small supported title/transition surface shared with the desktop export adapter."""

from __future__ import annotations

from math import isfinite
from typing import cast

from short_drama.studio_editorial import EditorClip


def _number(value: object, minimum: float, maximum: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and minimum <= value <= maximum


def _transition(clip: EditorClip, edge: str) -> dict[str, object]:
    value: object = (clip.model_extra or {}).get(edge, {})
    if not isinstance(value, dict):
        raise ValueError(f"片段 {clip.id} 的转场格式无效。")
    return cast(dict[str, object], value)


def validate_visual_export(clips: list[EditorClip]) -> None:
    media = [clip for clip in clips if clip.type in {"video", "image"}]
    for clip in clips:
        if clip.type == "adjustment":
            raise ValueError("调整层尚未接通导出，请移除调整层后导出。")
        if clip.type == "text":
            style: object = (clip.model_extra or {}).get("textStyle")
            if not isinstance(style, dict):
                raise ValueError("标题缺少正文或样式。")
            values = cast(dict[str, object], style)
            if isinstance(values.get("text"), str) and any(char in cast(str, values["text"]) for char in "{}\\"):
                raise ValueError("标题暂不支持花括号或反斜杠，请使用常规标点后导出。")
            supported = {"fontFamily": "Arial", "fontStyle": "normal", "textAlign": "center", "backgroundColor": "transparent",
                "strokeWidth": 0, "shadowBlur": 0, "shadowOffsetX": 0, "shadowOffsetY": 0,
                "letterSpacing": 0, "lineHeight": 1.2, "maxWidth": 0, "padding": 0, "borderRadius": 0}
            if any(values.get(key) != value for key, value in supported.items()):
                raise ValueError("标题包含尚未接通的样式，请在标题属性中恢复基本样式。")
            color = values.get("color")
            if (not isinstance(values.get("text"), str) or values.get("fontWeight") not in ("normal", "bold")
                    or not isinstance(color, str) or len(color) != 7 or not color.startswith("#")
                    or any(char not in "0123456789abcdefABCDEF" for char in color[1:])):
                raise ValueError("标题正文、字重或颜色无效。")
            if any(not _number(values.get(key), minimum, maximum) for key, minimum, maximum in (
                    ("fontSize", 1, 500), ("positionX", 0, 100), ("positionY", 0, 100), ("opacity", 0, 100))):
                raise ValueError("标题字号、位置或不透明度超出可用范围。")
        for edge in ("transitionIn", "transitionOut"):
            transition = _transition(clip, edge)
            kind = transition.get("type", "none")
            if kind == "none":
                continue
            if clip.type not in {"video", "image"} or kind not in ("fade-to-black", "dissolve"):
                raise ValueError("当前导出只支持画面淡入黑场、淡出黑场与相邻片段溶解。")
            if not _number(transition.get("duration"), 0.000001, clip.duration):
                raise ValueError("转场时长须大于 0，且不超过片段时长。")
        transition_in, transition_out = _transition(clip, "transitionIn"), _transition(clip, "transitionOut")
        incoming = float(cast(float, transition_in.get("duration", 0))) if transition_in.get("type") == "fade-to-black" else 0
        outgoing = float(cast(float, transition_out.get("duration", 0))) if transition_out.get("type", "none") != "none" else 0
        if incoming + outgoing > clip.duration + 1e-7:
            raise ValueError("片段的入场和出场转场不能重叠，请缩短转场时长。")
        if transition_in.get("type") == "dissolve":
            previous = next((item for item in media if item.id != clip.id and item.trackIndex == clip.trackIndex
                and abs(item.startTime + item.duration - clip.startTime) < 0.001
                and _transition(item, "transitionOut").get("type") == "dissolve"), None)
            duration = float(cast(float, transition_in["duration"]))
            if previous is None or abs(float(cast(float, _transition(previous, "transitionOut")["duration"])) - duration) > 1e-7:
                raise ValueError("溶解需要同轨相邻的两个片段，并使用相同的转场时长。")
            if any(item.id not in {clip.id, previous.id} and item.startTime < clip.startTime
                    and item.startTime + item.duration > clip.startTime - duration for item in media):
                raise ValueError("基本溶解区间同时覆盖了其他画面片段，请移开重叠画面或移除溶解。")
        if transition_out.get("type") == "dissolve" and not any(item.id != clip.id and item.trackIndex == clip.trackIndex
                and abs(item.startTime - clip.startTime - clip.duration) < 0.001
                and _transition(item, "transitionIn").get("type") == "dissolve" for item in media):
            raise ValueError("片段末尾的溶解没有匹配的下一片段。")
