/**
 * assets.ts / foldersApi の mutation メソッドが clearCache を呼ぶことを確認するテスト (P0-3)
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

// heic2any モック (assets.ts の import に必要)
vi.mock('heic2any/dist/heic2any.min.js?url', () => ({ default: '' }))

import { foldersApi, assetsCacheKey } from './assets'

const PROJECT_ID = 'proj-abc'
const FOLDER_ID = 'folder-xyz'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('foldersApi: mutation メソッドは assets キャッシュをクリアする (P0-3)', () => {
  it('create: assets キャッシュをクリアする', async () => {
    await foldersApi.create(PROJECT_ID, 'New Folder')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('update: assets キャッシュをクリアする', async () => {
    await foldersApi.update(PROJECT_ID, FOLDER_ID, 'Renamed Folder')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('delete: assets キャッシュをクリアする', async () => {
    await foldersApi.delete(PROJECT_ID, FOLDER_ID)
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })
})
