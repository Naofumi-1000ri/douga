"""ETag / If-None-Match caching tests for Assets and Sequences endpoints.

Tests verify:
1. GET assets list returns 200 + ETag header on first request
2. Same ETag in If-None-Match returns 304 (empty body)
3. After data change, ETag changes and returns 200
4. Sequence detail: timeline_data child changes alter the ETag
5. Sequence list: ETag reflects list contents
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.api._etag import compute_etag, etag_response
from src.schemas.asset import AssetResponse
from src.schemas.sequence import SequenceDetail, SequenceListItem

# ---------------------------------------------------------------------------
# Helpers: fake Request objects
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for fastapi.Request, supports reading headers."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers: dict[str, str] = headers or {}


# ---------------------------------------------------------------------------
# Unit tests for _etag utilities
# ---------------------------------------------------------------------------


def test_compute_etag_returns_weak_etag_format() -> None:
    payload = [{"id": "abc", "name": "foo"}]
    etag = compute_etag(payload)
    assert etag.startswith('W/"')
    assert etag.endswith('"')
    # 16 hex chars inside quotes
    inner = etag[3:-1]
    assert len(inner) == 16
    assert all(c in "0123456789abcdef" for c in inner)


def test_compute_etag_stable_for_same_payload() -> None:
    payload = [{"z": 1, "a": 2}]
    assert compute_etag(payload) == compute_etag(payload)


def test_compute_etag_differs_for_different_payload() -> None:
    assert compute_etag({"key": "a"}) != compute_etag({"key": "b"})


def test_compute_etag_key_order_stable() -> None:
    """Dict with different insertion order must produce same ETag (sort_keys=True)."""
    a = {"z": 1, "a": 2}
    b = {"a": 2, "z": 1}
    assert compute_etag(a) == compute_etag(b)


# ---------------------------------------------------------------------------
# Unit tests for etag_response helper
# ---------------------------------------------------------------------------


def test_etag_response_200_without_if_none_match() -> None:
    req = _FakeRequest()
    payload = {"hello": "world"}
    resp = etag_response(req, payload)
    assert resp.status_code == 200
    assert "ETag" in resp.headers
    assert resp.headers["ETag"].startswith('W/"')
    body = json.loads(resp.body)
    assert body == payload


def test_etag_response_304_on_matching_etag() -> None:
    payload = {"hello": "world"}
    etag = compute_etag(payload)
    req = _FakeRequest(headers={"if-none-match": etag})
    resp = etag_response(req, payload)
    assert resp.status_code == 304
    assert resp.headers.get("ETag") == etag
    assert resp.body == b""


def test_etag_response_200_on_stale_etag() -> None:
    payload = {"hello": "world"}
    req = _FakeRequest(headers={"if-none-match": 'W/"deadbeefdeadbeef"'})
    resp = etag_response(req, payload)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Stub helpers shared across API handler tests
# ---------------------------------------------------------------------------


def _make_asset_response(
    *,
    project_id: UUID | None = None,
    name: str = "test-asset",
    asset_type: str = "video",
) -> AssetResponse:
    return AssetResponse(
        id=uuid4(),
        project_id=project_id or uuid4(),
        name=name,
        type=asset_type,
        subtype="other",
        storage_key="key/test.mp4",
        storage_url="https://storage.example.com/test.mp4",
        thumbnail_url=None,
        duration_ms=5000,
        width=1920,
        height=1080,
        file_size=1024 * 1024,
        mime_type="video/mp4",
        sample_rate=None,
        channels=None,
        has_alpha=False,
        chroma_key_color=None,
        hash=None,
        is_internal=False,
        folder_id=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata=None,
    )


def _make_sequence_list_item(
    *,
    project_id: UUID | None = None,
    name: str = "Sequence 1",
    version: int = 1,
) -> SequenceListItem:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SequenceListItem(
        id=uuid4(),
        name=name,
        version=version,
        duration_ms=0,
        is_default=True,
        locked_by=None,
        lock_holder_name=None,
        thumbnail_url=None,
        created_at=now,
        updated_at=now,
    )


def _make_sequence_detail(
    *,
    project_id: UUID | None = None,
    name: str = "Sequence 1",
    version: int = 1,
    timeline_data: dict[str, Any] | None = None,
) -> SequenceDetail:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SequenceDetail(
        id=uuid4(),
        project_id=project_id or uuid4(),
        name=name,
        timeline_data=timeline_data or {"version": "1.0", "layers": [], "audio_tracks": []},
        version=version,
        duration_ms=0,
        is_default=True,
        locked_by=None,
        lock_holder_name=None,
        thumbnail_url=None,
        locked_at=None,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Case 1: GET assets list — 200 + ETag on first request
# ---------------------------------------------------------------------------


def test_assets_list_returns_200_with_etag() -> None:
    """First GET returns 200 and includes ETag header."""
    req = _FakeRequest()
    assets = [_make_asset_response()]
    resp = etag_response(req, assets)
    assert resp.status_code == 200
    assert "ETag" in resp.headers
    etag = resp.headers["ETag"]
    assert etag.startswith('W/"')


# ---------------------------------------------------------------------------
# Case 2: Same ETag → 304 (no body)
# ---------------------------------------------------------------------------


def test_assets_list_returns_304_on_matching_etag() -> None:
    """If-None-Match with current ETag → 304, empty body."""
    assets = [_make_asset_response()]
    etag = compute_etag(assets)

    req = _FakeRequest(headers={"if-none-match": etag})
    resp = etag_response(req, assets)
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers.get("ETag") == etag


# ---------------------------------------------------------------------------
# Case 3: Data change → ETag changes → 200
# ---------------------------------------------------------------------------


def test_assets_list_etag_changes_after_data_modification() -> None:
    """After asset list changes, the ETag must differ and return 200."""
    assets_v1 = [_make_asset_response(name="asset-v1")]
    etag_v1 = compute_etag(assets_v1)

    # Simulate adding a new asset
    assets_v2 = [_make_asset_response(name="asset-v1"), _make_asset_response(name="asset-v2")]
    etag_v2 = compute_etag(assets_v2)

    # ETags must differ
    assert etag_v1 != etag_v2, "regression: ETag must change when data changes"

    # With old ETag, should get 200 (not 304)
    req = _FakeRequest(headers={"if-none-match": etag_v1})
    resp = etag_response(req, assets_v2)
    assert resp.status_code == 200
    assert resp.headers.get("ETag") == etag_v2


# ---------------------------------------------------------------------------
# Case 4: Sequence detail — timeline_data child changes alter ETag
# ---------------------------------------------------------------------------


def test_sequence_detail_etag_changes_when_timeline_data_child_changes() -> None:
    """timeline_data nested child mutations must change the ETag."""
    detail_v1 = _make_sequence_detail(
        timeline_data={
            "version": "1.0",
            "layers": [{"id": "layer-1", "clips": [{"start_ms": 0, "end_ms": 1000}]}],
            "audio_tracks": [],
        }
    )

    detail_v2 = _make_sequence_detail(
        timeline_data={
            "version": "1.0",
            "layers": [
                # clip end_ms changed — a child-level mutation
                {"id": "layer-1", "clips": [{"start_ms": 0, "end_ms": 2000}]}
            ],
            "audio_tracks": [],
        }
    )

    etag_v1 = compute_etag(detail_v1)
    etag_v2 = compute_etag(detail_v2)

    assert etag_v1 != etag_v2, (
        "regression: ETag must change when timeline_data child element changes"
    )

    # Sending old ETag with new payload → 200
    req = _FakeRequest(headers={"if-none-match": etag_v1})
    resp = etag_response(req, detail_v2)
    assert resp.status_code == 200

    # Sending correct ETag → 304
    req_match = _FakeRequest(headers={"if-none-match": etag_v2})
    resp_match = etag_response(req_match, detail_v2)
    assert resp_match.status_code == 304


# ---------------------------------------------------------------------------
# Case 5: Sequence list — ETag reflects list contents
# ---------------------------------------------------------------------------


def test_sequence_list_etag_changes_on_list_mutation() -> None:
    """Sequence list ETag must change when a list item changes."""
    items_v1 = [_make_sequence_list_item(name="Seq A", version=1)]
    items_v2 = [_make_sequence_list_item(name="Seq A", version=2)]  # version bumped

    etag_v1 = compute_etag(items_v1)
    etag_v2 = compute_etag(items_v2)

    assert etag_v1 != etag_v2, (
        "regression: Sequence list ETag must change when item version changes"
    )

    # Old ETag → 200 for new data
    req = _FakeRequest(headers={"if-none-match": etag_v1})
    resp = etag_response(req, items_v2)
    assert resp.status_code == 200

    # Correct ETag → 304
    req_match = _FakeRequest(headers={"if-none-match": etag_v2})
    resp_match = etag_response(req_match, items_v2)
    assert resp_match.status_code == 304


# ---------------------------------------------------------------------------
# Regression: GCS signed URL must not affect ETag (P0-1)
# ---------------------------------------------------------------------------


def _make_asset_with_signed_url(signed_url: str, thumbnail_url: str | None = None) -> AssetResponse:
    """Create an AssetResponse with a specific signed URL (simulating GCS re-signing)."""
    return AssetResponse(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        name="test-video",
        type="video",
        subtype="other",
        storage_key="key/test.mp4",
        storage_url=signed_url,
        thumbnail_url=thumbnail_url,
        duration_ms=5000,
        width=1920,
        height=1080,
        file_size=1024 * 1024,
        mime_type="video/mp4",
        sample_rate=None,
        channels=None,
        has_alpha=False,
        chroma_key_color=None,
        hash=None,
        is_internal=False,
        folder_id=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata=None,
    )


def test_assets_etag_stable_despite_differing_signed_urls() -> None:
    """regression (P0-1): ETag must be the same for two asset lists that differ only
    in GCS signed URLs (storage_url / thumbnail_url).

    GCS signed URLs are re-generated on every request and contain volatile
    query parameters (X-Goog-Expires, X-Goog-Signature, etc.).  Including them
    in the hash would prevent 304 responses entirely.
    """
    # Simulate two consecutive GET /assets requests where only the signed URL changes
    url_a = (
        "https://storage.googleapis.com/bucket/key/test.mp4"
        "?X-Goog-Expires=3600&X-Goog-Signature=aaaa1111"
    )
    url_b = (
        "https://storage.googleapis.com/bucket/key/test.mp4"
        "?X-Goog-Expires=3600&X-Goog-Signature=bbbb2222"
    )
    thumb_a = "https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Signature=cccc"
    thumb_b = "https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Signature=dddd"

    assets_req1 = [_make_asset_with_signed_url(url_a, thumb_a)]
    assets_req2 = [_make_asset_with_signed_url(url_b, thumb_b)]

    etag_req1 = compute_etag(assets_req1, exclude_keys=["storage_url", "thumbnail_url"])
    etag_req2 = compute_etag(assets_req2, exclude_keys=["storage_url", "thumbnail_url"])

    assert etag_req1 == etag_req2, (
        "regression (P0-1): ETag must be identical when only GCS signed URLs differ. "
        "Including volatile signed URL fields in the hash breaks 304 caching."
    )


def test_assets_etag_changes_on_logical_data_change_even_with_exclude_keys() -> None:
    """P0-1 safety check: excluding signed URL fields must not prevent detection of
    actual data changes (e.g. file_size or name change).
    """
    asset_v1 = _make_asset_with_signed_url("https://storage.googleapis.com/bucket/key.mp4?sig=aaa")
    # Simulate asset rename
    asset_v2 = AssetResponse(**{**asset_v1.model_dump(), "name": "renamed-video"})

    etag_v1 = compute_etag([asset_v1], exclude_keys=["storage_url", "thumbnail_url"])
    etag_v2 = compute_etag([asset_v2], exclude_keys=["storage_url", "thumbnail_url"])

    assert etag_v1 != etag_v2, (
        "P0-1 safety: ETag must still change when non-URL fields change (e.g. name)."
    )


def test_etag_response_uses_exclude_keys() -> None:
    """etag_response with exclude_keys should return 304 when only excluded fields differ."""
    asset_v1 = _make_asset_with_signed_url("https://storage.example.com/v1?sig=aaa")
    asset_v2 = _make_asset_with_signed_url("https://storage.example.com/v1?sig=bbb")

    # Compute ETag ignoring storage_url
    etag = compute_etag([asset_v1], exclude_keys=["storage_url", "thumbnail_url"])

    # Request with that ETag should get 304 even though storage_url changed
    req = _FakeRequest(headers={"if-none-match": etag})
    resp = etag_response(req, [asset_v2], exclude_keys=["storage_url", "thumbnail_url"])
    assert resp.status_code == 304, (
        "etag_response must return 304 when only excluded (volatile) fields differ."
    )


# ---------------------------------------------------------------------------
# Regression: Sequences — signed thumbnail_url must not affect ETag (P0-2) (#230 item4)
# ---------------------------------------------------------------------------


def _make_sequence_list_item_with_thumbnail(
    thumbnail_url: str | None,
    version: int = 1,
) -> SequenceListItem:
    """Create a SequenceListItem with a specific thumbnail_url (simulating GCS re-signing)."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SequenceListItem(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        name="Sequence A",
        version=version,
        duration_ms=0,
        is_default=True,
        locked_by=None,
        lock_holder_name=None,
        thumbnail_url=thumbnail_url,
        created_at=now,
        updated_at=now,
    )


