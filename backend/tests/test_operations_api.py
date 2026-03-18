from types import SimpleNamespace

import pytest

from src.api import operations as operations_api
from src.schemas.operations_api import OperationItem


def _build_project() -> SimpleNamespace:
    return SimpleNamespace(
        timeline_data={
            "layers": [
                {
                    "id": "layer-text",
                    "name": "Text",
                    "type": "text",
                    "visible": True,
                    "locked": False,
                    "clips": [
                        {
                            "id": "clip-text-1",
                            "asset_id": None,
                            "text_content": "元のテロップ",
                            "text_style": {
                                "fontFamily": "Noto Sans JP",
                                "fontSize": 48,
                                "fontWeight": "bold",
                                "fontStyle": "normal",
                                "color": "#ffffff",
                                "backgroundColor": "#223344",
                                "backgroundOpacity": 0.25,
                                "textAlign": "center",
                                "verticalAlign": "middle",
                                "lineHeight": 1.4,
                                "letterSpacing": 0,
                                "strokeColor": "#000000",
                                "strokeWidth": 2,
                            },
                            "start_ms": 0,
                            "duration_ms": 4000,
                            "in_point_ms": 0,
                            "out_point_ms": None,
                            "transform": {"x": 0, "y": 320, "scale": 1, "rotation": 0},
                            "effects": {"opacity": 1},
                        }
                    ],
                }
            ],
            "audio_tracks": [],
        },
        duration_ms=4000,
    )


@pytest.mark.asyncio
async def test_dispatch_operation_normalizes_added_text_clip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(operations_api, "flag_modified", lambda *_args, **_kwargs: None)
    project = _build_project()

    op = OperationItem(
        type="clip.add",
        layer_id="layer-text",
        data={
            "clip": {
                "id": "clip-text-2",
                "asset_id": None,
                "text_content": "分割後テロップ",
                "text_style": {"font_size": 64},
                "start_ms": 4000,
                "duration_ms": 2000,
                "in_point_ms": 0,
                "out_point_ms": None,
                "transform": {"x": 0, "y": 320, "scale": 1, "rotation": 0},
                "effects": {"opacity": 1},
            }
        },
    )

    await operations_api._dispatch_operation(SimpleNamespace(), project, op)

    added_clip = project.timeline_data["layers"][0]["clips"][-1]
    assert added_clip["text_style"]["fontSize"] == 64
    assert added_clip["text_style"]["backgroundColor"] == "#000000"
    assert added_clip["text_style"]["strokeWidth"] == 2
    assert "font_size" not in added_clip["text_style"]


@pytest.mark.asyncio
async def test_dispatch_operation_merges_partial_text_style(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(operations_api, "flag_modified", lambda *_args, **_kwargs: None)
    project = _build_project()

    op = OperationItem(
        type="clip.text_style",
        clip_id="clip-text-1",
        data={"text_style": {"font_size": 72, "color": "#ffeeaa"}},
    )

    await operations_api._dispatch_operation(SimpleNamespace(), project, op)

    updated_style = project.timeline_data["layers"][0]["clips"][0]["text_style"]
    assert updated_style["fontSize"] == 72
    assert updated_style["color"] == "#ffeeaa"
    assert updated_style["backgroundColor"] == "#223344"
    assert updated_style["backgroundOpacity"] == 0.25
    assert "font_size" not in updated_style
