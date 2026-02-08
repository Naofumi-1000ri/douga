"""Semantic sync check service.

Checks whether narration content aligns with displayed visual content
using keyword matching (Tier 1, deterministic, no LLM cost).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SyncCheckResult:
    """Result of a single segment sync check."""
    segment_index: int
    segment_text: str
    timeline_start_ms: int
    timeline_end_ms: int
    active_content_assets: list[str]  # Asset names visible during segment
    match_status: str  # "match", "no_match", "no_content"
    keywords: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class SemanticCheckResult:
    """Overall semantic check result."""
    total_segments: int = 0
    matched_segments: int = 0
    unmatched_segments: int = 0
    no_content_segments: int = 0
    match_rate: float = 0.0
    details: list[SyncCheckResult] = field(default_factory=list)


class SemanticCheckService:
    """Checks narration-content alignment via keyword matching."""

    # Common Japanese particles/connectors to ignore
    STOP_WORDS_JA = {
        "の", "は", "が", "を", "に", "で", "と", "も", "から", "まで",
        "より", "へ", "や", "か", "な", "ね", "よ", "わ", "する", "ます",
        "です", "した", "して", "ている", "これ", "それ", "あれ", "この",
        "その", "ある", "いる", "なる", "できる", "ない", "ません",
    }

    def __init__(
        self,
        timeline_data: dict[str, Any],
        asset_name_map: dict[str, str] | None = None,
    ):
        self.timeline = timeline_data
        self.asset_name_map = asset_name_map or {}

    def check(self) -> SemanticCheckResult:
        """Run semantic sync check."""
        result = SemanticCheckResult()

        # Get transcription from metadata
        metadata = self.timeline.get("metadata", {})
        transcription = metadata.get("transcription", {})
        segments = transcription.get("segments", [])

        if not segments:
            return result

        result.total_segments = len(segments)

        for i, seg in enumerate(segments):
            text = seg.get("text", "")
            tl_start = seg.get("timeline_start_ms", 0)
            tl_dur = seg.get("timeline_duration_ms", 0)
            tl_end = tl_start + tl_dur

            # Find content-layer clips active during this segment
            active_assets = self._get_active_content_assets(tl_start, tl_end)

            # Extract keywords from narration text
            keywords = self._extract_keywords(text)

            if not active_assets:
                check = SyncCheckResult(
                    segment_index=i,
                    segment_text=text,
                    timeline_start_ms=tl_start,
                    timeline_end_ms=tl_end,
                    active_content_assets=[],
                    match_status="no_content",
                    keywords=keywords,
                )
                result.no_content_segments += 1
            else:
                # Check keyword overlap with asset names
                matched_kw = self._match_keywords_to_assets(keywords, active_assets)
                status = "match" if matched_kw else "no_match"

                check = SyncCheckResult(
                    segment_index=i,
                    segment_text=text,
                    timeline_start_ms=tl_start,
                    timeline_end_ms=tl_end,
                    active_content_assets=active_assets,
                    match_status=status,
                    keywords=keywords,
                    matched_keywords=matched_kw,
                )

                if status == "match":
                    result.matched_segments += 1
                else:
                    result.unmatched_segments += 1

            result.details.append(check)

        total_with_content = result.matched_segments + result.unmatched_segments
        result.match_rate = (
            result.matched_segments / total_with_content * 100
            if total_with_content > 0
            else 100.0
        )

        return result

    def _get_active_content_assets(
        self, start_ms: int, end_ms: int
    ) -> list[str]:
        """Get asset names of content-layer clips active during time range."""
        names: list[str] = []
        for layer in self.timeline.get("layers", []):
            if layer.get("type") not in ("content", "background"):
                continue
            for clip in layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if clip_end <= start_ms or clip_start >= end_ms:
                    continue
                asset_id = str(clip.get("asset_id", ""))
                if asset_id and asset_id in self.asset_name_map:
                    names.append(self.asset_name_map[asset_id])
                elif asset_id:
                    names.append(asset_id[:8])
        return names

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from Japanese text.

        Simple approach: split on common boundaries, filter stop words.
        """
        # Remove punctuation
        text = re.sub(r'[、。！？「」『』（）\[\]【】・…\s]+', ' ', text)

        # Split into potential words (rough tokenization)
        # For Japanese, we use character n-grams of length 2-4
        words: list[str] = []

        # Also split on spaces for any English words
        for part in text.split():
            if len(part) <= 1:
                continue
            if part.lower() in self.STOP_WORDS_JA:
                continue
            words.append(part.lower())
            # Also add 2-char substrings for Japanese compound matching
            if len(part) >= 4:
                for j in range(len(part) - 1):
                    bigram = part[j:j+2]
                    if bigram.lower() not in self.STOP_WORDS_JA:
                        words.append(bigram.lower())

        return list(set(words))

    def _match_keywords_to_assets(
        self, keywords: list[str], asset_names: list[str]
    ) -> list[str]:
        """Check if any keywords appear in asset names."""
        matched: list[str] = []
        for kw in keywords:
            for name in asset_names:
                if kw in name.lower():
                    matched.append(kw)
                    break
        return matched
