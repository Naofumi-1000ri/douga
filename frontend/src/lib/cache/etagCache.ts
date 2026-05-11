/**
 * localStorage ETag キャッシュユーティリティ
 *
 * キーの命名規則: `cache:v1:<resource>:<id>`
 * 例: `cache:v1:assets:<projectId>`
 *     `cache:v1:sequences:<projectId>`
 *     `cache:v1:sequence:<projectId>:<sequenceId>`
 */

/**
 * Cache schema version. Bumped to invalidate all existing entries whose semantics
 * are no longer compatible with the current reader.
 *
 * - v1: initial release (PR #210). 304 応答時に expiresAt をスライド延長していたため
 *   署名付き URL (60min TTL) が失効しても再フェッチされない欠陥があった。
 * - v2: PR #234/#237 で TTL スライドを廃止したため、v1 entry が残っていると
 *   (1) 旧 ttl の expiresAt をそのまま使い続け、(2) 旧 signed URL を 304 ループで
 *   持ち続けてしまう。v2 にバンプして v1 entry を即時破棄する。(#239)
 */
export const SCHEMA_VERSION = 2

/** キャッシュキーの接頭辞 (clearAllCache で削除対象を識別するために使用) */
const CACHE_KEY_PREFIX = 'cache:'

export type CacheEntry<T> = {
  schemaVersion: number
  etag: string
  payload: T
  /**
   * キャッシュに **書き込まれた** 時刻 (ms epoch)。
   * = 最後に 200 を受信した時刻。304 応答時には更新されない (#233/#235)。
   * したがって「最後にサーバーと通信した時刻」ではない点に注意。
   */
  fetchedAt: number
  /** エントリの有効期限 (ms epoch)。省略時は無期限。 */
  expiresAt?: number
}

/**
 * localStorage からキャッシュエントリを読み込む。
 * - スキーマバージョン不一致 → null を返して破棄
 * - TTL 切れ → null を返して破棄
 * - localStorage が使用不可（SSR等）→ null
 * - JSON パースエラー → null
 */
export function readCache<T>(key: string): CacheEntry<T> | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null

    const parsed = JSON.parse(raw) as unknown
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      !('schemaVersion' in parsed) ||
      (parsed as { schemaVersion: unknown }).schemaVersion !== SCHEMA_VERSION
    ) {
      // バージョン不一致または不正形式 → 破棄
      try {
        localStorage.removeItem(key)
      } catch {
        // ignore
      }
      return null
    }

    const entry = parsed as CacheEntry<T>

    // TTL チェック: expiresAt が設定されていて現在時刻を過ぎていれば破棄
    if (entry.expiresAt !== undefined && entry.expiresAt < Date.now()) {
      try {
        localStorage.removeItem(key)
      } catch {
        // ignore
      }
      return null
    }

    return entry
  } catch {
    return null
  }
}

/**
 * localStorage にキャッシュエントリを書き込む。
 * QuotaExceededError は握りつぶす。
 *
 * @param ttlMs - キャッシュの有効期間 (ms)。省略時は無期限。
 */
export function writeCache<T>(key: string, etag: string, payload: T, ttlMs?: number): void {
  const entry: CacheEntry<T> = {
    schemaVersion: SCHEMA_VERSION,
    etag,
    payload,
    fetchedAt: Date.now(),
    ...(ttlMs !== undefined ? { expiresAt: Date.now() + ttlMs } : {}),
  }
  try {
    localStorage.setItem(key, JSON.stringify(entry))
  } catch {
    // QuotaExceededError など — 無視して処理継続
  }
}

/**
 * localStorage からキャッシュエントリを削除する。
 * 書き込み API（POST/PATCH/DELETE）成功後に呼ぶ。
 */
export function clearCache(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    // ignore
  }
}

/**
 * `cache:` 接頭辞を持つ全てのキャッシュエントリを削除する。
 * ログアウト時は {@link clearAllUserData} を使うこと。
 */
export function clearAllCache(): void {
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith(CACHE_KEY_PREFIX)) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      localStorage.removeItem(key)
    }
  } catch {
    // ignore
  }
}

/**
 * ログアウト時にユーザー固有の全データを localStorage から削除する (D-2)。
 *
 * 削除対象:
 * - `cache:` 接頭辞 — ETag キャッシュ (ETag キャッシュ全般)
 * - `ai-chat-sessions-*` — AI チャットセッション一覧 (AIChatPanel)
 * - `ai-chat-messages-*` — AI チャットメッセージ (AIChatPanel)
 * - `ai-chat-current-session-*` — 最後に選択したセッション ID (AIChatPanel)
 *
 * 削除しないキー (ユーザー固有ではない設定):
 * - `editor-layout-settings` (editorLayoutSettings.ts)
 * - `asset-view-prefs` (AssetLibrary.tsx)
 * - `timeline-zoom` (Timeline.tsx)
 * - `timeline-default-image-duration-ms` (Editor.tsx)
 * - `i18nextLng` など国際化設定
 */
