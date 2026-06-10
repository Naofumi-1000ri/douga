/**
 * sequences.bypassCache.test.ts — bypassCache オプションの挙動を検証 (C-2)
 *
 * bypassCache=true のとき:
 *   - If-None-Match ヘッダーが送られない
 *   - onCacheHit が呼ばれない
 *   - 200 応答時は通常通り writeCache でキャッシュが書き込まれる (#230 item3)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// vi.hoisted でモック関数を先に定義する（ホイスト問題対策）
// ---------------------------------------------------------------------------
const { fetchWithETagMock, writeCacheMock, apiGetMock } = vi.hoisted(() => ({
  fetchWithETagMock: vi.fn(),
  writeCacheMock: vi.fn(),
  apiGetMock: vi.fn(async () => ({
    data: { id: 'seq-1' },
    headers: { etag: 'W/"v1"' },
    status: 200,
  })),
}))

vi.mock('@/lib/cache/etagCache', () => ({
  fetchWithETag: fetchWithETagMock,
  clearCache: vi.fn(),
  readCache: vi.fn(() => null),
  writeCache: writeCacheMock,
  clearAllCache: vi.fn(),
  clearAllUserData: vi.fn(),
  ASSETS_CACHE_TTL_MS: 3_000_000,
  SEQUENCES_CACHE_TTL_MS: 86_400_000,
}))

vi.mock('./client', () => ({
  default: {
    get: apiGetMock,
    post: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    put: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    patch: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    delete: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
  },
  API_BASE_URL: 'http://localhost:8000',
  getEditTokenForClient: vi.fn(() => null),
}))

vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: vi.fn(() => ({ token: null })),
  },
}))

import { sequencesApi } from './sequences'

const PROJECT_ID = 'proj-abc'
const SEQUENCE_ID = 'seq-xyz'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('sequencesApi.get: bypassCache オプション (C-2)', () => {
  it('bypassCache=false (デフォルト) のとき fetchWithETag が onCacheHit を受け取る', async () => {
    fetchWithETagMock.mockResolvedValueOnce({ id: 'seq-1' })
    const onCacheHit = vi.fn()
    await sequencesApi.get(PROJECT_ID, SEQUENCE_ID, onCacheHit, false)

    expect(fetchWithETagMock).toHaveBeenCalledOnce()
    const opts = fetchWithETagMock.mock.calls[0][0] as { onCacheHit: unknown }
    // onCacheHit が渡されている
    expect(opts.onCacheHit).toBe(onCacheHit)
  })

  it('bypassCache=true のとき fetcher が If-None-Match を無視して空ヘッダーでリクエストする', async () => {
    fetchWithETagMock.mockImplementationOnce(
      async (opts: { fetcher: (h: Record<string, string>) => Promise<unknown>; onCacheHit?: unknown }) => {
        // fetchWithETag が If-None-Match 付きヘッダーを渡してきても、
        // bypassCache=true の fetcher は requestHeaders = {} を使う
        const result = await opts.fetcher({ 'If-None-Match': 'W/"some-etag"' })
        return result
      }
    )

    const onCacheHit = vi.fn()
    await sequencesApi.get(PROJECT_ID, SEQUENCE_ID, onCacheHit, true)

    // apiClient.get が呼ばれた際のヘッダーを確認
    expect(apiGetMock).toHaveBeenCalledOnce()
    // mock.calls[0] の 2 番目の引数（axios config）を取り出す
    const rawCalls = apiGetMock.mock.calls as unknown as Array<[string, { headers?: Record<string, string> }]>
    const requestConfig = rawCalls[0][1]
    // bypassCache=true なので空オブジェクト {} が渡される（If-None-Match なし）
    expect(requestConfig.headers).toEqual({})
  })

  it('bypassCache=true のとき onCacheHit は fetchWithETag に渡されない', async () => {
    fetchWithETagMock.mockResolvedValueOnce({ id: 'seq-1' })
    const onCacheHit = vi.fn()
    await sequencesApi.get(PROJECT_ID, SEQUENCE_ID, onCacheHit, true)

    expect(fetchWithETagMock).toHaveBeenCalledOnce()
    const opts = fetchWithETagMock.mock.calls[0][0] as { onCacheHit?: unknown }
    // onCacheHit が渡されていない（undefined）
    expect(opts.onCacheHit).toBeUndefined()
  })

  it('bypassCache=true でも 200 応答時は writeCache でキャッシュが書き込まれる (#230 item3)', async () => {
    // bypassCache=true は If-None-Match を送らないだけで、
    // 200 応答時の writeCache（fetchWithETag 内部）は通常通り動く。
    // fetchWithETag に渡された cacheKey と ttlMs が writeCache で使われることを確認する。
    fetchWithETagMock.mockImplementationOnce(
      async (opts: {
        cacheKey: string
        fetcher: (h: Record<string, string>) => Promise<unknown>
        ttlMs?: number
      }) => {
        // fetchWithETag の実際の動作をシミュレート: 200 ならキャッシュ書き込み
        const result = await opts.fetcher({})
        writeCacheMock(opts.cacheKey, 'W/"v1"', result, opts.ttlMs)
        return result
      }
    )

    await sequencesApi.get(PROJECT_ID, SEQUENCE_ID, undefined, true)

    expect(writeCacheMock).toHaveBeenCalledOnce()
    // cacheKey が sequence detail キャッシュキー (cache:v1:sequence:<projectId>:<sequenceId>) になっている
    expect(writeCacheMock.mock.calls[0][0]).toBe(`cache:v1:sequence:${PROJECT_ID}:${SEQUENCE_ID}`)
  })
})
