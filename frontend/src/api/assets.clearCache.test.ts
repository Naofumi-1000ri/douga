/**
 * assets.ts / foldersApi の mutation メソッドが clearCache を呼ぶことを確認するテスト (P0-3)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// etagCache モック
// vi.mock は hoisted されるため、vi.hoisted でモック関数を先に定義する
// ---------------------------------------------------------------------------
const { clearCacheMock, fetchWithETagMock } = vi.hoisted(() => ({
  clearCacheMock: vi.fn(),
  fetchWithETagMock: vi.fn(async () => []),
}))

vi.mock('@/lib/cache/etagCache', () => ({
  fetchWithETag: fetchWithETagMock,
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

import { assetsApi, foldersApi, assetsCacheKey } from './assets'
import apiClient from './client'

const PROJECT_ID = 'proj-abc'
const FOLDER_ID = 'folder-xyz'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('foldersApi: mutation メソッドは assets キャッシュをクリアする (P0-3)', () => {
  it('assetsApi.list: signed URL を含むため If-None-Match を使わない', async () => {
    await assetsApi.list(PROJECT_ID)

    expect(fetchWithETagMock).toHaveBeenCalledOnce()
    const calls = fetchWithETagMock.mock.calls as unknown as Array<[Record<string, unknown>]>
    expect(calls[0][0]).toMatchObject({
      cacheKey: assetsCacheKey(PROJECT_ID),
      conditionalRequests: false,
    })
  })

  it('diagnoseThumbnailFailure: 失敗した URL を診断 endpoint に送る', async () => {
    await assetsApi.diagnoseThumbnailFailure(
      PROJECT_ID,
      'asset-123',
      'https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Date=20240101T000000Z',
      'asset-library-thumbnail_url'
    )

    expect(apiClient.post).toHaveBeenCalledWith(
      `/projects/${PROJECT_ID}/assets/asset-123/thumbnail-diagnostics`,
      {
        url: 'https://storage.googleapis.com/bucket/thumb.jpg?X-Goog-Date=20240101T000000Z',
        source: 'asset-library-thumbnail_url',
      }
    )
  })

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
