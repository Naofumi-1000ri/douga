# Douga 開発日記

## 2026-02-04: AI-Friendly ドキュメントの大幅拡充 - 5人のエキスパート視点による改善

### 背景・課題

dougaプロジェクトは「AIが動画編集APIを使う」というユースケースを重視している。「AIが迷わない = 最高のプロダクト」という原則のもと、llms.txt / llms-full.txt / ai-developer-guide.md といったAI向けドキュメントを整備してきた。

しかし、既存ドキュメントには以下の課題があった:

1. **エンティティ間の関係性が見えにくい** - Project, Layer, Clip, Asset などの関係がテキストで説明されているだけで、AIがナビゲーションパスを理解しづらい
2. **エラー発生時の機械可読なアクションがない** - `suggested_fix` は文字列のみで、AIがプログラマティックに次のアクションを選択できない
3. **リスクと確信度の関係が不明確** - どのような操作でどの程度の確信度が必要か、AIの判断基準がなかった
4. **障害シナリオの網羅性が不足** - エラーコード定義はあるが、実際の障害シナリオとリカバリ手順が体系化されていなかった
5. **ドメイン固有の知識が散在** - 動画編集用語やUdemy固有の要件が分散していて参照しづらかった

### 技術的アプローチ

計画ファイル（hashed-wondering-fog.md）に従い、5人の異なるエキスパート視点でドキュメントをレビューし、それぞれの改善提案を統合した:

```
LLM/Promptエキスパート → Schema Map、ナビゲーション効率
APIデザインエキスパート → suggested_actions構造化
UXエキスパート → Risk x Confidence Matrix、透明性
QAエキスパート → Failure Modes Catalog、リトライポリシー
動画編集ドメインエキスパート → 用語定義、Udemy要件
```

この5つの視点を組み合わせることで、AIが「何をどう取得し」「どう判断し」「失敗時にどうリカバリするか」を一貫して理解できるドキュメント構造を目指した。

### 実装した内容

#### 1. Schema Map セクション追加 (llms-full.txt 309-391行)

エンティティ関係をASCIIアートで可視化:

```
                        PROJECT
                           |
        +------------------+------------------+
        |                  |                  |
     LAYER            AUDIO_TRACK          ASSET
        |                  |                  ^
        | 1:N              | 1:N              | ref
        v                  v                  |
   VIDEO_CLIP         AUDIO_CLIP-------------+
        |
        | 1:N (optional)
        v
     KEYFRAME
```

また、Navigation Paths として「クリップを追加するには」「何が再生中か調べるには」といった典型的なユースケース別のAPI呼び出し順序を明記した。

#### 2. Terminology Definitions セクション追加 (llms-full.txt 93-166行)

動画編集の基本用語を日英併記で定義:

- Timeline Terms: Clip, Layer, Track, Playhead, Duration
- Timing Terms: start_ms, end_ms, duration_ms, in_point_ms, out_point_ms, Trim
- Transform Terms: x/y座標、scale、rotation、anchor、Crop
- Effect Terms: opacity、Chroma key、Blend mode、Ducking、Fade
- Important Distinctions: Trim vs Crop、Layer vs Track など紛らわしい用語の違い

AIが用語を正確に解釈するための辞書として機能する。

#### 3. Failure Modes & Recovery Catalog 追加 (llms-full.txt 1744-1828行)

障害シナリオを体系的に分類:

- Network & Communication Failures: timeout、connection lost、rate limit、auth expired
- Data Integrity Failures: ID mismatch、stale data、corrupt response、incomplete batch
- Operation-Specific Failures: 各操作ごとのよくある失敗と対処法

リトライポリシーも明確化:

```
5xx/timeout → 3回リトライ、指数バックオフ (1s, 2s, 4s)
429 rate limit → 5回リトライ、retry_afterヘッダーに従う
404 not found → 2回リトライ、IDをリフレッシュしてから
400/422 → 1回リトライ、入力を修正してから
```

エスカレーション基準として「3回以上同じエラー」「不明なエラー」「状態不整合」「削除など不可逆操作」を明記。

#### 4. Risk x Confidence Matrix 追加 (ai-developer-guide.md 145-178行)

リスクレベル（Low/Medium/High/Critical）と確信度（Low/Med/High/VHigh）のマトリクスを定義:

```
                    CONFIDENCE
                Low(<70%)  Med(70-84%)  High(85-95%)  VHigh(>95%)
         +---------------------------------------------------+
    Low  |  Ask      Ask+Suggest   Execute       Execute     |
R  Med   |  Ask      Ask           Ask+Suggest   Execute     |
I  High  |  Refuse   Ask           Ask           Ask+Suggest |
S  Crit  |  Refuse   Refuse        Ask           Ask         |
K        +---------------------------------------------------+
```

