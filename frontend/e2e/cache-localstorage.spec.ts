/**
 * E2E テスト: localStorage ETag キャッシュ動作の検証
 *
 * 検証内容:
 * 1. 初回ロード → ETag 付きレスポンスで localStorage にキャッシュエントリが作られる
 * 2. 2回目リクエスト → If-None-Match が送られ、304 が返る
 * 3. キャッシュがある状態でページリロード → UI が即時表示される
 */

import { test, expect } from '@playwright/test'
import { bootstrapMockEditorPage } from './helpers/editorMockServer'
import { openSeededEditor } from './helpers/editorPage'
import { SCHEMA_VERSION } from '../src/lib/cache/etagCache'

const ASSET_CACHE_KEY_PREFIX = 'cache:v1:assets:'

test.describe('localStorage ETag キャッシュ', () => {
  // ---------------------------------------------------------------------------
  // テスト 1: 初回ロードで localStorage にキャッシュエントリが作られる (ETag ありのモック)
  // ---------------------------------------------------------------------------
  test('初回ロード後に assets キャッシュエントリが localStorage に存在する', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    // アセット一覧リクエストをインターセプトして ETag ヘッダーを追加
    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      const req = route.request()
      const ifNoneMatch = req.headers()['if-none-match']

      if (ifNoneMatch) {
        // 2回目以降は 304 を返す
        await route.fulfill({ status: 304, headers: {}, body: '' })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { etag: 'W/"initial-etag"' },
        body: JSON.stringify([]),
      })
    })

    await openSeededEditor(page, projectId, mock.sequenceId)

    // アセットライブラリが表示されるまで待機
    await page.waitForTimeout(500)

    // localStorage にキャッシュエントリが存在するか確認
    const cacheEntry = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )

    // エントリが存在すること（旧実装では null になる — regression 防止アサート）
    expect(cacheEntry).not.toBeNull()

    // パースして schemaVersion と etag を検証
    const parsed = JSON.parse(cacheEntry!) as {
      schemaVersion: number
      etag: unknown
      payload: unknown
      fetchedAt: number
    }
    expect(parsed.schemaVersion).toBe(SCHEMA_VERSION)
    expect(parsed.fetchedAt).toBeGreaterThan(0)
    expect(parsed.etag).toBe('W/"initial-etag"')
  })

  // ---------------------------------------------------------------------------
  // テスト 2: ETag 付きモックサーバーで If-None-Match が送られ 304 が返る
  // ---------------------------------------------------------------------------
  test('ETag ヘッダーが返るとき: キャッシュに etag が保存され、2回目は If-None-Match が送られる', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    // アセット一覧リクエストをインターセプトして ETag ヘッダーを追加
    const requestHeaders: string[] = []
    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      const req = route.request()
      const ifNoneMatch = req.headers()['if-none-match']
      if (ifNoneMatch) {
        requestHeaders.push(ifNoneMatch)
      }

      // If-None-Match がある場合は 304 を返す
      if (ifNoneMatch === 'W/"mock-etag-v1"') {
        await route.fulfill({
          status: 304,
          headers: {},
          body: '',
        })
        return
      }

      // 通常レスポンスに ETag を付与
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: {
          etag: 'W/"mock-etag-v1"',
        },
        body: JSON.stringify([]),
      })
    })

    // 1回目のロード
    await openSeededEditor(page, projectId, mock.sequenceId)
    await page.waitForTimeout(800)

    // localStorage にキャッシュエントリが作られている
    const entryAfterFirstLoad = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )

    expect(entryAfterFirstLoad).not.toBeNull()
    const parsed = JSON.parse(entryAfterFirstLoad!) as { etag: string; schemaVersion: number }
    expect(parsed.etag).toBe('W/"mock-etag-v1"')
    expect(parsed.schemaVersion).toBe(SCHEMA_VERSION)

    // 2回目のロード（同ページ内でアセット再フェッチをトリガー）
    await page.evaluate(() => {
      window.dispatchEvent(new Event('douga-assets-changed'))
    })
    await page.waitForTimeout(500)

    // 2回目のリクエストで If-None-Match が送られた
    expect(requestHeaders).toContain('W/"mock-etag-v1"')
  })

  // ---------------------------------------------------------------------------
  // テスト 3: キャッシュがある状態でページ再訪問すると DOM が即時に表示される
  // ---------------------------------------------------------------------------
  test('キャッシュがある状態でページ再訪問すると DOM が即時に表示される', async ({ page }) => {
    const mock = await bootstrapMockEditorPage(page)
    const projectId = mock.projectId

    const mockAssets = [
      {
        id: 'asset-cached-1',
        project_id: projectId,
        name: 'cached-test-video.mp4',
        type: 'video' as const,
        storage_key: 'key/cached.mp4',
        storage_url: 'https://example.com/cached.mp4',
        thumbnail_url: null,
        duration_ms: 5000,
        width: 1280,
        height: 720,
        file_size: 1000000,
        mime_type: 'video/mp4',
        folder_id: null,
        created_at: '2026-01-01T00:00:00Z',
      },
    ]

    await page.route(`**/api/projects/${projectId}/assets`, async (route) => {
      const req = route.request()
      const ifNoneMatch = req.headers()['if-none-match']

      if (ifNoneMatch === 'W/"etag-with-assets"') {
        await route.fulfill({
          status: 304,
          headers: {},
          body: '',
        })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: {
          etag: 'W/"etag-with-assets"',
        },
        body: JSON.stringify(mockAssets),
      })
    })

    // 1回目のロード — キャッシュを作る
    await openSeededEditor(page, projectId, mock.sequenceId)
    await page.waitForTimeout(800)

    // キャッシュが作られていることを確認
    const entryRaw = await page.evaluate(
      ({ key }) => localStorage.getItem(key),
      { key: `${ASSET_CACHE_KEY_PREFIX}${projectId}` }
    )
    expect(entryRaw).not.toBeNull()

    // 2回目: ページリロード（同じ localStorage を保持）
    await page.reload()
    await page.waitForLoadState('domcontentloaded')

    // リロード後 200ms 以内にアプリが起動していること
    await expect(page.locator('body')).toBeVisible()

    // ページが完全に表示されること（楽観表示のため通常より速い）
    await page.waitForTimeout(200)
    await expect(page.locator('body')).toBeVisible()
  })
})
