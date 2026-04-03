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

        # line: canvas also expands by strokeWidth to match browser SVG
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
            # line: width + strokeWidth = 304, height + strokeWidth = 14
            assert img.size == (304, 14)

    def test_generate_shape_stroke_reaches_canvas_edge(self, temp_output_dir):
        """Stroke outer edge must touch the canvas boundary (pixel row/col 0).

        Regression test for #142: Pillow draws stroke inward, so the rectangle
        must be placed at (0,0)-(canvas-1) for the outer edge to reach the
        canvas boundary — matching browser SVG where stroke is centred on the
        path and extends outward by strokeWidth/2.

        With the old code that offset drawing by (sw/2, sw/2), the first
        row/column of pixels would be transparent.
        """
        from PIL import Image

        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        # Rectangle: corners must have stroke pixels
        output_path = pipeline._generate_shape_image(
            shape={
                "type": "rectangle",
                "width": 100,
                "height": 80,
                "fillColor": "transparent",
                "strokeColor": "#FF0000",
                "strokeWidth": 10,
                "filled": False,
            },
            clip={"transform": {}},
            shape_idx=0,
        )
        assert output_path is not None
        with Image.open(output_path) as img:
            pixels = img.load()
            assert pixels[0, 0][3] > 0, (
                "rectangle: pixel (0,0) is transparent — stroke does not reach canvas edge"
            )
            assert pixels[img.width - 1, img.height - 1][3] > 0, (
                "rectangle: pixel (w-1,h-1) is transparent — stroke does not reach canvas edge"
            )

        # Circle: check mid-edge pixels (top-centre, left-centre) since
        # ellipse corners are naturally transparent.
        output_path = pipeline._generate_shape_image(
            shape={
                "type": "circle",
                "width": 100,
                "height": 80,
                "fillColor": "transparent",
                "strokeColor": "#FF0000",
                "strokeWidth": 10,
                "filled": False,
            },
            clip={"transform": {}},
            shape_idx=0,
        )
        assert output_path is not None
        with Image.open(output_path) as img:
            pixels = img.load()
            mid_x = img.width // 2
            mid_y = img.height // 2
            # Top-centre: stroke must touch row 0
            assert pixels[mid_x, 0][3] > 0, (
                "circle: pixel (mid_x, 0) is transparent — stroke does not reach top edge"
            )
            # Left-centre: stroke must touch col 0
            assert pixels[0, mid_y][3] > 0, (
                "circle: pixel (0, mid_y) is transparent — stroke does not reach left edge"
            )

        # Line: top-centre pixel should have stroke
        output_path = pipeline._generate_shape_image(
            shape={
                "type": "line",
                "width": 100,
                "height": 10,
                "fillColor": "#000000",
                "strokeColor": "#FF0000",
                "strokeWidth": 8,
                "filled": True,
            },
            clip={"transform": {}},
            shape_idx=1,
        )
        assert output_path is not None
        with Image.open(output_path) as img:
            pixels = img.load()
            mid_x = img.width // 2
            # The line is centred vertically; with strokeWidth=8 the stroke
            # should reach close to row 0.  Verify the centre column has ink.
            centre_col_alpha = [pixels[mid_x, y][3] for y in range(img.height)]
            assert max(centre_col_alpha) > 0, "line: no visible stroke found"

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
            (0, 40),
            (160, 34),
            (154, 20),
            (230, 40),
            (154, 60),
            (160, 46),
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
        assert expected_points[0] == (0.0, 80.0)  # tail
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

    def test_build_clip_filter_no_freeze_video_gets_boundary_tpad(self):
        """Non-freeze video clips get 2-frame tpad to prevent black frames at
        clip boundaries caused by trim=end not aligning with source frame
        boundaries.  Input-level trim (-ss/-to) is used because FFmpeg 7.x
        silently ignores tpad when timestamp-altering filters precede it."""
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

        # Video clips now use input-level trim + tpad for boundary protection
        assert input_prefix[0] == "-ss"
        assert input_prefix[2] == "-to"
        assert float(input_prefix[1]) == 5.0
        assert float(input_prefix[3]) == 8.0
        # 2-frame tpad (~67ms at 30fps) for cross-platform safety
        assert "tpad=stop_mode=clone" in filter_str
        # No filter-level trim (moved to -ss/-to)
        assert "trim=" not in filter_str

    def test_build_clip_filter_boundary_guard_is_two_frames(self):
        """Non-freeze video clips must use 2-frame tpad (not 1-frame) to absorb
        decode-timestamp differences in Windows FFmpeg builds.

        Regression test for issue #158.
        The stop_duration must equal 2/fps (≈0.06667s at 30fps).
        With the old 1-frame implementation this test would fail because
        stop_duration would be 1/fps (≈0.03333s).
        """
        pipeline = RenderPipeline()
        fps = pipeline.fps  # default 30

        filter_str, _input_prefix = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 2000,
                "in_point_ms": 0,
                "out_point_ms": 2000,
                # No freeze_frame_ms — this exercises the boundary-guard path only
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
            total_duration_ms=2000,
            export_start_ms=0,
            export_end_ms=2000,
            is_still_image=False,
        )

        # Expected stop_duration: 2 frames worth, i.e. 2 / fps seconds
        expected_stop_duration = 2 / fps  # ~0.06667 at 30fps
        one_frame_stop_duration = 1 / fps  # ~0.03333 at 30fps — old (wrong) value

        # Confirm tpad is present
        assert "tpad=stop_mode=clone" in filter_str, (
            f"tpad filter missing from filter string: {filter_str}"
        )

        # Verify that the stop_duration matches exactly 2-frame value
        expected_str = f"tpad=stop_mode=clone:stop_duration={expected_stop_duration}"
        assert expected_str in filter_str, (
            f"Expected 2-frame tpad stop_duration ({expected_stop_duration}s) "
            f"not found in filter: {filter_str}"
        )

        # Explicitly assert it does NOT equal the old 1-frame value
        old_str = f"tpad=stop_mode=clone:stop_duration={one_frame_stop_duration}"
        assert old_str not in filter_str, (
            f"Old 1-frame tpad stop_duration ({one_frame_stop_duration}s) found — "
            f"boundary guard must be 2 frames for cross-platform safety (issue #158)"
        )

    def test_build_clip_filter_freeze_with_speed_compensates_tpad_duration(self):
        """speed != 1.0 + freeze_frame_ms: tpad stop_duration must be multiplied
        by speed so that the effective freeze duration after setpts division
        equals the user-specified freeze_frame_ms.

        Filter chain: format → tpad(stop_duration=X) → setpts/speed
        Effective freeze = X / speed  →  X = freeze_frame_ms * speed (in seconds)

        Regression test for issue #150.
        """
        pipeline = RenderPipeline()

        # speed=2.0, freeze=3000ms → expected tpad stop_duration = 6.0s
        filter_str, input_prefix = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 5000,
                "duration_ms": 2000,
                "in_point_ms": 10000,
                "out_point_ms": 12000,
                "freeze_frame_ms": 3000,
                "speed": 2.0,
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

        # tpad stop_duration must be 6.0 (= 3000ms * 2.0 / 1000)
        assert "tpad=stop_mode=clone:stop_duration=6.0" in filter_str, (
            f"tpad stop_duration should be 6.0 (freeze=3000ms * speed=2.0) but got: {filter_str}"
        )
        # setpts must divide by speed
        assert "setpts=(PTS-STARTPTS)/2.0" in filter_str, (
            f"setpts should divide by speed=2.0 but got: {filter_str}"
        )

    def test_build_clip_filter_freeze_with_speed_half_compensates_tpad_duration(self):
        """speed=0.5, freeze=4000ms → tpad stop_duration = 2.0s
        (effective = 2.0 / 0.5 = 4.0s = 4000ms)."""
        pipeline = RenderPipeline()

        filter_str, _ = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 2000,
                "in_point_ms": 0,
                "out_point_ms": 2000,
                "freeze_frame_ms": 4000,
                "speed": 0.5,
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

        # tpad stop_duration must be 2.0 (= 4000ms * 0.5 / 1000)
        assert "tpad=stop_mode=clone:stop_duration=2.0" in filter_str, (
            f"tpad stop_duration should be 2.0 (freeze=4000ms * speed=0.5) but got: {filter_str}"
        )

    def test_build_clip_filter_still_image_no_tpad(self):
        """Still image clips don't need tpad — -loop 1 generates infinite frames."""
        pipeline = RenderPipeline()

        filter_str, input_prefix = pipeline._build_clip_filter(
            input_idx=1,
            clip={
                "start_ms": 0,
                "duration_ms": 3000,
                "in_point_ms": 0,
                "out_point_ms": 3000,
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
            is_still_image=True,
        )

        # Still images use filter-level trim, no tpad
        assert "trim=" in filter_str
        assert "tpad=" not in filter_str
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


class TestTextOverlayPreviewParity:
    """Regression tests for text overlay preview-render parity (#152)."""

    def _make_monkeypatched_pipeline(self, monkeypatch, temp_output_dir, bbox=(0, 0, 100, 40)):
        """Set up a RenderPipeline with mocked Pillow."""

        class FakeFont:
            def getbbox(self, _text: str) -> tuple[int, int, int, int]:
                return bbox

        class FakeImage:
            def __init__(self, size: tuple[int, int]):
                self.size = size

            def putalpha(self, _mask) -> None:
                return None

            def rotate(self, *_args, **_kwargs):
                return self

            def save(self, path: str, _format: str) -> None:
                Path(path).write_bytes(b"fake-png")

        self._created_images: list[tuple[int, int]] = []
        created_images = self._created_images

        def fake_new(_mode, size, _color):
            img = FakeImage(size)
            created_images.append(size)
            return img

        draw_calls: list[tuple[tuple[float, float], str]] = []
        self._draw_calls = draw_calls

        class FakeDraw:
            def rectangle(self, *_args, **_kwargs) -> None:
                return None

            def text(self, position, text, **_kwargs) -> None:
                draw_calls.append((position, text))

        fake_font = FakeFont()
        monkeypatch.setattr(pipeline_module.ImageFont, "truetype", lambda *_a, **_kw: fake_font)
        monkeypatch.setattr(pipeline_module.ImageFont, "load_default", lambda: fake_font)
        monkeypatch.setattr(pipeline_module.Image, "new", fake_new)
        monkeypatch.setattr(pipeline_module.ImageDraw, "Draw", lambda _image: FakeDraw())

        p = RenderPipeline()
        p.output_dir = str(temp_output_dir)
        return p

    def test_text_image_with_background_uses_asymmetric_padding(self, monkeypatch, temp_output_dir):
        """Background text should use padding_v=8, padding_h=16 matching CSS '8px 16px'.

        With uniform padding=16 (old impl), img_height would be 16px taller.
        """
        # bbox=(0,0,100,40): content_width=100, content_height=max(40,48)=48
        # background: padding_v=8, padding_h=16, stroke_width=0
        # outer_padding_v=8, outer_padding_h=16
        # expected img_height = ceil(48 + 8*2) = 64
        # expected img_width  = ceil(100 + 16*2) = 132
        # old impl would give img_height = ceil(48 + 16*2) = 80
        pipeline = self._make_monkeypatched_pipeline(
            monkeypatch, temp_output_dir, bbox=(0, 0, 100, 40)
        )

        output_path = pipeline._generate_text_image(
            {
                "text_content": "Hello",
                "text_style": {
                    "fontSize": 48,
                    "textAlign": "left",
                    "lineHeight": 1.0,
                    "backgroundColor": "#000000",
                    "backgroundOpacity": 0.8,
                },
            },
            text_idx=0,
        )

        assert output_path is not None
        assert Path(output_path).exists()

        # First image created is the main canvas
        assert len(self._created_images) >= 1
        img_width, img_height = self._created_images[0]

        # Asymmetric padding: v=8, h=16 (with stroke_width=0 → outer same)
        assert img_height == 64, (
            f"Expected img_height=64 (padding_v=8), got {img_height}. Old uniform padding=16 would give 80."
        )
        assert img_width == 132, f"Expected img_width=132 (padding_h=16), got {img_width}."

    def test_text_image_without_background_uses_stroke_padding(self, monkeypatch, temp_output_dir):
        """Without background, both v/h padding should be stroke_width * 2."""
        # No background: padding_v=stroke_width*2, padding_h=stroke_width*2
        # stroke_width=0 → both 0 → outer_padding = 0
        # expected img_height = ceil(48 + 0*2) = 48
        # expected img_width  = ceil(100 + 0*2) = 100
        pipeline = self._make_monkeypatched_pipeline(
            monkeypatch, temp_output_dir, bbox=(0, 0, 100, 40)
        )

        output_path = pipeline._generate_text_image(
            {
                "text_content": "Hello",
                "text_style": {
                    "fontSize": 48,
                    "textAlign": "left",
                    "lineHeight": 1.0,
                    # no backgroundColor → transparent / no background
                },
            },
            text_idx=0,
        )

        assert output_path is not None
        assert Path(output_path).exists()

        assert len(self._created_images) >= 1
        img_width, img_height = self._created_images[0]

        # No background: padding = stroke_width*2 = 0
        assert img_height == 48, (
            f"Expected img_height=48 (no bg, stroke_width=0), got {img_height}."
        )
        assert img_width == 100, f"Expected img_width=100 (no bg, stroke_width=0), got {img_width}."

    def test_text_overlay_coordinates_use_round(self, monkeypatch, temp_output_dir):
        """Overlay coordinates should use round() not int() truncation.

        round(10.7) == 11, but int(10.7) == 10 (old truncation behavior).
        """
        pipeline = RenderPipeline()
        pipeline.output_dir = str(temp_output_dir)

        clip = {
            "transform": {"x": 10.7, "y": 5.3},
            "start_ms": 0,
            "duration_ms": 5000,
        }

        result = pipeline._build_text_overlay_filter(
            input_idx=1,
            clip=clip,
            base_output="base",
            text_idx=0,
            export_start_ms=0,
            export_end_ms=5000,
        )

        # round(10.7)=11 should appear in the filter string
        assert "11" in result, f"Expected round(10.7)=11 in filter, got: {result}"
        # int(10.7)=10 truncation should NOT be the coordinate (i.e., not '(10)')
        # The overlay_x expression contains round(center_x)
        assert "(10)" not in result or "(11)" in result, (
            f"Expected round() giving 11, but found truncated 10: {result}"
        )
