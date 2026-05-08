"""ETag utilities for HTTP caching (RFC 7232).

Provides weak ETag generation and If-None-Match handling for GET endpoints.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request, Response
from pydantic import BaseModel


def _serialize(payload: Any) -> str:
    """Normalize payload to a stable JSON string for hashing.

    - Pydantic BaseModel: use model_dump() with mode='json' to get
      JSON-serializable dicts, then json.dumps with sorted keys.
    - list of BaseModel: same, element-by-element.
    - Other (already-serializable types): json.dumps with sorted keys.
    """
    data: Any
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    elif isinstance(payload, list) and all(isinstance(item, BaseModel) for item in payload):
        data = [item.model_dump(mode="json") for item in payload]
    else:
        data = payload
    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)


def _remove_keys(data: Any, keys_to_exclude: set[str]) -> Any:
    """Recursively remove specified keys from a dict or list-of-dicts structure."""
    if isinstance(data, dict):
        return {
            k: _remove_keys(v, keys_to_exclude) for k, v in data.items() if k not in keys_to_exclude
        }
    if isinstance(data, list):
        return [_remove_keys(item, keys_to_exclude) for item in data]
    return data


def compute_etag(payload: Any, *, exclude_keys: list[str] | None = None) -> str:
    """Compute a weak ETag from the payload.

    Returns a string in the form ``W/"<16-hex-chars>"``.
    Uses SHA-256 of the stable JSON representation of the payload.

    Args:
        payload: The response payload (Pydantic model, list of models, or
                 JSON-serialisable object).
        exclude_keys: Optional list of top-level (or nested) dict keys to omit
                      before hashing.  Use this to exclude volatile fields such
                      as GCS signed URLs that change on every request but do not
                      reflect a logical data change.
    """
    data: Any
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    elif isinstance(payload, list) and all(isinstance(item, BaseModel) for item in payload):
        data = [item.model_dump(mode="json") for item in payload]
    else:
        data = payload

    if exclude_keys:
        data = _remove_keys(data, set(exclude_keys))

    serialized = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f'W/"{digest}"'


def etag_response(
    request: Request, payload: Any, *, exclude_keys: list[str] | None = None
) -> Response:
    """Return 304 or 200 JSON response based on ETag / If-None-Match negotiation.

    Args:
        request: The incoming FastAPI Request (used to read If-None-Match header).
        payload: The response payload (Pydantic model, list of models, or
                 JSON-serialisable object).  Used to compute the ETag and as
                 the 200 response body.
        exclude_keys: Optional list of dict keys to omit when computing the
                      ETag hash.  The response body is always the *full*
                      payload; only the hash input is filtered.  Use this for
                      fields whose values change on every request but do not
                      represent a logical data change (e.g. GCS signed URLs).

    Returns:
        - ``Response(status_code=304)`` with ``ETag`` header if the client's
          ``If-None-Match`` header matches the computed ETag.
        - ``JSONResponse`` with ``ETag`` header and the serialised payload
          otherwise.
    """
    etag = compute_etag(payload, exclude_keys=exclude_keys)
    if_none_match = request.headers.get("if-none-match") or request.headers.get("If-None-Match")

    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    serialized = _serialize(payload)
    return Response(
        content=serialized,
        status_code=200,
        media_type="application/json",
        headers={"ETag": etag},
    )
