"""Tests for text rendering and effects functionality.

Features:
- Japanese text rendering with custom fonts
- Text styling (color, size, shadow, outline)
- Effects (sparkle/キラキラ)
- Fade transitions
"""

import tempfile
from pathlib import Path

import pytest

from src.render.text_renderer import (
    TextRenderer,
    TextConfig,
    TextStyle,
    TextPosition,
    EffectType,
    EffectConfig,
    TransitionType,
    TransitionConfig,
    TextOverlay,
)


class TestTextStyle:
    """Tests for TextStyle dataclass."""

    def test_text_style_defaults(self):
        """Test default text style values."""
        style = TextStyle()
        assert style.font_size == 48
        assert style.font_color == "white"
        assert style.font_family == "NotoSansJP"
        assert style.bold is False
        assert style.italic is False
        assert style.outline_color is None
        assert style.outline_width == 0
        assert style.shadow_color is None
        assert style.shadow_offset == 2

    def test_text_style_with_outline(self):
        """Test text style with outline."""
        style = TextStyle(
            font_size=64,
            font_color="yellow",
            outline_color="black",
            outline_width=3,
        )
        assert style.font_size == 64
        assert style.outline_color == "black"
        assert style.outline_width == 3

    def test_text_style_with_shadow(self):
        """Test text style with shadow."""
        style = TextStyle(
            font_color="white",
            shadow_color="black",
            shadow_offset=4,
        )
        assert style.shadow_color == "black"
        assert style.shadow_offset == 4


class TestTextPosition:
    """Tests for TextPosition dataclass."""

    def test_position_defaults(self):
        """Test default position (center)."""
        pos = TextPosition()
        assert pos.x == "center"
        assert pos.y == "center"
        assert pos.anchor == "center"

    def test_position_absolute(self):
        """Test absolute positioning."""
        pos = TextPosition(x=100, y=200)
        assert pos.x == 100
        assert pos.y == 200

    def test_position_bottom_center(self):
        """Test bottom center positioning for subtitles."""
        pos = TextPosition(x="center", y="bottom", anchor="bottom")
        assert pos.x == "center"
        assert pos.y == "bottom"


class TestTextConfig:
    """Tests for TextConfig dataclass."""

    def test_text_config_basic(self):
        """Test basic text configuration."""
        config = TextConfig(
            text="こんにちは",
            start_ms=0,
            duration_ms=3000,
        )
        assert config.text == "こんにちは"
        assert config.start_ms == 0
        assert config.duration_ms == 3000
        assert config.style is not None
        assert config.position is not None

    def test_text_config_with_style(self):
        """Test text config with custom style."""
        style = TextStyle(font_size=72, font_color="red")
        config = TextConfig(
            text="テスト",
            start_ms=1000,
            duration_ms=2000,
            style=style,
        )
        assert config.style.font_size == 72
        assert config.style.font_color == "red"


class TestEffectType:
    """Tests for EffectType enum."""

    def test_effect_types_exist(self):
        """Test that effect types exist."""
        assert EffectType.SPARKLE.value == "sparkle"
        assert EffectType.GLOW.value == "glow"
        assert EffectType.PULSE.value == "pulse"


class TestEffectConfig:
    """Tests for EffectConfig dataclass."""

    def test_sparkle_effect_config(self):
        """Test sparkle effect configuration."""
        config = EffectConfig(
            effect_type=EffectType.SPARKLE,
            intensity=0.8,
            color="gold",
        )
        assert config.effect_type == EffectType.SPARKLE
        assert config.intensity == 0.8
        assert config.color == "gold"

    def test_glow_effect_config(self):
        """Test glow effect configuration."""
        config = EffectConfig(
            effect_type=EffectType.GLOW,
            intensity=0.5,
            color="blue",
            radius=10,
        )
        assert config.effect_type == EffectType.GLOW
        assert config.radius == 10


class TestTransitionType:
    """Tests for TransitionType enum."""

    def test_transition_types_exist(self):
        """Test that transition types exist."""
        assert TransitionType.FADE_IN.value == "fade_in"
        assert TransitionType.FADE_OUT.value == "fade_out"
        assert TransitionType.FADE_IN_OUT.value == "fade_in_out"
        assert TransitionType.SLIDE_IN.value == "slide_in"
        assert TransitionType.SLIDE_OUT.value == "slide_out"


class TestTransitionConfig:
    """Tests for TransitionConfig dataclass."""

    def test_fade_in_transition(self):
        """Test fade in transition configuration."""
        config = TransitionConfig(
            transition_type=TransitionType.FADE_IN,
            duration_ms=500,
        )
        assert config.transition_type == TransitionType.FADE_IN
        assert config.duration_ms == 500

    def test_fade_in_out_transition(self):
        """Test fade in/out transition configuration."""
        config = TransitionConfig(
            transition_type=TransitionType.FADE_IN_OUT,
            duration_ms=300,
        )
        assert config.transition_type == TransitionType.FADE_IN_OUT


class TestTextOverlay:
    """Tests for TextOverlay dataclass."""

    def test_text_overlay_creation(self):
        """Test text overlay creation."""
        text_config = TextConfig(text="テスト", start_ms=0, duration_ms=3000)
        overlay = TextOverlay(
            text_config=text_config,
            effect=None,
            transition_in=TransitionConfig(TransitionType.FADE_IN, 500),
            transition_out=TransitionConfig(TransitionType.FADE_OUT, 500),
        )
        assert overlay.text_config.text == "テスト"
        assert overlay.transition_in.duration_ms == 500


