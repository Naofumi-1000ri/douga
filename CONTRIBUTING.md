# Contributing to atsurae

atsurae へのコントリビューションを歓迎します。

## How to Contribute

1. このリポジトリを **Fork** する
2. フィーチャーブランチを作成する: `git checkout -b feature/my-feature`
3. 変更をコミットする: `git commit -m "Add my feature"`
4. ブランチを Push する: `git push origin feature/my-feature`
5. **Pull Request** を作成する

## Development Setup

### Frontend

```bash
cd frontend
npm install
npm run dev   # 開発サーバー起動（:5173）
```

API は Cloud Run 上のバックエンドを使用します。ローカルでバックエンドを起動する必要はありません。

### Backend

バックエンドは Cloud Run にデプロイされています。コード修正後のデプロイには `/deploy` スキルを使用してください。
production deploy は `backend/scripts/deploy_prod.sh` を基準にし、`douga-api` の Cloud Run `maxScale=4` を維持します。意図的に変更する場合だけ `MAX_SCALE` を明示的に上書きしてください。

```bash
cd backend
uv pip install -e ".[dev]"
pytest                # テスト実行
ruff check src/       # Lint
ruff format src/      # Format
```

## Code Style

- **Frontend**: ESLint + TypeScript strict mode
- **Backend**: Ruff (lint + format), mypy

PR 作成前に以下を確認してください:

```bash
# Frontend
cd frontend && npm run lint && npm run build

# Backend
cd backend && ruff check src/ && ruff format --check src/ && pytest
```

## Language

Issue や PR の説明は日本語でも英語でも OK です。

## License

コントリビューションは [MIT License](./LICENSE) のもとで提供されます。
