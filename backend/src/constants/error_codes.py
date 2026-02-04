"""Error codes dictionary for AI-Friendly API.

This is the single source of truth for all error codes, their retryability,
and suggested recovery actions. Used by exception handlers to generate
machine-readable error responses.
"""

from typing import Any, TypedDict


class SuggestedActionSpec(TypedDict, total=False):
    """Specification for suggested recovery action."""

    action: str
    endpoint: str
    parameters: dict[str, Any]


class ErrorCodeSpec(TypedDict, total=False):
    """Specification for an error code."""

    retryable: bool
    suggested_action: str
    suggested_endpoint: str
    parameters: dict[str, Any]


# Error codes dictionary - single source of truth
ERROR_CODES: dict[str, ErrorCodeSpec] = {
    # ==========================================================================
    # Resource errors (retryable after refresh)
    # ==========================================================================
    "PROJECT_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/capabilities",
    },
    "CLIP_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "LAYER_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "ASSET_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/assets",
    },
    "AUDIO_TRACK_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "AUDIO_CLIP_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "MARKER_NOT_FOUND": {
        "retryable": True,
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    # ==========================================================================
    # Validation errors (not retryable, fix input)
    # ==========================================================================
    "INVALID_TIME_RANGE": {
        "retryable": False,
    },
    "OUT_OF_BOUNDS": {
        "retryable": False,
    },
    "LAYER_LOCKED": {
        "retryable": False,
    },
    "MISSING_REQUIRED_FIELD": {
        "retryable": False,
    },
    "INVALID_FIELD_VALUE": {
        "retryable": False,
    },
    "INVALID_LAYER_TYPE": {
        "retryable": False,
    },
    "INVALID_ASSET_TYPE": {
        "retryable": False,
    },
    "DURATION_TOO_LONG": {
        "retryable": False,
    },
    "TOO_MANY_CLIPS": {
        "retryable": False,
    },
    "TOO_MANY_LAYERS": {
        "retryable": False,
    },
    # ==========================================================================
    # Conflict errors
    # ==========================================================================
    "CLIP_OVERLAP": {
        "retryable": False,
    },
    "CONCURRENT_MODIFICATION": {
        "retryable": True,
        "suggested_action": "refresh_etag",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "IDEMPOTENCY_CONFLICT": {
        "retryable": False,
    },
    "OPERATION_IN_PROGRESS": {
        "retryable": True,
        "suggested_action": "wait_and_retry",
        "parameters": {"delay_ms": 1000},
    },
    # ==========================================================================
    # Feature/capability errors
    # ==========================================================================
    "FEATURE_NOT_SUPPORTED": {
        "retryable": False,
    },
    "OPERATION_NOT_SUPPORTED": {
        "retryable": False,
    },
    # ==========================================================================
    # Rollback errors
    # ==========================================================================
    "ROLLBACK_NOT_AVAILABLE": {
        "retryable": False,
    },
    "OPERATION_ALREADY_ROLLED_BACK": {
        "retryable": False,
    },
    "ROLLBACK_FAILED": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 1000, "max_retries": 2},
    },
    # ==========================================================================
    # Authentication/Authorization errors
    # ==========================================================================
    "UNAUTHORIZED": {
        "retryable": False,
    },
    "FORBIDDEN": {
        "retryable": False,
    },
    "TOKEN_EXPIRED": {
        "retryable": True,
        "suggested_action": "refresh_token",
    },
    # ==========================================================================
    # System errors (retryable with backoff)
    # ==========================================================================
    "RATE_LIMITED": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 1000, "max_retries": 3},
    },
    "INTERNAL_ERROR": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    "SERVICE_UNAVAILABLE": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 5000, "max_retries": 3},
    },
    "DATABASE_ERROR": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    "STORAGE_ERROR": {
        "retryable": True,
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    # ==========================================================================
    # Request errors
    # ==========================================================================
    "BAD_REQUEST": {
        "retryable": False,
    },
    "VALIDATION_ERROR": {
        "retryable": False,
    },
    "NOT_FOUND": {
        "retryable": False,
    },
}


def get_error_spec(code: str) -> ErrorCodeSpec:
    """Get error specification by code.

    Args:
        code: The error code

    Returns:
        ErrorCodeSpec with retryable flag and suggested actions
    """
    return ERROR_CODES.get(code, {"retryable": False})


def is_retryable(code: str) -> bool:
    """Check if an error code is retryable.

    Args:
        code: The error code

    Returns:
        True if the error is retryable
    """
    spec = get_error_spec(code)
    return spec.get("retryable", False)
