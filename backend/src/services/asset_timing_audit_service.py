"""Helpers for read-only asset timing observability."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from src.models.asset import Asset

MEDIA_TYPES = {"audio", "video"}
MISSING_WAVEFORM_RISK_CODE = "missing_waveform_artifact"


def _build_source(
    source: str,
    *,
    duration_ms: int | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "channels": channels,
    }


def build_asset_timing_sources(
    asset: Asset,
    *,
    waveform: dict[str, Any] | None = None,
    storage_probe: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build comparable timing fact snapshots for a single asset."""
    sources = [
        _build_source(
            "asset_record",
            duration_ms=asset.duration_ms,
            sample_rate=asset.sample_rate,
            channels=asset.channels,
        )
    ]

    if waveform is not None:
        sources.append(
            _build_source(
                "waveform_artifact",
                duration_ms=waveform.get("duration_ms"),
                sample_rate=waveform.get("sample_rate"),
                channels=waveform.get("channels"),
            )
        )

    if storage_probe is not None:
        sources.append(
            _build_source(
                "storage_probe",
                duration_ms=storage_probe.get("duration_ms"),
                sample_rate=storage_probe.get("sample_rate"),
                channels=storage_probe.get("channels"),
            )
        )

    return sources


def detect_timing_drifts(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return pairwise timing fact mismatches for the provided sources."""
    drifts: list[dict[str, Any]] = []

    for left, right in combinations(sources, 2):
        for field in ("duration_ms", "sample_rate", "channels"):
            left_value = left.get(field)
            right_value = right.get(field)
            if left_value is None or right_value is None or left_value == right_value:
                continue

            drifts.append(
                {
                    "field": field,
                    "source_a": left["source"],
                    "source_b": right["source"],
                    "value_a": left_value,
                    "value_b": right_value,
                    "delta": abs(right_value - left_value),
                }
            )

    return sorted(drifts, key=lambda item: (item["field"], item["source_a"], item["source_b"]))


def build_asset_timing_fallback_risks(
    asset: Asset,
    *,
    waveform: dict[str, Any] | None = None,
    storage_probe_error: str | None = None,
) -> list[dict[str, str]]:
    """Describe currently visible timing fact gaps that still rely on fallbacks."""
    if asset.type not in MEDIA_TYPES:
        return []

    risks: list[dict[str, str]] = []

    if asset.duration_ms is None:
        risks.append(
            {
                "code": "missing_asset_duration",
                "message": "asset.duration_ms is missing; editor and export paths may fall back to derived or default durations.",
            }
        )

    if asset.type == "audio" and asset.sample_rate is None:
        risks.append(
            {
                "code": "missing_asset_sample_rate",
                "message": "asset.sample_rate is missing; audio-specific paths may fall back to implicit defaults.",
            }
        )

    if asset.type == "audio" and asset.channels is None:
        risks.append(
            {
                "code": "missing_asset_channels",
                "message": "asset.channels is missing; audio-specific paths may fall back to implicit defaults.",
            }
        )

    if waveform is None:
        risks.append(
            {
                "code": MISSING_WAVEFORM_RISK_CODE,
                "message": "Waveform artifact is missing; preview falls back to on-demand waveform generation.",
            }
        )

    if storage_probe_error:
        risks.append(
            {
                "code": "storage_probe_failed",
                "message": f"Storage probe failed during audit: {storage_probe_error}",
            }
        )

    return risks


def build_asset_timing_audit_entry(
    asset: Asset,
    *,
    waveform: dict[str, Any] | None = None,
    storage_probe: dict[str, Any] | None = None,
    storage_probe_error: str | None = None,
) -> dict[str, Any]:
    """Build a read-only audit payload for one asset."""
    sources = build_asset_timing_sources(asset, waveform=waveform, storage_probe=storage_probe)
    drifts = detect_timing_drifts(sources)
    fallback_risks = build_asset_timing_fallback_risks(
        asset,
        waveform=waveform,
        storage_probe_error=storage_probe_error,
    )

    return {
        "asset_id": asset.id,
        "asset_name": asset.name,
        "asset_type": asset.type,
        "asset_subtype": asset.subtype,
        "source_asset_id": asset.source_asset_id,
        "sources": sources,
        "drifts": drifts,
        "fallback_risks": fallback_risks,
        "storage_probe_error": storage_probe_error,
    }


def build_asset_timing_audit_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Build summary counters for a timing audit result set."""
    return {
        "total_assets": len(entries),
        "assets_with_drifts": sum(1 for entry in entries if entry["drifts"]),
        "assets_with_fallback_risks": sum(1 for entry in entries if entry["fallback_risks"]),
        "assets_missing_waveform": sum(
            1
            for entry in entries
            if any(risk["code"] == MISSING_WAVEFORM_RISK_CODE for risk in entry["fallback_risks"])
        ),
    }
