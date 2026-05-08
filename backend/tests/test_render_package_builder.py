import copy
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import src.render.package_builder as package_builder_module
import src.render.pipeline as pipeline_module
from src.api.ai_video import _add_avatar_dodge_keyframes
from src.render.package_builder import RenderPackageBuilder
from src.render.pipeline import RenderPipeline


def _generate_sine_audio(path: Path, duration_s: float, frequency: int = 440) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration_s}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _generate_green_screen_video(path: Path, duration_s: float, width: int, height: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x00ff00:s={width}x{height}:r=30:d={duration_s}",
            "-vf",
            "drawbox=x=40:y=20:w=80:h=80:color=0xff3366:t=fill",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _framemd5(path: Path) -> str:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "framemd5",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return "\n".join(line for line in result.stdout.splitlines() if not line.startswith("#"))


def _minimal_image_timeline(duration_ms: int = 1200) -> dict:
    return {
        "version": "1.0",
        "duration_ms": duration_ms,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-background",
                        "asset_id": "asset-image-1",
                        "start_ms": 0,
                        "duration_ms": duration_ms,
                        "in_point_ms": 0,
                        "out_point_ms": duration_ms,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                    }
                ],
            }
        ],
        "audio_tracks": [],
        "groups": [],
        "markers": [],
    }


@pytest.mark.asyncio
async def test_render_package_composite_script_omits_server_thread_cap(
    temp_output_dir: Path,
) -> None:
    image_path = temp_output_dir / "background.png"
    Image.new("RGBA", (320, 180), (24, 78, 164, 255)).save(image_path)

    builder = RenderPackageBuilder(
        project_id="proj-thread-policy",
        project_name="Thread Policy",
        width=320,
        height=180,
        fps=30,
    )
    try:
        await builder.build(
            _minimal_image_timeline(),
            {"asset-image-1": str(image_path)},
            {"asset-image-1": "background.png"},
        )

        composite_script = (
            Path(builder.package_dir) / "scripts" / "02_composite_video.sh"
        ).read_text()
        manifest = json.loads((Path(builder.package_dir) / "manifest.json").read_text())
        readme = (Path(builder.package_dir) / "README.txt").read_text()

        assert "-threads" not in composite_script
        assert manifest["execution_policy"]["server_ffmpeg_threads"] == (
            package_builder_module.settings.render_ffmpeg_threads
        )
        assert manifest["execution_policy"]["package_ffmpeg_threads"] == "auto"
        assert "omit the server-side FFmpeg thread cap" in "\n".join(
            manifest["execution_policy"]["notes"]
        )
        assert "intentionally does not pin FFmpeg thread count" in readme
    finally:
        builder.cleanup()


