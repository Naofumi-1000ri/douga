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
    suggested_fix: str  # Human-readable fix instruction (always required)
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
        "suggested_fix": "Verify the project_id exists and you have access to it",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/capabilities",
    },
    "CLIP_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh timeline structure to get current clip IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "LAYER_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh timeline structure to get current layer IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "ASSET_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh asset catalog to get available asset IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/assets",
    },
    "AUDIO_TRACK_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh timeline structure to get current audio track IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "AUDIO_CLIP_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh timeline structure to get current audio clip IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "MARKER_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh timeline structure to get current marker IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "OPERATION_NOT_FOUND": {
        "retryable": True,
        "suggested_fix": "Refresh operation history to get valid operation IDs",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/history",
    },
    # ==========================================================================
    # Validation errors (not retryable, fix input)
    # ==========================================================================
    "INVALID_TIME_RANGE": {
        "retryable": False,
        "suggested_fix": "Ensure start_ms >= 0, duration_ms > 0, and in_point_ms < out_point_ms",
    },
    "OUT_OF_BOUNDS": {
        "retryable": False,
        "suggested_fix": "Check the allowed range in /capabilities and adjust the value",
    },
    "LAYER_LOCKED": {
        "retryable": False,
        "suggested_fix": "Unlock the layer before making changes, or use a different layer",
    },
    "MISSING_REQUIRED_FIELD": {
        "retryable": False,
        "suggested_fix": "Add the missing required field to your request",
    },
    "INVALID_FIELD_VALUE": {
        "retryable": False,
        "suggested_fix": "Check the field's allowed values in /capabilities and correct the value",
    },
    "INVALID_CLIP_TYPE": {
        "retryable": False,
        "suggested_fix": "Use a clip with the required type for this operation (e.g., text clip for text style updates)",
    },
    "INVALID_LAYER_TYPE": {
        "retryable": False,
        "suggested_fix": "Use a valid layer type for this operation",
    },
    "INVALID_ASSET_TYPE": {
        "retryable": False,
        "suggested_fix": "Use an asset with a compatible type for this clip",
    },
    "DURATION_TOO_LONG": {
        "retryable": False,
        "suggested_fix": "Reduce the duration to be within the maximum limit (check /capabilities)",
    },
    "TOO_MANY_CLIPS": {
        "retryable": False,
        "suggested_fix": "Remove some clips from the layer before adding more",
    },
    "TOO_MANY_LAYERS": {
        "retryable": False,
        "suggested_fix": "Remove some layers from the project before adding more",
    },
    # ==========================================================================
    # Conflict errors
    # ==========================================================================
    "CLIP_OVERLAP": {
        "retryable": False,
        "suggested_fix": "Adjust the clip timing to avoid overlapping with existing clips",
    },
    "CONCURRENT_MODIFICATION": {
        "retryable": True,
        "suggested_fix": "Re-fetch the project structure, get the new ETag, and retry with the updated If-Match header",
        "suggested_action": "refresh_etag",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    "IDEMPOTENCY_CONFLICT": {
        "retryable": False,
        "suggested_fix": "Use a new unique Idempotency-Key for different request parameters",
    },
    "OPERATION_IN_PROGRESS": {
        "retryable": True,
        "suggested_fix": "Wait for the current operation to complete, then retry",
        "suggested_action": "wait_and_retry",
        "parameters": {"delay_ms": 1000},
    },
    # ==========================================================================
    # Semantic operation errors
    # ==========================================================================
    "SEMANTIC_OPERATION_FAILED": {
        "retryable": False,
        "suggested_fix": "Check the error_message for details; common causes include missing target_clip_id, no previous/next clip to snap to, or layer not found",
        "suggested_action": "refresh_ids",
        "suggested_endpoint": "GET /api/ai/v1/projects/{project_id}/structure",
    },
    # ==========================================================================
    # Feature/capability errors
    # ==========================================================================
    "FEATURE_NOT_SUPPORTED": {
        "retryable": False,
        "suggested_fix": "Check /capabilities for supported features",
    },
    "OPERATION_NOT_SUPPORTED": {
        "retryable": False,
        "suggested_fix": "Check /capabilities for supported operations",
    },
    # ==========================================================================
    # Rollback errors
    # ==========================================================================
    "ROLLBACK_NOT_AVAILABLE": {
        "retryable": False,
        "suggested_fix": "This operation cannot be rolled back; manual correction is required",
    },
    "OPERATION_ALREADY_ROLLED_BACK": {
        "retryable": False,
        "suggested_fix": "This operation was already rolled back; no further action needed",
    },
    "ROLLBACK_FAILED": {
        "retryable": True,
        "suggested_fix": "Wait a moment and retry the rollback operation",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 1000, "max_retries": 2},
    },
    # ==========================================================================
    # Authentication/Authorization errors
    # ==========================================================================
    "UNAUTHORIZED": {
        "retryable": False,
        "suggested_fix": "Provide a valid Authorization header with a Bearer token",
    },
    "FORBIDDEN": {
        "retryable": False,
        "suggested_fix": "You do not have permission to access this resource",
    },
    "TOKEN_EXPIRED": {
        "retryable": True,
        "suggested_fix": "Refresh your authentication token and retry",
        "suggested_action": "refresh_token",
    },
    # ==========================================================================
    # System errors (retryable with backoff)
    # ==========================================================================
    "RATE_LIMITED": {
        "retryable": True,
        "suggested_fix": "Wait and retry with exponential backoff",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 1000, "max_retries": 3},
    },
    "INTERNAL_ERROR": {
        "retryable": True,
        "suggested_fix": "Wait a moment and retry; if the problem persists, contact support",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    "SERVICE_UNAVAILABLE": {
        "retryable": True,
        "suggested_fix": "The service is temporarily unavailable; retry after a short delay",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 5000, "max_retries": 3},
    },
    "DATABASE_ERROR": {
        "retryable": True,
        "suggested_fix": "Database operation failed; retry after a short delay",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    "STORAGE_ERROR": {
        "retryable": True,
        "suggested_fix": "Storage operation failed; retry after a short delay",
        "suggested_action": "retry_with_backoff",
        "parameters": {"delay_ms": 2000, "max_retries": 2},
    },
    # ==========================================================================
    # Request errors
    # ==========================================================================
    "BAD_REQUEST": {
        "retryable": False,
        "suggested_fix": "Check the request format and parameters",
    },
    "VALIDATION_ERROR": {
        "retryable": False,
        "suggested_fix": "Fix the validation errors indicated in the error message",
    },
    "NOT_FOUND": {
        "retryable": False,
        "suggested_fix": "Verify the resource path and ID are correct",
    },
    "HTTP_ERROR": {
        "retryable": False,
        "suggested_fix": "An unexpected HTTP error occurred; check the status code and message for details",
    },
}

# Default spec for unknown error codes
_DEFAULT_ERROR_SPEC: ErrorCodeSpec = {
    "retryable": False,
    "suggested_fix": "Check the error code and message for details",
}


def get_error_spec(code: str) -> ErrorCodeSpec:
    """Get error specification by code.

    Args:
        code: The error code

    Returns:
        ErrorCodeSpec with retryable flag and suggested actions.
        Returns a default spec with suggested_fix for unknown codes.
    """
    return ERROR_CODES.get(code, _DEFAULT_ERROR_SPEC)


def is_retryable(code: str) -> bool:
    """Check if an error code is retryable.

    Args:
        code: The error code

    Returns:
        True if the error is retryable
    """
    spec = get_error_spec(code)
    return spec.get("retryable", False)
