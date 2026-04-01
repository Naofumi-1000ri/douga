"""Tests for the complete rendering pipeline.

Features:
- Full video rendering from timeline
- Progress tracking
- Job management
- Undo/Redo support
"""

from pathlib import Path

import src.render.pipeline as pipeline_module
from src.render.pipeline import (
    RenderConfig,
    RenderJob,
    RenderPipeline,
    RenderProgress,
    RenderStatus,
    TimelineData,
    UndoableAction,
    UndoManager,
)


class TestRenderStatus:
    """Tests for RenderStatus enum."""

    def test_render_statuses_exist(self):
        """Test that all render statuses exist."""
        assert RenderStatus.PENDING.value == "pending"
        assert RenderStatus.PROCESSING.value == "processing"
        assert RenderStatus.COMPLETED.value == "completed"
        assert RenderStatus.FAILED.value == "failed"
        assert RenderStatus.CANCELLED.value == "cancelled"


class TestRenderProgress:
    """Tests for RenderProgress dataclass."""

    def test_progress_creation(self):
        """Test progress creation."""
        progress = RenderProgress(
            job_id="job123",
            status=RenderStatus.PROCESSING,
            percent=50.0,
            current_step="レイヤー合成中",
            elapsed_ms=5000,
        )
        assert progress.job_id == "job123"
        assert progress.percent == 50.0
        assert progress.current_step == "レイヤー合成中"

    def test_progress_to_dict(self):
        """Test progress serialization."""
        progress = RenderProgress(
            job_id="job123",
            status=RenderStatus.PROCESSING,
            percent=75.0,
        )
        data = progress.to_dict()
        assert data["job_id"] == "job123"
        assert data["status"] == "processing"
        assert data["percent"] == 75.0


class TestRenderConfig:
    """Tests for RenderConfig dataclass."""

    def test_config_defaults(self):
        """Test default render configuration."""
        config = RenderConfig()
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.video_codec == "libx264"
        assert config.audio_codec == "aac"
        assert config.crf == 18

    def test_config_custom(self):
        """Test custom render configuration."""
        config = RenderConfig(
            width=1280,
            height=720,
            fps=60,
            crf=23,
        )
        assert config.width == 1280
        assert config.fps == 60


class TestRenderJob:
    """Tests for RenderJob dataclass."""

    def test_job_creation(self):
        """Test render job creation."""
        job = RenderJob(
            id="job123",
            project_id="proj456",
            status=RenderStatus.PENDING,
            config=RenderConfig(),
        )
        assert job.id == "job123"
        assert job.project_id == "proj456"
        assert job.status == RenderStatus.PENDING

    def test_job_to_dict(self):
        """Test job serialization."""
        job = RenderJob(
            id="job123",
            project_id="proj456",
            status=RenderStatus.COMPLETED,
            output_path="/output/video.mp4",
        )
        data = job.to_dict()
        assert data["id"] == "job123"
        assert data["status"] == "completed"
        assert data["output_path"] == "/output/video.mp4"


class TestTimelineData:
    """Tests for TimelineData dataclass."""

    def test_timeline_creation(self):
        """Test timeline data creation."""
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=60000,
            layers=[
                {"id": "layer1", "type": "background", "clips": []},
                {"id": "layer2", "type": "avatar", "clips": []},
            ],
            audio_tracks=[
                {"id": "audio1", "type": "narration", "clips": []},
            ],
        )
        assert timeline.project_id == "proj123"
        assert timeline.duration_ms == 60000
        assert len(timeline.layers) == 2
        assert len(timeline.audio_tracks) == 1

    def test_timeline_to_dict(self):
        """Test timeline serialization."""
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=30000,
            layers=[],
            audio_tracks=[],
        )
        data = timeline.to_dict()
        assert data["project_id"] == "proj123"
        assert data["duration_ms"] == 30000


