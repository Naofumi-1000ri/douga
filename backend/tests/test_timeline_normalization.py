import pytest

from src.render.timeline_normalization import (
    normalize_embedded_export_timeline,
    normalize_export_timeline,
)


def test_normalize_export_timeline_clamps_requested_range() -> None:
    timeline, render_duration_ms = normalize_export_timeline(
        {"duration_ms": 2000},
        2000,
        start_ms=-120,
        end_ms=2400,
    )

    assert render_duration_ms == 2000
    assert timeline["duration_ms"] == 2000
    assert timeline["export_start_ms"] == 0
    assert timeline["export_end_ms"] == 2000


def test_normalize_embedded_export_timeline_preserves_partial_export_metadata() -> None:
    timeline, render_duration_ms = normalize_embedded_export_timeline(
        {
            "duration_ms": 1200,
            "export_start_ms": 200,
            "export_end_ms": 1400,
        }
    )

    assert render_duration_ms == 1200
    assert timeline["duration_ms"] == 1200
    assert timeline["export_start_ms"] == 200
    assert timeline["export_end_ms"] == 1400


def test_normalize_export_timeline_rejects_invalid_range() -> None:
    with pytest.raises(ValueError, match="Invalid export range"):
        normalize_export_timeline({"duration_ms": 500}, 500, start_ms=300, end_ms=300)
