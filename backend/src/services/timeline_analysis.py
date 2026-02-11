"""Timeline composition analysis service.

Provides comprehensive quality analysis of timeline compositions,
including gap detection, pacing analysis, audio coverage, and
actionable improvement suggestions for AI agents.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Threshold constants
GAP_THRESHOLD_MS = 100  # Minimum gap duration to report
SHORT_CLIP_MS = 2000  # Clips shorter than this are "too fast"
LONG_CLIP_MS = 15000  # Clips longer than this are "too slow"
SHORT_CLIP_RATIO = 0.5  # If >50% of clips are short, flag as too_fast
LONG_CLIP_RATIO = 0.3  # If >30% of clips are long, flag as too_slow


class TimelineAnalyzer:
    """Analyzes timeline composition quality for AI agents."""

    def __init__(self, timeline_data: dict, asset_map: dict[str, dict] | None = None):
        self.timeline = timeline_data or {}
        self.asset_map = asset_map or {}
        self._project_duration_ms: int | None = None

    @property
    def project_duration_ms(self) -> int:
        """Calculate project duration from the last clip end across all layers/tracks."""
        if self._project_duration_ms is not None:
            return self._project_duration_ms

        max_end = 0
        for layer in self.timeline.get("layers", []):
            for clip in layer.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)
        for track in self.timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)

        # Also check the timeline-level duration_ms if set
        timeline_duration = self.timeline.get("duration_ms", 0)
        max_end = max(max_end, timeline_duration)

        self._project_duration_ms = max_end
        return max_end

    # =========================================================================
    # Main entry point
    # =========================================================================

    def analyze_all(self) -> dict:
        """Run all analyses and return combined report."""
        gap_analysis = self.analyze_gaps()
        pacing_analysis = self.analyze_pacing()
        audio_analysis = self.analyze_audio()
        layer_coverage = self.analyze_layer_coverage()
        suggestions = self.generate_suggestions(
            gap_analysis, pacing_analysis, audio_analysis, layer_coverage
        )
        quality_score = self.calculate_quality_score(
            gap_analysis, pacing_analysis, audio_analysis, layer_coverage
        )

        return {
            "project_duration_ms": self.project_duration_ms,
            "gap_analysis": gap_analysis,
            "pacing_analysis": pacing_analysis,
            "audio_analysis": audio_analysis,
            "layer_coverage": layer_coverage,
            "suggestions": suggestions,
            "quality_score": quality_score,
        }

    # =========================================================================
    # Gap Analysis
    # =========================================================================

    def analyze_gaps(self) -> dict:
        """Find gaps (dead space) in each layer and audio track.

        Returns:
            {
                "total_gaps": int,
                "total_gap_duration_ms": int,
                "layers": [
                    {
                        "layer_id": str,
                        "layer_name": str,
                        "type": "video",
                        "gaps": [{"start_ms": int, "end_ms": int, "duration_ms": int}]
                    }
                ]
            }
        """
        layer_gaps = []
        total_gaps = 0
        total_gap_duration_ms = 0

        # Video layers
        for layer in self.timeline.get("layers", []):
            gaps = self._find_gaps_in_clips(layer.get("clips", []))
            # Check leading gap (from 0 to first clip)
            clips = sorted(layer.get("clips", []), key=lambda c: c.get("start_ms", 0))
            if clips:
                first_start = clips[0].get("start_ms", 0)
                if first_start > GAP_THRESHOLD_MS:
                    gaps.insert(0, {
                        "start_ms": 0,
                        "end_ms": first_start,
                        "duration_ms": first_start,
                    })
                # Trailing gap
                last_end = max(
                    c.get("start_ms", 0) + c.get("duration_ms", 0) for c in clips
                )
                if self.project_duration_ms > 0 and self.project_duration_ms - last_end > GAP_THRESHOLD_MS:
                    gaps.append({
                        "start_ms": last_end,
                        "end_ms": self.project_duration_ms,
                        "duration_ms": self.project_duration_ms - last_end,
                    })

            total_gaps += len(gaps)
            total_gap_duration_ms += sum(g["duration_ms"] for g in gaps)

            layer_gaps.append({
                "layer_id": layer.get("id", ""),
                "layer_name": layer.get("name", ""),
                "type": "video",
                "gaps": gaps,
            })

        # Audio tracks
        for track in self.timeline.get("audio_tracks", []):
            gaps = self._find_gaps_in_clips(track.get("clips", []))
            clips = sorted(track.get("clips", []), key=lambda c: c.get("start_ms", 0))
            if clips:
                first_start = clips[0].get("start_ms", 0)
                if first_start > GAP_THRESHOLD_MS:
                    gaps.insert(0, {
                        "start_ms": 0,
                        "end_ms": first_start,
                        "duration_ms": first_start,
                    })
                last_end = max(
                    c.get("start_ms", 0) + c.get("duration_ms", 0) for c in clips
                )
                if self.project_duration_ms > 0 and self.project_duration_ms - last_end > GAP_THRESHOLD_MS:
                    gaps.append({
                        "start_ms": last_end,
                        "end_ms": self.project_duration_ms,
                        "duration_ms": self.project_duration_ms - last_end,
                    })

            total_gaps += len(gaps)
            total_gap_duration_ms += sum(g["duration_ms"] for g in gaps)

            layer_gaps.append({
                "layer_id": track.get("id", ""),
                "layer_name": track.get("name", ""),
                "type": "audio",
                "gaps": gaps,
            })

        return {
            "total_gaps": total_gaps,
            "total_gap_duration_ms": total_gap_duration_ms,
            "layers": layer_gaps,
        }

    def _find_gaps_in_clips(self, clips: list[dict]) -> list[dict]:
        """Find gaps between adjacent clips (sorted by start_ms).

        Only reports gaps > GAP_THRESHOLD_MS between clip interiors.
        Does NOT include leading/trailing gaps here (handled by caller).
        """
        if not clips:
            return []

        sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))
        gaps: list[dict] = []
        current_end = sorted_clips[0].get("start_ms", 0) + sorted_clips[0].get("duration_ms", 0)

        for clip in sorted_clips[1:]:
            start = clip.get("start_ms", 0)
            if start - current_end > GAP_THRESHOLD_MS:
                gaps.append({
                    "start_ms": current_end,
                    "end_ms": start,
                    "duration_ms": start - current_end,
                })
            current_end = max(current_end, start + clip.get("duration_ms", 0))

        return gaps

    # =========================================================================
    # Pacing Analysis
    # =========================================================================

    def analyze_pacing(self) -> dict:
        """Analyze clip density and pacing.

        Returns:
            {
                "avg_clip_duration_ms": float,
                "clip_count": int,
                "shortest_clip": {"id": str, "duration_ms": int, "layer_id": str} | None,
                "longest_clip": {"id": str, "duration_ms": int, "layer_id": str} | None,
                "pacing_issues": [str],
            }
        """
        all_clips: list[dict] = []

        for layer in self.timeline.get("layers", []):
            layer_id = layer.get("id", "")
            for clip in layer.get("clips", []):
                all_clips.append({
                    "id": clip.get("id", ""),
                    "duration_ms": clip.get("duration_ms", 0),
                    "layer_id": layer_id,
                })

        if not all_clips:
            return {
                "avg_clip_duration_ms": 0,
                "clip_count": 0,
                "shortest_clip": None,
                "longest_clip": None,
                "pacing_issues": [],
            }

        durations = [c["duration_ms"] for c in all_clips]
        avg_duration = sum(durations) / len(durations)
        shortest = min(all_clips, key=lambda c: c["duration_ms"])
        longest = max(all_clips, key=lambda c: c["duration_ms"])

        # Pacing issue detection
        pacing_issues: list[str] = []
        short_count = sum(1 for d in durations if d < SHORT_CLIP_MS)
        long_count = sum(1 for d in durations if d > LONG_CLIP_MS)

        if len(durations) > 0:
            if short_count / len(durations) > SHORT_CLIP_RATIO:
                pacing_issues.append(
                    f"too_fast: {short_count}/{len(durations)} clips are shorter than {SHORT_CLIP_MS}ms"
                )
            if long_count / len(durations) > LONG_CLIP_RATIO:
                pacing_issues.append(
                    f"too_slow: {long_count}/{len(durations)} clips are longer than {LONG_CLIP_MS}ms"
                )

        return {
            "avg_clip_duration_ms": round(avg_duration, 1),
            "clip_count": len(all_clips),
            "shortest_clip": shortest,
            "longest_clip": longest,
            "pacing_issues": pacing_issues,
        }

    # =========================================================================
    # Audio Analysis
    # =========================================================================

    def analyze_audio(self) -> dict:
        """Analyze audio track coverage and potential issues.

        Returns:
            {
                "tracks": [{
                    "track_id": str,
                    "track_name": str,
                    "track_type": str,
                    "clip_count": int,
                    "coverage_ms": int,
                    "coverage_pct": float,
                }],
                "narration_coverage_pct": float,
                "bgm_coverage_pct": float,
                "silent_intervals": [{"start_ms": int, "end_ms": int, "duration_ms": int}],
                "issues": [str],
            }
        """
        if self.project_duration_ms == 0:
            return {
                "tracks": [],
                "narration_coverage_pct": 0.0,
                "bgm_coverage_pct": 0.0,
                "silent_intervals": [],
                "issues": [],
            }

        tracks_info: list[dict] = []
        narration_intervals: list[tuple[int, int]] = []
        bgm_intervals: list[tuple[int, int]] = []
        all_audio_intervals: list[tuple[int, int]] = []
        issues: list[str] = []

        for track in self.timeline.get("audio_tracks", []):
            track_type = track.get("type", "")
            clips = track.get("clips", [])
            intervals = [
                (c.get("start_ms", 0), c.get("start_ms", 0) + c.get("duration_ms", 0))
                for c in clips
            ]
            coverage_ms = self._merged_coverage(intervals)
            coverage_pct = round(
                (coverage_ms / self.project_duration_ms) * 100, 1
            ) if self.project_duration_ms > 0 else 0.0

            tracks_info.append({
                "track_id": track.get("id", ""),
                "track_name": track.get("name", ""),
                "track_type": track_type,
                "clip_count": len(clips),
                "coverage_ms": coverage_ms,
                "coverage_pct": coverage_pct,
            })

            all_audio_intervals.extend(intervals)

            if track_type == "narration":
                narration_intervals.extend(intervals)
            elif track_type == "bgm":
                bgm_intervals.extend(intervals)

        narration_coverage_ms = self._merged_coverage(narration_intervals)
        bgm_coverage_ms = self._merged_coverage(bgm_intervals)
        narration_pct = round(
            (narration_coverage_ms / self.project_duration_ms) * 100, 1
        ) if self.project_duration_ms > 0 else 0.0
        bgm_pct = round(
            (bgm_coverage_ms / self.project_duration_ms) * 100, 1
        ) if self.project_duration_ms > 0 else 0.0

        # Detect silent intervals (no narration AND no BGM)
        silent_intervals = self._find_uncovered_intervals(
            all_audio_intervals, self.project_duration_ms
        )

        # Issues
        if narration_pct == 0 and self.project_duration_ms > 0:
            # Check if narration track exists but is empty
            narration_exists = any(
                t.get("type") == "narration" for t in self.timeline.get("audio_tracks", [])
            )
            if narration_exists:
                issues.append("Narration track exists but has no clips")
        elif narration_pct < 50:
            issues.append(
                f"Low narration coverage ({narration_pct}%). Udemy lectures typically need >80%."
            )

        if bgm_pct == 0 and self.project_duration_ms > 0:
            bgm_exists = any(
                t.get("type") == "bgm" for t in self.timeline.get("audio_tracks", [])
            )
            if bgm_exists:
                issues.append("BGM track exists but has no clips. Consider adding background music.")

        if silent_intervals:
            total_silence = sum(s["duration_ms"] for s in silent_intervals)
            silence_pct = round((total_silence / self.project_duration_ms) * 100, 1)
            if silence_pct > 10:
                issues.append(
                    f"{len(silent_intervals)} silent intervals detected totaling "
                    f"{total_silence}ms ({silence_pct}% of timeline)"
                )

        return {
            "tracks": tracks_info,
            "narration_coverage_pct": narration_pct,
            "bgm_coverage_pct": bgm_pct,
            "silent_intervals": silent_intervals,
            "issues": issues,
        }

    def _merged_coverage(self, intervals: list[tuple[int, int]]) -> int:
        """Calculate total coverage of merged (possibly overlapping) intervals."""
        if not intervals:
            return 0

        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged: list[tuple[int, int]] = [sorted_intervals[0]]

        for start, end in sorted_intervals[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        return sum(end - start for start, end in merged)

    def _find_uncovered_intervals(
        self, intervals: list[tuple[int, int]], total_duration: int
    ) -> list[dict]:
        """Find intervals within [0, total_duration] not covered by any interval."""
        if total_duration <= 0:
            return []

        if not intervals:
            return [{
                "start_ms": 0,
                "end_ms": total_duration,
                "duration_ms": total_duration,
            }]

        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged: list[tuple[int, int]] = [sorted_intervals[0]]

        for start, end in sorted_intervals[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        uncovered: list[dict] = []
        current = 0
        for start, end in merged:
            if start > current and start - current > GAP_THRESHOLD_MS:
                uncovered.append({
                    "start_ms": current,
                    "end_ms": start,
                    "duration_ms": start - current,
                })
            current = max(current, end)

        if total_duration > current and total_duration - current > GAP_THRESHOLD_MS:
            uncovered.append({
                "start_ms": current,
                "end_ms": total_duration,
                "duration_ms": total_duration - current,
            })

        return uncovered

    # =========================================================================
    # Layer Coverage Analysis
    # =========================================================================

    def analyze_layer_coverage(self) -> dict:
        """Analyze how well each layer covers the timeline.

        Returns:
            {
                "layers": [
                    {
                        "layer_id": str,
                        "layer_name": str,
                        "type": str,
                        "clip_count": int,
                        "coverage_ms": int,
                        "coverage_pct": float,
                    }
                ]
            }
        """
        layers_info: list[dict] = []

        for layer in self.timeline.get("layers", []):
            clips = layer.get("clips", [])
            intervals = [
                (c.get("start_ms", 0), c.get("start_ms", 0) + c.get("duration_ms", 0))
                for c in clips
            ]
            coverage_ms = self._merged_coverage(intervals)
            coverage_pct = round(
                (coverage_ms / self.project_duration_ms) * 100, 1
            ) if self.project_duration_ms > 0 else 0.0

            layers_info.append({
                "layer_id": layer.get("id", ""),
                "layer_name": layer.get("name", ""),
                "type": layer.get("type", ""),
                "clip_count": len(clips),
                "coverage_ms": coverage_ms,
                "coverage_pct": coverage_pct,
            })

        return {"layers": layers_info}

    # =========================================================================
    # Suggestion Generation
    # =========================================================================

    def generate_suggestions(
        self,
        gap_analysis: dict | None = None,
        pacing_analysis: dict | None = None,
        audio_analysis: dict | None = None,
        layer_coverage: dict | None = None,
    ) -> list[dict]:
        """Generate actionable improvement suggestions based on analysis results.

        If any analysis dict is None, it will be computed on the fly.
        """
        if gap_analysis is None:
            gap_analysis = self.analyze_gaps()
        if pacing_analysis is None:
            pacing_analysis = self.analyze_pacing()
        if audio_analysis is None:
            audio_analysis = self.analyze_audio()
        if layer_coverage is None:
            layer_coverage = self.analyze_layer_coverage()

        suggestions: list[dict] = []

        # --- Gap-based suggestions ---
        for layer_info in gap_analysis.get("layers", []):
            for gap in layer_info.get("gaps", []):
                if gap["duration_ms"] > 1000:  # Only suggest for significant gaps
                    suggestions.append({
                        "priority": "medium",
                        "category": "gap",
                        "message": (
                            f"Gap of {gap['duration_ms']}ms in "
                            f"{layer_info['layer_name']} ({layer_info['type']}) "
                            f"from {gap['start_ms']}ms to {gap['end_ms']}ms"
                        ),
                        "suggested_operation": {
                            "description": "Add a clip to fill the gap",
                            "endpoint": "POST /api/ai/v1/projects/{{project_id}}/clips",
                            "parameters": {
                                "layer_id": layer_info["layer_id"],
                                "start_ms": gap["start_ms"],
                                "duration_ms": gap["duration_ms"],
                            },
                        },
                    })

        # --- Background coverage suggestion ---
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "background" and layer_info["coverage_pct"] < 100:
                suggestions.append({
                    "priority": "high",
                    "category": "missing_background",
                    "message": (
                        f"Background layer covers only {layer_info['coverage_pct']}% "
                        f"of the timeline. The full timeline should have a background."
                    ),
                    "suggested_operation": {
                        "description": "Add or extend background clips to cover full timeline",
                        "endpoint": "POST /api/ai/v1/projects/{{project_id}}/clips",
                        "parameters": {
                            "layer_id": layer_info["layer_id"],
                            "type": "background",
                        },
                    },
                })

        # --- Audio suggestions ---
        narration_pct = audio_analysis.get("narration_coverage_pct", 0)
        if 0 < narration_pct < 80:
            suggestions.append({
                "priority": "high",
                "category": "low_narration",
                "message": (
                    f"Narration covers only {narration_pct}% of the timeline. "
                    "Udemy lectures typically require >80% narration coverage."
                ),
                "suggested_operation": {
                    "description": "Add narration clips to uncovered intervals",
                    "endpoint": "POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                    "parameters": {
                        "track_type": "narration",
                    },
                },
            })

        if audio_analysis.get("bgm_coverage_pct", 0) == 0 and self.project_duration_ms > 0:
            suggestions.append({
                "priority": "low",
                "category": "missing_bgm",
                "message": "No BGM detected. Consider adding background music for better engagement.",
                "suggested_operation": {
                    "description": "Add a BGM clip spanning the full timeline",
                    "endpoint": "POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                    "parameters": {
                        "track_type": "bgm",
                    },
                },
            })

        for silent in audio_analysis.get("silent_intervals", []):
            if silent["duration_ms"] > 3000:  # Only flag silence >3s
                suggestions.append({
                    "priority": "medium",
                    "category": "silence",
                    "message": (
                        f"Silent interval of {silent['duration_ms']}ms "
                        f"from {silent['start_ms']}ms to {silent['end_ms']}ms. "
                        "Consider adding narration or BGM."
                    ),
                    "suggested_operation": {
                        "description": "Add audio to fill silence",
                        "endpoint": "POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                        "parameters": {
                            "start_ms": silent["start_ms"],
                            "duration_ms": silent["duration_ms"],
                        },
                    },
                })

        # --- Pacing suggestions ---
        for issue in pacing_analysis.get("pacing_issues", []):
            priority = "medium"
            if "too_fast" in issue:
                suggestions.append({
                    "priority": priority,
                    "category": "pacing",
                    "message": f"Pacing issue: {issue}. Consider merging or extending short clips.",
                    "suggested_operation": None,
                })
            elif "too_slow" in issue:
                suggestions.append({
                    "priority": priority,
                    "category": "pacing",
                    "message": f"Pacing issue: {issue}. Consider splitting long clips.",
                    "suggested_operation": None,
                })

        # --- Text/telop layer check ---
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "text" and layer_info["clip_count"] == 0:
                suggestions.append({
                    "priority": "low",
                    "category": "missing_text",
                    "message": (
                        "No text/telop clips found. "
                        "Consider adding subtitles or captions for better accessibility."
                    ),
                    "suggested_operation": {
                        "description": "Add text overlay clips",
                        "endpoint": "POST /api/ai/v1/projects/{{project_id}}/semantic",
                        "parameters": {
                            "operation": "add_text_with_timing",
                        },
                    },
                })

        # Sort by priority: high > medium > low
        priority_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(key=lambda s: priority_order.get(s["priority"], 99))

        return suggestions

    # =========================================================================
    # Quality Score
    # =========================================================================

    def calculate_quality_score(
        self,
        gap_analysis: dict | None = None,
        pacing_analysis: dict | None = None,
        audio_analysis: dict | None = None,
        layer_coverage: dict | None = None,
    ) -> int:
        """Calculate 0-100 quality score.

        Scoring breakdown:
        - Background coverage: 25 points (100% coverage = 25 points)
        - Narration coverage: 25 points (>=80% = 25 points)
        - Gap-free layers: 25 points (no significant gaps = 25 points)
        - Pacing: 25 points (no pacing issues = 25 points)
        """
        if self.project_duration_ms == 0:
            return 0

        if gap_analysis is None:
            gap_analysis = self.analyze_gaps()
        if pacing_analysis is None:
            pacing_analysis = self.analyze_pacing()
        if audio_analysis is None:
            audio_analysis = self.analyze_audio()
        if layer_coverage is None:
            layer_coverage = self.analyze_layer_coverage()

        score = 0

        # --- Background coverage: 25 points ---
        bg_coverage = 0.0
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "background":
                bg_coverage = layer_info["coverage_pct"]
                break
        score += round(min(bg_coverage / 100, 1.0) * 25)

        # --- Narration coverage: 25 points ---
        narration_pct = audio_analysis.get("narration_coverage_pct", 0)
        if narration_pct >= 80:
            score += 25
        else:
            # Linear scale: 0% -> 0 points, 80% -> 25 points
            score += round((narration_pct / 80) * 25)

        # --- Gap-free: 25 points ---
        total_gaps = gap_analysis.get("total_gaps", 0)
        total_gap_duration = gap_analysis.get("total_gap_duration_ms", 0)
        if total_gaps == 0:
            score += 25
        else:
            # Penalize based on gap duration relative to project duration
            gap_ratio = total_gap_duration / self.project_duration_ms if self.project_duration_ms > 0 else 1
            gap_score = max(0, 25 - round(gap_ratio * 50))  # -2 points per 1% gap
            score += gap_score

        # --- Pacing: 25 points ---
        pacing_issues = pacing_analysis.get("pacing_issues", [])
        if not pacing_issues:
            score += 25
        else:
            # Deduct points per issue
            deduction = len(pacing_issues) * 10
            score += max(0, 25 - deduction)

        return min(100, max(0, score))
