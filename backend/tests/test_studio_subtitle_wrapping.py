from pathlib import Path

import pytest

from short_drama.studio_editorial import EditorSubtitle, EditorTimeline, EditorTrack
from short_drama.studio_export import _write_subtitles
from short_drama.studio_subtitle_fonts import default_subtitle_font_family, wrap_subtitle_for_export


def _family() -> str:
    family = default_subtitle_font_family()
    if family is None:
        pytest.skip("No confirmed local subtitle face on this platform")
    return family


def test_long_chinese_wraps_for_available_width_without_losing_text() -> None:
    family = _family()
    text = "今晚的风很轻我们一起把这封信交给下一位旅人"
    wrapped = wrap_subtitle_for_export(text, family, 56 * 1280 / 1080, 720 * .9)
    assert len(wrapped.splitlines()) == 2
    assert wrapped.replace("\n", "") == text
    if family == "PingFang SC":
        assert wrapped == "今晚的风很轻我们一起把这封\n信交给下一位旅人"


def test_explicit_newlines_and_short_default_text_remain_exact() -> None:
    family = _family()
    for text in ("第一段：原声降低一半", "Sound stays in sync", "镜头02 Audio 50%", "第一段：原声降低一半\n\n第二段：我们继续前行"):
        assert wrap_subtitle_for_export(text, family, 56 * 1280 / 1080, 720 * .9) == text
    words = "Every handover keeps the original words together even on a narrow screen"
    wrapped = wrap_subtitle_for_export(words, family, 56 * 1280 / 1080, 720 * .9)
    assert "\n" in wrapped
    assert wrapped.split() == words.split()
    assert wrap_subtitle_for_export("unbreakableword", family, 100, 1) == "unbreakableword"


def test_unconfirmed_explicit_font_does_not_use_default_face_measurement() -> None:
    text = "显式字体没有确认时不借另一种字体擅自断行"
    assert wrap_subtitle_for_export(text, "Missing custom family 999", 100, 50) == text


def test_ass_wrap_is_render_only_and_srt_retains_original_text(tmp_path: Path) -> None:
    _family()
    text = "今晚的风很轻我们一起把这封信交给下一位旅人"
    timeline = EditorTimeline(id="local", name="Local", tracks=[EditorTrack(id="sub", name="字幕", type="subtitle")], clips=[],
        subtitles=[EditorSubtitle(id="cue", text=text, startTime=0, endTime=1, trackIndex=0, style={})])
    before = timeline.model_dump_json()
    srt, ass = _write_subtitles(timeline, tmp_path, 720, 1280)
    assert timeline.model_dump_json() == before
    assert text in srt.read_text()
    assert "\\N" in ass.read_text()


@pytest.mark.parametrize("solo", [False, True])
def test_srt_is_chronological_without_changing_caption_render_or_saved_order(tmp_path: Path, solo: bool) -> None:
    timeline = EditorTimeline(id="film", name="Film", clips=[], tracks=[
        EditorTrack(id="dialogue", name="对白", type="subtitle", solo=solo),
        EditorTrack(id="hidden", name="隐藏", type="subtitle", enabled=False),
        EditorTrack(id="other", name="其他", type="subtitle"),
    ], subtitles=[
        EditorSubtitle(id="late", text="后来保存的末句", startTime=8, endTime=9, trackIndex=0, style={}),
        EditorSubtitle(id="early-a", text="先出现的第一行", startTime=1.25, endTime=3, trackIndex=0, style={}),
        EditorSubtitle(id="hidden", text="不应导出", startTime=0, endTime=1, trackIndex=1, style={}),
        EditorSubtitle(id="early-b", text="同时出现的第二行", startTime=1.25, endTime=2, trackIndex=0, style={}),
        EditorSubtitle(id="other", text="另一轨字幕", startTime=4, endTime=5, trackIndex=2, style={}),
    ])
    before = timeline.model_dump_json()
    srt, ass = _write_subtitles(timeline, tmp_path, 1280, 720)
    blocks = [block.splitlines() for block in srt.read_text().strip().split("\n\n")]
    expected = ["先出现的第一行", "同时出现的第二行"]
    if not solo:
        expected.append("另一轨字幕")
    expected.append("后来保存的末句")
    assert [block[2] for block in blocks] == expected
    assert [block[0] for block in blocks] == [str(index) for index in range(1, len(expected) + 1)]
    assert blocks[0][1] == "00:00:01,250 --> 00:00:03,000"
    rendered = [line for line in ass.read_text().splitlines() if line.startswith("Dialogue:")]
    assert "后来保存的末句" in rendered[0]
    assert "先出现的第一行" in rendered[1]
    assert "同时出现的第二行" in rendered[2]
    assert len(rendered) == len(expected)
    assert "不应导出" not in srt.read_text() + ass.read_text()
    assert timeline.model_dump_json() == before
