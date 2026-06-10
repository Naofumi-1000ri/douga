# Review & Branch Protection Operations Guide

This document covers the PR review workflow and branch protection settings for the Douga repository.

---

## Table of Contents

1. [Branch Protection](#branch-protection)
2. [Pre-PR Gate](#pre-pr-gate)
3. [Review Workflow](#review-workflow)

---

## Branch Protection

### Current Settings (set 2026-06-10, issue #302)

The `main` branch has the following protection rules configured:

| Setting | Value | Rationale |
|---|---|---|
| `required_status_checks.strict` | `false` | squash マージ運用と整合。マージ前に main を rebase する強制はしない |
| `required_status_checks.contexts` | See below | CI が green のときだけマージ可能 |
| `required_pull_request_reviews` | `null` (無効) | 独立レビューエージェント + オーケストレーター承認運用のため、GitHub の approve 必須化は単独運用を止める |
| `enforce_admins` | `false` | 管理者も同ルールに従う（任意） |
| `allow_force_pushes` | `false` | force-push 禁止 |
| `allow_deletions` | `false` | ブランチ削除禁止 |

### Required Status Checks

以下の 3 つのチェックがすべて green でないと main へのマージ不可:

- `Backend Checks`
- `Frontend Checks`
- `Backend DB Checks`

これらは GitHub Actions ワークフロー名（`.github/workflows/` 内の `name:` フィールド）に対応している。

### 設定の変更方法

管理者権限が必要。`gh` CLI を使って変更する:

```bash
gh api -X PUT repos/Naofumi-1000ri/douga/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["Backend Checks", "Frontend Checks", "Backend DB Checks"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

設定確認:

```bash
gh api repos/Naofumi-1000ri/douga/branches/main/protection \
  --jq '{required_status_checks: .required_status_checks.contexts, allow_force_pushes: .allow_force_pushes.enabled, allow_deletions: .allow_deletions.enabled}'
```

### ワークフロー名が変わった場合

CI ワークフローの `name:` フィールドを変更したときは、上記コマンドで `contexts` 配列を更新すること。
変更前に `gh api repos/Naofumi-1000ri/douga/commits/<SHA>/check-runs --jq '.check_runs[].name'` で新しいチェック名を確認する。

---

## Pre-PR Gate

PR を作成する前に、以下のゲートをローカルで通過していること:

1. `ruff format --check src` — フォーマットチェック
2. `ruff check src` — lint チェック
3. render parity: `ENVIRONMENT=test DEV_MODE=true` で無マーカー 4 ファイルのレンダーを確認

詳細は [MEMORY.md の Pre-PR gate CI parity](../../.claude/MEMORY.md) を参照。

---

## Review Workflow

1. 実装エージェントが pre-PR ゲートを通過後、PR を作成する
2. 独立したレビューエージェントがコードレビューを実施（`/review` または `code-review` スキル）
3. オーケストレーターが承認してマージを委任する
4. マージエージェントが `gh pr checks --watch` で CI green を確認後にマージ実行
5. マージ後、`/Users/hgs/devel/douga_root/main` を `origin/main` に同期

> **注意**: メインエージェントは直接 `gh pr merge` を実行しない。常に別エージェントに委任すること。
> ([workflow rules: Merge/Deploy delegation](../../.claude/projects/-Users-hgs-devel-douga-root/memory/feedback_merge_deploy_delegation.md))
