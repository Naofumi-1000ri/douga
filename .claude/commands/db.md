# DB接続

Cloud SQL Auth Proxy経由でCloud SQL (PostgreSQL) に接続し、クエリを実行するスキルです。

## 引数

`$ARGUMENTS` でSQLクエリまたは操作を指定:
- SQLクエリ文字列 → そのまま実行
- `connect` → インタラクティブ接続の手順を表示
- 引数なし → テーブル一覧を表示

## 接続情報

| 項目 | 値 |
|------|-----|
| Instance | douga-2f6f8:asia-northeast1:douga-db |
| Tier | db-f1-micro (max_connections=25) |
| Database | douga |
| User | postgres |
| Proxy Port | 15432 |

## 実行手順

### 1. Cloud SQL Auth Proxyの起動

まずProxyが動いているか確認:

```bash
lsof -i :15432 2>/dev/null | grep LISTEN
```

動いていなければ起動（バックグラウンド）:

```bash
cloud-sql-proxy douga-2f6f8:asia-northeast1:douga-db --port=15432 &
sleep 2
```

### 2. パスワード取得

Cloud Runの環境変数からDBパスワードを取得:

```bash
DB_PASS=$(gcloud run services describe douga-api \
  --region=asia-northeast1 --project=douga-2f6f8 --format=json \
  | python3 -c "
import sys, json, re
envs = json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('env', [])
for e in envs:
    if e['name'] == 'DATABASE_URL':
        match = re.search(r'://[^:]+:([^@]+)@', e['value'])
        if match: print(match.group(1), end='')
")
```

### 3. クエリ実行

```bash
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -p 15432 -U postgres -d douga -c "$QUERY"
```

### 4. Proxyの停止（クエリ完了後）

```bash
kill $(lsof -t -i :15432) 2>/dev/null
```

## 実行フロー

1. Proxyが起動していなければ起動する
2. パスワードを取得する
3. `$ARGUMENTS`の内容に応じてクエリを実行する
4. 結果をユーザーに表示する
5. Proxyを停止する

## 注意事項

- **READ ONLYを推奨**: SELECT文の実行を基本とする。UPDATE/DELETE/DROPは必ずユーザーに確認してから実行
- **パスワードをログに残さない**: echoやprintで表示しない
- **接続数に注意**: db-f1-microはmax_connections=25。長時間接続しない
- **gcloud認証が必要**: `gcloud auth print-access-token` が通ること

## よく使うクエリ例

```sql
-- テーブル一覧
SELECT tablename FROM pg_tables WHERE schemaname = 'public';

-- プロジェクトのアセット確認
SELECT name, type, duration_ms, width, height FROM assets WHERE project_id = '<project_id>' ORDER BY created_at;

-- プロジェクト一覧
SELECT id, name, created_at FROM projects ORDER BY created_at DESC LIMIT 10;

-- アセット統計
SELECT type, count(*), count(duration_ms) as with_duration FROM assets GROUP BY type;
```

$ARGUMENTS
