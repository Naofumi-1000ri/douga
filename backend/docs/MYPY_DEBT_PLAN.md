# Backend mypy debt plan

Baseline on March 8, 2026:

- Command: `ENVIRONMENT=test uv run --extra dev mypy src/ --ignore-missing-imports`
- Result on `main` (`207b55e`): `604 errors in 64 files`

The reduction strategy for Issue #2 is intentionally incremental. The goal is to
pay down existing type debt in reviewable slices without mixing that work with
CI gate changes.

Work buckets:

1. Model annotations and forward references
   - Fix missing `TYPE_CHECKING` imports and obvious annotation gaps in ORM models.
   - Scope is limited to type declarations and should not change runtime behavior.

2. Storage service typing
   - Define one shared interface shape for local and GCS storage implementations.
   - Remove ambiguous factory typing so API modules stop accumulating `attr-defined`
     noise from `StorageService`.

3. Nullability and union handling in schema/service logic
   - Target concentrated `union-attr` / `arg-type` clusters such as
     `schemas/clip_adapter.py` and `services/validation_service.py`.
   - Keep these separate because they touch runtime branching and need behavioral
     review.

4. Large `dict[Any, Any]` and untyped helper clusters
   - Tackle high-volume files like `services/ai_service.py`,
     `api/ai_v1.py`, and `services/timeline_analysis.py` in smaller follow-ups.
   - Prefer introducing local typed aliases or typed DTOs before broader refactors.

Operating rules:

- Each change set should be narrow enough to review independently.
- Re-measure the full backend mypy baseline after each slice.
- Do not block unrelated merges by coupling debt repayment with new CI policy.
- When a slice touches runtime logic, keep the diff focused and verify behavior
  separately from the typing cleanup itself.

Current CI policy on main:

- Required CI now runs `mypy src/ --ignore-missing-imports` again.
- Remaining legacy mypy clusters are explicitly suppressed in `pyproject.toml`
  using per-module overrides.
- Debt repayment should remove modules from that override list as slices land.
- New code should not add new override entries.
