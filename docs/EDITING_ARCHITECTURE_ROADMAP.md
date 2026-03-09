# Douga Editing Architecture Roadmap

Last updated: 2026-03-10

This memo reframes Douga as an AI-assisted editing platform rather than only a single editor UI.
It combines the current codebase shape, the existing AI-friendly docs, and the operational lessons from recent issue work.

---

## 1. Goal

Define a realistic path from the current product to an editing foundation that can support:

- human-first editing
- AI-assisted editing
- machine-checkable validation
- reproducible preview/export quality
- safe rollout and rollback

This document is intentionally practical. It does not describe an ideal greenfield rewrite. It describes what Douga should stabilize next.

---

## 2. Current Douga in One View

Today, Douga already has the core pieces of an editing platform:

- frontend editor with timeline, preview, property panel, and optimistic sequence editing
- backend asset APIs, preview helpers, validation service, operation history, and render pipeline
- AI-facing API surface with read models, semantic operations, validate-only, diff, and rollback primitives

Current strengths:

- deterministic timeline units and center-origin transform model are mostly established
- preview-side editing is fast enough for human iteration
- operation history and rollback exist for part of the mutation surface
- render/export is already centralized in the backend

Current structural weakness:

- the "editing foundation" is still split across raw timeline mutation, frontend-local workflow logic, backend mutation services, and render-specific assumptions
- preview truth, metadata truth, and export truth are not yet guaranteed to stay aligned
- AI can act on the system, but the safe plan/apply/check loop is not yet the dominant editing path

---

## 3. Editing Workflow Breakdown

### 3.1 General video editing workflow

Most video editing pipelines can be decomposed into these stages:

| Stage | Human-led purpose | AI-assisted purpose |
| --- | --- | --- |
| Ingest | import assets, label, sort, identify source quality | classify assets, extract metadata, detect structure |
| Structuring | define sections, story arc, beats, markers | propose outline, rough sequencing, timing anchors |
| Assembly | place clips, trim, align timing | draft timeline mutations under constraints |
| Audio | narration sync, leveling, ducking, waveform inspection | detect silence, normalize, suggest mixes |
| Visual | crop, fit/fill, chroma, overlays, transitions | propose transforms and safe defaults |
| Annotation | telop, shapes, highlights, arrows | generate callouts and review points |
| Review | compare expectation vs output | run automated checks, surface diffs and warnings |
| Export | render, inspect artifacts, ship | compile, sample, validate, re-run if needed |

### 3.2 Current Douga flow

Current Douga roughly behaves like this:

1. assets are uploaded and classified
2. a sequence is loaded into the frontend store
3. the editor mutates timeline data optimistically
4. mutations are persisted through project/sequence save APIs
5. preview is assembled from frontend state plus preview helpers like waveform/thumbnail generation
6. backend render compiles timeline data into FFmpeg jobs
7. output is reviewed manually or via targeted checks

### 3.3 Where responsibility changes under AI

Human editing:

- humans hold editorial intent
- the system mostly applies and visualizes
- errors are tolerable if locally reversible

AI-assisted editing:

- the system must hold more explicit constraints
- planning and execution must be separated
- validation must happen before and after mutation
- rollback must be cheap and mechanically obvious

That means Douga needs stronger boundaries than a traditional timeline editor.

---

## 4. Current Architecture Map

The current system can already be read as these components:

| Component | Current location | Current responsibility |
| --- | --- | --- |
| Asset catalog | `backend/src/api/assets.py`, `backend/src/services/storage_service.py`, `backend/src/services/audio_extractor.py` | asset ingest, storage, metadata, extracted audio |
| Sequence/timeline state | `frontend/src/store/projectStore.ts`, `backend/src/api/sequences.py`, `backend/src/api/projects.py` | active timeline, optimistic local updates, persistence |
| Operation ledger | `backend/src/services/operation_service.py`, `backend/src/api/operations.py` | operation history, diff storage, rollback for supported ops |
| Validation gate | `backend/src/services/validation_service.py`, `backend/src/services/semantic_check_service.py` | validate-only checks and semantic safety rules |
| Preview projection | `frontend/src/components/editor/*`, `backend/src/services/preview_service.py` | timeline preview, waveform, thumbnails, preview clips |
| Render compiler | `backend/src/render/pipeline.py`, `backend/src/render/layer_compositor.py`, `backend/src/render/audio_mixer.py` | compile timeline into export output |
| AI surface | `backend/src/api/ai_v1.py`, `docs/AI_FRIENDLY_SPEC.md`, `docs/API_REFERENCE.md` | AI-oriented read/write surface and target contract |

