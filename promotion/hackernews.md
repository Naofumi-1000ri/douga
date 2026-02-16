# Hacker News — Show HN Post

---

## Title

Show HN: Atsurae – AI video editor with text instructions and FFmpeg

---

## Post Body

I built atsurae, a video editing service designed from the ground up for AI agents. Describe what you want in text, and the engine composes production-ready video from raw assets.

**The technical problem:** Programmatic video editing with FFmpeg filter_complex is painful. I needed to generate complex filter graphs that compose 5 simultaneous layers — background, screen captures, chroma-keyed avatars, particle effects, and animated captions — with per-clip timing, transitions, and audio ducking. The generator takes a declarative timeline and produces the correct chain of overlay, chromakey, fade, and amix filters, outputting 1920x1080 30fps H.264+AAC.

**The API design problem:** Most APIs are built for humans reading docs. I needed one AI agents could use with zero prior knowledge. Three independent AI experts rated it 9.2/10 for AI Friendliness. A naive agent test (no docs pre-loaded) scored 9.5/10. Key decisions:

- Errors include `expected_format` and `suggested_fix` for self-healing loops
- Three-stage safety: `validate_only` -> `preview-diff` -> `rollback`
- Schema at three detail levels (L1/L2/L3) with `token_estimate` so agents manage their own context budget

**Stack:** React + Vite, FastAPI on Cloud Run, PostgreSQL, GCS, FFmpeg.

**Next:** Open-source MCP server for Claude Desktop — 10 tool endpoints covering inspect, edit, batch, preview, render, and rollback. The AI-Friendly API framework will be MIT-licensed.

Free tier: 3 projects, 5 exports/month, 1080p, 10-min max, 1GB storage, watermarked. Live at https://douga-2f6f8.web.app. Feedback on the API design patterns especially welcome.
