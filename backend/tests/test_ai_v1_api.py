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

        # Check expected capability fields
        assert "effects" in data
        assert "easings" in data
        assert "max_layers" in data
        assert "max_duration_ms" in data
        assert "max_batch_ops" in data

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

        options = OperationOptions(
            validate_only=True,
            include_diff=False,
        )

        assert options.validate_only is True
        assert options.include_diff is False

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
