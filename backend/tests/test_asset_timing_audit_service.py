from types import SimpleNamespace
from uuid import uuid4

from src.services.asset_timing_audit_service import (
    MISSING_WAVEFORM_RISK_CODE,
    build_asset_timing_audit_entry,
    build_asset_timing_audit_summary,
)


def _make_asset(**overrides):
    values = {
        "id": uuid4(),
        "name": "narration.mp3",
        "type": "audio",
        "subtype": "narration",
        "duration_ms": 6000,
        "sample_rate": 48000,
        "channels": 1,
        "source_asset_id": None,
        "is_internal": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_asset_timing_audit_entry_detects_waveform_and_probe_drift() -> None:
    asset = _make_asset()

    entry = build_asset_timing_audit_entry(
        asset,
        waveform={"duration_ms": 6123, "sample_rate": 44100},
        storage_probe={"duration_ms": 6124, "sample_rate": 48000, "channels": 1},
    )

    assert {drift["field"] for drift in entry["drifts"]} == {"duration_ms", "sample_rate"}
    assert entry["fallback_risks"] == []


def test_build_asset_timing_audit_entry_flags_missing_waveform_and_audio_facts() -> None:
    asset = _make_asset(duration_ms=None, sample_rate=None, channels=None)

    entry = build_asset_timing_audit_entry(asset)

    assert {risk["code"] for risk in entry["fallback_risks"]} == {
        "missing_asset_duration",
        "missing_asset_sample_rate",
        "missing_asset_channels",
        MISSING_WAVEFORM_RISK_CODE,
    }
    assert entry["drifts"] == []


def test_build_asset_timing_audit_summary_counts_drifts_and_missing_waveforms() -> None:
    entries = [
        build_asset_timing_audit_entry(
            _make_asset(),
            waveform={"duration_ms": 6123, "sample_rate": 44100},
        ),
        build_asset_timing_audit_entry(_make_asset(name="raw.mp3", duration_ms=None)),
    ]

    summary = build_asset_timing_audit_summary(entries)

    assert summary == {
        "total_assets": 2,
        "assets_with_drifts": 1,
        "assets_with_fallback_risks": 1,
        "assets_missing_waveform": 1,
    }
