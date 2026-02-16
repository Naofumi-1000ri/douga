# Product Hunt Launch — atsurae

---

## Tagline

AI-native video editing: just describe it, we craft it.

---

## Description

atsurae turns text instructions into production-ready videos. Upload assets, describe your edit, and our engine auto-composes 5 layers into 1080p MP4. Built API-first for AI agents — rated 9.2/10 AI Friendliness by 3 experts. Free plan: 3 projects, 5 exports/month, 1080p, 10-min max, 1GB storage, watermarked.

---

## First Comment

Hey Product Hunt! I'm the maker of atsurae (Japanese for "to craft/tailor").

I build Udemy courses, and the biggest bottleneck was always video editing — not the content, but the tedious assembly of layers, transitions, and captions. So I built a tool where you describe the video you want, and AI handles the rest.

The core challenge was making an API that AI agents can actually use well. After months of iteration, three independent AI experts rated our API 9.2/10 for AI Friendliness — one said it should become an industry standard. Key innovations: self-healing errors with suggested_fix, three-stage safety (validate_only + preview-diff + rollback), and schema-level tiers (L1/L2/L3) with token estimates so AI agents manage their own context windows.

Under the hood, we generate FFmpeg filter_complex graphs that compose 5 layers in real-time: backgrounds, screen recordings, chroma-keyed avatars, particle effects, and animated captions. Output is broadcast-ready 1920x1080 H.264+AAC.

Next up: an open-source MCP server so Claude Desktop and other AI agents can edit videos directly. The AI-Friendly API framework itself will be open-sourced under MIT.

Free plan: 3 projects, 5 exports/month, 1080p, 10-min max, 1GB storage, watermarked. Would love your feedback!

---

## Topics / Tags

1. Artificial Intelligence
2. Video Editing
3. Developer Tools
4. API
5. Open Source
