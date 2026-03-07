# Project Rules

Use `main` as the canonical integration branch.

Known stable baseline:

- `baseline-20260308-main-stable`

## Issue-First Workflow

For any new feature, bugfix, refactor, or investigation:

1. Start from a GitHub Issue.
2. Do not begin implementation for a new topic without an issue number, unless this is an explicitly approved emergency hotfix.
3. If no issue exists yet, stop and ask for one or confirm the emergency exception.

Each issue should define:

- background
- expected outcome
- acceptance criteria
- deploy/rollback concerns

## Branch Rules

- Branch from current `main`
- Use `codex/issue-<number>-<short-name>` for issue work
- Use a clearly named `codex/` branch for emergency exceptions

## Deploy Rules

For frontend-affecting changes:

1. Run local verification
   - `npm run lint`
   - `npx tsc -p tsconfig.json --noEmit`
   - `npm run build`
   - relevant Playwright tests
2. Deploy a preview/staging candidate first when practical
3. Deploy production only from the verified candidate
4. Merge the verified candidate into `main`
5. Re-deploy from `main` so production matches `origin/main`

Do not leave production on a branch state that is not in `main`.

## Rollback / Audit Rules

- Treat `main` as the source of truth
- Before starting new work, check whether any older branch still has unmerged commits
- For unmerged commits, explicitly decide whether they are:
  - superseded
  - still needed
  - safe to discard

## Current Audit Note

At the baseline above:

- Prior work was mostly represented in `main`
- `fix-play-abort` still had one unmerged frontend commit and should be treated as a follow-up issue if still needed
