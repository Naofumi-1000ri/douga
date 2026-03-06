#!/usr/bin/env python3
"""Compose English LP video v1 from assets using FFmpeg.

Produces a ~33-second English promotional video with 4 sections:
  1. Intro (0-5s)      - Brand title with nebula background
  2. Editor (5-14s)    - Editor UI showcase (from AI demo recording)
  3. AI Demo (14-27s)  - Live AI typing + response
  4. CTA (27-33s)      - Call to action

Based on compose_lp_video.py (Japanese version v7).
"""
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
ROOT = Path("/Users/hgs/devel/douga_root")
PROMO = ROOT / "promotion"

BG_NEBULA = PROMO / "no01.png"
AI_DEMO_VIDEO = PROMO / "lp_video_en_1min.mp4"  # 88.6s recording
BGM = PROMO / "BGM01.mp3"

OUTPUT = PROMO / "lp_video_en_v1.mp4"
LP_COPY = ROOT / "main/frontend/public/lp/lp_video_en.mp4"

# Fonts
FONT_EN = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_EN_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────
FPS = 30
W, H = 1920, 1080
CRF = 18
XFADE_DUR = 0.5

# Section durations
INTRO_DUR = 5.0
EDITOR_DUR = 9.0
AI_DEMO_DUR = 14.5  # typing(7s) + response(8s) - xfade(0.5s)
CTA_DUR = 6.0
# Total after 3 crossfades: 34.5 - 1.5 = 33.0s

# AI Demo source timecodes (from 88.6s recording)
TYPING_START = 4      # Editor loaded, command being typed
TYPING_DUR = 7.0
RESPONSE_START = 50   # AI response visible (fresher than 78s)
RESPONSE_DUR = 8.0

# Editor section timecodes
EDITOR_START = 4      # Editor loaded
EDITOR_END = 13       # 9s of editor UI

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def run(cmd: list[str], desc: str = "") -> None:
    if desc:
        print(f"  → {desc}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {desc}")
        print(f"stderr: {result.stderr}")
        print(f"stdout: {result.stdout}")
        sys.exit(1)


def ffmpeg(*args: str, desc: str = "") -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", *args]
    run(cmd, desc=desc)


def scale_fill_filter() -> str:
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "'\\''")
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


# ──────────────────────────────────────────────
# Section renderers
# ──────────────────────────────────────────────

def render_intro(out: Path) -> None:
    """Section 1: Intro (5s) - 'atsurae' + 'AI Video Editor' on nebula bg."""
    title = escape_drawtext("atsurae")
    subtitle = escape_drawtext("AI Video Editor")

    vf = (
        f"{scale_fill_filter()},"
        f"fade=t=in:st=0:d=1,"
        f"drawtext=fontfile='{FONT_EN}'"
        f":text='{title}'"
        f":fontsize=96"
        f":fontcolor=white"
        f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":x=(w-text_w)/2"
        f":y=(h-text_h)/2-40,"
        f"drawtext=fontfile='{FONT_EN_REGULAR}'"
        f":text='{subtitle}'"
        f":fontsize=42"
        f":fontcolor=white@0.9"
        f":shadowcolor=black@0.5:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2"
        f":y=(h/2)+40"
    )

    ffmpeg(
        "-loop", "1", "-i", str(BG_NEBULA),
        "-t", str(INTRO_DUR),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-crf", str(CRF),
        "-an", str(out),
        desc="Rendering intro section"
    )


def render_editor(out: Path) -> None:
    """Section 2: Editor (9s) - Editor UI from demo recording with title overlay."""
    title = escape_drawtext("Just describe, it edits.")

    title_filter = (
        f"drawbox=x=0:y=0:w=iw:h=70:color=black@0.5:t=fill,"
        f"drawtext=fontfile='{FONT_EN}'"
        f":text='{title}'"
        f":fontsize=36"
        f":fontcolor=white"
        f":shadowcolor=black@0.4:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2"
        f":y=17"
    )

    ffmpeg(
        "-ss", str(EDITOR_START), "-i", str(AI_DEMO_VIDEO),
        "-t", str(EDITOR_DUR),
        "-vf", f"{title_filter},fps={FPS},format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-crf", str(CRF),
        "-an", str(out),
        desc="Rendering editor section"
    )


