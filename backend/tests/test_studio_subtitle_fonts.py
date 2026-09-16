"""Confirmed installed subtitle families remain response metadata, not authored styles."""
from pathlib import Path
import sys

from PIL import ImageFont
import pytest

from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorSubtitle, EditorTimeline, EditorTrack, read_editorial
from short_drama.studio_export import _write_subtitles
from short_drama.studio_shot_editorial import read_shot_editorial
from short_drama.studio_subtitle_fonts import default_subtitle_font_family, readable_font_family, resolved_subtitle_family
from tests.test_studio_shot_editorial import episode as episode


def test_missing_or_unreadable_font_is_not_reported_as_available(tmp_path: Path) -> None:
    assert readable_font_family(tmp_path / "missing.ttc", "PingFang SC") is None
    invalid = tmp_path / "invalid.ttc"
    invalid.write_text("not a font")
    assert readable_font_family(invalid, "PingFang SC") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="Checks the actual macOS TTC faces")
def test_actual_pingfang_simplified_chinese_face_is_selected_instead_of_first_hk_face() -> None:
    candidates = [Path("/System/Library/Fonts/PingFang.ttc"),
        *Path("/System/Library/AssetsV2").glob("com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc")]
    existing = next((path for path in candidates if path.is_file()), None)
    if existing is None:
        pytest.skip("This macOS environment does not have readable PingFang resources")
    first_family = ImageFont.truetype(str(existing), 16, index=0).getname()[0]
    assert first_family in {"PingFang HK", "PingFang SC", "PingFang TC"}
    assert readable_font_family(existing, "not-an-installed-family") is None
    assert readable_font_family(existing, "PingFang SC") == default_subtitle_font_family() == "PingFang SC"


def test_film_and_shot_metadata_leave_saved_document_and_styles_untouched(
    episode: tuple[ShortDramaService, str, list[str]],
) -> None:
    service, project_id, shots = episode
    before = service.get_project(project_id).document
    film = read_editorial(service, project_id)
    shot = read_shot_editorial(service, project_id, shots[0])
    assert film.default_subtitle_font_family == shot.default_subtitle_font_family == default_subtitle_font_family()
    assert "default_subtitle_font_family" not in film.project and "default_subtitle_font_family" not in shot.project
    assert service.get_project(project_id).document == before


def test_subtitle_export_uses_confirmed_default_but_preserves_explicit_family(tmp_path: Path) -> None:
    family = default_subtitle_font_family()
    expected = family or "Arial"
    track = EditorTrack(id="subtitles", name="字幕", type="subtitle")
    timeline = EditorTimeline(id="film", name="成片", tracks=[track], clips=[], subtitles=[
        EditorSubtitle(id="default", text="未设置", startTime=0, endTime=1, trackIndex=0),
        EditorSubtitle(id="generic", text="通用", startTime=1, endTime=2, trackIndex=0, style={"fontFamily": " sans-serif "}),
        EditorSubtitle(id="system", text="系统", startTime=2, endTime=3, trackIndex=0, style={"fontFamily": "'system-ui'"}),
        EditorSubtitle(id="explicit", text="保持字体", startTime=3, endTime=4, trackIndex=0, style={"fontFamily": "Courier New"}),
    ])
    before = timeline.model_dump(mode="json")
    _srt, ass = _write_subtitles(timeline, tmp_path, 1080, 1920)
    value = ass.read_text()
    assert f"Style: Default,{expected}," in value
    dialogue = [line for line in value.splitlines() if line.startswith("Dialogue:")]
    assert all(f"\\fn{expected}\\" in line for line in dialogue[:3])
    assert "\\fnCourier New\\" in dialogue[3]
    assert timeline.model_dump(mode="json") == before
    assert resolved_subtitle_family("  ", family) == expected
    assert resolved_subtitle_family("'Arial', sans-serif", family) == "Arial"
