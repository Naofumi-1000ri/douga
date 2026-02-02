# Session File Design

Status: Draft
Last updated: 2026-02-02
Owner: (TBD)

## 1. Overview

Session files let users save and restore the current timeline state within the same project. A session is stored as an **asset** of type `session`, can be filtered in the asset library, and can be opened via double‑click.

Scope is **project‑local only**; cross‑project sharing is out of scope.

## 2. Goals / Non‑Goals

Goals:
- Save the current timeline state as a session asset.
- Restore a saved session reliably with safe asset mapping.
- Avoid incorrect asset relinking (prefer user selection when ambiguous).
- Keep session metadata visible in the asset library without fetching JSON every time.

Non‑Goals:
- Cross‑project sharing/import/export of sessions.
- Versioned history or branching of sessions.
- Automatic repair of missing assets beyond mapping & user selection.

## 3. Key Decisions

- Mapping strategy: **Fingerprint match** (hash + file_size + duration_ms).
- Hash calculation: on upload; also during session save if hash is missing.
- Multiple candidates: **must prompt user** to choose (no auto‑pick).
- Session name duplicates: **UUID suffix** to avoid overwrite.
- Session schema migration: **frontend** handles migrations.

## 4. Data Model

### 4.1 Asset Model
- New asset type: `session`.
- Add optional `hash` field to `Asset`.
- Use existing `file_size` and `duration_ms` for fingerprinting.

### 4.2 Session JSON Schema

```json
{
  "schema_version": "1.0",
  "created_at": "2026-02-02T10:00:00Z",
  "app_version": "0.1.0",
  "timeline_data": {
    "version": "1.0",
    "duration_ms": 60000,
    "layers": [],
    "audio_tracks": [],
    "groups": []
  },
  "asset_references": [
    {
      "id": "asset-uuid",
      "name": "avatar.mp4",
      "type": "video",
      "fingerprint": {
        "hash": "sha256:abc123...",
        "file_size": 12345678,
        "duration_ms": 30000
      },
      "metadata": {
        "codec": "h264",
        "width": 1920,
        "height": 1080
      }
    }
  ]
}
```

Notes:
- `duration_ms = 0` is valid for images. Unknown values are **null**, not `0`.
- `metadata` is for display only; it is **not** used for matching.
- `schema_version` is independent of `timeline_data.version`.

## 5. Storage

- Session JSON stored in GCS: `sessions/{safe_name}.json`.
- Asset record stores:
  - `name` (sanitized + UUID if duplicated)
  - `type = session`
  - `storage_url`
  - `file_size`
  - `metadata.app_version`, `metadata.created_at`

### 5.1 Session Name Sanitization
- Trim whitespace, replace invalid characters, compress underscores.
- Limit to 100 chars.
- If empty, fallback to `"session"`.
- On collision, append short UUID.

## 6. API

### 6.1 Save Session
`POST /projects/{project_id}/sessions`

Inputs:
- `session_name` (string)
- `session_data` (SessionData)

Behavior:
1. Sanitize name and resolve duplicates.
2. Fetch project assets.
3. Compute missing hashes (if any) and **update both DB and session_data**.
4. Server sets `created_at` and `app_version` (ignore client values).
5. Persist JSON to GCS.
6. Create session asset record with metadata for listing.

Errors:
- Hash calculation timeout → warn, continue (hash may remain null).
- Save failure → return error, **do not open** session.

### 6.2 List Sessions
- Existing assets list API with `type=session` filter.
- Use asset metadata for `created_at` / `app_version` display.

### 6.3 Load Session
- Fetch session JSON from storage_url.
- Migrate schema if needed.
- Map asset references to project assets.

## 7. Hash Calculation & SSRF Controls

- **SSRF protection**: parse `storage_url` and only allow bucket `douga-assets-{project_id}`.
- Access GCS via SDK, not raw URL.
- Hash calculation may be **blocking**; must be executed in a background task or thread pool to avoid blocking the event loop.

## 8. Asset Mapping Algorithm

Priority order per reference:
1. **ID match** (UUID)
2. **Fingerprint match** (hash + size + duration)
   - Only if `hash` is non‑null.
3. **Partial match** (size + duration)
   - Only if both values are non‑null.
   - Single candidate → auto‑map with warning.
   - Multiple candidates → user selection.
4. No match → `unmappedAssetIds`.

If any references require user selection, **do not apply** the timeline until selection completes.

### Duration Strictness
- Current decision: `hash + size + duration` must all match for fingerprint equality.
- Risk: strict duration may cause false negatives due to rounding differences.
- Mitigation option (TBD): if `hash` matches but `duration` differs, allow mapping with warning.

## 9. Migration Strategy

- Frontend migrator upgrades old session formats to current schema.
- Unknown values remain **null** (never coerced to `0`).
- `timeline_data` migration handled by existing project timeline migration logic.

## 10. UI / UX

### 10.1 Asset Library
- Add filter: `[全部] [動画] [音声] [画像] [セッション]`.
- List items show `name`, `created_at`, `app_version` (from asset metadata).

### 10.2 Save Flow
- Button: “セッションを保存”.
- Name input dialog.
- Duplicate name → UUID suffix (inform user in UI).
- On error, show message and **keep current timeline**.

### 10.3 Open Flow
- Double‑click session asset.
- Confirm dialog: Save current changes?
  - Yes → save then open.
  - No → open without saving.
  - Cancel → do nothing.
- If user selection dialog appears and is cancelled → abort open.

### 10.4 Missing Assets
- Preview overlay: black background + “アセットがありません”.
- Timeline: warning icon on affected clips.
- Export/Render: black frames + warning log.

## 11. Error Handling Rules

- Save failure → no state change.
- Hash calculation timeout → continue; hash may remain null.
- JSON parse/migration error → show error, do not change timeline.
- Asset permission error → treat as unmapped and warn.

## 12. Performance Considerations

- Hashing large files is expensive; run in background and chunk reads.
- Avoid fetching session JSON for list views (use metadata).
- Large sessions should show loading indicator during mapping.

## 13. Testing Plan (Summary)

Unit:
- Migrator converts legacy format to fingerprint with nulls for unknowns.
- Mapper:
  - ID match
  - fingerprint match (hash non‑null)
  - partial match only with non‑null size/duration
  - hash‑null does not auto‑match
  - multiple candidates → pending selection
- Sanitization rules.
- Hash calculation for large files (chunked).

E2E (manual):
- Save → list shows session with metadata.
- Open → timeline restored.
- Missing assets → black preview + warning icon.
- Multiple candidates → selection dialog; cancel aborts open.

## 14. Open Questions

- Should hash match ignore duration mismatch (warn only)?
- Should session JSON store the final resolved filename (post‑sanitization) for user clarity?

