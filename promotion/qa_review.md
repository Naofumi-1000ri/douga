# QA Review Report
Date: 2026-02-16

## Overall Score: 5.5/10

**総評: 各成果物の方向性は正しいが、ファイル間の仕様不整合、リンク切れ、OGP設定の不備など即公開には対応が必要な問題が多数存在する。特に無料プランの数値がファイルごとにバラバラであることは、ユーザーからの信頼を失う致命的な問題。**

---

## 1. robots.txt
- Score: 6/10

### 良い点
- 基本構造は正しい。`User-agent: *` で全体許可した上で、個別AIクローラーを明示的に許可する設計は理にかなっている
- Sitemapディレクティブが含まれている
- コメントが適切で可読性が高い

### 問題点
1. **`Claude-Web` は正式なUser-agent名ではない。** AnthropicのクローラーはUser-agent `ClaudeBot` を使用する。`Claude-Web` は非公式な名前であり、実際のクローラーにマッチしない可能性が高い
2. **`Anthropic` も一般的なUser-agent名ではない。** Anthropicが使用する正式なクローラー名は `ClaudeBot` と `anthropic-ai`
3. **欠落しているAIクローラー:**
   - `ClaudeBot` (Anthropic正式名)
   - `anthropic-ai` (Anthropicのもう1つのクローラー)
   - `Amazonbot` (Amazon)
   - `Meta-ExternalAgent` (Meta/Facebook)
   - `Applebot-Extended` (Apple Intelligence用)
   - `YouBot` (You.com)
4. **`Host` ディレクティブは非標準。** `Host:` はYandex独自仕様であり、Google/Bingは無視する。害はないが不要
5. **`User-agent: *` + `Allow: /` の後に個別指定する意味が薄い。** 既に全体許可済みなので、個別AIクローラーへの `Allow: /` は冗長。ただし明示性のために残すのは許容範囲

### 改善案
```
User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /
```
に修正。`Claude-Web` と `Anthropic` を削除し、正式名に置換。`Host` ディレクティブを削除。欠落クローラーを追加。

---

## 2. sitemap.xml
- Score: 5/10

### 良い点
- XMLフォーマットは正しい
- `lastmod` が当日日付
- 名前空間宣言が正確

### 問題点
1. **URLが1つしかない。** SPAであっても、主要なルート（`/login`, `/editor`, `/pricing` 等）はsitemapに含めるべき。クローラーがアプリ内ページを発見できない
2. **`changefreq` は廃止予定。** Googleは `changefreq` を無視すると公式に表明している（2023年以降）。害はないが無意味
3. **`lastmod` が手動更新前提。** ビルド時に自動設定する仕組みがないと、すぐに古くなる
4. **`/sitemap.xml` のみで画像/動画サイトマップがない。** SoftwareApplication + 動画編集サービスなら、`<image:image>` や `<video:video>` 拡張を検討すべき

### 改善案
- 主要ルート（LP、ログイン、料金ページ、ドキュメント等）のURLを追加
- `changefreq` を削除
- ビルドスクリプトで `lastmod` を自動設定する仕組みを導入

---

## 3. index.html (canonical URL, OGP, 静的HTML)
- Score: 6/10

### 良い点
- セマンティックHTML構造が適切（`header`, `nav`, `main`, `section`, `article`, `footer`）
- 静的HTML fallbackがあり、JSが動作しない環境でもクローラーがコンテンツを取得可能
- canonical URLが設定されている
- `lang="ja"` が正しく設定
- 構造化データ (JSON-LD) が含まれている
- 料金プラン、使い方、CTAまで静的に記述されておりSEO上良い

### 問題点
1. **[致命的] OGP画像が相対パス。** `content="/og-image.jpg"` は多くのプラットフォームで正しく解釈されない。絶対URLが必須:
   ```html
   <meta property="og:image" content="https://douga-2f6f8.web.app/og-image.jpg" />
   ```
   `twitter:image` も同様
