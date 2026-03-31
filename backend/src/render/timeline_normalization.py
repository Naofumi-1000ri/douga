"""Helpers for normalizing timeline export ranges across render entry points."""

from __future__ import annotations

import copy
from typing import Any


def normalize_export_timeline(
    timeline_data: dict[str, Any],
    full_duration_ms: int,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Return a normalized timeline with a validated export range."""
    if full_duration_ms <= 0:
        raise ValueError("Timeline has no duration")

    export_start_ms = 0 if start_ms is None else max(0, start_ms)
    export_end_ms = full_duration_ms if end_ms is None else min(end_ms, full_duration_ms)

    if export_start_ms >= export_end_ms:
        raise ValueError("Invalid export range: start must be less than end")

    normalized_timeline = copy.deepcopy(timeline_data)
    render_duration_ms = export_end_ms - export_start_ms
    normalized_timeline["duration_ms"] = render_duration_ms
    normalized_timeline["export_start_ms"] = export_start_ms
    normalized_timeline["export_end_ms"] = export_end_ms

    return normalized_timeline, render_duration_ms


def normalize_embedded_export_timeline(
    timeline_data: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Normalize a timeline that may already include export range fields."""
    export_start_ms = int(timeline_data.get("export_start_ms", 0) or 0)
    export_end_ms = timeline_data.get("export_end_ms")
    duration_ms = int(timeline_data.get("duration_ms", 0) or 0)

    full_duration_ms = duration_ms + export_start_ms
    if export_end_ms is not None:
        full_duration_ms = max(full_duration_ms, int(export_end_ms))

    return normalize_export_timeline(
        timeline_data,
        full_duration_ms,
        start_ms=export_start_ms,
        end_ms=int(export_end_ms) if export_end_ms is not None else None,
    )
