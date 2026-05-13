/**
 * assets.ts / foldersApi の mutation メソッドが clearCache を呼ぶことを確認するテスト (P0-3)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

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
  vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('foldersApi: mutation メソッドは assets キャッシュをクリアする (P0-3)', () => {
  it('assetsApi.list: signed URL を含むため localStorage/ETag キャッシュを使わない', async () => {
    const onCacheHit = vi.fn()

    await assetsApi.list(PROJECT_ID, false, onCacheHit)

    expect(fetchWithETagMock).not.toHaveBeenCalled()
    expect(onCacheHit).not.toHaveBeenCalled()
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
    expect(apiClient.get).toHaveBeenCalledWith(
      `/projects/${PROJECT_ID}/assets`,
      {
        params: { _signed_url_refresh: '1700000000000' },
        headers: {
          'Cache-Control': 'no-store',
          Pragma: 'no-cache',
        },
      }
    )
  })

  it('assetsApi.list: includeInternal=true を API params として渡す', async () => {
    await assetsApi.list(PROJECT_ID, true)

    expect(apiClient.get).toHaveBeenCalledWith(
      `/projects/${PROJECT_ID}/assets`,
      {
        params: {
          _signed_url_refresh: '1700000000000',
          include_internal: true,
        },
        headers: {
          'Cache-Control': 'no-store',
          Pragma: 'no-cache',
        },
      }
    )
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