@pytest.mark.asyncio
async def test_chunked_render_package_composite_script_omits_server_thread_cap(
    temp_output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = temp_output_dir / "background.png"
    Image.new("RGBA", (320, 180), (24, 78, 164, 255)).save(image_path)

    monkeypatch.setattr(
        package_builder_module,
        "analyze_timeline_for_memory",
        lambda *_args, **_kwargs: {
            "needs_chunking": True,
            "recommended_chunks": 3,
            "chunk_duration_s": 1,
        },
    )

    builder = RenderPackageBuilder(
        project_id="proj-thread-policy-chunked",
        project_name="Thread Policy Chunked",
        width=320,
        height=180,
        fps=30,
    )
    try:
        await builder.build(
            _minimal_image_timeline(duration_ms=2500),
            {"asset-image-1": str(image_path)},
            {"asset-image-1": "background.png"},
        )

        chunk_script = (
            Path(builder.package_dir) / "scripts" / "01_render_chunk_000.sh"
        ).read_text()
        assert "-threads" not in chunk_script
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_output_matches_server_export(temp_output_dir: Path) -> None:
    image_path = temp_output_dir / "background.png"
    Image.new("RGBA", (320, 180), (24, 78, 164, 255)).save(image_path)

    audio_path = temp_output_dir / "tone.wav"
    _generate_sine_audio(audio_path, duration_s=1.2)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1200,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-background",
                        "asset_id": "asset-image-1",
                        "start_ms": 0,
                        "duration_ms": 1200,
                        "in_point_ms": 0,
                        "out_point_ms": 1200,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "crop": {
                            "top": 0.0,
                            "right": 0.08,
                            "bottom": 0.0,
                            "left": 0.05,
                        },
                        "highlights": [
                            {
                                "x_norm": 0.5,
                                "y_norm": 0.45,
                                "w_norm": 0.22,
                                "h_norm": 0.14,
                                "time_ms": 300,
                                "duration_ms": 250,
                                "color": "#ffcc00",
                                "thickness": 4,
                            }
                        ],
                        "effects": {
                            "opacity": 0.85,
                            "fade_in_ms": 120,
                            "fade_out_ms": 120,
                        },
                    }
                ],
            },
            {
                "id": "layer-shape",
                "name": "Shape",
                "type": "effects",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-shape",
                        "start_ms": 100,
                        "duration_ms": 700,
                        "transform": {
                            "x": 60,
                            "y": -10,
                            "width": 80,
                            "height": 80,
                            "scale": 1.0,
                            "rotation": 18,
                        },
                        "effects": {
                            "opacity": 0.75,
                        },
                        "shape": {
                            "type": "rectangle",
                            "fillColor": "#ff3366",
                            "strokeColor": "#ffffff",
                            "strokeWidth": 3,
                            "filled": True,
                        },
                    }
                ],
            },
            {
                "id": "layer-text",
                "name": "Text",
                "type": "text",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-text",
                        "start_ms": 240,
                        "duration_ms": 600,
                        "transform": {
                            "x": 0,
                            "y": 48,
                            "rotation": 0,
                        },
                        "effects": {
                            "opacity": 1.0,
                            "fade_in_ms": 120,
                            "fade_out_ms": 120,
                        },
                        "text_content": "Package parity",
                        "text_style": {
                            "fontFamily": "Noto Sans JP",
                            "fontSize": 28,
                            "fontWeight": "bold",
                            "color": "#ffffff",
                            "backgroundColor": "#000000",
                            "backgroundOpacity": 0.45,
                            "strokeColor": "#0f172a",
                            "strokeWidth": 2,
                            "textAlign": "center",
                            "lineHeight": 1.2,
                        },
                    }
                ],
            },
        ],
        "audio_tracks": [
            {
                "id": "track-audio",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-audio",
                        "asset_id": "asset-audio-1",
                        "start_ms": 0,
                        "duration_ms": 1200,
                        "in_point_ms": 0,
                        "out_point_ms": 1200,
                        "volume": 0.8,
                        "fade_in_ms": 100,
                        "fade_out_ms": 100,
                        "volume_keyframes": [
                            {"time_ms": 0, "value": 0.4},
                            {"time_ms": 600, "value": 1.0},
                            {"time_ms": 1200, "value": 0.5},
                        ],
                    }
                ],
            }
        ],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-image-1": str(image_path),
        "asset-audio-1": str(audio_path),
    }

    server_output = temp_output_dir / "server_export.mp4"
    pipeline = RenderPipeline(
        job_id="parity-server",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Test",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-image-1": "background.png",
                "asset-audio-1": "tone.wav",
            },
        )
        extract_dir = temp_output_dir / "package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_output_matches_server_export_for_partial_media_render(
    temp_output_dir: Path,
) -> None:
    background_path = temp_output_dir / "bg.png"
    Image.new("RGBA", (320, 180), (15, 23, 42, 255)).save(background_path)

    video_path = temp_output_dir / "greenscreen.mp4"
    _generate_green_screen_video(video_path, duration_s=1.6, width=160, height=120)

    audio_path = temp_output_dir / "tone.wav"
    _generate_sine_audio(audio_path, duration_s=1.6)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1600,
        "export_start_ms": 200,
        "export_end_ms": 1400,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1600,
                        "in_point_ms": 0,
                        "out_point_ms": 1600,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {
                            "opacity": 1.0,
                        },
                    }
                ],
            },
            {
                "id": "layer-avatar",
                "name": "Avatar",
                "type": "avatar",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-avatar",
                        "asset_id": "asset-avatar",
                        "start_ms": 0,
                        "duration_ms": 900,
                        "freeze_frame_ms": 300,
                        "in_point_ms": 0,
                        "out_point_ms": 900,
                        "transform": {
                            "x": -36,
                            "y": 6,
                            "width": 160,
                            "height": 120,
                            "scale": 1.0,
                            "rotation": -8,
                        },
                        "crop": {
                            "top": 0.0,
                            "right": 0.05,
                            "bottom": 0.0,
                            "left": 0.05,
                        },
                        "effects": {
                            "opacity": 0.9,
                            "fade_in_ms": 120,
                            "fade_out_ms": 180,
                            "chroma_key": {
                                "enabled": True,
                                "color": "#00FF00",
                                "similarity": 0.4,
                                "blend": 0.1,
                            },
                        },
                    }
                ],
            },
        ],
        "audio_tracks": [
            {
                "id": "track-audio",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-audio",
                        "asset_id": "asset-audio",
                        "start_ms": 0,
                        "duration_ms": 1600,
                        "in_point_ms": 0,
                        "out_point_ms": 1600,
                        "volume": 0.7,
                        "fade_in_ms": 150,
                        "fade_out_ms": 150,
                    }
                ],
            }
        ],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(background_path),
        "asset-avatar": str(video_path),
        "asset-audio": str(audio_path),
    }

    render_duration_ms = timeline_data["export_end_ms"] - timeline_data["export_start_ms"]
    server_output = temp_output_dir / "server_partial.mp4"
    server_timeline = copy.deepcopy(timeline_data)
    server_timeline["duration_ms"] = render_duration_ms
    pipeline = RenderPipeline(
        job_id="parity-partial",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(server_timeline, assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Partial",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "background.png",
                "asset-avatar": "greenscreen.mp4",
                "asset-audio": "tone.wav",
            },
        )
        extract_dir = temp_output_dir / "partial-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)

        duration_probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(package_output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert abs(float(duration_probe.stdout.strip()) - (render_duration_ms / 1000)) < 0.05
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_output_matches_server_export_for_keyframes_and_transitions(
    temp_output_dir: Path,
) -> None:
    background_path = temp_output_dir / "bg.png"
    Image.new("RGBA", (320, 180), (5, 10, 30, 255)).save(background_path)

    sprite_path = temp_output_dir / "sprite.png"
    Image.new("RGBA", (160, 120), (255, 120, 40, 255)).save(sprite_path)

    audio_path = temp_output_dir / "tone.wav"
    _generate_sine_audio(audio_path, duration_s=1.5)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1500,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1500,
                        "in_point_ms": 0,
                        "out_point_ms": 1500,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
            {
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-sprite",
                        "asset_id": "asset-sprite",
                        "start_ms": 0,
                        "duration_ms": 1500,
                        "in_point_ms": 0,
                        "out_point_ms": 1500,
                        "transform": {
                            "x": -80,
                            "y": 0,
                            "width": 160,
                            "height": 120,
                            "scale": 0.85,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 0.9},
                        "transition_in": {"type": "slide_left", "duration_ms": 180},
                        "transition_out": {"type": "slide_right", "duration_ms": 220},
                        "keyframes": [
                            {
                                "time_ms": 0,
                                "transform": {
                                    "x": -80,
                                    "y": -15,
                                    "scale": 0.85,
                                    "rotation": -8,
                                },
                                "opacity": 0.6,
                            },
                            {
                                "time_ms": 700,
                                "transform": {
                                    "x": 40,
                                    "y": 12,
                                    "scale": 1.1,
                                    "rotation": 10,
                                },
                                "opacity": 1.0,
                            },
                            {
                                "time_ms": 1500,
                                "transform": {
                                    "x": -10,
                                    "y": 28,
                                    "scale": 0.95,
                                    "rotation": 0,
                                },
                                "opacity": 0.75,
                            },
                        ],
                    }
                ],
            },
            {
                "id": "layer-text",
                "name": "Text",
                "type": "text",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-text",
                        "start_ms": 220,
                        "duration_ms": 900,
                        "transform": {
                            "x": 0,
                            "y": 46,
                            "rotation": 0,
                            "scale": 1.0,
                        },
                        "effects": {"opacity": 1.0},
                        "transition_in": {"type": "slide_up", "duration_ms": 140},
                        "transition_out": {"type": "slide_down", "duration_ms": 160},
                        "text_content": "Keyframe parity",
                        "text_style": {
                            "fontFamily": "Noto Sans JP",
                            "fontSize": 26,
                            "fontWeight": "bold",
                            "color": "#ffffff",
                            "backgroundColor": "#000000",
                            "backgroundOpacity": 0.4,
                            "strokeColor": "#0f172a",
                            "strokeWidth": 2,
                            "textAlign": "center",
                            "lineHeight": 1.2,
                        },
                    }
                ],
            },
        ],
        "audio_tracks": [
            {
                "id": "track-audio",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-audio",
                        "asset_id": "asset-audio",
                        "start_ms": 0,
                        "duration_ms": 1500,
                        "in_point_ms": 0,
                        "out_point_ms": 1500,
                        "volume": 0.7,
                        "fade_in_ms": 120,
                        "fade_out_ms": 150,
                    }
                ],
            }
        ],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(background_path),
        "asset-sprite": str(sprite_path),
        "asset-audio": str(audio_path),
    }

    server_output = temp_output_dir / "server_keyframes.mp4"
    pipeline = RenderPipeline(
        job_id="parity-keyframes",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Keyframes",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "background.png",
                "asset-sprite": "sprite.png",
                "asset-audio": "tone.wav",
            },
        )
        extract_dir = temp_output_dir / "keyframe-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)

        manifest = json.loads((package_root / "manifest.json").read_text())
        assert manifest["requirements"]["expected_ffmpeg_version"]
        assert manifest["requirements"]["docker_image"] == "jrottenberg/ffmpeg:6.1-ubuntu2204"
        assert (package_root / "render-docker.sh").exists()
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_matches_chunked_server_export(
    temp_output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_path = temp_output_dir / "chunked-bg.png"
    Image.new("RGBA", (320, 180), (18, 52, 86, 255)).save(background_path)

    audio_path = temp_output_dir / "chunked-tone.wav"
    _generate_sine_audio(audio_path, duration_s=2.4)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 2400,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 2400,
                        "in_point_ms": 0,
                        "out_point_ms": 2400,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
            {
                "id": "layer-text",
                "name": "Text",
                "type": "text",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-text",
                        "start_ms": 300,
                        "duration_ms": 1500,
                        "transform": {
                            "x": 0,
                            "y": 30,
                            "rotation": 0,
                            "scale": 1.0,
                        },
                        "effects": {"opacity": 1.0, "fade_in_ms": 150, "fade_out_ms": 150},
                        "text_content": "Chunk parity",
                        "text_style": {
                            "fontFamily": "Noto Sans JP",
                            "fontSize": 28,
                            "fontWeight": "bold",
                            "color": "#ffffff",
                            "backgroundColor": "#000000",
                            "backgroundOpacity": 0.35,
                            "strokeColor": "#0f172a",
                            "strokeWidth": 2,
                            "textAlign": "center",
                            "lineHeight": 1.2,
                        },
                    }
                ],
            },
        ],
        "audio_tracks": [
            {
                "id": "track-audio",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-audio",
                        "asset_id": "asset-audio",
                        "start_ms": 0,
                        "duration_ms": 2400,
                        "in_point_ms": 0,
                        "out_point_ms": 2400,
                        "volume": 0.8,
                        "fade_in_ms": 120,
                        "fade_out_ms": 180,
                    }
                ],
            }
        ],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(background_path),
        "asset-audio": str(audio_path),
    }

    forced_mem_info = {
        "estimated_bytes": 1,
        "estimated_mb": 1,
        "container_limit_bytes": 1,
        "container_limit_mb": 1,
        "safety_limit_bytes": 1,
        "safety_limit_mb": 1,
        "duration_s": 2.4,
        "total_clips": 3,
        "num_layers_with_clips": 2,
        "has_chroma_key": False,
        "needs_chunking": True,
        "recommended_chunks": 2,
        "chunk_duration_s": 1,
        "max_safe_duration_s": 1,
    }
    monkeypatch.setattr(
        pipeline_module,
        "analyze_timeline_for_memory",
        lambda *_args, **_kwargs: forced_mem_info,
    )
    monkeypatch.setattr(
        package_builder_module,
        "analyze_timeline_for_memory",
        lambda *_args, **_kwargs: forced_mem_info,
    )

    server_output = temp_output_dir / "server_chunked.mp4"
    pipeline = RenderPipeline(
        job_id="parity-chunked",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Chunked",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "background.png",
                "asset-audio": "tone.wav",
            },
        )
        extract_dir = temp_output_dir / "chunked-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_matches_server_export_for_avatar_dodge_keyframes(
    temp_output_dir: Path,
) -> None:
    background_path = temp_output_dir / "avatar-dodge-bg.png"
    Image.new("RGBA", (320, 180), (30, 41, 59, 255)).save(background_path)

    content_path = temp_output_dir / "avatar-dodge-content.png"
    Image.new("RGBA", (220, 140), (59, 130, 246, 255)).save(content_path)

    avatar_path = temp_output_dir / "avatar-dodge-avatar.png"
    Image.new("RGBA", (96, 96), (248, 113, 113, 255)).save(avatar_path)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1800,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
            {
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-content",
                        "asset_id": "asset-content",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "transform": {
                            "x": -10,
                            "y": 0,
                            "width": 220,
                            "height": 140,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                        "highlights": [
                            {
                                "time_ms": 600,
                                "duration_ms": 400,
                                "x_norm": 0.58,
                                "y_norm": 0.68,
                                "w_norm": 0.18,
                                "h_norm": 0.14,
                                "color": "#ffcc00",
                                "thickness": 4,
                            }
                        ],
                    }
                ],
            },
            {
                "id": "layer-avatar",
                "name": "Avatar",
                "type": "avatar",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-avatar",
                        "asset_id": "asset-avatar",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "transform": {
                            "x": 120,
                            "y": 80,
                            "width": 96,
                            "height": 96,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
        ],
        "audio_tracks": [],
        "groups": [],
        "markers": [],
    }

    await _add_avatar_dodge_keyframes(timeline_data)
    avatar_clip = timeline_data["layers"][2]["clips"][0]
    assert len(avatar_clip.get("keyframes", [])) >= 2

    assets = {
        "asset-bg": str(background_path),
        "asset-content": str(content_path),
        "asset-avatar": str(avatar_path),
    }

    server_output = temp_output_dir / "server_avatar_dodge.mp4"
    pipeline = RenderPipeline(
        job_id="parity-avatar-dodge",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Avatar Dodge",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "background.png",
                "asset-content": "content.png",
                "asset-avatar": "avatar.png",
            },
        )
        extract_dir = temp_output_dir / "avatar-dodge-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_matches_server_export_for_multitrack_audio_dynamics(
    temp_output_dir: Path,
) -> None:
    background_path = temp_output_dir / "audio-parity-bg.png"
    Image.new("RGBA", (320, 180), (2, 132, 199, 255)).save(background_path)

    narration_path = temp_output_dir / "narration.wav"
    bgm_path = temp_output_dir / "bgm.wav"
    se_path = temp_output_dir / "se.wav"
    _generate_sine_audio(narration_path, duration_s=1.8, frequency=660)
    _generate_sine_audio(bgm_path, duration_s=1.8, frequency=220)
    _generate_sine_audio(se_path, duration_s=0.5, frequency=990)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1800,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            }
        ],
        "audio_tracks": [
            {
                "id": "track-narration",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-narration",
                        "asset_id": "asset-narration",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "volume": 0.9,
                        "fade_in_ms": 120,
                        "fade_out_ms": 150,
                        "volume_keyframes": [
                            {"time_ms": 0, "value": 0.3},
                            {"time_ms": 500, "value": 1.0},
                            {"time_ms": 1800, "value": 0.75},
                        ],
                    }
                ],
            },
            {
                "id": "track-bgm",
                "type": "bgm",
                "volume": 0.35,
                "muted": False,
                "solo": False,
                "ducking": {
                    "enabled": True,
                    "duck_to": 0.2,
                    "attack_ms": 150,
                    "release_ms": 400,
                },
                "clips": [
                    {
                        "id": "clip-bgm",
                        "asset_id": "asset-bgm",
                        "start_ms": 0,
                        "duration_ms": 1800,
                        "in_point_ms": 0,
                        "out_point_ms": 1800,
                        "volume": 1.0,
                        "fade_in_ms": 80,
                        "fade_out_ms": 160,
                    }
                ],
            },
            {
                "id": "track-se",
                "type": "se",
                "volume": 0.8,
                "muted": False,
                "solo": False,
                "clips": [
                    {
                        "id": "clip-se",
                        "asset_id": "asset-se",
                        "start_ms": 650,
                        "duration_ms": 500,
                        "in_point_ms": 0,
                        "out_point_ms": 500,
                        "volume": 0.85,
                        "fade_in_ms": 40,
                        "fade_out_ms": 100,
                    }
                ],
            },
        ],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(background_path),
        "asset-narration": str(narration_path),
        "asset-bgm": str(bgm_path),
        "asset-se": str(se_path),
    }

    server_output = temp_output_dir / "server_audio_dynamics.mp4"
    pipeline = RenderPipeline(
        job_id="parity-audio-dynamics",
        project_id="project-parity",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    builder = RenderPackageBuilder(
        project_id="project-parity",
        project_name="Parity Audio Dynamics",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "background.png",
                "asset-narration": "narration.wav",
                "asset-bgm": "bgm.wav",
                "asset-se": "se.wav",
            },
        )
        extract_dir = temp_output_dir / "audio-dynamics-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()
        assert _framemd5(server_output) == _framemd5(package_output)
    finally:
        builder.cleanup()


