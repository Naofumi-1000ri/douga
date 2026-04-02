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
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.render.audio_mixer import AudioMixer
from src.render.pipeline import RenderPipeline, analyze_timeline_for_memory
from src.render.timeline_normalization import normalize_embedded_export_timeline

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
        safe_name = re.sub(r"[^\w\-]", "_", project_name)[:50]
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
        self._script_entries: list[tuple[str, str]] = []
        self.expected_ffmpeg_version = self._detect_ffmpeg_version()

    def _detect_ffmpeg_version(self) -> str | None:
        """Capture the server FFmpeg version for package diagnostics."""
        try:
            result = subprocess.run(
                [settings.ffmpeg_path, "-version"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

        first_line = result.stdout.splitlines()[0].strip() if result.stdout else ""
        return first_line or None

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
        self._script_entries = []
        normalized_timeline, render_duration_ms = normalize_embedded_export_timeline(timeline_data)

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

        mem_info = analyze_timeline_for_memory(
            normalized_timeline, self.width, self.height, self.fps
        )

        if mem_info["needs_chunking"] and mem_info["recommended_chunks"] > 1:
            self._build_chunked_scripts(
                pipeline,
                normalized_timeline,
                assets_local,
                render_duration_ms,
                mem_info,
            )
        else:
            self._build_standard_scripts(
                pipeline,
                normalized_timeline,
                assets_local,
                render_duration_ms,
            )

        # Step 8: Generate master render.sh
        self._write_master_script()
        self._write_docker_script()

        # Step 9: Generate manifest, timeline, README
        self._write_manifest(normalized_timeline, render_duration_ms)
        self._write_timeline(normalized_timeline)
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
            safe_name = re.sub(r"[^\w\-.]", "_", base_name)

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

    def _shell_join(self, cmd: list[str]) -> str:
        """Escape a command list for insertion into a shell script."""
        escaped_args: list[str] = []
        for arg in cmd:
            if any(c in arg for c in " \t\n\"'\\;|&$(){}[]<>!#~`") or not arg:
                safe_arg = arg.replace("'", "'\\''")
                escaped_args.append(f"'{safe_arg}'")
            else:
                escaped_args.append(arg)
        return " ".join(escaped_args)

    def _copy_generated_files(
        self,
        generated_files: dict[str, str],
        *,
        prefix: str = "",
    ) -> None:
        """Copy generated PNGs into the package and register rewrite mappings."""
        for label, gen_path in generated_files.items():
            if not os.path.exists(gen_path):
                continue
            dest_name = f"{prefix}{label}" if prefix else label
            dest = os.path.join(self.generated_dir, dest_name)
            shutil.copy2(gen_path, dest)
            self._generated_path_map[gen_path] = f"./generated/{dest_name}"

    def _build_standard_scripts(
        self,
        pipeline: RenderPipeline,
        timeline_data: dict[str, Any],
        assets_local: dict[str, str],
        render_duration_ms: int,
    ) -> None:
        """Build the default single-pass render scripts."""
        audio_tracks = pipeline._build_audio_tracks(
            timeline_data,
            assets_local,
            render_duration_ms,
        )
        audio_output = "./output/mixed_audio.wav"

        audio_mixer = AudioMixer(pipeline.output_dir)
        audio_cmd = audio_mixer.build_mix_command(
            audio_tracks,
            os.path.join(pipeline.output_dir, "mixed_audio.wav"),
            render_duration_ms,
        )
        silence_cmd = audio_mixer.build_silence_command(
            os.path.join(pipeline.output_dir, "mixed_audio.wav"),
            render_duration_ms,
        )

        composite_output = "./output/composite.mp4"
        composite_result = pipeline.build_composite_command(
            timeline_data,
            assets_local,
            render_duration_ms,
            os.path.join(pipeline.output_dir, "composite.mp4"),
        )
        if composite_result:
            _composite_cmd, generated_files = composite_result
            self._copy_generated_files(generated_files)

        final_cmd = pipeline.build_final_command(
            os.path.join(pipeline.output_dir, "composite.mp4"),
            os.path.join(pipeline.output_dir, "mixed_audio.wav"),
            os.path.join(pipeline.output_dir, "final.mp4"),
            render_duration_ms,
        )

        audio_script_cmd = self._rewrite_command(
            audio_cmd if audio_cmd else silence_cmd,
            pipeline.output_dir,
        )
        self._write_script("01_mix_audio.sh", audio_script_cmd, "Audio mixing", audio_output)

        if composite_result:
            composite_script_cmd = self._drop_server_only_composite_limits(
                self._rewrite_command(composite_result[0], pipeline.output_dir)
            )
        else:
            duration_s = render_duration_ms / 1000
            composite_script_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={self.width}x{self.height}:r={self.fps}:d={duration_s}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
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

    def _build_chunked_scripts(
        self,
        pipeline: RenderPipeline,
        timeline_data: dict[str, Any],
        assets_local: dict[str, str],
        render_duration_ms: int,
        mem_info: dict[str, Any],
    ) -> None:
        """Build package scripts that mirror server-side chunked rendering."""
        export_start_ms = timeline_data.get("export_start_ms", 0)
        export_end_ms = timeline_data.get("export_end_ms", render_duration_ms + export_start_ms)
        chunk_boundaries = pipeline._calculate_chunk_boundaries(
            timeline_data,
            export_start_ms,
            export_end_ms,
            mem_info["chunk_duration_s"],
        )

        chunk_outputs: list[str] = []
        audio_mixer = AudioMixer(pipeline.output_dir)
        for chunk_idx, (chunk_start_ms, chunk_end_ms) in enumerate(chunk_boundaries):
            chunk_timeline = pipeline._create_chunk_timeline(
                timeline_data,
                chunk_start_ms,
                chunk_end_ms,
            )
            chunk_duration_ms = chunk_end_ms - chunk_start_ms
            chunk_prefix = f"chunk_{chunk_idx:03d}"
            chunk_audio_abs = os.path.join(
                pipeline.output_dir, "chunks", f"{chunk_prefix}_audio.wav"
            )
            chunk_video_abs = os.path.join(
                pipeline.output_dir, "chunks", f"{chunk_prefix}_video.mp4"
            )
            chunk_final_abs = os.path.join(pipeline.output_dir, "chunks", f"{chunk_prefix}.mp4")

            audio_tracks = pipeline._build_audio_tracks(
                chunk_timeline,
                assets_local,
                chunk_duration_ms,
            )
            audio_cmd = audio_mixer.build_mix_command(
                audio_tracks, chunk_audio_abs, chunk_duration_ms
            )
            silence_cmd = audio_mixer.build_silence_command(chunk_audio_abs, chunk_duration_ms)
            composite_result = pipeline.build_composite_command(
                chunk_timeline,
                assets_local,
                chunk_duration_ms,
                chunk_video_abs,
            )
            if composite_result:
                _cmd, generated_files = composite_result
                self._copy_generated_files(generated_files, prefix=f"{chunk_prefix}_")
                composite_script_cmd = self._drop_server_only_composite_limits(
                    self._rewrite_command(composite_result[0], pipeline.output_dir)
                )
            else:
                duration_s = chunk_duration_ms / 1000
                composite_script_cmd = [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s={self.width}x{self.height}:r={self.fps}:d={duration_s}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-pix_fmt",
                    "yuv420p",
                    f"./output/chunks/{chunk_prefix}_video.mp4",
                ]

            final_cmd = pipeline.build_final_command(
                chunk_video_abs,
                chunk_audio_abs,
                chunk_final_abs,
                chunk_duration_ms,
            )
            audio_script_cmd = self._rewrite_command(
                audio_cmd if audio_cmd else silence_cmd,
                pipeline.output_dir,
            )
            final_script_cmd = self._rewrite_command(final_cmd, pipeline.output_dir)
            prepared_audio_cmd = self._prepare_script_command(
                f"{chunk_idx + 1:02d}_{chunk_prefix}_audio.sh",
                audio_script_cmd,
            )
            prepared_composite_cmd = self._prepare_script_command(
                f"{chunk_idx + 1:02d}_{chunk_prefix}_composite.sh",
                composite_script_cmd,
            )
            prepared_final_cmd = self._prepare_script_command(
                f"{chunk_idx + 1:02d}_{chunk_prefix}_final.sh",
                final_script_cmd,
            )

            script_content = f"""#!/bin/bash
# Render chunk {chunk_idx + 1}/{len(chunk_boundaries)}
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output/chunks

{self._shell_join(prepared_audio_cmd)}
{self._shell_join(prepared_composite_cmd)}
{self._shell_join(prepared_final_cmd)}

echo "[OK] Chunk {chunk_idx + 1}/{len(chunk_boundaries)} complete: ./output/chunks/{chunk_prefix}.mp4"
"""
            script_name = f"{chunk_idx + 1:02d}_render_{chunk_prefix}.sh"
            self._write_raw_script(
                script_name,
                script_content,
                f"Render chunk {chunk_idx + 1}/{len(chunk_boundaries)}",
            )
            chunk_outputs.append(f"{chunk_prefix}.mp4")

        concat_lines = "\n".join(f"file '{chunk_name}'" for chunk_name in chunk_outputs)
        concat_script = f"""#!/bin/bash
# Concatenate rendered chunks
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output/chunks
cat > output/chunks/concat_list.txt <<'EOF'
{concat_lines}
EOF

ffmpeg -y -f concat -safe 0 -i ./output/chunks/concat_list.txt -c copy -movflags +faststart ./output/final.mp4

echo "[OK] Chunk concatenation complete: ./output/final.mp4"
"""
        self._write_raw_script(
            f"{len(chunk_boundaries) + 1:02d}_concat_chunks.sh",
            concat_script,
            "Concatenate chunks",
        )

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

    def _drop_server_only_composite_limits(self, cmd: list[str]) -> list[str]:
        """Remove server-only FFmpeg resource caps from package composite scripts.

        The render package targets output parity with Export, but it runs on a
        client machine. Cloud Run-specific caps such as `-threads 2` should not
        be baked into the downloaded scripts.
        """
        rewritten: list[str] = []
        idx = 0
        while idx < len(cmd):
            if cmd[idx] == "-threads" and idx + 1 < len(cmd):
                idx += 2
                continue

            rewritten.append(cmd[idx])
            idx += 1

        return rewritten

    def _prepare_script_command(
        self,
        filename: str,
        cmd: list[str],
    ) -> list[str]:
        """Rewrite complex filter graphs into external script files.

        This avoids shell-quoting drift between direct subprocess execution on the
        server and `bash render.sh` inside the downloaded package.
        """
        prepared = list(cmd)
        if "-filter_complex" not in prepared:
            return prepared

        filter_index = prepared.index("-filter_complex")
        if filter_index + 1 >= len(prepared):
            return prepared

        filter_complex = prepared[filter_index + 1]
        filter_name = Path(filename).stem + ".filtergraph"
        filter_path = os.path.join(self.scripts_dir, filter_name)
        with open(filter_path, "w") as f:
            f.write(filter_complex)

        return [
            *prepared[:filter_index],
            "-filter_complex_script",
            f"./scripts/{filter_name}",
            *prepared[filter_index + 2 :],
        ]

    def _write_script(
        self,
        filename: str,
        cmd: list[str],
        description: str,
        output_file: str,
    ) -> None:
        """Write an FFmpeg command as a shell script."""
        prepared_cmd = self._prepare_script_command(filename, cmd)
        script_content = f"""#!/bin/bash
# {description}
# Output: {output_file}
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output

{self._shell_join(prepared_cmd)}

echo "[OK] {description} complete: {output_file}"
"""
        self._write_raw_script(filename, script_content, description)

    def _write_raw_script(self, filename: str, content: str, description: str) -> None:
        """Write a prebuilt shell script and register it for render.sh."""
        path = os.path.join(self.scripts_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
        self._script_entries.append((description, f"scripts/{filename}"))

    def _write_master_script(self) -> None:
        """Write the master render.sh script."""
        expected_line = self.expected_ffmpeg_version or "unknown"
        step_lines: list[str] = []
        total_steps = len(self._script_entries)
        for idx, (description, script_path) in enumerate(self._script_entries, start=1):
            step_lines.append(f'echo "[{idx}/{total_steps}] {description}..."')
            step_lines.append(f"bash {script_path}")
            step_lines.append('echo ""')
        steps_block = "\n".join(step_lines)
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

ACTUAL_FFMPEG_VERSION="$(ffmpeg -version | head -1)"
echo "FFmpeg: $ACTUAL_FFMPEG_VERSION"
echo "Expected: {expected_line}"
if [ "{expected_line}" != "unknown" ] && [ "$ACTUAL_FFMPEG_VERSION" != "{expected_line}" ]; then
    echo "WARN: FFmpeg version differs from the server runtime. Output parity may drift."
fi
echo ""

mkdir -p output

{steps_block}

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

    def _write_docker_script(self) -> None:
        """Write an optional Docker wrapper for a pinned FFmpeg runtime."""
        content = """#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker not found."
    exit 1
fi

docker run --rm \
  -v "$PWD":/work \
  -w /work \
  jrottenberg/ffmpeg:6.1-ubuntu2204 \
  bash render.sh
"""
        path = os.path.join(self.package_dir, "render-docker.sh")
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
            "created_at": datetime.now(UTC).isoformat(),
            "project": {
                "id": self.project_id,
                "name": self.project_name,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "duration_ms": duration_ms,
            },
            "assets": {
                rel_path: original_path for original_path, rel_path in self._asset_path_map.items()
            },
            "generated_files": list(self._generated_path_map.values()),
            "scripts": [
                *[script_path for _description, script_path in self._script_entries],
                "render-docker.sh",
            ],
            "output": "output/final.mp4",
            "requirements": {
                "ffmpeg": "4.0+",
                "expected_ffmpeg_version": self.expected_ffmpeg_version,
                "shell": "bash",
                "docker_image": "jrottenberg/ffmpeg:6.1-ubuntu2204",
            },
            "execution_policy": {
                "output_parity_target": "Matches Export output for the same timeline/assets",
                "server_ffmpeg_threads": settings.render_ffmpeg_threads,
                "package_ffmpeg_threads": "auto",
                "notes": [
                    "Package composite scripts omit the server-side FFmpeg thread cap.",
                    "render-docker.sh pins the FFmpeg runtime but does not emulate Cloud Run resource limits.",
                ],
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

PURPOSE:
  - This package is the local execution route for the same render pipeline as Export
  - Running `bash render.sh` is expected to produce the same final video as Export
    for the same timeline / assets / export range

REQUIREMENTS:
  - FFmpeg 4.0+ (https://ffmpeg.org/)
  - bash shell (macOS, Linux, WSL, Git Bash)

USAGE:
  1. Extract this ZIP
  2. Open a terminal in the extracted folder
  3. Run: bash render.sh
  4. Output will be in: output/final.mp4
  5. If you want the closest server runtime, run: bash render-docker.sh

MANUAL STEPS (if render.sh fails):
  bash scripts/01_mix_audio.sh       # Mix audio tracks
  bash scripts/02_composite_video.sh # Composite video layers
  bash scripts/03_encode_final.sh    # Encode final MP4

STRUCTURE:
  assets/       - Media files (video, audio, images)
  generated/    - Pre-rendered text and shape overlays (PNG)
  scripts/      - Individual FFmpeg scripts
  render.sh     - Master script (runs all 3 steps)
  render-docker.sh - Optional pinned FFmpeg runtime via Docker
  manifest.json - Package metadata (for programmatic use)
  timeline.json - Original timeline data (for reference)

NOTES:
  - Text and shape overlays are pre-rendered as PNG images
    (no font dependencies on your machine)
  - All file paths in scripts are relative to the package root
  - FFmpeg must be in your PATH
  - render.sh prints the server FFmpeg version used to build this package
  - scripts/02_composite_video.sh intentionally does not pin FFmpeg thread count;
    local FFmpeg chooses threads automatically
  - Server-side `-threads {settings.render_ffmpeg_threads}` is treated as a Cloud Run
    resource cap, not as output-parity behavior
  - render-docker.sh pins the FFmpeg image/version only; it does not recreate
    server CPU or memory limits
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