class TestRenderPipeline:
    """Tests for RenderPipeline class."""

    def test_create_job(self):
        """Test creating a render job."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)

        assert job.id is not None
        assert job.project_id == "proj123"
        assert job.status == RenderStatus.PENDING

    def test_get_job(self):
        """Test getting a render job by ID."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        created_job = pipeline.create_job(timeline)
        retrieved_job = pipeline.get_job(created_job.id)

        assert retrieved_job is not None
        assert retrieved_job.id == created_job.id

    def test_get_nonexistent_job(self):
        """Test getting nonexistent job returns None."""
        pipeline = RenderPipeline()
        job = pipeline.get_job("nonexistent")
        assert job is None

    def test_list_jobs(self):
        """Test listing jobs for a project."""
        pipeline = RenderPipeline()

        # Create multiple jobs
        for i in range(3):
            timeline = TimelineData(
                project_id="proj123",
                duration_ms=10000,
                layers=[],
                audio_tracks=[],
            )
            pipeline.create_job(timeline)

        jobs = pipeline.list_jobs("proj123")
        assert len(jobs) == 3

    def test_cancel_job(self):
        """Test cancelling a pending job."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)
        result = pipeline.cancel_job(job.id)

        assert result is True
        updated_job = pipeline.get_job(job.id)
        assert updated_job.status == RenderStatus.CANCELLED

    def test_get_progress(self):
        """Test getting job progress."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)
        progress = pipeline.get_progress(job.id)

        assert progress is not None
        assert progress.job_id == job.id
        assert progress.percent >= 0

    def test_register_progress_callback(self):
        """Test registering progress callback."""
        pipeline = RenderPipeline()
        received_updates = []

        def callback(progress: RenderProgress):
            received_updates.append(progress)

        pipeline.register_progress_callback("job123", callback)

        # Simulate progress update
        pipeline._notify_progress(
            RenderProgress(
                job_id="job123",
                status=RenderStatus.PROCESSING,
                percent=50.0,
            )
        )

        assert len(received_updates) == 1
        assert received_updates[0].percent == 50.0

    def test_build_clip_fade_alpha_expr_accounts_for_export_offset(self):
        """Fade expression should use clip-relative time even for clipped exports."""
        pipeline = RenderPipeline()

        expr = pipeline._build_clip_fade_alpha_expr(
            {
                "start_ms": 1000,
                "duration_ms": 4000,
                "effects": {"fade_in_ms": 1000, "fade_out_ms": 500},
            },
            export_start_ms=1500,
        )

        assert expr is not None
        assert "(T-0.000000+0.500000)" in expr
        assert "/1.000000" in expr
        assert "(4.000000-(T-0.000000+0.500000))" in expr
        assert "/0.500000" in expr
        assert "min(" not in expr

    def test_build_clip_fade_alpha_expr_matches_preview_overlap_order(self):
        """Fade-out should override fade-in when both windows overlap."""
        pipeline = RenderPipeline()

        expr = pipeline._build_clip_fade_alpha_expr(
            {
                "start_ms": 0,
                "duration_ms": 1000,
                "effects": {"fade_in_ms": 800, "fade_out_ms": 800},
            },
            export_start_ms=0,
        )

        assert expr is not None
        assert "if(lt((1.000000-(T-0.000000+0.000000)),0.800000)" in expr
        assert "if(lt((T-0.000000+0.000000),0.800000)" in expr

    def test_build_clip_filter_adds_time_based_alpha_fade(self):
        """Video overlays should add alpha fade filters before compositing."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 1000,
                "duration_ms": 4000,
                "in_point_ms": 0,
                "out_point_ms": None,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 640,
                    "height": 360,
                },
                "effects": {"opacity": 1.0, "fade_in_ms": 1000, "fade_out_ms": 500},
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=5000,
            export_start_ms=1500,
            export_end_ms=5000,
        )

        assert "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'" in filter_str
        assert "alpha(X,Y)*((1.000000)*(max(0,if(" in filter_str
        assert "overlay=x='(main_w/2)+(0.000000)" in filter_str

    def test_build_clip_filter_uses_keyframed_transform_expressions(self):
        """Keyframed clips should emit per-frame expressions for transform and opacity."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 500,
                "duration_ms": 1200,
                "in_point_ms": 0,
                "out_point_ms": None,
                "transform": {
                    "x": -80,
                    "y": 10,
                    "scale": 0.8,
                    "rotation": -5,
                    "width": 640,
                    "height": 360,
                },
                "effects": {"opacity": 0.9},
                "keyframes": [
                    {
                        "time_ms": 0,
                        "transform": {"x": -80, "y": 10, "scale": 0.8, "rotation": -5},
                        "opacity": 0.9,
                    },
                    {
                        "time_ms": 600,
                        "transform": {"x": 60, "y": -25, "scale": 1.1, "rotation": 12},
                        "opacity": 0.55,
                    },
                    {
                        "time_ms": 1200,
                        "transform": {"x": 10, "y": 35, "scale": 0.95, "rotation": 0},
                        "opacity": 1.0,
                    },
                ],
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=2000,
            export_start_ms=0,
            export_end_ms=2000,
        )

        assert "scale=w='max(2,trunc(640*(" in filter_str
        assert "rotate='(" in filter_str
        assert "0.600000" in filter_str
        assert "1.200000" in filter_str
        assert "alpha(X,Y)*((if(lt(((T-0.500000)),0.000000),0.900000" in filter_str

    def test_generate_shape_image_prefers_shape_dimensions_like_browser_preview(
        self, temp_output_dir
    ):
        """Shape PNG generation should use shape.width/height, not transform.width/height."""
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_shape_image(
            shape={
                "type": "rectangle",
                "width": 100,
                "height": 50,
                "fillColor": "#ff3366",
                "strokeColor": "#ffffff",
                "strokeWidth": 2,
                "filled": True,
            },
            clip={
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 300,
                    "height": 200,
                }
            },
            shape_idx=0,
        )

        assert output_path is not None
        from PIL import Image

        # Canvas should be shape.width + strokeWidth × shape.height + strokeWidth
        # (strokeWidth=2 → 102 × 52) to match browser SVG canvas size.
        with Image.open(output_path) as image:
            assert image.size == (102, 52)

    def test_generate_shape_image_canvas_includes_stroke_width(self, temp_output_dir):
        """Shape PNG canvas size must be (shape.width + strokeWidth) × (shape.height + strokeWidth).

        This matches the browser SVG which uses the expanded canvas so that the stroke
        is not clipped at the edges.
        """
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        for shape_type in ("rectangle", "circle"):
            output_path = pipeline._generate_shape_image(
                shape={
                    "type": shape_type,
                    "width": 200,
                    "height": 100,
                    "fillColor": "#aabbcc",
                    "strokeColor": "#112233",
                    "strokeWidth": 6,
                    "filled": True,
                },
                clip={"transform": {}},
                shape_idx=0,
            )
            assert output_path is not None, f"{shape_type}: output_path is None"
            from PIL import Image

            with Image.open(output_path) as img:
                assert img.size == (206, 106), (
                    f"{shape_type}: expected (206, 106) but got {img.size}"
                )

        # line: canvas should NOT expand by strokeWidth (existing behaviour)
        output_path_line = pipeline._generate_shape_image(
            shape={
                "type": "line",
                "width": 300,
                "height": 10,
                "fillColor": "#000000",
                "strokeColor": "#ffffff",
                "strokeWidth": 4,
                "filled": True,
            },
            clip={"transform": {}},
            shape_idx=1,
        )
        assert output_path_line is not None
        from PIL import Image

        with Image.open(output_path_line) as img:
            # line height is overridden to max(stroke_width*2, 4) = 8, width stays 300
            assert img.size[0] == 300

    def test_build_clip_filter_shape_uses_intrinsic_overlay_size(self):
        """Shape clips should scale their generated PNG, not reinterpret transform width/height."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 1000,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.5,
                    "rotation": 0,
                    "width": 300,
                    "height": 200,
                },
                "effects": {"opacity": 1.0},
                "shape": {
                    "type": "rectangle",
                    "width": 100,
                    "height": 50,
                    "fillColor": "#ff3366",
                    "strokeColor": "#ffffff",
                    "strokeWidth": 2,
                    "filled": True,
                },
            },
            layer_type="effects",
            base_output="0:v",
            total_duration_ms=1000,
            is_still_image=True,
        )

        assert "scale=w='max(2,trunc(iw*(1.500000)))'" in filter_str
        assert "trunc(300*(1.500000))" not in filter_str
        assert "trunc(200*(1.500000))" not in filter_str

    def test_build_clip_filter_shape_scale_one_does_not_fall_back_to_transform_dimensions(self):
        """Shape clips should keep intrinsic overlay size even when scale is exactly 1."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 1000,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 300,
                    "height": 200,
                },
                "effects": {"opacity": 1.0},
                "shape": {
                    "type": "rectangle",
                    "width": 100,
                    "height": 50,
                    "fillColor": "#ff3366",
                    "strokeColor": "#ffffff",
                    "strokeWidth": 2,
                    "filled": True,
                },
            },
            layer_type="effects",
            base_output="0:v",
            total_duration_ms=1000,
            is_still_image=True,
        )

        assert "trunc(300*(1.000000))" not in filter_str
        assert "trunc(200*(1.000000))" not in filter_str
        assert "scale=w='max(2,trunc(300))'" not in filter_str
        assert "scale=w='max(2,trunc(iw*(1.000000)))'" not in filter_str

    def test_generate_shape_image_arrow_filled(self, temp_output_dir):
        """Arrow shape generates a non-empty PNG with correct dimensions."""
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_shape_image(
            shape={
                "type": "arrow",
                "width": 230,
                "height": 80,
                "fillColor": "#ff3366",
                "strokeColor": "#000000",
                "strokeWidth": 2,
                "filled": True,
            },
            clip={"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0, "width": 300, "height": 200}},
            shape_idx=99,
        )

        assert output_path is not None
        from PIL import Image

        with Image.open(output_path) as img:
            # strokeWidth=2 → canvas is (230+2, 80+2)
            assert img.size == (232, 82)
            assert img.mode == "RGBA"
            # Verify the image contains non-transparent pixels (arrow was actually drawn)
            bbox = img.getbbox()
            assert bbox is not None, "Arrow image is completely transparent – nothing was drawn"

    def test_generate_shape_image_arrow_outline_only(self, temp_output_dir):
        """Arrow shape with filled=False still produces a visible outline."""
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_shape_image(
            shape={
                "type": "arrow",
                "width": 230,
                "height": 80,
                "fillColor": "#ff3366",
                "strokeColor": "#000000",
                "strokeWidth": 3,
                "filled": False,
            },
            clip={"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}},
            shape_idx=100,
        )

        assert output_path is not None
        from PIL import Image

        with Image.open(output_path) as img:
            # strokeWidth=3 → canvas is (230+3, 80+3)
            assert img.size == (233, 83)
            bbox = img.getbbox()
            assert bbox is not None, "Outline-only arrow is completely transparent"

    def test_generate_shape_image_arrow_wide_extends_shaft(self, temp_output_dir):
        """An arrow wider than the reference minimum should extend the shaft, not the head."""
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_shape_image(
            shape={
                "type": "arrow",
                "width": 400,
                "height": 80,
                "fillColor": "#ff3366",
                "strokeColor": "#000000",
                "strokeWidth": 2,
                "filled": True,
            },
            clip={"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}},
            shape_idx=101,
        )

        assert output_path is not None
        from PIL import Image

        with Image.open(output_path) as img:
            # strokeWidth=2 → canvas is (400+2, 80+2)
            assert img.size == (402, 82)
            bbox = img.getbbox()
            assert bbox is not None

    def test_generate_shape_image_arrow_minimum_width_clamp(self, temp_output_dir):
        """Arrow narrower than minimum width should still render at minimum arrow width geometry.

        This locks the behavior to match the frontend shapeGeometry.ts:
        minimumArrowWidth = ARROW_REFERENCE_WIDTH * (height / ARROW_REFERENCE_HEIGHT).
        For height=40 that is 230 * 0.5 = 115.
        """
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_shape_image(
            shape={
                "type": "arrow",
                "width": 50,  # well below minimum
                "height": 40,
                "fillColor": "#ff3366",
                "strokeColor": "#000000",
                "strokeWidth": 2,
                "filled": True,
            },
            clip={"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}},
            shape_idx=102,
        )

        assert output_path is not None
        from PIL import Image

        with Image.open(output_path) as img:
            # Image canvas uses the requested width + strokeWidth.  Arrow polygon
            # is drawn using the safe (clamped) geometry so it fits.
            # strokeWidth=2 → canvas is (50+2, 40+2)
            assert img.size == (52, 42)
            bbox = img.getbbox()
            assert bbox is not None, "Small arrow should still be visible"

    def test_generate_shape_image_arrow_scaled_geometry_matches_frontend(self, temp_output_dir):
        """Arrow geometry constants must match frontend shapeGeometry.ts reference values.

        This test locks the 6-point reference polygon and scaling formula so that
        any drift between frontend and backend is caught immediately.
        """
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        # Use height=160 (scale=2.0) so we can verify exact scaled coordinates
        height = 160
        width = 460  # reference_width * 2 = 460, so no extra shaft
        arrow_ref_height = 80
        arrow_ref_width = 230
        arrow_ref_points = [
            (0, 40), (160, 34), (154, 20),
            (230, 40), (154, 60), (160, 46),
        ]

        scale = height / arrow_ref_height  # 2.0
        min_arrow_width = arrow_ref_width * scale  # 460
        safe_width = max(min_arrow_width, width)
        unscaled_width = safe_width / scale
        extra_shaft = max(0, unscaled_width - arrow_ref_width)

        expected_points = []
        for i, (x, y) in enumerate(arrow_ref_points):
            adjusted_x = x if i == 0 else x + extra_shaft
            expected_points.append((adjusted_x * scale, y * scale))

        # Verify the expected geometry at scale=2.0
        assert expected_points[0] == (0.0, 80.0)   # tail
        assert expected_points[3] == (460.0, 80.0)  # tip
        assert extra_shaft == 0.0

        # Actually generate the image to ensure no crash
        output_path = pipeline._generate_shape_image(
            shape={
                "type": "arrow",
                "width": width,
                "height": height,
                "fillColor": "#00ff00",
                "strokeColor": "#000000",
                "strokeWidth": 2,
                "filled": True,
            },
            clip={"transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}},
            shape_idx=103,
        )
        assert output_path is not None

    def test_build_clip_filter_image_with_explicit_size_ignores_scale_like_browser_preview(self):
        """Image clips with explicit width/height should not apply transform.scale again."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "asset_id": "image-1",
                "start_ms": 0,
                "duration_ms": 1000,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.5,
                    "rotation": 0,
                    "width": 320,
                    "height": 180,
                },
                "effects": {"opacity": 1.0},
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=1000,
            is_still_image=True,
        )

        assert "scale=w='max(2,trunc(320))':h='max(2,trunc(180))':eval=init" in filter_str
        assert "trunc(320*(1.500000))" not in filter_str
        assert "trunc(180*(1.500000))" not in filter_str

    def test_build_clip_filter_adds_slide_transition_offsets(self):
        """Slide transitions should become overlay position offsets."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 250,
                "duration_ms": 1000,
                "in_point_ms": 0,
                "out_point_ms": None,
                "transform": {
                    "x": 20,
                    "y": -10,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 320,
                    "height": 180,
                },
                "effects": {"opacity": 1.0},
                "transition_in": {"type": "slide_left", "duration_ms": 200},
                "transition_out": {"type": "slide_right", "duration_ms": 300},
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=1500,
            export_start_ms=0,
            export_end_ms=1500,
        )

        assert "main_w*(1-(((t-0.250000))/0.200000))" in filter_str
        assert "main_w*(1-((1.000000-((t-0.250000)))/0.300000))" in filter_str
        assert "overlay=x='(main_w/2)+(20.000000)+(0)+" in filter_str

    def test_build_text_overlay_filter_adds_time_based_alpha_fade(self):
        """Text overlays should preprocess the PNG stream with fade alpha."""
        pipeline = RenderPipeline()

        filter_str = pipeline._build_text_overlay_filter(
            input_idx=2,
            clip={
                "start_ms": 1000,
                "duration_ms": 4000,
                "transform": {"x": 120, "y": -80},
                "effects": {"fade_in_ms": 1000, "fade_out_ms": 500},
            },
            base_output="0:v",
            text_idx=0,
            export_start_ms=1500,
        )

        assert "[2:v]format=rgba,geq=" in filter_str
        assert "[textsrc0]" in filter_str
        assert "[0:v][textsrc0]overlay=" in filter_str
        assert "alpha(X,Y)*(max(0,if(" in filter_str

    def test_build_composite_command_loops_generated_text_png(self, monkeypatch, tmp_path):
        """Generated text PNGs should be looped so time-based alpha can animate."""
        pipeline = RenderPipeline()
        pipeline.output_dir = str(tmp_path)
        text_png = tmp_path / "text.png"
        text_png.write_bytes(b"fake-png")

        monkeypatch.setattr(pipeline, "_generate_text_image", lambda _clip, _idx: str(text_png))

        result = pipeline.build_composite_command(
            timeline_data={
                "duration_ms": 5000,
                "layers": [
                    {
                        "id": "layer1",
                        "type": "content",
                        "visible": True,
                        "clips": [
                            {
                                "id": "text1",
                                "start_ms": 0,
                                "duration_ms": 2000,
                                "text_content": "fade me",
                                "transform": {"x": 0, "y": 0},
                                "effects": {"fade_in_ms": 500, "fade_out_ms": 500},
                            }
                        ],
                    }
                ],
            },
            assets={},
            duration_ms=5000,
            output_path=str(tmp_path / "out.mp4"),
        )

        assert result is not None
        cmd, _generated_files = result
        text_input_idx = cmd.index(str(text_png))
        assert cmd[text_input_idx - 5 : text_input_idx + 1] == [
            "-loop",
            "1",
            "-framerate",
            str(pipeline.fps),
            "-i",
            str(text_png),
        ]

    def test_generate_text_image_offsets_negative_glyph_bbox(self, monkeypatch, temp_output_dir):
        """Text PNG generation should compensate for negative font bbox offsets."""

        class FakeFont:
            def getbbox(self, _text: str) -> tuple[int, int, int, int]:
                return (-4, -12, 96, 28)

        class FakeImage:
            def __init__(self, size: tuple[int, int]):
                self.size = size

            def putalpha(self, _mask) -> None:
                return None

            def rotate(self, *_args, **_kwargs):
                return self

            def save(self, path: str, _format: str) -> None:
                Path(path).write_bytes(b"fake-png")

        draw_calls: list[tuple[tuple[float, float], str]] = []

        class FakeDraw:
            def rectangle(self, *_args, **_kwargs) -> None:
                return None

            def text(self, position, text, **_kwargs) -> None:
                draw_calls.append((position, text))

        fake_font = FakeFont()

        monkeypatch.setattr(
            pipeline_module.ImageFont, "truetype", lambda *_args, **_kwargs: fake_font
        )
        monkeypatch.setattr(pipeline_module.ImageFont, "load_default", lambda: fake_font)
        monkeypatch.setattr(
            pipeline_module.Image,
            "new",
            lambda _mode, size, _color: FakeImage(size),
        )
        monkeypatch.setattr(pipeline_module.ImageDraw, "Draw", lambda _image: FakeDraw())

        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        output_path = pipeline._generate_text_image(
            {
                "text_content": "テスト",
                "text_style": {
                    "fontSize": 48,
                    "textAlign": "left",
                    "lineHeight": 1.0,
                },
            },
            text_idx=0,
        )

        assert output_path is not None
        assert Path(output_path).exists()
        assert draw_calls

        main_text_position = draw_calls[0][0]
        assert main_text_position == (4.0, 12.0)

    def test_build_composite_command_includes_clip_with_freeze_only_in_export_range(
        self, monkeypatch, tmp_path
    ):
        """Clip whose base duration ends before export range but whose freeze_frame_ms
        extension overlaps the export window must NOT be skipped.

        Regression test for #107: the loop entry guard calculated clip_end as
        start_ms + duration_ms, ignoring freeze_frame_ms. This caused freeze-only
        overlap clips to be silently dropped, producing black frames.
        """
        pipeline = RenderPipeline()
        pipeline.output_dir = str(tmp_path)

        # Clip: base duration 1000-3000ms, freeze extends to 6000ms
        # Export window: 4000-6000ms (only the freeze portion overlaps)
        asset_path = str(tmp_path / "video.mp4")
        Path(asset_path).write_bytes(b"fake")

        result = pipeline.build_composite_command(
            timeline_data={
                "duration_ms": 6000,
                "export_start_ms": 4000,
                "export_end_ms": 6000,
                "layers": [
                    {
                        "id": "layer1",
                        "type": "content",
                        "visible": True,
                        "clips": [
                            {
                                "id": "clip1",
                                "start_ms": 1000,
                                "duration_ms": 2000,
                                "in_point_ms": 0,
                                "out_point_ms": 2000,
                                "freeze_frame_ms": 3000,
                                "asset_id": "asset1",
                                "transform": {
                                    "x": 0,
                                    "y": 0,
                                    "scale": 1.0,
                                    "rotation": 0,
                                    "width": 1920,
                                    "height": 1080,
                                },
                                "effects": {"opacity": 1.0},
                            }
                        ],
                    }
                ],
            },
            assets={"asset1": asset_path},
            duration_ms=6000,
            output_path=str(tmp_path / "out.mp4"),
        )

        # The clip must NOT be skipped — result should be non-None and
        # the ffmpeg command must reference the asset input.
        assert result is not None, (
            "build_composite_command returned None — freeze-extended clip was skipped"
        )
        cmd, _generated_files = result
        assert asset_path in cmd, (
            "Asset path not found in ffmpeg command — clip with freeze_frame_ms "
            "overlapping export range was incorrectly filtered out"
        )
        # The filter_complex should contain tpad for the freeze extension
        filter_complex_str = " ".join(cmd)
        assert "tpad=" in filter_complex_str, (
            "tpad filter missing — freeze frame extension not applied"
        )
        # For freeze-frame clips, trim is moved to input-level -ss/-to
        # (FFmpeg 7.x bug: tpad is silently ignored after trim).
        # Verify that -ss and -to appear in the command before -i.
        cmd_str = " ".join(cmd)
        assert "-ss " in cmd_str, "-ss flag missing — freeze-frame clip should use input-level trim"
        assert "-to " in cmd_str, "-to flag missing — freeze-frame clip should use input-level trim"

    def test_build_clip_filter_freeze_uses_input_level_trim(self):
        """Freeze-frame clips must use input-level -ss/-to instead of
        filter-level trim, because FFmpeg 7.x silently ignores tpad
        after trim.  Regression test for #107."""
        pipeline = RenderPipeline()

        filter_str, input_prefix = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 5000,
                "duration_ms": 2000,
                "in_point_ms": 10000,
                "out_point_ms": 12000,
                "freeze_frame_ms": 3000,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 1920,
                    "height": 1080,
                },
                "effects": {"opacity": 1.0},
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=10000,
            export_start_ms=0,
            export_end_ms=10000,
            is_still_image=False,
        )

        # Filter must NOT contain trim (it's at input level)
        assert "trim=" not in filter_str, (
            "trim filter found in filter chain — freeze-frame clips "
            "must use input-level -ss/-to to avoid FFmpeg 7.x tpad bug"
        )
        # Filter must contain tpad
        assert "tpad=stop_mode=clone:stop_duration=3.0" in filter_str
        # Input prefix must have -ss and -to
        assert len(input_prefix) == 4, f"Expected [-ss, X, -to, Y], got {input_prefix}"
        assert input_prefix[0] == "-ss"
        assert input_prefix[2] == "-to"
        assert float(input_prefix[1]) == 10.0  # in_point_ms / 1000
        assert float(input_prefix[3]) == 12.0  # out_point_ms / 1000

    def test_build_clip_filter_no_freeze_uses_filter_trim(self):
        """Non-freeze clips must still use filter-level trim (no input prefix)."""
        pipeline = RenderPipeline()

        filter_str, input_prefix = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 3000,
                "in_point_ms": 5000,
                "out_point_ms": 8000,
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "width": 1920,
                    "height": 1080,
                },
                "effects": {"opacity": 1.0},
            },
            layer_type="content",
            base_output="0:v",
            total_duration_ms=5000,
            export_start_ms=0,
            export_end_ms=5000,
            is_still_image=False,
        )

        # Filter must contain trim (no freeze, so filter-level is fine)
        assert "trim=start=5.0:end=8.0" in filter_str
        # No tpad
        assert "tpad=" not in filter_str
        # No input prefix args
        assert input_prefix == []


