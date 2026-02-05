# AI Friendly Waterfall Plan (Next Development)

Last updated: 2026-02-03

Purpose: lock the next development into a **single, AI-friendly waterfall** flow.
Goal: remove ambiguity for AI by freezing invariants, contracts, and validation rules.

---

## 0) Scope Freeze (Non‑Negotiable Decisions)

These are fixed for the next release cycle:

- **time_base**: `ms` only
- **frame_rate**: `30` fixed
- **resolution**: `1920x1080` fixed
- **overlap_policy**: `disallow` (no overlaps on same layer/track)
- **anchor**: normalized `(0..1)` required in all transforms
- **rounding**: `round_half_up` (floor(x + 0.5))

Exit criteria:
- Written in spec and approved (no further changes during this cycle)

---

## 1) Contract Freeze (API + Schema)

Deliverables:
- OpenAPI (v1) as single source of truth
- JSON Schemas for all request/response payloads
- Error model (structured error codes + suggested_fix)
- validate_only + diff + rollback contract definitions

Exit criteria:
- Contract tests defined and reviewed
- No changes allowed without version bump

---

## 2) Design Freeze (Storage + Processing)

Deliverables:
- DB changes for markers, keyframes, audio tracks
- Operation history + rollback model
- Idempotency + concurrency (ETag / If‑Match)
- Overlap enforcement strategy

Exit criteria:
- Design review passed
- Migration plan approved

---

## 3) Implementation Phase A (Safety Foundation)

Implement in order:
1. API versioning: `/api/ai/v1`
2. Structured error responses
3. validate_only for all mutating endpoints
4. diff endpoint (pre‑apply)
5. overlap enforcement (disallow)
6. idempotency + ETag

Exit criteria:
- Contract tests 100% passing
- validate_only and diff verified

---

## 4) Implementation Phase B (Missing CRUD)

Implement in order:
1. Marker CRUD
2. Keyframe CRUD (video + audio volume)
3. Audio track creation
4. TextStyle + Shape strict schemas

Exit criteria:
- End‑to‑end AI edit scenario passes without manual fixes

---

## 5) Operational Safety (AI Workflow)

Deliverables:
- Plan → validate → apply → diff → rollback flow
- Audit history
- Batch atomic/best_effort selection

Exit criteria:
- AI edit success rate > 99%
- Manual fix rate < 5%

---

## 6) Release & Verification

Deliverables:
- Golden projects
- Regression suite
- Performance baseline (large projects)
- Updated docs synced to contract

Exit criteria:
- All tests green
- Docs match implementation

---

## Acceptance Criteria (Global)

- No hidden defaults
- All inputs validated
- All errors machine‑actionable
- All edits verifiable before apply
- All edits reversible after apply

