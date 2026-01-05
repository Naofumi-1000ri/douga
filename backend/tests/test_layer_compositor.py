"""Tests for multi-layer video compositing functionality.

Layer structure (5 layers):
L5: テロップ・テキスト (Text overlays)
L4: エフェクト（キラキラ等）(Effects)
L3: アバター（クロマキー合成後）(Avatar with chroma key)
L2: 操作画面・スライド (Screen capture / Slides)
L1: 背景（3D空間/グラデーション）(Background)
"""

import tempfile
from pathlib import Path

import pytest

from src.render.layer_compositor import (
    LayerCompositor,
    Layer,
    LayerType,
    Clip,
    Transform,
    ChromaKeyConfig,
    CompositeConfig,
    CompositeOutput,
)


class TestLayerType:
    """Tests for LayerType enum."""

    def test_layer_types_exist(self):
        """Test that all 5 layer types exist."""
        assert LayerType.BACKGROUND.value == 1
        assert LayerType.SCREEN.value == 2
        assert LayerType.AVATAR.value == 3
        assert LayerType.EFFECT.value == 4
        assert LayerType.TEXT.value == 5

    def test_layer_order(self):
        """Test layer order (background is bottom, text is top)."""
        layers = sorted(LayerType, key=lambda x: x.value)
        assert layers[0] == LayerType.BACKGROUND
        assert layers[-1] == LayerType.TEXT


class TestTransform:
    """Tests for Transform dataclass."""

    def test_transform_defaults(self):
        """Test default transform values."""
        transform = Transform()
        assert transform.x == 0
        assert transform.y == 0
        assert transform.scale == 1.0
        assert transform.rotation == 0
        assert transform.opacity == 1.0

    def test_transform_custom_values(self):
        """Test custom transform values."""
        transform = Transform(x=100, y=50, scale=0.5, rotation=45, opacity=0.8)
        assert transform.x == 100
        assert transform.y == 50
        assert transform.scale == 0.5
        assert transform.rotation == 45
        assert transform.opacity == 0.8


class TestChromaKeyConfig:
    """Tests for ChromaKeyConfig dataclass."""

    def test_chroma_key_defaults(self):
        """Test default chroma key configuration."""
        config = ChromaKeyConfig()
        assert config.enabled is False
        assert config.color == "0x00FF00"  # Green
        assert config.similarity == 0.3
        assert config.blend == 0.1

    def test_chroma_key_green_screen(self):
        """Test green screen chroma key config."""
        config = ChromaKeyConfig(enabled=True, color="0x00FF00", similarity=0.4)
        assert config.enabled is True
        assert config.color == "0x00FF00"
        assert config.similarity == 0.4


class TestClip:
    """Tests for Clip dataclass."""

    def test_clip_creation(self, test_video_with_audio):
        """Test clip creation with required fields."""
        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=0,
            duration_ms=5000,
        )
        assert clip.asset_path == str(test_video_with_audio)
        assert clip.start_ms == 0
        assert clip.duration_ms == 5000
        assert clip.in_point_ms == 0  # Default
        assert clip.transform is not None

    def test_clip_with_trim(self, test_video_with_audio):
        """Test clip with in/out points for trimming."""
        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=1000,
            duration_ms=3000,
            in_point_ms=2000,  # Start from 2s in source
        )
        assert clip.in_point_ms == 2000
        assert clip.duration_ms == 3000

    def test_clip_with_chroma_key(self, test_video_with_audio):
        """Test clip with chroma key enabled."""
        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=0,
            duration_ms=5000,
            chroma_key=ChromaKeyConfig(enabled=True),
        )
        assert clip.chroma_key.enabled is True


class TestLayer:
    """Tests for Layer dataclass."""

    def test_layer_creation(self, test_video_with_audio):
        """Test layer creation."""
        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=0,
            duration_ms=5000,
        )
        layer = Layer(
            layer_type=LayerType.BACKGROUND,
            clips=[clip],
        )
        assert layer.layer_type == LayerType.BACKGROUND
        assert len(layer.clips) == 1

    def test_layer_with_multiple_clips(self, multiple_audio_videos):
        """Test layer with multiple clips."""
        clips = [
            Clip(asset_path=str(v), start_ms=i * 5000, duration_ms=5000)
            for i, v in enumerate(multiple_audio_videos[:3])
        ]
        layer = Layer(layer_type=LayerType.SCREEN, clips=clips)
        assert len(layer.clips) == 3