export function clearAllUserData(): void {
  // 1. ETag キャッシュを削除
  clearAllCache()

  // 2. AI チャット関連キーを削除
  try {
    const aiPrefixes = [
      'ai-chat-sessions-',
      'ai-chat-messages-',
      'ai-chat-current-session-',
    ]
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && aiPrefixes.some((prefix) => key.startsWith(prefix))) {
        keysToRemove.push(key)
      }
    }
    for (const key of keysToRemove) {
      localStorage.removeItem(key)
    }
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// fetchWithETag
// ---------------------------------------------------------------------------

/**
 * GCS 署名付き URL の有効期間 (60 分) より十分短い TTL (50 分)。
 * assets キャッシュに使用する。これにより、キャッシュから復元した storage_url が
 * 有効期限切れになる前に再フェッチが強制される。
 */
export const ASSETS_CACHE_TTL_MS = 50 * 60 * 1000 // 50 minutes

/**
 * sequences / sequence detail キャッシュの TTL。
 * GCS 署名付き URL を含まないため長めに設定。
 */
export const SEQUENCES_CACHE_TTL_MS = 24 * 60 * 60 * 1000 // 24 hours

export interface FetchWithETagOptions<T> {
  cacheKey: string
  /**
   * 実際の HTTP リクエストを発行する関数。
   * headers には If-None-Match が入ることがある。
   * 返却値の status が 304 の場合はキャッシュ利用。
   */
  fetcher: (headers: Record<string, string>) => Promise<{
    data: T
    etag: string | null
    status: number
  }>
  /**
   * キャッシュヒット時（楽観表示）に即座に呼ばれるコールバック。
   * ネットワークリクエストが完了する前に UI を更新するために使う。
   */
  onCacheHit?: (cached: T) => void
  /**
   * キャッシュの有効期間 (ms)。省略時は無期限。
   * GCS 署名付き URL を含むレスポンスには ASSETS_CACHE_TTL_MS を設定すること。
   */
  ttlMs?: number
}

/**
 * ETag を利用したキャッシュ付き fetch ヘルパー。
 *
 * 1. localStorage を読む → あれば onCacheHit(cached.payload) を即座にコール（楽観表示）
 * 2. If-None-Match ヘッダー付きでサーバーにリクエスト
 *    ただし TTL 切れの場合は If-None-Match を送らず非条件 GET でフレッシュなデータを取得
 * 3. 304 → キャッシュの payload を返す（expiresAt は維持し、TTL を延長しない）
 * 4. 200 → 新しい etag でキャッシュを更新して payload を返す
 *
 * ## TTL non-sliding design (#233, #235)
 *
 * 304 応答時に expiresAt を延長しない (TTL 非スライド) のは、assets キャッシュが
 * GCS 署名付き URL (60 分 TTL) を含むため。URL が失効しても 304 が返り続けると、
 * キャッシュ内の URL がいつまでも更新されない問題を防ぐ。
 *
 * sequences キャッシュ (24h TTL, 署名付き URL なし) は本来 TTL スライド可能だが、
 * 以下の理由で同じ非スライド設計を共用している:
 * - 24h を超える連続作業セッションは稀で、24h 経過時に full GET が走る実害は最小
 * - リソース種別ごとに挙動を分けると `fetchWithETag` の API が複雑化する
 * - assets / sequences で挙動が揃っているほうがデバッグが容易
 *
 * もし将来 sequences の cache hit 率向上が必要になったら、`extendOn304: boolean`
 * オプションを追加する方針を検討する。
 */
export async function fetchWithETag<T>(opts: FetchWithETagOptions<T>): Promise<T> {
  const { cacheKey, fetcher, onCacheHit, ttlMs } = opts

  // 1. キャッシュを読む (TTL 切れは readCache 内で null になる)
  const cached = readCache<T>(cacheKey)

  // 楽観表示: キャッシュがあれば即座にコールバック
  if (cached && onCacheHit) {
    onCacheHit(cached.payload)
  }

  // 2. リクエストヘッダーを組み立てる
  // キャッシュがある（TTL 内）場合のみ If-None-Match を送る。
  // TTL 切れの場合は cached が null になるため非条件 GET になる。
  const headers: Record<string, string> = {}
  if (cached?.etag) {
    headers['If-None-Match'] = cached.etag
  }

  // 3. フェッチ実行
  const result = await fetcher(headers)

  if (result.status === 304) {
    // 304: キャッシュが有効 — expiresAt は維持して payload を返す。
    // writeCache で TTL をリセットしてしまうと、キャッシュ内に保持している
    // GCS 署名付き URL (storage_url / thumbnail_url) が 60 分で失効する前に
    // 強制再取得する仕組みが効かなくなり、期限切れ URL が残り続けてしまう。
    // (#233)
    //
    // 304 が返るのは If-None-Match を送った場合のみ → cached は必ず非 null。
    // サーバが仕様外の 304 を返した場合の防御として明示的にエラーにする。(#235)
    if (!cached) {
      throw new Error(
        '[fetchWithETag] Received 304 without a cached entry. ' +
        'This is a server protocol violation — 304 should only be returned ' +
        'when If-None-Match was sent.'
      )
    }
    return cached.payload
  }

  // 4. 200: キャッシュを更新
  if (result.etag) {
    writeCache<T>(cacheKey, result.etag, result.data, ttlMs)
  }

  return result.data
}