2. **`og:url` が欠落。** OGP仕様で推奨される基本プロパティ
3. **`og:site_name` が欠落**
4. **`og:locale` が欠落** (`ja_JP` を設定すべき)
5. **`twitter:site` が欠落。** Twitterアカウントが設定されていないと、ツイート時のカード表示が不完全
6. **構造化データに `url` プロパティがない。** SoftwareApplicationスキーマでは推奨
7. **構造化データに `author` がない**
8. **`<meta name="robots">` タグがない。** robots.txtと合わせて `<meta name="robots" content="index, follow">` を明示するのがベストプラクティス
9. **favicon が汎用の `vite.svg`。** ブランドロゴに差し替えるべき
10. **料金プランの数値がプロモーション素材と不整合**（後述の「一貫性」セクション参照）

### 改善案
- OGP画像を絶対URLに修正（最優先）
- `og:url`, `og:site_name`, `og:locale`, `twitter:site` を追加
- 構造化データに `url`, `author`, `datePublished` を追加
- ファビコンをブランドロゴに変更

---

## 4. README.md
- Score: 5/10

### 良い点
- バイリンガル（英語+日本語）構成で国際的なアピールが可能
- アーキテクチャ図がASCIIアートで視覚的にわかりやすい
- バッジが適切に配置されている
- API設計の哲学が明確に説明されている
- 技術スタック表が整理されている

### 問題点
1. **[致命的] `LICENSE` ファイルが存在しない。** MITライセンスバッジを掲げているが、`LICENSE` ファイルがリポジトリに存在しない。法的に問題がある
2. **[致命的] `CONTRIBUTING.md` が存在しない。** PRs Welcomeバッジとコントリビューションガイドへのリンクがあるが、ファイルが存在しない
3. **[重大] git cloneのURLがプレースホルダー。** `github.com/your-org/atsurae.git` は実際のリポジトリURLではない（実際は `github.com/Naofumi-1000ri/douga.git`）。このまま公開すると動作しない
4. **無料プランの仕様不整合:**
   - README: 「AI編集 月10回」「ウォーターマーク付き」
   - Twitter/PH/HN: 「月5本レンダリング」
   - index.html: 「AI自動編集（月10回）」「全テンプレート利用可」（ウォーターマーク言及なし）
5. **「Build: passing」バッジがダミー。** CI/CDリンクが空で実際のビルドステータスを反映していない。虚偽表示に見える
6. **情報量が多すぎる。** READMEだけで227行。初見の開発者にとっては圧倒的。Quick Startを先頭に持ってきて、詳細はサブページに分離すべき
7. **バックエンドのセットアップ手順がない。** フロントエンドのみの手順で、バックエンドはCloud Run前提とあるが、コントリビューターがバックエンドの開発に参加する方法が不明
8. **スクリーンショット/デモGIFがない。** ライブデモリンクはあるが、READMEに視覚的なプレビューがないのはOSSとして弱い

### 改善案
- `LICENSE` ファイルを作成（最優先）
- `CONTRIBUTING.md` を作成
- git clone URLを実際のリポジトリURLに修正
- ダミーバッジを削除するか、実際のCI/CDに接続
- ヒーロー画像またはデモGIFを追加
- 構成を「Quick Start → What is → Features → ...」に並べ替え

---

## 5. twitter_threads.md
- Score: 6/10

### 良い点
- 3パターン（技術者/クリエイター/AI界隈）のターゲット分けが適切
- 各パターンの語調がターゲットに合っている
- 具体的な技術数値（9.2/10, 5層、1920x1080等）が説得力を持つ
- パターンBの冒頭「正直に言います」は共感を誘う良い書き出し
- 全ツイートが280文字制限内（1件の例外あり）

### 問題点
1. **[要修正] パターンC 3/6 がTwitter加重文字数で282文字。** 日本語はTwitter内部で1文字=2ウェイトとカウントされるため、実質的に280文字制限を超過している（2文字オーバー）
2. **無料プランの数値不整合:**
   - パターンA-7: 「月5本レンダリング」
   - パターンB-5: 「月5本レンダリング」
   - index.html: 「AI自動編集（月10回）」
   - README: 「AI編集 月10回」
   → どれが正しいのか不明。これは投稿後に指摘されると信頼を失う