class TestCompositeConfig:
    """Tests for CompositeConfig dataclass."""

    def test_composite_config_defaults(self):
        """Test default composite configuration."""
        config = CompositeConfig()
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.duration_ms is None

    def test_composite_config_custom(self):
        """Test custom composite configuration."""
        config = CompositeConfig(width=1280, height=720, fps=60, duration_ms=10000)
        assert config.width == 1280
        assert config.height == 720
        assert config.fps == 60
        assert config.duration_ms == 10000


class TestLayerCompositor:
    """Tests for LayerCompositor class."""

    def test_composite_single_layer(self, test_video_with_audio, temp_output_dir):
        """Test compositing with single background layer."""
        compositor = LayerCompositor()
        output_path = temp_output_dir / "single_layer.mp4"

        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=0,
            duration_ms=5000,
        )
        layer = Layer(layer_type=LayerType.BACKGROUND, clips=[clip])

        config = CompositeConfig(width=1280, height=720, duration_ms=5000)

        result = compositor.composite(
            layers=[layer],
            output_path=str(output_path),
            config=config,
        )

        assert result.path.exists()
        assert result.duration_ms > 0

    def test_composite_two_layers(
        self, test_video_with_audio, test_video_no_audio, temp_output_dir
    ):
        """Test compositing with two layers (background + screen)."""
        compositor = LayerCompositor()
        output_path = temp_output_dir / "two_layers.mp4"

        background = Layer(
            layer_type=LayerType.BACKGROUND,
            clips=[
                Clip(
                    asset_path=str(test_video_with_audio),
                    start_ms=0,
                    duration_ms=5000,
                )
            ],
        )

        screen = Layer(
            layer_type=LayerType.SCREEN,
            clips=[
                Clip(
                    asset_path=str(test_video_no_audio),
                    start_ms=0,
                    duration_ms=5000,
                    transform=Transform(x=100, y=100, scale=0.5),
                )
            ],
        )

        config = CompositeConfig(width=1280, height=720, duration_ms=5000)

        result = compositor.composite(
            layers=[background, screen],
            output_path=str(output_path),
            config=config,
        )

        assert result.path.exists()
        assert result.width == 1280
        assert result.height == 720

    def test_composite_with_chroma_key(
        self, test_video_with_audio, temp_output_dir
    ):
        """Test compositing with chroma key (green screen removal)."""
        compositor = LayerCompositor()
        output_path = temp_output_dir / "chroma_key.mp4"

        background = Layer(
            layer_type=LayerType.BACKGROUND,
            clips=[
                Clip(
                    asset_path=str(test_video_with_audio),
                    start_ms=0,
                    duration_ms=3000,
                )
            ],
        )

        # Avatar layer with chroma key
        avatar = Layer(
            layer_type=LayerType.AVATAR,
            clips=[
                Clip(
                    asset_path=str(test_video_with_audio),
                    start_ms=0,
                    duration_ms=3000,
                    chroma_key=ChromaKeyConfig(
                        enabled=True, color="0x00FF00", similarity=0.3
                    ),
                    transform=Transform(x=50, y=50, scale=0.4),
                )
            ],
        )

        config = CompositeConfig(width=1280, height=720, duration_ms=3000)

        result = compositor.composite(
            layers=[background, avatar],
            output_path=str(output_path),
            config=config,
        )

        assert result.path.exists()

    def test_composite_layer_ordering(
        self, test_video_with_audio, test_video_no_audio, temp_output_dir
    ):
        """Test that layers are composited in correct order."""
        compositor = LayerCompositor()
        output_path = temp_output_dir / "ordered_layers.mp4"

        # Create layers in wrong order, compositor should sort them
        screen = Layer(
            layer_type=LayerType.SCREEN,
            clips=[
                Clip(
                    asset_path=str(test_video_no_audio),
                    start_ms=0,
                    duration_ms=3000,
                )
            ],
        )

        background = Layer(
            layer_type=LayerType.BACKGROUND,
            clips=[
                Clip(
                    asset_path=str(test_video_with_audio),
                    start_ms=0,
                    duration_ms=3000,
                )
            ],
        )

        config = CompositeConfig(width=1280, height=720, duration_ms=3000)

        # Pass in wrong order
        result = compositor.composite(
            layers=[screen, background],  # Wrong order
            output_path=str(output_path),
            config=config,
        )

        assert result.path.exists()

    def test_composite_preserves_audio(self, test_video_with_audio, temp_output_dir):
        """Test that compositing preserves audio from layers."""
        compositor = LayerCompositor()
        output_path = temp_output_dir / "with_audio.mp4"

        layer = Layer(
            layer_type=LayerType.BACKGROUND,
            clips=[
                Clip(
                    asset_path=str(test_video_with_audio),
                    start_ms=0,
                    duration_ms=5000,
                )
            ],
        )

        config = CompositeConfig(width=1280, height=720, duration_ms=5000)

        result = compositor.composite(
            layers=[layer],
            output_path=str(output_path),
            config=config,
        )

        from src.utils.media_info import has_audio_track
        assert has_audio_track(str(result.path))


