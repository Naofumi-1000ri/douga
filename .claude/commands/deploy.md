# Deploy Backend to Cloud Run

バックエンドをCloud Runにデプロイするスキルです。

## 実行手順

### 1. gcloud認証チェック

まず、gcloud認証が有効か確認してください：

```bash
gcloud auth print-access-token 2>&1
```

**認証エラーの場合**（以下のようなメッセージが出た場合）:
- "ERROR: (gcloud.auth.print-access-token)"
- "Your current active account"
- "token has been expired or revoked"
- "Refresh token has expired"

→ **ユーザーに通知して停止**：
```
gcloud認証が無効です。以下のコマンドで再認証してください：

gcloud auth login
gcloud auth application-default login

再認証後、再度 /deploy を実行してください。
```

### 2. Dockerイメージのビルド

**重要**: Apple Silicon Mac等ARM環境からデプロイする場合、AMD64でビルドする必要があります。

```bash
cd /Users/hgs/devel/douga/backend
docker build --platform linux/amd64 -t douga-api-deploy .
```

### 3. イメージのタグ付けとプッシュ

```bash
docker tag douga-api-deploy asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest
docker push asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest
```

### 4. Cloud Runにデプロイ

```bash
gcloud run deploy douga-api \
  --image asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy/douga-api:latest \
  --region asia-northeast1 \
  --project douga-2f6f8 \
  --allow-unauthenticated
```

### 5. デプロイ確認

```bash
curl -s https://douga-api-344056413972.asia-northeast1.run.app/health
```

期待される応答: `{"status":"healthy","version":"0.1.0"}`

## GCP設定

| 項目 | 値 |
|------|-----|
| Project ID | douga-2f6f8 |
| Region | asia-northeast1 |
| Service Name | douga-api |
| Image Registry | asia-northeast1-docker.pkg.dev/douga-2f6f8/cloud-run-source-deploy |
| Service URL | https://douga-api-344056413972.asia-northeast1.run.app |

## トラブルシューティング

### exec format error
→ `--platform linux/amd64` でビルドし直す

### Container failed to start
→ Cloud Runのログを確認：
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=douga-api" --project douga-2f6f8 --limit 20 --format="value(textPayload)"
```

### Push failed
→ Docker認証を確認：
```bash
gcloud auth configure-docker asia-northeast1-docker.pkg.dev
```

$ARGUMENTS
