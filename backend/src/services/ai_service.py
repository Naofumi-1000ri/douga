"""Compatibility wrapper for the AI service package.

The implementation lives under ``src.services.ai``.  This module preserves the
legacy import path used by API modules and tests.
"""

from src.services.ai.service import AIService
from src.services.ai.utils import (
    DEFAULT_TEXT_STYLE,
    TEXT_STYLE_KEY_MAP,
    _escape_user_string,
    _sanitize_timeline_ms,
    normalize_text_clip_for_storage,
    normalize_text_style_for_storage,
)

__all__ = [
    "AIService",
    "DEFAULT_TEXT_STYLE",
    "TEXT_STYLE_KEY_MAP",
    "_escape_user_string",
    "_sanitize_timeline_ms",
    "normalize_text_clip_for_storage",
    "normalize_text_style_for_storage",
]