class TestFilterComplexGeneration:
    """Tests for FFmpeg filter_complex string generation."""

    def test_generate_scale_filter(self):
        """Test scale filter generation."""
        compositor = LayerCompositor()
        filter_str = compositor._generate_scale_filter(
            input_label="[0:v]",
            output_label="[scaled0]",
            width=640,
            height=360,
        )
        assert "scale=640:360" in filter_str
        assert "[0:v]" in filter_str
        assert "[scaled0]" in filter_str

    def test_generate_overlay_filter(self):
        """Test overlay filter generation."""
        compositor = LayerCompositor()
        filter_str = compositor._generate_overlay_filter(
            base_label="[base]",
            overlay_label="[overlay]",
            output_label="[out]",
            x=100,
            y=50,
        )
        assert "overlay=" in filter_str
        assert "100" in filter_str
        assert "50" in filter_str

    def test_generate_chroma_key_filter(self):
        """Test chroma key filter generation."""
        compositor = LayerCompositor()
        config = ChromaKeyConfig(enabled=True, color="0x00FF00", similarity=0.3)
        filter_str = compositor._generate_chroma_key_filter(
            input_label="[0:v]",
            output_label="[keyed]",
            config=config,
        )
        assert "colorkey=" in filter_str
        assert "0x00FF00" in filter_str

    def test_build_full_filter_complex(self, test_video_with_audio):
        """Test building complete filter_complex string."""
        compositor = LayerCompositor()

        clip = Clip(
            asset_path=str(test_video_with_audio),
            start_ms=0,
            duration_ms=5000,
            transform=Transform(x=0, y=0, scale=1.0),
        )
        layer = Layer(layer_type=LayerType.BACKGROUND, clips=[clip])

        config = CompositeConfig(width=1280, height=720, duration_ms=5000)

        filter_complex = compositor._build_filter_complex(
            layers=[layer],
            config=config,
        )

        assert isinstance(filter_complex, str)
        assert len(filter_complex) > 0


class TestCompositeOutput:
    """Tests for CompositeOutput dataclass."""

    def test_composite_output_creation(self, temp_output_dir):
        """Test CompositeOutput creation."""
        path = temp_output_dir / "output.mp4"
        path.touch()

        output = CompositeOutput(
            path=path,
            duration_ms=10000,
            width=1920,
            height=1080,
            file_size=1024000,
            layers_count=3,
        )

        assert output.path == path
        assert output.duration_ms == 10000
        assert output.layers_count == 3

    def test_composite_output_to_dict(self, temp_output_dir):
        """Test CompositeOutput serialization."""
        path = temp_output_dir / "output.mp4"
        path.touch()

        output = CompositeOutput(
            path=path,
            duration_ms=10000,
            width=1920,
            height=1080,
            file_size=1024000,
            layers_count=3,
        )

        data = output.to_dict()
        assert data["duration_ms"] == 10000
        assert data["layers_count"] == 3