def test_sequence_list_etag_stable_despite_differing_thumbnail_urls() -> None:
    """regression (P0-2 sequences): Sequence list ETag must be the same when only
    thumbnail_url differs (GCS re-signing).  Sequences API uses
    exclude_keys=["thumbnail_url", "locked_at"].
    """
    thumb_a = (
        "https://storage.googleapis.com/bucket/thumb.jpg"
        "?X-Goog-Date=20260101T000000Z&X-Goog-Expires=3600&X-Goog-Signature=aaaa"
    )
    thumb_b = (
        "https://storage.googleapis.com/bucket/thumb.jpg"
        "?X-Goog-Date=20260101T000000Z&X-Goog-Expires=3600&X-Goog-Signature=bbbb"
    )

    items_req1 = [_make_sequence_list_item_with_thumbnail(thumb_a)]
    items_req2 = [_make_sequence_list_item_with_thumbnail(thumb_b)]

    etag_req1 = compute_etag(items_req1, exclude_keys=["thumbnail_url", "locked_at"])
    etag_req2 = compute_etag(items_req2, exclude_keys=["thumbnail_url", "locked_at"])

    assert etag_req1 == etag_req2, (
        "regression (P0-2): Sequence list ETag must be identical when only thumbnail_url differs. "
        "Including volatile signed URL fields in the hash breaks 304 caching."
    )


