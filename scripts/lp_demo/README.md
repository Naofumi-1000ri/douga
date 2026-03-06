# LP Demo Recording Scripts

Playwright scripts to record a demo video of the douga AI video editor.
The resulting video/screenshots can be used in the atsurae.ai landing page
and promotional materials.

## What the recording shows

1. The douga editor loads with a pre-configured project
2. The AI chat panel opens
3. A command is typed slowly into the AI chat (e.g. "イントロ動画を作って")
4. The AI responds and the timeline updates automatically
5. The Play button is clicked to preview the result

Total recording duration: ~45 seconds.

## Setup

```bash
pip install playwright
playwright install chromium
```

## Step 1: Save authentication

Firebase Google OAuth requires a manual login. This script opens a browser,
lets you log in, and saves the session to `auth_state.json`.

```bash
python save_auth.py
```

Follow the on-screen instructions:
1. Log in with your Google account in the browser window
2. Wait for the dashboard to fully load
3. Press Enter in the terminal

The auth state is saved to `auth_state.json` (gitignored).

## Step 2: Record the demo

```bash
python record_demo.py --project-id <PROJECT_ID> --sequence-id <SEQUENCE_ID>
```

### Options

| Flag              | Description                                 | Default                |
|-------------------|---------------------------------------------|------------------------|
| `--project-id`    | Project ID to open in the editor            | `$DOUGA_PROJECT_ID`   |
| `--sequence-id`   | Sequence ID within the project              | `$DOUGA_SEQUENCE_ID`  |
| `--command`       | AI command text to type                     | `イントロ動画を作って` |
| `--headless`      | Run without a visible browser window        | off                    |

### Environment variables

You can set defaults via environment variables instead of CLI flags:

```bash
export DOUGA_PROJECT_ID=abc123
export DOUGA_SEQUENCE_ID=seq456
export DOUGA_DEMO_COMMAND="BGMを追加して"
python record_demo.py
```

## Output

After recording, output files are in `output/`:

```
output/
├── screenshots/
│   ├── 01_editor_loaded.png
│   ├── 02_ai_panel_open.png
│   ├── 03_command_typed.png
│   ├── 04_ai_progress_01.png
│   ├── 05_ai_response_complete.png
│   ├── 06_playing.png
│   ├── 07_preview_playing.png
│   └── 08_final.png
└── videos/
    └── demo_20260215_143000.webm
```

Screenshots are useful as hero images for the LP. The video is the full
recording in WebM format (convert to MP4 with ffmpeg if needed):

```bash
ffmpeg -i output/videos/demo_*.webm -c:v libx264 -crf 23 output/demo.mp4
```

## Timing / Choreography

| Time    | Phase                          |
|---------|--------------------------------|
| 0-3s    | Page loads, editor appears     |
| 3-5s    | AI chat panel opens            |
| 5-8s    | Command typed slowly           |
| 8-9s    | Enter pressed, command sent    |
| 9-35s   | AI processes, timeline updates |
| 35-40s  | Play button clicked            |
| 40-45s  | Preview plays                  |

## Tips

- Use a project that already has some assets uploaded for a richer demo.
- For a clean recording, clear the AI chat history in the editor first.
- Re-run `save_auth.py` if the auth token expires (typically after ~1 hour).
- The `--headless` flag records without showing the browser, but you lose
  the ability to visually monitor the recording in real-time.