def _ffprobe_video_size(path):
    """Return (width, height) of the first video stream."""
    import subprocess
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


@pytest.mark.asyncio
async def test_render_package_transform_scale_applied_for_image_clip(
    temp_output_dir: Path,
) -> None:
    """Regression test for #213: transform.scale must be reflected in the
    download render package filtergraph (image clip with explicit width/height).

    `image_with_explicit_size` clips (still-image + asset_id + explicit width/height)
    previously entered a branch in _build_clip_filter that emitted a hard-coded
    ``scale=w='max(2,trunc(W))':h='max(2,trunc(H))'`` ignoring the scale parameter
    entirely.  The fix must multiply W/H by the scale_expr.

    This test verifies the filtergraph written into the package contains the
    scale multiplier — no FFmpeg execution required (unit-level, fast).
    """
    bg_path = temp_output_dir / "bg_scale.png"
    Image.new("RGBA", (320, 180), (10, 20, 40, 255)).save(bg_path)

    sprite_path = temp_output_dir / "sprite_scale.png"
    Image.new("RGBA", (160, 90), (255, 0, 128, 255)).save(sprite_path)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1000,
        "layers": [
            {
                "id": "layer-bg",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "in_point_ms": 0,
                        "out_point_ms": 1000,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
            {
                "id": "layer-sprite",
                "name": "Sprite",
                "type": "content",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-sprite",
                        "asset_id": "asset-sprite",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "in_point_ms": 0,
                        "out_point_ms": 1000,
                        # explicit width/height + scale=0.5 — the condition that
                        # previously hit `image_with_explicit_size` and ignored scale.
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 160,
                            "height": 90,
                            "scale": 0.5,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
        ],
        "audio_tracks": [],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(bg_path),
        "asset-sprite": str(sprite_path),
    }

    builder = RenderPackageBuilder(
        project_id="project-scale",
        project_name="Scale Test",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "bg_scale.png",
                "asset-sprite": "sprite_scale.png",
            },
        )
        extract_dir = temp_output_dir / "scale-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))

        # Find the composite filtergraph file
        filtergraph_files = list((package_root / "scripts").glob("*.filtergraph"))
        assert filtergraph_files, "No .filtergraph file found in package scripts/"
        filtergraph_content = filtergraph_files[0].read_text()

        # The scale filter for the sprite clip must multiply width/height by
        # the scale expression (0.5 in this case).  A plain ``trunc(160)`` or
        # ``trunc(90)`` without multiplication means scale is being ignored.
        # We check that "160*" or "*(0.5)" appears, which rules out the bare
        # ``scale=w='max(2,trunc(160))':h='max(2,trunc(90))'`` bug pattern.
        assert "160*(" in filtergraph_content or "*(0.5)" in filtergraph_content, (
            "Filtergraph does not contain scale multiplication for the sprite clip.\n"
            "Expected something like scale=w='max(2,trunc(160*(0.5)))' but got:\n"
            + filtergraph_content
        )
        # The bare (un-scaled) pattern must NOT appear for the sprite input.
        # trunc(160)) without a following '*' means scale is hard-coded.
        assert "trunc(160))" not in filtergraph_content, (
            "Filtergraph still contains hard-coded 'trunc(160))' without scale multiplier.\n"
            "This means transform.scale=0.5 is being ignored for image_with_explicit_size clips.\n"
            + filtergraph_content
        )
    finally:
        builder.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
