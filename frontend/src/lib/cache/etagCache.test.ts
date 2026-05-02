/**
 * etagCache ユニットテスト (Vitest)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  readCache,
  writeCache,
  clearCache,
  fetchWithETag,
  SCHEMA_VERSION,
  type CacheEntry,
} from './etagCache'

// ---------------------------------------------------------------------------
// localStorage モック
// ---------------------------------------------------------------------------
const store: Record<string, string> = {}

const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value },
  removeItem: (key: string) => { delete store[key] },
  clear: () => { Object.keys(store).forEach(k => delete store[k]) },
}

Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

beforeEach(() => {
  localStorageMock.clear()
})

// ---------------------------------------------------------------------------
// 1. writeCache / readCache ラウンドトリップ
// ---------------------------------------------------------------------------
describe('writeCache / readCache', () => {
  it('書き込んだデータをそのまま読み返せる', () => {
    const payload = [{ id: '1', name: 'test' }]
    writeCache('cache:v1:assets:proj-1', 'W/"abc123"', payload)

    const entry = readCache<typeof payload>('cache:v1:assets:proj-1')
    expect(entry).not.toBeNull()
    expect(entry!.etag).toBe('W/"abc123"')
    expect(entry!.payload).toEqual(payload)
    expect(entry!.schemaVersion).toBe(SCHEMA_VERSION)
    expect(entry!.fetchedAt).toBeTypeOf('number')
  })

  it('存在しないキーは null を返す', () => {
    expect(readCache('cache:v1:nonexistent')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 2. SCHEMA_VERSION 不一致のキャッシュは null を返す
// ---------------------------------------------------------------------------
describe('スキーマバージョン検証', () => {
  it('schemaVersion が異なるエントリは null を返し、ストレージから削除する', () => {
    const staleEntry: CacheEntry<string[]> = {
      schemaVersion: 0, // 古いバージョン
      etag: 'W/"old"',
      payload: ['old', 'data'],
      fetchedAt: Date.now(),
    }
    store['cache:v1:stale'] = JSON.stringify(staleEntry)

    const result = readCache<string[]>('cache:v1:stale')
    expect(result).toBeNull()
    // 破棄されているので localStorage にも存在しない
    expect(store['cache:v1:stale']).toBeUndefined()
  })

  it('schemaVersion フィールドがないエントリは null を返す', () => {
    store['cache:v1:broken'] = JSON.stringify({ etag: 'W/"x"', payload: [] })
    expect(readCache('cache:v1:broken')).toBeNull()
  })

  it('不正な JSON は null を返す', () => {
    store['cache:v1:malformed'] = 'NOT_JSON{'
    expect(readCache('cache:v1:malformed')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 3. fetchWithETag: キャッシュヒット時に onCacheHit が即座に呼ばれる
// ---------------------------------------------------------------------------
describe('fetchWithETag: キャッシュヒット時の楽観表示', () => {
  it('キャッシュがあれば onCacheHit が fetcher より先に呼ばれる', async () => {
    const payload = [{ id: '1' }]
    writeCache('cache:v1:test:opt', 'W/"v1"', payload)

    const callOrder: string[] = []
    const onCacheHit = vi.fn(() => { callOrder.push('onCacheHit') })
    const fetcher = vi.fn(async () => {
      callOrder.push('fetcher')
      return { data: payload, etag: 'W/"v1"', status: 200 }
    })

    await fetchWithETag({ cacheKey: 'cache:v1:test:opt', fetcher, onCacheHit })

    expect(onCacheHit).toHaveBeenCalledOnce()
    expect(onCacheHit).toHaveBeenCalledWith(payload)
    // onCacheHit が fetcher より先に呼ばれていること
    expect(callOrder[0]).toBe('onCacheHit')
    expect(callOrder[1]).toBe('fetcher')
  })

  it('キャッシュがなければ onCacheHit は呼ばれない', async () => {
    const onCacheHit = vi.fn()
    const fetcher = vi.fn(async () => ({
      data: [{ id: '1' }],
      etag: 'W/"new"',
      status: 200,
    }))

    await fetchWithETag({ cacheKey: 'cache:v1:test:no-cache', fetcher, onCacheHit })

    expect(onCacheHit).not.toHaveBeenCalled()
    expect(fetcher).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// 4. fetchWithETag: 304 レスポンスでキャッシュ payload が返る（fetcher は 1 回だけ呼ばれる）
// ---------------------------------------------------------------------------
describe('fetchWithETag: 304 Not Modified', () => {
  it('304 のとき cached.payload を返し、fetcher は 1 回だけ呼ばれる', async () => {
    const originalPayload = [{ id: 'cached' }]
    writeCache('cache:v1:test:304', 'W/"etag-1"', originalPayload)

    const fetcher = vi.fn(async (headers: Record<string, string>) => {
      expect(headers['If-None-Match']).toBe('W/"etag-1"')
      return { data: [] as typeof originalPayload, etag: null, status: 304 }
    })

    const result = await fetchWithETag<typeof originalPayload>({
      cacheKey: 'cache:v1:test:304',
      fetcher,
    })

    expect(result).toEqual(originalPayload)
    expect(fetcher).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// 5. fetchWithETag: 200 レスポンスでキャッシュが etag 付きで更新される
// ---------------------------------------------------------------------------
describe('fetchWithETag: 200 OK', () => {
  it('200 のとき新しい etag と payload でキャッシュが更新される', async () => {
    const newPayload = [{ id: 'new' }]
    const fetcher = vi.fn(async () => ({
      data: newPayload,
      etag: 'W/"new-etag"',
      status: 200,
    }))

    const result = await fetchWithETag({
      cacheKey: 'cache:v1:test:200',
      fetcher,
    })

    expect(result).toEqual(newPayload)

    // キャッシュが更新されているか確認
    const cached = readCache<typeof newPayload>('cache:v1:test:200')
    expect(cached).not.toBeNull()
    expect(cached!.etag).toBe('W/"new-etag"')
    expect(cached!.payload).toEqual(newPayload)
  })

  it('既存キャッシュがある場合に 200 で新しい内容に上書きされる', async () => {
    const oldPayload = [{ id: 'old' }]
    writeCache('cache:v1:test:update', 'W/"old-etag"', oldPayload)

    const newPayload = [{ id: 'new' }, { id: 'newer' }]
    const fetcher = vi.fn(async () => ({
      data: newPayload,
      etag: 'W/"new-etag"',
      status: 200,
    }))

    const result = await fetchWithETag({
      cacheKey: 'cache:v1:test:update',
      fetcher,
    })

    expect(result).toEqual(newPayload)
    const cached = readCache<typeof newPayload>('cache:v1:test:update')
    expect(cached!.etag).toBe('W/"new-etag"')
    expect(cached!.payload).toEqual(newPayload)
  })
})

// ---------------------------------------------------------------------------
// clearCache
// ---------------------------------------------------------------------------
describe('clearCache', () => {
  it('指定キーのエントリが削除される', () => {
    writeCache('cache:v1:to-delete', 'W/"e"', [1, 2, 3])
    clearCache('cache:v1:to-delete')
    expect(readCache('cache:v1:to-delete')).toBeNull()
  })

  it('存在しないキーを clearCache しても例外にならない', () => {
    expect(() => clearCache('cache:v1:nonexistent-key')).not.toThrow()
  })
})
