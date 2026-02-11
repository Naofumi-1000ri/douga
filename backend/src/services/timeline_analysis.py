"""Timeline composition analysis service.

Provides comprehensive quality analysis of timeline compositions,
including gap detection, pacing analysis, audio coverage, and
actionable improvement suggestions for AI agents.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

# Threshold constants
GAP_THRESHOLD_MS = 100  # Minimum gap duration to report
SHORT_CLIP_MS = 2000  # Clips shorter than this are "too fast"
LONG_CLIP_MS = 15000  # Clips longer than this are "too slow"
SHORT_CLIP_RATIO = 0.5  # If >50% of clips are short, flag as too_fast
LONG_CLIP_RATIO = 0.3  # If >30% of clips are long, flag as too_slow
SECTION_GAP_MS = 500  # Minimum gap in primary content to detect section boundary


class TimelineAnalyzer:
    """Analyzes timeline composition quality for AI agents."""

    def __init__(
        self,
        timeline_data: dict,
        asset_map: dict[str, dict] | None = None,
        project_id: str | None = None,
    ):
        self.timeline = timeline_data or {}
        self.asset_map = asset_map or {}
        self.project_id = project_id
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
        score_result = self.calculate_quality_score_with_context(
            gap_analysis, pacing_analysis, audio_analysis, layer_coverage
        )

        sections = self.detect_sections()

        audio_balance = self.analyze_audio_balance()

        return {
            "project_duration_ms": self.project_duration_ms,
            "gap_analysis": gap_analysis,
            "pacing_analysis": pacing_analysis,
            "audio_analysis": audio_analysis,
            "audio_balance": audio_balance,
            "layer_coverage": layer_coverage,
            "suggestions": suggestions,
            "quality_score": score_result["score"],
            "score_context": score_result["score_context"],
            "sections": sections,
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
    # Audio Balance Analysis (detailed)
    # =========================================================================

    def analyze_audio_balance(self) -> dict:
        """Detailed audio balance analysis across all tracks.

        Returns per-track stats (clip count, coverage, volume consistency,
        ducking status), cross-track issues (missing BGM, ducking not enabled,
        audio-video misalignment), silent intervals, recommendations, and an
        overall audio_score (0-100).
        """
        if self.project_duration_ms == 0:
            return {
                "tracks": [],
                "cross_track_issues": [],
                "silent_intervals": [],
                "recommendations": [],
                "audio_score": 0,
            }

        tracks_result: list[dict] = []
        all_audio_intervals: list[tuple[int, int]] = []
        narration_intervals: list[tuple[int, int]] = []
        bgm_intervals: list[tuple[int, int]] = []
        has_bgm_track = False
        has_bgm_clips = False
        bgm_ducking_enabled = False
        narration_has_clips = False

        # --- Collect audio group_ids ---
        audio_group_ids: set[str] = set()

        for track in self.timeline.get("audio_tracks", []):
            track_type = track.get("type", "")
            track_id = track.get("id", "")
            track_name = track.get("name", "")
            clips = track.get("clips", [])
            ducking = track.get("ducking", {})
            has_ducking = bool(ducking.get("enabled", False))

            if track_type == "narration":
                if clips:
                    narration_has_clips = True
            elif track_type == "bgm":
                has_bgm_track = True
                bgm_ducking_enabled = has_ducking
                if clips:
                    has_bgm_clips = True

            # Per-clip volume analysis
            volumes: list[float] = []
            issues: list[dict] = []
            intervals: list[tuple[int, int]] = []

            for clip in clips:
                vol = clip.get("volume", 1.0)
                volumes.append(vol)
                start = clip.get("start_ms", 0)
                end = start + clip.get("duration_ms", 0)
                intervals.append((start, end))
                gid = clip.get("group_id")
                if gid:
                    audio_group_ids.add(gid)

            all_audio_intervals.extend(intervals)
            if track_type == "narration":
                narration_intervals.extend(intervals)
            elif track_type == "bgm":
                bgm_intervals.extend(intervals)

            # Coverage
            coverage_ms = self._merged_coverage(intervals)
            coverage_pct = round(
                (coverage_ms / self.project_duration_ms) * 100, 1
            ) if self.project_duration_ms > 0 else 0.0

            # Volume stats
            avg_volume = round(sum(volumes) / len(volumes), 2) if volumes else 0.0
            vol_min = round(min(volumes), 2) if volumes else 0.0
            vol_max = round(max(volumes), 2) if volumes else 0.0

            # Volume inconsistency check
            if len(volumes) >= 2 and (vol_max - vol_min) > 0.3:
                affected = [
                    clip.get("id", "")
                    for clip in clips
                    if abs(clip.get("volume", 1.0) - avg_volume) > 0.15
                ]
                issues.append({
                    "type": "volume_inconsistency",
                    "message": (
                        f"Volume varies from {vol_min} to {vol_max} across clips"
                    ),
                    "affected_clips": affected,
                    "suggested_fix": (
                        f"Normalize volume to {avg_volume} across all "
                        f"{track_name} clips"
                    ),
                })

            tracks_result.append({
                "track_id": track_id,
                "track_name": track_name,
                "track_type": track_type,
                "clip_count": len(clips),
                "total_duration_ms": coverage_ms,
                "coverage_pct": coverage_pct,
                "avg_volume": avg_volume,
                "volume_range": {"min": vol_min, "max": vol_max},
                "has_ducking": has_ducking,
                "issues": issues,
            })

        # --- Cross-track issues ---
        cross_track_issues: list[dict] = []

        # No BGM
        if has_bgm_track and not has_bgm_clips:
            cross_track_issues.append({
                "type": "no_bgm",
                "message": (
                    "No BGM track has any clips. "
                    "Consider adding background music."
                ),
                "time_range": {
                    "start_ms": 0,
                    "end_ms": self.project_duration_ms,
                },
            })
        elif not has_bgm_track:
            cross_track_issues.append({
                "type": "no_bgm",
                "message": (
                    "No BGM track exists. "
                    "Consider adding a BGM track with background music."
                ),
                "time_range": {
                    "start_ms": 0,
                    "end_ms": self.project_duration_ms,
                },
            })

        # Narration overlaps with BGM but no ducking
        if narration_has_clips and has_bgm_clips and not bgm_ducking_enabled:
            # Check actual overlap
            has_overlap = self._intervals_overlap(
                narration_intervals, bgm_intervals
            )
            if has_overlap:
                cross_track_issues.append({
                    "type": "narration_without_ducking",
                    "message": (
                        "Narration overlaps with BGM but auto-ducking "
                        "is not enabled"
                    ),
                    "affected_tracks": ["narration", "bgm"],
                })

        # Audio-video misalignment: video clips with group_id that have
        # no matching audio clip
        for layer in self.timeline.get("layers", []):
            for clip in layer.get("clips", []):
                gid = clip.get("group_id")
                if gid and gid not in audio_group_ids:
                    cross_track_issues.append({
                        "type": "audio_video_misalignment",
                        "message": (
                            f"Video clip at {clip.get('start_ms', 0)}ms "
                            f"has no matching audio (no group_id link)"
                        ),
                        "video_clip_id": clip.get("id", ""),
                        "time_ms": clip.get("start_ms", 0),
                    })

        # --- Silent intervals ---
        silent_intervals = self._find_uncovered_intervals(
            all_audio_intervals, self.project_duration_ms
        )

        # --- Recommendations ---
        recommendations: list[str] = []
        if not has_bgm_clips:
            recommendations.append("Add BGM to fill silent intervals")
        if narration_has_clips and has_bgm_clips and not bgm_ducking_enabled:
            recommendations.append(
                "Enable auto-ducking on BGM track for narration clarity"
            )
        # Check for volume normalization needs
        for t_info in tracks_result:
            if t_info["issues"]:
                for issue in t_info["issues"]:
                    if issue["type"] == "volume_inconsistency":
                        recommendations.append(
                            f"Normalize {t_info['track_name']} volume to "
                            f"{t_info['avg_volume']}"
                        )

        # --- Audio score (0-100) ---
        # Narration coverage: 30 points
        narration_coverage_ms = self._merged_coverage(narration_intervals)
        narration_pct = (
            (narration_coverage_ms / self.project_duration_ms) * 100
            if self.project_duration_ms > 0
            else 0.0
        )
        if narration_pct >= 80:
            narration_score = 30
        else:
            narration_score = round((narration_pct / 80) * 30)

        # BGM existence: 20 points
        bgm_score = 20 if has_bgm_clips else 0

        # Volume consistency: 25 points
        inconsistency_count = sum(
            1
            for t_info in tracks_result
            if any(i["type"] == "volume_inconsistency" for i in t_info["issues"])
        )
        if inconsistency_count == 0:
            volume_score = 25
        else:
            volume_score = max(0, 25 - inconsistency_count * 10)

        # Ducking: 25 points
        if not has_bgm_clips:
            # No BGM, ducking is irrelevant - give partial credit
            ducking_score = 15
        elif bgm_ducking_enabled:
            ducking_score = 25
        else:
            ducking_score = 0

        audio_score = min(
            100,
            max(0, narration_score + bgm_score + volume_score + ducking_score),
        )

        return {
            "tracks": tracks_result,
            "cross_track_issues": cross_track_issues,
            "silent_intervals": silent_intervals,
            "recommendations": recommendations,
            "audio_score": audio_score,
        }

    def _intervals_overlap(
        self,
        intervals_a: list[tuple[int, int]],
        intervals_b: list[tuple[int, int]],
    ) -> bool:
        """Check if any interval in A overlaps with any interval in B."""
        if not intervals_a or not intervals_b:
            return False

        # Merge intervals_a
        sorted_a = sorted(intervals_a, key=lambda x: x[0])
        merged_a: list[tuple[int, int]] = [sorted_a[0]]
        for start, end in sorted_a[1:]:
            if start <= merged_a[-1][1]:
                merged_a[-1] = (merged_a[-1][0], max(merged_a[-1][1], end))
            else:
                merged_a.append((start, end))

        # Merge intervals_b
        sorted_b = sorted(intervals_b, key=lambda x: x[0])
        merged_b: list[tuple[int, int]] = [sorted_b[0]]
        for start, end in sorted_b[1:]:
            if start <= merged_b[-1][1]:
                merged_b[-1] = (merged_b[-1][0], max(merged_b[-1][1], end))
            else:
                merged_b.append((start, end))

        # Two-pointer overlap check
        i, j = 0, 0
        while i < len(merged_a) and j < len(merged_b):
            a_start, a_end = merged_a[i]
            b_start, b_end = merged_b[j]
            if a_start < b_end and b_start < a_end:
                return True
            if a_end <= b_start:
                i += 1
            else:
                j += 1

        return False

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
    # Section Detection
    # =========================================================================

    def detect_sections(self) -> list[dict]:
        """Detect logical sections/segments in the timeline.

        Sections are detected by:
        1. Gaps in the primary content layer (>SECTION_GAP_MS gap = section boundary)
        2. Marker positions (explicit section markers)
        3. Background changes (different background clips = different sections)

        Returns a list of section dicts sorted by start_ms.
        """
        if self.project_duration_ms == 0:
            return []

        # --- Step 1: Find content-layer clip boundaries ---
        content_clips: list[dict] = []
        for layer in self.timeline.get("layers", []):
            if layer.get("type", "content") == "content":
                content_clips.extend(layer.get("clips", []))

        # If no content layer, fall back to all layers
        if not content_clips:
            for layer in self.timeline.get("layers", []):
                content_clips.extend(layer.get("clips", []))

        if not content_clips:
            # No clips at all -- return single section spanning the timeline
            return [self._build_section(
                section_index=0,
                name="Section 1",
                start_ms=0,
                end_ms=self.project_duration_ms,
                clip_ids=[],
            )]

        sorted_clips = sorted(content_clips, key=lambda c: c.get("start_ms", 0))

        # --- Step 2: Collect boundary timestamps from content gaps ---
        boundaries: list[int] = []
        current_end = sorted_clips[0].get("start_ms", 0) + sorted_clips[0].get("duration_ms", 0)
        for clip in sorted_clips[1:]:
            clip_start = clip.get("start_ms", 0)
            if clip_start - current_end > SECTION_GAP_MS:
                boundaries.append(clip_start)
            current_end = max(current_end, clip_start + clip.get("duration_ms", 0))

        # --- Step 3: Add marker positions as boundaries ---
        markers = self.timeline.get("markers", [])
        marker_map: dict[int, str] = {}  # time_ms -> marker name
        for marker in markers:
            t = marker.get("time_ms", 0)
            if 0 < t < self.project_duration_ms:
                boundaries.append(t)
                marker_map[t] = marker.get("name", "")

        # --- Step 4: Add background-change boundaries ---
        bg_clips: list[dict] = []
        for layer in self.timeline.get("layers", []):
            if layer.get("type") == "background":
                bg_clips.extend(layer.get("clips", []))
        if len(bg_clips) > 1:
            sorted_bg = sorted(bg_clips, key=lambda c: c.get("start_ms", 0))
            for bg_clip in sorted_bg[1:]:
                bg_start = bg_clip.get("start_ms", 0)
                if 0 < bg_start < self.project_duration_ms:
                    boundaries.append(bg_start)

        # --- Step 5: Deduplicate and sort boundaries ---
        # Merge boundaries that are within SECTION_GAP_MS of each other
        unique_boundaries = sorted(set(boundaries))
        merged_boundaries: list[int] = []
        for b in unique_boundaries:
            if not merged_boundaries or b - merged_boundaries[-1] > SECTION_GAP_MS:
                merged_boundaries.append(b)
            else:
                # Keep the one that has a marker name, if any
                if b in marker_map and merged_boundaries[-1] not in marker_map:
                    merged_boundaries[-1] = b

        # --- Step 6: Build section list ---
        section_starts = [0] + merged_boundaries
        section_ends = merged_boundaries + [self.project_duration_ms]
        sections: list[dict] = []

        for idx, (s_start, s_end) in enumerate(zip(section_starts, section_ends)):
            if s_end <= s_start:
                continue

            # Find closest marker name for this section start
            name = marker_map.get(s_start, "")
            if not name:
                # Check if any marker is close to this boundary
                for t, mname in marker_map.items():
                    if abs(t - s_start) <= SECTION_GAP_MS and mname:
                        name = mname
                        break
            if not name:
                name = f"Section {idx + 1}"

            # Collect clip IDs that overlap this section
            clip_ids: list[str] = []
            for layer in self.timeline.get("layers", []):
                for clip in layer.get("clips", []):
                    c_start = clip.get("start_ms", 0)
                    c_end = c_start + clip.get("duration_ms", 0)
                    if c_start < s_end and c_end > s_start:
                        cid = clip.get("id", "")
                        if cid:
                            clip_ids.append(cid)

            sections.append(self._build_section(
                section_index=idx,
                name=name,
                start_ms=s_start,
                end_ms=s_end,
                clip_ids=clip_ids,
            ))

        return sections

    def _build_section(
        self,
        section_index: int,
        name: str,
        start_ms: int,
        end_ms: int,
        clip_ids: list[str],
    ) -> dict:
        """Build a section dict with metadata about what the section contains."""
        has_narration = False
        has_background = False
        has_text = False
        suggested_improvements: list[str] = []

        # Check narration coverage
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != "narration":
                continue
            for clip in track.get("clips", []):
                c_start = clip.get("start_ms", 0)
                c_end = c_start + clip.get("duration_ms", 0)
                if c_start < end_ms and c_end > start_ms:
                    has_narration = True
                    break
            if has_narration:
                break

        # Check background coverage
        for layer in self.timeline.get("layers", []):
            if layer.get("type") != "background":
                continue
            for clip in layer.get("clips", []):
                c_start = clip.get("start_ms", 0)
                c_end = c_start + clip.get("duration_ms", 0)
                if c_start < end_ms and c_end > start_ms:
                    has_background = True
                    break
            if has_background:
                break

        # Check text coverage
        for layer in self.timeline.get("layers", []):
            if layer.get("type") != "text":
                continue
            for clip in layer.get("clips", []):
                c_start = clip.get("start_ms", 0)
                c_end = c_start + clip.get("duration_ms", 0)
                if c_start < end_ms and c_end > start_ms:
                    has_text = True
                    break
            if has_text:
                break

        # Generate suggestions
        if not has_narration:
            suggested_improvements.append("Add narration for this section")
        if not has_background:
            suggested_improvements.append("Add background for this section")
        if not has_text:
            suggested_improvements.append("Add text overlay for this section")

        return {
            "section_index": section_index,
            "name": name,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "clip_ids": clip_ids,
            "has_narration": has_narration,
            "has_background": has_background,
            "has_text": has_text,
            "suggested_improvements": suggested_improvements,
        }

    # =========================================================================
    # Suggestion Generation
    # =========================================================================

    def _make_suggested_operation(
        self,
        endpoint: str,
        method: str,
        body: dict,
        description: str,
    ) -> dict:
        """Build a copy-paste ready suggested_operation with full request body.

        If project_id is set, replaces {{project_id}} placeholders in the
        endpoint path. Idempotency-Key is always a real UUID so agents can
        execute the suggestion with zero modification.
        """
        resolved_endpoint = endpoint
        if self.project_id:
            resolved_endpoint = endpoint.replace("{{project_id}}", self.project_id)
        return {
            "description": description,
            "endpoint": resolved_endpoint,
            "method": method,
            "body": body,
            "headers": {
                "Idempotency-Key": str(uuid.uuid4()),
            },
        }

    def generate_suggestions(
        self,
        gap_analysis: dict | None = None,
        pacing_analysis: dict | None = None,
        audio_analysis: dict | None = None,
        layer_coverage: dict | None = None,
    ) -> list[dict]:
        """Generate actionable improvement suggestions based on analysis results.

        If any analysis dict is None, it will be computed on the fly.

        Priority rules:
        - high: section missing text entirely, section missing narration entirely,
                background coverage <90%, gaps >=20s
        - medium: gaps 10s-20s, low narration (<80%), pacing issues
        - low: gaps <10s, missing BGM, missing text (non-section-level)
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

        # --- Gap-based suggestions with improved priority ---
        for layer_info in gap_analysis.get("layers", []):
            for gap in layer_info.get("gaps", []):
                if gap["duration_ms"] > 1000:  # Only suggest for significant gaps
                    # Priority based on gap duration
                    if gap["duration_ms"] >= 20000:
                        gap_priority = "high"
                    elif gap["duration_ms"] >= 10000:
                        gap_priority = "medium"
                    else:
                        gap_priority = "low"

                    suggestions.append({
                        "priority": gap_priority,
                        "category": "gap",
                        "message": (
                            f"Gap of {gap['duration_ms']}ms in "
                            f"{layer_info['layer_name']} ({layer_info['type']}) "
                            f"from {gap['start_ms']}ms to {gap['end_ms']}ms"
                        ),
                        "suggested_operation": self._make_suggested_operation(
                            endpoint="POST /api/ai/v1/projects/{{project_id}}/clips",
                            method="POST",
                            body={
                                "clip": {
                                    "layer_id": layer_info["layer_id"],
                                    "start_ms": gap["start_ms"],
                                    "duration_ms": gap["duration_ms"],
                                },
                                "options": {},
                            },
                            description="Add a clip to fill the gap",
                        ),
                    })

        # --- Background coverage suggestion ---
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "background" and layer_info["coverage_pct"] < 100:
                # High priority if coverage < 90%
                bg_priority = "high" if layer_info["coverage_pct"] < 90 else "medium"
                suggestions.append({
                    "priority": bg_priority,
                    "category": "missing_background",
                    "message": (
                        f"Background layer covers only {layer_info['coverage_pct']}% "
                        f"of the timeline. The full timeline should have a background."
                    ),
                    "suggested_operation": self._make_suggested_operation(
                        endpoint="POST /api/ai/v1/projects/{{project_id}}/clips",
                        method="POST",
                        body={
                            "clip": {
                                "layer_id": layer_info["layer_id"],
                                "start_ms": 0,
                                "duration_ms": self.project_duration_ms,
                            },
                            "options": {},
                        },
                        description="Add or extend background clips to cover full timeline",
                    ),
                })

        # --- Audio suggestions ---
        narration_pct = audio_analysis.get("narration_coverage_pct", 0)

        # Section-level checks for high priority
        sections = self.detect_sections()
        for section in sections:
            if not section.get("has_text"):
                suggestions.append({
                    "priority": "high",
                    "category": "missing_text_section",
                    "message": (
                        f"Section '{section['name']}' ({section['start_ms']}ms-{section['end_ms']}ms) "
                        f"has no text overlay. Add subtitles or captions."
                    ),
                    "suggested_operation": self._make_suggested_operation(
                        endpoint="POST /api/ai/v1/projects/{{project_id}}/semantic",
                        method="POST",
                        body={
                            "operation": {
                                "operation": "add_text_with_timing",
                                "parameters": {
                                    "text": "テキストを入力",
                                    "position": "bottom",
                                    "start_ms": section["start_ms"],
                                    "duration_ms": section["duration_ms"],
                                },
                            },
                            "options": {},
                        },
                        description=f"Add text overlay for section '{section['name']}'",
                    ),
                })
            if not section.get("has_narration"):
                suggestions.append({
                    "priority": "high",
                    "category": "missing_narration_section",
                    "message": (
                        f"Section '{section['name']}' ({section['start_ms']}ms-{section['end_ms']}ms) "
                        f"has no narration. Add narration audio."
                    ),
                    "suggested_operation": self._make_suggested_operation(
                        endpoint="POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                        method="POST",
                        body={
                            "clip": {
                                "track_type": "narration",
                                "start_ms": section["start_ms"],
                                "duration_ms": section["duration_ms"],
                            },
                            "options": {},
                        },
                        description=f"Add narration for section '{section['name']}'",
                    ),
                })

        if 0 < narration_pct < 80:
            suggestions.append({
                "priority": "high",
                "category": "low_narration",
                "message": (
                    f"Narration covers only {narration_pct}% of the timeline. "
                    "Udemy lectures typically require >80% narration coverage."
                ),
                "suggested_operation": self._make_suggested_operation(
                    endpoint="POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                    method="POST",
                    body={
                        "clip": {
                            "track_type": "narration",
                        },
                        "options": {},
                    },
                    description="Add narration clips to uncovered intervals",
                ),
            })

        if audio_analysis.get("bgm_coverage_pct", 0) == 0 and self.project_duration_ms > 0:
            suggestions.append({
                "priority": "low",
                "category": "missing_bgm",
                "message": "No BGM detected. Consider adding background music for better engagement.",
                "suggested_operation": self._make_suggested_operation(
                    endpoint="POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                    method="POST",
                    body={
                        "clip": {
                            "track_type": "bgm",
                            "start_ms": 0,
                            "duration_ms": self.project_duration_ms,
                        },
                        "options": {},
                    },
                    description="Add a BGM clip spanning the full timeline",
                ),
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
                    "suggested_operation": self._make_suggested_operation(
                        endpoint="POST /api/ai/v1/projects/{{project_id}}/audio-clips",
                        method="POST",
                        body={
                            "clip": {
                                "start_ms": silent["start_ms"],
                                "duration_ms": silent["duration_ms"],
                            },
                            "options": {},
                        },
                        description="Add audio to fill silence",
                    ),
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

        # --- Text/telop layer check (non-section-level, low priority) ---
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "text" and layer_info["clip_count"] == 0:
                suggestions.append({
                    "priority": "low",
                    "category": "missing_text",
                    "message": (
                        "No text/telop clips found. "
                        "Consider adding subtitles or captions for better accessibility."
                    ),
                    "suggested_operation": self._make_suggested_operation(
                        endpoint="POST /api/ai/v1/projects/{{project_id}}/semantic",
                        method="POST",
                        body={
                            "operation": {
                                "operation": "add_text_with_timing",
                            },
                            "options": {},
                        },
                        description="Add text overlay clips",
                    ),
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
        result = self.calculate_quality_score_with_context(
            gap_analysis, pacing_analysis, audio_analysis, layer_coverage
        )
        return result["score"]

    def calculate_quality_score_with_context(
        self,
        gap_analysis: dict | None = None,
        pacing_analysis: dict | None = None,
        audio_analysis: dict | None = None,
        layer_coverage: dict | None = None,
    ) -> dict:
        """Calculate 0-100 quality score with per-category breakdown and tips.

        Returns:
            {
                "score": int (0-100),
                "score_context": {
                    "breakdown": {
                        "background_coverage": {"score": int, "max": 25, "detail": str},
                        "narration_coverage": {"score": int, "max": 25, "detail": str},
                        "gap_free": {"score": int, "max": 25, "detail": str},
                        "pacing": {"score": int, "max": 25, "detail": str},
                    },
                    "improvement_tips": [str, ...]
                }
            }
        """
        if self.project_duration_ms == 0:
            return {
                "score": 0,
                "score_context": {
                    "breakdown": {
                        "background_coverage": {"score": 0, "max": 25, "detail": "No timeline content"},
                        "narration_coverage": {"score": 0, "max": 25, "detail": "No timeline content"},
                        "gap_free": {"score": 0, "max": 25, "detail": "No timeline content"},
                        "pacing": {"score": 0, "max": 25, "detail": "No timeline content"},
                    },
                    "improvement_tips": ["Add clips to the timeline to begin editing"],
                },
            }

        if gap_analysis is None:
            gap_analysis = self.analyze_gaps()
        if pacing_analysis is None:
            pacing_analysis = self.analyze_pacing()
        if audio_analysis is None:
            audio_analysis = self.analyze_audio()
        if layer_coverage is None:
            layer_coverage = self.analyze_layer_coverage()

        tips: list[str] = []

        # --- Background coverage: 25 points ---
        bg_coverage = 0.0
        for layer_info in layer_coverage.get("layers", []):
            if layer_info["type"] == "background":
                bg_coverage = layer_info["coverage_pct"]
                break
        bg_score = round(min(bg_coverage / 100, 1.0) * 25)
        bg_detail = f"{bg_coverage:.0f}% coverage"
        if bg_score < 25:
            missing_pct = 100 - bg_coverage
            tips.append(f"Extend background to cover remaining {missing_pct:.0f}% of timeline")

        # --- Narration coverage: 25 points ---
        narration_pct = audio_analysis.get("narration_coverage_pct", 0)
        if narration_pct >= 80:
            narr_score = 25
        else:
            narr_score = round((narration_pct / 80) * 25)
        narr_detail = f"{narration_pct:.0f}% coverage (>=80% for full score)"
        if narr_score < 25:
            uncovered_pct = max(0, 80 - narration_pct)
            tips.append(f"Add narration to cover {uncovered_pct:.0f}% more of the timeline (target: 80%)")

        # --- Gap-free: 25 points ---
        total_gaps = gap_analysis.get("total_gaps", 0)
        total_gap_duration = gap_analysis.get("total_gap_duration_ms", 0)
        if total_gaps == 0:
            gap_score = 25
            gap_detail = "No significant gaps"
        else:
            gap_ratio = total_gap_duration / self.project_duration_ms if self.project_duration_ms > 0 else 1
            gap_score = max(0, 25 - round(gap_ratio * 50))
            gap_detail = f"{total_gaps} gaps totaling {total_gap_duration}ms"
            tips.append(f"Fill {total_gaps} gap(s) ({total_gap_duration}ms total) across layers")

        # --- Pacing: 25 points ---
        pacing_issues = pacing_analysis.get("pacing_issues", [])
        if not pacing_issues:
            pacing_score = 25
            pacing_detail = "No pacing issues"
        else:
            deduction = len(pacing_issues) * 10
            pacing_score = max(0, 25 - deduction)
            issue_types = [i.get("type", "unknown") for i in pacing_issues]
            pacing_detail = f"{len(pacing_issues)} issue(s): {', '.join(issue_types)}"
            if any(i.get("type") == "too_fast" for i in pacing_issues):
                tips.append("Extend short clips or merge adjacent clips for better pacing")
            if any(i.get("type") == "too_slow" for i in pacing_issues):
                tips.append("Split long clips or add transitions to improve pacing")

        total_score = min(100, max(0, bg_score + narr_score + gap_score + pacing_score))

        return {
            "score": total_score,
            "score_context": {
                "breakdown": {
                    "background_coverage": {"score": bg_score, "max": 25, "detail": bg_detail},
                    "narration_coverage": {"score": narr_score, "max": 25, "detail": narr_detail},
                    "gap_free": {"score": gap_score, "max": 25, "detail": gap_detail},
                    "pacing": {"score": pacing_score, "max": 25, "detail": pacing_detail},
                },
                "improvement_tips": tips,
            },
        }
