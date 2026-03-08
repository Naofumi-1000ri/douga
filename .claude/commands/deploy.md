# Deploy

backend/frontendをデプロイするスキルです。

## 引数

`$ARGUMENTS` で対象を指定:
- `backend` or `back` — backendのみ
- `frontend` or `front` — frontendのみ
- `all` or 引数なし — 両方

## 実行手順

### 0. gcloud認証チェック

```bash
gcloud auth print-access-token 2>&1 | head -1
```

認証エラーの場合 → ユーザーに通知して停止:
```
gcloud認証が無効です。以下で再認証してください:
  gcloud auth login
  gcloud auth application-default login
```

### 1. Backend デプロイ

#### 1-1. SHA付きイメージをビルド・push・Cloud Run更新

`backend/scripts/deploy_prod.sh` は current `main` の commit SHA を自動で解決し、`GIT_HASH` build arg と image tag の両方に使う。
deploy 後は `/health` を叩いて `status=healthy` と `git_hash=<deployed sha>` を自動検証する。

```bash
cd /Users/hgs/devel/douga_root/main/backend
./scripts/deploy_prod.sh
```

実行内容:

1. `git rev-parse HEAD` で deploy 対象 SHA を解決
2. `docker build --platform linux/amd64 --build-arg GIT_HASH=<sha>` で backend image を build
3. `asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:<sha>` に push
4. `gcloud run services update --image=<sha-tagged-image> --update-env-vars GIT_HASH=<sha>` で Cloud Run 更新

**⚠️ 絶対に `gcloud run deploy` を使わない。env varがロールバックされる。必ず `gcloud run services update --image=` を使う。**

#### 1-2. env var確認

デプロイ後、重要なenv varが保持されているか必ず確認:

```bash
gcloud run services describe douga-api \
  --region=asia-northeast1 --project=douga-2f6f8 \
  --format=json | python3 -c "
import sys, json
envs = json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('env', [])
critical = ['OPENAI_API_KEY', 'DATABASE_URL', 'CORS_ORIGINS', 'GEMINI_API_KEY', 'FIREBASE_PROJECT_ID']
for e in envs:
    name = e['name']
    if name in critical:
        v = e.get('value', '')
        status = 'OK' if v else 'EMPTY!'
        print(f'  {name}: {status}')"
```

もし空のenv varがあれば `.env` から復元:

```bash
# backend/.env から値を読んで設定
KEY_NAME=OPENAI_API_KEY
VAL=$(grep "^${KEY_NAME}=" /Users/hgs/devel/douga_root/main/backend/.env | cut -d= -f2-)
gcloud run services update douga-api \
  --region=asia-northeast1 --project=douga-2f6f8 \
  --update-env-vars="${KEY_NAME}=${VAL}"
```

#### 1-3. ヘルスチェック

```bash
curl -s https://douga-api-344056413972.asia-northeast1.run.app/health
```

期待値:
- `status` は `healthy`
- `git_hash` は deploy 対象 commit SHA と一致

補足:
- Cloud Run の service-level env `GIT_HASH` は image 内 `ENV GIT_HASH` を上書きする
- そのため image tag/build arg だけでなく、service env も同じ SHA に更新する

### 2. Frontend デプロイ

**`npm run deploy` は自動でビルド + API key検証を行う（firebase.json predeploy hook）。**

```bash
cd /Users/hgs/devel/douga_root/main/frontend
npm run deploy
```

デプロイ先: https://douga-2f6f8.web.app

#### 2-1. デプロイ後のログイン確認

デプロイ後、必ず本番サイトでログインできることを確認:

```bash
curl -s -o /dev/null -w "%{http_code}" https://douga-2f6f8.web.app/
```

## GCP設定

| 項目 | 値 |
|------|-----|
| Project ID | douga-2f6f8 |
| Region | asia-northeast1 |
| Service Name | douga-api |
| Image Registry | asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy |
| Service URL | https://douga-api-344056413972.asia-northeast1.run.app |
| Firebase Hosting | https://douga-2f6f8.web.app |

## 禁止事項

| 禁止 | 理由 | 正しい方法 |
|------|------|-----------|
| `gcloud run deploy` | env varがリセットされる | `gcloud run services update --image=` |
| `firebase deploy` 直接実行 | ビルド・検証がスキップされる | `npm run deploy`（build + verify 付き） |
| 古い `dist/` をそのままデプロイ | env varが焼き込み済みで古い可能性 | 必ずビルドしてからデプロイ |

## トラブルシューティング

### ログインできない（auth/api-key-expired）
→ フロントエンドのビルドが古い。`npm run build && npm run deploy` で解決。
→ `scripts/verify-build.js` がキー不一致を検出してブロックするはず。

### env varがロールバックされた
→ `gcloud run deploy` を使った可能性。`gcloud run services update --image=` に切り替える。
→ `.env` ファイルから復元（上記 1-2 参照）

### exec format error
→ `--platform linux/amd64` でビルドし直す

### Container failed to start
→ ログ確認:
```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="douga-api"' \
  --project=douga-2f6f8 --limit=20 --freshness=10m --format="table(timestamp,textPayload)"
```

### Push failed
→ Docker認証:
```bash
gcloud auth configure-docker asia-northeast1-docker.pkg.dev
```

$ARGUMENTS