3. **パターンA-1 の引用が検証不能。** 「Stripe・Twilio・OpenAIと対等以上」「業界標準にすべき」というAI専門家のコメントは、出典が示されていない。HN等で問われた時に答えられるか？
4. **全パターンの最終ツイートが似すぎている。** 3つとも「atsurae -- AIが、あつらえる。」「LP: ...」「フィードバック歓迎」で終わる。同一人物が3パターン全て投稿すると、テンプレ感が出る
5. **ハッシュタグが一切ない。** Twitter/Xでは `#AI動画編集` `#IndieHacker` `#FFmpeg` 等のハッシュタグがディスカバラビリティに重要
6. **画像/動画の指示がない。** Twitterスレッドで画像なしはエンゲージメント激減。各ツイートに添付すべきメディアの指示がない
7. **バズ要素が弱い。** 数字は入っているが、「Before/After比較」「衝撃的な結果」「常識を覆す主張」などの強いフック要素が不足

### 改善案
- パターンC 3/6 を2文字以上短縮
- 無料プランの数値を全ファイルで統一
- 各ツイートに推奨メディア（スクショ、GIF、動画）の指示を追加
- ハッシュタグ戦略を追加
- 最終ツイートのバリエーションを増やす
- 引用の出典を明記するか、表現を「内部評価で」に変更

---

## 6. product_hunt.md
- Score: 6.5/10

### 良い点
- Tagline が55文字で60文字制限内
- Description が246文字で260文字制限内
- First Comment が詳細で技術的背景が伝わる
- 「Udemy courses」という具体的なユースケースが訴求力を持つ
- Topics/Tags が適切（AI, Video Editing, Developer Tools, API, Open Source）

### 問題点
1. **Tagline が技術寄りすぎる。** 「AI-native」はPH一般ユーザーには響かない。「Describe your video, AI edits it」のようなシンプルさが必要
2. **「craft」の使い方がやや不自然。** 英語ネイティブ的に "we craft it" は工芸品のニュアンスが強い。video editingに対して "we build it" や "we produce it" がより自然
3. **First Commentが長すぎる。** PH上位の投稿のFirst Commentは通常150-200語程度。現在のものは200語以上あり、技術詳細が多すぎる。PHユーザーはAPI設計の詳細より「何ができるか」「なぜ使うか」を知りたい
4. **具体的なBefore/After数値がない。** 「3時間 → 15分」のような具体的な改善数値がないと訴求力が弱い
5. **スクリーンショット/GIF/動画の指示がない。** PHではビジュアルが極めて重要。ギャラリー画像の指示が必要
6. **Maker名が不明。** PHプロフィール情報がない
7. **無料プランの数値不整合**（前述と同じ）
8. **「Open Source」タグ付きだが、何がOSSで何がSaaSなのか不明確。** 現時点でOSS部分は公開されていない

### 改善案
- Tagline をよりシンプルに: "Turn text instructions into pro videos -- AI handles the editing"
- First Comment を短縮し、Before/After数値を追加
- ギャラリー画像・動画の準備リストを作成
- 「Open Source」タグは実際にOSS公開後に使用

---

## 7. hackernews.md
- Score: 7/10

### 良い点
- 技術的な深さがHNコミュニティに適している
- FFmpeg filter_complexの具体的な課題説明がエンジニアの興味を引く
- 「Show HN:」フォーマットが正しい
- API設計パターンの具体例（suggested_fix, validate_only, token_estimate等）が有用
- 自己宣伝臭が抑えられている（PHより良い）
- コードスニペットがないのはHNポストとしては適切（長くなりすぎない）

### 問題点
1. **[要修正] タイトルが94文字で80文字を大幅に超過。** HNのタイトルは80文字が上限。現在のタイトルではカットされる
   - 現在: `Show HN: Atsurae -- AI-native video editing via text instructions (FFmpeg, 5-layer compositing)` (94文字)
   - 修正案: `Show HN: Atsurae -- AI video editor with text instructions and FFmpeg` (67文字)