class TestTextRenderer:
    """Tests for TextRenderer class."""

    def test_render_simple_text(self, temp_output_dir):
        """Test rendering simple text to image."""
        renderer = TextRenderer()
        output_path = temp_output_dir / "text.png"

        config = TextConfig(
            text="Hello World",
            start_ms=0,
            duration_ms=3000,
        )

        result = renderer.render_text_image(config, str(output_path))

        assert result.exists()
        assert result.suffix == ".png"

    def test_render_japanese_text(self, temp_output_dir):
        """Test rendering Japanese text."""
        renderer = TextRenderer()
        output_path = temp_output_dir / "japanese.png"

        config = TextConfig(
            text="こんにちは世界",
            start_ms=0,
            duration_ms=3000,
            style=TextStyle(font_family="NotoSansJP"),
        )

        result = renderer.render_text_image(config, str(output_path))

        assert result.exists()

    def test_render_text_with_outline(self, temp_output_dir):
        """Test rendering text with outline."""
        renderer = TextRenderer()
        output_path = temp_output_dir / "outline.png"

        config = TextConfig(
            text="アウトライン",
            start_ms=0,
            duration_ms=3000,
            style=TextStyle(
                font_size=64,
                font_color="white",
                outline_color="black",
                outline_width=3,
            ),
        )

        result = renderer.render_text_image(config, str(output_path))

        assert result.exists()

    def test_render_text_with_shadow(self, temp_output_dir):
        """Test rendering text with shadow."""
        renderer = TextRenderer()
        output_path = temp_output_dir / "shadow.png"

        config = TextConfig(
            text="シャドウテスト",
            start_ms=0,
            duration_ms=3000,
            style=TextStyle(
                font_color="white",
                shadow_color="gray",
                shadow_offset=3,
            ),
        )

        result = renderer.render_text_image(config, str(output_path))

        assert result.exists()

    def test_generate_drawtext_filter(self):
        """Test FFmpeg drawtext filter generation."""
        renderer = TextRenderer()

        config = TextConfig(
            text="テスト",
            start_ms=1000,
            duration_ms=2000,
            style=TextStyle(font_size=48, font_color="white"),
            position=TextPosition(x="center", y="center"),
        )

        filter_str = renderer.generate_drawtext_filter(config)

        assert "drawtext=" in filter_str
        assert "fontsize=48" in filter_str

    def test_generate_drawtext_filter_with_fade(self):
        """Test drawtext filter with fade transitions."""
        renderer = TextRenderer()

        config = TextConfig(
            text="フェードテスト",
            start_ms=0,
            duration_ms=3000,
            style=TextStyle(font_size=48),
        )

        transition_in = TransitionConfig(TransitionType.FADE_IN, 500)
        transition_out = TransitionConfig(TransitionType.FADE_OUT, 500)

        filter_str = renderer.generate_drawtext_filter(
            config,
            transition_in=transition_in,
            transition_out=transition_out,
        )

        assert "drawtext=" in filter_str
        # Should include alpha expression for fade
        assert "alpha=" in filter_str or "enable=" in filter_str


class TestEffectRenderer:
    """Tests for effect rendering."""

    def test_generate_sparkle_effect(self, temp_output_dir):
        """Test sparkle effect generation."""
        renderer = TextRenderer()

        effect = EffectConfig(
            effect_type=EffectType.SPARKLE,
            intensity=0.7,
            color="gold",
        )

        # Generate sparkle overlay frames
        result = renderer.generate_effect_overlay(
            effect,
            width=1920,
            height=1080,
            duration_ms=3000,
            output_dir=str(temp_output_dir),
        )

        assert result is not None
        # Should return path to generated effect video/image sequence
        assert isinstance(result, (str, Path))

    def test_generate_glow_effect_filter(self):
        """Test glow effect filter generation."""
        renderer = TextRenderer()

        effect = EffectConfig(
            effect_type=EffectType.GLOW,
            intensity=0.5,
            color="blue",
            radius=8,
        )

        filter_str = renderer.generate_effect_filter(effect)

        # Glow uses gblur or similar filter
        assert "gblur" in filter_str or "boxblur" in filter_str


class TestFadeTransitions:
    """Tests for fade transition generation."""

    def test_generate_fade_in_filter(self):
        """Test fade in filter generation."""
        renderer = TextRenderer()

        transition = TransitionConfig(
            transition_type=TransitionType.FADE_IN,
            duration_ms=500,
        )

        filter_str = renderer.generate_transition_filter(
            transition,
            start_ms=0,
            total_duration_ms=3000,
        )

        assert "fade=" in filter_str or "alpha=" in filter_str

    def test_generate_fade_out_filter(self):
        """Test fade out filter generation."""
        renderer = TextRenderer()

        transition = TransitionConfig(
            transition_type=TransitionType.FADE_OUT,
            duration_ms=500,
        )

        filter_str = renderer.generate_transition_filter(
            transition,
            start_ms=2500,
            total_duration_ms=3000,
        )

        assert "fade=" in filter_str or "alpha=" in filter_str

    def test_generate_fade_in_out_filter(self):
        """Test fade in and out filter generation."""
        renderer = TextRenderer()

        transition = TransitionConfig(
            transition_type=TransitionType.FADE_IN_OUT,
            duration_ms=500,
        )

        filter_str = renderer.generate_transition_filter(
            transition,
            start_ms=0,
            total_duration_ms=3000,
        )

        # Should have both fade in and fade out
        assert "fade=" in filter_str or "alpha=" in filter_str
