"""Tests for AI video production features.

Tests asset classification, schema validation, and plan-to-timeline conversion.
These are pure unit tests — no database or external services required.
"""

import pytest

from src.services.asset_classifier import ClassificationResult, classify_asset
from src.schemas.ai_video import (
    VideoBrief,
    VideoPlan,
    PlanSection,
    PlanElement,
    PlanAudioElement,
    ElementTransform,
    ElementEffects,
    TextStylePlan,
    AssetCatalogResponse,
    AssetCatalogEntry,
    AssetCatalogSummary,
)
from src.services.plan_to_timeline import plan_to_timeline


# =============================================================================
# Asset Classifier Tests
# =============================================================================


class TestAssetClassifier:
    """Test automatic asset classification."""

    def test_classify_avatar_by_filename(self):
        result = classify_asset("講師アバター.mp4", "video/mp4")
        assert result.type == "video"
        assert result.subtype == "avatar"
        assert result.confidence >= 0.7

    def test_classify_avatar_greenscreen(self):
        result = classify_asset("greenscreen_character.mp4", "video/mp4")
        assert result.subtype == "avatar"

    def test_classify_background_by_filename(self):
        result = classify_asset("bg_space.jpg", "image/jpeg")
        assert result.type == "image"
        assert result.subtype == "background"

    def test_classify_slide_by_filename(self):
        result = classify_asset("slide_変数の基礎.png", "image/png")
        assert result.type == "image"
        assert result.subtype == "slide"

    def test_classify_narration_by_filename(self):
        result = classify_asset("narration_intro.wav", "audio/wav")
        assert result.type == "audio"
        assert result.subtype == "narration"

    def test_classify_bgm_by_filename(self):
        result = classify_asset("bgm_calm.mp3", "audio/mpeg")
        assert result.type == "audio"
        assert result.subtype == "bgm"

    def test_classify_se_by_filename(self):
        result = classify_asset("se_ding.wav", "audio/wav")
        assert result.type == "audio"
        assert result.subtype == "se"

    def test_classify_screen_by_filename(self):
        result = classify_asset("screen_unity操作.mp4", "video/mp4")
        assert result.type == "video"
        assert result.subtype == "screen"

    def test_classify_video_no_audio_metadata(self):
        """Video without audio -> avatar (by metadata)."""
        result = classify_asset("unknown.mp4", "video/mp4", has_audio=False)
        assert result.type == "video"
        assert result.subtype == "avatar"

    def test_classify_video_with_audio_metadata(self):
        """Video with audio -> screen (by metadata)."""
        result = classify_asset("unknown.mp4", "video/mp4", has_audio=True)
        assert result.type == "video"
        assert result.subtype == "screen"

    def test_classify_short_audio_as_se(self):
        """Audio < 5 seconds -> SE."""
        result = classify_asset("click.mp3", "audio/mpeg", duration_ms=2000)
        assert result.subtype == "se"

    def test_classify_long_audio_as_bgm(self):
        """Audio > 60 seconds -> BGM."""
        result = classify_asset("music.mp3", "audio/mpeg", duration_ms=120000)
        assert result.subtype == "bgm"

    def test_classify_medium_audio_as_narration(self):
        """Audio 5-60 seconds -> narration."""
        result = classify_asset("speech.wav", "audio/wav", duration_ms=30000)
        assert result.subtype == "narration"

    def test_classify_wide_image_as_slide(self):
        """Wide aspect ratio image -> slide."""
        result = classify_asset("presentation.png", "image/png", width=1920, height=1080)
        assert result.subtype == "slide"

    def test_incompatible_type_subtype_skipped(self):
        """Image file shouldn't classify as narration even if filename matches."""
        result = classify_asset("narration.png", "image/png")
        # narration is not compatible with image, should fall through
        assert result.type == "image"
        assert result.subtype != "narration"

    def test_japanese_filename_avatar(self):
        result = classify_asset("キャラクター動画.mp4", "video/mp4")
        assert result.subtype == "avatar"

    def test_japanese_filename_effect(self):
        result = classify_asset("エフェクト_キラキラ.mp4", "video/mp4")
        assert result.subtype == "effect"


# =============================================================================
# Schema Tests
# =============================================================================