class TestUndoableAction:
    """Tests for UndoableAction dataclass."""

    def test_action_creation(self):
        """Test undoable action creation."""
        action = UndoableAction(
            id="action123",
            action_type="add_clip",
            description="クリップを追加",
            data={"clip_id": "clip456", "layer_id": "layer1"},
            reverse_data={"clip_id": "clip456"},
        )
        assert action.id == "action123"
        assert action.action_type == "add_clip"
        assert action.data["clip_id"] == "clip456"


class TestUndoManager:
    """Tests for UndoManager class."""

    def test_execute_action(self):
        """Test executing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)

        assert manager.can_undo() is True
        assert manager.can_redo() is False

    def test_undo(self):
        """Test undoing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={"value": 1},
            reverse_data={"value": 0},
        )

        manager.execute(action)
        undone = manager.undo()

        assert undone is not None
        assert undone.id == "action1"
        assert manager.can_undo() is False
        assert manager.can_redo() is True

    def test_redo(self):
        """Test redoing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={"value": 1},
            reverse_data={"value": 0},
        )

        manager.execute(action)
        manager.undo()
        redone = manager.redo()

        assert redone is not None
        assert redone.id == "action1"
        assert manager.can_undo() is True
        assert manager.can_redo() is False

    def test_undo_stack_limit(self):
        """Test undo stack has a limit."""
        manager = UndoManager(max_history=5)

        # Execute more actions than limit
        for i in range(10):
            action = UndoableAction(
                id=f"action{i}",
                action_type="test",
                description=f"Action {i}",
                data={},
                reverse_data={},
            )
            manager.execute(action)

        # Should only be able to undo 5 times
        undo_count = 0
        while manager.can_undo():
            manager.undo()
            undo_count += 1

        assert undo_count == 5

    def test_new_action_clears_redo_stack(self):
        """Test that new action clears redo stack."""
        manager = UndoManager()

        # Execute and undo
        action1 = UndoableAction(
            id="action1",
            action_type="test",
            description="Action 1",
            data={},
            reverse_data={},
        )
        manager.execute(action1)
        manager.undo()

        assert manager.can_redo() is True

        # Execute new action
        action2 = UndoableAction(
            id="action2",
            action_type="test",
            description="Action 2",
            data={},
            reverse_data={},
        )
        manager.execute(action2)

        # Redo stack should be cleared
        assert manager.can_redo() is False

    def test_get_undo_description(self):
        """Test getting description of next undo action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)
        desc = manager.get_undo_description()

        assert desc == "クリップを追加"

    def test_get_redo_description(self):
        """Test getting description of next redo action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)
        manager.undo()
        desc = manager.get_redo_description()

        assert desc == "クリップを追加"

    def test_clear_history(self):
        """Test clearing undo/redo history."""
        manager = UndoManager()

        for i in range(3):
            action = UndoableAction(
                id=f"action{i}",
                action_type="test",
                description=f"Action {i}",
                data={},
                reverse_data={},
            )
            manager.execute(action)

        manager.undo()  # Create redo entry

        manager.clear()

        assert manager.can_undo() is False
        assert manager.can_redo() is False
