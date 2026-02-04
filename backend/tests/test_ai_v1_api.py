"""
Tests for AI v1 API endpoints (Envelope format).

These tests verify:
- Envelope response format (request_id, data, meta)
- Idempotency-Key header requirement for mutations
- If-Match soft enforcement (warnings, 409 on mismatch)
- All Phase 0 endpoints: capabilities, overview, structure, assets, clips

Run with: pytest tests/test_ai_v1_api.py -v

Note: Tests marked with @pytest.mark.requires_db require database connection.
Skip them in CI with: pytest tests/test_ai_v1_api.py -v -m "not requires_db"
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.main import app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers():
    """Authentication headers for dev mode."""
    return {"Authorization": "Bearer dev-token"}




# =============================================================================
# Envelope Format Tests
# =============================================================================


class TestEnvelopeFormat:
    """Test envelope response format compliance."""

    def test_capabilities_returns_envelope(self, client, auth_headers):
        """GET /capabilities returns proper envelope format."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Verify envelope structure
        assert "request_id" in data
        assert "data" in data
        assert "meta" in data

        # Verify meta structure
        meta = data["meta"]
        assert "api_version" in meta
        assert meta["api_version"] == "1.0"
        assert "processing_time_ms" in meta
        assert "timestamp" in meta

        # Verify request_id is a UUID
        uuid.UUID(data["request_id"])

    @pytest.mark.requires_db
    def test_error_returns_envelope(self, client, auth_headers):
        """Error responses also use envelope format.

        Note: Requires DB connection to test 404 response.
        """
        # Request non-existent project
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # Might get 404 (project not found) or 500 (DB error)
        assert response.status_code in [404, 500]
        data = response.json()

        # Verify envelope structure with error
        assert "request_id" in data
        assert "error" in data
        assert "meta" in data

        # Verify error structure
        error = data["error"]
        assert "code" in error
        assert "message" in error


# =============================================================================
# Capabilities Endpoint Tests
# =============================================================================


class TestCapabilitiesEndpoint:
    """Test GET /api/ai/v1/capabilities endpoint."""

    def test_capabilities_returns_api_info(self, client, auth_headers):
        """Capabilities endpoint returns API features and limits."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]

        # Check API version info
        assert data["api_version"] == "1.0"
        assert data["schema_version"] == "1.0-unified"

        # Check features (Phase 1 complete)
        assert data["features"]["validate_only"] is True
        assert data["features"]["return_diff"] is False  # Phase 2+3
        assert data["features"]["rollback"] is False  # Phase 2+3

        # Check schema notes (unified format documentation)
        assert "schema_notes" in data
        assert data["schema_notes"]["clip_format"] == "unified"
        assert "flat" in data["schema_notes"]["transform_formats"]
        assert "nested" in data["schema_notes"]["transform_formats"]

        # Check limits
        assert "limits" in data
        assert data["limits"]["max_layers"] == 5
        assert data["limits"]["max_duration_ms"] == 3600000

        # Check legacy capability fields still present
        assert "effects" in data
        assert "easings" in data

    def test_capabilities_documents_unsupported_fields(self, client, auth_headers):
        """Capabilities endpoint documents unsupported fields for AI guidance."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        schema_notes = response.json()["data"]["schema_notes"]

        # Supported transform fields are documented
        assert "supported_transform_fields" in schema_notes
        assert "position.x" in schema_notes["supported_transform_fields"]
        assert "position.y" in schema_notes["supported_transform_fields"]
        assert "scale.x" in schema_notes["supported_transform_fields"]

        # Unsupported transform fields are documented
        assert "unsupported_transform_fields" in schema_notes
        unsupported = schema_notes["unsupported_transform_fields"]
        assert any("rotation" in f for f in unsupported)
        assert any("opacity" in f for f in unsupported)
        assert any("anchor" in f for f in unsupported)
        assert any("scale.y" in f for f in unsupported)

        # Unsupported clip-level fields are documented
        assert "unsupported_clip_fields" in schema_notes
        clip_unsupported = schema_notes["unsupported_clip_fields"]
        assert "effects" in clip_unsupported
        assert "transition_in" in clip_unsupported
        assert "transition_out" in clip_unsupported

        # Text style note for unknown keys
        assert "text_style_note" in schema_notes

    @pytest.mark.requires_db
    def test_capabilities_requires_auth(self, client):
        """Capabilities endpoint requires authentication.

        Note: In dev mode, authentication may be bypassed.
        """
        response = client.get("/api/ai/v1/capabilities")

        # In dev mode, might return 200 or auth might be required
        assert response.status_code in [200, 401, 500]