class TestSchemas:
    """Test Pydantic schema validation."""

    def test_video_brief_defaults(self):
        brief = VideoBrief(title="Test")
        assert brief.style == "tutorial"
        assert brief.target_duration_seconds == 300
        assert brief.language == "ja"
        assert brief.preferences.use_avatar is True

    def test_video_brief_full(self):
        brief = VideoBrief(
            title="Unity入門",
            description="テスト動画",
            style="lecture",
            target_duration_seconds=600,
            sections=[
                {
                    "type": "intro",
                    "title": "イントロ",
                    "description": "挨拶",
                    "estimated_duration_seconds": 15,
                    "assets_hint": ["avatar"],
                }
            ],
        )
        assert len(brief.sections) == 1
        assert brief.sections[0].type == "intro"

    def test_video_plan_validation(self):
        plan = VideoPlan(
            version="1.0",
            total_duration_ms=15000,
            status="draft",
            sections=[
                PlanSection(
                    id="sec_001",
                    type="intro",
                    title="イントロ",
                    layout="avatar_fullscreen",
                    start_ms=0,
                    duration_ms=15000,
                    elements=[
                        PlanElement(
                            id="elem_001",
                            layer="background",
                            asset_id="some-uuid",
                            start_ms=0,
                            duration_ms=15000,
                        )
                    ],
                    audio=[
                        PlanAudioElement(
                            id="aud_001",
                            track="bgm",
                            asset_id="bgm-uuid",
                            start_ms=0,
                            duration_ms=15000,
                            volume=0.3,
                        )
                    ],
                )
            ],
        )
        assert len(plan.sections) == 1
        assert plan.sections[0].elements[0].layer == "background"


# =============================================================================
# Plan to Timeline Conversion Tests
# =============================================================================