This is already a strong starting point. The problem is not missing pieces. The problem is unstable contracts between pieces.

---

## 5. Why AI-Driven Quality Breaks Down

The quality failures seen so far can be grouped into five stages.

### 5.1 Input failures

Typical examples:

- asset metadata does not match the derived artifact
- waveform duration and asset duration drift
- extracted audio and source video are treated as if they share identical timing
- missing invariants for resolution/fps/time base allow hidden assumptions

Result:

- AI plans against wrong source facts
- preview and export can both be "correct" relative to different truths

### 5.2 Planning failures

Typical examples:

- planning and direct execution are mixed in one request path
- AI is allowed to think in free-form instructions without enough hard constraints
- capability boundaries are not explicit enough

Result:

- plans look plausible but are not mechanically safe
- local optimizations accumulate into globally poor edits

### 5.3 Apply failures

Typical examples:

- raw timeline mutation is possible without a single canonical operation model
- batch semantics are best-effort rather than clearly atomic
- frontend optimistic updates and backend persisted state can drift during sequence switching

Result:

- partial success creates hard-to-explain states
- rollback is possible only for some mutations

### 5.4 Verify failures

Typical examples:

- preview and export use different execution paths
- post-apply validation is not mandatory for all changes
- quality checks exist, but not as a universal gate between apply and export

Result:

- a change can "look good" in-editor and still export incorrectly
- regressions are discovered issue-by-issue instead of at a shared control point

### 5.5 Output failures

Typical examples:

- render-specific assumptions differ from editor assumptions
- artifacts are not always sampled and compared back to the plan intent
- rollout happens before a stable audit package is assembled

Result:

- the final MP4 becomes the first reliable truth, which is too late

---

## 6. Failure Taxonomy Table

| Stage | Primary risk | Current symptom family | What the platform should guarantee |
| --- | --- | --- | --- |
| Input | wrong source facts | metadata drift, waveform drift, missing asset constraints | one canonical fact model per asset/sequence |
| Plan | under-constrained intent | AI overreaches, unsafe free-form edits | plans are explicit, typed, and checkable |
| Apply | unsafe mutation path | partial updates, cross-sequence write risk | all edits flow through a canonical operation layer |
| Verify | mismatched truths | preview/export divergence, hidden regressions | post-apply checks are mandatory and structured |
| Output | late discovery | export-only bugs, audit gaps | export uses the same validated intermediate model |

---

## 7. Target Architecture for Douga

The editing foundation should be organized around seven explicit layers.

### 7.1 Canonical content layer

Objects:

- asset
- derived asset
- sequence
- timeline
- clip
- audio clip
- marker

Rule:

- each object has one canonical schema and one canonical owner
- derived facts such as duration, dimensions, waveform duration, and clip bounds must have a clearly named source of truth

### 7.2 Plan layer

Responsibility:

- convert editorial intent into an explicit edit plan
- contain no direct persistence side effects

Rule:

- plans are typed, diffable, and versioned
- AI may generate or revise plans, but may not skip the plan layer

### 7.3 Apply layer

Responsibility:

- transform a validated plan into concrete operations
- enforce idempotency, sequencing, and rollback metadata

Rule:

- raw timeline writes are an implementation detail, not the public editing surface
- operation semantics must be explicit enough to replay or revert

### 7.4 Validation layer

Responsibility:

- pre-apply validation
- post-apply validation
- export-readiness validation

Checks should include:

- structure validity
- overlap policy
- asset availability
- timeline/preview/export invariant checks
- known quality checks such as readability, safe zones, missing previews, and duration drift

### 7.5 Projection layer

Responsibility:

- generate editor preview state
- generate machine review state
- expose L1/L2/L3 summaries and diffs

Rule:

- preview is not allowed to invent facts that export cannot reproduce

### 7.6 Render layer

Responsibility:

- compile the validated timeline into a deterministic render package

Rule:

- export should consume the same normalized intermediate representation that preview verification consumed

### 7.7 Audit layer

Responsibility:

- record what changed, why it changed, what checks ran, and how to roll it back

Rule:

- every AI-driven change should be reproducible from plan + capabilities + validation results + operation history

