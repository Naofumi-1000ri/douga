"""Contract tests for effects_spec.yaml <-> generated schemas consistency.

These tests verify that the generated Pydantic schemas, capabilities data,
and spec YAML are all in sync. They do NOT require a running server or database.

Run with:
    pytest backend/tests/contract/test_effects_contract.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Ensure the backend source is importable
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from src.schemas.effects_generated import (
    EFFECTS_CAPABILITIES,
    ChromaKeyEffect,
    Effects,
    GeneratedEffectsDetails,
    GeneratedUpdateClipEffectsRequest,
)

# Also import the generate script to load the spec directly
sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
from generate_effects import load_spec, generate_capabilities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_spec() -> dict:
    """Load the effects_spec.yaml from the canonical location."""
    spec_path = BACKEND_ROOT / "specs" / "effects_spec.yaml"
    assert spec_path.exists(), f"effects_spec.yaml not found at {spec_path}"
    return load_spec(spec_path)


# ---------------------------------------------------------------------------
# Test: Spec <-> Generated Schema Consistency
# ---------------------------------------------------------------------------

class TestSpecSchemaConsistency:
    """Verify generated schemas match effects_spec.yaml."""

    def test_spec_file_exists(self) -> None:
        """effects_spec.yaml must exist."""
        spec_path = BACKEND_ROOT / "specs" / "effects_spec.yaml"
        assert spec_path.exists()

    def test_generated_file_exists(self) -> None:
        """effects_generated.py must exist."""
        gen_path = BACKEND_ROOT / "src" / "schemas" / "effects_generated.py"
        assert gen_path.exists()

    def test_spec_has_version(self) -> None:
        """Spec must declare a version."""
        spec = _load_spec()
        assert "version" in spec
        assert spec["version"] == "1.0"

    def test_spec_effects_match_generated_effects_model(self) -> None:
        """All effects in spec must appear in the generated Effects model."""
        spec = _load_spec()
        spec_effects = set(spec.get("effects", {}).keys())
        model_fields = set(Effects.model_fields.keys())

        # Every spec effect should be a field in Effects
        for effect_name in spec_effects:
            assert effect_name in model_fields, (
                f"Effect '{effect_name}' is in spec but not in Effects model"
            )

    def test_chroma_key_params_match_spec(self) -> None:
        """ChromaKeyEffect fields must match spec params."""
        spec = _load_spec()
        ck_spec = spec["effects"]["chroma_key"]["params"]
        ck_fields = ChromaKeyEffect.model_fields

        for param_name in ck_spec:
            assert param_name in ck_fields, (
                f"Param '{param_name}' in spec but not in ChromaKeyEffect"
            )

    def test_chroma_key_defaults_match_spec(self) -> None:
        """ChromaKeyEffect default values must match spec."""
        spec = _load_spec()
        ck_spec = spec["effects"]["chroma_key"]["params"]

        ck = ChromaKeyEffect()
        assert ck.enabled == ck_spec["enabled"]["default"]
        assert ck.color == ck_spec["color"]["default"]
        assert ck.similarity == ck_spec["similarity"]["default"]
        assert ck.blend == ck_spec["blend"]["default"]

    def test_effects_details_defaults_match_spec(self) -> None:
        """GeneratedEffectsDetails defaults must match spec."""
        spec = _load_spec()
        details = GeneratedEffectsDetails()

        # opacity
        assert details.opacity == spec["effects"]["opacity"]["params"]["value"]["default"]
        # blend_mode
        assert details.blend_mode == spec["effects"]["blend_mode"]["params"]["value"]["default"]
        # fade_in_ms
        assert details.fade_in_ms == spec["effects"]["fade_in_ms"]["params"]["value"]["default"]
        # fade_out_ms
        assert details.fade_out_ms == spec["effects"]["fade_out_ms"]["params"]["value"]["default"]
        # chroma_key
        assert details.chroma_key_similarity == spec["effects"]["chroma_key"]["params"]["similarity"]["default"]
        assert details.chroma_key_blend == spec["effects"]["chroma_key"]["params"]["blend"]["default"]


# ---------------------------------------------------------------------------
# Test: Parameter Range Validation
# ---------------------------------------------------------------------------

class TestParameterValidation:
    """Verify parameter range enforcement from spec."""

    def test_chroma_key_similarity_rejects_above_max(self) -> None:
        """similarity > 1.0 must be rejected."""
        with pytest.raises(ValidationError):
            ChromaKeyEffect(similarity=1.5)

    def test_chroma_key_similarity_rejects_below_min(self) -> None:
        """similarity < 0.0 must be rejected."""
        with pytest.raises(ValidationError):
            ChromaKeyEffect(similarity=-0.1)

    def test_chroma_key_blend_rejects_above_max(self) -> None:
        """blend > 1.0 must be rejected."""
        with pytest.raises(ValidationError):
            ChromaKeyEffect(blend=1.5)

    def test_chroma_key_color_rejects_invalid_hex(self) -> None:
        """Invalid hex color must be rejected."""
        with pytest.raises(ValidationError):
            ChromaKeyEffect(color="not-a-color")

    def test_chroma_key_color_accepts_valid_hex(self) -> None:
        """Valid hex color must be accepted."""
        ck = ChromaKeyEffect(color="#FF00FF")
        assert ck.color == "#FF00FF"

    def test_opacity_rejects_above_max(self) -> None:
        """opacity > 1.0 in Effects must be rejected."""
        with pytest.raises(ValidationError):
            Effects(opacity=1.5)

    def test_opacity_rejects_below_min(self) -> None:
        """opacity < 0.0 in Effects must be rejected."""
        with pytest.raises(ValidationError):
            Effects(opacity=-0.1)

    def test_fade_in_ms_rejects_above_max(self) -> None:
        """fade_in_ms > 10000 must be rejected."""
        with pytest.raises(ValidationError):
            Effects(fade_in_ms=20000)

    def test_fade_out_ms_rejects_negative(self) -> None:
        """fade_out_ms < 0 must be rejected."""
        with pytest.raises(ValidationError):
            Effects(fade_out_ms=-1)

    def test_update_request_similarity_range(self) -> None:
        """Update request must enforce similarity range from spec."""
        with pytest.raises(ValidationError):
            GeneratedUpdateClipEffectsRequest(chroma_key_similarity=2.0)

    def test_update_request_color_pattern(self) -> None:
        """Update request must enforce hex color pattern from spec."""
        with pytest.raises(ValidationError):
            GeneratedUpdateClipEffectsRequest(chroma_key_color="red")


# ---------------------------------------------------------------------------
# Test: Capabilities Endpoint Consistency
# ---------------------------------------------------------------------------

class TestCapabilitiesConsistency:
    """Verify EFFECTS_CAPABILITIES matches effects_spec.yaml."""

    def test_capabilities_has_supported_effects(self) -> None:
        """Capabilities dict must have supported_effects."""
        assert "supported_effects" in EFFECTS_CAPABILITIES
        assert isinstance(EFFECTS_CAPABILITIES["supported_effects"], list)
        assert len(EFFECTS_CAPABILITIES["supported_effects"]) > 0

    def test_capabilities_matches_spec(self) -> None:
        """EFFECTS_CAPABILITIES must match what generate_capabilities produces from spec."""
        spec = _load_spec()
        expected = generate_capabilities(spec)
        assert EFFECTS_CAPABILITIES == expected

    def test_all_spec_effects_in_capabilities(self) -> None:
        """All effects defined in spec must appear in capabilities."""
        spec = _load_spec()
        spec_effects = set(spec.get("effects", {}).keys())
        cap_effects = set(EFFECTS_CAPABILITIES["supported_effects"])
        assert spec_effects == cap_effects

    def test_chroma_key_params_in_capabilities(self) -> None:
        """chroma_key params must be listed in capabilities."""
        ck_params = EFFECTS_CAPABILITIES["effect_params"]["chroma_key"]
        assert "color" in ck_params
        assert "similarity" in ck_params
        assert "blend" in ck_params

    def test_chroma_key_similarity_range_in_capabilities(self) -> None:
        """chroma_key similarity range in capabilities must match spec."""
        spec = _load_spec()
        spec_sim = spec["effects"]["chroma_key"]["params"]["similarity"]
        cap_sim = EFFECTS_CAPABILITIES["effect_params"]["chroma_key"]["similarity"]
        assert cap_sim["min"] == spec_sim["minimum"]
        assert cap_sim["max"] == spec_sim["maximum"]
        assert cap_sim["default"] == spec_sim["default"]


# ---------------------------------------------------------------------------
# Test: Unknown Effects Warning (Design Principle)
# ---------------------------------------------------------------------------

class TestUnknownEffectHandling:
    """Verify that unknown effects are handled gracefully."""

    def test_effects_model_ignores_unknown_fields(self) -> None:
        """Effects model should ignore unknown fields (no error) by default.

        Pydantic v2 ignores extra fields by default.
        This test verifies that unknown effects do not cause a hard error.
        """
        # Pydantic v2 default: extra fields are ignored
        data = {"opacity": 0.8, "unknown_effect": {"enabled": True}}
        effects = Effects.model_validate(data)
        assert effects.opacity == 0.8
        # unknown_effect should be silently dropped (not raise)

    def test_update_request_ignores_unknown_fields(self) -> None:
        """UpdateClipEffectsRequest ignores unknown fields gracefully."""
        data = {"opacity": 0.5, "future_effect_strength": 0.9}
        req = GeneratedUpdateClipEffectsRequest.model_validate(data)
        assert req.opacity == 0.5


# ---------------------------------------------------------------------------
# Test: Backward Compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Verify that old import paths still work."""

    def test_timeline_chromakeyeffect_import(self) -> None:
        """ChromaKeyEffect can be imported from timeline module."""
        from src.schemas.timeline import ChromaKeyEffect as CK
        assert CK is ChromaKeyEffect

    def test_timeline_effects_import(self) -> None:
        """Effects can be imported from timeline module."""
        from src.schemas.timeline import Effects as E
        assert E is Effects

    def test_ai_effectsdetails_import(self) -> None:
        """EffectsDetails can be imported from ai module."""
        from src.schemas.ai import EffectsDetails
        assert EffectsDetails is GeneratedEffectsDetails

    def test_ai_updateclipeffectsrequest_import(self) -> None:
        """UpdateClipEffectsRequest can be imported from ai module."""
        from src.schemas.ai import UpdateClipEffectsRequest
        assert UpdateClipEffectsRequest is GeneratedUpdateClipEffectsRequest