class TestPlanToTimeline:
    """Test deterministic conversion of VideoPlan to timeline_data."""

    def _make_simple_plan(self) -> VideoPlan:
        """Create a simple test plan with 2 sections."""
        return VideoPlan(
            version="1.0",
            total_duration_ms=25000,
            status="draft",
            sections=[
                PlanSection(
                    id="sec_001",
                    type="intro",
                    title="イントロ",
                    layout="avatar_fullscreen",
                    start_ms=0,
                    duration_ms=15000,
                    elements=[
                        PlanElement(
                            id="elem_001",
                            layer="background",
                            asset_id="bg-uuid",
                            start_ms=0,
                            duration_ms=15000,
                        ),
                        PlanElement(
                            id="elem_002",
                            layer="avatar",
                            asset_id="avatar-uuid",
                            start_ms=0,
                            duration_ms=15000,
                            effects=ElementEffects(
                                chroma_key={"enabled": True, "color": "#00FF00", "similarity": 0.3}
                            ),
                        ),
                        PlanElement(
                            id="elem_003",
                            layer="text",
                            text_content="セクション3\nスクリプト基礎",
                            start_ms=1000,
                            duration_ms=5000,
                            text_style=TextStylePlan(
                                fontSize=64,
                                fontWeight="bold",
                                color="#FFFFFF",
                            ),
                        ),
                    ],
                    audio=[
                        PlanAudioElement(
                            id="aud_001",
                            track="narration",
                            asset_id="narr-uuid",
                            start_ms=0,
                            duration_ms=14000,
                        ),
                        PlanAudioElement(
                            id="aud_002",
                            track="bgm",
                            asset_id="bgm-uuid",
                            start_ms=0,
                            duration_ms=15000,
                            volume=0.3,
                            fade_in_ms=2000,
                        ),
                    ],
                ),
                PlanSection(
                    id="sec_002",
                    type="content",
                    title="変数の基礎",
                    layout="slide_with_avatar",
                    start_ms=15000,
                    duration_ms=10000,
                    elements=[
                        PlanElement(
                            id="elem_004",
                            layer="content",
                            asset_id="slide-uuid",
                            start_ms=0,
                            duration_ms=10000,
                        ),
                        PlanElement(
                            id="elem_005",
                            layer="avatar",
                            asset_id="avatar-uuid",
                            start_ms=0,
                            duration_ms=10000,
                            transform=ElementTransform(x=700, y=350, scale=0.3),
                        ),
                    ],
                    audio=[
                        PlanAudioElement(
                            id="aud_003",
                            track="narration",
                            asset_id="narr2-uuid",
                            start_ms=0,
                            duration_ms=9000,
                        ),
                    ],
                ),
            ],
        )

    def test_timeline_structure(self):
        """Test that output has correct structure."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        assert timeline["version"] == "1.0"
        assert len(timeline["layers"]) == 5
        assert len(timeline["audio_tracks"]) == 3

    def test_layer_types(self):
        """Test layer type assignments."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        layer_types = [l["type"] for l in timeline["layers"]]
        assert layer_types == ["background", "content", "avatar", "effects", "text"]

    def test_audio_track_types(self):
        """Test audio track type assignments."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        track_types = [t["type"] for t in timeline["audio_tracks"]]
        assert track_types == ["narration", "bgm", "se"]

    def test_background_clips(self):
        """Test background layer clips."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        bg_layer = timeline["layers"][0]
        assert len(bg_layer["clips"]) == 1
        assert bg_layer["clips"][0]["asset_id"] == "bg-uuid"
        assert bg_layer["clips"][0]["start_ms"] == 0
        assert bg_layer["clips"][0]["duration_ms"] == 15000

    def test_content_clips(self):
        """Test content layer clips from section 2."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        content_layer = timeline["layers"][1]
        assert len(content_layer["clips"]) == 1
        assert content_layer["clips"][0]["asset_id"] == "slide-uuid"
        # Section 2 starts at 15000ms
        assert content_layer["clips"][0]["start_ms"] == 15000

    def test_avatar_clips(self):
        """Test avatar layer clips from both sections."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        avatar_layer = timeline["layers"][2]
        assert len(avatar_layer["clips"]) == 2

        # First avatar clip: section 1
        assert avatar_layer["clips"][0]["start_ms"] == 0
        assert avatar_layer["clips"][0]["duration_ms"] == 15000

        # Second avatar clip: section 2, offset by section start
        assert avatar_layer["clips"][1]["start_ms"] == 15000
        assert avatar_layer["clips"][1]["transform"]["x"] == 700
        assert avatar_layer["clips"][1]["transform"]["y"] == 350
        assert avatar_layer["clips"][1]["transform"]["scale"] == 0.3

    def test_text_clips(self):
        """Test text layer clips."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        text_layer = timeline["layers"][4]
        assert len(text_layer["clips"]) == 1
        clip = text_layer["clips"][0]
        assert clip["text_content"] == "セクション3\nスクリプト基礎"
        assert clip["asset_id"] is None  # Text clips have no asset
        assert clip["start_ms"] == 1000  # section offset(0) + element start(1000)
        assert clip["text_style"]["fontSize"] == 64

    def test_narration_clips(self):
        """Test narration audio track."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        narration_track = timeline["audio_tracks"][0]
        assert len(narration_track["clips"]) == 2

        # First narration
        assert narration_track["clips"][0]["asset_id"] == "narr-uuid"
        assert narration_track["clips"][0]["start_ms"] == 0
        assert narration_track["clips"][0]["duration_ms"] == 14000

        # Second narration from section 2
        assert narration_track["clips"][1]["asset_id"] == "narr2-uuid"
        assert narration_track["clips"][1]["start_ms"] == 15000  # section offset

    def test_bgm_clips(self):
        """Test BGM audio track with ducking config."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        bgm_track = timeline["audio_tracks"][1]
        assert bgm_track["ducking"]["enabled"] is True
        assert bgm_track["ducking"]["duck_to"] == 0.1
        assert len(bgm_track["clips"]) == 1
        assert bgm_track["clips"][0]["volume"] == 0.3
        assert bgm_track["clips"][0]["fade_in_ms"] == 2000

    def test_duration_calculation(self):
        """Test total duration is calculated from clip endpoints."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        # Max clip end: section 2 starts at 15000, content clip is 10000ms
        # So max = 15000 + 10000 = 25000
        assert timeline["duration_ms"] == 25000

    def test_chroma_key_applied(self):
        """Test chroma key effect is applied to clips."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        avatar_layer = timeline["layers"][2]
        first_avatar = avatar_layer["clips"][0]
        assert first_avatar["effects"]["chroma_key"]["enabled"] is True
        assert first_avatar["effects"]["chroma_key"]["color"] == "#00FF00"

    def test_fade_transitions(self):
        """Test fade in/out applied via effects."""
        plan = VideoPlan(
            total_duration_ms=5000,
            sections=[
                PlanSection(
                    id="sec_001",
                    type="intro",
                    title="Fade test",
                    start_ms=0,
                    duration_ms=5000,
                    elements=[
                        PlanElement(
                            id="elem_001",
                            layer="text",
                            text_content="Fading text",
                            start_ms=0,
                            duration_ms=5000,
                            effects=ElementEffects(fade_in_ms=500, fade_out_ms=500),
                        ),
                    ],
                ),
            ],
        )
        timeline = plan_to_timeline(plan)
        text_clip = timeline["layers"][4]["clips"][0]
        assert text_clip["transition_in"]["type"] == "fade"
        assert text_clip["transition_in"]["duration_ms"] == 500
        assert text_clip["transition_out"]["type"] == "fade"
        assert text_clip["transition_out"]["duration_ms"] == 500

    def test_empty_plan(self):
        """Test conversion of empty plan."""
        plan = VideoPlan()
        timeline = plan_to_timeline(plan)

        assert timeline["duration_ms"] == 0
        assert len(timeline["layers"]) == 5
        assert len(timeline["audio_tracks"]) == 3
        for layer in timeline["layers"]:
            assert len(layer["clips"]) == 0
        for track in timeline["audio_tracks"]:
            assert len(track["clips"]) == 0

    def test_unique_clip_ids(self):
        """Test that all generated clip IDs are unique."""
        plan = self._make_simple_plan()
        timeline = plan_to_timeline(plan)

        all_ids = set()
        for layer in timeline["layers"]:
            for clip in layer["clips"]:
                assert clip["id"] not in all_ids, f"Duplicate clip ID: {clip['id']}"
                all_ids.add(clip["id"])
        for track in timeline["audio_tracks"]:
            for clip in track["clips"]:
                assert clip["id"] not in all_ids, f"Duplicate audio clip ID: {clip['id']}"
                all_ids.add(clip["id"])
