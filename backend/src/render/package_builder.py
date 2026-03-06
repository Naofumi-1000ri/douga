"""Client-side render package builder.

Generates a self-contained ZIP archive that clients can use to render
video locally with FFmpeg, offloading the work from Cloud Run.

Package structure:
    render_package_{project_name}/
    ├── assets/                    # Media files downloaded from GCS
    ├── generated/                 # Server-generated text/shape PNGs
    ├── scripts/
    │   ├── 01_mix_audio.sh
    │   ├── 02_composite_video.sh
    │   └── 03_encode_final.sh
    ├── render.sh                  # Master script
    ├── manifest.json
    ├── timeline.json
    └── README.txt
"""

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.render.audio_mixer import AudioMixer
from src.render.pipeline import RenderPipeline

logger = logging.getLogger(__name__)
settings = get_settings()


class RenderPackageBuilder:
    """Builds a self-contained render package ZIP for client-side FFmpeg execution."""

    def __init__(
        self,
        project_id: str,
        project_name: str,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
    ):
        self.project_id = project_id
        self.project_name = project_name
        self.width = width
        self.height = height
        self.fps = fps

        # Create temp work directory
        self.work_dir = tempfile.mkdtemp(prefix=f"douga_pkg_{project_id[:8]}_")
        safe_name = re.sub(r'[^\w\-]', '_', project_name)[:50]
        self.package_name = f"render_package_{safe_name}"
        self.package_dir = os.path.join(self.work_dir, self.package_name)

        # Create subdirectories
        self.assets_dir = os.path.join(self.package_dir, "assets")
        self.generated_dir = os.path.join(self.package_dir, "generated")
        self.scripts_dir = os.path.join(self.package_dir, "scripts")
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.generated_dir, exist_ok=True)
        os.makedirs(self.scripts_dir, exist_ok=True)

        # Track asset path mappings: original_path -> package_relative_path
        self._asset_path_map: dict[str, str] = {}
        self._generated_path_map: dict[str, str] = {}

    async def build(
        self,
        timeline_data: dict[str, Any],
        assets_local: dict[str, str],
        asset_names: dict[str, str] | None = None,
    ) -> str:
        """Build the render package and return path to ZIP file.

        Args:
            timeline_data: Full timeline JSON data
            assets_local: Map of asset_id -> local file path (already downloaded)
            asset_names: Optional map of asset_id -> human-readable name

        Returns:
            Path to the generated ZIP file
        """
        asset_names = asset_names or {}
        duration_ms = timeline_data.get("duration_ms", 0)
        export_start_ms = timeline_data.get("export_start_ms", 0)
        export_end_ms = timeline_data.get("export_end_ms", duration_ms + export_start_ms)
        render_duration_ms = export_end_ms - export_start_ms

        # Step 1: Copy assets into package with human-readable names
        self._copy_assets(assets_local, asset_names)

        # Step 2: Create pipeline to get FFmpeg commands + generate PNGs
        pipeline = RenderPipeline(
            job_id=f"pkg_{self.project_id[:8]}",
            project_id=self.project_id,
            width=self.width,
            height=self.height,
            fps=self.fps,
        )

        # Remap assets to package paths for command building
        package_assets = {}
        for asset_id, original_path in assets_local.items():
            if original_path in self._asset_path_map:
                package_assets[asset_id] = self._asset_path_map[original_path]
            else:
                package_assets[asset_id] = original_path

        # Step 3: Build audio tracks and mix command
        audio_tracks = pipeline._build_audio_tracks(timeline_data, assets_local, render_duration_ms)
        audio_output = "./output/mixed_audio.aac"

        audio_mixer = AudioMixer(pipeline.output_dir)
        # Build audio command using original paths (we'll rewrite later)
        audio_cmd = audio_mixer.build_mix_command(
            audio_tracks,
            os.path.join(pipeline.output_dir, "mixed_audio.aac"),
            render_duration_ms,
        )
        silence_cmd = audio_mixer.build_silence_command(
            os.path.join(pipeline.output_dir, "mixed_audio.aac"),
            render_duration_ms,
        )

        # Step 4: Build composite command (also generates PNGs)
        composite_output = "./output/composite.mp4"
        composite_result = pipeline.build_composite_command(
            timeline_data,
            assets_local,
            render_duration_ms,
            os.path.join(pipeline.output_dir, "composite.mp4"),
        )

        # Step 5: Copy generated PNGs to package
        if composite_result:
            _composite_cmd, generated_files = composite_result
            for label, gen_path in generated_files.items():
                if os.path.exists(gen_path):
                    dest = os.path.join(self.generated_dir, label)
                    shutil.copy2(gen_path, dest)
                    self._generated_path_map[gen_path] = f"./generated/{label}"

        # Step 6: Build final encode command
        final_cmd = pipeline.build_final_command(
            os.path.join(pipeline.output_dir, "composite.mp4"),
            os.path.join(pipeline.output_dir, "mixed_audio.aac"),
            os.path.join(pipeline.output_dir, "final.mp4"),
            render_duration_ms,
        )

        # Step 7: Rewrite paths and generate scripts
        audio_script_cmd = self._rewrite_command(
            audio_cmd if audio_cmd else silence_cmd,
            pipeline.output_dir,
        )
        self._write_script(
            "01_mix_audio.sh",
            audio_script_cmd,
            "Audio mixing",
            audio_output,
        )

        if composite_result:
            composite_script_cmd = self._rewrite_command(
                composite_result[0],
                pipeline.output_dir,
            )
        else:
            # Generate blank video command
            duration_s = render_duration_ms / 1000
            composite_script_cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={self.width}x{self.height}:r={self.fps}:d={duration_s}",
                "-c:v", "libx264",
                "-preset", "medium",
                "-pix_fmt", "yuv420p",
                composite_output,
            ]
        self._write_script(
            "02_composite_video.sh",
            composite_script_cmd,
            "Video compositing",
            composite_output,
        )

        final_script_cmd = self._rewrite_command(final_cmd, pipeline.output_dir)
        self._write_script(
            "03_encode_final.sh",
            final_script_cmd,
            "Final encoding",
            "./output/final.mp4",
        )

        # Step 8: Generate master render.sh
        self._write_master_script()

        # Step 9: Generate manifest, timeline, README
        self._write_manifest(timeline_data, render_duration_ms)
        self._write_timeline(timeline_data)
        self._write_readme()

        # Step 10: Create ZIP
        zip_path = os.path.join(self.work_dir, f"{self.package_name}.zip")
        self._create_zip(zip_path)

        # Cleanup pipeline temp dir
        try:
            shutil.rmtree(pipeline.work_dir, ignore_errors=True)
        except Exception:
            pass

        return zip_path

    def cleanup(self) -> None:
        """Remove temporary work directory."""
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass

    def _copy_assets(
        self,
        assets_local: dict[str, str],
        asset_names: dict[str, str],
    ) -> None:
        """Copy assets into package with human-readable filenames."""
        used_names: dict[str, int] = {}

        for asset_id, local_path in assets_local.items():
            if not os.path.exists(local_path):
                logger.warning(f"[PACKAGE] Asset file not found: {local_path}")
                continue

            # Get human-readable name or use asset_id
            ext = Path(local_path).suffix
            base_name = asset_names.get(asset_id, asset_id[:12])
            # Strip extension from name if it matches file extension (avoid double-ext)
            if ext and base_name.lower().endswith(ext.lower()):
                base_name = base_name[: -len(ext)]
            # Sanitize filename
            safe_name = re.sub(r'[^\w\-.]', '_', base_name)

            # Handle duplicates
            full_name = f"{safe_name}{ext}"
            if full_name in used_names:
                used_names[full_name] += 1
                full_name = f"{safe_name}_{used_names[full_name]}{ext}"
            else:
                used_names[full_name] = 1

            dest = os.path.join(self.assets_dir, full_name)
            shutil.copy2(local_path, dest)
            self._asset_path_map[local_path] = f"./assets/{full_name}"

    def _rewrite_command(
        self,
        cmd: list[str],
        pipeline_output_dir: str,
    ) -> list[str]:
        """Rewrite absolute paths in FFmpeg command to package-relative paths.

        Replaces:
        - Asset file paths -> ./assets/...
        - Generated PNG paths -> ./generated/...
        - Pipeline output dir paths -> ./output/...
        - FFmpeg binary path -> 'ffmpeg'
        """
        rewritten: list[str] = []

        for arg in cmd:
            new_arg = arg

            # Replace ffmpeg binary path with just 'ffmpeg'
            if new_arg == settings.ffmpeg_path:
                new_arg = "ffmpeg"
                rewritten.append(new_arg)
                continue

            # Replace asset paths
            for original, relative in self._asset_path_map.items():
                new_arg = new_arg.replace(original, relative)

            # Replace generated file paths
            for original, relative in self._generated_path_map.items():
                new_arg = new_arg.replace(original, relative)

            # Replace pipeline output dir references
            if pipeline_output_dir and pipeline_output_dir in new_arg:
                new_arg = new_arg.replace(pipeline_output_dir, "./output")

            rewritten.append(new_arg)

        return rewritten

    def _write_script(
        self,
        filename: str,
        cmd: list[str],
        description: str,
        output_file: str,
    ) -> None:
        """Write an FFmpeg command as a shell script."""
        # Escape arguments for shell
        escaped_args = []
        for arg in cmd:
            if any(c in arg for c in ' \t\n"\'\\;|&$(){}[]<>!#~`') or not arg:
                # Single-quote the argument, escaping any internal single quotes
                # In bash: replace ' with '\'' (end quote, escaped quote, start quote)
                safe_arg = arg.replace("'", "'\\''")
                escaped_args.append(f"'{safe_arg}'")
            else:
                escaped_args.append(arg)

        script_content = f"""#!/bin/bash
# {description}
# Output: {output_file}
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output

{' '.join(escaped_args)}

echo "[OK] {description} complete: {output_file}"
"""
        path = os.path.join(self.scripts_dir, filename)
        with open(path, "w") as f:
            f.write(script_content)
        os.chmod(path, 0o755)

    def _write_master_script(self) -> None:
        """Write the master render.sh script."""
        content = f"""#!/bin/bash
# Render Package: {self.project_name}
# Generated by douga render engine
set -euo pipefail
cd "$(dirname "$0")"

echo "=== douga Render Package ==="
echo "Project: {self.project_name}"
echo ""

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "ERROR: FFmpeg not found. Please install FFmpeg first."
    echo "  macOS: brew install ffmpeg"
    echo "  Ubuntu: sudo apt install ffmpeg"
    exit 1
fi

echo "FFmpeg: $(ffmpeg -version | head -1)"
echo ""

mkdir -p output

echo "[1/3] Mixing audio..."
bash scripts/01_mix_audio.sh
echo ""

echo "[2/3] Compositing video..."
bash scripts/02_composite_video.sh
echo ""

echo "[3/3] Final encoding..."
bash scripts/03_encode_final.sh
echo ""

echo "=== Render Complete ==="
echo "Output: output/final.mp4"
if command -v du &> /dev/null; then
    echo "Size: $(du -h output/final.mp4 | cut -f1)"
fi
"""
        path = os.path.join(self.package_dir, "render.sh")
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)

    def _write_manifest(
        self,
        timeline_data: dict[str, Any],
        duration_ms: int,
    ) -> None:
        """Write manifest.json with package metadata."""
        manifest = {
            "version": "1.0.0",
            "generator": "douga-render-engine",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": {
                "id": self.project_id,
                "name": self.project_name,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "duration_ms": duration_ms,
            },
            "assets": {
                rel_path: original_path
                for original_path, rel_path in self._asset_path_map.items()
            },
            "generated_files": list(self._generated_path_map.values()),
            "scripts": [
                "scripts/01_mix_audio.sh",
                "scripts/02_composite_video.sh",
                "scripts/03_encode_final.sh",
            ],
            "output": "output/final.mp4",
            "requirements": {
                "ffmpeg": "4.0+",
                "shell": "bash",
            },
        }

        path = os.path.join(self.package_dir, "manifest.json")
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def _write_timeline(self, timeline_data: dict[str, Any]) -> None:
        """Write timeline.json for reference."""
        path = os.path.join(self.package_dir, "timeline.json")
        with open(path, "w") as f:
            json.dump(timeline_data, f, indent=2, ensure_ascii=False, default=str)

    def _write_readme(self) -> None:
        """Write README.txt with usage instructions."""
        content = f"""=== douga Render Package ===

Project: {self.project_name}
Resolution: {self.width}x{self.height} @ {self.fps}fps

REQUIREMENTS:
  - FFmpeg 4.0+ (https://ffmpeg.org/)
  - bash shell (macOS, Linux, WSL, Git Bash)

USAGE:
  1. Extract this ZIP
  2. Open a terminal in the extracted folder
  3. Run: bash render.sh
  4. Output will be in: output/final.mp4

MANUAL STEPS (if render.sh fails):
  bash scripts/01_mix_audio.sh       # Mix audio tracks
  bash scripts/02_composite_video.sh # Composite video layers
  bash scripts/03_encode_final.sh    # Encode final MP4

STRUCTURE:
  assets/       - Media files (video, audio, images)
  generated/    - Pre-rendered text and shape overlays (PNG)
  scripts/      - Individual FFmpeg scripts
  render.sh     - Master script (runs all 3 steps)
  manifest.json - Package metadata (for programmatic use)
  timeline.json - Original timeline data (for reference)

NOTES:
  - Text and shape overlays are pre-rendered as PNG images
    (no font dependencies on your machine)
  - All file paths in scripts are relative to the package root
  - FFmpeg must be in your PATH
"""
        path = os.path.join(self.package_dir, "README.txt")
        with open(path, "w") as f:
            f.write(content)

    def _create_zip(self, zip_path: str) -> None:
        """Create ZIP archive of the package directory."""
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(self.package_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, self.work_dir)
                    zf.write(file_path, arcname)
