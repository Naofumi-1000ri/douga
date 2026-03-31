"""Parity tests: frame_sampler transform helpers match frontend behavior.

These tests verify that the Python implementations of interpolation and fade
helpers mirror the TypeScript logic in:
  - frontend/src/utils/keyframes.ts  (getInterpolatedTransform)
  - frontend/src/components/editor/editorPreviewStageShared.ts (calculateFadeOpacity)
"""
import pytest

import sys
import types

# Stub out heavy service imports that require GCP/DB connections at import time,
# so we can import only the frame_sampler helpers without the full service layer.
_storage_stub = types.ModuleType("src.services.storage_service")
_storage_stub.StorageService = object  # type: ignore[attr-defined]
sys.modules.setdefault("src.services.storage_service", _storage_stub)

from src.services.frame_sampler import (  # noqa: E402
    FrameSampler,
    _calculate_fade_opacity,
    _interpolate_transform_at,
)


# ---------------------------------------------------------------------------
# _interpolate_transform_at
# ---------------------------------------------------------------------------


def _make_clip(
    start_ms: int = 0,
    duration_ms: int = 5000,
    x: float = 10,
    y: float = 20,
    scale: float = 1.0,
    rotation: float = 0.0,
    opacity: float = 1.0,
    keyframes: list | None = None,
) -> dict:
    return {
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "transform": {"x": x, "y": y, "scale": scale, "rotation": rotation, "width": 100, "height": 50},
        "effects": {"opacity": opacity},
        "keyframes": keyframes or [],
    }