2. **「3 independent AI experts」の表現がHNで叩かれる可能性。** HNコミュニティは権威主義的な表現に懐疑的。「独立した3人の専門家」が誰なのか、どういう基準で評価したのかが不明だと、「自画自賛」と見なされるリスクがある
3. **「9.2/10 AI Friendliness」スコアの根拠が薄い。** 独自指標を前面に出すのはHNでは逆効果になりうる。メトリクスの定義を聞かれた時に答えられる準備が必要
4. **ソースコードへのリンクがない。** Show HNでは通常、GitHubリポジトリのリンクが期待される。Firebase Hostingの本番URLのみでは「ソースを見せない」と判断されるリスク
5. **無料プランの数値不整合**（前述と同じ）
6. **本文がやや長い。** HNの理想的なShow HNポストは10-15行程度。現在は20行以上あり、スクロールが必要

### 改善案
- タイトルを80文字以内に短縮（最優先）
- GitHubリポジトリリンクを追加
- 「9.2/10」スコアは本文後半に移動し、具体的な技術内容を先に
- 本文を10-15行に圧縮

---

## 総合評価

### 即投稿可能か: No（条件付き）

**以下を修正するまで投稿してはいけない:**

1. **[P0 - ブロッカー] 無料プランの仕様をファイル間で統一する**
   - 「月10回」なのか「月5本」なのか確定する
   - 「ウォーターマーク付き」は本当か
   - 「最大10分」の制限はあるのか
   - 7ファイル全てを同じ数値に統一すること

2. **[P0 - ブロッカー] HNタイトルを80文字以内にする**

3. **[P0 - ブロッカー] OGP画像を絶対URLにする**
   - Twitter投稿時にカード画像が表示されない

4. **[P0 - ブロッカー] LICENSEファイルを作成する**
   - MITバッジを掲げてLICENSEファイルがないのは法的に問題

5. **[P0 - ブロッカー] READMEのgit clone URLを修正する**
   - `your-org/atsurae.git` は動作しない

6. **[P1 - 重要] TwitterパターンC 3/6 を280文字以内にする**

7. **[P1 - 重要] robots.txt の `Claude-Web` を `ClaudeBot` に修正**

8. **[P1 - 重要] CONTRIBUTING.md を作成する**

### 最優先修正事項
1. 無料プランの仕様統一（全ファイル横断で最も深刻）
2. OGP絶対URL化
3. HNタイトル短縮
4. LICENSE / CONTRIBUTING.md 作成
5. README の clone URL 修正

### ブランド一貫性: 4/10

- 「AIが、あつらえる」のキャッチフレーズは全ファイルで一貫している（良い）
- しかし**無料プランの仕様がバラバラ**なのは致命的な一貫性欠如
- プロジェクト名が「douga」(リポジトリ名) vs 「atsurae」(ブランド名) で混在
- 英語と日本語のトーン&マナーは概ね統一されている

### 競合差別化度: 6/10

- **Descript**: 音声ベース編集。atsuraeはプログラマティック合成で差別化できているが、Descriptのテキスト編集体験との直接比較がどのファイルにもない
- **Runway**: AI生成動画。atsuraeは既存素材の「編集」であり、「生成」ではないという差別化ポイントが不明確
- **CapCut**: テンプレベース編集。atsuraeの5層レイヤー合成やAPI-first設計は差別化になるが、「なぜCapCutではダメなのか」を語るコンテンツがない
- **最大の差別化ポイント「AI-Friendly API」は技術者にしか刺さらない。** 一般ユーザー向けには「テキストだけで完結する」「Udemy講座に特化」をもっと前面に出すべき
- 競合製品との明示的な比較表がどこにもない

### ファイル間の数値不整合一覧

| 項目 | index.html | README | Twitter | PH | HN |
|------|-----------|--------|---------|----|----|
| AI編集回数/月 | 10回 | 10回 | 5本 | 5 renders | 5 renders |
| プロジェクト数 | 3件 | 3 | 3個 | - | 3 |
| ウォーターマーク | 言及なし | 付き | 言及なし | 言及なし | 言及なし |
| 最大動画長 | 言及なし | 言及なし | 10分 | 言及なし | 言及なし |
| テンプレート | 全利用可 | 言及なし | 言及なし | 言及なし | 言及なし |

**これは「各ファイルを独立して書いた」ことの典型的な失敗パターン。1つの正式な仕様書（pricing.md等）を作成し、全ファイルがそこを参照する構成にすべき。**