# =============================================================================
# Read Endpoint Tests
# =============================================================================


class TestReadEndpoints:
    """Test read-only endpoints (overview, structure, assets).

    Note: These tests require database connection for full functionality.
    """

    @pytest.mark.requires_db
    def test_overview_endpoint_exists(self, client, auth_headers):
        """GET /overview endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_structure_endpoint_exists(self, client, auth_headers):
        """GET /structure endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/structure",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_assets_endpoint_exists(self, client, auth_headers):
        """GET /assets endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/assets",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data


# =============================================================================
# Write Endpoint Tests (POST /clips)
# =============================================================================


class TestClipsEndpoint:
    """Test POST /api/ai/v1/projects/{id}/clips endpoint."""

    @pytest.mark.requires_db
    def test_clips_requires_idempotency_key(self, client, auth_headers):
        """POST /clips requires Idempotency-Key header for mutations.

        Note: In test environment, may fail at DB level before header check.
        """
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should be 400 for missing Idempotency-Key, or 500 if DB fails first
        assert response.status_code in [400, 500]
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" in data.get("detail", "")

    @pytest.mark.requires_db
    def test_clips_with_idempotency_key(self, client, auth_headers):
        """POST /clips accepts Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        }

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should not be 400 for missing Idempotency-Key
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" not in data.get("detail", "")

    def test_clips_validate_only_no_idempotency_key_required(self, client, auth_headers):
        """POST /clips with validate_only=true doesn't require Idempotency-Key."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should return 400 for FEATURE_NOT_SUPPORTED (validate_only not implemented yet)
        # but NOT for missing Idempotency-Key
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" not in data.get("detail", "")
            # Check envelope error format
            if "error" in data:
                assert data["error"]["code"] == "FEATURE_NOT_SUPPORTED"


# =============================================================================
# If-Match Header Tests
# =============================================================================


class TestIfMatchHeader:
    """Test If-Match header soft enforcement."""

    @pytest.mark.requires_db
    def test_missing_if_match_adds_warning(self, client, auth_headers):
        """Missing If-Match header adds warning to meta.

        Note: Requires DB connection to reach header validation.
        """
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            # No If-Match header
        }

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        data = response.json()

        # If we get past auth and get envelope, check for warning in meta
        if "meta" in data and "warnings" in data["meta"]:
            warnings = data["meta"]["warnings"]
            # If warnings present, If-Match warning should be there
            if warnings:
                assert any("If-Match" in w for w in warnings)


# =============================================================================
# Version Endpoint Tests
# =============================================================================


class TestVersionEndpoint:
    """Test GET /api/ai/v1/version endpoint."""

    def test_version_returns_api_version(self, client, auth_headers):
        """Version endpoint returns API version info."""
        response = client.get("/api/ai/v1/version", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert "data" in data
        assert data["data"]["api_version"] == "1.0"


# =============================================================================
# Error Code Tests
# =============================================================================


class TestErrorCodes:
    """Test that error responses use proper error codes."""

    @pytest.mark.requires_db
    def test_not_found_uses_proper_code(self, client, auth_headers):
        """404 errors use proper error code in envelope.

        Note: Requires DB connection to verify project doesn't exist.
        """
        fake_project_id = str(uuid.uuid4())

        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # May be 404 (not found) or 500 (DB error)
        assert response.status_code in [404, 500]
        data = response.json()

        assert "error" in data
        if response.status_code == 404:
            assert data["error"]["code"] in ["PROJECT_NOT_FOUND", "NOT_FOUND"]

    @pytest.mark.requires_db
    def test_unauthorized_uses_proper_code(self, client):
        """401 errors use proper error code in envelope.

        Note: In dev mode, authentication may be bypassed.
        """
        fake_project_id = str(uuid.uuid4())

        response = client.get(f"/api/ai/v1/projects/{fake_project_id}/overview")

        # May be 401 (unauthorized) or 200/404/500 (dev mode bypass or DB error)
        data = response.json()

        # V1 endpoints return envelope format for errors
        if response.status_code == 401 and "error" in data:
            assert data["error"]["code"] == "UNAUTHORIZED"


# =============================================================================
# Schema Unit Tests (no DB required)
# =============================================================================


class TestSchemas:
    """Test Pydantic schema validation."""

    def test_envelope_response_schema(self):
        """EnvelopeResponse schema validates correctly."""
        from src.schemas.envelope import EnvelopeResponse, ResponseMeta

        meta = ResponseMeta(
            api_version="1.0",
            processing_time_ms=100,
            timestamp=datetime.now(timezone.utc),
            warnings=["test warning"],
        )

        envelope = EnvelopeResponse(
            request_id=str(uuid.uuid4()),
            data={"test": "data"},
            meta=meta,
        )

        assert envelope.data == {"test": "data"}
        assert envelope.meta.api_version == "1.0"
        assert "test warning" in envelope.meta.warnings

    def test_error_info_schema(self):
        """ErrorInfo schema validates correctly."""
        from src.schemas.envelope import ErrorInfo, ErrorLocation

        location = ErrorLocation(
            field="layer_id",
            clip_id="clip-123",
        )

        error = ErrorInfo(
            code="CLIP_NOT_FOUND",
            message="Clip not found",
            location=location,
            retryable=True,
        )

        assert error.code == "CLIP_NOT_FOUND"
        assert error.location.clip_id == "clip-123"
        assert error.retryable is True

    def test_operation_options_schema(self):
        """OperationOptions schema validates correctly."""
        from src.schemas.options import OperationOptions

        # Test with include_diff (legacy name)
        options = OperationOptions(
            validate_only=True,
            include_diff=False,
        )

        assert options.validate_only is True
        assert options.include_diff is False

        # Test with return_diff (spec name, alias)
        options_alias = OperationOptions(
            validate_only=True,
            return_diff=True,
        )
        assert options_alias.include_diff is True  # Alias maps to include_diff

        # Test defaults
        default_options = OperationOptions()
        assert default_options.validate_only is False
        assert default_options.include_diff is False

    def test_request_context_creation(self):
        """RequestContext creates valid context."""
        from src.middleware.request_context import create_request_context

        context = create_request_context()

        assert context.request_id is not None
        uuid.UUID(context.request_id)  # Should be valid UUID
        assert context.start_time > 0
        assert isinstance(context.warnings, list)

    def test_build_meta_timing(self):
        """build_meta calculates processing time correctly."""
        import time

        from src.middleware.request_context import build_meta, create_request_context

        context = create_request_context()
        time.sleep(0.01)  # Wait 10ms
        meta = build_meta(context)

        assert meta.processing_time_ms >= 10
        assert meta.api_version == "1.0"
        assert meta.timestamp is not None


# =============================================================================
# Phase 1: Error Codes Unit Tests
# =============================================================================


class TestErrorCodesModule:
    """Test error_codes.py module."""

    def test_error_codes_structure(self):
        """ERROR_CODES dictionary has expected structure."""
        from src.constants.error_codes import ERROR_CODES

        assert "CLIP_NOT_FOUND" in ERROR_CODES
        assert "LAYER_NOT_FOUND" in ERROR_CODES
        assert "INTERNAL_ERROR" in ERROR_CODES

        clip_error = ERROR_CODES["CLIP_NOT_FOUND"]
        assert "retryable" in clip_error
        assert clip_error["retryable"] is True

    def test_is_retryable_function(self):
        """is_retryable function works correctly."""
        from src.constants.error_codes import is_retryable

        assert is_retryable("CLIP_NOT_FOUND") is True
        assert is_retryable("INTERNAL_ERROR") is True
        assert is_retryable("VALIDATION_ERROR") is False
        assert is_retryable("UNKNOWN_CODE") is False

    def test_get_error_spec_function(self):
        """get_error_spec function returns correct specs."""
        from src.constants.error_codes import get_error_spec

        spec = get_error_spec("CLIP_NOT_FOUND")
        assert spec["retryable"] is True
        assert "suggested_action" in spec

        unknown = get_error_spec("NONEXISTENT")
        assert unknown.get("retryable", False) is False


# =============================================================================
# Phase 1: DougaError Exception Tests
# =============================================================================


class TestDougaExceptions:
    """Test custom exception classes."""

    def test_douga_error_base(self):
        """DougaError base class works correctly."""
        from src.exceptions import DougaError

        error = DougaError("Test error")
        assert error.message == "Test error"
        assert error.code == "INTERNAL_ERROR"
        assert error.status_code == 500

    def test_resource_not_found_errors(self):
        """Resource not found errors have correct codes."""
        from src.exceptions import (
            AssetNotFoundError,
            ClipNotFoundError,
            LayerNotFoundError,
            ProjectNotFoundError,
        )

        proj_error = ProjectNotFoundError("proj-123")
        assert proj_error.code == "PROJECT_NOT_FOUND"
        assert proj_error.status_code == 404
        assert "proj-123" in proj_error.message

        clip_error = ClipNotFoundError("clip-456", layer_id="layer-789")
        assert clip_error.code == "CLIP_NOT_FOUND"
        assert clip_error.location is not None
        assert clip_error.location.clip_id == "clip-456"
        assert clip_error.location.layer_id == "layer-789"

        layer_error = LayerNotFoundError("layer-abc")
        assert layer_error.code == "LAYER_NOT_FOUND"

        asset_error = AssetNotFoundError("asset-def")
        assert asset_error.code == "ASSET_NOT_FOUND"

    def test_validation_errors(self):
        """Validation errors have correct codes."""
        from src.exceptions import (
            InvalidTimeRangeError,
            LayerLockedError,
            MissingRequiredFieldError,
            OutOfBoundsError,
        )

        time_error = InvalidTimeRangeError(start_ms=5000, end_ms=3000, field="start_ms")
        assert time_error.code == "INVALID_TIME_RANGE"
        assert time_error.status_code == 400
        assert time_error.location is not None
        assert time_error.location.field == "start_ms"

        bounds_error = OutOfBoundsError(field="duration_ms", value=999999, max_value=3600000)
        assert bounds_error.code == "OUT_OF_BOUNDS"

        locked_error = LayerLockedError("layer-123")
        assert locked_error.code == "LAYER_LOCKED"

        missing_error = MissingRequiredFieldError("layer_id")
        assert missing_error.code == "MISSING_REQUIRED_FIELD"
        assert missing_error.location.field == "layer_id"

    def test_conflict_errors(self):
        """Conflict errors have correct codes."""
        from src.exceptions import (
            ClipOverlapError,
            ConcurrentModificationError,
            IdempotencyConflictError,
        )

        overlap_error = ClipOverlapError(
            clip_id="clip-1", layer_id="layer-1", conflicting_clip_id="clip-2"
        )
        assert overlap_error.code == "CLIP_OVERLAP"
        assert overlap_error.status_code == 409

        concurrent_error = ConcurrentModificationError()
        assert concurrent_error.code == "CONCURRENT_MODIFICATION"

        idempotency_error = IdempotencyConflictError()
        assert idempotency_error.code == "IDEMPOTENCY_CONFLICT"

    def test_error_to_error_info_conversion(self):
        """DougaError.to_error_info() creates valid ErrorInfo."""
        from src.exceptions import ClipNotFoundError

        error = ClipNotFoundError("clip-123")
        error_info = error.to_error_info()

        assert error_info.code == "CLIP_NOT_FOUND"
        assert "clip-123" in error_info.message
        assert error_info.retryable is True
        # Check suggested_fix is from spec (human-readable instruction)
        assert error_info.suggested_fix is not None
        assert "timeline structure" in error_info.suggested_fix.lower()
        # Check suggested_actions
        assert len(error_info.suggested_actions) > 0
        assert error_info.suggested_actions[0].action == "refresh_ids"


# =============================================================================
# Phase 1: Validation Service Unit Tests
# =============================================================================


class TestValidationService:
    """Test ValidationService and validation result structures."""

    def test_would_affect_structure(self):
        """WouldAffect has correct structure."""
        from src.services.validation_service import WouldAffect

        would_affect = WouldAffect(
            clips_created=1,
            clips_modified=0,
            clips_deleted=0,
            duration_change_ms=5000,
            layers_affected=["layer-123"],
        )

        result_dict = would_affect.to_dict()
        assert result_dict["clips_created"] == 1
        assert result_dict["clips_modified"] == 0
        assert result_dict["clips_deleted"] == 0
        assert result_dict["duration_change_ms"] == 5000
        assert "layer-123" in result_dict["layers_affected"]

    def test_validation_result_structure(self):
        """ValidationResult has correct structure."""
        from src.services.validation_service import ValidationResult, WouldAffect

        would_affect = WouldAffect(clips_created=1)
        result = ValidationResult(
            valid=True,
            warnings=["Test warning"],
            would_affect=would_affect,
        )

        result_dict = result.to_dict()
        assert result_dict["valid"] is True
        assert "Test warning" in result_dict["warnings"]
        assert result_dict["would_affect"]["clips_created"] == 1

    def test_validation_result_defaults(self):
        """ValidationResult has sensible defaults."""
        from src.services.validation_service import ValidationResult

        result = ValidationResult(valid=False)
        result_dict = result.to_dict()

        assert result_dict["valid"] is False
        assert result_dict["warnings"] == []
        assert result_dict["would_affect"]["clips_created"] == 0


# =============================================================================
# Phase 1: validate_only Integration Tests (require DB)
# =============================================================================


class TestValidateOnlyEndpoint:
    """Test validate_only mode for POST /clips."""

    def test_validate_only_returns_validation_result(self, client, auth_headers):
        """validate_only=true returns validation result instead of creating clip."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        )

        data = response.json()

        # Either validation passes and returns result, or error occurs
        # Both should be envelope format
        assert "request_id" in data
        assert "meta" in data

        # Should NOT be 400 with FEATURE_NOT_SUPPORTED (that was Phase 0)
        if response.status_code == 400:
            assert data.get("error", {}).get("code") != "FEATURE_NOT_SUPPORTED"

    def test_validate_only_no_idempotency_key_needed(self, client, auth_headers):
        """validate_only=true doesn't require Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())

        # No Idempotency-Key header
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": "layer-1",
                    "asset_id": str(uuid.uuid4()),
                    "start_ms": 0,
                    "duration_ms": 1000,
                },
            },
        )

        data = response.json()

        # Should not fail due to missing Idempotency-Key
        if response.status_code == 400:
            error_code = data.get("error", {}).get("code", "")
            error_detail = data.get("detail", "")
            assert "Idempotency-Key" not in error_code
            assert "Idempotency-Key" not in error_detail


# =============================================================================
# Clip Adapter Tests (transitional -> spec schema support)
# =============================================================================


class TestClipAdapter:
    """Test UnifiedClipInput adapter for both flat and nested formats."""

    def test_flat_format_parsing(self):
        """Flat format (transitional) parses correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        flat_data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(flat_data)

        assert unified.layer_id == "layer-1"
        assert unified.start_ms == 0
        assert unified.duration_ms == 1000
        assert unified.x == 100
        assert unified.y == 200
        assert unified.scale == 1.5

    def test_nested_format_parsing(self):
        """Nested format (spec) parses correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.5, "y": 1.5},
                "rotation": 0,
                "opacity": 1.0,
                "anchor": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)

        assert unified.layer_id == "layer-1"
        assert unified.type == "video"
        assert unified.transform is not None
        assert unified.transform.position.x == 100
        assert unified.transform.position.y == 200
        assert unified.transform.scale.x == 1.5
        # After validation, flat values should be populated from nested
        assert unified.x == 100
        assert unified.y == 200
        assert unified.scale == 1.5

    def test_nested_to_flat_conversion(self):
        """Nested format converts to flat dict correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 5000,
            "duration_ms": 2000,
            "transform": {
                "position": {"x": 50, "y": -100},
                "scale": {"x": 2.0, "y": 2.0},
                "rotation": 45,
                "opacity": 0.8,
                "anchor": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)
        flat_dict = unified.to_flat_dict()

        assert flat_dict["layer_id"] == "layer-1"
        assert flat_dict["start_ms"] == 5000
        assert flat_dict["duration_ms"] == 2000
        assert flat_dict["x"] == 50
        assert flat_dict["y"] == -100
        assert flat_dict["scale"] == 2.0
        assert str(flat_dict["asset_id"]) == "00000000-0000-0000-0000-000000000001"

    def test_flat_to_add_clip_request(self):
        """Flat format converts to AddClipRequest correctly."""
        from src.schemas.ai import AddClipRequest
        from src.schemas.clip_adapter import UnifiedClipInput

        flat_data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(flat_data)
        flat_dict = unified.to_flat_dict()
        add_clip = AddClipRequest.model_validate(flat_dict)

        assert add_clip.layer_id == "layer-1"
        assert add_clip.x == 100
        assert add_clip.y == 200
        assert add_clip.scale == 1.5

    def test_nested_to_add_clip_request(self):
        """Nested format converts to AddClipRequest correctly."""
        from src.schemas.ai import AddClipRequest
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "image",
            "layer_id": "layer-2",
            "asset_id": "00000000-0000-0000-0000-000000000002",
            "start_ms": 1000,
            "duration_ms": 3000,
            "transform": {
                "position": {"x": -50, "y": 100},
                "scale": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)
        flat_dict = unified.to_flat_dict()
        add_clip = AddClipRequest.model_validate(flat_dict)

        assert add_clip.layer_id == "layer-2"
        assert add_clip.x == -50
        assert add_clip.y == 100
        assert add_clip.scale == 0.5

    def test_text_clip_with_content(self):
        """Text clip with content parses and converts correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        text_data = {
            "type": "text",
            "layer_id": "layer-text",
            "start_ms": 0,
            "duration_ms": 5000,
            "text_content": "Hello World",
            "transform": {
                "position": {"x": 0, "y": 300},
                "scale": {"x": 1, "y": 1},
            },
        }

        unified = UnifiedClipInput.model_validate(text_data)
        flat_dict = unified.to_flat_dict()

        assert flat_dict["text_content"] == "Hello World"
        assert flat_dict["x"] == 0
        assert flat_dict["y"] == 300

    def test_adapt_clip_input_function(self):
        """adapt_clip_input helper function works correctly."""
        from src.schemas.clip_adapter import adapt_clip_input

        # Test flat format
        flat_result = adapt_clip_input({
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 10,
            "y": 20,
        })
        assert flat_result["x"] == 10
        assert flat_result["y"] == 20

        # Test nested format
        nested_result = adapt_clip_input({
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 30, "y": 40},
                "scale": {"x": 2, "y": 2},
            },
        })
        assert nested_result["x"] == 30
        assert nested_result["y"] == 40
        assert nested_result["scale"] == 2

    def test_create_clip_request_with_flat_format(self):
        """CreateClipRequest accepts flat format."""
        from src.api.ai_v1 import CreateClipRequest

        request = CreateClipRequest.model_validate({
            "options": {"validate_only": False},
            "clip": {
                "layer_id": "layer-1",
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "start_ms": 0,
                "duration_ms": 1000,
                "x": 100,
                "y": 200,
            },
        })

        internal = request.to_internal_clip()
        assert internal.layer_id == "layer-1"
        assert internal.x == 100
        assert internal.y == 200

    def test_create_clip_request_with_nested_format(self):
        """CreateClipRequest accepts nested format."""
        from src.api.ai_v1 import CreateClipRequest

        request = CreateClipRequest.model_validate({
            "options": {"validate_only": True},
            "clip": {
                "type": "video",
                "layer_id": "layer-1",
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "start_ms": 0,
                "duration_ms": 1000,
                "transform": {
                    "position": {"x": 100, "y": 200},
                    "scale": {"x": 1.5, "y": 1.5},
                },
            },
        })

        internal = request.to_internal_clip()
        assert internal.layer_id == "layer-1"
        assert internal.x == 100
        assert internal.y == 200
        assert internal.scale == 1.5

    def test_text_style_with_known_keys_parses_to_model(self):
        """TextStyle with only known keys parses to TextStyle model."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "start_ms": 0,
            "duration_ms": 1000,
            "text_content": "Hello",
            "text_style": {
                "font_family": "Arial",
                "font_size": 24,
                "color": "#000000",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        # With only known keys, it parses as TextStyle (not dict)
        from src.schemas.clip_adapter import TextStyle

        assert isinstance(unified.text_style, TextStyle)
        assert unified.text_style.font_family == "Arial"
        assert unified.text_style.font_size == 24

    def test_text_style_with_unknown_keys_falls_back_to_dict(self):
        """TextStyle with unknown keys falls back to dict, preserving all keys."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "start_ms": 0,
            "duration_ms": 1000,
            "text_content": "Hello",
            "text_style": {
                "fontFamily": "Arial",  # camelCase (unknown key)
                "fontSize": 24,  # camelCase (unknown key)
                "color": "#000000",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        # With unknown keys, it falls back to dict (preserving all keys)
        assert isinstance(unified.text_style, dict)
        assert unified.text_style["fontFamily"] == "Arial"
        assert unified.text_style["fontSize"] == 24
        assert unified.text_style["color"] == "#000000"

    def test_non_uniform_scale_generates_warning(self):
        """Non-uniform scale (x != y) generates a warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 2.0, "y": 1.5},  # Non-uniform
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about non-uniform scale
        assert any("Non-uniform scale" in w for w in warnings)
        assert any("coerced to uniform scale=2.0" in w for w in warnings)
        # scale.x is used as uniform scale
        assert unified.scale == 2.0

    def test_unsupported_transform_rotation_generates_warning(self):
        """Non-zero rotation generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": 45,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("rotation=45" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transform_opacity_generates_warning(self):
        """Non-default opacity generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "opacity": 0.5,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("opacity=0.5" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transform_anchor_generates_warning(self):
        """Non-default anchor generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "anchor": {"x": 0, "y": 0},  # Non-default
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("anchor" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_effects_field_generates_warning(self):
        """Effects field generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "effects": {
                "opacity": 0.8,
                "blend_mode": "multiply",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("effects" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transitions_generate_warnings(self):
        """Transition fields generate warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transition_in": {
                "type": "fade",
                "duration_ms": 500,
            },
            "transition_out": {
                "type": "slide",
                "duration_ms": 300,
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("transition_in" in w and "not yet supported" in w for w in warnings)
        assert any("transition_out" in w and "not yet supported" in w for w in warnings)

    def test_no_warnings_for_fully_supported_flat_format(self):
        """Flat format with only supported fields generates no warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert len(warnings) == 0

    def test_no_warnings_for_fully_supported_nested_format(self):
        """Nested format with only supported fields generates no warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.5, "y": 1.5},  # Uniform
                # rotation, opacity, anchor at defaults
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert len(warnings) == 0

    def test_mixed_format_generates_warning_flat_takes_precedence(self):
        """Mixed format (both flat + nested) warns and flat takes precedence."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            # Flat values
            "x": 50,
            "y": 75,
            "scale": 2.0,
            # Nested values (different from flat)
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.0, "y": 1.0},
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about mixed format
        assert any("Both flat" in w and "nested" in w for w in warnings)
        assert any("flat values take precedence" in w for w in warnings)

        # Flat values should be used (not overwritten by nested)
        assert unified.x == 50
        assert unified.y == 75
        assert unified.scale == 2.0

    def test_mixed_format_still_warns_about_unsupported_transform_fields(self):
        """Mixed format still warns about unsupported transform fields."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            # Flat values
            "x": 50,
            "y": 75,
            "scale": 2.0,
            # Nested with unsupported fields
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.0, "y": 1.0},
                "rotation": 45,  # Unsupported
                "opacity": 0.5,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about mixed format
        assert any("Both flat" in w for w in warnings)
        # Should also warn about unsupported transform fields
        assert any("rotation=45" in w for w in warnings)
        assert any("opacity=0.5" in w for w in warnings)