def render_ai_demo(out: Path) -> None:
    """Section 3: AI Demo - typing (4-11s) + response (50-58s), crossfaded."""
    title = escape_drawtext("Edit videos with natural language")

    title_filter = (
        f"drawbox=x=0:y=0:w=iw:h=80:color=black@0.5:t=fill,"
        f"drawtext=fontfile='{FONT_EN}'"
        f":text='{title}'"
        f":fontsize=40"
        f":fontcolor=white"
        f":shadowcolor=black@0.4:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2"
        f":y=20"
    )

    typing_end = TYPING_START + TYPING_DUR
    response_end = RESPONSE_START + RESPONSE_DUR
    xf_offset = TYPING_DUR - XFADE_DUR

    filtergraph = (
        f"[0:v]trim=start={TYPING_START}:end={typing_end},setpts=PTS-STARTPTS,"
        f"{title_filter},fps={FPS},format=yuv420p[typing];"
        f"[1:v]trim=start={RESPONSE_START}:end={response_end},setpts=PTS-STARTPTS,"
        f"{title_filter},fps={FPS},format=yuv420p[response];"
        f"[typing][response]xfade=transition=fade:duration={XFADE_DUR}:offset={xf_offset}"
    )

    ffmpeg(
        "-i", str(AI_DEMO_VIDEO),
        "-i", str(AI_DEMO_VIDEO),
        "-filter_complex", filtergraph,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-crf", str(CRF),
        "-an", str(out),
        desc="Rendering AI demo section (typing + response)"
    )


def render_cta(out: Path) -> None:
    """Section 4: CTA (6s) - 'Get Started Free' + 'atsurae.ai' on nebula bg."""
    title = escape_drawtext("Get Started Free")
    url = escape_drawtext("atsurae.ai")

    vf = (
        f"{scale_fill_filter()},"
        f"fade=t=out:st={CTA_DUR - 1}:d=1,"
        f"drawtext=fontfile='{FONT_EN}'"
        f":text='{title}'"
        f":fontsize=84"
        f":fontcolor=white"
        f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":x=(w-text_w)/2"
        f":y=(h-text_h)/2-30,"
        f"drawtext=fontfile='{FONT_EN}'"
        f":text='{url}'"
        f":fontsize=48"
        f":fontcolor=white@0.85"
        f":shadowcolor=black@0.4:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2"
        f":y=(h/2)+50"
    )

    ffmpeg(
        "-loop", "1", "-i", str(BG_NEBULA),
        "-t", str(CTA_DUR),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-crf", str(CRF),
        "-an", str(out),
        desc="Rendering CTA section"
    )


# ──────────────────────────────────────────────
# Crossfade & combine
# ──────────────────────────────────────────────

def crossfade_all(sections: list[Path], durations: list[float], out: Path) -> None:
    """Apply xfade between consecutive sections."""
    if len(sections) < 2:
        shutil.copy(sections[0], out)
        return

    inputs = []
    for s in sections:
        inputs.extend(["-i", str(s)])

    filter_parts = []
    current_label = "[0:v]"
    cumulative_dur = durations[0]

    for i in range(1, len(sections)):
        offset = cumulative_dur - XFADE_DUR
        out_label = f"[xf{i}]" if i < len(sections) - 1 else "[vout]"
        filter_parts.append(
            f"{current_label}[{i}:v]xfade=transition=fade:duration={XFADE_DUR}:offset={offset}{out_label}"
        )
        current_label = out_label
        cumulative_dur = offset + durations[i]

    filtergraph = ";".join(filter_parts)

    ffmpeg(
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "[vout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS), "-crf", str(CRF),
        "-an", str(out),
        desc="Applying crossfades between sections"
    )


def add_bgm(video: Path, out: Path) -> None:
    """Add BGM with volume adjustment, fade in/out."""
    durations = [INTRO_DUR, EDITOR_DUR, AI_DEMO_DUR, CTA_DUR]
    total_dur = sum(durations) - (len(durations) - 1) * XFADE_DUR
    fade_out_start = total_dur - 2.0

    audio_filter = (
        f"volume=0.3,"
        f"afade=t=in:st=0:d=2,"
        f"afade=t=out:st={fade_out_start}:d=2"
    )

    ffmpeg(
        "-i", str(video),
        "-i", str(BGM),
        "-t", str(total_dur),
        "-filter_complex", f"[1:a]{audio_filter}[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out),
        desc="Adding BGM with fade in/out"
    )