def test_sequence_list_etag_changes_on_version_bump_even_with_exclude_thumbnail() -> None:
    """P0-2 safety check: excluding thumbnail_url must not prevent detection of
    actual data changes (e.g. version bump).
    """
    item_v1 = _make_sequence_list_item_with_thumbnail(
        "https://storage.googleapis.com/bucket/thumb.jpg?sig=aaa", version=1
    )
    item_v2 = _make_sequence_list_item_with_thumbnail(
        "https://storage.googleapis.com/bucket/thumb.jpg?sig=bbb", version=2
    )

    etag_v1 = compute_etag([item_v1], exclude_keys=["thumbnail_url", "locked_at"])
    etag_v2 = compute_etag([item_v2], exclude_keys=["thumbnail_url", "locked_at"])

    assert etag_v1 != etag_v2, (
        "P0-2 safety: ETag must still change when non-URL fields change (e.g. version)."
    )


def test_sequence_list_etag_response_304_when_only_thumbnail_differs() -> None:
    """etag_response with exclude_keys should return 304 when only thumbnail_url differs."""
    item_v1 = _make_sequence_list_item_with_thumbnail(
        "https://storage.googleapis.com/bucket/thumb.jpg?sig=aaa"
    )
    item_v2 = _make_sequence_list_item_with_thumbnail(
        "https://storage.googleapis.com/bucket/thumb.jpg?sig=bbb"
    )

    etag = compute_etag([item_v1], exclude_keys=["thumbnail_url", "locked_at"])

    req = _FakeRequest(headers={"if-none-match": etag})
    resp = etag_response(req, [item_v2], exclude_keys=["thumbnail_url", "locked_at"])
    assert resp.status_code == 304, (
        "etag_response must return 304 when only thumbnail_url (volatile) differs."
    )
