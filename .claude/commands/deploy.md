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

#### 1-1. Dockerイメージのビルド（AMD64必須）

```bash
cd /Users/hgs/devel/douga_root/main/backend
docker build --platform linux/amd64 -t douga-api-deploy .
```

#### 1-2. タグ付け・プッシュ

```bash
docker tag douga-api-deploy asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest
docker push asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest
```

#### 1-3. Cloud Runデプロイ

**重要: `gcloud run services update --image=` を使う。`gcloud run deploy` は使わない（env varがロールバックされる）。**

```bash
gcloud run services update douga-api \
  --region=asia-northeast1 \
  --project=douga-2f6f8 \
  --image="asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest"
```

#### 1-4. env var確認

デプロイ後、重要なenv varが保持されているか必ず確認:

```bash
gcloud run services describe douga-api \
  --region=asia-northeast1 --project=douga-2f6f8 \
  --format=json | python3 -c "
import sys, json
envs = json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('env', [])
critical = ['OPENAI_API_KEY', 'DATABASE_URL', 'CORS_ORIGINS', 'GEMINI_API_KEY', 'FIREBASE_PROJECT_ID']
for e in envs:
    if e['name'] in critical:
        v = e.get('value', '')
        print(f'  {e[\"name\"]}: {\"OK\" if v else \"EMPTY!\"}')"
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

#### 1-5. ヘルスチェック

```bash
curl -s https://douga-api-344056413972.asia-northeast1.run.app/health
```

### 2. Frontend デプロイ

```bash
cd /Users/hgs/devel/douga_root/main/frontend
npm run build && npm run deploy
```

デプロイ先: https://douga-2f6f8.web.app

## GCP設定

| 項目 | 値 |
|------|-----|
| Project ID | douga-2f6f8 |
| Region | asia-northeast1 |
| Service Name | douga-api |
| Image Registry | asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy |
| Service URL | https://douga-api-344056413972.asia-northeast1.run.app |
| Firebase Hosting | https://douga-2f6f8.web.app |

## トラブルシューティング

### env varがロールバックされた
→ `gcloud run deploy` を使った可能性。`gcloud run services update --image=` に切り替える。
→ `.env` ファイルから復元（上記 1-4 参照）

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