def extract_frames(video: Path) -> None:
    """Extract key frames for review."""
    timestamps = [3, 7, 12, 20, 30]
    for ts in timestamps:
        frame_out = PROMO / f"en_v1_frame_{ts}s.jpg"
        ffmpeg(
            "-ss", str(ts), "-i", str(video),
            "-frames:v", "1", "-q:v", "2",
            "-update", "1",
            str(frame_out),
            desc=f"Extracting frame at {ts}s"
        )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  LP Video English v1 Composition")
    print("=" * 60)

    # Verify assets exist
    for p in [BG_NEBULA, AI_DEMO_VIDEO, BGM]:
        if not p.exists():
            print(f"ERROR: Missing asset: {p}")
            sys.exit(1)
    print("All assets verified.\n")

    durations = [INTRO_DUR, EDITOR_DUR, AI_DEMO_DUR, CTA_DUR]

    with tempfile.TemporaryDirectory(prefix="lp_en_v1_") as tmpdir:
        tmp = Path(tmpdir)
        intro_mp4 = tmp / "01_intro.mp4"
        editor_mp4 = tmp / "02_editor.mp4"
        ai_demo_mp4 = tmp / "03_ai_demo.mp4"
        cta_mp4 = tmp / "04_cta.mp4"
        crossfaded_mp4 = tmp / "05_crossfaded.mp4"

        # Step 1: Render individual sections
        print("[1/5] Rendering individual sections...")
        render_intro(intro_mp4)
        render_editor(editor_mp4)
        render_ai_demo(ai_demo_mp4)
        render_cta(cta_mp4)

        # Step 2: Apply crossfades
        print("\n[2/5] Applying crossfades...")
        crossfade_all(
            [intro_mp4, editor_mp4, ai_demo_mp4, cta_mp4],
            durations,
            crossfaded_mp4
        )

        # Step 3: Add BGM
        print("\n[3/5] Adding BGM...")
        add_bgm(crossfaded_mp4, OUTPUT)

    # Step 4: Extract key frames
    print("\n[4/5] Extracting key frames...")
    extract_frames(OUTPUT)

    # Step 5: Copy to frontend
    print("\n[5/5] Copying to frontend public/lp/...")
    LP_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT, LP_COPY)
    print(f"  → Copied to {LP_COPY}")

    # Report
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(OUTPUT)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        import json
        info = json.loads(result.stdout)
        fmt = info.get("format", {})
        streams = info.get("streams", [])

        duration = float(fmt.get("duration", 0))
        size_mb = int(fmt.get("size", 0)) / (1024 * 1024)

        video_stream = next((s for s in streams if s["codec_type"] == "video"), None)
        audio_stream = next((s for s in streams if s["codec_type"] == "audio"), None)

        print(f"\n{'=' * 60}")
        print(f"  OUTPUT: {OUTPUT}")
        print(f"{'=' * 60}")
        print(f"  Duration : {duration:.1f}s")
        print(f"  File size: {size_mb:.1f} MB")
        if video_stream:
            print(f"  Video    : {video_stream['width']}x{video_stream['height']}, "
                  f"{video_stream.get('r_frame_rate', '?')} fps, "
                  f"{video_stream['codec_name']}")
        if audio_stream:
            print(f"  Audio    : {audio_stream['codec_name']}, "
                  f"{audio_stream.get('sample_rate', '?')} Hz, "
                  f"{audio_stream.get('channels', '?')} ch")
        print(f"{'=' * 60}")

    # Check extracted frames
    print("\nKey frames:")
    for ts in [3, 7, 12, 20, 30]:
        fp = PROMO / f"en_v1_frame_{ts}s.jpg"
        if fp.exists():
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.name}: {size_kb:.0f} KB")
        else:
            print(f"  {fp.name}: MISSING")

    print(f"\nLP copy: {LP_COPY}")
    print("Done!")


if __name__ == "__main__":
    main()