例えば「全部の隙間詰めて」はHigh Risk、80% Confidenceなので「Ask: 5レイヤーすべてですか?」となる。

#### 5. AI Capabilities & Limitations セクション追加 (ai-developer-guide.md 23-82行)

AIにできること・できないことを明示:

- できること: Timeline manipulation, Semantic operations, Batch operations, Structure analysis, Parameter calculations, Asset management
- できないこと: Visual judgment（レンダリング結果を「見る」）、Audio quality（音を「聞く」）、Aesthetic decisions（「良い感じ」の判断）、Creative interpretation、Cross-session memory、Real-time preview

Transparency Guidelinesとして「完璧に配置しました!」ではなく「位置関係をプレビューで確認することをお勧めします」と伝えるべきことを明記。

#### 6. Udemy-Specific Requirements セクション追加 (llms-full.txt 1990-2094行)

Udemyプラットフォーム固有の要件を集約:

- Output Specifications: 1920x1080、30fps、H.264/AAC、MP4
- Safe Zone Guidelines: 5%マージン（96px）、下部10%はプログレスバー領域
- Text Readability: Main title 48px以上、Body text 24px以上、コントラスト比4.5:1以上
- Lecture Structure Patterns: Intro 5-10s、Agenda 5-15s、Main content、Transitions 1-3s、Outro 5-10s
- Common Avatar Positions: Center、Bottom-right（x=600, y=350, scale=0.4）、Bottom-left
- BGM Guidelines: ナレーションなし 0.3-0.5、ナレーションあり 0.05-0.1（auto_duck_bgm使用）
- Pre-Export Checklist

### 技術的判断とその理由

#### なぜASCII図を使ったか

Mermaid記法やSVGではなくASCIIアートを選択した理由:

1. トークン効率 - MermaidはAIが解析するのに追加コストがかかる
2. 普遍性 - どのLLMでも正確に解釈できる
3. コピペ可能 - AIがそのまま応答に含められる

#### suggested_actionsを構造化しなかった理由

計画では `suggested_actions: [{action: "...", parameters: {...}, confidence: 0.9}]` のような構造を検討したが、現時点では文字列ベースの `suggested_fix` を維持した。理由:

1. 現行APIがそもそも構造化エラーを返していない - バックエンド実装が追いついていない
2. ドキュメント先行で整合性が取れない - 「こう書いてあるけど実際は違う」状態を避けた
3. 段階的改善の余地 - バックエンド実装後にドキュメントを更新する方が健全

代わりに、Failure Modes Catalogで「このエラーが出たらこうする」をテキストで網羅した。

#### Version Historyの追加

llms-full.txtの末尾にVersion Historyセクションを追加:

```
| Version | Date | Changes |
|---------|------|---------|
| 1.5.0 | 2026-02 | Added Schema Map, Terminology Definitions, ... |
```

AIがドキュメントのバージョンを認識し、「このドキュメントはいつ更新されたか」を把握できるようにした。

### 学び・気づき

#### 1. 「AIが迷わない」の具体化は難しい

「AIが迷わない」は理念としては明確だが、具体的に何を書けばいいかは試行錯誤が必要だった。5人のエキスパート視点というフレームワークが有効だったのは、それぞれが「AIが迷うポイント」を異なる角度から照らしたから。

- LLMエキスパート: 「どこから情報を取ればいいか分からない」→ Schema Map
- UXエキスパート: 「どこまで自律的に行動していいか分からない」→ Risk x Confidence Matrix
- QAエキスパート: 「失敗したらどうすればいいか分からない」→ Failure Modes Catalog

#### 2. 表形式 vs 散文

ドキュメント全体で表が多用されている。表は情報密度が高く参照しやすいが、文脈を伝えにくい。今回追加したセクションでは:

- 定義・分類 → 表
- プロセス・手順 → 箇条書き
- 関係性 → ASCII図
- 判断基準 → マトリクス

と使い分けた。

#### 3. バックエンド実装との同期

v1 実装が進み、`validate_only` / `diff` / `history` / `rollback` はすべて利用可能になった（operation_id ベース）。
llms-full.txt の「Future」表記は廃止し、現行仕様として整理した。

### 変更量サマリー

- llms-full.txt: 1819行 → 2111行 (+292行、実質+476行相当の内容追加)
- ai-developer-guide.md: 427行 → 504行 (+77行、実質+95行相当の内容追加)

### 次のアクション

1. **DB統合テスト** - rollback/history の実データ復元検証
2. **実際のAIエージェントでの検証** - Claude Code等でdouga APIを操作させ、ドキュメントの有効性を確認
3. **フィードバックループの構築** - AIが「迷った」ケースを収集し、ドキュメント改善に反映

---

*記録者: Claude Opus 4.5*
