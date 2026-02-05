# AI Friendly Gap Analysis (Current vs Target)

Last updated: 2026-02-04

This document compares the current implementation to the AI-friendly target spec.
Source references are in code under `backend/src/api/*`, `backend/src/services/*`, `backend/src/schemas/*`, and `backend/src/main.py`.

---

## 1) API Surface & Versioning

- **Target**: `/api/ai/v1` with explicit versioning, `/capabilities`, uniform schemas.
- **Current**:
  - v1 endpoints exist under `/api/ai/v1` with `/capabilities`.
  - Version info exists at `/api/ai/v1/version`.
- **Gap**:
  - OpenAPI remains a target spec (not a strict contract for runtime).
- **Impact**: Low (core versioning + capabilities are in place).

---

## 2) Project Invariants (time_base / fps / resolution)

- **Target**: `time_base=ms`, `frame_rate=30`, `resolution=1920x1080`, immutable after creation.
- **Current**:
  - Project model stores `width/height/fps` with defaults (1920/1080/30) but **mutable** via `/api/projects/{id}` (PUT).
  - No `time_base` or `overlap_policy` field in DB.
- **Gap**:
  - No immutable project invariants.
  - Missing explicit `time_base` and overlap policy.
- **Impact**: High (AI cannot assume fixed units without an invariant contract).

---

## 3) Read Models (L1/L2/L3)

- **Target**: L1/L2/L3 + full timeline + diff + validation results.
- **Current** (`/api/ai/v1`):
  - L1: `/projects/{id}/overview`
  - L2: `/projects/{id}/structure`, `/projects/{id}/at-time/{ms}`, `/projects/{id}/assets`
  - L3: `/projects/{id}/clips/{clip_id}`, `/projects/{id}/audio-clips/{clip_id}`
  - History: `/projects/{id}/history`, `/projects/{id}/operations/{operation_id}`
- **Gap**:
  - No full timeline endpoint.
  - No standalone diff endpoint (diff is returned per-mutation when include_diff=true).
- **Impact**: Medium (state inspection improved; full timeline still missing).

---

## 4) Write Operations Coverage

- **Target**: full CRUD for layers, clips, audio, markers, keyframes, audio tracks, volume keyframes.
- **Current** (`/api/ai/v1`):
  - Layers: add / reorder / update
  - Video clips: add / move / transform / delete
  - Audio clips: add / move / delete
  - Audio tracks: add
  - Markers: add / update / delete
  - Semantic ops: snap_to_previous, snap_to_next, close_gap, auto_duck_bgm, rename_layer
  - Batch operations (best_effort)
- **Missing (AI cannot do via API)**:
  - Keyframe CRUD (video + audio volume)
  - Clip copy/paste
  - Clip duration/in-out update in one call
  - Full text/shape schema operations
- **Impact**: Medium (core CRUD is available; advanced ops remain).

---

## 5) Transform & Coordinate Model

- **Target**: `transform` object with position/scale/rotation/opacity/anchor (normalized anchor).
- **Current**:
  - `AddClipRequest` uses flat `x/y/scale` (not nested transform).
  - `UpdateClipTransformRequest` includes `x/y/width/height/scale/rotation/anchor`.
  - `anchor` is **enum string** (center/top-left/etc), not normalized.
  - `opacity` is only in effects, not transform.
- **Gap**:
  - Model not aligned with target transform schema.
  - Anchor semantics differ (string vs normalized).
- **Impact**: Medium-High (coordinate confusion / hallucination risk).

---

## 6) Text / Shape Schema

- **Target**: strict `TextStyle` and `Shape` schemas with constraints.
- **Current**:
  - `text_style` is a free-form dict in `AddClipRequest`.
  - No `Shape` schema in AI requests.
- **Gap**:
  - Missing typed schema for text/shape.
- **Impact**: High (AI cannot rely on validated structure).

---

## 7) Validation / Safe Apply

