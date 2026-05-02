/**
 * localStorage ETag キャッシュユーティリティ
 *
 * キーの命名規則: `cache:v1:<resource>:<id>`
 * 例: `cache:v1:assets:<projectId>`
 *     `cache:v1:sequences:<projectId>`
 *     `cache:v1:sequence:<projectId>:<sequenceId>`
 */

export const SCHEMA_VERSION = 1

export type CacheEntry<T> = {
  schemaVersion: number
  etag: string
  payload: T
  fetchedAt: number
}

/**
 * localStorage からキャッシュエントリを読み込む。
 * - スキーマバージョン不一致 → null を返して破棄
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

    return parsed as CacheEntry<T>
  } catch {
    return null
  }
}

/**
 * localStorage にキャッシュエントリを書き込む。
 * QuotaExceededError は握りつぶす。
 */
export function writeCache<T>(key: string, etag: string, payload: T): void {
  const entry: CacheEntry<T> = {
    schemaVersion: SCHEMA_VERSION,
    etag,
    payload,
    fetchedAt: Date.now(),
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

// ---------------------------------------------------------------------------
// fetchWithETag
// ---------------------------------------------------------------------------

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
}

/**
 * ETag を利用したキャッシュ付き fetch ヘルパー。
 *
 * 1. localStorage を読む → あれば onCacheHit(cached.payload) を即座にコール（楽観表示）
 * 2. If-None-Match ヘッダー付きでサーバーにリクエスト
 * 3. 304 → キャッシュの payload を返す（fetchedAt のみ更新）
 * 4. 200 → 新しい etag でキャッシュを更新して payload を返す
 */
export async function fetchWithETag<T>(opts: FetchWithETagOptions<T>): Promise<T> {
  const { cacheKey, fetcher, onCacheHit } = opts

  // 1. キャッシュを読む
  const cached = readCache<T>(cacheKey)

  // 楽観表示: キャッシュがあれば即座にコールバック
  if (cached && onCacheHit) {
    onCacheHit(cached.payload)
  }

  // 2. リクエストヘッダーを組み立てる
  const headers: Record<string, string> = {}
  if (cached?.etag) {
    headers['If-None-Match'] = cached.etag
  }

  // 3. フェッチ実行
  const result = await fetcher(headers)

  if (result.status === 304) {
    // 304: キャッシュが有効 — fetchedAt のみ更新して payload を返す
    if (cached) {
      writeCache<T>(cacheKey, cached.etag, cached.payload)
    }
    // cached が null になるケースは通常ない（If-None-Match を送った場合のみ 304 になる）
    // 万一 cached が null でも fetcher が data を返していればそれを使う
    return cached?.payload ?? result.data
  }

  // 4. 200: キャッシュを更新
  if (result.etag) {
    writeCache<T>(cacheKey, result.etag, result.data)
  }

  return result.data
}