async def test_render_package_transform_scale_image_clip_output_matches_server(
    temp_output_dir: Path,
) -> None:
    """End-to-end parity test for #213: package render.sh output must match
    the server-side RenderPipeline output when transform.scale != 1.0 for an
    image clip with explicit width/height (image_with_explicit_size path).
    """
    bg_path = temp_output_dir / "bg_scale_e2e.png"
    Image.new("RGBA", (320, 180), (10, 20, 40, 255)).save(bg_path)

    sprite_path = temp_output_dir / "sprite_scale_e2e.png"
    Image.new("RGBA", (160, 90), (255, 0, 128, 255)).save(sprite_path)

    timeline_data = {
        "version": "1.0",
        "duration_ms": 1000,
        "layers": [
            {
                "id": "layer-bg",
                "name": "Background",
                "type": "background",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-bg",
                        "asset_id": "asset-bg",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "in_point_ms": 0,
                        "out_point_ms": 1000,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 320,
                            "height": 180,
                            "scale": 1.0,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
            {
                "id": "layer-sprite",
                "name": "Sprite",
                "type": "content",
                "visible": True,
                "clips": [
                    {
                        "id": "clip-sprite",
                        "asset_id": "asset-sprite",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "in_point_ms": 0,
                        "out_point_ms": 1000,
                        "transform": {
                            "x": 0,
                            "y": 0,
                            "width": 160,
                            "height": 90,
                            "scale": 0.5,
                            "rotation": 0,
                        },
                        "effects": {"opacity": 1.0},
                    }
                ],
            },
        ],
        "audio_tracks": [],
        "groups": [],
        "markers": [],
    }

    assets = {
        "asset-bg": str(bg_path),
        "asset-sprite": str(sprite_path),
    }

    # ---- Server-side render ----
    server_output = temp_output_dir / "server_scale_e2e.mp4"
    pipeline = RenderPipeline(
        job_id="scale-server-e2e",
        project_id="project-scale-e2e",
        width=320,
        height=180,
        fps=30,
    )
    await pipeline.render(copy.deepcopy(timeline_data), assets, str(server_output))
    assert server_output.exists()

    # ---- Package render ----
    builder = RenderPackageBuilder(
        project_id="project-scale-e2e",
        project_name="Scale E2E Test",
        width=320,
        height=180,
        fps=30,
    )
    try:
        zip_path = await builder.build(
            copy.deepcopy(timeline_data),
            assets,
            {
                "asset-bg": "bg_scale_e2e.png",
                "asset-sprite": "sprite_scale_e2e.png",
            },
        )
        extract_dir = temp_output_dir / "scale-e2e-package"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        package_root = next(extract_dir.glob("render_package_*"))
        render_result = subprocess.run(
            ["bash", "render.sh"],
            cwd=package_root,
            capture_output=True,
            text=True,
        )
        assert render_result.returncode == 0, (
            f"render.sh failed.\nstdout: {render_result.stdout}\nstderr: {render_result.stderr}"
        )

        package_output = package_root / "output" / "final.mp4"
        assert package_output.exists()

        # Server and package outputs must be pixel-identical (framemd5 parity)
        server_md5 = _framemd5(server_output)
        package_md5 = _framemd5(package_output)
        assert server_md5 == package_md5, (
            "Package output does not match server output — transform.scale may be ignored.\n"
            f"server framemd5 (first line): {server_md5.splitlines()[0]}\n"
            f"package framemd5 (first line): {package_md5.splitlines()[0]}"
        )
    finally:
        builder.cleanup()
