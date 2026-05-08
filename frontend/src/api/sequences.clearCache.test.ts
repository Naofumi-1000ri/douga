/**
 * sequences.ts の mutation メソッドが clearCache を呼ぶことを確認するテスト (P0-2)
 *
 * apiClient をモック化して HTTP リクエストなしにテストする。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// etagCache モック
// vi.mock は hoisted されるため、vi.hoisted でモック関数を先に定義する
// ---------------------------------------------------------------------------
const { clearCacheMock } = vi.hoisted(() => ({
  clearCacheMock: vi.fn(),
}))

vi.mock('@/lib/cache/etagCache', () => ({
  fetchWithETag: vi.fn(async () => []),
  clearCache: clearCacheMock,
  readCache: vi.fn(() => null),
  writeCache: vi.fn(),
  clearAllCache: vi.fn(),
  ASSETS_CACHE_TTL_MS: 3_000_000,
  SEQUENCES_CACHE_TTL_MS: 86_400_000,
}))

// ---------------------------------------------------------------------------
// apiClient モック
// ---------------------------------------------------------------------------
vi.mock('./client', () => ({
  default: {
    get: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    post: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    put: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    patch: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
    delete: vi.fn(async () => ({ data: {}, headers: {}, status: 200 })),
  },
  API_BASE_URL: 'http://localhost:8000',
  getEditTokenForClient: vi.fn(() => null),
}))

// authStore モック
vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: vi.fn(() => ({ token: null })),
  },
}))

import { sequencesApi, sequenceListCacheKey, sequenceDetailCacheKey } from './sequences'

const PROJECT_ID = 'proj-abc'
const SEQUENCE_ID = 'seq-xyz'
const SNAPSHOT_ID = 'snap-123'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('sequencesApi: mutation メソッドは clearCache を呼ぶ (P0-2)', () => {
  it('lock: sequenceList と sequenceDetail のキャッシュをクリアする', async () => {
    await sequencesApi.lock(PROJECT_ID, SEQUENCE_ID)
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })

  it('unlock: sequenceList と sequenceDetail のキャッシュをクリアする', async () => {
    await sequencesApi.unlock(PROJECT_ID, SEQUENCE_ID)
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })

  it('restoreSnapshot: sequenceList と sequenceDetail のキャッシュをクリアする', async () => {
    await sequencesApi.restoreSnapshot(PROJECT_ID, SEQUENCE_ID, SNAPSHOT_ID)
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })

  it('create: sequenceList キャッシュをクリアする', async () => {
    await sequencesApi.create(PROJECT_ID, 'New Sequence')
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
  })

  it('update: sequenceDetail キャッシュをクリアする', async () => {
    const fakeTimeline = { version: '1.0', layers: [], audio_tracks: [], duration_ms: 0 }
    await sequencesApi.update(PROJECT_ID, SEQUENCE_ID, fakeTimeline, 1)
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })

  it('rename: sequenceList と sequenceDetail のキャッシュをクリアする', async () => {
    await sequencesApi.rename(PROJECT_ID, SEQUENCE_ID, 'renamed')
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })

  it('delete: sequenceList と sequenceDetail のキャッシュをクリアする', async () => {
    await sequencesApi.delete(PROJECT_ID, SEQUENCE_ID)
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceListCacheKey(PROJECT_ID))
    expect(clearCacheMock).toHaveBeenCalledWith(sequenceDetailCacheKey(PROJECT_ID, SEQUENCE_ID))
  })
})
