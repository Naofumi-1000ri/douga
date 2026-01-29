"""Composition rule validation engine.

Validates timeline composition without rendering, checking for common issues:
- Overlapping clips on the same layer
- Clips extending beyond timeline duration
- Missing assets
- Avatar/text placement outside safe zones
- Empty layers with no content
- Audio track mismatches
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A detected composition issue."""

    rule: str
    severity: str  # "error", "warning", "info"
    message: str
    time_ms: int | None = None
    clip_id: str | None = None
    layer: str | None = None
    suggestion: str | None = None


class CompositionValidator:
    """Validates timeline composition rules without rendering."""

    # Safe zone margins (percentage of canvas)
    SAFE_ZONE_MARGIN = 0.05  # 5% from edges (title safe zone)

    def __init__(
        self,
        timeline_data: dict[str, Any],
        project_width: int = 1920,
        project_height: int = 1080,
        asset_ids: set[str] | None = None,
    ):
        self.timeline = timeline_data
        self.width = project_width
        self.height = project_height
        self.duration_ms = timeline_data.get("duration_ms", 0)
        self.known_asset_ids = asset_ids or set()

    def validate(self, rules: list[str] | None = None) -> list[ValidationIssue]:
        """Run all or selected validation rules.

        Args:
            rules: Specific rules to run. None = all rules.

        Returns:
            List of validation issues found.
        """
        all_rules = {
            "overlapping_clips": self._check_overlapping_clips,
            "clip_bounds": self._check_clip_bounds,
            "missing_assets": self._check_missing_assets,
            "safe_zone": self._check_safe_zone,
            "empty_layers": self._check_empty_layers,
            "audio_sync": self._check_audio_sync,
            "duration_consistency": self._check_duration_consistency,
            "text_readability": self._check_text_readability,
            "layer_ordering": self._check_layer_ordering,
            "gap_detection": self._check_visual_gaps,
        }

        selected_rules = rules if rules else list(all_rules.keys())
        issues: list[ValidationIssue] = []

        for rule_name in selected_rules:
            rule_fn = all_rules.get(rule_name)
            if rule_fn:
                try:
                    issues.extend(rule_fn())
                except Exception as e:
                    logger.warning(f"Validation rule '{rule_name}' failed: {e}")
                    issues.append(ValidationIssue(
                        rule=rule_name,
                        severity="warning",
                        message=f"Rule check failed: {e}",
                    ))

        return issues

    def _check_overlapping_clips(self) -> list[ValidationIssue]:
        """Check for overlapping clips on the same layer."""
        issues: list[ValidationIssue] = []

        for layer in self.timeline.get("layers", []):
            layer_type = layer.get("type", "content")
            clips = layer.get("clips", [])

            # Sort by start time
            sorted_clips = sorted(clips, key=lambda c: c.get("start_ms", 0))

            for i in range(len(sorted_clips) - 1):
                clip_a = sorted_clips[i]
                clip_b = sorted_clips[i + 1]

                end_a = clip_a.get("start_ms", 0) + clip_a.get("duration_ms", 0)
                start_b = clip_b.get("start_ms", 0)

                if end_a > start_b:
                    overlap_ms = end_a - start_b
                    issues.append(ValidationIssue(
                        rule="overlapping_clips",
                        severity="warning",
                        message=f"Clips overlap by {overlap_ms}ms on {layer_type} layer",
                        time_ms=start_b,
                        clip_id=clip_b.get("id"),
                        layer=layer_type,
                        suggestion=f"Move clip to {end_a}ms or trim previous clip",
                    ))

        return issues

    def _check_clip_bounds(self) -> list[ValidationIssue]:
        """Check for clips extending beyond timeline duration."""
        issues: list[ValidationIssue] = []

        if self.duration_ms <= 0:
            return issues

        for layer in self.timeline.get("layers", []):
            layer_type = layer.get("type", "content")
            for clip in layer.get("clips", []):
                start = clip.get("start_ms", 0)
                duration = clip.get("duration_ms", 0)
                end = start + duration

                if end > self.duration_ms:
                    issues.append(ValidationIssue(
                        rule="clip_bounds",
                        severity="warning",
                        message=f"Clip extends {end - self.duration_ms}ms beyond timeline end",
                        time_ms=start,
                        clip_id=clip.get("id"),
                        layer=layer_type,
                        suggestion=f"Trim clip duration to {self.duration_ms - start}ms",
                    ))

                if start < 0:
                    issues.append(ValidationIssue(
                        rule="clip_bounds",
                        severity="error",
                        message="Clip starts before timeline (negative start_ms)",
                        time_ms=start,
                        clip_id=clip.get("id"),
                        layer=layer_type,
                        suggestion="Move clip to start_ms=0",
                    ))

        # Check audio tracks too
        for track in self.timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                start = clip.get("start_ms", 0)
                duration = clip.get("duration_ms", 0)
                end = start + duration

                if end > self.duration_ms:
                    issues.append(ValidationIssue(
                        rule="clip_bounds",
                        severity="warning",
                        message=f"Audio clip extends {end - self.duration_ms}ms beyond timeline",
                        time_ms=start,
                        clip_id=clip.get("id"),
                        layer=track.get("type", "se"),
                        suggestion=f"Trim audio duration to {self.duration_ms - start}ms",
                    ))

        return issues

    def _check_missing_assets(self) -> list[ValidationIssue]:
        """Check for clips referencing missing assets."""
        issues: list[ValidationIssue] = []

        if not self.known_asset_ids:
            return issues  # Can't check without known assets

        for layer in self.timeline.get("layers", []):
            for clip in layer.get("clips", []):
                asset_id = clip.get("asset_id")
                if asset_id and str(asset_id) not in self.known_asset_ids:
                    issues.append(ValidationIssue(
                        rule="missing_assets",
                        severity="error",
                        message=f"Clip references missing asset: {str(asset_id)[:8]}...",
                        time_ms=clip.get("start_ms", 0),
                        clip_id=clip.get("id"),
                        layer=layer.get("type"),
                        suggestion="Upload the missing asset or remove the clip",
                    ))

        for track in self.timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                asset_id = clip.get("asset_id")
                if asset_id and str(asset_id) not in self.known_asset_ids:
                    issues.append(ValidationIssue(
                        rule="missing_assets",
                        severity="error",
                        message=f"Audio clip references missing asset: {str(asset_id)[:8]}...",
                        time_ms=clip.get("start_ms", 0),
                        clip_id=clip.get("id"),
                        layer=track.get("type"),
                    ))

        return issues

    def _check_safe_zone(self) -> list[ValidationIssue]:
        """Check if text/avatar clips are within safe zones."""
        issues: list[ValidationIssue] = []

        margin_x = self.width * self.SAFE_ZONE_MARGIN
        margin_y = self.height * self.SAFE_ZONE_MARGIN
        half_w = self.width / 2
        half_h = self.height / 2

        for layer in self.timeline.get("layers", []):
            layer_type = layer.get("type", "content")

            # Only check text and avatar layers
            if layer_type not in ("text", "avatar"):
                continue

            for clip in layer.get("clips", []):
                transform = clip.get("transform", {})
                x = transform.get("x", 0)
                y = transform.get("y", 0)
                clip_w = transform.get("width", 200) or 200
                clip_h = transform.get("height", 100) or 100
                scale = transform.get("scale", 1.0)

                # Calculate actual bounds (center + offset)
                actual_w = clip_w * scale / 2
                actual_h = clip_h * scale / 2

                # Check if any edge is outside safe zone
                left = half_w + x - actual_w
                right = half_w + x + actual_w
                top = half_h + y - actual_h
                bottom = half_h + y + actual_h

                out_of_bounds = False
                direction = []

                if left < margin_x:
                    out_of_bounds = True
                    direction.append("left")
                if right > self.width - margin_x:
                    out_of_bounds = True
                    direction.append("right")
                if top < margin_y:
                    out_of_bounds = True
                    direction.append("top")
                if bottom > self.height - margin_y:
                    out_of_bounds = True
                    direction.append("bottom")

                if out_of_bounds:
                    issues.append(ValidationIssue(
                        rule="safe_zone",
                        severity="warning",
                        message=f"{layer_type.title()} extends outside safe zone ({', '.join(direction)})",
                        time_ms=clip.get("start_ms", 0),
                        clip_id=clip.get("id"),
                        layer=layer_type,
                        suggestion="Adjust position or scale to stay within 5% margin",
                    ))

        return issues

    def _check_empty_layers(self) -> list[ValidationIssue]:
        """Check for visible layers with no clips."""
        issues: list[ValidationIssue] = []

        for layer in self.timeline.get("layers", []):
            if layer.get("visible", True) and not layer.get("clips"):
                issues.append(ValidationIssue(
                    rule="empty_layers",
                    severity="info",
                    message=f"Layer '{layer.get('name', 'unknown')}' is visible but has no clips",
                    layer=layer.get("type"),
                ))

        return issues

    def _check_audio_sync(self) -> list[ValidationIssue]:
        """Check for audio-visual sync issues."""
        issues: list[ValidationIssue] = []

        # Get narration boundaries
        narration_clips: list[tuple[int, int]] = []
        for track in self.timeline.get("audio_tracks", []):
            if track.get("type") != "narration" or track.get("muted"):
                continue
            for clip in track.get("clips", []):
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                if dur > 0:
                    narration_clips.append((start, start + dur))

        if not narration_clips:
            return issues

        # Check if there are visual clips during narration periods
        visual_clips: list[tuple[int, int]] = []
        for layer in self.timeline.get("layers", []):
            if not layer.get("visible", True):
                continue
            for clip in layer.get("clips", []):
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                if dur > 0:
                    visual_clips.append((start, start + dur))

        # Check each narration period
        for narr_start, narr_end in narration_clips:
            has_visual = any(
                v_start < narr_end and v_end > narr_start
                for v_start, v_end in visual_clips
            )

            if not has_visual:
                issues.append(ValidationIssue(
                    rule="audio_sync",
                    severity="warning",
                    message=f"Narration at {narr_start}ms has no visual content",
                    time_ms=narr_start,
                    suggestion="Add visual content (slide/avatar) during narration",
                ))

        return issues

    def _check_duration_consistency(self) -> list[ValidationIssue]:
        """Check that timeline duration matches actual content."""
        issues: list[ValidationIssue] = []

        # Calculate actual content end
        max_end = 0
        for layer in self.timeline.get("layers", []):
            for clip in layer.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)

        for track in self.timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_end = max(max_end, end)

        if self.duration_ms > 0 and max_end > 0:
            diff = abs(self.duration_ms - max_end)
            if diff > 1000:  # More than 1 second difference
                issues.append(ValidationIssue(
                    rule="duration_consistency",
                    severity="warning",
                    message=(
                        f"Timeline duration ({self.duration_ms}ms) differs from "
                        f"content end ({max_end}ms) by {diff}ms"
                    ),
                    suggestion=f"Update timeline duration to {max_end}ms",
                ))

        return issues

    def _check_text_readability(self) -> list[ValidationIssue]:
        """Check text clips for readability issues."""
        issues: list[ValidationIssue] = []

        for layer in self.timeline.get("layers", []):
            if layer.get("type") != "text":
                continue

            for clip in layer.get("clips", []):
                if clip.get("text_content") is None:
                    continue

                text = clip["text_content"]
                duration_ms = clip.get("duration_ms", 0)
                text_style = clip.get("text_style", {})
                font_size = text_style.get("fontSize", 48)

                # Check minimum display time (roughly 200ms per word)
                word_count = len(text.split())
                min_display_ms = word_count * 200 + 500  # 200ms/word + 500ms base

                if duration_ms < min_display_ms and word_count > 3:
                    issues.append(ValidationIssue(
                        rule="text_readability",
                        severity="warning",
                        message=f"Text '{text[:30]}...' shown for {duration_ms}ms but needs ~{min_display_ms}ms to read",
                        time_ms=clip.get("start_ms", 0),
                        clip_id=clip.get("id"),
                        layer="text",
                        suggestion=f"Extend duration to at least {min_display_ms}ms",
                    ))

                # Check font size
                if font_size < 24:
                    issues.append(ValidationIssue(
                        rule="text_readability",
                        severity="warning",
                        message=f"Font size {font_size}px may be too small for video",
                        time_ms=clip.get("start_ms", 0),
                        clip_id=clip.get("id"),
                        layer="text",
                        suggestion="Use at least 24px font size for 1080p video",
                    ))

        return issues

    def _check_layer_ordering(self) -> list[ValidationIssue]:
        """Check that layer order values match array positions.

        Convention: layers[0] is topmost (highest order), layers[N-1] is bottommost (order 0).
        Expected order = len(layers) - 1 - array_index.
        """
        issues: list[ValidationIssue] = []

        layers = self.timeline.get("layers", [])
        num_layers = len(layers)

        for i, layer in enumerate(layers):
            expected = num_layers - 1 - i
            actual = layer.get("order", -1)

            if actual != expected:
                issues.append(ValidationIssue(
                    rule="layer_ordering",
                    severity="info",
                    message=f"Layer '{layer.get('name', 'unknown')}' has order {actual}, expected {expected} (array index {i})",
                    layer=layer.get("type", "content"),
                    suggestion=f"Set layer order to {expected}",
                ))

        return issues

    def _check_visual_gaps(self) -> list[ValidationIssue]:
        """Check for periods with no visual content (blank screen)."""
        issues: list[ValidationIssue] = []

        if self.duration_ms <= 0:
            return issues

        # Collect all visual clip intervals
        intervals: list[tuple[int, int]] = []
        for layer in self.timeline.get("layers", []):
            if not layer.get("visible", True):
                continue
            for clip in layer.get("clips", []):
                start = clip.get("start_ms", 0)
                dur = clip.get("duration_ms", 0)
                if dur > 0:
                    intervals.append((start, start + dur))

        if not intervals:
            issues.append(ValidationIssue(
                rule="gap_detection",
                severity="error",
                message="No visual content in timeline",
                suggestion="Add clips to at least one visual layer",
            ))
            return issues

        # Merge overlapping intervals
        intervals.sort()
        merged: list[tuple[int, int]] = [intervals[0]]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Check for gaps
        min_gap_ms = 500  # Report gaps > 500ms

        # Gap at start
        if merged[0][0] > min_gap_ms:
            issues.append(ValidationIssue(
                rule="gap_detection",
                severity="warning",
                message=f"No visual content for first {merged[0][0]}ms (blank screen)",
                time_ms=0,
                suggestion="Add background or intro content at start",
            ))

        # Gaps between clips
        for i in range(len(merged) - 1):
            gap_start = merged[i][1]
            gap_end = merged[i + 1][0]
            gap_duration = gap_end - gap_start

            if gap_duration >= min_gap_ms:
                issues.append(ValidationIssue(
                    rule="gap_detection",
                    severity="warning",
                    message=f"Visual gap: {gap_duration}ms blank screen at {gap_start}ms",
                    time_ms=gap_start,
                    suggestion="Fill gap with transition, background, or extend adjacent clips",
                ))

        return issues
