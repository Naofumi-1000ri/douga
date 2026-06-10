/**
 * assets.ts / foldersApi の mutation メソッドが clearCache を呼ぶことを確認するテスト (P0-3)
 * およびアセット一覧が fetchWithETag + validatePayload で ETag キャッシュを利用することを確認 (#273)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { FetchWithETagOptions } from '@/lib/cache/etagCache'
import type { Asset } from './assets'

// ---------------------------------------------------------------------------
// etagCache モック
// vi.mock は hoisted されるため、vi.hoisted でモック関数を先に定義する
// ---------------------------------------------------------------------------
const { clearCacheMock, fetchWithETagMock } = vi.hoisted(() => ({
  clearCacheMock: vi.fn(),
  fetchWithETagMock: vi.fn(async () => [] as Asset[]),
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

const PROJECT_ID = 'proj-abc'
const FOLDER_ID = 'folder-xyz'

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
})

afterEach(() => {
  vi.restoreAllMocks()
})

// helper: 最後の fetchWithETag 呼び出しの opts を取得
function getLastFetchOpts(): FetchWithETagOptions<Asset[]> {
  const calls = fetchWithETagMock.mock.calls as unknown as [FetchWithETagOptions<Asset[]>][]
  if (calls.length === 0) throw new Error('fetchWithETag was not called')
  return calls[calls.length - 1][0]
}

describe('assetsApi.list: fetchWithETag + validatePayload で ETag キャッシュを利用 (#273)', () => {
  it('fetchWithETag を使う（clearCache は呼ばない）', async () => {
    const onCacheHit = vi.fn()

    await assetsApi.list(PROJECT_ID, false, onCacheHit)

    expect(fetchWithETagMock).toHaveBeenCalledOnce()
    expect(clearCacheMock).not.toHaveBeenCalled()
  })

  it('fetchWithETag に正しい cacheKey / ttlMs / conditionalRequests を渡す', async () => {
    await assetsApi.list(PROJECT_ID)

    const opts = getLastFetchOpts()
    expect(opts.cacheKey).toBe(assetsCacheKey(PROJECT_ID))
    expect(opts.ttlMs).toBe(3_000_000) // ASSETS_CACHE_TTL_MS mock value
    // backend の ETag から signed URL を除外しているため conditionalRequests=false
    expect(opts.conditionalRequests).toBe(false)
  })

  it('onCacheHit コールバックを fetchWithETag に渡す', async () => {
    const onCacheHit = vi.fn()
    await assetsApi.list(PROJECT_ID, false, onCacheHit)

    const opts = getLastFetchOpts()
    expect(opts.onCacheHit).toBe(onCacheHit)
  })

  it('validatePayload が設定されている', async () => {
    await assetsApi.list(PROJECT_ID)

    const opts = getLastFetchOpts()
    expect(typeof opts.validatePayload).toBe('function')
  })

  it('validatePayload: 有効な署名URLを持つアセット配列は true を返す', async () => {
    await assetsApi.list(PROJECT_ID)

    const opts = getLastFetchOpts()
    if (!opts.validatePayload) throw new Error('validatePayload is not set')

    // URL なしのアセット（storage_url が署名付きでない場合は isSignedUrlValid が true を返す）
    const result = opts.validatePayload([
      {
        id: '1',
        project_id: PROJECT_ID,
        name: 'test',
        type: 'video',
        storage_key: 'key/test.mp4',
        storage_url: '/plain/path',
        thumbnail_url: null,
        duration_ms: null,
        width: null,
        height: null,
        file_size: 1024,
        mime_type: 'video/mp4',
        folder_id: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    ])
    expect(result).toBe(true)
  })

  it('validatePayload: 期限切れ署名URLを持つアセットは false を返す', async () => {
    await assetsApi.list(PROJECT_ID)

    const opts = getLastFetchOpts()
    if (!opts.validatePayload) throw new Error('validatePayload is not set')

    // 期限切れ署名 URL (X-Goog-Date が過去すぎて失効)
    const expiredUrl =
      'https://storage.googleapis.com/bucket/file.mp4?X-Goog-Date=20200101T000000Z&X-Goog-Expires=3600'
    const result = opts.validatePayload([
      {
        id: '1',
        project_id: PROJECT_ID,
        name: 'test',
        type: 'video',
        storage_key: 'key/test.mp4',
        storage_url: expiredUrl,
        thumbnail_url: null,
        duration_ms: null,
        width: null,
        height: null,
        file_size: 1024,
        mime_type: 'video/mp4',
        folder_id: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    ])
    expect(result).toBe(false)
  })

  it('includeInternal=true を fetcher params として渡す', async () => {
    type Fetcher = FetchWithETagOptions<Asset[]>['fetcher']
    let capturedFetcher: Fetcher | null = null
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(fetchWithETagMock as any).mockImplementationOnce(
      async (opts: FetchWithETagOptions<Asset[]>) => {
        capturedFetcher = opts.fetcher
        return []
      }
    )

    const { default: apiClient } = await import('./client')
    await assetsApi.list(PROJECT_ID, true)

    if (capturedFetcher != null) {
      await (capturedFetcher as Fetcher)({})
      expect(apiClient.get).toHaveBeenCalledWith(
        `/projects/${PROJECT_ID}/assets`,
        expect.objectContaining({
          params: { include_internal: true },
        })
      )
    }
  })
})

describe('assetsApi: mutation メソッドは assets キャッシュをクリアする (#230 item2, item7)', () => {
  it('create: assets キャッシュをクリアする', async () => {
    await assetsApi.create(PROJECT_ID, {
      name: 'test.mp4',
      type: 'video',
      subtype: 'other',
      storage_key: 'key/test.mp4',
      storage_url: 'key/test.mp4',
      file_size: 1024,
      mime_type: 'video/mp4',
    })
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('delete: assets キャッシュをクリアする', async () => {
    await assetsApi.delete(PROJECT_ID, 'asset-123')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('rename: assets キャッシュをクリアする', async () => {
    await assetsApi.rename(PROJECT_ID, 'asset-123', 'renamed.mp4')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('moveToFolder: assets キャッシュをクリアする', async () => {
    await assetsApi.moveToFolder(PROJECT_ID, 'asset-123', 'folder-xyz')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('saveSession: assets キャッシュをクリアする', async () => {
    await assetsApi.saveSession(PROJECT_ID, 'session-name', {
      schema_version: '1',
      timeline_data: { version: '1.0', layers: [], audio_tracks: [], duration_ms: 0 },
      asset_references: [],
    })
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('updateSession: assets キャッシュをクリアする', async () => {
    await assetsApi.updateSession(PROJECT_ID, 'session-id', 'session-name', {
      schema_version: '1',
      timeline_data: { version: '1.0', layers: [], audio_tracks: [], duration_ms: 0 },
      asset_references: [],
    })
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })

  it('extractAudio: 新しいオーディオアセット作成後に assets キャッシュをクリアする', async () => {
    await assetsApi.extractAudio(PROJECT_ID, 'video-asset-id')
    expect(clearCacheMock).toHaveBeenCalledWith(assetsCacheKey(PROJECT_ID))
  })
})

describe('foldersApi: mutation メソッドは assets キャッシュをクリアする (P0-3)', () => {
  it('diagnoseThumbnailFailure: 失敗した URL を診断 endpoint に送る', async () => {
    const { default: apiClient } = await import('./client')
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
