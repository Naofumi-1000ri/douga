"""AI service sub-package.

Split from the monolithic ai_service.py (Issue #284).

- llm_gateway  : OpenAI / Gemini / Anthropic call + streaming + context build
- chat         : provider routing wrappers
- project_queries : L1/L2/L3 read helpers
- timeline_editor : timeline mutation, semantic operation, batch, and analysis helpers
- timeline_ops : tool-call dispatch helpers
- utils        : shared normalization/sanitization helpers
"""