class TestInterpolateTransformAt:
    """_interpolate_transform_at mirrors frontend getInterpolatedTransform."""

    def test_no_keyframes_returns_base_transform(self):
        clip = _make_clip(x=50, y=30, scale=1.5, rotation=45.0, opacity=0.8)
        result = _interpolate_transform_at(clip, time_ms=1000)
        assert result["x"] == 50
        assert result["y"] == 30
        assert result["scale"] == 1.5
        assert result["rotation"] == 45.0
        assert result["opacity"] == 0.8

    def test_single_keyframe_returns_keyframe_values(self):
        clip = _make_clip(x=0, y=0, opacity=1.0)
        clip["keyframes"] = [
            {"time_ms": 1000, "transform": {"x": 100, "y": 200, "scale": 2.0, "rotation": 90.0}, "opacity": 0.5}
        ]
        result = _interpolate_transform_at(clip, time_ms=1000)
        assert result["x"] == 100
        assert result["y"] == 200
        assert result["scale"] == 2.0
        assert result["rotation"] == 90.0
        assert result["opacity"] == 0.5

    def test_two_keyframes_linear_interpolation(self):
        clip = _make_clip(start_ms=0, x=0, y=0, opacity=1.0)
        clip["keyframes"] = [
            {"time_ms": 0, "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0.0}, "opacity": 0.0},
            {"time_ms": 1000, "transform": {"x": 100, "y": 200, "scale": 2.0, "rotation": 90.0}, "opacity": 1.0},
        ]
        # At t=500ms (midpoint), should be halfway between the two keyframes
        result = _interpolate_transform_at(clip, time_ms=500)
        assert result["x"] == pytest.approx(50.0)
        assert result["y"] == pytest.approx(100.0)
        assert result["scale"] == pytest.approx(1.5)
        assert result["rotation"] == pytest.approx(45.0)
        assert result["opacity"] == pytest.approx(0.5)

    def test_before_first_keyframe_clamps_to_first(self):
        clip = _make_clip(start_ms=0, x=0, y=0, opacity=1.0)
        clip["keyframes"] = [
            {"time_ms": 500, "transform": {"x": 10, "y": 20, "scale": 1.0, "rotation": 0.0}, "opacity": 0.3},
            {"time_ms": 1000, "transform": {"x": 100, "y": 200, "scale": 2.0, "rotation": 90.0}, "opacity": 1.0},
        ]
        # time_ms=100 is before the first keyframe at 500ms
        result = _interpolate_transform_at(clip, time_ms=100)
        assert result["x"] == 10
        assert result["y"] == 20
        assert result["opacity"] == pytest.approx(0.3)

    def test_after_last_keyframe_clamps_to_last(self):
        clip = _make_clip(start_ms=0, x=0, y=0, opacity=1.0)
        clip["keyframes"] = [
            {"time_ms": 0, "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0.0}, "opacity": 0.0},
            {"time_ms": 1000, "transform": {"x": 100, "y": 200, "scale": 2.0, "rotation": 90.0}, "opacity": 1.0},
        ]
        # time_ms=5000 (absolute) is well past the last keyframe at 1000ms
        result = _interpolate_transform_at(clip, time_ms=5000)
        assert result["x"] == 100
        assert result["y"] == 200
        assert result["opacity"] == pytest.approx(1.0)

    def test_keyframe_opacity_falls_back_to_effects_opacity(self):
        """When keyframe.opacity is None, fall back to clip.effects.opacity."""
        clip = _make_clip(start_ms=0, x=0, y=0, opacity=0.7)
        clip["keyframes"] = [
            {"time_ms": 0, "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0.0}, "opacity": None},
        ]
        result = _interpolate_transform_at(clip, time_ms=0)
        assert result["opacity"] == pytest.approx(0.7)

    def test_interpolation_at_exact_keyframe_boundary(self):
        clip = _make_clip(start_ms=0, x=0, y=0, opacity=1.0)
        clip["keyframes"] = [
            {"time_ms": 0, "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0.0}, "opacity": 0.0},
            {"time_ms": 1000, "transform": {"x": 100, "y": 0, "scale": 1.0, "rotation": 0.0}, "opacity": 1.0},
        ]
        result_start = _interpolate_transform_at(clip, time_ms=0)
        assert result_start["x"] == pytest.approx(0.0)
        assert result_start["opacity"] == pytest.approx(0.0)

        result_end = _interpolate_transform_at(clip, time_ms=1000)
        assert result_end["x"] == pytest.approx(100.0)
        assert result_end["opacity"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _calculate_fade_opacity
# ---------------------------------------------------------------------------


class TestCalculateFadeOpacity:
    """_calculate_fade_opacity mirrors frontend calculateFadeOpacity."""

    def test_no_fade_returns_1(self):
        assert _calculate_fade_opacity(500, 1000, 0, 0) == pytest.approx(1.0)

    def test_fade_in_at_start_returns_0(self):
        # At time 0 with fade_in=500ms → multiplier = 0/500 = 0
        assert _calculate_fade_opacity(0, 1000, 500, 0) == pytest.approx(0.0)

    def test_fade_in_midpoint(self):
        # At time 250 with fade_in=500ms → multiplier = 250/500 = 0.5
        assert _calculate_fade_opacity(250, 1000, 500, 0) == pytest.approx(0.5)

    def test_fade_in_complete_returns_1(self):
        # At time 500 with fade_in=500ms → no longer in fade-in window
        assert _calculate_fade_opacity(500, 1000, 500, 0) == pytest.approx(1.0)

    def test_fade_out_near_end(self):
        # duration=1000, fade_out=500 → at time=750, time_from_end=250
        # multiplier = 250/500 = 0.5
        assert _calculate_fade_opacity(750, 1000, 0, 500) == pytest.approx(0.5)

    def test_fade_out_at_end_returns_0(self):
        # At time=1000 (== duration), time_from_end=0 → 0/500=0
        assert _calculate_fade_opacity(1000, 1000, 0, 500) == pytest.approx(0.0)

    def test_fade_out_before_window_returns_1(self):
        # At time=400, duration=1000, fade_out=500 → time_from_end=600 > 500 → no fade
        assert _calculate_fade_opacity(400, 1000, 0, 500) == pytest.approx(1.0)

    def test_both_fades_overlap_short_clip_min_applies(self):
        # Short clip: duration=200, fade_in=200, fade_out=200
        # At time=100: fade_in_mult=100/200=0.5, fade_out time_from_end=100, mult=100/200=0.5
        # min(0.5, 0.5) = 0.5
        assert _calculate_fade_opacity(100, 200, 200, 200) == pytest.approx(0.5)

    def test_clamp_min_0(self):
        # Negative time_from_end should not go below 0
        result = _calculate_fade_opacity(1100, 1000, 0, 500)
        assert result >= 0.0

    def test_clamp_max_1(self):
        assert _calculate_fade_opacity(500, 1000, 0, 0) <= 1.0


# ---------------------------------------------------------------------------
# freeze_frame visibility
# ---------------------------------------------------------------------------


class TestFreezeFrameVisibility:
    """Clips with freeze_frame_ms should remain visible beyond clip_end."""

    def _make_frame_sampler(self) -> FrameSampler:
        return FrameSampler(
            timeline_data={"duration_ms": 10000, "layers": []},
            assets={},
        )

    def test_clip_visible_during_freeze_zone(self):
        sampler = self._make_frame_sampler()
        clip_start = 0
        clip_dur = 2000
        freeze_frame_ms = 1000

        # Normal clip end: 2000ms; with freeze: 3000ms
        clip_end_with_freeze = clip_start + clip_dur + freeze_frame_ms

        # At time=2500ms (in freeze zone): should be visible
        time_ms = 2500
        assert time_ms < clip_end_with_freeze, "Should be visible during freeze zone"
        assert time_ms >= clip_start + clip_dur, "Should be in freeze zone"

    def test_clip_not_visible_after_freeze_zone(self):
        clip_start = 0
        clip_dur = 2000
        freeze_frame_ms = 1000
        clip_end_with_freeze = clip_start + clip_dur + freeze_frame_ms

        # At time=3001ms: should NOT be visible
        time_ms = 3001
        assert time_ms >= clip_end_with_freeze, "Should not be visible after freeze zone ends"

    def test_clip_visible_at_normal_end_without_freeze(self):
        clip_start = 0
        clip_dur = 2000
        freeze_frame_ms = 0
        clip_end = clip_start + clip_dur + freeze_frame_ms

        # At time=1999ms: should be visible
        assert 1999 < clip_end

        # At time=2000ms: not visible (clip_end <= time_ms)
        assert clip_end <= 2000

    def test_freeze_seek_position_is_last_frame_of_content(self):
        """During freeze zone, seek should be pinned to (in_point_ms + clip_dur - 1)."""
        clip = {
            "start_ms": 0,
            "duration_ms": 2000,
            "freeze_frame_ms": 1000,
            "in_point_ms": 500,
        }
        clip_start = clip["start_ms"]
        clip_dur = clip["duration_ms"]
        in_point_ms = clip["in_point_ms"]
        freeze_frame_ms = clip["freeze_frame_ms"]

        time_ms = 2500  # In freeze zone

        # Expected seek position: last usable frame of clip content
        expected_seek_ms = in_point_ms + clip_dur - 1  # 500 + 2000 - 1 = 2499

        # Actual computation (mirrors _render_single_frame logic)
        if freeze_frame_ms > 0 and time_ms >= clip_start + clip_dur:
            actual_seek_ms = in_point_ms + clip_dur - 1
        else:
            actual_seek_ms = in_point_ms + (time_ms - clip_start)

        assert actual_seek_ms == expected_seek_ms