- **Target**: `validate_only`, `diff`, `rollback` for all mutations.
- **Current**:
  - `options.validate_only` is supported for v1 mutation endpoints.
  - `options.include_diff=true` returns per-operation diff.
  - Rollback is available via `/operations/{operation_id}/rollback` for supported ops.
- **Gap**:
  - No standalone diff endpoint (by design; use include_diff).
- **Impact**: Low (safe apply is supported in v1).

---

## 8) Overlap Policy

- **Target**: overlap policy is explicit and enforced.
- **Current**:
  - AI service explicitly **removes overlap checks** for add/move (video + audio).
  - Composition validator can detect overlap, but does not block changes.
- **Gap**:
  - Behavior contradicts `overlap_policy=disallow` target.
- **Impact**: High (unexpected collisions / visual/audio conflicts).

---

## 9) Batch Semantics

- **Target**: `atomic` vs `best_effort` with clear rollback semantics.
- **Current**:
  - Batch executes in order; partial failures are allowed.
  - No `atomic` option.
- **Gap**:
  - No transaction-like semantics.
- **Impact**: Medium (state inconsistency after partial failures).

---

## 10) Error Model

- **Target**: structured errors with `code`, `details`, `location`, `suggested_fix`.
- **Current**:
  - FastAPI `HTTPException` with `detail` string.
  - No stable `error_code` contract.
- **Gap**:
  - Errors are not machine-actionable for AI.
- **Impact**: High (AI cannot self-correct).

---

## 11) Idempotency & Concurrency

- **Target**: `Idempotency-Key` + ETag/If-Match.
- **Current**:
  - No idempotency headers or ETag usage in AI endpoints.
  - `/api/projects/{id}/timeline` allows full timeline overwrite.
- **Gap**:
  - No concurrency control / duplicate suppression.
- **Impact**: Medium-High (race conditions, duplicate edits).

---

## 12) Plan APIs

- **Target**: generic edit plan with validate/apply.
- **Current**:
  - `/api/ai-video` has **video plan** generation/apply (content planning), not timeline edit plan.
  - No plan validation/diff in `/api/ai`.
- **Gap**:
  - Missing generic editing plan API and validation flow.
- **Impact**: Medium-High (AI cannot stage complex edits safely).

---

## 13) Asset Pipeline

- **Target**: AI can create/upload/list assets via standard API.
- **Current**:
  - Assets are managed under `/api/projects/{id}/assets` and `/api/projects/{id}/assets/upload-url`.
  - AI namespace provides **read-only** asset catalog via `/api/ai/v1/projects/{project_id}/assets`.
- **Gap**:
  - AI cannot upload assets via AI endpoints.
- **Impact**: Medium (AI is blocked from fully autonomous workflows).

---

## 14) Rendering

- **Target**: render start/status under AI namespace.
- **Current**:
  - Render endpoints exist under `/api/projects/{id}/render*` (non-AI).
  - AI cannot trigger render in its own API flow.
- **Gap**:
  - Missing AI render control.
- **Impact**: Medium (AI cannot complete end-to-end flow).

---

## Summary: Highest Risk Gaps (Priority)

1) No validate-only + diff + rollback (unsafe edits)
2) No structured error codes (AI cannot self-correct)
3) Overlap policy not enforced in AI add/move
4) Missing keyframe/marker/audio track APIs
5) No idempotency/ETag for concurrency safety
6) Transform/anchor schema mismatch

---

## Suggested Next Steps (Target Alignment)

1) Introduce `validate_only` + `diff` for all mutation endpoints.
2) Add structured error schema (`code`, `location`, `suggested_fix`).
3) Enforce overlap policy or make it explicit with project-level invariant.
4) Add missing CRUD for markers, keyframes, audio tracks, volume keyframes.
5) Normalize transform schema (position/scale/rotation/opacity/anchor).
6) Add idempotency and ETag support.