---

## 8. What AI Should and Should Not Touch

AI should be allowed to touch:

- capabilities
- L1/L2/L3 read models
- typed plan requests
- validate-only endpoints
- apply endpoints that return diff and rollback metadata
- review/check endpoints

AI should not directly touch:

- arbitrary raw timeline JSON patches
- storage-layer metadata mutation
- render-layer internals
- hidden editor-only state
- ad hoc recovery paths that bypass operation logging

The platform should treat "AI is powerful" as a reason to narrow the write surface, not widen it.

---

## 9. Human-in-the-Loop Checkpoints

Not every stage needs a human. But these do:

1. before applying large structural plan changes
2. when validation returns warnings instead of hard errors
3. before export on user-facing or irreversible outputs
4. when post-checks disagree with the plan intent

Human review should happen against structured artifacts:

- proposed diff
- validation summary
- preview samples
- export risk summary

not against an opaque AI transcript.

---

## 10. Recommended Roadmap

### Phase 1: Make the current system observable

Goal:

- stop arguing about what the current truth is

Priority work:

- add a full timeline read model under the AI-facing surface
- define canonical asset metadata ownership, including derived media facts
- formalize preview/export invariants and list known drifts
- expose stronger machine-readable validation results and error codes

Success condition:

- all important editing facts can be read, diffed, and validated without inspecting frontend-only state

### Phase 2: Stabilize the operation model

Goal:

- make timeline mutation safe and replayable

Priority work:

- route more edits through typed operation semantics instead of raw state replacement
- expand rollback coverage
- add clear `atomic` vs `best_effort` batch semantics
- reduce direct full-timeline overwrite paths where possible

Success condition:

- the default path for mutation is operation-first, not timeline-patch-first

### Phase 3: Standardize plan/apply/check

Goal:

- make AI editing a constrained loop, not an open-ended mutation surface

Priority work:

- add a formal edit plan schema
- separate plan generation from plan validation and application
- require post-apply checks for AI-driven edits
- package diff + validation + rollback metadata as one audit bundle

Success condition:

- every AI edit can be explained as `read -> plan -> validate -> apply -> check -> rollback if needed`

### Phase 4: Unify preview and export truth

Goal:

- stop discovering correctness only at export time

Priority work:

- normalize intermediate render inputs
- compare preview assumptions against export assumptions
- promote recurring issue classes into shared invariant tests
- increase preview sampling and export-readiness checks

Success condition:

- preview and export differ only in fidelity, not in logic

### Phase 5: Raise the abstraction level

Goal:

- enable higher-level autonomous editing safely

Priority work:

- semantic editing plans that operate on sections, beats, and intent
- reusable review policies
- human approval rules by risk level
- AI-facing workflows for assembly, annotation, and review generation

Success condition:

- high-level editing can be automated without bypassing the lower safety layers

---

## 11. Concrete Near-Term Priorities

If we only do a few things next, these should be first:

1. canonicalize asset-derived facts such as duration and waveform timing
2. add or finish the full timeline and stronger validation/error read models
3. reduce direct timeline replacement and widen typed operation coverage
4. define the formal plan/apply/check contract for AI-driven edits
5. turn recurring preview/export bugs into shared invariants and contract tests

These five items reduce the most future pain per unit of work.

---

## 12. Rollout and Rollback Strategy

This architecture should not be introduced as one rewrite.

Recommended rollout:

1. add read-only observability first
2. add new validation and operation surfaces in parallel with existing flows
3. migrate specific edit classes one by one
4. gate AI-driven workflows on the new path before human-only flows
5. only retire older mutation paths after auditability and rollback are proven

Recommended rollback posture:

- operation layer changes must be revertible without reverting render changes
- validation policy changes must be feature-flagged or soft-gated first
- render compiler changes must be deployable independently from editor UI changes
- plan-layer rollout must never become the sole path before the old path can still recover user work

---

## 13. What “Done” Looks Like

Douga can be considered an AI-ready editing foundation when:

- canonical facts do not drift across asset, preview, and export
- plans are explicit and validated before apply
- operations are diffable, idempotent, and rollbackable
- preview and export consume the same normalized editing truth
- human review is inserted at risk boundaries, not everywhere
- the system can explain why a result happened, not just that it happened

Until then, the right strategy is not "more AI freedom". It is "more explicit contracts around the editing core".
